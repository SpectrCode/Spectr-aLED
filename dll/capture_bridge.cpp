#define DLL_EXPORT extern "C" __declspec(dllexport)

#include <windows.h>
#include <d3d11.h>
#include <d3d11_1.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>
#include <shellscalingapi.h>
#include <math.h>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "Shcore.lib")

// ============================================================
// SHADER (dynamic params: precision, samples, coord mode)
// Legacy: 2D area-weighted box filter (single dispatch)
// ============================================================

static const char* g_shader_code = R"(
Texture2D<float4> srcTex : register(t0);
RWStructuredBuffer<float3> dstBuf : register(u0);
Buffer<float> coordBuf : register(t1);

cbuffer Params : register(b0)
{
    int src_w;
    int src_h;
    int dst_w;
    int dst_h;
    int pixel_limit;    // 0 = unlimited, >0 = max total source pixels to sample
    int coord_mode;     // 0 = frame (compute inline), 1 = once (read from cache)
    int prec_coord;     // 0 = fp32, 1 = fp16
    int prec_weights;   // 0 = fp32, 1 = fp16
    int prec_color;     // 0 = fp32, 1 = fp16
    int prec_accum;     // 0 = fp32, 1 = fp16
    int pad0;
    int pad1;
};

float hfloat(float v, int fp16) { return (fp16 != 0) ? float(half(v)) : v; }
float hmul(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) * half(b)) : a * b; }
float hadd(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) + half(b)) : a + b; }
float hdiv(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) / half(b)) : a / b; }
float3 hfloat3(float3 v, int fp16) { return (fp16 != 0) ? float3(half3(v)) : v; }
float3 hadd3(float3 a, float3 b, int fp16) { return (fp16 != 0) ? float3(half3(a) + half3(b)) : a + b; }
float3 hmul3(float3 a, float v, int fp16) { return (fp16 != 0) ? float3(half3(a) * half(v)) : a * v; }
float3 hdiv3(float3 a, float b, int fp16) { return (fp16 != 0) ? float3(half3(a) / half(b)) : a / b; }

[numthreads(8,8,1)]
void main(uint3 id : SV_DispatchThreadID)
{
    if (id.x >= dst_w || id.y >= dst_h) return;

    int idx = id.y * dst_w + id.x;
    int x_start, y_start, x_end, y_end, x_step, y_step;
    float src_x0, src_y0, src_x1, src_y1;

    if (coord_mode != 0)
    {
        int base = idx * 16;
        x_start = (int)coordBuf[base + 0];
        y_start = (int)coordBuf[base + 1];
        x_step  = (int)coordBuf[base + 2];
        y_step  = (int)coordBuf[base + 3];
        x_end   = (int)coordBuf[base + 4];
        y_end   = (int)coordBuf[base + 5];
        src_x0  = coordBuf[base + 6];
        src_y0  = coordBuf[base + 7];
        src_x1  = coordBuf[base + 8];
        src_y1  = coordBuf[base + 9];
        x_start = max(x_start, 0);
        y_start = max(y_start, 0);
        x_end   = min(x_end, src_w);
        y_end   = min(y_end, src_h);
        if (x_end < x_start) x_end = x_start + 1;
        if (y_end < y_start) y_end = y_start + 1;
        x_step = max(x_step, 1);
        y_step = max(y_step, 1);
        if (x_end - x_start > src_w) x_end = x_start + src_w;
        if (y_end - y_start > src_h) y_end = y_start + src_h;
        src_x0 = clamp(src_x0, 0.0f, (float)src_w);
        src_y0 = clamp(src_y0, 0.0f, (float)src_h);
        src_x1 = clamp(src_x1, 0.0f, (float)src_w);
        src_y1 = clamp(src_y1, 0.0f, (float)src_h);
    }
    else
    {
        float fx0 = hmul((float)id.x, hdiv((float)src_w, (float)dst_w, prec_coord), prec_coord);
        float fy0 = hmul((float)id.y, hdiv((float)src_h, (float)dst_h, prec_coord), prec_coord);
        float fx1 = hmul((float)(id.x + 1), hdiv((float)src_w, (float)dst_w, prec_coord), prec_coord);
        float fy1 = hmul((float)(id.y + 1), hdiv((float)src_h, (float)dst_h, prec_coord), prec_coord);
        src_x0 = fx0; src_y0 = fy0; src_x1 = fx1; src_y1 = fy1;
        y_start = max((int)floor(fy0), 0);
        y_end   = min((int)ceil(fy1), src_h);
        x_start = max((int)floor(fx0), 0);
        x_end   = min((int)ceil(fx1), src_w);
        int y_span = y_end - y_start;
        int x_span = x_end - x_start;
        if (pixel_limit > 0)
        {
            int total_px = src_w * src_h;
            if (total_px > pixel_limit)
            {
                float ratio = (float)total_px / (float)pixel_limit;
                int gstep = max(1, (int)ceil(sqrt(ratio)));
                y_step = gstep;
                x_step = gstep;
            }
        }
        else { y_step = 1; x_step = 1; }
    }

    float3 sum = float3(0, 0, 0);
    float total_weight = 0.0f;

    for (int y = y_start; y < y_end; y += y_step)
    {
        float sy0 = (float)y;
        float sy1 = min((float)(y + y_step), (float)y_end);
        float v_weight = min(sy1, src_y1) - max(sy0, src_y0);
        v_weight = hfloat(v_weight, prec_weights);
        if (v_weight <= 0.0f) continue;
        for (int x = x_start; x < x_end; x += x_step)
        {
            float sx0 = (float)x;
            float sx1 = min((float)(x + x_step), (float)x_end);
            float h_weight = min(sx1, src_x1) - max(sx0, src_x0);
            h_weight = hfloat(h_weight, prec_weights);
            if (h_weight <= 0.0f) continue;
            float pixel_weight = hmul(v_weight, h_weight, prec_weights);
            int sample_x = min(max(x + x_step / 2, 0), src_w - 1);
            int sample_y = min(max(y + y_step / 2, 0), src_h - 1);
            float4 color = srcTex.Load(int3(sample_x, sample_y, 0));
            float3 c = hfloat3(float3(color.z, color.y, color.x), prec_color);
            sum = hadd3(sum, hmul3(c, pixel_weight, prec_accum), prec_accum);
            total_weight = hadd(total_weight, pixel_weight, prec_accum);
        }
    }

    if (total_weight > 0.0f)
        sum = hdiv3(sum, total_weight, prec_accum);

    dstBuf[idx] = sum;
}
)";

