"""
Module for working with capture_bridge.dll
"""

import ctypes

# Import path utilities
from path_utils import get_dll_path

# Configuration constants (embedded - no external config file)
TARGET_W = 120
TARGET_H = 68


class CaptureBridge:
    """Class for managing capture bridge DLL"""
    
    def __init__(self):
        self.dll = None
        self.lock = ctypes.CDLL.__new__(ctypes.CDLL)  # Placeholder for lock
        
        # Path to DLL
        dll_path = get_dll_path()
        
        try:
            self.dll = ctypes.CDLL(dll_path)
            self._setup_functions()
            print(f"[OK] Capture bridge loaded: {dll_path}")
        except Exception as e:
            print(f"[ERROR] Failed to load capture bridge: {e}")
            raise
    
    def _setup_functions(self):
        """Setup function argument types and return values"""
        
        # init_capture
        self.dll.init_capture.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.dll.init_capture.restype = ctypes.c_bool
        
        # capture_frame
        self.dll.capture_frame.argtypes = []
        self.dll.capture_frame.restype = ctypes.c_bool
        
        # set_capture_fps (0 = без лимита, адаптивно)
        self.dll.set_capture_fps.argtypes = [ctypes.c_int]
        self.dll.set_capture_fps.restype = None
        
        # copy_frame
        self.dll.copy_frame.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int
        ]
        self.dll.copy_frame.restype = ctypes.c_bool
        
        # get_frame_size_bytes
        self.dll.get_frame_size_bytes.argtypes = []
        self.dll.get_frame_size_bytes.restype = ctypes.c_int
        
        # get_frame_id
        self.dll.get_frame_id.argtypes = []
        self.dll.get_frame_id.restype = ctypes.c_ulonglong
        
        # is_hdr
        self.dll.is_hdr.argtypes = []
        self.dll.is_hdr.restype = ctypes.c_bool
        
        # shutdown_capture
        self.dll.shutdown_capture.argtypes = []
        self.dll.shutdown_capture.restype = None
        
        # set_second_resolution
        self.dll.set_second_resolution.argtypes = [ctypes.c_int, ctypes.c_int]
        self.dll.set_second_resolution.restype = None
        
        # copy_frame2
        self.dll.copy_frame2.argtypes = [
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int
        ]
        self.dll.copy_frame2.restype = ctypes.c_bool
        
        # get_frame_size_bytes2
        self.dll.get_frame_size_bytes2.argtypes = []
        self.dll.get_frame_size_bytes2.restype = ctypes.c_int
        
        # set_shader_params
        self.dll.set_shader_params.argtypes = [
            ctypes.c_int,  # max_samples
            ctypes.c_int,  # coord_mode
            ctypes.c_int,  # prec_coord
            ctypes.c_int,  # prec_weights
            ctypes.c_int,  # prec_color
            ctypes.c_int,  # prec_accum
        ]
        self.dll.set_shader_params.restype = None

        # set_separable_mode (optional — old DLL may not export it)
        self._has_separable = False
        try:
            self.dll.set_separable_mode.argtypes = [ctypes.c_int]
            self.dll.set_separable_mode.restype = None
            self.dll.get_separable_mode.argtypes = []
            self.dll.get_separable_mode.restype = ctypes.c_int
            self._has_separable = True
        except (AttributeError, OSError):
            pass  # old DLL without separable API
    
    def init_capture(self, monitor_index: int, width: int, height: int) -> bool:
        """Initialize screen capture"""
        if not self.dll:
            return False
        return self.dll.init_capture(monitor_index, width, height)
    
    def capture_frame(self) -> bool:
        """Capture frame"""
        if not self.dll:
            return False
        return self.dll.capture_frame()
    
    def set_capture_fps(self, fps: int):
        """Set max capture FPS (0 = no limit, adaptive)"""
        if not self.dll:
            return
        try:
            self.dll.set_capture_fps(int(fps) if fps and int(fps) > 0 else 0)
        except Exception as e:
            print(f"[WARN] set_capture_fps error: {e}")
    
    def copy_frame(self, buffer_ptr, buffer_size: int) -> bool:
        """Copy frame to buffer"""
        if not self.dll:
            return False
        return self.dll.copy_frame(buffer_ptr, buffer_size)
    
    def get_frame_size_bytes(self) -> int:
        """Get frame size in bytes"""
        if not self.dll:
            return 0
        return self.dll.get_frame_size_bytes()
    
    def get_frame_id(self) -> int:
        """Get current frame ID"""
        if not self.dll:
            return 0
        return self.dll.get_frame_id()
    
    def is_hdr(self) -> bool:
        """Check if content is HDR"""
        if not self.dll:
            return False
        return self.dll.is_hdr()
    
    def shutdown_capture(self):
        """Stop capture"""
        if self.dll:
            try:
                self.dll.shutdown_capture()
            except Exception as e:
                print(f"[WARN] Shutdown capture error: {e}")
    
    def set_second_resolution(self, width: int, height: int):
        """Set resolution for second stream"""
        if self.dll:
            try:
                self.dll.set_second_resolution(width, height)
            except Exception as e:
                print(f"[WARN] Set second resolution error: {e}")
    
    def copy_frame2(self, buffer_ptr, buffer_size: int) -> bool:
        """Copy frame for second stream"""
        if not self.dll:
            return False
        return self.dll.copy_frame2(buffer_ptr, buffer_size)
    
    def set_shader_params(self, pixel_limit: int, coord_mode: str,
                          prec_coord: str, prec_weights: str,
                          prec_color: str, prec_accum: str):
        """
        Set shader runtime parameters.
        
        Args:
            pixel_limit: 0 = unlimited, >0 = max total source pixels to sample
            coord_mode: "frame" (recalc each frame) or "once" (cache)
            prec_coord: "fp32" or "fp16"
            prec_weights: "fp32" or "fp16"
            prec_color: "fp32" or "fp16"
            prec_accum: "fp32" or "fp16"
        """
        if not self.dll:
            return
        try:
            limit = max(0, int(pixel_limit))
            coord = 1 if coord_mode == "once" else 0
            pc = 1 if prec_coord == "fp16" else 0
            pw = 1 if prec_weights == "fp16" else 0
            pcl = 1 if prec_color == "fp16" else 0
            pa = 1 if prec_accum == "fp16" else 0
            self.dll.set_shader_params(limit, coord, pc, pw, pcl, pa)
        except Exception as e:
            print(f"[WARN] set_shader_params error: {e}")

    def set_separable_mode(self, enable: bool):
        """
        Enable/disable separable 2-pass pipeline in real-time.
        When enabled: box filter splits into 2 1D passes (no warp divergence).
        Result is identical; quality is preserved.
        No-op if DLL doesn't support it (old build).
        """
        if not self.dll or not getattr(self, '_has_separable', False):
            return
        try:
            self.dll.set_separable_mode(1 if enable else 0)
            print(f"[OK] Separable mode: {'ON' if enable else 'OFF'}")
        except Exception as e:
            print(f"[WARN] set_separable_mode error: {e}")

    def get_separable_mode(self) -> bool:
        """Get current separable mode state"""
        if not self.dll or not getattr(self, '_has_separable', False):
            return True
        try:
            return self.dll.get_separable_mode() != 0
        except Exception as e:
            return True
