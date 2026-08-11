#define DLL_EXPORT extern "C" __declspec(dllexport)

#include <windows.h>
#include <d3d11.h>
#include <d3d11_1.h>
#include <dxgi1_6.h>
#include <d3dcompiler.h>
#include <shellscalingapi.h>

#pragma comment(lib, "d3d11.lib")
#pragma comment(lib, "dxgi.lib")
#pragma comment(lib, "d3dcompiler.lib")
#pragma comment(lib, "Shcore.lib")

// ============================================================
// SHADER
// ============================================================

static const char* g_shader_code = R"(
Texture2D<float4> srcTex : register(t0);
RWStructuredBuffer<float3> dstBuf : register(u0);

cbuffer Params : register(b0)
{
    int src_w;
    int src_h;
    int dst_w;
    int dst_h;
};

[numthreads(8,8,1)]
void main(uint3 id : SV_DispatchThreadID)
{
    if (id.x >= dst_w || id.y >= dst_h)
        return;

    // Координаты в исходном изображении (float для точного area computing)
    float src_x0 = (float)(id.x) * (float)src_w / (float)dst_w;
    float src_y0 = (float)(id.y) * (float)src_h / (float)dst_h;

    // Координаты конца выходного пикселя в исходном изображении
    float src_x1 = (float)(id.x + 1) * (float)src_w / (float)dst_w;
    float src_y1 = (float)(id.y + 1) * (float)src_h / (float)dst_h;

    // Область покрытия в исходном изображении
    int y_start = (int)floor(src_y0);
    int y_end = (int)ceil(src_y1);
    int x_start = (int)floor(src_x0);
    int x_end = (int)ceil(src_x1);

    // Ограничение границами исходного изображения
    y_start = max(y_start, 0);
    y_end = min(y_end, src_h);
    x_start = max(x_start, 0);
    x_end = min(x_end, src_w);

    float3 sum = float3(0,0,0);
    float total_weight = 0.0f;

    for (int y = y_start; y < y_end; y++)
    {
        float sy0 = (float)y;
        float sy1 = (float)(y + 1);
        
        // Вертикальный вес пересечения
        float v_weight = min(sy1, src_y1) - max(sy0, src_y0);
        if (v_weight <= 0.0f) continue;

        for (int x = x_start; x < x_end; x++)
        {
            float sx0 = (float)x;
            float sx1 = (float)(x + 1);
            
            // Горизонтальный вес пересечения
            float h_weight = min(sx1, src_x1) - max(sx0, src_x0);
            if (h_weight <= 0.0f) continue;

            // Вес пикселя - площадь его пересечения с выходным пикселем
            float pixel_weight = v_weight * h_weight;
            
            float4 color = srcTex.Load(int3(x, y, 0));
            float3 c = float3(color.z, color.y, color.x);
            
            sum += c * pixel_weight;
            total_weight += pixel_weight;
        }
    }

    // Нормализация по суммарному весу
    if (total_weight > 0.0f)
    {
        sum /= total_weight;
    }

    int idx = id.y * dst_w + id.x;
    dstBuf[idx] = sum;
}
)";

// ============================================================
// GLOBALS
// ============================================================

static ID3D11Device* g_device = nullptr;
static ID3D11DeviceContext* g_context = nullptr;
static IDXGIOutputDuplication* g_duplication = nullptr;

static ID3D11ComputeShader* g_cs = nullptr;
static ID3D11Buffer* g_outputBuffer = nullptr;
static ID3D11UnorderedAccessView* g_uav = nullptr;
static ID3D11ShaderResourceView* g_srv = nullptr;
static ID3D11Buffer* g_constBuffer = nullptr;
static ID3D11Buffer* g_readback = nullptr;

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

// ============================================================

struct Params
{
    int src_w;
    int src_h;
    int dst_w;
    int dst_h;
};

// ============================================================
// DPI AWARENESS
// ============================================================