// ============================================================
// SEPARABLE SHADER (2-pass: exact same result, no warp divergence)
//
// Pass 1: vertical weighted sum  → temp[src_w × dst_h]
//   Each thread: (src_x, dst_y), loops over y only (~32 iters)
//   All 8 threads in a warp (consecutive src_x, same dst_y) have
//   IDENTICAL y-span → ZERO divergence in the inner loop.
//
// Pass 2: horizontal weighted sum + normalize → output[dst_w × dst_h]
//   Each thread: (dst_x, dst_y), loops over x only (~30 iters)
//   At most ±1 divergence in x-span per warp.
//
// Mathematically identical to the 2D box filter:
//   sum   = Σ_y v_w(y) * [Σ_x h_w(x) * src[x,y]]
//   total = (Σ_y v_w) * (Σ_x h_w)
// ============================================================

static const char* g_sep_shader_p1 = R"(
Texture2D<float4> srcTex : register(t0);
RWStructuredBuffer<float3> dstBuf : register(u0);

cbuffer Params : register(b0)
{
    int src_w;
    int src_h;
    int dst_w;
    int dst_h;
    int pixel_limit;
    int coord_mode;
    int prec_coord;
    int prec_weights;
    int prec_color;
    int prec_accum;
    int pad0;
    int pad1;
};

float hfloat(float v, int fp16) { return (fp16 != 0) ? float(half(v)) : v; }
float hmul(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) * half(b)) : a * b; }
float hadd(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) + half(b)) : a + b; }
float3 hfloat3(float3 v, int fp16) { return (fp16 != 0) ? float3(half3(v)) : v; }
float3 hadd3(float3 a, float3 b, int fp16) { return (fp16 != 0) ? float3(half3(a) + half3(b)) : a + b; }
float3 hmul3(float3 a, float v, int fp16) { return (fp16 != 0) ? float3(half3(a) * half(v)) : a * v; }

[numthreads(8,8,1)]
void main(uint3 id : SV_DispatchThreadID)
{
    // Thread = (src_x, dst_y)
    if (id.x >= src_w || id.y >= dst_h) return;

    int sx = id.x;
    int dy = id.y;

    // Vertical span for this dst_y row
    float fy0 = (float)dy * (float)src_h / (float)dst_h;
    float fy1 = (float)(dy + 1) * (float)src_h / (float)dst_h;
    int y_start = max((int)floor(fy0), 0);
    int y_end   = min((int)ceil(fy1), src_h);

    int y_span = y_end - y_start;
    int y_step = 1;
    if (pixel_limit > 0)
    {
        int total_px = src_w * src_h;
        if (total_px > pixel_limit)
        {
            float ratio = (float)total_px / (float)pixel_limit;
            y_step = max(1, (int)ceil(sqrt(ratio)));
        }
    }

    float3 sum = float3(0, 0, 0);

    for (int y = y_start; y < y_end; y += y_step)
    {
        float vy0 = (float)y;
        float vy1 = min((float)(y + y_step), (float)y_end);
        float v_weight = min(vy1, fy1) - max(vy0, fy0);
        if (v_weight <= 0.0f) continue;

        int sample_y = y + y_step / 2;
        sample_y = min(max(sample_y, 0), src_h - 1);

        float4 color = srcTex.Load(int3(sx, sample_y, 0));
        float3 c = hfloat3(float3(color.z, color.y, color.x), prec_color);
        sum = hadd3(sum, hmul3(c, hfloat(v_weight, prec_weights), prec_accum), prec_accum);
    }

    dstBuf[dy * src_w + sx] = sum;
}
)";

static const char* g_sep_shader_p2 = R"(
StructuredBuffer<float3> srcBuf : register(t0);
RWStructuredBuffer<float3> dstBuf : register(u0);

cbuffer Params : register(b0)
{
    int src_w;
    int src_h;
    int dst_w;
    int dst_h;
    int pixel_limit;
    int coord_mode;
    int prec_coord;
    int prec_weights;
    int prec_color;
    int prec_accum;
    int pad0;
    int pad1;
};

float hfloat(float v, int fp16) { return (fp16 != 0) ? float(half(v)) : v; }
float hmul(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) * half(b)) : a * b; }
float hadd(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) + half(b)) : a + b; }
float hdiv(float a, float b, int fp16) { return (fp16 != 0) ? float(half(a) / half(b)) : a / b; }
float3 hadd3(float3 a, float3 b, int fp16) { return (fp16 != 0) ? float3(half3(a) + half3(b)) : a + b; }
float3 hmul3(float3 a, float v, int fp16) { return (fp16 != 0) ? float3(half3(a) * half(v)) : a * v; }
float3 hdiv3(float3 a, float b, int fp16) { return (fp16 != 0) ? float3(half3(a) / half(b)) : a / b; }

[numthreads(8,8,1)]
void main(uint3 id : SV_DispatchThreadID)
{
    // Thread = (dst_x, dst_y)
    if (id.x >= dst_w || id.y >= dst_h) return;

    int dx = id.x;
    int dy = id.y;

    // Horizontal span for this dst_x col
    float fx0 = (float)dx * (float)src_w / (float)dst_w;
    float fx1 = (float)(dx + 1) * (float)src_w / (float)dst_w;
    int x_start = max((int)floor(fx0), 0);
    int x_end   = min((int)ceil(fx1), src_w);

    int x_span = x_end - x_start;
    int x_step = 1;
    if (pixel_limit > 0)
    {
        int total_px = src_w * src_h;
        if (total_px > pixel_limit)
        {
            float ratio = (float)total_px / (float)pixel_limit;
            x_step = max(1, (int)ceil(sqrt(ratio)));
        }
    }

    // Vertical total weight (for normalization) — same for all dx
    float fy0 = (float)dy * (float)src_h / (float)dst_h;
    float fy1 = (float)(dy + 1) * (float)src_h / (float)dst_h;
    int y_start = max((int)floor(fy0), 0);
    int y_end   = min((int)ceil(fy1), src_h);
    int y_span = y_end - y_start;
    int y_step = 1;
    if (pixel_limit > 0)
    {
        int total_px = src_w * src_h;
        if (total_px > pixel_limit)
        {
            float ratio = (float)total_px / (float)pixel_limit;
            y_step = max(1, (int)ceil(sqrt(ratio)));
        }
    }
    float v_total = 0.0f;
    for (int y = y_start; y < y_end; y += y_step)
    {
        float vy0 = (float)y;
        float vy1 = min((float)(y + y_step), (float)y_end);
        float v_w = min(vy1, fy1) - max(vy0, fy0);
        if (v_w > 0.0f) v_total = hadd(v_total, v_w, prec_weights);
    }

    // Horizontal weighted sum
    float3 sum = float3(0, 0, 0);
    float h_total = 0.0f;

    for (int x = x_start; x < x_end; x += x_step)
    {
        float vx0 = (float)x;
        float vx1 = min((float)(x + x_step), (float)x_end);
        float h_w = min(vx1, fx1) - max(vx0, fx0);
        if (h_w <= 0.0f) continue;

        float3 val = srcBuf[dy * src_w + x];
        sum = hadd3(sum, hmul3(val, hfloat(h_w, prec_weights), prec_accum), prec_accum);
        h_total = hadd(h_total, h_w, prec_weights);
    }

    float total = hmul(h_total, v_total, prec_accum);
    if (total > 0.0f)
        sum = hdiv3(sum, total, prec_accum);

    dstBuf[dy * dst_w + dx] = sum;
}
)";

