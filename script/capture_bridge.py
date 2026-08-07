"""
Module for working with capture_bridge.dll
"""

import ctypes
import sys
from ctypes import wintypes

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