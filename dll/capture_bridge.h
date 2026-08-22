#pragma once

#ifdef _WIN32
    #define DLL_EXPORT extern "C" __declspec(dllexport)
#else
    #define DLL_EXPORT extern "C"
#endif

DLL_EXPORT bool init_capture(int output_index, int dst_w, int dst_h);
DLL_EXPORT bool capture_frame();

// Устанавливает максимальную частоту кадров захвата (0 = без лимита, адаптивно)
DLL_EXPORT void set_capture_fps(int fps);

DLL_EXPORT void* get_frame_buffer();
DLL_EXPORT int get_frame_size_bytes();
DLL_EXPORT bool is_hdr();

// 🔥 новый счетчик новых кадров
DLL_EXPORT unsigned long long get_frame_id();

DLL_EXPORT void shutdown_capture();

// ============================
// 🔥 SECOND STREAM API
// ============================

DLL_EXPORT void set_second_resolution(int w, int h);
DLL_EXPORT bool copy_frame2(float* dst, int max_bytes);
DLL_EXPORT int get_frame_size_bytes2();

// ============================
// SHADER RUNTIME PARAMS
// ============================

DLL_EXPORT void set_shader_params(
    int max_samples,     // 0 = unlimited, >0 = cap per axis
    int coord_mode,      // 0 = frame (recalc each frame), 1 = once (cache)
    int prec_coord,      // 0 = fp32, 1 = fp16
    int prec_weights,    // 0 = fp32, 1 = fp16
    int prec_color,      // 0 = fp32, 1 = fp16
    int prec_accum);     // 0 = fp32, 1 = fp16

// ============================
// SEPARABLE 2-PASS MODE
// ============================
// Включает/выключает separable 2-pass pipeline.
// При включении: box filter разбивается на 2 1D-прохода,
// что устраняет warp divergence при непропорциональном scale.
// Результат идентичен, качество не теряется.
DLL_EXPORT void set_separable_mode(int enable);  // 0 = off, 1 = on (default: on)
DLL_EXPORT int get_separable_mode();