// ============================================================
// GLOBALS
// ============================================================

static ID3D11Device* g_device = nullptr;
static ID3D11DeviceContext* g_context = nullptr;
static IDXGIOutputDuplication* g_duplication = nullptr;

static ID3D11ComputeShader* g_cs = nullptr;

// Separable (2-pass) shaders
static ID3D11ComputeShader* g_sep_cs1 = nullptr;
static ID3D11ComputeShader* g_sep_cs2 = nullptr;
static ID3D11Buffer* g_sepBuf = nullptr;          // intermediate: src_w × dst_h
static ID3D11UnorderedAccessView* g_sepUAV = nullptr;
static ID3D11ShaderResourceView* g_sepSRV = nullptr;
static int g_sepBuf_src_w = 0;
static int g_sepBuf_dst_h = 0;
static bool g_use_separable = true;               // default: use separable 2-pass

static ID3D11Buffer* g_outputBuffer = nullptr;
static ID3D11UnorderedAccessView* g_uav = nullptr;
static ID3D11ShaderResourceView* g_srv = nullptr;
static ID3D11Buffer* g_constBuffer = nullptr;
static ID3D11Buffer* g_readback = nullptr;

// Stream 2 separable buffers
static ID3D11Buffer* g_sepBuf2 = nullptr;
static ID3D11UnorderedAccessView* g_sepUAV2 = nullptr;
static ID3D11ShaderResourceView* g_sepSRV2 = nullptr;
static int g_sepBuf2_src_w = 0;
static int g_sepBuf2_dst_h = 0;

static int g_dst_w = 0;
static int g_dst_h = 0;

// ===== SECOND STREAM =====
static int g_dst2_w = 0;
static int g_dst2_h = 0;

static ID3D11Buffer* g_outputBuffer2 = nullptr;
static ID3D11UnorderedAccessView* g_uav2 = nullptr;
static ID3D11Buffer* g_readback2 = nullptr;

static int g_output_index = 0;
static bool g_is_hdr = false;

static volatile unsigned long long g_frame_id = 0;

// ===== SHADER RUNTIME PARAMS =====
static volatile int g_pixel_limit = 0;        // 0 = unlimited, >0 = max total source pixels to sample
static volatile int g_coord_mode = 0;          // 0 = frame, 1 = once
static volatile int g_prec_coord = 0;
static volatile int g_prec_weights = 0;
static volatile int g_prec_color = 0;
static volatile int g_prec_accum = 0;

// ===== COORDINATE CACHE =====
// Buffer layout: 16 floats per dst pixel (64 bytes)
// [0] x_start [1] y_start [2] x_step [3] y_step
// [4] x_end   [5] y_end   [6] src_x0 [7] src_y0
// [8] src_x1  [9] src_y1  [10-15] pad
#define COORD_FLOATS_PER_ENTRY 16

static ID3D11Buffer* g_coordBuf1 = nullptr;    // coord cache for stream 1
static ID3D11Buffer* g_coordBuf2 = nullptr;    // coord cache for stream 2
static ID3D11ShaderResourceView* g_coordSrv1 = nullptr;
static ID3D11ShaderResourceView* g_coordSrv2 = nullptr;
static ID3D11Buffer* g_coordStaging1 = nullptr;
static ID3D11Buffer* g_coordStaging2 = nullptr;

static bool g_coord_dirty1 = true;
static bool g_coord_dirty2 = true;
static int g_coord_last_src_w1 = 0;
static int g_coord_last_src_h1 = 0;
static int g_coord_last_src_w2 = 0;
static int g_coord_last_src_h2 = 0;

// Mutex protecting shader params & coord cache state from concurrent access
// (UI thread writes set_shader_params, capture thread reads in capture_frame)
static CRITICAL_SECTION g_params_cs;
static bool g_params_cs_init = false;

static void params_lock_init()
{
    if (!g_params_cs_init)
    {
        InitializeCriticalSection(&g_params_cs);
        g_params_cs_init = true;
    }
}

static void params_lock()   { EnterCriticalSection(&g_params_cs); }
static void params_unlock() { LeaveCriticalSection(&g_params_cs); }

// ===== FPS LIMIT =====
static volatile LONG g_max_fps = 0;
static LARGE_INTEGER g_last_capture_tick = {};
static bool g_last_capture_tick_valid = false;
static LARGE_INTEGER g_freq = {};

static void fps_limit_init_once()
{
    if (g_freq.QuadPart == 0)
    {
        QueryPerformanceFrequency(&g_freq);
    }
}

static bool fps_limit_allow()
{
    LONG fps = g_max_fps;
    if (fps <= 0)
        return true;

    fps_limit_init_once();
    if (g_freq.QuadPart == 0)
        return true;

    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);

    if (!g_last_capture_tick_valid)
        return true;

    LONGLONG min_interval = g_freq.QuadPart / fps;
    if (min_interval <= 0)
        min_interval = 1;

    if ((now.QuadPart - g_last_capture_tick.QuadPart) < min_interval)
        return false;

    return true;
}

static void fps_limit_mark_captured()
{
    fps_limit_init_once();
    QueryPerformanceCounter(&g_last_capture_tick);
    g_last_capture_tick_valid = true;
}

// ============================================================
// PARAMS STRUCT (matches shader cbuffer)
// ============================================================

struct Params
{
    int src_w;
    int src_h;
    int dst_w;
    int dst_h;
    int pixel_limit;
    int coord_mode;
    int prec_coord;
    int prec_weights;
    int prec_color;
    int prec_accum;
    int pad0;
    int pad1;
};

// ============================================================
// DPI AWARENESS
// ============================================================

static bool enable_per_monitor_dpi_v2()
{
    DPI_AWARENESS_CONTEXT previous =
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    if (previous == nullptr)
        return false;
    return true;
}

// ============================================================

template<typename T>
void safe_release(T*& obj)
{
    if (obj)
    {
        obj->Release();
        obj = nullptr;
    }
}

// ============================================================
// COORDINATE CACHE — CPU computation
// ============================================================

