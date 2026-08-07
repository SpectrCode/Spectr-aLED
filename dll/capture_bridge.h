#pragma once

#ifdef _WIN32
    #define DLL_EXPORT extern "C" __declspec(dllexport)
#else
    #define DLL_EXPORT extern "C"
#endif

DLL_EXPORT bool init_capture(int output_index, int dst_w, int dst_h);
DLL_EXPORT bool capture_frame();

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