static bool enable_per_monitor_dpi_v2()
{
    // Устанавливаем DPI awareness для текущего потока.
    // Это важно для корректного определения физических координат
    // монитора/output при работе с DXGI Desktop Duplication.

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

static bool recreate_duplication()
{
    // На случай, если функция вызывается после ACCESS_LOST
    // или из другого потока.
    SetThreadDpiAwarenessContext(
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    );

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

    safe_release(output6);
    safe_release(output);
    safe_release(adapter);
    safe_release(dxgiDevice);

    return SUCCEEDED(hr);
}

// ============================================================

DLL_EXPORT bool init_capture(int output_index, int dst_w, int dst_h)
{
    // ========================================================
    // DPI — ОБЯЗАТЕЛЬНО ДО DXGI / OUTPUT
    // ========================================================

    if (!enable_per_monitor_dpi_v2())
        return false;

    g_output_index = output_index;
    g_dst_w = dst_w;
    g_dst_h = dst_h;
    g_frame_id = 0;

    D3D_FEATURE_LEVEL fl;

    UINT flags = D3D11_CREATE_DEVICE_BGRA_SUPPORT;
    // flags |= D3D11_CREATE_DEVICE_DEBUG; // опционально

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

    // ===== ПРИОРИТЕТ GPU =====


    IDXGIDevice* dxgiDevice = nullptr;
    if (SUCCEEDED(g_device->QueryInterface(__uuidof(IDXGIDevice), (void**)&dxgiDevice)))
    {
        dxgiDevice->SetGPUThreadPriority(7);
        dxgiDevice->Release();
    }

    // =========================

    if (!recreate_duplication())
        return false;

    if (!compile_shader())
        return false;

    // ===== OUTPUT BUFFER =====
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

    // ===== READBACK =====
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.MiscFlags = 0;

    g_device->CreateBuffer(&desc, nullptr, &g_readback);

    // ===== CONST BUFFER =====
    D3D11_BUFFER_DESC cbd = {};
    cbd.ByteWidth = sizeof(Params);
    cbd.Usage = D3D11_USAGE_DEFAULT;
    cbd.BindFlags = D3D11_BIND_CONSTANT_BUFFER;

    g_device->CreateBuffer(&cbd, nullptr, &g_constBuffer);

    return true;
}

// ============================================================

DLL_EXPORT void set_second_resolution(int w, int h)
{
    g_dst2_w = w;
    g_dst2_h = h;

    safe_release(g_outputBuffer2);
    safe_release(g_uav2);
    safe_release(g_readback2);

    if (w <= 0 || h <= 0)
        return;

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
}

// ============================================================

DLL_EXPORT bool capture_frame()
{
    if (!g_duplication)
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

    g_is_hdr = (desc.Format == DXGI_FORMAT_R16G16B16A16_FLOAT);

    // ===== SRV (с оптимизацией) =====
    static DXGI_FORMAT lastFormat = DXGI_FORMAT_UNKNOWN;

    if (!g_srv || lastFormat != desc.Format)
    {
        safe_release(g_srv);

        D3D11_SHADER_RESOURCE_VIEW_DESC srvd = {};
        srvd.Format = desc.Format;
        srvd.ViewDimension = D3D11_SRV_DIMENSION_TEXTURE2D;
        srvd.Texture2D.MipLevels = 1;

        g_device->CreateShaderResourceView(tex, &srvd, &g_srv);
        lastFormat = desc.Format;
    }

    // ===== FIRST STREAM =====
    Params p = { (int)desc.Width, (int)desc.Height, g_dst_w, g_dst_h };

    g_context->UpdateSubresource(g_constBuffer, 0, nullptr, &p, 0, 0);

    g_context->CSSetShader(g_cs, nullptr, 0);
    g_context->CSSetShaderResources(0, 1, &g_srv);
    g_context->CSSetUnorderedAccessViews(0, 1, &g_uav, nullptr);
    g_context->CSSetConstantBuffers(0, 1, &g_constBuffer);

    g_context->Dispatch((g_dst_w + 7) / 8, (g_dst_h + 7) / 8, 1);

    g_context->CopyResource(g_readback, g_outputBuffer);

    // ===== SECOND STREAM =====
    if (g_dst2_w > 0 && g_dst2_h > 0 && g_uav2)
    {
        Params p2 = { (int)desc.Width, (int)desc.Height, g_dst2_w, g_dst2_h };

        g_context->UpdateSubresource(g_constBuffer, 0, nullptr, &p2, 0, 0);
        g_context->CSSetUnorderedAccessViews(0, 1, &g_uav2, nullptr);

        g_context->Dispatch((g_dst2_w + 7) / 8, (g_dst2_h + 7) / 8, 1);

        g_context->CopyResource(g_readback2, g_outputBuffer2);
    }

    // ===== UNBIND (ВАЖНО) =====
    ID3D11ShaderResourceView* nullSRV[1] = { nullptr };
    ID3D11UnorderedAccessView* nullUAV[1] = { nullptr };

    g_context->CSSetShaderResources(0, 1, nullSRV);
    g_context->CSSetUnorderedAccessViews(0, 1, nullUAV, nullptr);


    safe_release(tex);
    safe_release(resource);

    bool is_new_frame = (info.AccumulatedFrames > 0);

    g_duplication->ReleaseFrame();

    if (is_new_frame)
    {
        g_frame_id++;
        return true;
    }

    return false;
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

DLL_EXPORT void shutdown_capture()
{
    safe_release(g_cs);
    safe_release(g_uav);
    safe_release(g_uav2);
    safe_release(g_srv);
    safe_release(g_outputBuffer);
    safe_release(g_outputBuffer2);
    safe_release(g_readback);
    safe_release(g_readback2);
    safe_release(g_constBuffer);
    safe_release(g_duplication);
    safe_release(g_context);
    safe_release(g_device);
}