static void compute_coord_cache(const int* dst_buf /*unused*/, int dst_w, int dst_h,
                                int src_w, int src_h,
                                int max_samples,
                                float* out) // out = dst_w * dst_h * 16 floats
{
    for (int y = 0; y < dst_h; y++)
    {
        for (int x = 0; x < dst_w; x++)
        {
            int idx = (y * dst_w + x) * COORD_FLOATS_PER_ENTRY;

            float fx0 = (float)x * (float)src_w / (float)dst_w;
            float fy0 = (float)y * (float)src_h / (float)dst_h;
            float fx1 = (float)(x + 1) * (float)src_w / (float)dst_w;
            float fy1 = (float)(y + 1) * (float)src_h / (float)dst_h;

            int x_start = (int)floorf(fx0);
            int y_start = (int)floorf(fy0);
            int x_end = (int)ceilf(fx1);
            int y_end = (int)ceilf(fy1);

            x_start = (x_start < 0) ? 0 : x_start;
            y_start = (y_start < 0) ? 0 : y_start;
            x_end = (x_end > src_w) ? src_w : x_end;
            y_end = (y_end > src_h) ? src_h : y_end;

            int x_span = x_end - x_start;
            int y_span = y_end - y_start;

            int x_step = 1, y_step = 1;
            if (max_samples > 0)
            {
                int total_px = src_w * src_h;
                if (total_px > max_samples)
                {
                    float ratio = (float)total_px / (float)max_samples;
                    int gstep = (int)ceilf(sqrtf(ratio));
                    if (gstep < 1) gstep = 1;
                    x_step = gstep;
                    y_step = gstep;
                }
            }

            // Write entry
            out[idx + 0] = (float)x_start;
            out[idx + 1] = (float)y_start;
            out[idx + 2] = (float)x_step;
            out[idx + 3] = (float)y_step;
            out[idx + 4] = (float)x_end;
            out[idx + 5] = (float)y_end;
            out[idx + 6] = fx0;
            out[idx + 7] = fy0;
            out[idx + 8] = fx1;
            out[idx + 9] = fy1;
            // [10..15] remain 0 (padding)
            for (int p = 10; p < COORD_FLOATS_PER_ENTRY; p++)
                out[idx + p] = 0.0f;
        }
    }
}

// Upload computed coords to GPU buffer. Returns true on success.
static bool upload_coord_cache(ID3D11Buffer* staging, ID3D11Buffer* gpu_buf,
                               int dst_w, int dst_h, int src_w, int src_h, int max_samples)
{
    if (!staging || !gpu_buf || !g_context)
        return false;

    int total_floats = dst_w * dst_h * COORD_FLOATS_PER_ENTRY;
    int byte_size = total_floats * sizeof(float);

    // Allocate temp CPU memory
    float* cpu_data = (float*)malloc(byte_size);
    if (!cpu_data) return false;

    compute_coord_cache(nullptr, dst_w, dst_h, src_w, src_h, max_samples, cpu_data);

    // Map staging, write, unmap
    D3D11_MAPPED_SUBRESOURCE ms;
    bool success = false;
    if (SUCCEEDED(g_context->Map(staging, 0, D3D11_MAP_WRITE_DISCARD, 0, &ms)))
    {
        memcpy(ms.pData, cpu_data, byte_size);
        g_context->Unmap(staging, 0);
        g_context->CopyResource(gpu_buf, staging);
        success = true;
    }

    free(cpu_data);
    return success;
}

// Allocate (or reallocate) coord cache buffers for a given stream
static void ensure_coord_buffers(int dst_w, int dst_h,
                                 ID3D11Buffer** gpuBuf,
                                 ID3D11ShaderResourceView** srv,
                                 ID3D11Buffer** staging)
{
    int total_floats = dst_w * dst_h * COORD_FLOATS_PER_ENTRY;
    int byte_size = total_floats * sizeof(float);

    if (staging && *staging)
    {
        safe_release(*staging);
        safe_release(*srv);
        safe_release(*gpuBuf);
    }

    // GPU buffer (DEFAULT usage, BIND_SHADER_RESOURCE) - typed buffer
    D3D11_BUFFER_DESC bd = {};
    bd.ByteWidth = byte_size;
    bd.Usage = D3D11_USAGE_DEFAULT;
    bd.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    g_device->CreateBuffer(&bd, nullptr, gpuBuf);

    // SRV (typed: FLOAT)
    D3D11_SHADER_RESOURCE_VIEW_DESC svd = {};
    svd.Format = DXGI_FORMAT_R32_FLOAT;
    svd.ViewDimension = D3D11_SRV_DIMENSION_BUFFER;
    svd.Buffer.FirstElement = 0;
    svd.Buffer.NumElements = total_floats;
    g_device->CreateShaderResourceView(*gpuBuf, &svd, srv);

    // Staging buffer
    D3D11_BUFFER_DESC sd = {};
    sd.ByteWidth = byte_size;
    sd.Usage = D3D11_USAGE_STAGING;
    sd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    g_device->CreateBuffer(&sd, nullptr, staging);
}

// ============================================================

static bool compile_shader()
{
    ID3DBlob* blob = nullptr;
    ID3DBlob* err = nullptr;

    HRESULT hr = D3DCompile(
        g_shader_code,
        strlen(g_shader_code),
        nullptr,
        nullptr,
        nullptr,
        "main",
        "cs_5_0",
        0,
        0,
        &blob,
        &err
    );

    if (FAILED(hr))
    {
        if (err)
        {
            OutputDebugStringA((char*)err->GetBufferPointer());
            err->Release();
        }
        return false;
    }

    hr = g_device->CreateComputeShader(
        blob->GetBufferPointer(),
        blob->GetBufferSize(),
        nullptr,
        &g_cs
    );

    blob->Release();
    return SUCCEEDED(hr);
}

// ============================================================

static bool compile_sep_shaders()
{
    // Compile pass 1
    ID3DBlob* blob = nullptr;
    ID3DBlob* err = nullptr;

    HRESULT hr = D3DCompile(
        g_sep_shader_p1,
        strlen(g_sep_shader_p1),
        nullptr, nullptr, nullptr,
        "main", "cs_5_0", 0, 0,
        &blob, &err
    );
    if (FAILED(hr))
    {
        if (err) { OutputDebugStringA((char*)err->GetBufferPointer()); err->Release(); }
        return false;
    }
    hr = g_device->CreateComputeShader(blob->GetBufferPointer(), blob->GetBufferSize(), nullptr, &g_sep_cs1);
    blob->Release();
    if (FAILED(hr)) return false;

    // Compile pass 2
    blob = nullptr; err = nullptr;
    hr = D3DCompile(
        g_sep_shader_p2,
        strlen(g_sep_shader_p2),
        nullptr, nullptr, nullptr,
        "main", "cs_5_0", 0, 0,
        &blob, &err
    );
    if (FAILED(hr))
    {
        if (err) { OutputDebugStringA((char*)err->GetBufferPointer()); err->Release(); }
        return false;
    }
    hr = g_device->CreateComputeShader(blob->GetBufferPointer(), blob->GetBufferSize(), nullptr, &g_sep_cs2);
    blob->Release();
    return SUCCEEDED(hr);
}

// Allocate (or reallocate) intermediate buffer for separable pass
static void ensure_sep_buffers(int src_w, int dst_h,
                               ID3D11Buffer** buf,
                               ID3D11UnorderedAccessView** uav,
                               ID3D11ShaderResourceView** srv,
                               int* cached_src_w,
                               int* cached_dst_h)
{
    if (*buf && *cached_src_w == src_w && *cached_dst_h == dst_h)
        return; // already correct size

    safe_release(*buf);
    safe_release(*uav);
    safe_release(*srv);

    int elements = src_w * dst_h;
    int byte_width = elements * (int)sizeof(float) * 3;

    D3D11_BUFFER_DESC desc = {};
    desc.ByteWidth = byte_width;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS | D3D11_BIND_SHADER_RESOURCE;
    desc.StructureByteStride = sizeof(float) * 3;
    desc.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    g_device->CreateBuffer(&desc, nullptr, buf);

    D3D11_UNORDERED_ACCESS_VIEW_DESC uavd = {};
    uavd.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    uavd.Buffer.NumElements = elements;
    g_device->CreateUnorderedAccessView(*buf, &uavd, uav);

    // NOTE: Must NOT use D3D11_BUFFEREX_SRV_FLAG_RAW here!
    // The shader declares StructuredBuffer<float3>, which requires a
    // non-RAW structured buffer SRV. RAW flag only allows ByteAddressBuffer.
    D3D11_SHADER_RESOURCE_VIEW_DESC svd = {};
    svd.ViewDimension = D3D11_SRV_DIMENSION_BUFFEREX;
    svd.BufferEx.FirstElement = 0;
    svd.BufferEx.NumElements = elements;
    svd.BufferEx.Flags = 0;  // no RAW — structured access
    g_device->CreateShaderResourceView(*buf, &svd, srv);

    *cached_src_w = src_w;
    *cached_dst_h = dst_h;
}

// ============================================================

static bool recreate_duplication()
{
    DPI_AWARENESS_CONTEXT previous =
        SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);

    if (previous == nullptr)
        previous = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_UNAWARE);

    safe_release(g_duplication);

    IDXGIDevice* dxgiDevice = nullptr;
    g_device->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgiDevice);

    IDXGIAdapter* adapter = nullptr;
    dxgiDevice->GetAdapter(&adapter);

    IDXGIOutput* output = nullptr;
    adapter->EnumOutputs(g_output_index, &output);

    IDXGIOutput6* output6 = nullptr;
    output->QueryInterface(__uuidof(IDXGIOutput6), (void**)&output6);

    DXGI_FORMAT formats[] = {
        DXGI_FORMAT_R16G16B16A16_FLOAT,
        DXGI_FORMAT_B8G8R8A8_UNORM
    };

    HRESULT hr = output6->DuplicateOutput1(
        g_device,
        0,
        2,
        formats,
        &g_duplication
    );

    SetThreadDpiAwarenessContext(previous);

    safe_release(output6);
    safe_release(output);
    safe_release(adapter);
    safe_release(dxgiDevice);

    return SUCCEEDED(hr);
}

// ============================================================

DLL_EXPORT bool init_capture(int output_index, int dst_w, int dst_h)
{
    g_output_index = output_index;
    g_dst_w = dst_w;
    g_dst_h = dst_h;
    g_frame_id = 0;

    D3D_FEATURE_LEVEL fl;

    UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;

    if (FAILED(D3D11CreateDevice(
        nullptr,
        D3D_DRIVER_TYPE_HARDWARE,
        nullptr,
        flags,
        nullptr,
        0,
        D3D11_SDK_VERSION,
        &g_device,
        &fl,
        &g_context
    )))
        return false;

    // ===== GPU PRIORITY =====
    constexpr INT GPU_PRIORITY_ABSOLUTE = (1 << 30) | 30;

    IDXGIDevice* dxgiDevice = nullptr;
    if (SUCCEEDED(g_device->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgiDevice)))
    {
        dxgiDevice->SetGPUThreadPriority(GPU_PRIORITY_ABSOLUTE);
        dxgiDevice->Release();
    }

    if (!recreate_duplication())
        return false;

    if (!compile_shader())
        return false;

    // Compile separable 2-pass shaders (optional, non-fatal)
    if (g_use_separable)
    {
        if (!compile_sep_shaders())
        {
            // Fall back to legacy single-dispatch path
            g_use_separable = false;
        }
    }

    // ===== OUTPUT BUFFER (stream 1) =====
    D3D11_BUFFER_DESC desc = {};
    desc.ByteWidth = dst_w * dst_h * sizeof(float) * 3;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
    desc.StructureByteStride = sizeof(float) * 3;
    desc.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;

    g_device->CreateBuffer(&desc, nullptr, &g_outputBuffer);

    D3D11_UNORDERED_ACCESS_VIEW_DESC uavd = {};
    uavd.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    uavd.Buffer.NumElements = dst_w * dst_h;
    g_device->CreateUnorderedAccessView(g_outputBuffer, &uavd, &g_uav);

    // ===== READBACK (stream 1) =====
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.MiscFlags = 0;
    g_device->CreateBuffer(&desc, nullptr, &g_readback);

    // ===== CONST BUFFER (DYNAMIC) =====
    D3D11_BUFFER_DESC cbd = {};
    cbd.ByteWidth = sizeof(Params);
    cbd.Usage = D3D11_USAGE_DYNAMIC;
    cbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;
    cbd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    g_device->CreateBuffer(&cbd, nullptr, &g_constBuffer);

    // ===== COORD CACHE (stream 1) =====
    ensure_coord_buffers(dst_w, dst_h, &g_coordBuf1, &g_coordSrv1, &g_coordStaging1);
    g_coord_dirty1 = true;

    return true;
}

// ============================================================

DLL_EXPORT void set_second_resolution(int w, int h)
{
    g_dst2_w = w;
    g_dst2_h = h;

    // Release old stream-2 resources
    safe_release(g_outputBuffer2);
    safe_release(g_uav2);
    safe_release(g_readback2);
    safe_release(g_coordBuf2);
    safe_release(g_coordSrv2);
    safe_release(g_coordStaging2);
    // Release separable buffers (stream 2)
    safe_release(g_sepBuf2);
    safe_release(g_sepUAV2);
    safe_release(g_sepSRV2);
    g_sepBuf2_src_w = 0;
    g_sepBuf2_dst_h = 0;

    if (w <= 0 || h <= 0)
    {
        g_coord_dirty2 = true;
        return;
    }

    D3D11_BUFFER_DESC desc = {};
    desc.ByteWidth = w * h * sizeof(float) * 3;
    desc.Usage = D3D11_USAGE_DEFAULT;
    desc.BindFlags = D3D11_BIND_UNORDERED_ACCESS;
    desc.StructureByteStride = sizeof(float) * 3;
    desc.MiscFlags = D3D11_RESOURCE_MISC_BUFFER_STRUCTURED;
    g_device->CreateBuffer(&desc, nullptr, &g_outputBuffer2);

    D3D11_UNORDERED_ACCESS_VIEW_DESC uavd = {};
    uavd.ViewDimension = D3D11_UAV_DIMENSION_BUFFER;
    uavd.Buffer.NumElements = w * h;
    g_device->CreateUnorderedAccessView(g_outputBuffer2, &uavd, &g_uav2);

    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    g_device->CreateBuffer(&desc, nullptr, &g_readback2);

    // ===== COORD CACHE (stream 2) =====
    ensure_coord_buffers(w, h, &g_coordBuf2, &g_coordSrv2, &g_coordStaging2);
    g_coord_dirty2 = true;
}

// ============================================================
// NEW API: set_shader_params
// ============================================================

DLL_EXPORT void set_shader_params(
    int pixel_limit,     // 0 = unlimited, >0 = max total source pixels
    int coord_mode,      // 0 = frame (recalc), 1 = once (cache)
    int prec_coord,      // 0 = fp32, 1 = fp16
    int prec_weights,    // 0 = fp32, 1 = fp16
    int prec_color,      // 0 = fp32, 1 = fp16
    int prec_accum)      // 0 = fp32, 1 = fp16
{
    if (pixel_limit < 0) pixel_limit = 0;

    params_lock_init();
    params_lock();

    g_pixel_limit = pixel_limit;
    g_coord_mode = (coord_mode != 0) ? 1 : 0;
    g_prec_coord = (prec_coord != 0) ? 1 : 0;
    g_prec_weights = (prec_weights != 0) ? 1 : 0;
    g_prec_color = (prec_color != 0) ? 1 : 0;
    g_prec_accum = (prec_accum != 0) ? 1 : 0;

    // Changing sample count invalidates coord cache (steps change)
    g_coord_dirty1 = true;
    g_coord_dirty2 = true;

    params_unlock();
}

// ============================================================

DLL_EXPORT bool capture_frame()
{
    if (!g_duplication)
        return false;

    if (!fps_limit_allow())
        return false;

    IDXGIResource* resource = nullptr;
    DXGI_OUTDUPL_FRAME_INFO info = {};

    HRESULT hr = g_duplication->AcquireNextFrame(1, &info, &resource);

    if (hr == DXGI_ERROR_WAIT_TIMEOUT)
        return false;

    if (FAILED(hr))
    {
        if (hr == DXGI_ERROR_ACCESS_LOST)
        {
            recreate_duplication();
        }
        return false;
    }

    if (info.LastPresentTime.QuadPart == 0)
    {
        safe_release(resource);
        g_duplication->ReleaseFrame();
        return false;
    }

    ID3D11Texture2D* tex = nullptr;
    resource->QueryInterface(__uuidof(ID3D11Texture2D), (void**)&tex);

    D3D11_TEXTURE2D_DESC desc;
    tex->GetDesc(&desc);

    int src_w = (int)desc.Width;
    int src_h = (int)desc.Height;
    g_is_hdr = (desc.Format == DXGI_FORMAT_R16G16B16A16_FLOAT);

    // ===== SRV =====
    safe_release(g_srv);
    {
        D3D11_SHADER_RESOURCE_VIEW_DESC srvd = {};
        srvd.Format = desc.Format;
        srvd.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        srvd.Texture2D.MipLevels = 1;
        g_device->CreateShaderResourceView(tex, &srvd, &g_srv);
    }

    // ===== COORDINATE CACHE INVALIDATION =====
    // If coord mode is "once", we need valid cache. Invalidate if src or dst changed.
    // Use a snapshot of params under lock to avoid torn reads with set_shader_params.
    params_lock_init();
    params_lock();
    int snap_coord_mode = g_coord_mode;
    int snap_pixel_limit = g_pixel_limit;
    bool snap_dirty1 = g_coord_dirty1;
    bool snap_dirty2 = g_coord_dirty2;
    params_unlock();

    if (snap_coord_mode != 0)
    {
        // Stream 1: check if src or dst1 changed since last compute
        if (snap_dirty1 ||
            g_coord_last_src_w1 != src_w || g_coord_last_src_h1 != src_h)
        {
            if (g_dst_w > 0 && g_dst_h > 0 && g_coordStaging1 && g_coordBuf1)
            {
                if (upload_coord_cache(g_coordStaging1, g_coordBuf1,
                    g_dst_w, g_dst_h, src_w, src_h, snap_pixel_limit))
                {
                    params_lock();
                    g_coord_last_src_w1 = src_w;
                    g_coord_last_src_h1 = src_h;
                    g_coord_dirty1 = false;
                    params_unlock();
                }
                // On failure: keep dirty=true so we retry next frame, and fall back below
            }
        }

        // Stream 2
        if (g_dst2_w > 0 && g_dst2_h > 0)
        {
            if (snap_dirty2 ||
                g_coord_last_src_w2 != src_w || g_coord_last_src_h2 != src_h)
            {
                if (g_coordStaging2 && g_coordBuf2)
                {
                    if (upload_coord_cache(g_coordStaging2, g_coordBuf2,
                        g_dst2_w, g_dst2_h, src_w, src_h, snap_pixel_limit))
                    {
                        params_lock();
                        g_coord_last_src_w2 = src_w;
                        g_coord_last_src_h2 = src_h;
                        g_coord_dirty2 = false;
                        params_unlock();
                    }
                }
            }
        }
    }

    // ===== DISPATCH (stream 1) =====
    params_lock();
    int p_pixel_limit = g_pixel_limit;
    int p_prec_weights = g_prec_weights;
    int p_prec_color = g_prec_color;
    int p_prec_accum = g_prec_accum;
    bool use_sep = g_use_separable && g_sep_cs1 && g_sep_cs2;
    params_unlock();

    if (use_sep)
    {
        // ---- SEPARABLE 2-PASS (stream 1) ----
        // Ensure intermediate buffer is sized for current src_w × dst_h
        ensure_sep_buffers(src_w, g_dst_h, &g_sepBuf, &g_sepUAV, &g_sepSRV,
                           &g_sepBuf_src_w, &g_sepBuf_dst_h);

        if (g_sepBuf && g_sepUAV && g_sepSRV)
        {
            // --- Pass 1: vertical sum → temp[src_w × dst_h] ---
            D3D11_MAPPED_SUBRESOURCE cm;
            if (SUCCEEDED(g_context->Map(g_constBuffer, 0, D3D11_MAP_WRITE_DISCARD, 0, &cm)))
            {
                Params p = {};
                p.src_w = src_w;
                p.src_h = src_h;
                p.dst_w = g_dst_w;
                p.dst_h = g_dst_h;
                p.pixel_limit = p_pixel_limit;
                p.coord_mode = 0;
                p.prec_weights = p_prec_weights;
                p.prec_color = p_prec_color;
                p.prec_accum = p_prec_accum;
                p.pad0 = 0; p.pad1 = 0;
                memcpy(cm.pData, &p, sizeof(Params));
                g_context->Unmap(g_constBuffer, 0);
            }

            g_context->CSSetShader(g_sep_cs1, nullptr, 0);
            g_context->CSSetShaderResources(0, 1, &g_srv);
            g_context->CSSetUnorderedAccessViews(0, 1, &g_sepUAV, nullptr);
            g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);
            g_context->Dispatch((src_w + 7) / 8, (g_dst_h + 7) / 8, 1);

            // Unbind pass-1 outputs before pass-2 binds them
            ID3D11ShaderResourceView* nullSRV[1] = { nullptr };
            ID3D11UnorderedAccessView* nullUAV[1] = { nullptr };
            g_context->CSSetShaderResources(0, 1, nullSRV);
            g_context->CSSetUnorderedAccessViews(0, 1, nullUAV, nullptr);

            // --- Pass 2: horizontal sum + normalize → output[dst_w × dst_h] ---
            g_context->CSSetShader(g_sep_cs2, nullptr, 0);
            g_context->CSSetShaderResources(0, 1, &g_sepSRV);
            g_context->CSSetUnorderedAccessViews(0, 1, &g_uav, nullptr);
            g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);
            g_context->Dispatch((g_dst_w + 7) / 8, (g_dst_h + 7) / 8, 1);

            g_context->CSSetShaderResources(0, 1, nullSRV);
            g_context->CSSetUnorderedAccessViews(0, 1, nullUAV, nullptr);

            g_context->CopyResource(g_readback, g_outputBuffer);
        }
        else
        {
            // Fallback: legacy single-dispatch
            g_context->CSSetShader(g_cs, nullptr, 0);
            g_context->CSSetShaderResources(0, 1, &g_srv);
            g_context->CSSetUnorderedAccessViews(0, 1, &g_uav, nullptr);
            g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);
            g_context->Dispatch((g_dst_w + 7) / 8, (g_dst_h + 7) / 8, 1);
            g_context->CopyResource(g_readback, g_outputBuffer);
        }
    }
    else
    {
        // ---- LEGACY SINGLE DISPATCH (stream 1) ----
        params_lock();
        int p_coord_mode = g_coord_mode;
        bool p_dirty1 = g_coord_dirty1;
        params_unlock();

        int eff_coord_mode1 = p_coord_mode;
        if (eff_coord_mode1 != 0 && (p_dirty1 || !g_coordSrv1))
            eff_coord_mode1 = 0;

        D3D11_MAPPED_SUBRESOURCE cm;
        if (SUCCEEDED(g_context->Map(g_constBuffer, 0, D3D11_MAP_WRITE_DISCARD, 0, &cm)))
        {
            Params p = {};
            p.src_w = src_w;
            p.src_h = src_h;
            p.dst_w = g_dst_w;
            p.dst_h = g_dst_h;
            p.pixel_limit = p_pixel_limit;
            p.coord_mode = eff_coord_mode1;
            p.prec_weights = p_prec_weights;
            p.prec_color = p_prec_color;
            p.prec_accum = p_prec_accum;
            p.pad0 = 0;
            p.pad1 = 0;
            memcpy(cm.pData, &p, sizeof(Params));
            g_context->Unmap(g_constBuffer, 0);
        }

        g_context->CSSetShader(g_cs, nullptr, 0);

        ID3D11ShaderResourceView* coord_bind = nullptr;
        if (eff_coord_mode1 != 0 && g_coordSrv1)
            coord_bind = g_coordSrv1;

        ID3D11ShaderResourceView* srvs[2] = { g_srv, coord_bind };
        g_context->CSSetShaderResources(0, 2, srvs);
        g_context->CSSetUnorderedAccessViews(0, 1, &g_uav, nullptr);
        g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);

        g_context->Dispatch((g_dst_w + 7) / 8, (g_dst_h + 7) / 8, 1);
        g_context->CopyResource(g_readback, g_outputBuffer);
    }

    // ===== SECOND STREAM =====
    if (g_dst2_w > 0 && g_dst2_h > 0 && g_uav2)
    {
        params_lock();
        int p2_pixel_limit = g_pixel_limit;
        int p2_prec_weights = g_prec_weights;
        int p2_prec_color = g_prec_color;
        int p2_prec_accum = g_prec_accum;
        bool use_sep2 = g_use_separable && g_sep_cs1 && g_sep_cs2;
        params_unlock();

        if (use_sep2)
        {
            // ---- SEPARABLE 2-PASS (stream 2) ----
            ensure_sep_buffers(src_w, g_dst2_h, &g_sepBuf2, &g_sepUAV2, &g_sepSRV2,
                               &g_sepBuf2_src_w, &g_sepBuf2_dst_h);

            if (g_sepBuf2 && g_sepUAV2 && g_sepSRV2)
            {
                D3D11_MAPPED_SUBRESOURCE cm2;
                if (SUCCEEDED(g_context->Map(g_constBuffer, 0, D3D11_MAP_WRITE_DISCARD, 0, &cm2)))
                {
                    Params p2 = {};
                    p2.src_w = src_w;
                    p2.src_h = src_h;
                    p2.dst_w = g_dst2_w;
                    p2.dst_h = g_dst2_h;
                    p2.pixel_limit = p2_pixel_limit;
                    p2.coord_mode = 0;
                    p2.prec_weights = p2_prec_weights;
                    p2.prec_color = p2_prec_color;
                    p2.prec_accum = p2_prec_accum;
                    p2.pad0 = 0; p2.pad1 = 0;
                    memcpy(cm2.pData, &p2, sizeof(Params));
                    g_context->Unmap(g_constBuffer, 0);
                }

                g_context->CSSetShader(g_sep_cs1, nullptr, 0);
                g_context->CSSetShaderResources(0, 1, &g_srv);
                g_context->CSSetUnorderedAccessViews(0, 1, &g_sepUAV2, nullptr);
                g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);
                g_context->Dispatch((src_w + 7) / 8, (g_dst2_h + 7) / 8, 1);

                ID3D11ShaderResourceView* nullSRV[1] = { nullptr };
                ID3D11UnorderedAccessView* nullUAV[1] = { nullptr };
                g_context->CSSetShaderResources(0, 1, nullSRV);
                g_context->CSSetUnorderedAccessViews(0, 1, nullUAV, nullptr);

                g_context->CSSetShader(g_sep_cs2, nullptr, 0);
                g_context->CSSetShaderResources(0, 1, &g_sepSRV2);
                g_context->CSSetUnorderedAccessViews(0, 1, &g_uav2, nullptr);
                g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);
                g_context->Dispatch((g_dst2_w + 7) / 8, (g_dst2_h + 7) / 8, 1);

                g_context->CSSetShaderResources(0, 1, nullSRV);
                g_context->CSSetUnorderedAccessViews(0, 1, nullUAV, nullptr);

                g_context->CopyResource(g_readback2, g_outputBuffer2);
            }
            else
            {
                g_context->CSSetShader(g_cs, nullptr, 0);
                g_context->CSSetShaderResources(0, 1, &g_srv);
                g_context->CSSetUnorderedAccessViews(0, 1, &g_uav2, nullptr);
                g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);
                g_context->Dispatch((g_dst2_w + 7) / 8, (g_dst2_h + 7) / 8, 1);
                g_context->CopyResource(g_readback2, g_outputBuffer2);
            }
        }
        else
        {
            // ---- LEGACY SINGLE DISPATCH (stream 2) ----
            params_lock();
            int p2_coord_mode = g_coord_mode;
            bool p2_dirty2 = g_coord_dirty2;
            params_unlock();

            if (p2_coord_mode != 0 && (p2_dirty2 || !g_coordSrv2))
                p2_coord_mode = 0;

            D3D11_MAPPED_SUBRESOURCE cm2;
            if (SUCCEEDED(g_context->Map(g_constBuffer, 0, D3D11_MAP_WRITE_DISCARD, 0, &cm2)))
            {
                Params p2 = {};
                p2.src_w = src_w;
                p2.src_h = src_h;
                p2.dst_w = g_dst2_w;
                p2.dst_h = g_dst2_h;
                p2.pixel_limit = p2_pixel_limit;
                p2.coord_mode = p2_coord_mode;
                p2.prec_weights = p2_prec_weights;
                p2.prec_color = p2_prec_color;
                p2.prec_accum = p2_prec_accum;
                p2.pad0 = 0;
                p2.pad1 = 0;
                memcpy(cm2.pData, &p2, sizeof(Params));
                g_context->Unmap(g_constBuffer, 0);
            }

            ID3D11ShaderResourceView* coord_bind2 = nullptr;
            if (p2_coord_mode != 0 && g_coordSrv2)
                coord_bind2 = g_coordSrv2;

            ID3D11ShaderResourceView* srvs2[2] = { g_srv, coord_bind2 };
            g_context->CSSetShaderResources(0, 2, srvs2);
            g_context->CSSetUnorderedAccessViews(0, 1, &g_uav2, nullptr);
            g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);

            g_context->Dispatch((g_dst2_w + 7) / 8, (g_dst2_h + 7) / 8, 1);
            g_context->CopyResource(g_readback2, g_outputBuffer2);
        }
    }

    // ===== UNBIND =====
    ID3D11ShaderResourceView* nullSRVs[2] = { nullptr, nullptr };
    ID3D11UnorderedAccessView* nullUAV[1] = { nullptr };
    g_context->CSSetShaderResources(0, 2, nullSRVs);
    g_context->CSSetUnorderedAccessViews(0, 1, nullUAV, nullptr);

    safe_release(tex);
    safe_release(resource);

    bool is_new_frame = (info.AccumulatedFrames > 0);

    g_duplication->ReleaseFrame();

    if (is_new_frame)
    {
        g_frame_id++;
        fps_limit_mark_captured();
        return true;
    }

    return false;
}

// ============================================================

DLL_EXPORT void set_capture_fps(int fps)
{
    if (fps < 0) fps = 0;
    g_max_fps = fps;
    g_last_capture_tick_valid = false;
    g_last_capture_tick.QuadPart = 0;
}

// ============================================================

DLL_EXPORT bool copy_frame(float* dst, int max_bytes)
{
    if (!g_readback)
        return false;

    D3D11_MAPPED_SUBRESOURCE m;

    if (FAILED(g_context->Map(g_readback, 0, D3D11_MAP_READ, 0, &m)))
        return false;

    int size = g_dst_w * g_dst_h * sizeof(float) * 3;

    if (size > max_bytes)
    {
        g_context->Unmap(g_readback, 0);
        return false;
    }

    memcpy(dst, m.pData, size);
    g_context->Unmap(g_readback, 0);

    return true;
}

// ============================================================

DLL_EXPORT bool copy_frame2(float* dst, int max_bytes)
{
    if (!g_readback2 || g_dst2_w <= 0 || g_dst2_h <= 0)
        return false;

    D3D11_MAPPED_SUBRESOURCE m;

    if (FAILED(g_context->Map(g_readback2, 0, D3D11_MAP_READ, 0, &m)))
        return false;

    int size = g_dst2_w * g_dst2_h * sizeof(float) * 3;

    if (size > max_bytes)
    {
        g_context->Unmap(g_readback2, 0);
        return false;
    }

    memcpy(dst, m.pData, size);
    g_context->Unmap(g_readback2, 0);

    return true;
}

// ============================================================

DLL_EXPORT int get_frame_size_bytes()
{
    return g_dst_w * g_dst_h * sizeof(float) * 3;
}

DLL_EXPORT int get_frame_size_bytes2()
{
    return g_dst2_w * g_dst2_h * sizeof(float) * 3;
}

DLL_EXPORT bool is_hdr()
{
    return g_is_hdr;
}

DLL_EXPORT unsigned long long get_frame_id()
{
    return g_frame_id;
}

// ============================================================

// ============================================================

DLL_EXPORT void set_separable_mode(int enable)
{
    params_lock_init();
    params_lock();
    g_use_separable = (enable != 0);
    params_unlock();

    // If enabling and shaders not yet compiled, try now
    if (g_use_separable && !g_sep_cs1 && g_device)
    {
        compile_sep_shaders();
    }
}

DLL_EXPORT int get_separable_mode()
{
    return g_use_separable ? 1 : 0;
}

// ============================================================

DLL_EXPORT void shutdown_capture()
{
    safe_release(g_cs);
    safe_release(g_sep_cs1);
    safe_release(g_sep_cs2);
    safe_release(g_sepBuf);
    safe_release(g_sepUAV);
    safe_release(g_sepSRV);
    safe_release(g_sepBuf2);
    safe_release(g_sepUAV2);
    safe_release(g_sepSRV2);
    safe_release(g_uav);
    safe_release(g_uav2);
    safe_release(g_srv);
    safe_release(g_outputBuffer);
    safe_release(g_outputBuffer2);
    safe_release(g_readback);
    safe_release(g_readback2);
    safe_release(g_constBuffer);
    safe_release(g_coordBuf1);
    safe_release(g_coordBuf2);
    safe_release(g_coordSrv1);
    safe_release(g_coordSrv2);
    safe_release(g_coordStaging1);
    safe_release(g_coordStaging2);
    safe_release(g_duplication);
    safe_release(g_context);
    safe_release(g_device);
}
