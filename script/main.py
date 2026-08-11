"""
Main application file for GPU Capture + WLED
Combines all modules and launches the application"
"""

import sys
import os
import atexit

# Import path utilities first
from path_utils import resolve_resource_path, get_dll_path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mapping auto-save file - handled in maping.py module

import tkinter as tk
from tkinter import ttk, filedialog, simpledialog, font as tkfont
from PIL import Image, ImageTk
import json
from ctypes import wintypes, windll, byref, c_int, sizeof
import threading
import time
import ctypes
import socket
import struct
import urllib.request
import numpy as np
from queue import Queue, Empty, Full

# System tray support for Windows
try:
    import pystray
    HAS_SYSTRAY = True
except ImportError:
    HAS_SYSTRAY = False

# === HOST ONLINE CHECKER ===
def is_host_online(host: str, port: int = 80, timeout: float = 0.3) -> bool:
    """
    Fast check if host is online using TCP socket connection.
    Returns True if connected within timeout, False otherwise.
    
    Args:
        host: IP address or hostname
        port: Port to check (default 80 for HTTP)
        timeout: Connection timeout in seconds (default 0.3)
    
    Returns:
        bool: True if host is reachable, False otherwise
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except Exception:
        return False


# === DPI Scaling Support for Windows ===
def enable_dpi_scaling():
    """Enable high resolution support (DPI scaling) for Windows"""
    try:
        # Enable DPI awareness for Windows 10/11
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            # Alternative method via WinAPI
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass

enable_dpi_scaling()


# === Windows Window Styling Functions ===
def set_window_accent_policy(hwnd: int, accent_state: int) -> bool:
    """
    Apply accent policy to a window (blur behind, glass effect, etc.)
    
    Args:
        hwnd: Window handle
        accent_state: Accent state (0=normal, 1=gradient, 2=transparent, 3=blurbehind)
    
    Returns:
        bool: True on success, False otherwise
    """
    try:
        # ACCENT_POLICY structure
        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", c_int),
                ("AccentFlags", c_int),
                ("GradientColor", wintypes.COLORREF),
                ("AnimationId", c_int)
            ]
        
        accent = ACCENT_POLICY()
        accent.AccentState = accent_state
        accent.AccentFlags = 0
        accent.GradientColor = 0
        accent.AnimationId = 0
        
        # WINDOWCOMPOSITIONATTRIBDATA structure
        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("AttribType", c_int),
                ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t)
            ]
        
        # Define accent states
        ACCENT_ENABLED = 1
        ACCENT_GRADIENT = 2
        ACCENT_TRANSPARENT = 3
        ACCENT_ENABLE_BLURBEHIND = 4
        
        # Define composition attrib types
        WCA_ACCENT_POLICY = 19
        
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.AttribType = WCA_ACCENT_POLICY
        data.Data = ctypes.byref(accent)
        data.SizeOfData = sizeof(ACCENT_POLICY)
        
        windll.user32.SetWindowCompositionAttribute(hwnd, byref(data))
        return True
    except Exception:
        return False


def set_window_dark_mode(hwnd):
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19

    value = ctypes.c_int(1)

    # Windows 11 / Win10 2004+
    res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        DWMWA_USE_IMMERSIVE_DARK_MODE,
        ctypes.byref(value),
        ctypes.sizeof(value)
    )

    # Старые Windows 10
    if res != 0:
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1,
            ctypes.byref(value),
            ctypes.sizeof(value)
        )

    # Обновить рамку окна
    ctypes.windll.user32.SetWindowPos(
        wintypes.HWND(hwnd),
        0,
        0, 0, 0, 0,
        0x0027  # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
    )

    return res == 0


def show_splash_screen(image_path: str = "main.png", duration_ms: int = 5000):
    """
    Show splash screen with image before opening main window.
    Returns control only after splash screen is closed.
    
    Args:
        image_path: Path to splash screen image
        duration_ms: Display duration in milliseconds
    
    Returns:
        bool: True on success, False on error
    """
    splash = None
    try:
        # Resolve path for PyInstaller compatibility
        resolved_path = resolve_resource_path(image_path)
        
        # Create splash screen window without title bar (borderless)
        splash = tk.Tk()
        splash.overrideredirect(True)  # Remove frame and title bar
        
        # Get screen size
        screen_width = splash.winfo_screenwidth()
        screen_height = splash.winfo_screenheight()
        
        # Load image
        if os.path.exists(resolved_path):
            try:
                img = Image.open(resolved_path)
                
                # If image is larger than screen - reduce it
                max_width, max_height = screen_width - 40, screen_height - 40
                img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
                
                splash_width, splash_height = img.size
                
                # Center window
                x = (screen_width - splash_width) // 2
                y = (screen_height - splash_height) // 2
                
                splash.geometry(f"{splash_width}x{splash_height}+{x}+{y}")
                
                # Create PhotoImage from PIL Image
                splash_image = ImageTk.PhotoImage(img)
                
                # Display image
                label = tk.Label(splash, image=splash_image, bg="black")
                label.image = splash_image  # Save reference
                label.pack()
                
            except Exception as e:
                print(f"[WARN] Failed to load splash image: {e}")
                # Fallback - simple text window
                splash.geometry("300x150")
                x = (screen_width - 300) // 2
                y = (screen_height - 150) // 2
                splash.geometry(f"300x150+{x}+{y}")
                
                label = tk.Label(
                    splash,
                    text="Spectr aLED",
                    font=("Segoe UI", 16, "bold"),
                    bg="#1a1b26",
                    fg="#c0caf5"
                )
                label.pack(padx=20, pady=40)
        else:
            print(f"[WARN] Splash image not found: {resolved_path}")
            splash.geometry("300x150")
            
            x = (screen_width - 300) // 2
            y = (screen_height - 150) // 2
            splash.geometry(f"300x150+{x}+{y}")
            
            label = tk.Label(
                splash,
                text="Spectr aLED",
                font=("Segoe UI", 16, "bold"),
                bg="#1a1b26",
                fg="#c0caf5"
            )
            label.pack(padx=20, pady=40)
        
        # Apply glass effect to splash screen
        try:
            hwnd = int(splash.winfo_id())
            set_window_accent_policy(hwnd, 3)  # ACCENT_ENABLE_BLURBEHIND
            set_window_dark_mode(hwnd)
        except Exception:
            pass
        
        # Remove transparency (for Windows)
        try:
            hwnd = int(splash.winfo_id())
            windll.user32.SetWindowLongPtrW(
                hwnd,
                -20,  # GWL_EXSTYLE
                windll.user32.GetWindowLongPtrW(hwnd, -20) & ~0x00000020  # WS_EX_TRANSPARENT
            )
        except Exception:
            pass
        
        # Ensure window is drawn before starting loop
        splash.update_idletasks()
        splash.update()  # Force update to ensure drawing
        
        # Show splash screen and start event loop with delay
        splash.lift()  # Lift above other windows
        splash.attributes("-topmost", True)  # Make on top of all windows
        
        print(f"[INFO] Splash screen shown for {duration_ms}ms")
        
        # Schedule close after duration (using after method)
        def close_splash():
            if splash and splash.winfo_exists():
                try:
                    splash.destroy()
                except:
                    pass
                splash.quit()  # Quit the mainloop
        
        splash.after(duration_ms, close_splash)
        
        # Start main loop during splash screen (blocking call)
        splash.mainloop()  # Use mainloop instead of update for waiting
        
    except Exception as e:
        print(f"[ERROR] Splash screen error: {e}")
        if splash:
            try:
                splash.destroy()
            except:
                pass
        return False
    
    return True

# =============================================================================
# CONFIGURATION CONSTANTS (embedded - no external config file)
# =============================================================================

# === CAPTURE CONFIG ===
TARGET_W = 120
TARGET_H = 68
DEFAULT_LED_COUNT = 2048

# === DLL PATH - loaded from path_utils ===

# === DDP Socket Config ===
DDP_PORT = 4048
DDP_MAX_CHUNK_SIZE = 1440

# === QUEUE SIZES ===
CAPTURE_QUEUE_SIZE = 2
PREVIEW_QUEUE_SIZE = 1
DDP_QUEUE_SIZE = 1

# === AMBI LIGHT MODES ===
AMBI_MODES = ["Matrix", "Ambilight 3%", "Ambilight 6%", "Ambilight 9%"]

# === ASPECT RATIO MODES ===
ASPECT_RATIOS = ["full", "16:9", "21:9", "4:3", "2.39:1", "2:1"]

# === LUT SIZES ===
LUT_SIZES = [32, 64, 96, 128, 160, 192, 224, 256]

# === HDR TONEMAP MODES ===
HDR_TONEMODES = ["gamma", "pq"]

# === CALIBRATION DEFAULTS ===
DEFAULT_CALIBRATION = {
    "white": [1.0, 1.0, 1.0],
    "red": [1.0, 1.0, 1.0],
    "green": [1.0, 1.0, 1.0],
    "blue": [1.0, 1.0, 1.0],
    "yellow": [1.0, 1.0, 1.0],
    "cyan": [1.0, 1.0, 1.0],
    "magenta": [1.0, 1.0, 1.0],
}

# === PQ CURVE CONFIG ===
PQ_POINTS = 64
PQ_NITS = [
    0, 0.1, 0.2, 0.4, 0.6, 0.8, 1, 1.4, 1.8, 2.2, 2.6, 3, 3.5, 4, 4.5, 5, 
    6, 7, 8, 9, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 120, 140, 
    160, 180, 200, 220, 240, 260, 280, 300, 320, 360, 400, 440, 480, 520, 
    560, 600, 640, 680, 720, 760, 800, 900, 1000, 1200, 1500, 2000, 3000, 
    5000, 8000, 10000
]

# === BLACK DETECTION ===
BLACK_THRESHOLD = 0.001
BLACK_RESTART_DELAY = 0.5

# === FPS UPDATE INTERVAL (ms) ===
FPS_UPDATE_INTERVAL_MS = 200

# === CONFIG FILE PATH ===
CONFIG_FILE_PATH = "app_config.json"


def get_default_settings():
    """Returns a dictionary with all default settings"""
    return {
        # Capture settings
        "monitor_index": 0,
        "input_target_w": TARGET_W,
        "input_target_h": TARGET_H,
        "aspect1": "full",
        
        # Stream 2 capture settings
        "input_target2_w": 120,
        "input_target2_h": 68,
        "aspect2": "full",
        
        # Active stream selector
        "active_stream": 1,
        
        # Stream 1 SDR/HDR settings
        "stream1_vars": {
            "brightness_sdr": 127,
            "gamma_sdr": 0.8,
            "gamma_sdr_en": True,
            "sat_sdr_en": False,
            "sat_sdr": 1.0,
            "brightness_hdr": 255,
            "gamma_hdr": 1.8,
            "gamma_hdr_en": True,
            "sat_hdr_en": False,
            "sat_hdr": 1.0,
        },
        
        # Stream 2 SDR/HDR settings
        "stream2_vars": {
            "brightness_sdr": 127,
            "gamma_sdr": 1.0,
            "gamma_sdr_en": True,
            "sat_sdr_en": False,
            "sat_sdr": 0.8,
            "brightness_hdr": 255,
            "gamma_hdr": 1.8,
            "gamma_hdr_en": True,
            "sat_hdr_en": False,
            "sat_hdr": 1.0,
        },
        
        # General settings
        "sdr_brightness": 255,
        "sdr_gamma": 1.0,
        "hdr_brightness": 255,
        "hdr_gamma": 1.8,
        "sdr_gamma_enabled": False,
        "hdr_gamma_enabled": True,
        "sdr_saturation_enabled": False,
        "hdr_saturation_enabled": False,
        "sdr_saturation": 1.0,
        "hdr_saturation": 1.0,
        
        # HDR settings
        "tonemap_enabled": True,
        "hdr_tonemap_mode": "pq",
        "clip_nits": 1000,
        
        # Calibration
        "lut_size1": 64,
        "lut_size2": 64,
        "calibration1_enabled": False,
        "calibration2_enabled": False,
        
        # Stream enable states (saved separately from UI variables)
        "first_stream_enabled": True,
        "second_stream_enabled": False,
        
        # Calibrations
        "global_calibration": DEFAULT_CALIBRATION.copy(),
        "global_calibration2": DEFAULT_CALIBRATION.copy(),
        
        # Ambi modes
        "ambi_mode1": "Matrix",
        "ambi_mode2": "Matrix",
        
        # PQ Curve settings
        "pq_curve_strength": 3.0,
        "pq_curve_bias": 0.025,
        "pq_rgb_mode1": "rgb",
        "pq_rgb_mode2": "rgb",
        
        # Compute target removed - no longer using torch
        
        # External LUT enabled flag
        "external_lut_enabled": False,
        
        # PQ curve values (RGB) - Stream 1 (base curve for compatibility)
        "pq_values_r": [0.0] * PQ_POINTS,
        "pq_values_g": [0.0] * PQ_POINTS,
        "pq_values_b": [0.0] * PQ_POINTS,
    }


def save_settings_to_file(settings: dict, filepath: str = None):
    """Save settings to JSON file"""
    if filepath is None:
        filepath = CONFIG_FILE_PATH
    
    try:
        # Convert tkinter variable values to native types
        def convert_value(val):
            if hasattr(val, 'get'):
                return val.get()
            return val
        
        def convert_dict(d):
            result = {}
            for k, v in d.items():
                if isinstance(v, dict):
                    result[k] = convert_dict(v)
                elif isinstance(v, list):
                    # Don't process dicts inside lists - they are already converted values
                    result[k] = [convert_value(x) if hasattr(x, 'get') and not isinstance(x, dict) else x for x in v]
                else:
                    result[k] = convert_value(v) if hasattr(v, 'get') and not isinstance(v, dict) else v
            return result
        
        converted_settings = convert_dict(settings)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(converted_settings, f, indent=2, ensure_ascii=False)
        
        print(f"[OK] Settings saved to: {filepath}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save settings: {e}")
        return False


def load_settings_from_file(filepath: str = None):
    """Load settings from JSON file. Returns dict or None on error"""
    if filepath is None:
        filepath = CONFIG_FILE_PATH
    
    try:
        if not os.path.exists(filepath):
            print(f"[INFO] Config file not found: {filepath} - using defaults")
            return get_default_settings()
        
        with open(filepath, 'r', encoding='utf-8') as f:
            settings = json.load(f)
        
        # Fill missing values with defaults
        default = get_default_settings()
        for key in default:
            if key not in settings:
                settings[key] = default[key]
        
        print(f"[OK] Settings loaded from: {filepath}")
        return settings
    except Exception as e:
        print(f"[ERROR] Failed to load settings: {e}")
        return get_default_settings()


def load_settings_json(filepath: str):
    """
    Load JSON file and return dict.
    Function for use in GUI when selecting a file.
    """
    if not os.path.exists(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return None
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            settings = json.load(f)
        
        # Fill missing values with defaults
        default = get_default_settings()
        for key in default:
            if key not in settings:
                settings[key] = default[key]
        
        print(f"[OK] Settings loaded from: {filepath}")
        return settings
    except Exception as e:
        print(f"[ERROR] Failed to load settings: {e}")
        return None


def save_settings_json(settings: dict, filepath: str):
    """
    Save settings to JSON file.
    Function for use in GUI when selecting a file.
    """
    try:
        # Convert tkinter variable values to native types
        def convert_value(val):
            if hasattr(val, 'get'):
                return val.get()
            return val
        
        def convert_dict(d):
            result = {}
            for k, v in d.items():
                if isinstance(v, dict):
                    result[k] = convert_dict(v)
                elif isinstance(v, list):
                    # Don't process dicts inside lists - they are already converted values
                    result[k] = [convert_value(x) if hasattr(x, 'get') and not isinstance(x, dict) else x for x in v]
                else:
                    result[k] = convert_value(v) if hasattr(v, 'get') and not isinstance(v, dict) else v
            return result
        
        converted_settings = convert_dict(settings)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(converted_settings, f, indent=2, ensure_ascii=False)
        
        print(f"[OK] Settings saved to: {filepath}")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to save settings: {e}")
        return None


# Import modules with cache optimization

# Import settings save/load functions (now embedded in main.py)
from capture_bridge import CaptureBridge
from wled_controller import WLEDController, wled_controller

# Import cache optimization utilities
try:
    from cache_optimizer import (
        CacheOptimizedBuffer,
        CacheOptimizedFrameBuffer,
        GPUKernelCacheOptimizer,
        align_to_cache_line,
        CACHE_LINE_SIZE as OPT_CACHE_LINE_SIZE,
        get_optimal_lut_size
    )
    HAS_OPTIMIZER = True
except ImportError:
    # Fallback if cache optimizer not available
    print("[WARN] cache_optimizer.py not found - using standard implementation")
    CacheOptimizedBuffer = None
    CacheOptimizedFrameBuffer = None
    GPUKernelCacheOptimizer = None
    align_to_cache_line = lambda x: x
    OPT_CACHE_LINE_SIZE = 64
    HAS_OPTIMIZER = False

from image_processor import (
    ImageProcessor, 
    generate_3d_lut, 
    generate_3d_lut_async,
    apply_lut_generic,
    apply_ambilight, 
    apply_saturation, 
    generate_pq_exponential,
    apply_shadow_bias_to_curve, 
    apply_pq_curve,
    apply_custom_gamma
)


# === File Dialog Helper Functions (always on top) ===
def open_file_dialog(parent, dialog_type="open", title="Select file", **kwargs):
    """
    Open a file dialog that stays on top of all windows.
    
    Args:
        parent: Parent window or root
        dialog_type: "open" for askopenfilename, "save" for asksaveasfilename
        title: Dialog title
        **kwargs: Additional arguments to pass to the filedialog function
    
    Returns:
        str or None: Selected file path or None if cancelled
    """
    # Create a temporary topmost window to host the dialog
    temp_window = tk.Toplevel(parent)
    temp_window.title(title)
    temp_window.attributes("-topmost", True)
    temp_window.withdraw()  # Hide the window but keep it active
    
    # Bring to front before opening dialog
    temp_window.lift()
    temp_window.focus_force()
    
    try:
        if dialog_type == "open":
            result = filedialog.askopenfilename(parent=temp_window, **kwargs)
        else:  # save
            result = filedialog.asksaveasfilename(parent=temp_window, **kwargs)
        
        return result
    finally:
        temp_window.destroy()


def open_directory_dialog(parent, title="Select folder", **kwargs):
    """
    Open a directory dialog that stays on top of all windows.
    
    Args:
        parent: Parent window or root
        title: Dialog title
        **kwargs: Additional arguments to pass to the filedialog function
    
    Returns:
        str or None: Selected directory path or None if cancelled
    """
    # Create a temporary topmost window to host the dialog
    temp_window = tk.Toplevel(parent)
    temp_window.title(title)
    temp_window.attributes("-topmost", True)
    temp_window.withdraw()
    
    try:
        result = filedialog.askdirectory(parent=temp_window, **kwargs)
        return result
    finally:
        temp_window.destroy()

# Import mapping module
from maping import open_mapping_window

# Import calibration window modules
from calibration_window_stream1 import open_calibration_window as open_calibration_stream1
from calibration_window_stream2 import open_calibration_window2 as open_calibration_stream2

# Import custom gamma window modules
from custom_gamma_s1 import open_custom_gamma_menu_s1
from custom_gamma_s2 import open_custom_gamma_menu_s2

# Import preview loop modules
from preview_s1 import run_preview_loop as preview_s1_loop
from preview_s2 import run_preview2_loop as preview_s2_loop

# Import PQ curve editor modules
from pq_curve_editor_s1 import open_pq_curve_s1
from pq_curve_editor_s2 import open_pq_curve_s2


# Global variables for WLED devices and mapping
WLED_DEVICES = []
MASTER_MAPPING = []
MASTER_MAPPING_DIRTY = True

# DDP Socket (global for all streams)
ddp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ddp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)


def load_mapping_file():
    """Load mapping file from dialog window with topmost priority"""
    path = open_file_dialog(None, "open", title="Select Mapping File", filetypes=[("Text files", "*.txt")])
    if not path:
        return None
    
    mapping = []
    
    with open(path) as f:
        for line in f:
            if ":" not in line:
                continue
            _, coords = line.split(":")
            r, c = coords.strip().split(",")
            mapping.append((int(r), int(c)))
    
    return mapping


def set_wled_ddp_mode(ip: str, keep_last_frame: bool = True) -> bool:
    """Switch WLED to DDP mode with fast online check"""
    # Fast check if host is online using TCP socket (0.3s timeout)
    if not is_host_online(ip, port=80, timeout=0.3):
        print(f"[ERROR] WLED connection failed: {ip} (device offline)")
        return False
    
    payload = {
        "on": True,
        "bri": 255,
        "transition": 0,
        "live": True,
        "nl": {"on": False},
        "lor": 0
    }
    
    if not keep_last_frame:
        payload["seg"] = [{
            "id": 0,
            "fx": 0,
            "col": [[10, 10, 10]]
        }]
    
    req = urllib.request.Request(
        f"http://{ip}/json/state",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        urllib.request.urlopen(req, timeout=1)
        print(f"[OK] WLED {ip} switched to DDP mode")
        return True
    except Exception as e:
        print(f"[ERROR] WLED connection failed: {ip}")
        print(e)
        return False


def restore_wled(ip: str):
    """Restore normal WLED operation mode with fast online check"""
    # Fast check if host is online using TCP socket (0.3s timeout)
    if not is_host_online(ip, port=80, timeout=0.3):
        return
    
    payload = {
        "live": False,
        "on": True,
        "bri": 255,
        "transition": 0,
        "seg": [{
            "id": 0,
            "fx": 0,
            "col": [[10, 10, 10]]
        }]
    }
    
    req = urllib.request.Request(
        f"http://{ip}/json/state",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=1)
    except Exception as e:
        print(f"[WARN] Failed to restore WLED {ip}: {e}")


def send_ddp(ip: str, data: bytes):
    """Send DDP packet to device"""
    global ddp_socket
    
    total_len = len(data)
    packets = (total_len + DDP_MAX_CHUNK_SIZE - 1) // DDP_MAX_CHUNK_SIZE
    
    seq = 0
    seq = (seq + 1) % 255
    
    for i in range(packets):
        chunk = data[i * DDP_MAX_CHUNK_SIZE:(i + 1) * DDP_MAX_CHUNK_SIZE]
        
        header = struct.pack(
            "!BBBBLH",
            0x40 | (0x01 if i == packets - 1 else 0),
            seq,
            0x0B,
            1,
            i * DDP_MAX_CHUNK_SIZE,
            len(chunk)
        )
        
        ddp_socket.sendto(header + chunk, (ip, DDP_PORT))


def get_monitors_info():
    """Get information about connected monitors"""
    user32 = ctypes.windll.user32
    
    monitors = []
    
    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32)
        ]
    
    def monitor_enum_proc(hMonitor, hdcMonitor, lprcMonitor, dwData):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(info)
        
        user32.GetMonitorInfoW(hMonitor, ctypes.byref(info))
        
        width = info.rcMonitor.right - info.rcMonitor.left
        height = info.rcMonitor.bottom - info.rcMonitor.top
        
        name = info.szDevice
        
        monitors.append({
            "name": name,
            "width": width,
            "height": height
        })
        
        return True
    
    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(wintypes.RECT),
        ctypes.c_double
    )
    
    user32.EnumDisplayMonitors(
        0, 0, MonitorEnumProc(monitor_enum_proc), 0
    )
    
    return monitors


# === System Tray Icon Manager for Windows ===
class TrayManager:
    """System tray manager for Windows"""
    
    def __init__(self, root, app):
        self.root = root
        self.app = app
        self.tray_icon = None
        self.is_running = False
        
    def create_tray_icon(self):
        """Create tray icon"""
        if not HAS_SYSTRAY:
            print("[WARN] pystray not available - tray icon disabled")
            return False
            
        try:
            # Try to use main.png as tray icon (using path_utils)
            icon_path = resolve_resource_path("main.png")
            if not os.path.exists(icon_path):
                icon_path = resolve_resource_path("SpectrLed.png")
            
            # Create icon with transparency
            image = Image.open(icon_path).convert("RGBA")
            
            # Create tray menu
            from pystray import MenuItem as item
            
            menu = (
                item('Show', self.restore_window),
                item('Exit', self.exit_app)
            )
            
            # Create tray icon
            self.tray_icon = pystray.Icon(
                "Spectr aLED",
                image,
                "Spectr aLED",
                menu
            )
            
            # Setup left click handler - restore window
            def on_click(icon, item):
                self.restore_window()
            
            self.tray_icon.icon = image
            self.tray_icon.title = "Spectr aLED"
            
            print("[OK] Tray icon created")
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to create tray icon: {e}")
            return False
    
    def start_tray(self):
        """Start tray icon in separate thread"""
        if not self.tray_icon:
            return
        
        def run_tray():
            try:
                self.is_running = True
                self.tray_icon.run()
            except Exception as e:
                print(f"[ERROR] Tray icon error: {e}")
            finally:
                self.is_running = False
        
        # Start in separate thread (daemon)
        tray_thread = threading.Thread(target=run_tray, daemon=True)
        tray_thread.start()
        
    def restore_window(self):
        """Restore window from tray"""
        try:
            # Show window
            self.root.deiconify()
            
            # If window was maximized before minimize - restore normal mode
            if hasattr(self.app, '_maximized_before_minimize') and self.app._maximized_before_minimize:
                self.root.state('zoomed')
            else:
                # Restore normal state
                pass
            
            print("[OK] Window restored from tray")
        except Exception as e:
            print(f"[ERROR] Failed to restore window: {e}")
    
    def hide_window(self):
        """Hide window (minimize to tray)"""
        try:
            # Save current window state
            if self.root.state() == 'zoomed':
                self.app._maximized_before_minimize = True
            else:
                self.app._maximized_before_minimize = False
            
            # Hide window
            self.root.withdraw()
            
            print("[OK] Window hidden to tray")
        except Exception as e:
            print(f"[ERROR] Failed to hide window: {e}")
    
    def exit_app(self):
        """Full application shutdown"""
        try:
            if self.tray_icon:
                try:
                    self.tray_icon.stop()
                except:
                    pass
            self.app.running = False
            print("[INFO] Application exiting...")
            # Close main window
            self.root.quit()
        except Exception as e:
            print(f"[ERROR] Exit error: {e}")
    
    def show_tray_notification(self, title, message):
        """Show notification in tray"""
        try:
            if self.tray_icon:
                self.tray_icon.notify(message, title)
        except Exception as e:
            pass


class GPUCaptureApp:
    """Main application class - combines all functions"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Spectr aLED")
        # Base window size (after calculation - to fit all elements)
        self.root.geometry("1280x680")
        
        # Open window full screen on startup
        try:
            self.root.state('zoomed')
        except:
            pass
        
        # Color scheme (dark theme)
        self.colors = {
            "bg": "#1a1b26",
            "panel_bg": "#24283b",
            "accent": "#7aa2f7",
            "accent_hover": "#5d7fd0",
            "text_main": "#c0caf5",
            "text_dim": "#777c9e",
            "success": "#9ece6a",
            "warning": "#e0af68",
            "error": "#f7768e",
            "border": "#414868"
        }
        
        # === INIT MODULES ===
        try:
            self.bridge = CaptureBridge()
        except Exception as e:
            print(f"[ERROR] Failed to initialize bridge: {e}")
            self.bridge = None
        
        self.wled_ctrl = WLEDController()
        self.processor = ImageProcessor()
        
        # === STATE VARIABLES ===
        self.frame_buffer = np.empty((TARGET_H, TARGET_W, 3), dtype=np.float32)
        
        # === WINDOW TRACKING (prevent multiple windows) ===
        self.calibration_window1 = None
        self.calibration_window2 = None
        self.pq_window = None
        self.custom_gamma_window_s1 = None
        self.custom_gamma_window_s2 = None
        self.mapping_window = None
        
        # === BACKGROUND IMAGE REFERENCES (to prevent GC) ===
        self.main_window_bg = None
        self.pq_window_bg = None
        self.calibration_window1_bg = None
        self.calibration_window2_bg = None
        self.custom_gamma_s1_bg = None
        self.custom_gamma_s2_bg = None
        
        # Device info (no longer using torch)
        self.device = None
        
        # === SYSTEM TRAY SUPPORT ===
        self.tray_manager = None
        try:
            from pystray import Icon
            self.has_tray_support = True
        except ImportError:
            self.has_tray_support = False
        
        # === GUI VARIABLES (Stream 1) ===
        self.sdr_brightness = tk.IntVar(value=255)
        self.sdr_gamma = tk.DoubleVar(value=1.0)
        self.hdr_brightness = tk.IntVar(value=255)
        self.hdr_gamma = tk.DoubleVar(value=1.8)
        
        self.monitor_index = tk.IntVar(value=0)
        self.wled_ip_var = tk.StringVar()
        self.wled_discovered = []
        
        # WLED ping status tracking
        self._wled_ping_thread_running = False
        self._wled_ping_stop_event = threading.Event()
        self.running = True
        self.preview_enabled = False
        self.tonemap_enabled = tk.BooleanVar(value=True)
        self.tonemap_strength = tk.DoubleVar(value=1.0)
        
        self.avg_nits = 0.0
        self.peak_nits = 0.0
        
        self.calibration_enabled = tk.BooleanVar(value=True)
        self.global_calibration = DEFAULT_CALIBRATION.copy()
        self.global_lut = generate_3d_lut(self.global_calibration, size=64)
        
        # === DELAY TRACKING ===
        self.last_frame_time = time.perf_counter()
        
        # === MAPPING ===
        self.global_indices = None
        self.exp_pixel_indices = None
        
        # === STREAM 1 STATE ===
        self.stream1_enabled = True
        
        # === STREAM 2 STATE ===
        self.stream2_enabled = False
        self.sdr_saturation_enabled = tk.BooleanVar(value=False)
        self.sdr_saturation = tk.DoubleVar(value=1.0)
        self.hdr_saturation_enabled = tk.BooleanVar(value=False)
        self.hdr_saturation = tk.DoubleVar(value=1.0)
        
        self.hdr_active = False
        self.dll_lock = threading.Lock()
        
        # Initial SDR panel highlighting (active by default)
        self.sdr_panel_highlight_color = "#9ece6a"
        self.hdr_panel_highlight_color = "#c0caf5"
        self.brightness_table = {"levels": [], "nits": []}
        
        self.target_w = tk.IntVar(value=TARGET_W)
        self.target_h = tk.IntVar(value=TARGET_H)
        self.streaming_enabled = False
        
        # === STREAM 2 VARIABLES ===
        self.clip_nits = tk.IntVar(value=1000)
        self.sdr_gamma_enabled = tk.BooleanVar(value=False)
        self.hdr_gamma_enabled = tk.BooleanVar(value=True)
        
        self.last_frame_id = 0
        self.capture_count = 0
        
        # Stream 2 specific
        self.exp_pixel_indices2 = None
        self.exp_led_indices2 = None
        self.scale_count = 0
        self.preview_count = 0
        self.first_stream_enabled = tk.BooleanVar(value=True)
        self.first_stream_enabled.trace_add("write", self.on_first_stream_toggle)
        
        # FPS counters
        self.capture_fps_real = 0
        self.preview_fps_real = 0
        self.last_fps_time = time.perf_counter()
        self.ddp_frame_count = 0
        self.ddp_fps_real = 0
        
        # Restart handling
        self.restart_requested = False
        self.restart_lock = threading.Lock()
        self.capture_paused = False
        
        # Mapping stats
        self.mapping_fps = {}
        self._sync_lock = False
        self.mapping_counts = {}
        self.dll_restarting = False
        self.last_ddp_frame = None
        self.capture_delay_ms = 0.0
        self.scale_delay_ms = 0.0
        
        # Stream 2 specific - active stream selector
        self.active_stream = tk.IntVar(value=1)
        
        # Delays
        self.ddp_delay_ms = 0.0
        self.preview_delay_ms = 0.0
        self.calibration1_enabled = tk.BooleanVar(value=False)
        self.calibration2_enabled = tk.BooleanVar(value=False)
        
        # Queues for stream 2
        self.capture2_queue = Queue(maxsize=CAPTURE_QUEUE_SIZE)
        self.ddp2_queue = Queue(maxsize=DDP_QUEUE_SIZE)
        self.pipeline_delay_ms = 0.0
        
        # Stream 2 timing
        self.last_frame2_time = time.perf_counter()
        self.last_frame2_id = -1
        
        # Monitors info
        self.monitors = get_monitors_info()
        
        # Stream 2 resolution
        self.target2_w = tk.IntVar(value=120)
        self.global_calibration2 = DEFAULT_CALIBRATION.copy()
        self.global_lut2 = generate_3d_lut(self.global_calibration2, size=64)
        self.target2_h = tk.IntVar(value=68)
        
        self.second_stream_enabled = tk.BooleanVar(value=False)
        self.second_stream_enabled.trace_add("write", self.on_second_stream_toggle)
        
        # Stream 2 counters
        self.second_capture_count = 0
        self.second_fps_real = 0
        self.preview2_count = 0
        
        # Preview state for stream 2
        self.preview2_enabled = False
        
        # Stream 2 settings
        self.sdr2_brightness = tk.IntVar(value=255)
        self.sdr2_gamma = tk.DoubleVar(value=1.0)
        self.sdr2_gamma_enabled = tk.BooleanVar(value=False)
        self.sdr2_gamma_mode = tk.StringVar(value="stream")  # "stream" or "custom"
        self.sdr2_saturation_enabled = tk.BooleanVar(value=False)
        self.sdr2_saturation = tk.DoubleVar(value=1.0)
        
        self.hdr2_brightness = tk.IntVar(value=255)
        self.hdr2_gamma = tk.DoubleVar(value=1.8)
        self.hdr2_gamma_enabled = tk.BooleanVar(value=True)
        self.hdr2_saturation_enabled = tk.BooleanVar(value=False)
        self.hdr2_saturation = tk.DoubleVar(value=1.0)
        
        # Stream 2 delay
        self.pipeline2_delay_ms = 0.0
        
        # Aspect ratios
        self.aspect1 = tk.StringVar(value="full")
        self.aspect2 = tk.StringVar(value="full")
        
        # Black detection
        self.black_start_time = None
        self.black_threshold = BLACK_THRESHOLD
        self.black_restart_delay = BLACK_RESTART_DELAY
        self.last_frame2_valid = None
        
        # Stream 2 DDP
        self.last_ddp2_frame = None
        self.ddp2_frame_count = 0
        self.ddp2_delay_ms = 0.0
        self.last_ddp2_frame_time = 0.0
        
        # Ambi modes
        self.ambi_mode1 = tk.StringVar(value="Matrix")
        self.ambi_mode2 = tk.StringVar(value="Matrix")
        
        # External LUTs
        self.external_lut_sdr_1 = None
        self.external_lut_hdr_1 = None
        self.external_lut_sdr_2 = None
        self.external_lut_hdr_2 = None
        
        # HDR tonemap mode
        self.hdr_tonemap_mode = tk.StringVar(value="pq")
        self.hdr_tonemap_mode.trace_add("write", self.update_hdr_mode_ui)
        
        self.external_lut_enabled = tk.BooleanVar(value=False)
        
        self.external_lut = None
        self.external_lut2 = None
        
        # === INPUT (GUI) VARIABLES ===
        self.input_target_w = tk.IntVar(value=TARGET_W)
        self.input_target_h = tk.IntVar(value=TARGET_H)
        
        self.input_target2_w = tk.IntVar(value=120)
        self.input_target2_h = tk.IntVar(value=68)
        
        # NITS tracking
        self.last_nits_ok_time = time.perf_counter()
        self.nits_zero_start = None
        # Limit label update frequency (not more than 4 times per second)
        self.last_nits_update_time = 0.0
        
        # LUT sizes
        self.lut_size1 = tk.IntVar(value=64)
        self.lut_size2 = tk.IntVar(value=64)
        
        # Compute target removed - no longer using torch
        
        # PQ RGB modes
        self.pq_rgb_mode1 = tk.StringVar(value="rgb")
        self.pq_rgb_mode2 = tk.StringVar(value="rgb")
        
        # Current PQ values
        self.global_lut = generate_3d_lut(
            self.global_calibration,
            size=self.lut_size1.get()
        )
        
        self.global_lut2 = generate_3d_lut(
            self.global_calibration2,
            size=self.lut_size2.get()
        )
        
        # === STREAM STATE ===
        self.stream1_vars = {
            "brightness_sdr": tk.IntVar(value=127),
            "gamma_sdr": tk.DoubleVar(value=0.8),
            "gamma_sdr_en": tk.BooleanVar(value=True),
            "sat_sdr_en": tk.BooleanVar(value=False),
            "sat_sdr": tk.DoubleVar(value=1.0),
            
            "brightness_hdr": tk.IntVar(value=255),
            "gamma_hdr": tk.DoubleVar(value=1.8),
            "gamma_hdr_en": tk.BooleanVar(value=True),
            "sat_hdr_en": tk.BooleanVar(value=False),
            "sat_hdr": tk.DoubleVar(value=1.0),
        }
        
        self.stream2_vars = {
            "brightness_sdr": tk.IntVar(value=127),
            "gamma_sdr": tk.DoubleVar(value=0.8),
            "gamma_sdr_en": tk.BooleanVar(value=True),
            "sat_sdr_en": tk.BooleanVar(value=False),
            "sat_sdr": tk.DoubleVar(value=1.0),
            
            "brightness_hdr": tk.IntVar(value=255),
            "gamma_hdr": tk.DoubleVar(value=1.8),
            "gamma_hdr_en": tk.BooleanVar(value=True),
            "sat_hdr_en": tk.BooleanVar(value=False),
            "sat_hdr": tk.DoubleVar(value=1.0),
        }
        
        # Preview FPS
        self.preview2_fps_real = 0
        self.preview2_queue = Queue(maxsize=PREVIEW_QUEUE_SIZE)
        
        # Monitor list
        self.monitor_list = [
            f"{i}: {m['name']} ({m['width']}x{m['height']})"
            for i, m in enumerate(self.monitors)
        ]
        
        # Initialize queues
        self.preview_frame = None
        self.preview_lock = threading.Lock()
        self.capture_queue = Queue(maxsize=CAPTURE_QUEUE_SIZE)
        self.ddp_queue = Queue(maxsize=DDP_QUEUE_SIZE)
        self.preview_queue = Queue(maxsize=PREVIEW_QUEUE_SIZE)
        
        # PQ Curve config
        self.pq_points = 64
        
        # zone distribution (higher density in shadows and midtones)
        self.pq_nits = np.array(PQ_NITS, dtype=np.float32)
        
        # Stream 1 PQ Curve settings
        self.pq_curve_strength1 = tk.DoubleVar(value=3.0)
        self.pq_curve_bias1 = tk.DoubleVar(value=0.025)
        
        # Stream 2 PQ Curve settings
        self.pq_curve_strength2 = tk.DoubleVar(value=3.0)
        self.pq_curve_bias2 = tk.DoubleVar(value=0.025)
        
        # Current PQ values (base curve for compatibility) - init with Stream 1 settings
        self.pq_values = generate_pq_exponential(
            strength=self.pq_curve_strength1.get(),
            points=self.pq_points
        )
        
        # Apply shadow bias to base curve
        self.pq_values = self.apply_shadow_bias_to_curve(self.pq_values, self.pq_curve_bias1.get())
        
        # PQ values - Stream 1 (initialize with base curve)
        self.pq_values_r1 = np.copy(self.pq_values)
        self.pq_values_g1 = np.copy(self.pq_values)
        self.pq_values_b1 = np.copy(self.pq_values)
        
        # PQ values - Stream 2 (initialize with same base curve but will be independent)
        self.pq_values_r2 = np.copy(self.pq_values)
        self.pq_values_g2 = np.copy(self.pq_values)
        self.pq_values_b2 = np.copy(self.pq_values)
        
        self.pq_sliders = []
        
        # === CUSTOM GAMMA VALUES (Stream 1 and Stream 2) - 64 points each ===
        # Generate values for custom gamma: first=0, last=255, middle interpolate
        def generate_gamma_values():
            """Generate gamma values with interpolation"""
            n = 64
            values = np.zeros(n, dtype=np.float32)
            # First value is 0, last is 255
            for i in range(n):
                if i == 0:
                    values[i] = 0.0
                elif i == n - 1:
                    values[i] = 255.0
                else:
                    # Linear interpolation between 0 and 255
                    values[i] = (i / (n - 1)) * 255.0
            return values
        
        self.custom_gamma_sdr_values = generate_gamma_values()
        self.custom_gamma_hdr_values = generate_gamma_values()
        
        # Stream 1: R, G, B separately for "separate" mode
        self.custom_gamma_sdr_r1 = np.copy(self.custom_gamma_sdr_values)
        self.custom_gamma_sdr_g1 = np.copy(self.custom_gamma_sdr_values)
        self.custom_gamma_sdr_b1 = np.copy(self.custom_gamma_sdr_values)
        
        self.custom_gamma_hdr_r1 = np.copy(self.custom_gamma_hdr_values)
        self.custom_gamma_hdr_g1 = np.copy(self.custom_gamma_hdr_values)
        self.custom_gamma_hdr_b1 = np.copy(self.custom_gamma_hdr_values)
        
        # Stream 2
        self.custom_gamma_sdr_r2 = np.copy(self.custom_gamma_sdr_values)
        self.custom_gamma_sdr_g2 = np.copy(self.custom_gamma_sdr_values)
        self.custom_gamma_sdr_b2 = np.copy(self.custom_gamma_sdr_values)
        
        self.custom_gamma_hdr_r2 = np.copy(self.custom_gamma_hdr_values)
        self.custom_gamma_hdr_g2 = np.copy(self.custom_gamma_hdr_values)
        self.custom_gamma_hdr_b2 = np.copy(self.custom_gamma_hdr_values)
        
        # Display mode variables for Stream 1 and Stream 2
        self.custom_gamma_rgb_mode1 = tk.StringVar(value="rgb")  # "rgb" or "separate"
        self.custom_gamma_rgb_mode2 = tk.StringVar(value="rgb")
        
        # References to background images to prevent garbage collection
        
        # === VARIABLES FOR SAVING CUSTOM GAMMA WHEN SWITCHING MODES ===
        # Save Stream 1 SDR gamma values (R, G, B)
        self.saved_custom_gamma_sdr_r1 = np.copy(self.custom_gamma_sdr_r1)
        self.saved_custom_gamma_sdr_g1 = np.copy(self.custom_gamma_sdr_g1)
        self.saved_custom_gamma_sdr_b1 = np.copy(self.custom_gamma_sdr_b1)
        
        # Save Stream 2 SDR gamma values (R, G, B)
        self.saved_custom_gamma_sdr_r2 = np.copy(self.custom_gamma_sdr_r2)
        self.saved_custom_gamma_sdr_g2 = np.copy(self.custom_gamma_sdr_g2)
        self.saved_custom_gamma_sdr_b2 = np.copy(self.custom_gamma_sdr_b2)
        
        # Save Stream 1 custom gamma parameters (curve, bias, and enabled state)
        self.saved_curve_strength1 = tk.DoubleVar(value=2.0)
        self.saved_bias1 = tk.DoubleVar(value=0.025)
        self.saved_custom_gamma_enabled1 = tk.BooleanVar(value=True)
        
        # Save Stream 2 custom gamma parameters (curve, bias, and enabled state)
        self.saved_curve_strength2 = tk.DoubleVar(value=2.0)
        self.saved_bias2 = tk.DoubleVar(value=0.025)
        self.saved_custom_gamma_enabled2 = tk.BooleanVar(value=True)
        
        # Apply curve and bias to custom gamma on first run (one-time initialization)
        from custom_gamma_s1 import generate_custom_gamma_curve, apply_shadow_bias_to_custom_gamma
        
        # Stream 1: Generate curve with default parameters and apply bias
        strength1 = self.saved_curve_strength1.get()
        bias1 = self.saved_bias1.get()
        base1 = generate_custom_gamma_curve(strength=strength1, points=64)
        biased1 = apply_shadow_bias_to_custom_gamma(base1, bias1)
        
        # Apply to Stream 1 (in-place assignment) - only if not already set
        if len(self.custom_gamma_sdr_r1) == 64:
            self.custom_gamma_sdr_r1[:] = biased1[:64]
            self.custom_gamma_sdr_g1[:] = biased1[:64]
            self.custom_gamma_sdr_b1[:] = biased1[:64]
        
        # Save to saved arrays
        if len(self.saved_custom_gamma_sdr_r1) == 64:
            self.saved_custom_gamma_sdr_r1[:] = biased1[:64]
            self.saved_custom_gamma_sdr_g1[:] = biased1[:64]
            self.saved_custom_gamma_sdr_b1[:] = biased1[:64]
        
        # Stream 2: Generate curve with default parameters and apply bias
        strength2 = self.saved_curve_strength2.get()
        bias2 = self.saved_bias2.get()
        base2 = generate_custom_gamma_curve(strength=strength2, points=64)
        biased2 = apply_shadow_bias_to_custom_gamma(base2, bias2)
        
        # Apply to Stream 2 (in-place assignment) - only if not already set
        if len(self.custom_gamma_sdr_r2) == 64:
            self.custom_gamma_sdr_r2[:] = biased2[:64]
            self.custom_gamma_sdr_g2[:] = biased2[:64]
            self.custom_gamma_sdr_b2[:] = biased2[:64]
        
        # Save to saved arrays
        if len(self.saved_custom_gamma_sdr_r2) == 64:
            self.saved_custom_gamma_sdr_r2[:] = biased2[:64]
            self.saved_custom_gamma_sdr_g2[:] = biased2[:64]
            self.saved_custom_gamma_sdr_b2[:] = biased2[:64]
        
        # === INPUT (GUI) VARIABLES ===
        self.pq_rgb_mode = tk.StringVar(value="rgb")
        
        self.pq_values_r = self.pq_values.copy()
        self.pq_values_g = self.pq_values.copy()
        self.pq_values_b = self.pq_values.copy()
        
        # Build GUI first - don't start threads yet (splash screen will be shown before mainloop)
        self.build_gui()
        
        # Threads will be started later after mainloop is ready
    
    def build_gui(self):
        """Create GUI with modern design"""
        # Configure ttk styles
        style = ttk.Style()
        style.theme_use("clam")
        
        # Colors from self.colors for access in lambda functions
        colors = self.colors
        
        # Main style for dark theme
        style.configure(".", background=colors["bg"], foreground=colors["text_main"])
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabel", background=colors["bg"], foreground=colors["text_main"])
        style.configure("TLabelframe", background=colors["bg"], foreground=colors["text_main"])
        style.configure("TLabelframe.Label", background=colors["bg"], foreground=colors["text_main"])
        style.configure("TButton", 
                       background=colors["panel_bg"],
                       foreground=colors["text_main"],
                       borderwidth=0,
                       padding=(8, 6))
        style.map("TButton",
                 background=[("active", colors["accent"]), ("pressed", colors["accent_hover"])],
                 foreground=[("disabled", "#5c6370")])
        style.configure("TCheckbutton", 
                       background=colors["bg"],
                       foreground=colors["text_main"])
        style.map("TCheckbutton",
                 background=[("active", colors["panel_bg"])],
                 indicatorcolor=[("selected", colors["accent"])])
        style.configure("TRadiobutton", 
                       background=colors["bg"],
                       foreground=colors["text_main"])
        style.map("TRadiobutton",
                 background=[("active", colors["panel_bg"])],
                 indicatorcolor=[("selected", colors["accent"])])
        style.configure("TCombobox",
                       fieldbackground=colors["panel_bg"],
                       background=colors["panel_bg"],
                       foreground=colors["text_main"],
                       borderwidth=0,
                       padding=(8, 4))
        style.map("TCombobox",
                 fieldbackground=[("readonly", colors["panel_bg"])],
                 background=[("readonly", colors["accent"])],
                 arrowcolor=[("disabled", "#5c6370")])
        style.configure("TScale", 
                       background=colors["bg"],
                       troughcolor=colors["border"])
        style.configure("Vertical.TScale", troughcolor=colors["border"])
        
        # === FIXED: Background Canvas should be at bottom with container inside ===
        # Create main Canvas (background)
        main_canvas = tk.Canvas(self.root, highlightthickness=0, bd=0)
        main_canvas.pack(fill="both", expand=True)
        
        # Load background image for main window
        bg_item = None
        self.bg_original_img = None  # Original image without scaling
        
        try:
            # Use path_utils to resolve background image location
            bg_path = resolve_resource_path("background.png")
            if os.path.exists(bg_path):
                self.bg_original_img = Image.open(bg_path).convert("RGBA")
                # Create initial scaled background
                initial_width = self.root.winfo_width() if self.root.winfo_width() > 1 else 1280
                initial_height = self.root.winfo_height() if self.root.winfo_height() > 1 else 680
                bg_img = self.bg_original_img.resize((initial_width, initial_height), Image.Resampling.LANCZOS)
                self.main_window_bg = ImageTk.PhotoImage(bg_img)
                bg_item = main_canvas.create_image(0, 0, image=self.main_window_bg, anchor="nw")
        except Exception as e:
            print(f"[WARN] Failed to load background for main window: {e}")
        
        # Main container (inside canvas)
        main = ttk.Frame(main_canvas, padding=(10, 10))
        
        main_window = main_canvas.create_window(
            0,
            0,
            anchor="nw",
            window=main
        )
        
        # Stretch Canvas on window resize - SCALE BACKGROUND IN BOTH DIRECTIONS
        def _resize_background(event):
            if bg_item and self.bg_original_img:
                try:
                    new_width = event.width
                    new_height = event.height
                    # Stretch background to entire Canvas (in both directions)
                    resized_img = self.bg_original_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    self.main_window_bg = ImageTk.PhotoImage(resized_img)
                    main_canvas.itemconfig(bg_item, image=self.main_window_bg)
                except:
                    pass
            main_canvas.itemconfigure(main_window, width=event.width)
        
        main_canvas.bind("<Configure>", _resize_background)
        
        # Application title - black background to edges
        title_frame = tk.Frame(main, bg="black", height=84)
        title_frame.pack(fill="x")
        title_frame.pack_propagate(False)
        
        # Container for centering image with black background around
        title_container = tk.Frame(title_frame, bg="black")
        title_container.pack(expand=True, fill="both")
        
        # Load SpectrLed.png image
        try:
            # Use path_utils to resolve SpectrLed image location
            img_path = resolve_resource_path("SpectrLed.png")
            if os.path.exists(img_path):
                img = Image.open(img_path)
                self.title_image = ImageTk.PhotoImage(img)
                
                # Center image - create container with black background
                title_label = tk.Label(title_container, image=self.title_image, bg="black")
                title_label.pack(expand=True)
            else:
                # Fallback to text if image not found
                title_label = tk.Label(
                    title_container,
                    text="Spectr aLED",
                    font=("Segoe UI", 18, "bold"),
                    bg="black",
                    fg=colors["accent"]
                )
                title_label.pack(expand=True)
        except Exception as e:
            print(f"[WARN] Failed to load SpectrLed.png: {e}")
            title_label = tk.Label(
                title_container,
                text="Spectr aLED",
                font=("Segoe UI", 18, "bold"),
                bg="black",
                fg=colors["accent"]
            )
            title_label.pack(expand=True)
        
        # =========================
        # DISPLAY
        # =========================
        display = tk.LabelFrame(
            main,
            text="",  # Remove LabelFrame title
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        display.pack(fill="x", pady=(5, 0))

        # Container for centering
        monitor_container = tk.Frame(display, bg=colors["bg"])
        monitor_container.pack(pady=10)

        tk.Label(
            monitor_container,
            text="📺 Monitor",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"]
        ).pack(side="left", padx=(0, 15))

        self.monitor_combo = ttk.Combobox(
            monitor_container,
            values=self.monitor_list,
            state="readonly",
            width=40
        )
        self.monitor_combo.current(0)
        self.monitor_combo.pack(side="left")

        # Update list when opening Combobox
        self.monitor_combo.bind("<Button-1>", lambda e: self.refresh_monitors())
        self.monitor_combo.bind("<FocusIn>", lambda e: self.refresh_monitors())
        self.monitor_combo.bind("<<ComboboxSelected>>", lambda e: self.on_monitor_change(e))
                
        # =========================
        # CAPTURE PANEL
        # =========================
        capture = tk.LabelFrame(
            main,
            text=" 🎥 Capture",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        capture.pack(fill="x", pady=(15, 0))
        
        # --- ROW 1 (Stream 1) ---
        row1 = tk.Frame(capture, bg=colors["bg"])
        row1.pack(fill="x", pady=(8, 4), padx=10)
        
        self.create_checkbox(row1, "Stream 1", self.first_stream_enabled)
        
        tk.Label(row1, text="W:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left", padx=(20, 4))
        ttk.Entry(row1, textvariable=self.input_target_w, width=6).pack(side="left", padx=2)
        
        tk.Label(row1, text="H:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left", padx=4)
        ttk.Entry(row1, textvariable=self.input_target_h, width=6).pack(side="left", padx=2)
        
        ttk.Button(
            row1,
            text="Apply",
            command=self.apply_resolution
        ).pack(side="left", padx=8)
        
        ttk.Button(
            row1,
            text="👁 Preview",
            command=self.toggle_preview
        ).pack(side="left", padx=4)
        
        aspect_combo = ttk.Combobox(
            row1,
            textvariable=self.aspect1,
            values=["full", "16:9", "21:9", "4:3", "2.39:1", "2:1"],
            width=7,
            state="readonly"
        )
        aspect_combo.pack(side="left", padx=4)
        
        # Apply aspect ratio change immediately without clicking Apply button
        self.aspect1.trace_add("write", lambda *args: self.apply_resolution())
        
        ttk.Button(
            row1,
            text="🔧 Calibration",
            command=self.open_global_calibration
        ).pack(side="left", padx=4)
        
        self.create_checkbox(row1, "Calib. On", self.calibration1_enabled)
        
        ambi_combo = ttk.Combobox(
            row1,
            textvariable=self.ambi_mode1,
            values=["Matrix", "Ambilight 3%", "Ambilight 6%", "Ambilight 9%"],
            width=12,
            state="readonly"
        )
        ambi_combo.pack(side="left", padx=4)
        
        # --- ROW 2 (Stream 2) ---
        row2 = tk.Frame(capture, bg=colors["bg"])
        row2.pack(fill="x", pady=(4, 8), padx=10)
        
        self.create_checkbox(row2, "Stream 2", self.second_stream_enabled)
        
        tk.Label(row2, text="W:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left", padx=(20, 4))
        ttk.Entry(row2, textvariable=self.input_target2_w, width=6).pack(side="left", padx=2)
        
        tk.Label(row2, text="H:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left", padx=4)
        ttk.Entry(row2, textvariable=self.input_target2_h, width=6).pack(side="left", padx=2)
        
        ttk.Button(
            row2,
            text="Apply",
            command=self.apply_second_resolution
        ).pack(side="left", padx=8)
        
        ttk.Button(
            row2,
            text="👁 Preview",
            command=self.toggle_preview2
        ).pack(side="left", padx=4)
        
        aspect_combo2 = ttk.Combobox(
            row2,
            textvariable=self.aspect2,
            values=["full", "16:9", "21:9", "4:3", "2.39:1", "2:1"],
            width=7,
            state="readonly"
        )
        aspect_combo2.pack(side="left", padx=4)
        
        # Apply aspect ratio change immediately without clicking Apply button
        self.aspect2.trace_add("write", lambda *args: self.apply_second_resolution())
        
        ttk.Button(
            row2,
            text="🔧 Calibration",
            command=self.open_global_calibration2
        ).pack(side="left", padx=4)
        
        self.create_checkbox(row2, "Calib. On", self.calibration2_enabled)
        
        ambi_combo2 = ttk.Combobox(
            row2,
            textvariable=self.ambi_mode2,
            values=["Matrix", "Ambilight 3%", "Ambilight 6%", "Ambilight 9%"],
            width=12,
            state="readonly"
        )
        ambi_combo2.pack(side="left", padx=4)
        
        # =========================
        # STREAM SWITCHER
        # =========================
        stream_switcher = tk.Frame(main, bg=colors["bg"])
        stream_switcher.pack(fill="x", pady=(15, 0))
        
        ttk.Separator(stream_switcher).pack(fill="x", expand=True, side="left")
        
        switch_label = tk.Label(
            stream_switcher,
            text="Active Stream:",
            font=("Segoe UI", 9),
            bg=colors["bg"],
            fg=colors["text_main"]
        )
        switch_label.pack(side="left", padx=(12, 12))
        
        self.create_radiobutton(stream_switcher, "Stream 1", self.active_stream, 1, lambda: self.sync_ui_from_stream())
        self.create_radiobutton(stream_switcher, "Stream 2", self.active_stream, 2, lambda: self.sync_ui_from_stream())
        
        ttk.Separator(stream_switcher).pack(fill="x", expand=True, side="right")
        
        # =========================
        # COLOR CORRECTION PANEL
        # =========================
        modes_frame = tk.Frame(main, bg=colors["bg"])
        modes_frame.pack(fill="both", expand=True, pady=(15, 0))
        
        # SDR Panel
        self.sdr_panel = tk.LabelFrame(
            modes_frame,
            text=" 📷 SDR - Color Correction",
            font=("Segoe UI", 9, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        self.sdr_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))
        
        self.create_color_control_row(
            self.sdr_panel, "Brightness:", self.sdr_brightness, 1, 255,
            colors, on_change=lambda *a: self.push_ui_to_active_stream()
        )
        self.create_checkbox_with_var(self.sdr_panel, "Gamma On", self.sdr_gamma_enabled)
        self.create_color_control_row(
            self.sdr_panel, "Gamma:", self.sdr_gamma, 0.5, 3.0, colors,
            resolution=0.1, on_change=lambda *a: self.push_ui_to_active_stream()
        )

        
        self.create_checkbox_with_var(self.sdr_panel, "Saturation On", self.sdr_saturation_enabled)
        self.create_color_control_row(
            self.sdr_panel, "Saturation:", self.sdr_saturation, 0.0, 2.0, colors,
            resolution=0.1, on_change=lambda *a: self.push_ui_to_active_stream()
        )
        # Custom gamma mode switcher
        custom_gamma_mode_frame = tk.Frame(self.sdr_panel, bg=colors["bg"])
        custom_gamma_mode_frame.pack(fill="x", padx=5, pady=(8, 4))
        
        tk.Label(custom_gamma_mode_frame, text="Gamma Mode:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
        
        self.gamma_mode_sdr = tk.StringVar(value="stream")  # "stream" or "custom"
        
        ttk.Radiobutton(
            custom_gamma_mode_frame,
            text="Stream",
            variable=self.gamma_mode_sdr,
            value="stream",
            command=self.on_gamma_mode_change
        ).pack(side="left", padx=(10, 4))
        
        ttk.Radiobutton(
            custom_gamma_mode_frame,
            text="Custom (S1/S2)",
            variable=self.gamma_mode_sdr,
            value="custom",
            command=self.on_gamma_mode_change
        ).pack(side="left", padx=4)
        
        # Custom gamma buttons row
        custom_gamma_buttons = tk.Frame(self.sdr_panel, bg=colors["bg"])
        custom_gamma_buttons.pack(fill="x", padx=5, pady=(4, 8))
        
        self.custom_gamma_s1_btn = ttk.Button(
            custom_gamma_buttons,
            text="🎨 Custom Gamma S1",
            command=lambda: open_custom_gamma_menu_s1(self),
            state="disabled"
        )
        self.custom_gamma_s1_btn.pack(fill="x", side="left", padx=(0, 4))
        
        self.custom_gamma_s2_btn = ttk.Button(
            custom_gamma_buttons,
            text="🎨 Custom Gamma S2",
            command=lambda: open_custom_gamma_menu_s2(self),
            state="disabled"
        )
        self.custom_gamma_s2_btn.pack(fill="x", side="left")        
        
        # HDR Panel
        self.hdr_panel = tk.LabelFrame(
            modes_frame,
            text=" ☀️ HDR - Color Correction",
            font=("Segoe UI", 9, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        self.hdr_panel.pack(side="left", fill="both", expand=True, padx=(0, 8))
        
        self.create_color_control_row(
            self.hdr_panel, "Brightness:", self.hdr_brightness, 1, 255,
            colors, on_change=lambda *a: self.push_ui_to_active_stream()
        )
        self.create_checkbox_with_var(self.hdr_panel, "Gamma On", self.hdr_gamma_enabled)
        self.create_color_control_row(
            self.hdr_panel, "Gamma:", self.hdr_gamma, 0.5, 3.0, colors,
            resolution=0.1, on_change=lambda *a: self.push_ui_to_active_stream()
        )
        self.create_checkbox_with_var(self.hdr_panel, "Saturation On", self.hdr_saturation_enabled)
        self.create_color_control_row(
            self.hdr_panel, "Saturation:", self.hdr_saturation, 0.0, 2.0, colors,
            resolution=0.1, on_change=lambda *a: self.push_ui_to_active_stream()
        )
        
        # Tonemap controls
        ttk.Checkbutton(self.hdr_panel, text="Light mapping", variable=self.tonemap_enabled).pack(anchor="w", padx=5, pady=2)
        
        mode_row = tk.Frame(self.hdr_panel, bg=colors["bg"])
        mode_row.pack(fill="x", padx=5, pady=(4, 8))
        
        tk.Label(mode_row, text="HDR Mode:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
        
        ttk.Radiobutton(
            mode_row,
            text="Gamma",
            variable=self.hdr_tonemap_mode,
            value="gamma"
        ).pack(side="left", padx=(10, 4))
        
        ttk.Radiobutton(
            mode_row,
            text="PQ",
            variable=self.hdr_tonemap_mode,
            value="pq"
        ).pack(side="left", padx=4)
        
        # Peak NITS
        peak_frame = tk.Frame(self.hdr_panel, bg=colors["bg"])
        peak_frame.pack(fill="x", padx=5)
        
        tk.Label(peak_frame, text="Peak (only gamma mod):", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
        self.hdr_peak_label_val = tk.Label(peak_frame, textvariable=self.clip_nits, bg=colors["bg"], fg=colors["accent"])
        self.hdr_peak_label_val.pack(side="left", padx=(8, 4))
        
        tk.Scale(
            self.hdr_panel,
            from_=1000, to=10000,
            resolution=100,
            orient="horizontal",
            variable=self.clip_nits,
            showvalue=0,
            bg=colors["bg"],
            fg=colors["text_main"],
            troughcolor=colors["border"],
            highlightthickness=0
        ).pack(fill="x", padx=5, pady=(4, 8))
        
        # Row with both PQ buttons
        pq_buttons_row = tk.Frame(self.hdr_panel, bg=colors["bg"])
        pq_buttons_row.pack(fill="x", padx=5, pady=(0, 4))
        
        self.pq_button_stream1 = ttk.Button(pq_buttons_row, text="🎨 PQ Curve Editor S1", command=lambda: open_pq_curve_s1(self))
        self.pq_button_stream1.pack(fill="x", side="left", padx=(0, 4))
        
        self.pq_button_stream2 = ttk.Button(pq_buttons_row, text="🎨 PQ Curve Editor S2", command=lambda: open_pq_curve_s2(self))
        self.pq_button_stream2.pack(fill="x", side="left")
        
        # Info Panel - Adaptive width (adjusts to available space)
        info_panel = tk.LabelFrame(
            modes_frame,
            text=" 📊 Information",
            font=("Segoe UI", 9, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        # Pack with expand=True to fill available space
        info_panel.pack(side="left", fill="both", expand=False, padx=(0, 0))
        # Remove fixed width - let panel adapt based on content and window size
        
        # Simple frame for information content (no scrollbar)
        info_content = tk.Frame(info_panel, bg=colors["bg"])
        info_content.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.info_metrics_label = tk.Label(
            info_content,
            text="Waiting for metrics...",
            justify="left",
            anchor="nw",
            font=("Consolas", 9),
            bg=colors["bg"],
            fg=colors["text_main"]
        )
        self.info_metrics_label.pack(fill="x", pady=(0, 5))

        # =========================
        # SUPPORT THE DEVELOPER SECTION
        # =========================
        support_frame = tk.Frame(info_content, bg=colors["bg"])
        support_frame.pack(fill="x", padx=5, pady=(10, 0))

        # Support header row with USDT TRC20 badge (right aligned)
        support_header_frame = tk.Frame(info_content, bg=colors["bg"])
        support_header_frame.pack(fill="x", padx=5, pady=(10, 0))

        support_label = tk.Label(
            support_header_frame,
            text="Support the Developer",
            font=("Segoe UI", 9, "bold"),
            bg=colors["bg"],
            fg=colors["accent"]
        )
        support_label.pack(side="left")

        usdt_badge = tk.Label(
            support_header_frame,
            text="USDT TRC20",
            font=("Consolas", 7, "bold"),
            bg="#9ece6a",
            fg="#1a1b26",
            padx=5,
            pady=2
        )
        usdt_badge.pack(side="right")

        wallet_address_frame = tk.Frame(info_content, bg=colors["bg"])
        wallet_address_frame.pack(fill="x", padx=5, pady=(0, 5))

        self.usdt_wallet_address = "TYbZrXSy4v3gZRLcW7pYBsqNmKdjaQBrNh"
        
        # Wallet address with padding (frame border minus 10px)
        wallet_inner_frame = tk.Frame(wallet_address_frame, bg=colors["panel_bg"])
        wallet_inner_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        # Wallet address label - always in one line with horizontal scroll
        self.wallet_address_label = tk.Label(
            wallet_inner_frame,
            text=self.usdt_wallet_address,
            font=("Consolas", 9),
            bg="#24283b",
            fg="#c0caf5",
            justify="center"
        )
        self.wallet_address_label.pack(side="left", fill="x", expand=True)

        self.copy_btn = ttk.Button(
            wallet_address_frame,
            text="Copy Wallet Address",
            command=self.copy_wallet_address
        )
        self.copy_btn.pack(fill="x", pady=(4, 0))

        # HDR labels
        self.hdr_avg_label = tk.Label(
            self.hdr_panel,
            text="HDR Avg: 0 nits",
            font=("Segoe UI", 9),
            bg=colors["bg"],
            fg=colors["text_dim"]
        )
        self.hdr_avg_label.pack(anchor="w", pady=(4, 0))
        
        # HDR Peak NITS label
        self.hdr_peak_nits_label = tk.Label(
            self.hdr_panel,
            text="HDR Peak: 0 nits",
            font=("Segoe UI", 9),
            bg=colors["bg"],
            fg=colors["text_dim"]
        )
        self.hdr_peak_nits_label.pack(anchor="w", pady=(2, 0))

        
        # =========================
        # CONFIGURATION AND MAPPING (SIDE BY SIDE)
        # =========================
        config_mapping_row = tk.Frame(main, bg=colors["bg"])
        config_mapping_row.pack(fill="x", pady=(20, 5))
        
        # Left side - Configuration frame
        config_frame_left = tk.LabelFrame(
            config_mapping_row,
            text=" ⚙ Configuration",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        config_frame_left.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(0, 8)
        )

        
        btn_row1 = tk.Frame(config_frame_left, bg=colors["bg"])
        btn_row1.pack(fill="x", pady=(8, 4), padx=5)
        
        ttk.Button(
            btn_row1,
            text="💾 Save Default",
            command=self.save_config_default
        ).pack(side="left", padx=2)
        
        ttk.Button(
            btn_row1,
            text="📂 Load from Memory",
            command=self.load_config_default
        ).pack(side="left", padx=2)
        
        ttk.Separator(btn_row1, orient="vertical").pack(fill="y", side="left", padx=(20, 20))
        
        ttk.Button(
            btn_row1,
            text="💾 Save As",
            command=self.save_config_as
        ).pack(side="left", padx=2)
        
        ttk.Button(
            btn_row1,
            text="📂 Load from File",
            command=self.load_config_from
        ).pack(side="left", padx=2)
        
        # Right side - Updates block
        updates_frame = tk.LabelFrame(
            config_mapping_row,
            text=" 🔔 Update",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        updates_frame.pack(side="left", fill="x", padx=(0, 8))
        
        # Check for updates button
        ttk.Button(
            updates_frame,
            text="✓ Check Updates",
            command=self.check_for_updates
        ).pack(side="left", padx=10, pady=8)
        
        # Version label (right side)
        version_label = tk.Label(
            updates_frame,
            text="ver. 1.0.2",
            font=("Consolas", 9),
            bg=colors["bg"],
            fg=colors["accent"]
        )
        version_label.pack(side="left", padx=(4, 10))
        
        # Right side - Mapping frame
        mapping_frame_right = tk.LabelFrame(
            config_mapping_row,
            text=" 🗺 Mapping",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        mapping_frame_right.pack(side="left", fill="x")
        
        # Mapping button (dummy without functionality)
        ttk.Button(
            mapping_frame_right,
            text="🗺 Mapping",
            command=self.map_mapping_button
        ).pack(side="left", padx=10, pady=8)
        
        # =========================
        # WLED
        # =========================
        wled_add_frame = tk.Frame(main, bg=colors["bg"])
        wled_add_frame.pack(fill="x", pady=(15, 0))
        
        ttk.Separator(wled_add_frame).pack(fill="x", expand=True, side="left")
        
        self.wled_ip_combo = ttk.Combobox(
            wled_add_frame,
            textvariable=self.wled_ip_var,
            values=[],
            width=30
        )
        self.wled_ip_combo.pack(side="left", padx=(12, 6), fill="x", expand=True)
        
        scan_btn = tk.Button(
            wled_add_frame,
            text="🔍 Scan",
            font=("Segoe UI", 9),
            bg=colors["panel_bg"],
            fg=colors["text_main"],
            bd=0,
            command=self.scan_wled_devices,
            cursor="hand2"
        )
        scan_btn.pack(side="left", padx=(4, 8))
        
        add_btn = tk.Button(
            wled_add_frame,
            text="➕ Add",
            font=("Segoe UI", 9),
            bg=colors["panel_bg"],
            fg=colors["text_main"],
            bd=0,
            command=self.add_wled_device_from_input,
            cursor="hand2"
        )
        add_btn.pack(side="left", padx=(4, 8))
        
        ttk.Separator(wled_add_frame).pack(fill="x", expand=True, side="right")
        
        
        ttk.Label(main, text="WLED Devices:", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        
        # =========================
        # WLED LIST (Scrollable when >5 devices)
        # =========================
        # Create container for scrollable list
        self.wled_container = tk.Frame(main, bg=colors["bg"])
        self.wled_container.pack(fill="both", expand=True, pady=(0, 15))
        
        # Canvas for scrolling device list (limited height)
        self.wled_canvas = tk.Canvas(self.wled_container, bg=colors["bg"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.wled_container, orient="vertical", command=self.wled_canvas.yview)
        
        # Frame inside canvas for device placement
        self.wled_frame = tk.Frame(self.wled_canvas, bg=colors["bg"])

        # Frame inside Canvas (will stretch to full width)
        self.wled_window = self.wled_canvas.create_window(
            (0, 0),
            window=self.wled_frame,
            anchor="nw",
            width=1
        )

        # Update scroll region when content changes
        self.wled_frame.bind(
            "<Configure>",
            lambda e: self.wled_canvas.configure(
                scrollregion=self.wled_canvas.bbox("all")
            )
        )

        # Stretch frame to full width on Canvas resize
        self.wled_canvas.bind(
            "<Configure>",
            lambda e: self.wled_canvas.itemconfigure(
                self.wled_window,
                width=e.width
            )
        )

        # Scrollbar
        self.wled_canvas.configure(yscrollcommand=scrollbar.set)

        # Place
        self.wled_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Block canvas size propagation (so height works correctly)
        self.wled_canvas.pack_propagate = lambda *args: None
        
        # Initialize device list
        self.update_wled_list()       
        
        # Sync UI
        for var in [
            self.sdr_brightness,
            self.sdr_gamma,
            self.sdr_gamma_enabled,
            self.sdr_saturation_enabled,
            self.sdr_saturation,
            self.hdr_brightness,
            self.hdr_gamma,
            self.hdr_gamma_enabled,
            self.hdr_saturation_enabled,
            self.hdr_saturation
        ]:
            var.trace_add("write", lambda *args: self.push_ui_to_active_stream())
        
        self.sync_ui_from_stream()
    
    def create_checkbox(self, parent, text, variable):
        """Create custom checkbox with modern design"""
        frame = tk.Frame(parent, bg=parent.cget("bg"))
        frame.pack(side="left", padx=4)
        
        cb = tk.Checkbutton(
            frame,
            text=text,
            variable=variable,
            font=("Segoe UI", 9),
            bg=parent.cget("bg"),
            fg=self.colors["text_main"],
            selectcolor=self.colors["panel_bg"],
            activebackground=parent.cget("bg"),
            activeforeground=self.colors["text_main"],
            indicatoron=False,
            bd=1,
            relief="flat",
            highlightthickness=0,
            padx=6,
            pady=4
        )
        cb.pack()
        
        def on_click():
            if variable.get():
                cb.config(bg=self.colors["panel_bg"], fg=self.colors["accent"])
            else:
                cb.config(bg=parent.cget("bg"), fg=self.colors["text_main"])
        
        variable.trace_add("write", lambda *args: on_click())
        on_click()
    
    def create_radiobutton(self, parent, text, variable, value, command=None):
        """Create custom radio button"""
        rb = tk.Radiobutton(
            parent,
            text=text,
            variable=variable,
            value=value,
            font=("Segoe UI", 9),
            bg=parent.cget("bg"),
            fg=self.colors["text_main"],
            selectcolor=self.colors["panel_bg"],
            activebackground=parent.cget("bg"),
            activeforeground=self.colors["accent"],
            indicatoron=False,
            bd=1,
            relief="flat",
            highlightthickness=0,
            padx=8,
            pady=4,
            command=command
        )
        rb.pack(side="left", padx=(4, 12))
        
        def on_change(*args):
            if variable.get() == value:
                rb.config(bg=self.colors["panel_bg"], fg=self.colors["accent"])
            else:
                rb.config(bg=parent.cget("bg"), fg=self.colors["text_main"])
        
        variable.trace_add("write", lambda *args: on_change())
        on_change()
    
    def create_checkbox_with_var(self, parent, text, variable):
        """Create checkbox with auto-update"""
        cb = ttk.Checkbutton(parent, text=text, variable=variable)
        cb.pack(anchor="w", padx=5, pady=2)
    
    def create_color_control_row(self, parent, label_text, var, min_val, max_val, colors, resolution=1.0, on_change=None):
        """Create color control row"""
        row = tk.Frame(parent, bg=colors["bg"])
        row.pack(fill="x", padx=5)
        
        label = tk.Label(row, text=label_text, bg=colors["bg"], fg=colors["text_dim"], width=12, anchor="w")
        label.pack(side="left")
        
        val_label = tk.Label(row, textvariable=var, bg=colors["bg"], fg=colors["accent"], font=("Consolas", 9))
        val_label.pack(side="left", padx=(8, 4))
        
        scale = tk.Scale(
            row,
            from_=min_val, to=max_val,
            resolution=resolution,
            orient="horizontal",
            variable=var,
            showvalue=False,
            bg=colors["bg"],
            fg=colors["text_main"],
            troughcolor=colors["border"],
            highlightthickness=0,
            width=8
        )
        scale.pack(side="left", fill="x", expand=True, padx=(4, 0))
        
        if on_change:
            var.trace_add("write", lambda *a: on_change())
    
    def apply_shadow_bias_to_curve(self, y: np.ndarray, bias: float) -> np.ndarray:
        """Apply shadow bias to PQ curve"""
        return apply_shadow_bias_to_curve(y, bias)
    
    def rebuild_pq_curve(self):
        """Rebuild PQ curve for both streams"""
        # Stream 1
        base1 = generate_pq_exponential(
            strength=self.pq_curve_strength1.get(),
            points=self.pq_points
        )
        base1 = self.apply_shadow_bias_to_curve(base1, self.pq_curve_bias1.get())
        
        # Stream 2
        base2 = generate_pq_exponential(
            strength=self.pq_curve_strength2.get(),
            points=self.pq_points
        )
        base2 = self.apply_shadow_bias_to_curve(base2, self.pq_curve_bias2.get())
        
        # Update values for Stream 1 (if zero, use base curve)
        if np.all(self.pq_values_r1 == 0):
            self.pq_values_r1[:] = base1
        else:
            self.pq_values_r1[:] = base1
        
        # Update values for Stream 2 (if zero, use base curve)
        if np.all(self.pq_values_r2 == 0):
            self.pq_values_r2[:] = base2
        else:
            self.pq_values_r2[:] = base2
        
        # For compatibility update old variables (use Stream 1 as base)
        self.pq_values_r[:] = base1
        self.pq_values_g[:] = base1
        self.pq_values_b[:] = base1
    
    def apply_shadow_bias_nits(self, nits: np.ndarray, bias: float) -> np.ndarray:
        """Apply shadow bias to nits"""
        if bias <= 0.0:
            return nits
        
        t = np.clip(nits / 20.0, 0.0, 1.0)
        
        shadow_mask = np.exp(-t * 5.0)
        lift = 2.0 * shadow_mask
        
        bias_val = bias ** 1.2
        
        return nits + bias_val * lift
    
    def generate_pq_exponential(self, strength: float = 3.0, points: int = None) -> np.ndarray:
        """Generate PQ exponential curve"""
        if points is None:
            points = self.pq_points
        x = np.linspace(0.0, 1.0, points)
        y = np.power(x, strength)
        return np.clip(y, 0.0, 1.0).astype(np.float32)
    
    def update_hdr_mode_ui(self, *args):
        """Update UI for HDR mode"""
        mode = self.hdr_tonemap_mode.get()
        if mode == "pq":
            self.pq_button_stream1.configure(state="normal")
            self.pq_button_stream2.configure(state="normal")
        else:
            self.pq_button_stream1.configure(state="disabled")
            self.pq_button_stream2.configure(state="disabled")
    
    def update_calibration_ui_state(self):
        """Update calibration UI state"""
        state = "disabled" if self.external_lut_enabled.get() else "normal"
        
        for widget in self.sdr_panel.winfo_children():
            try:
                widget.configure(state=state)
            except:
                pass
        
        for widget in self.hdr_panel.winfo_children():
            try:
                widget.configure(state=state)
            except:
                pass
    
    
    def apply_pq_curve(self, hdr_tensor: np.ndarray, stream: int = 1) -> np.ndarray:
        """Apply PQ curve to HDR tensor"""
        
        x = np.clip(hdr_tensor * 80.0, 0.0, 10000.0)
        
        if stream == 1:
            mode = self.pq_rgb_mode1.get()
            values_r = self.pq_values_r1
            values_g = self.pq_values_g1
            values_b = self.pq_values_b1
        else:
            mode = self.pq_rgb_mode2.get()
            values_r = self.pq_values_r2
            values_g = self.pq_values_g2
            values_b = self.pq_values_b2
        
        if mode == "rgb":
            y = np.interp(x, self.pq_nits, values_r)
            return y.astype(np.float32)
        
        out = np.empty_like(hdr_tensor, dtype=np.float32)
        
        out[..., 0] = np.interp(x[..., 0], self.pq_nits, values_b)  # B
        out[..., 1] = np.interp(x[..., 1], self.pq_nits, values_g)  # G
        out[..., 2] = np.interp(x[..., 2], self.pq_nits, values_r)  # R
        
        return out.astype(np.float32)
    
    def load_external_lut(self, stream: int = 1, mode: str = "SDR"):
        """Load external LUT file with topmost priority"""
        path = open_file_dialog(
            self.root, 
            "open", 
            title="Select LUT File",
            filetypes=[("LUT files", "*.npy *.cube *.txt")]
        )
        if not path:
            return
        
        try:
            if path.endswith(".npy"):
                lut = np.load(path)
                lut = lut[..., ::-1]  # RGB to BGR
            elif path.endswith(".cube"):
                lut = self.load_cube_lut(path)
            else:
                raise ValueError("Unsupported LUT format")
            
            # Save LUT and path
            if stream == 1:
                if mode == "SDR":
                    self.external_lut_sdr_1 = lut
                    self.external_lut_sdr_1_path = path
                else:
                    self.external_lut_hdr_1 = lut
                    self.external_lut_hdr_1_path = path
            else:
                if mode == "SDR":
                    self.external_lut_sdr_2 = lut
                    self.external_lut_sdr_2_path = path
                else:
                    self.external_lut_hdr_2 = lut
                    self.external_lut_hdr_2_path = path
            
            print(f"[OK] LUT loaded: S{stream} {mode} -> {path}")
        
        except Exception as e:
            print("[ERROR] LUT load failed:", e)
    
    def load_cube_lut(self, path: str) -> np.ndarray:
        """Load CUBE LUT file"""
        size = 0
        data = []
        
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                
                if not line or line.startswith("#"):
                    continue
                
                if "LUT_3D_SIZE" in line:
                    size = int(line.split()[-1])
                    continue
                
                parts = line.split()
                if len(parts) == 3:
                    data.append([float(x) for x in parts])
        
        if size == 0:
            raise ValueError("LUT_3D_SIZE not found")
        
        lut = np.array(data, dtype=np.float32)
        
        if lut.shape[0] != size ** 3:
            raise ValueError("Invalid LUT size")
        
        lut = lut.reshape((size, size, size, 3))
        lut = lut[..., ::-1]  # RGB to BGR
        
        return lut
    
    def apply_lut_generic(self, frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
        """Apply LUT to frame"""
        return apply_lut_generic(frame, lut)
    
    def apply_ambilight(self, frame: np.ndarray, percent: float, power: float = 2.0) -> np.ndarray:
        """Apply Ambilight effect"""
        return apply_ambilight(frame, percent, power)
    
    def get_stream1_resolution(self) -> tuple:
        """Get Stream 1 resolution"""
        return self.target_w.get(), self.target_h.get()
    
    def request_restart(self, full: bool = False):
        """Request restart capture"""
        with self.restart_lock:
            if self.restart_requested:
                return
            
            self.restart_requested = True
        
        threading.Thread(
            target=self._restart_worker,
            args=(full,),
            daemon=True
        ).start()
    
    def _restart_worker(self, full: bool):
        """Worker thread for restart"""
        print("\n[WARN] Async restart...")
        
        self.dll_restarting = True
        self.capture_paused = True
        
        time.sleep(0.02)
        
        # Clear queues
        for q in [
            self.capture_queue,
            self.capture2_queue,
            self.preview_queue,
            self.preview2_queue
        ]:
            try:
                while True:
                    q.get_nowait()
            except Empty:
                pass
        
        # Recalc resolutions
        w, h = self.recalc_resolution_for_current_state()
        w2, h2 = self.recalc_resolution_stream2()
        
        # Stop DLL
        with self.dll_lock:
            try:
                if self.bridge:
                    self.bridge.shutdown_capture()
            except:
                pass
        
        time.sleep(0.05)
        
        # Start DLL
        with self.dll_lock:
            ok = False
            if self.bridge:
                ok = self.bridge.init_capture(
                    self.monitor_index.get(),
                    w,
                    h
                )
            
            if ok and self.second_stream_enabled.get() and w2 > 0 and h2 > 0:
                try:
                    if self.bridge:
                        self.bridge.set_second_resolution(w2, h2)
                except:
                    pass
        
        # Reset stream 1
        self.frame_buffer = np.empty((h, w, 3), dtype=np.float32)
        self.frame_buffer.fill(0.0)
        
        self.last_frame_id = -1
        self.last_frame_time = time.perf_counter()
        
        # Reset stream 2 (keep last frame if valid)
        self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
        self.frame_buffer2.fill(0.0)
        
        if getattr(self, "last_frame2_valid", None) is not None:
            self.last_frame2_valid = self.last_frame2_valid.copy()
        
        self.last_frame2_id = -1
        self.last_frame2_time = time.perf_counter()
        
        # Unlock
        self.capture_paused = False
        self.dll_restarting = False
        
        with self.restart_lock:
            self.restart_requested = False
        
        print("[OK] Restart done" if ok else "[ERROR] Restart failed")
    
    def center_crop(self, frame: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        """Center crop frame"""
        h, w = frame.shape[:2]
        
        if target_w <= 0 or target_h <= 0:
            return frame
        
        if target_w > w or target_h > h:
            return frame
        
        x1 = (w - target_w) // 2
        y1 = (h - target_h) // 2
        x2 = x1 + target_w
        y2 = y1 + target_h
        
        return frame[y1:y2, x1:x2]
    
    def recalc_resolution_stream2(self) -> tuple:
        """Recalculate Stream 2 resolution"""
        w = self.input_target2_w.get()
        h = self.input_target2_h.get()
        
        aspect = self.aspect2.get()
        
        if aspect == "full":
            return w, h
        
        ratio = self.parse_ratio(aspect)
        return self.compute_aspect_adjusted(w, h, ratio)
    
    def parse_ratio(self, s: str) -> tuple:
        """Parse aspect ratio"""
        w, h = s.split(":")
        return float(w), float(h)
    
    def recalc_resolution_for_current_state(self) -> tuple:
        """Recalculate resolution for current state"""
        w = self.input_target_w.get()
        h = self.input_target_h.get()
        
        aspect = self.aspect1.get()
        
        if aspect == "full":
            return w, h
        
        ratio = self.parse_ratio(aspect)
        return self.compute_aspect_adjusted(w, h, ratio)
    
    def compute_aspect_adjusted(self, w: int, h: int, target_ratio: tuple) -> tuple:
        """Compute resolution with aspect ratio adjustment"""

        mon = self.monitors[self.monitor_index.get()]
        mon_ratio = mon["width"] / mon["height"]
        target = target_ratio[0] / target_ratio[1]

        if mon_ratio > target:
            # Монитор шире целевого
            new_w = int((w / target) * mon_ratio)
            new_h = h
        else:
            # Монитор выше целевого
            new_w = w
            new_h = int((h * target) / mon_ratio)

        return max(1, new_w), max(1, new_h)
    
    def apply_resolution(self):
        """Apply resolution for Stream 1"""
        w = self.input_target_w.get()
        h = self.input_target_h.get()
        
        if w <= 0 or h <= 0:
            return
        
        aspect = self.aspect1.get()
        
        if aspect == "full":
            w2, h2 = w, h
        else:
            ratio = self.parse_ratio(aspect)
            w2, h2 = self.compute_aspect_adjusted(w, h, ratio)
        
        self.target_w.set(w2)
        self.target_h.set(h2)
        
        print(f"[INFO] S1 {w}x{h} -> {w2}x{h2} ({aspect})")
        
        with self.dll_lock:
            if self.bridge:
                self.bridge.shutdown_capture()
                time.sleep(0.1)
                self.bridge.init_capture(self.monitor_index.get(), w2, h2)
        
        self.frame_buffer = np.empty((h2, w2, 3), dtype=np.float32)
        
        # Reset Stream 2 buffer for recreation on resolution change
        if self.second_stream_enabled.get():
            with self.dll_lock:
                try:
                    if self.bridge:
                        self.bridge.set_second_resolution(0, 0)
                        time.sleep(0.05)
                except:
                    pass
            # Clear Stream 2 buffer for recreation in capture_loop
            self.frame_buffer2 = None
        
        self.rebuild_master_mapping()
    
    def apply_second_resolution(self):
        """Apply resolution for Stream 2"""
        w = self.input_target2_w.get()
        h = self.input_target2_h.get()
        
        if w <= 0 or h <= 0:
            print("[INFO] Disable second stream")
            with self.dll_lock:
                if self.bridge:
                    self.bridge.set_second_resolution(0, 0)
            self.frame_buffer2 = None
            return
        
        aspect = self.aspect2.get()
        
        if aspect == "full":
            w2, h2 = w, h
        else:
            ratio = self.parse_ratio(aspect)
            w2, h2 = self.compute_aspect_adjusted(w, h, ratio)
        
        self.target2_w.set(w2)
        self.target2_h.set(h2)
        
        print(f"[INFO] S2 {w}x{h} -> {w2}x{h2} ({aspect})")
        
        with self.dll_lock:
            if self.bridge:
                self.bridge.set_second_resolution(0, 0)
                time.sleep(0.05)
                self.bridge.set_second_resolution(w2, h2)
        
        self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
        
        self.rebuild_master_mapping()
    
    def restart_capture_full(self):
        """Full restart of capture (both streams)"""
        print("\n[WARN] Restarting FULL capture (both streams)...")
        
        self.dll_restarting = True
        self.capture_paused = True
        
        time.sleep(0.05)
        
        # Clear queues
        for q in [
            self.capture_queue,
            self.capture2_queue,
            self.ddp_queue,
            self.ddp2_queue,
            self.preview_queue,
            self.preview2_queue
        ]:
            try:
                while True:
                    q.get_nowait()
            except:
                pass
        
        # Stop DLL
        with self.dll_lock:
            try:
                if self.bridge:
                    self.bridge.shutdown_capture()
            except:
                pass
            
            time.sleep(0.1)
        
        # Get resolutions
        w, h = self.get_stream1_resolution()
        w2, h2 = self.recalc_resolution_stream2()
        
        # Start DLL
        with self.dll_lock:
            ok = False
            if self.bridge:
                ok = self.bridge.init_capture(self.monitor_index.get(), w, h)
            
            if ok and self.second_stream_enabled.get() and w2 > 0 and h2 > 0:
                self._init_second_stream()
        
        # Reset stream 1
        self.frame_buffer = np.empty((h, w, 3), dtype=np.float32)
        self.frame_buffer.fill(0.0)
        
        self.last_frame_time = time.perf_counter()
        self.last_frame_id = -1
        
        # Reset stream 2
        if self.second_stream_enabled.get():
            self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
            self.frame_buffer2.fill(0.0)
            
            if getattr(self, "last_frame2_valid", None) is not None:
                self.last_frame2_valid = self.last_frame2_valid.copy()
        
        self.last_frame2_time = time.perf_counter()
        self.last_frame2_id = -1
        
        self.capture_paused = False
        self.dll_restarting = False
        
        # Update mode highlighting after startup
        self.update_mode_highlight(self.hdr_active)
        
        if ok:
            print("[OK] FULL capture restarted")
        else:
            print("[ERROR] Failed to restart capture")
    
    def _init_second_stream(self):
        """Initialize second stream"""
        w2 = self.target2_w.get()
        h2 = self.target2_h.get()
        
        if w2 <= 0 or h2 <= 0:
            return
        
        print(f"[INFO] Init second stream {w2}x{h2}")
        
        with self.dll_lock:
            try:
                if self.bridge:
                    self.bridge.set_second_resolution(0, 0)
                    time.sleep(0.05)
                    self.bridge.set_second_resolution(w2, h2)
            except:
                print("[WARN] Failed to init second stream")
        
        self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
        
        self.last_frame2_time = time.perf_counter()
        self.last_frame2_id = -1
    
    def on_first_stream_toggle(self, *args):
        """Stream 1 toggle handler"""
        self.stream1_enabled = self.first_stream_enabled.get()
        
        if not self.stream1_enabled:
            print("[INFO] Disable stream1 ONLY")
            
            self.exp_pixel_indices = None
            self.last_ddp_frame = None
            
            for q in [self.capture_queue, self.ddp_queue, self.preview_queue]:
                try:
                    while True:
                        q.get_nowait()
                except Empty:
                    pass
    
    def open_global_calibration2(self):
        """Open Stream 2 calibration window"""
        open_calibration_stream2(self, self.global_calibration2)
    
    def get_active_params(self) -> dict:
        """Get active stream parameters (gamma mode unified for both streams)"""
        if self.active_stream.get() == 1:
            params = self.stream1_vars.copy()
            params["gamma_mode_sdr"] = self.gamma_mode_sdr.get()
            return params
        else:
            # For Stream 2 use the same gamma mode
            params = self.stream2_vars.copy()
            params['gamma_mode_sdr'] = self.gamma_mode_sdr.get()  # Use unified mode not sdr2_gamma_mode
            return params
    
    def sync_ui_from_stream(self):
        """Sync UI from active stream"""
        if self._sync_lock:
            return
        self._sync_lock = True
        
        p = self.get_active_params()
        
        self.sdr_brightness.set(p["brightness_sdr"].get())
        self.sdr_gamma.set(p["gamma_sdr"].get())
        self.sdr_gamma_enabled.set(p["gamma_sdr_en"].get())
        
        # Sync gamma mode if available
        if "gamma_mode_sdr" in p:
            self.gamma_mode_sdr.set(p["gamma_mode_sdr"])
        
        self.sdr_saturation_enabled.set(p["sat_sdr_en"].get())
        self.sdr_saturation.set(p["sat_sdr"].get())
        
        self.hdr_brightness.set(p["brightness_hdr"].get())
        self.hdr_gamma.set(p["gamma_hdr"].get())
        self.hdr_gamma_enabled.set(p["gamma_hdr_en"].get())
        self.hdr_saturation_enabled.set(p["sat_hdr_en"].get())
        self.hdr_saturation.set(p["sat_hdr"].get())
        
        self._sync_lock = False
    
    def push_ui_to_active_stream(self):
        """Push UI to active stream"""
        if self._sync_lock:
            return
        self._sync_lock = True
        
        p = self.get_active_params()
        
        p["brightness_sdr"].set(self.sdr_brightness.get())
        p["gamma_sdr"].set(self.sdr_gamma.get())
        p["gamma_sdr_en"].set(self.sdr_gamma_enabled.get())
        
        # Sync gamma mode if available
        if "gamma_mode_sdr" in p:
            p["gamma_mode_sdr"] = self.gamma_mode_sdr.get()
        
        p["sat_sdr_en"].set(self.sdr_saturation_enabled.get())
        p["sat_sdr"].set(self.sdr_saturation.get())
        
        p["brightness_hdr"].set(self.hdr_brightness.get())
        p["gamma_hdr"].set(self.hdr_gamma.get())
        p["gamma_hdr_en"].set(self.hdr_gamma_enabled.get())
        p["sat_hdr_en"].set(self.hdr_saturation_enabled.get())
        p["sat_hdr"].set(self.hdr_saturation.get())
        
        self._sync_lock = False
    
    def on_second_stream_toggle(self, *args):
        """Stream 2 toggle handler"""
        # Update internal stream state flag for use in background threads
        self.stream2_enabled = bool(self.second_stream_enabled.get())
        
        if not self.second_stream_enabled.get():
            print("[INFO] HARD disable second stream (DLL)")
            
            self.preview2_enabled = False
            
            if hasattr(self, "preview2_window") and self.preview2_window is not None:
                try:
                    self.preview2_window.destroy()
                except:
                    pass
                self.preview2_window = None
            
            self.capture_paused = True
            time.sleep(0.05)
            
            with self.dll_lock:
                if self.bridge:
                    self.bridge.set_second_resolution(0, 0)
            
            self.frame_buffer2 = None
            
            try:
                while True:
                    self.preview2_queue.get_nowait()
            except Empty:
                pass
            
            self.capture_paused = False
    
    def toggle_preview2(self):
        """Stream 2 preview toggle"""
        self.preview2_enabled = not self.preview2_enabled
    
    def generate_3d_lut(self, calibration: dict, size: int = 128) -> np.ndarray:
        """Generate 3D LUT for LED calibration"""
        return generate_3d_lut(calibration, size)
    
    def open_global_calibration(self):
        """Open Stream 1 calibration window"""
        open_calibration_stream1(self, self.global_calibration)
    
    def create_default_calibration(self) -> dict:
        """Create default calibration"""
        return DEFAULT_CALIBRATION.copy()
    
    def on_gamma_mode_change(self):
        """Gamma mode change handler (stream/custom)"""
        from custom_gamma_s1 import generate_custom_gamma_curve, apply_shadow_bias_to_custom_gamma

        mode = self.gamma_mode_sdr.get()

        # If custom mode, buttons are active
        if mode == "custom":
            self.custom_gamma_s1_btn.configure(state="normal")
            self.custom_gamma_s2_btn.configure(state="normal")

            # IMPORTANT: Use in-place assignment to preserve array references for slider callbacks
            # Restore saved 64-point curves directly - these are the source of truth
            if len(self.saved_custom_gamma_sdr_r1) == 64:
                self.custom_gamma_sdr_r1[:] = self.saved_custom_gamma_sdr_r1[:64]
                self.custom_gamma_sdr_g1[:] = self.saved_custom_gamma_sdr_g1[:64]
                self.custom_gamma_sdr_b1[:] = self.saved_custom_gamma_sdr_b1[:64]

            if len(self.saved_custom_gamma_sdr_r2) == 64:
                self.custom_gamma_sdr_r2[:] = self.saved_custom_gamma_sdr_r2[:64]
                self.custom_gamma_sdr_g2[:] = self.saved_custom_gamma_sdr_g2[:64]
                self.custom_gamma_sdr_b2[:] = self.saved_custom_gamma_sdr_b2[:64]

            # Apply curve and bias from saved parameters ONLY if 64-point curves are not available
            # (e.g., on first run or after loading config without 64-point values)
            if len(self.custom_gamma_sdr_r1) != 64:
                strength1 = self.saved_curve_strength1.get()
                bias1 = self.saved_bias1.get()
                base1 = generate_custom_gamma_curve(strength=strength1, points=64)
                biased1 = apply_shadow_bias_to_custom_gamma(base1, bias1)

                # Apply to Stream 1 (in-place assignment)
                self.custom_gamma_sdr_r1[:] = biased1[:64]
                self.custom_gamma_sdr_g1[:] = biased1[:64]
                self.custom_gamma_sdr_b1[:] = biased1[:64]

            if len(self.custom_gamma_sdr_r2) != 64:
                strength2 = self.saved_curve_strength2.get()
                bias2 = self.saved_bias2.get()
                base2 = generate_custom_gamma_curve(strength=strength2, points=64)
                biased2 = apply_shadow_bias_to_custom_gamma(base2, bias2)

                # Apply to Stream 2 (in-place assignment)
                self.custom_gamma_sdr_r2[:] = biased2[:64]
                self.custom_gamma_sdr_g2[:] = biased2[:64]
                self.custom_gamma_sdr_b2[:] = biased2[:64]

            # Disable standard gamma for SDR
            # (this is done in process_loop via mode check)
        else:
            # Stream gamma - buttons inactive
            self.custom_gamma_s1_btn.configure(state="disabled")
            self.custom_gamma_s2_btn.configure(state="disabled")

            # Save current values before reset (to restore on return)
            # Use in-place assignment to preserve array references
            if len(self.custom_gamma_sdr_r1) == 64:
                self.saved_custom_gamma_sdr_r1[:] = self.custom_gamma_sdr_r1[:64]
                self.saved_custom_gamma_sdr_g1[:] = self.custom_gamma_sdr_g1[:64]
                self.saved_custom_gamma_sdr_b1[:] = self.custom_gamma_sdr_b1[:64]

            if len(self.custom_gamma_sdr_r2) == 64:
                self.saved_custom_gamma_sdr_r2[:] = self.custom_gamma_sdr_r2[:64]
                self.saved_custom_gamma_sdr_g2[:] = self.custom_gamma_sdr_g2[:64]
                self.saved_custom_gamma_sdr_b2[:] = self.custom_gamma_sdr_b2[:64]

            # Save current curve and bias parameters
            # (Values will be updated when custom gamma window is opened/updated)

        # Synchronize SDR gamma for both streams when changing mode
        if mode == "stream":
            # When switching to stream gamma, reset custom values to linear
            n = 64
            for i in range(n):
                if i == 0:
                    val = 0.0
                elif i == n - 1:
                    val = 255.0
                else:
                    val = (i / (n - 1)) * 255.0

                # Reset for both Stream 1 and Stream 2
                self.custom_gamma_sdr_r1[i] = val
                self.custom_gamma_sdr_g1[i] = val
                self.custom_gamma_sdr_b1[i] = val
                self.custom_gamma_sdr_r2[i] = val
                self.custom_gamma_sdr_g2[i] = val
                self.custom_gamma_sdr_b2[i] = val
    
    def apply_led_calibration(self, tensor: np.ndarray) -> np.ndarray:
        """Apply LED calibration Stream 1"""
        
        frame = np.clip(tensor, 0.0, 1.0)
        
        if self.external_lut_enabled.get():
            if self.hdr_active:
                lut = getattr(self, "external_lut_hdr_1", None)
            else:
                lut = getattr(self, "external_lut_sdr_1", None)
            
            if lut is not None:
                return apply_lut_generic(frame, lut)
        
        # Use LUT array size, not variable value
        size = self.global_lut.shape[0] - 1
        
        pos = frame * size
        i0 = np.floor(pos).astype(np.int32)
        i0 = np.clip(i0, 0, size)
        i1 = np.clip(i0 + 1, 0, size)
        
        f = pos - i0
        
        fx = f[..., 0:1]
        fy = f[..., 1:2]
        fz = f[..., 2:3]
        
        lut = self.global_lut
        
        c000 = lut[i0[..., 0], i0[..., 1], i0[..., 2]]
        c100 = lut[i1[..., 0], i0[..., 1], i0[..., 2]]
        c010 = lut[i0[..., 0], i1[..., 1], i0[..., 2]]
        c110 = lut[i1[..., 0], i1[..., 1], i0[..., 2]]
        
        c001 = lut[i0[..., 0], i0[..., 1], i1[..., 2]]
        c101 = lut[i1[..., 0], i0[..., 1], i1[..., 2]]
        c011 = lut[i0[..., 0], i1[..., 1], i1[..., 2]]
        c111 = lut[i1[..., 0], i1[..., 1], i1[..., 2]]
        
        c00 = c000 * (1 - fx) + c100 * fx
        c01 = c001 * (1 - fx) + c101 * fx
        c10 = c010 * (1 - fx) + c110 * fx
        c11 = c011 * (1 - fx) + c111 * fx
        
        c0 = c00 * (1 - fy) + c10 * fy
        c1 = c01 * (1 - fy) + c11 * fy
        
        return c0 * (1 - fz) + c1 * fz
    
    def apply_led_calibration2(self, tensor: np.ndarray) -> np.ndarray:
        """Apply LED calibration Stream 2"""
        
        frame = np.clip(tensor, 0.0, 1.0)
        
        if self.external_lut_enabled.get():
            if self.hdr_active:
                lut = getattr(self, "external_lut_hdr_2", None)
            else:
                lut = getattr(self, "external_lut_sdr_2", None)
            
            if lut is not None:
                return apply_lut_generic(frame, lut)
        
        # Use LUT array size, not variable value
        size = self.global_lut2.shape[0] - 1
        
        pos = frame * size
        i0 = np.floor(pos).astype(np.int32)
        i1 = np.clip(i0 + 1, 0, size)
        
        f = pos - i0
        
        fx = f[..., 0:1]
        fy = f[..., 1:2]
        fz = f[..., 2:3]
        
        lut = self.global_lut2
        
        c000 = lut[i0[..., 0], i0[..., 1], i0[..., 2]]
        c100 = lut[i1[..., 0], i0[..., 1], i0[..., 2]]
        c010 = lut[i0[..., 0], i1[..., 1], i0[..., 2]]
        c110 = lut[i1[..., 0], i1[..., 1], i0[..., 2]]
        
        c001 = lut[i0[..., 0], i0[..., 1], i1[..., 2]]
        c101 = lut[i1[..., 0], i0[..., 1], i1[..., 2]]
        c011 = lut[i0[..., 0], i1[..., 1], i1[..., 2]]
        c111 = lut[i1[..., 0], i1[..., 1], i1[..., 2]]
        
        c00 = c000 * (1 - fx) + c100 * fx
        c01 = c001 * (1 - fx) + c101 * fx
        c10 = c010 * (1 - fx) + c110 * fx
        c11 = c011 * (1 - fx) + c111 * fx
        
        c0 = c00 * (1 - fy) + c10 * fy
        c1 = c01 * (1 - fy) + c11 * fy
        
        return c0 * (1 - fz) + c1 * fz
    
    def apply_saturation(self, tensor: np.ndarray, strength: float) -> np.ndarray:
        """Apply saturation"""
        return apply_saturation(tensor, strength)
    
    def push_latest(self, queue_obj: Queue, item):
        """Push latest element to queue"""
        try:
            while True:
                queue_obj.get_nowait()
        except Empty:
            pass
        
        try:
            queue_obj.put_nowait(item)
        except Full:
            pass
    
    def update_mode_highlight(self, hdr_active: bool):
        """Update HDR/SDR mode highlight"""
        success_color = "#9ece6a"  # Green color from self.colors
        text_main_color = "#c0caf5"
        
        if hdr_active:
            self.hdr_avg_label.config(text=f"HDR Avg: {self.avg_nits:.1f} nits")
            self.hdr_peak_nits_label.config(text=f"HDR Peak: {self.peak_nits:.1f} nits")
            # Highlight HDR panel in green
            self.hdr_panel.configure(
                fg=success_color,
                highlightbackground=success_color,
                highlightthickness=2
            )
            # Disable SDR panel highlighting
            self.sdr_panel.configure(
                fg=text_main_color,
                highlightbackground=self.colors["border"],
                highlightthickness=1
            )
        else:
            self.hdr_avg_label.config(text="HDR Avg: SDR")
            self.hdr_peak_nits_label.config(text="HDR Peak: SDR")
            # Highlight SDR panel in green
            self.sdr_panel.configure(
                fg=success_color,
                highlightbackground=success_color,
                highlightthickness=2
            )
            # Disable HDR panel highlighting
            self.hdr_panel.configure(
                fg=text_main_color,
                highlightbackground=self.colors["border"],
                highlightthickness=1
            )
    
    def update_nits_labels(self):
        """Update nits labels with current avg/peak values"""
        if not self.hdr_active:
            return
        
        # Limit update frequency - not more than 4 times per second (250 ms)
        current_time = time.perf_counter()
        if current_time - self.last_nits_update_time < 0.25:
            return
        
        self.last_nits_update_time = current_time
        self.hdr_avg_label.config(text=f"HDR Avg: {self.avg_nits:.1f} nits")
        self.hdr_peak_nits_label.config(text=f"HDR Peak: {self.peak_nits:.1f} nits")
    
    def toggle_stream(self, index: int):
        """Toggle stream for device"""
        dev = WLED_DEVICES[index]
        
        dev["stream"] = 2 if dev.get("stream", 1) == 1 else 1
        
        self.rebuild_master_mapping()
        self.update_wled_list()
    
    def get_wled_name(self, ip: str) -> str:
        """Get WLED device name with fast online check"""
        # Fast check if host is online using TCP socket
        if not is_host_online(ip, port=80, timeout=0.3):
            return "WLED"
        
        try:
            with urllib.request.urlopen(f"http://{ip}/json/info", timeout=1) as r:
                data = json.loads(r.read().decode())
                return data.get("name", "WLED")
        except:
            return "WLED"
    
    def ping_wled_device(self, ip: str) -> bool:
        """
        Check if WLED device is online using fast socket check first.
        Returns True if reachable, False otherwise.
        """
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            return False
        
        try:
            with urllib.request.urlopen(f"http://{ip}/json/info", timeout=1) as r:
                data = json.loads(r.read().decode())
                return "name" in data or "WLED" in str(data)
        except:
            return False
    
    def get_wled_led_count(self, ip: str) -> int:
        """Get number of LEDs on device with fast online check"""
        # Fast check if host is online using TCP socket (0.3s timeout)
        if not is_host_online(ip, port=80, timeout=0.3):
            return DEFAULT_LED_COUNT
        
        try:
            with urllib.request.urlopen(f"http://{ip}/json/info", timeout=1) as r:
                data = json.loads(r.read().decode())
                return data.get("leds", {}).get("count", DEFAULT_LED_COUNT)
        except:
            return DEFAULT_LED_COUNT
    
    def scan_wled_devices(self):
        """Scan WLED devices"""
        print("[INFO] Async scanning WLED devices...")
        
        import asyncio
        import ipaddress
        
        def get_local_ips():
            ips = set()
            
            hostname = socket.gethostname()
            try:
                for ip in socket.gethostbyname_ex(hostname)[2]:
                    if ip.startswith("192.168."):
                        ips.add(ip)
            except:
                pass
            
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                ips.add(s.getsockname()[0])
                s.close()
            except:
                pass
            
            return ips
        
        def build_subnets():
            subnets = set()
            
            for ip in get_local_ips():
                try:
                    net = ipaddress.ip_network(ip + "/24", strict=False)
                    base = str(net.network_address).rsplit(".", 1)[0]
                    subnets.add(base)
                except:
                    pass
            
            subnets.add("192.168.1")
            subnets.add("192.168.137")
            
            return list(subnets)
        
        async def check_ip(ip, semaphore):
            url = f"http://{ip}/json/info"
            
            async with semaphore:
                try:
                    reader, writer = await asyncio.wait_for(
                        asyncio.open_connection(ip, 80),
                        timeout=0.3
                    )
                    
                    request = (
                        f"GET /json/info HTTP/1.1\r\n"
                        f"Host: {ip}\r\n"
                        f"Connection: close\r\n\r\n"
                    )
                    
                    writer.write(request.encode())
                    await writer.drain()
                    
                    data = await asyncio.wait_for(reader.read(512), timeout=0.3)
                    
                    writer.close()
                    await writer.wait_closed()
                    
                    if b"WLED" in data or b"name" in data:
                        try:
                            text = data.decode(errors="ignore")
                            name = "WLED"
                            
                            if '"name":"' in text:
                                name = text.split('"name":"')[1].split('"')[0]
                            
                            return f"{ip} ({name})"
                        except:
                            return f"{ip} (WLED)"
                
                except:
                    return None
        
        async def scan():
            subnets = build_subnets()
            print("[INFO] Subnets:", subnets)
            
            tasks = []
            semaphore = asyncio.Semaphore(100)
            
            for base in subnets:
                for i in range(1, 255):
                    ip = f"{base}.{i}"
                    tasks.append(check_ip(ip, semaphore))
            
            results = await asyncio.gather(*tasks)
            
            return [r for r in results if r]
        
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            results = loop.run_until_complete(scan())
            loop.close()
            
            self.wled_discovered = results
            self.wled_ip_combo["values"] = results
            
            if results:
                self.wled_ip_combo.current(0)
            
            print("[INFO] Found:", results)
        
        threading.Thread(target=run_async, daemon=True).start()
    
    def add_wled_device_from_input(self):
        """Add WLED device from input"""
        raw = self.wled_ip_var.get().strip()
        ip = raw.split(" ")[0]
        
        if not ip:
            print("Enter IP")
            return
        
        if any(dev["ip"] == ip for dev in WLED_DEVICES):
            print(f"[WARN] WLED {ip} already added")
            return
        
        # Get device name first
        name = self.get_wled_name(ip)
        
        # Add device without DDP mode (will switch to DDP when mapping is loaded)
        new_device = {
            "ip": ip,
            "name": name,
            "mapping": None,
            "offset": 0,
            "length": 0,
            "stream": 1,
            "online": False  # Default status
        }
        
        WLED_DEVICES.append(new_device)
        
        self.rebuild_master_mapping()
        self.update_wled_list()
        
        print(f"WLED ADDED: {ip} ({name}) - waiting for mapping")
    
    def load_mapping_for_device(self, index: int):
        """Load mapping for device and switch to DDP mode if not already in it"""
        global MASTER_MAPPING_DIRTY
        
        if index >= len(WLED_DEVICES):
            return
        
        mapping = load_mapping_file()
        if mapping is None:
            return
        
        dev = WLED_DEVICES[index]
        
        dev["mapping"] = mapping
        dev["length"] = len(mapping)
        
        MASTER_MAPPING_DIRTY = True
        
        self.rebuild_master_mapping()
        self.update_wled_list()
        
        # Switch to DDP mode after loading mapping
        ip = dev.get("ip")
        if ip:
            set_wled_ddp_mode(ip, keep_last_frame=True)
        
        print(f"[OK] Mapping loaded for {dev['ip']} ({len(mapping)} LEDs) - DDP mode ON")
    
    def test_wled_device(self, ip: str, led_count_unused=None):
        """Test WLED device"""
        def send_json_color(r: int, g: int, b: int):
            payload = {
                "on": True,
                "bri": 255,
                "live": False,
                "seg": [{
                    "id": 0,
                    "fx": 0,
                    "col": [[r, g, b]]
                }]
            }
            
            req = urllib.request.Request(
                f"http://{ip}/json/state",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            urllib.request.urlopen(req, timeout=2)
        
        def run():
            try:
                send_json_color(255, 0, 0)
                time.sleep(1)
                
                send_json_color(0, 255, 0)
                time.sleep(1)
                
                send_json_color(0, 0, 255)
                time.sleep(1)
                
                send_json_color(10, 10, 10)
            
            except Exception as e:
                print("[TEST ERROR]", e)
        
        threading.Thread(target=run, daemon=True).start()
    
    def restart_capture(self):
        """Restart capture"""
        print("\n[WARN] No frames - restarting capture...")
        
        self.dll_restarting = True
        self.capture_paused = True
        
        time.sleep(0.05)
        
        with self.dll_lock:
            try:
                if self.bridge:
                    self.bridge.shutdown_capture()
            except:
                pass
            
            time.sleep(0.1)
            
            ok = False
            if self.bridge:
                ok = self.bridge.init_capture(self.monitor_index.get(), TARGET_W, TARGET_H)
        
        # Reset main state
        self.frame_buffer = np.empty((TARGET_H, TARGET_W, 3), dtype=np.float32)
        
        self.last_frame_time = time.perf_counter()
        self.last_frame_id = -1
        
        # Reset second stream
        self.last_frame2_time = time.perf_counter()
        self.last_frame2_id = -1
        
        if self.second_stream_enabled.get():
            self._init_second_stream()
        
        self.capture_paused = False
        self.dll_restarting = False
        
        if not ok:
            print("[ERROR] Failed to reinitialize capture DLL")
        else:
            print("[OK] Capture restarted (WLED keeps last frame)")
    
    def ddp_send_loop(self):
        """DDP send loop for Stream 1"""
        while self.running:
            
            if not self.stream1_enabled:
                time.sleep(0.01)
                continue
            
            try:
                frame = self.ddp_queue.get(timeout=0.5)
            except Empty:
                
                if (
                    self.last_ddp_frame is not None
                    and self.streaming_enabled
                    and self.stream1_enabled
                ):
                    frame_view = memoryview(self.last_ddp_frame)
                    
                    for dev in self.device_slices:
                        start = dev["start"] * 3
                        end = dev["end"] * 3
                        
                        chunk = frame_view[start:end]
                        send_ddp(dev["ip"], chunk)
                    
                    continue
                
                time.sleep(0.01)
                continue
            
            if not self.streaming_enabled or not self.stream1_enabled:
                continue
            
            send_start = time.perf_counter()
            
            frame_view = memoryview(frame)
            
            for dev in self.device_slices:
                start = dev["start"] * 3
                end = dev["end"] * 3
                
                chunk = frame_view[start:end]
                send_ddp(dev["ip"], chunk)
            
            self.last_ddp_frame = frame
            
            self.ddp_delay_ms = (time.perf_counter() - send_start) * 1000
            self.ddp_frame_count += 1
            self.last_ddp_frame_time = time.perf_counter()
    
    def ddp2_send_loop(self):
        """DDP send loop for Stream 2"""
        while self.running:
            
            # Use internal flag instead of Tkinter variable to avoid RuntimeError after mainloop shutdown
            if not self.stream2_enabled:
                time.sleep(0.01)
                continue
            
            try:
                frame = self.ddp2_queue.get(timeout=0.5)
            
            except Empty:
                
                if (
                    self.last_ddp2_frame is not None
                    and self.streaming2_enabled
                    and self.stream2_enabled
                ):
                    frame_view = memoryview(self.last_ddp2_frame)
                    
                    for dev in self.device_slices2:
                        start = dev["start"] * 3
                        end = dev["end"] * 3
                        
                        chunk = frame_view[start:end]
                        send_ddp(dev["ip"], chunk)
                    
                    continue
                
                time.sleep(0.01)
                continue
            
            if not self.streaming2_enabled or not self.stream2_enabled:
                 continue
            
            send_start = time.perf_counter()
            
            frame_view = memoryview(frame)
            
            for dev in self.device_slices2:
                start = dev["start"] * 3
                end = dev["end"] * 3
                
                chunk = frame_view[start:end]
                send_ddp(dev["ip"], chunk)
            
            # Save last frame for keepalive
            self.last_ddp2_frame = frame
            
            self.ddp2_delay_ms = (time.perf_counter() - send_start) * 1000
            self.ddp2_frame_count += 1
            self.last_ddp2_frame_time = time.perf_counter()
    
    def remove_wled_device(self, index: int):
        """Remove WLED device"""
        if index < 0 or index >= len(WLED_DEVICES):
            return
        
        dev = WLED_DEVICES[index]
        
        try:
            restore_wled(dev["ip"])
        except:
            pass
        
        WLED_DEVICES.pop(index)
        
        self.rebuild_master_mapping()
        
        self.mapping_fps.clear()
        self.mapping_counts.clear()
        
        self.update_wled_list()
        
        print("REMOVED:", dev["ip"])
    
    def add_wled_device(self):
        """Add WLED device"""
        global MASTER_MAPPING_DIRTY
        
        ip = simpledialog.askstring("WLED IP", "Enter device IP:")
        if not ip:
            return
        
        if any(dev["ip"] == ip for dev in WLED_DEVICES):
            print(f"[WARN] WLED {ip} already added")
            return
        
        mapping = load_mapping_file()
        if mapping is None:
            return
        
        if not set_wled_ddp_mode(ip):
            return
        
        led_count = len(mapping)
        
        offset = len(MASTER_MAPPING)
        
        MASTER_MAPPING.extend(mapping)
        MASTER_MAPPING_DIRTY = True
        
        WLED_DEVICES.append({
            "ip": ip,
            "name": self.get_wled_name(ip),
            "mapping": None,
            "offset": 0,
            "length": 0
        })
        
        try:
            restore_wled(ip)
        except Exception as e:
            print("[WARN] Failed to set default color:", e)
        
        self.rebuild_master_mapping()
        self.update_wled_list()
        
        print(f"WLED ADDED: {ip}")
    
    def load_brightness_table(self):
        """Load brightness table with topmost priority"""
        path = open_file_dialog(
            self.root,
            "open",
            title="Select Brightness Table File",
            filetypes=[("Text files", "*.txt")]
        )
        if not path:
            return
        
        levels = []
        nits = []
        
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    lvl, nit = line.split()
                    levels.append(int(lvl))
                    nits.append(float(nit))
                except:
                    continue
        
        if levels and nits:
            self.brightness_table = {
                "levels": levels,
                "nits": nits
            }
            print("Brightness table loaded:", self.brightness_table)
    
    def update_wled_list(self):
        """Update WLED device list in UI"""
        # Clear old widgets
        for widget in self.wled_frame.winfo_children():
            widget.destroy()
        
        colors = self.colors
        
        # Check device count - if more than 5, set fixed height
        device_count = len(WLED_DEVICES)
        if device_count > 5:
            # Block canvas with fixed height and enable scrolling
            self.wled_canvas.configure(height=280, yscrollincrement=20)
            self.wled_canvas.config(scrollregion=self.wled_canvas.bbox("all"))
        else:
            # Unblock canvas - it will take as much space as needed
            self.wled_canvas.configure(height=device_count * 55 + 10 if device_count > 0 else 20)
        
        for i, dev in enumerate(WLED_DEVICES):
            row = tk.Frame(self.wled_frame, bg=colors["bg"])
            row.pack(fill="x", pady=(2, 0))
            
            name = dev.get("name", "WLED")
            led_info = f"{dev['length']} LEDs" if dev["mapping"] else "no mapping"
            status = "Stream" if dev["mapping"] else "Waiting"
            
            # Build device info text: IP (Name) [led_info] [status] [online_status]
            # Online status will be added later in ping thread or here
            main_text = f"{dev['ip']} ({name}) [{led_info}] [{status}]"
            
            # Status label with online indicator on the right side of IP info
            ip_label = tk.Label(
                row,
                text=main_text,
                font=("Consolas", 9),
                bg=colors["bg"],
                fg=colors["text_main"]
            )
            ip_label.pack(side="left", padx=(8, 12))
            
            # Online/Offline status label (right after IP info)
            online_status = dev.get("online", False)
            if online_status:
                status_text = "Online"
                status_fg = colors["success"]  # Green
            else:
                status_text = "Offline"
                status_fg = colors["error"]  # Red
            
            status_label = tk.Label(
                row,
                text=f"[{status_text}]",
                font=("Consolas", 9),
                bg=colors["bg"],
                fg=status_fg
            )
            status_label.pack(side="left", padx=(4, 8))
            
            def create_button(parent, text, cmd):
                btn = tk.Button(
                    parent,
                    text=text,
                    font=("Segoe UI", 8),
                    bg=colors["panel_bg"],
                    fg=colors["text_main"],
                    bd=0,
                    command=lambda c=cmd: c(),
                    cursor="hand2",
                    relief="flat"
                )
                btn.pack(side="right", padx=(2, 6))
                return btn
            
            create_button(row, "🧪 Test", lambda d=dev: self.test_wled_device(d["ip"], d["length"]))
            create_button(row, "📂 Load Mapping", lambda i=i: self.load_mapping_for_device(i))
            create_button(row, "🗑 Delete", lambda i=i: self.remove_wled_device(i))
            
            def toggle_stream_cmd(dev=dev):
                idx = WLED_DEVICES.index(dev)
                self.toggle_stream(idx)
            
            btn_text = f"Stream {dev.get('stream', 1)}"
            create_button(row, btn_text, toggle_stream_cmd)
    
    def rebuild_master_mapping(self):
        """Rebuild master mapping"""
        global MASTER_MAPPING_DIRTY

        self.device_slices = []
        self.device_slices2 = []

        mapping1 = []
        mapping2 = []

        # Split mapping by streams
        for dev in WLED_DEVICES:

            if not dev["mapping"]:
                continue

            if dev.get("stream", 1) == 1:
                target_mapping = mapping1
                target_slices = self.device_slices
            else:
                target_mapping = mapping2
                target_slices = self.device_slices2

            start = len(target_mapping)

            target_mapping.extend(dev["mapping"])

            target_slices.append({
                "ip": dev["ip"],
                "start": start,
                "end": start + dev["length"]
            })

        # Dimensions from GUI
        w1 = self.target_w.get()
        h1 = self.target_h.get()

        w2 = self.target2_w.get()
        h2 = self.target2_h.get()

        # =========================
        # STREAM 1
        # =========================
        if mapping1:
            coords = np.array(mapping1, dtype=np.int32)

            mask = (
                (coords[:, 0] >= 0) &
                (coords[:, 0] < h1) &
                (coords[:, 1] >= 0) &
                (coords[:, 1] < w1)
            )

            coords = coords[mask]

            if len(coords):
                self.exp_pixel_indices = coords[:, 0] * w1 + coords[:, 1]
                self.exp_led_indices = np.arange(len(coords), dtype=np.int32)
            else:
                self.exp_pixel_indices = None
                self.exp_led_indices = None

        else:
            self.exp_pixel_indices = None
            self.exp_led_indices = None

        # =========================
        # STREAM 2
        # =========================
        if mapping2:
            coords2 = np.array(mapping2, dtype=np.int32)

            mask2 = (
                (coords2[:, 0] >= 0) &
                (coords2[:, 0] < h2) &
                (coords2[:, 1] >= 0) &
                (coords2[:, 1] < w2)
            )

            coords2 = coords2[mask2]

            if len(coords2):
                self.exp_pixel_indices2 = coords2[:, 0] * w2 + coords2[:, 1]
                self.exp_led_indices2 = np.arange(len(coords2), dtype=np.int32)
            else:
                self.exp_pixel_indices2 = None
                self.exp_led_indices2 = None

        else:
            self.exp_pixel_indices2 = None
            self.exp_led_indices2 = None

        print(f"[OK] Devices S1: {len(self.device_slices)} | S2: {len(self.device_slices2)}")

        MASTER_MAPPING_DIRTY = False
        self.streaming_enabled = len(self.device_slices) > 0
        self.streaming2_enabled = len(self.device_slices2) > 0
    
    def apply_phys_lin_tonemap(self, tensor: np.ndarray, gamma: float,
                               gamma_enabled: bool, stream: int = 1) -> dict:
        """Apply physical linear tone mapping"""
        
        mode = self.hdr_tonemap_mode.get()
        
        if mode == "pq":
            mapped = self.apply_pq_curve(tensor, stream=stream)
        else:
            clip_nits = float(self.clip_nits.get())
            
            tensor_nits = tensor * 80.0
            tensor_nits = np.clip(tensor_nits, 0.0, clip_nits)
            
            normalized = tensor_nits / clip_nits
            
            if gamma_enabled:
                mapped = np.power(normalized, 1.0 / gamma)
            else:
                mapped = normalized
        
        out = np.clip(mapped, 0.0, 1.0).astype(np.float32)
        
        return {
            "wled": out,
            "preview": out
        }
    
    def capture_loop(self):
        """Frame capture stream"""
        if not self.bridge:
            print("[ERROR] Bridge not initialized!")
            return
        
        with self.dll_lock:
            ok = self.bridge.init_capture(
                self.monitor_index.get(),
                TARGET_W,
                TARGET_H
            )
        
        if not ok:
            print("Failed to initialize capture DLL")
            return
        
        self.frame_buffer = np.empty((TARGET_H, TARGET_W, 3), dtype=np.float32)
        self.frame_buffer.fill(0.0)
        
        self.frame_buffer2 = None
        self.last_frame2_valid = None
        
        self.last_frame_time = time.perf_counter()
        self.last_frame_id = -1
        
        self.last_nits_ok_time = time.perf_counter()
        self.nits_zero_start = None
        
        self.ddp2_last_ping = time.perf_counter()
        self.ddp2_ping_interval = 0.5
        
        while self.running:
            
            if not self.stream1_enabled and not self.stream2_enabled:
                time.sleep(0.01)
                continue
            
            capture_start = time.perf_counter()
            now = time.perf_counter()
            
            # STREAM 1
            with self.dll_lock:
                ok = self.bridge.capture_frame()
                
                if not ok:
                    ok_copy = False
                    frame_id = None
                else:
                    frame_id = self.bridge.get_frame_id()
                    
                    if frame_id == self.last_frame_id:
                        continue
                    
                    self.frame_buffer.fill(0.0)
                    
                    ok_copy = self.bridge.copy_frame(
                        self.frame_buffer.ctypes.data_as(
                            ctypes.POINTER(ctypes.c_float)
                        ),
                        self.frame_buffer.nbytes
                    )
            
            self.capture_delay_ms = (time.perf_counter() - capture_start) * 1000
            
            if not ok or not ok_copy:
                if now - self.last_frame_time > 10.0:
                    self.request_restart(full=False)
                continue
            
            self.last_frame_id = frame_id
            self.last_frame_time = now
            self.capture_count += 1
            
            # WATCHDOG
            avg_nits = self.avg_nits
            
            if avg_nits > 0.0:
                self.last_nits_ok_time = now
                self.nits_zero_start = None
            else:
                if self.nits_zero_start is None:
                    self.nits_zero_start = now
                elif now - self.nits_zero_start > 0.5:
                    self.request_restart(full=False)
                    self.nits_zero_start = None
                    continue
            
            frame_copy = self.frame_buffer.copy()
            
            tw = self.input_target_w.get()
            th = self.input_target_h.get()
            
            if tw > 0 and th > 0:
                h, w = frame_copy.shape[:2]
                if tw <= w and th <= h:
                    x1 = (w - tw) // 2
                    y1 = (h - th) // 2
                    frame_copy = frame_copy[y1:y1 + th, x1:x1 + tw]
            
            self.push_latest(self.capture_queue, frame_copy)
            
            # STREAM 2
            if self.stream2_enabled:
                
                if self.frame_buffer2 is None:
                    w2 = self.target2_w.get()
                    h2 = self.target2_h.get()
                    
                    if w2 > 0 and h2 > 0:
                        with self.dll_lock:
                            self.bridge.set_second_resolution(w2, h2)
                        
                        self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
                
                ok2 = False
                frame2 = None
                
                if self.frame_buffer2 is not None:
                    ok2 = self.bridge.copy_frame2(
                        self.frame_buffer2.ctypes.data_as(
                            ctypes.POINTER(ctypes.c_float)
                        ),
                        self.frame_buffer2.nbytes
                    )
                    
                    if ok2:
                        frame2 = self.frame_buffer2.copy()
                        self.last_frame2_valid = frame2.copy()
                    else:
                        if self.last_frame2_valid is not None:
                            frame2 = self.last_frame2_valid.copy()
                            ok2 = True
                    
                    if now - self.ddp2_last_ping > self.ddp2_ping_interval:
                        try:
                            ping_frame = (
                                self.last_frame2_valid
                                if self.last_frame2_valid is not None
                                else self.frame_buffer2 * 0.0
                            )
                            
                            self.bridge.copy_frame2(
                                ping_frame.ctypes.data_as(
                                    ctypes.POINTER(ctypes.c_float)
                                ),
                                ping_frame.nbytes
                            )
                        except:
                            pass
                        
                        self.ddp2_last_ping = now
                
                if ok2 and frame2 is not None:
                    
                    self.second_capture_count += 1
                    
                    tw2 = self.input_target2_w.get()
                    th2 = self.input_target2_h.get()
                    
                    if tw2 > 0 and th2 > 0:
                        h2, w2 = frame2.shape[:2]
                        if tw2 <= w2 and th2 <= h2:
                            x1 = (w2 - tw2) // 2
                            y1 = (h2 - th2) // 2
                            frame2 = frame2[y1:y1 + th2, x1:x1 + tw2]
                    
                    self.last_frame2_time = now
                    self.push_latest(self.capture2_queue, frame2)
                
                else:
                    if self.capture_paused or self.dll_restarting:
                        continue
                    
                    with self.dll_lock:
                        self.bridge.set_second_resolution(0, 0)
                    
                    self.frame_buffer2 = None
                    
                    try:
                        while True:
                            self.preview2_queue.get_nowait()
                    except Empty:
                        pass
            
            self.update_fps_counters()
    
    def apply_custom_gamma_to_tensor(self, tensor: np.ndarray, stream: int = 1) -> np.ndarray:
        """Apply custom gamma to tensor"""
        if stream == 1:
            gamma_mode = self.custom_gamma_rgb_mode1.get()
            values_r = self.custom_gamma_sdr_r1
            values_g = self.custom_gamma_sdr_g1
            values_b = self.custom_gamma_sdr_b1
        else:
            gamma_mode = self.custom_gamma_rgb_mode2.get()
            values_r = self.custom_gamma_sdr_r2
            values_g = self.custom_gamma_sdr_g2
            values_b = self.custom_gamma_sdr_b2
        
        if gamma_mode == "rgb":
            return apply_custom_gamma(tensor, values_r, gamma_mode="rgb")
        else:
            return apply_custom_gamma(
                tensor,
                None,
                gamma_mode="separate",
                gamma_sdr_r=values_r,
                gamma_sdr_g=values_g,
                gamma_sdr_b=values_b
            )
    
    def process_loop(self):
        """Process stream for Stream 1"""
        last_hdr_state = None
        
        while self.running:
            try:
                frame = self.capture_queue.get(timeout=1.0)
            except Empty:
                continue
            
            pipeline_start = time.perf_counter()
            
            tensor = frame
            
            # LUMA
            lum = (
                tensor[..., 0] * 0.2126 +
                tensor[..., 1] * 0.7152 +
                tensor[..., 2] * 0.0722
            )
            
            self.avg_nits = float(np.mean(lum) * 80.0)
            self.peak_nits = float(np.max(lum) * 80.0)
            
            with self.dll_lock:
                hdr_active = False
                if self.bridge:
                    hdr_active = self.bridge.is_hdr()
            
            self.hdr_active = hdr_active
            
            # Update UI each frame for dynamic display of avg/peak nits
            if hdr_active != last_hdr_state:
                self.root.after(0, self.update_mode_highlight, hdr_active)
                last_hdr_state = hdr_active
            else:
                # If mode not changed, update only text label with current values
                self.root.after(0, self.update_nits_labels)
            
            p = self.stream1_vars
            
            if hdr_active:
                brightness = p["brightness_hdr"].get() / 255.0
                gamma = p["gamma_hdr"].get()
                gamma_enabled = p["gamma_hdr_en"].get()
                sat_enabled = p["sat_hdr_en"].get()
                sat_strength = p["sat_hdr"].get()
            else:
                brightness = p["brightness_sdr"].get() / 255.0
                gamma = p["gamma_sdr"].get()
                gamma_enabled = p["gamma_sdr_en"].get()
                
                # Gamma mode unified for both streams - applied immediately to SDR and HDR
                gamma_mode = self.gamma_mode_sdr.get()
                
                sat_enabled = p["sat_sdr_en"].get()
                sat_strength = p["sat_sdr"].get()
            
            # TONEMAP
            if hdr_active and self.tonemap_enabled.get():
                result = self.apply_phys_lin_tonemap(
                    tensor,
                    gamma,
                    gamma_enabled,
                    stream=1
                )
                
                tensor_wled = result["wled"]
                tensor_wled = tensor_wled.astype(np.float32)
            
            else:
                tensor_wled = np.clip(tensor, 0.0, 1.0)
                
                # Apply gamma
                if gamma_mode == "custom":
                    # Custom gamma applied immediately to both streams (S1 and S2)
                    self.apply_custom_gamma_to_tensor(tensor_wled, stream=1)  # Apply for Stream 1
                    self.apply_custom_gamma_to_tensor(tensor_wled, stream=2)  # Apply for Stream 2
                    tensor_wled = self.apply_custom_gamma_to_tensor(tensor_wled, stream=1)  # Return for current stream
                elif gamma_enabled and gamma_mode == "stream":
                    # Stream gamma (standard) - applied to both streams immediately
                    tensor_wled = np.power(tensor_wled, 1.0 / gamma)
            
            # LUT
            if self.calibration1_enabled.get():
                tensor_wled = self.apply_led_calibration(tensor_wled)
            
            # SATURATION
            if sat_enabled:
                tensor_wled = self.apply_saturation(tensor_wled, sat_strength)
            
            # BRIGHTNESS
            tensor_wled *= brightness
            tensor_wled = np.clip(tensor_wled, 0.0, 1.0)
            
            # AMBI
            mode = self.ambi_mode1.get()
            if mode != "Matrix":
                if "3" in mode:
                    tensor_wled = self.apply_ambilight(tensor_wled, 0.03)
                elif "6" in mode:
                    tensor_wled = self.apply_ambilight(tensor_wled, 0.06)
                elif "9" in mode:
                    tensor_wled = self.apply_ambilight(tensor_wled, 0.09)
            
            # FINAL CONVERSION
            tensor_u8 = (tensor_wled * 255.0).astype(np.uint8)
            
            # DDP MAPPING
            if self.exp_pixel_indices is not None:
                flat = tensor_u8.reshape(-1, 3)
                
                if len(flat) > np.max(self.exp_pixel_indices):
                    pixels = flat[self.exp_pixel_indices][:, [2, 1, 0]]
                    out = pixels.reshape(-1).tobytes()
                    
                    try:
                        self.ddp_queue.put_nowait(out)
                    except Full:
                        pass
            
            self.push_latest(self.preview_queue, tensor_u8)
            
            self.pipeline_delay_ms = (time.perf_counter() - pipeline_start) * 1000
    
    def process2_loop(self):
        """Process stream for Stream 2"""
        last_hdr_state = None
        
        while self.running:
            try:
                frame = self.capture2_queue.get(timeout=1.0)
            except Empty:
                continue
            
            pipeline_start = time.perf_counter()
            
            tensor = frame
            
            # LUMA
            lum = (
                tensor[..., 0] * 0.2126 +
                tensor[..., 1] * 0.7152 +
                tensor[..., 2] * 0.0722
            )
            
            self.avg_nits = float(np.mean(lum) * 80.0)
            self.peak_nits = float(np.max(lum) * 80.0)
            
            with self.dll_lock:
                hdr_active = False
                if self.bridge:
                    hdr_active = self.bridge.is_hdr()
            
            # Update UI each frame for dynamic display of avg/peak nits
            if hdr_active != last_hdr_state:
                self.root.after(0, self.update_mode_highlight, hdr_active)
                last_hdr_state = hdr_active
            else:
                # If mode not changed, update only text label with current values
                self.root.after(0, self.update_nits_labels)
            
            p = self.stream2_vars
            
            if hdr_active:
                brightness = p["brightness_hdr"].get() / 255.0
                gamma = p["gamma_hdr"].get()
                gamma_enabled = p["gamma_hdr_en"].get()
                sat_enabled = p["sat_hdr_en"].get()
                sat_strength = p["sat_hdr"].get()
            else:
                brightness = p["brightness_sdr"].get() / 255.0
                gamma = p["gamma_sdr"].get()
                gamma_enabled = p["gamma_sdr_en"].get()
                
                # Gamma mode unified for both streams - applied immediately to SDR and HDR
                gamma_mode = self.gamma_mode_sdr.get()
                
                sat_enabled = p["sat_sdr_en"].get()
                sat_strength = p["sat_sdr"].get()
            
            # TONEMAP
            if hdr_active and self.tonemap_enabled.get():
                result = self.apply_phys_lin_tonemap(
                    tensor,
                    gamma,
                    gamma_enabled,
                    stream=2
                )
                tensor_wled = result.get("wled", tensor)
                tensor_wled = tensor_wled.astype(np.float32)
            
            else:
                tensor_wled = np.clip(tensor, 0.0, 1.0)
                
                # Apply gamma
                if gamma_mode == "custom":
                    # Custom gamma applied immediately to both streams (S1 and S2)
                    self.apply_custom_gamma_to_tensor(tensor_wled, stream=1)  # Apply for Stream 1
                    self.apply_custom_gamma_to_tensor(tensor_wled, stream=2)  # Apply for Stream 2
                    tensor_wled = self.apply_custom_gamma_to_tensor(tensor_wled, stream=2)  # Return for current stream
                elif gamma_enabled and gamma_mode == "stream":
                    # Stream gamma (standard) - applied to both streams immediately
                    tensor_wled = np.power(tensor_wled, 1.0 / gamma)
            
            # LUT
            if self.calibration2_enabled.get():
                tensor_wled = self.apply_led_calibration2(tensor_wled)
            
            # SATURATION
            if sat_enabled:
                tensor_wled = self.apply_saturation(tensor_wled, sat_strength)
            
            # BRIGHTNESS
            tensor_wled *= brightness
            tensor_wled = np.clip(tensor_wled, 0.0, 1.0)
            
            # AMBILIGHT
            mode = self.ambi_mode2.get()
            if mode != "Matrix":
                if "3" in mode:
                    tensor_wled = self.apply_ambilight(tensor_wled, 0.03)
                elif "6" in mode:
                    tensor_wled = self.apply_ambilight(tensor_wled, 0.06)
                elif "9" in mode:
                    tensor_wled = self.apply_ambilight(tensor_wled, 0.09)
            
            # FINAL CONVERSION
            tensor_u8 = (tensor_wled * 255.0).astype(np.uint8)
            
            # DDP MAPPING
            idx = self.exp_pixel_indices2
            
            if idx is not None:
                flat = tensor_u8.reshape(-1, 3)
                
                if np.max(idx) < flat.shape[0]:
                    pixels = flat[idx][:, [2, 1, 0]]
                    out = pixels.ravel().tobytes()
                    
                    try:
                        self.ddp2_queue.put_nowait(out)
                    except Full:
                        pass
            
            # PREVIEW
            self.push_latest(self.preview2_queue, tensor_u8)
            
            self.pipeline_delay_ms = (time.perf_counter() - pipeline_start) * 1000
    
    def on_monitor_change(self, event=None):
        """Monitor change handler"""
        self.monitor_index.set(self.monitor_combo.current())
        
        w, h = self.recalc_resolution_for_current_state()
        w2, h2 = self.recalc_resolution_stream2()
        
        self.capture_paused = True
        time.sleep(0.05)
        
        with self.dll_lock:
            try:
                if self.bridge:
                    self.bridge.shutdown_capture()
            except:
                pass
            
            # Init primary stream
            ok = False
            if self.bridge:
                ok = self.bridge.init_capture(self.monitor_index.get(), w, h)
            
            # Init secondary stream
            if ok and self.second_stream_enabled.get():
                try:
                    if self.bridge:
                        self.bridge.set_second_resolution(w2, h2)
                except:
                    pass
        
        # Buffer 1
        self.frame_buffer = np.empty((h, w, 3), dtype=np.float32)
        self.frame_buffer.fill(0.0)
        
        self.last_frame_id = -1
        self.last_frame_time = time.perf_counter()
        
        # Buffer 2
        if self.second_stream_enabled.get():
            self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
        else:
            self.frame_buffer2 = None
        
        self.last_frame2_id = -1
        self.last_frame2_time = time.perf_counter()
        
        # Sync UI state
        self.target2_w.set(w2)
        self.target2_h.set(h2)
        
        self.capture_paused = False
    
    def refresh_monitors(self, event=None):
        """Refresh monitor list"""
        current_index = self.monitor_combo.current()
        
        self.monitors = get_monitors_info()
        
        self.monitor_list = [
            f"{i}: {m['name']} ({m['width']}x{m['height']})"
            for i, m in enumerate(self.monitors)
        ]
        
        self.monitor_combo["values"] = self.monitor_list
        
        if current_index < len(self.monitor_list):
            self.monitor_combo.current(current_index)
        else:
            self.monitor_combo.current(0)
        
        print("[INFO] Monitor list refreshed")
    
    def toggle_preview(self):
        """Toggle Stream 1 preview"""
        self.preview_enabled = not self.preview_enabled
    
    def update_fps_counters(self):
        """Update FPS counters"""
        now = time.perf_counter()
        
        if now - self.last_fps_time >= 1.0:
            self.capture_fps_real = self.capture_count
            self.scale_fps_real = self.scale_count
            self.preview_fps_real = self.preview_count
            self.ddp_fps_real = self.ddp_frame_count
            self.capture_count = 0
            self.scale_count = 0
            self.preview_count = 0
            self.ddp_frame_count = 0
            self.last_fps_time = now
            
            self.second_fps_real = self.second_capture_count
            self.preview2_fps_real = self.preview2_count
            self.second_capture_count = 0
            self.preview2_count = 0
    
    def save_config_default(self):
        """Save default config to app_config.json"""
        settings = self.get_all_settings()
        success = save_settings_to_file(settings, CONFIG_FILE_PATH)
        if success:
            print("[OK] Default config saved successfully")
    
    def load_config_default(self):
        """Load default config from app_config.json"""
        settings = load_settings_from_file(CONFIG_FILE_PATH)
        self.apply_settings(settings)
        print("[OK] Default config loaded successfully")
    
    def save_config_as(self):
        """Save config with file selection (topmost priority)"""
        filepath = open_file_dialog(
            self.root,
            "save",
            title="Save Config As",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialfile="config.json"
        )
        if not filepath:
            return
        settings = self.get_all_settings()
        success = save_settings_json(settings, filepath)
        if success:
            print(f"[OK] Config saved to: {filepath}")
    
    def load_config_from(self):
        """Load config with file selection (topmost priority)"""
        filepath = open_file_dialog(
            self.root,
            "open",
            title="Load Config From",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not filepath:
            return
        settings = load_settings_json(filepath)
        if settings:
            self.apply_settings(settings)
            print(f"[OK] Config loaded from: {filepath}")
    
    def check_for_updates(self):
        """Open GitHub releases page in browser to check for updates"""
        import webbrowser
        github_url = "https://github.com/SpectrCode/Spectr-aLED"
        try:
            webbrowser.open(github_url)
            print(f"[INFO] Opening GitHub: {github_url}")
        except Exception as e:
            print(f"[ERROR] Failed to open browser: {e}")
    
    def map_mapping_button(self):
        """Open mapping window from external maping.py file"""
        print("[INFO] Opening mapping window...")
        try:
            # Check if window is already open
            if hasattr(self, 'mapping_window') and self.mapping_window is not None:
                try:
                    # If window exists and still active - lift it up
                    if self.mapping_window.winfo_exists():
                        self.mapping_window.lift()
                        self.mapping_window.focus_force()
                        return
                    else:
                        # Window closed, reset reference
                        self.mapping_window = None
                except:
                    self.mapping_window = None
            
            win = open_mapping_window(self.root)
            self.mapping_window = win
        except Exception as e:
            print(f"[ERROR] Failed to open mapping window: {e}")
    
    def _connect_wled_device(self, dev: dict) -> bool:
        """
        Connect to WLED device and apply mapping if exists.
        Returns True on success, False otherwise.
        
        If device has mapping - switch to DDP mode (live: True).
        If no mapping - just connect without switching to DDP mode yet.
        """
        try:
            ip = dev.get("ip")
            if not ip:
                return False
            
            # Check if device is online and set status
            is_online = self.ping_wled_device(ip)
            dev["online"] = is_online
            
            # Check if device has mapping
            mapping = dev.get("mapping")
            
            if mapping and len(mapping) > 0:
                # Device has mapping - switch to DDP mode (live: True)
                set_wled_ddp_mode(ip, keep_last_frame=True)
                dev["length"] = len(mapping)
                status_str = "Online" if is_online else "Offline"
                print(f"[OK] WLED {ip} connected ({status_str}) with {len(mapping)} LEDs from saved mapping (DDP mode ON)")
            else:
                # No mapping - just connect without DDP mode
                # Keep WLED in normal mode (live: False) until mapping is loaded
                dev["length"] = 0
                
                status_str = "Online" if is_online else "Offline"
                
                # Try to get LED count from device info for future use
                try:
                    led_count = self.get_wled_led_count(ip)
                    print(f"[OK] WLED {ip} connected ({status_str}) (no mapping, ready for mapping)")
                except Exception as e:
                    print(f"[INFO] WLED {ip} connected ({status_str}) (no mapping) - cannot read LED count: {e}")
            
            return True
            
        except Exception as e:
            dev["online"] = False
            print(f"[ERROR] Failed to connect to WLED {dev.get('ip')}: {e}")
            return False
    
    def _wled_ping_loop(self):
        """
        Background thread that periodically pings WLED devices and updates their online status.
        Runs every 10 seconds for all devices to ensure accurate status.
        """
        while self.running and not self._wled_ping_stop_event.is_set():
            try:
                # Ping ALL devices to check their current status
                for dev in WLED_DEVICES:
                    ip = dev.get("ip")
                    if ip:
                        is_online = self.ping_wled_device(ip)
                        dev["online"] = is_online
                
                # Update UI with new status
                self.root.after(0, self.update_wled_list)
                
            except Exception as e:
                print(f"[ERROR] WLED ping loop error: {e}")
            
            # Wait 10 seconds before next check
            try:
                self._wled_ping_stop_event.wait(timeout=10)
            except:
                break
    
    def start_wled_ping_thread(self):
        """Start WLED device ping monitoring thread"""
        if not self._wled_ping_thread_running:
            self._wled_ping_thread_running = True
            self._wled_ping_stop_event.clear()
            
            threading.Thread(
                target=self._wled_ping_loop,
                daemon=True
            ).start()
            print("[OK] WLED ping monitoring thread started (10s interval)")

    def get_all_settings(self):
        """Get all current settings as dictionary"""
        # Stream 1 SDR/HDR vars
        stream1_vars = {
            "brightness_sdr": self.stream1_vars["brightness_sdr"].get(),
            "gamma_sdr": self.stream1_vars["gamma_sdr"].get(),
            "gamma_sdr_en": self.stream1_vars["gamma_sdr_en"].get(),
            "sat_sdr_en": self.stream1_vars["sat_sdr_en"].get(),
            "sat_sdr": self.stream1_vars["sat_sdr"].get(),
            "brightness_hdr": self.stream1_vars["brightness_hdr"].get(),
            "gamma_hdr": self.stream1_vars["gamma_hdr"].get(),
            "gamma_hdr_en": self.stream1_vars["gamma_hdr_en"].get(),
            "sat_hdr_en": self.stream1_vars["sat_hdr_en"].get(),
            "sat_hdr": self.stream1_vars["sat_hdr"].get(),
        }
        
        # Stream 2 SDR/HDR vars
        stream2_vars = {
            "brightness_sdr": self.stream2_vars["brightness_sdr"].get(),
            "gamma_sdr": self.stream2_vars["gamma_sdr"].get(),
            "gamma_sdr_en": self.stream2_vars["gamma_sdr_en"].get(),
            "sat_sdr_en": self.stream2_vars["sat_sdr_en"].get(),
            "sat_sdr": self.stream2_vars["sat_sdr"].get(),
            "brightness_hdr": self.stream2_vars["brightness_hdr"].get(),
            "gamma_hdr": self.stream2_vars["gamma_hdr"].get(),
            "gamma_hdr_en": self.stream2_vars["gamma_hdr_en"].get(),
            "sat_hdr_en": self.stream2_vars["sat_hdr_en"].get(),
            "sat_hdr": self.stream2_vars["sat_hdr"].get(),
        }
        
        return {
            # Capture settings
            "monitor_index": self.monitor_index.get(),
            "input_target_w": self.input_target_w.get(),
            "input_target_h": self.input_target_h.get(),
            "aspect1": self.aspect1.get(),
            
            # Stream 2 capture settings
            "input_target2_w": self.input_target2_w.get(),
            "input_target2_h": self.input_target2_h.get(),
            "aspect2": self.aspect2.get(),
            
            # Active stream selector
            "active_stream": self.active_stream.get(),
            
            # Gamma mode (stream or custom)
            "gamma_mode_sdr": self.gamma_mode_sdr.get(),
            
            # Stream 1 SDR/HDR settings
            "stream1_vars": stream1_vars,
            
            # Stream 2 SDR/HDR settings
            "stream2_vars": stream2_vars,
            
            # General settings (synced with active stream)
            "sdr_brightness": self.sdr_brightness.get(),
            "sdr_gamma": self.sdr_gamma.get(),
            "hdr_brightness": self.hdr_brightness.get(),
            "hdr_gamma": self.hdr_gamma.get(),
            "sdr_gamma_enabled": self.sdr_gamma_enabled.get(),
            "hdr_gamma_enabled": self.hdr_gamma_enabled.get(),
            "sdr_saturation_enabled": self.sdr_saturation_enabled.get(),
            "hdr_saturation_enabled": self.hdr_saturation_enabled.get(),
            "sdr_saturation": self.sdr_saturation.get(),
            "hdr_saturation": self.hdr_saturation.get(),
            
            # HDR settings
            "tonemap_enabled": self.tonemap_enabled.get(),
            "hdr_tonemap_mode": self.hdr_tonemap_mode.get(),
            "clip_nits": self.clip_nits.get(),
            
            # Calibration
            "lut_size1": self.lut_size1.get(),
            "lut_size2": self.lut_size2.get(),
            "calibration1_enabled": self.calibration1_enabled.get(),
            "calibration2_enabled": self.calibration2_enabled.get(),
            
            # Stream enable states (saved separately from UI variables)
            "first_stream_enabled": self.first_stream_enabled.get(),
            "second_stream_enabled": self.second_stream_enabled.get(),
            
            # Calibrations (convert to dict with lists)
            "global_calibration": {
                k: [float(x) for x in v] 
                for k, v in self.global_calibration.items()
            },
            "global_calibration2": {
                k: [float(x) for x in v] 
                for k, v in self.global_calibration2.items()
            },
            
            # Ambi modes
            "ambi_mode1": self.ambi_mode1.get(),
            "ambi_mode2": self.ambi_mode2.get(),
            
            # PQ Curve settings - for compatibility (using Stream 1 defaults)
            "pq_curve_strength": self.pq_curve_strength1.get(),
            "pq_curve_bias": self.pq_curve_bias1.get(),
            "pq_rgb_mode1": self.pq_rgb_mode1.get(),
            "pq_rgb_mode2": self.pq_rgb_mode2.get(),
            
            # Compute target removed - no longer using torch
            
            # External LUT enabled flag
            "external_lut_enabled": self.external_lut_enabled.get(),
            
            # External LUT file paths (for auto-load on config load)
            "external_lut_sdr_1_path": getattr(self, 'external_lut_sdr_1_path', ''),
            "external_lut_hdr_1_path": getattr(self, 'external_lut_hdr_1_path', ''),
            "external_lut_sdr_2_path": getattr(self, 'external_lut_sdr_2_path', ''),
            "external_lut_hdr_2_path": getattr(self, 'external_lut_hdr_2_path', ''),
            
            # PQ curve values (RGB) - Stream 1
            "pq_values_r1": [float(x) for x in self.pq_values_r1],
            "pq_values_g1": [float(x) for x in self.pq_values_g1],
            "pq_values_b1": [float(x) for x in self.pq_values_b1],
            
            # PQ curve values (RGB) - Stream 2
            "pq_values_r2": [float(x) for x in self.pq_values_r2],
            "pq_values_g2": [float(x) for x in self.pq_values_g2],
            "pq_values_b2": [float(x) for x in self.pq_values_b2],
            
            # Saved custom gamma values (Stream 1)
            "saved_custom_gamma_sdr_r1": [float(x) for x in self.saved_custom_gamma_sdr_r1],
            "saved_custom_gamma_sdr_g1": [float(x) for x in self.saved_custom_gamma_sdr_g1],
            "saved_custom_gamma_sdr_b1": [float(x) for x in self.saved_custom_gamma_sdr_b1],
            
            # Saved custom gamma values (Stream 2)
            "saved_custom_gamma_sdr_r2": [float(x) for x in self.saved_custom_gamma_sdr_r2],
            "saved_custom_gamma_sdr_g2": [float(x) for x in self.saved_custom_gamma_sdr_g2],
            "saved_custom_gamma_sdr_b2": [float(x) for x in self.saved_custom_gamma_sdr_b2],
            
            # Saved custom gamma curve/bias/enabled values (Stream 1)
            "saved_curve_strength1": self.saved_curve_strength1.get(),
            "saved_bias1": self.saved_bias1.get(),
            "saved_custom_gamma_enabled1": self.saved_custom_gamma_enabled1.get(),
            
            # Saved custom gamma curve/bias/enabled values (Stream 2)
            "saved_curve_strength2": self.saved_curve_strength2.get(),
            "saved_bias2": self.saved_bias2.get(),
            "saved_custom_gamma_enabled2": self.saved_custom_gamma_enabled2.get(),
            
            # WLED devices with mappings and connection state
            "wled_devices": [
                {
                    "ip": dev["ip"],
                    "name": dev.get("name", "WLED"),
                    "mapping": [list(coord) for coord in dev.get("mapping", [])] if dev.get("mapping") else None,
                    "stream": dev.get("stream", 1),
                    "connected": True  # Mark device as connected for auto-reconnect on load
                }
                for dev in WLED_DEVICES
            ],
        }
    
    def apply_settings(self, settings: dict):
        """Apply settings from dictionary"""
        try:
            # Capture settings
            if "monitor_index" in settings:
                self.monitor_index.set(int(settings["monitor_index"]))
            if "input_target_w" in settings:
                self.input_target_w.set(int(settings["input_target_w"]))
            if "input_target_h" in settings:
                self.input_target_h.set(int(settings["input_target_h"]))
            if "aspect1" in settings:
                self.aspect1.set(str(settings["aspect1"]))
            
            # Saved custom gamma values (Stream 1) - load FIRST so they're available when mode changes
            if "saved_custom_gamma_sdr_r1" in settings and len(settings["saved_custom_gamma_sdr_r1"]) == 64:
                self.saved_custom_gamma_sdr_r1 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_r1"]], dtype=np.float32)
            if "saved_custom_gamma_sdr_g1" in settings and len(settings["saved_custom_gamma_sdr_g1"]) == 64:
                self.saved_custom_gamma_sdr_g1 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_g1"]], dtype=np.float32)
            if "saved_custom_gamma_sdr_b1" in settings and len(settings["saved_custom_gamma_sdr_b1"]) == 64:
                self.saved_custom_gamma_sdr_b1 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_b1"]], dtype=np.float32)
            
            if "saved_custom_gamma_sdr_r2" in settings and len(settings["saved_custom_gamma_sdr_r2"]) == 64:
                self.saved_custom_gamma_sdr_r2 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_r2"]], dtype=np.float32)
            if "saved_custom_gamma_sdr_g2" in settings and len(settings["saved_custom_gamma_sdr_g2"]) == 64:
                self.saved_custom_gamma_sdr_g2 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_g2"]], dtype=np.float32)
            if "saved_custom_gamma_sdr_b2" in settings and len(settings["saved_custom_gamma_sdr_b2"]) == 64:
                self.saved_custom_gamma_sdr_b2 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_b2"]], dtype=np.float32)
            
            # Apply saved custom gamma values to current (use 64 slider values as source of truth)
            # IMPORTANT: Use in-place assignment with [:] to preserve array references for slider callbacks
            if len(self.saved_custom_gamma_sdr_r1) == 64:
                self.custom_gamma_sdr_r1[:] = self.saved_custom_gamma_sdr_r1[:64]
                self.custom_gamma_sdr_g1[:] = self.saved_custom_gamma_sdr_g1[:64]
                self.custom_gamma_sdr_b1[:] = self.saved_custom_gamma_sdr_b1[:64]
            
            if len(self.saved_custom_gamma_sdr_r2) == 64:
                self.custom_gamma_sdr_r2[:] = self.saved_custom_gamma_sdr_r2[:64]
                self.custom_gamma_sdr_g2[:] = self.saved_custom_gamma_sdr_g2[:64]
                self.custom_gamma_sdr_b2[:] = self.saved_custom_gamma_sdr_b2[:64]
            
            # Gamma mode - set after loading saved gamma values so on_gamma_mode_change has access to them
            if "gamma_mode_sdr" in settings:
                gamma_mode = str(settings["gamma_mode_sdr"])
                self.gamma_mode_sdr.set(gamma_mode)
                # Вызываем обработчик изменения режима для обновления состояния кнопок в реальном времени
                # This will now have access to the saved custom gamma values
                self.on_gamma_mode_change()
            
            # Stream 2 capture settings
            if "input_target2_w" in settings:
                self.input_target2_w.set(int(settings["input_target2_w"]))
            if "input_target2_h" in settings:
                self.input_target2_h.set(int(settings["input_target2_h"]))
            if "aspect2" in settings:
                self.aspect2.set(str(settings["aspect2"]))
            
            # Active stream
            if "active_stream" in settings:
                self.active_stream.set(int(settings["active_stream"]))
            
            # Stream 1 SDR/HDR settings
            if "stream1_vars" in settings:
                sv = settings["stream1_vars"]
                if isinstance(sv, dict):
                    self.stream1_vars["brightness_sdr"].set(int(sv.get("brightness_sdr", 127)))
                    self.stream1_vars["gamma_sdr"].set(float(sv.get("gamma_sdr", 0.8)))
                    self.stream1_vars["gamma_sdr_en"].set(bool(sv.get("gamma_sdr_en", True)))
                    self.stream1_vars["sat_sdr_en"].set(bool(sv.get("sat_sdr_en", False)))
                    self.stream1_vars["sat_sdr"].set(float(sv.get("sat_sdr", 1.0)))
                    self.stream1_vars["brightness_hdr"].set(int(sv.get("brightness_hdr", 255)))
                    self.stream1_vars["gamma_hdr"].set(float(sv.get("gamma_hdr", 1.8)))
                    self.stream1_vars["gamma_hdr_en"].set(bool(sv.get("gamma_hdr_en", True)))
                    self.stream1_vars["sat_hdr_en"].set(bool(sv.get("sat_hdr_en", False)))
                    self.stream1_vars["sat_hdr"].set(float(sv.get("sat_hdr", 1.0)))
            
            # Stream 2 SDR/HDR settings
            if "stream2_vars" in settings:
                sv = settings["stream2_vars"]
                if isinstance(sv, dict):
                    self.stream2_vars["brightness_sdr"].set(int(sv.get("brightness_sdr", 127)))
                    self.stream2_vars["gamma_sdr"].set(float(sv.get("gamma_sdr", 0.8)))
                    self.stream2_vars["gamma_sdr_en"].set(bool(sv.get("gamma_sdr_en", True)))
                    self.stream2_vars["sat_sdr_en"].set(bool(sv.get("sat_sdr_en", False)))
                    self.stream2_vars["sat_sdr"].set(float(sv.get("sat_sdr", 1.0)))
                    self.stream2_vars["brightness_hdr"].set(int(sv.get("brightness_hdr", 255)))
                    self.stream2_vars["gamma_hdr"].set(float(sv.get("gamma_hdr", 1.8)))
                    self.stream2_vars["gamma_hdr_en"].set(bool(sv.get("gamma_hdr_en", True)))
                    self.stream2_vars["sat_hdr_en"].set(bool(sv.get("sat_hdr_en", False)))
                    self.stream2_vars["sat_hdr"].set(float(sv.get("sat_hdr", 1.0)))
            
            # General settings
            if "sdr_brightness" in settings:
                self.sdr_brightness.set(int(settings["sdr_brightness"]))
            if "sdr_gamma" in settings:
                self.sdr_gamma.set(float(settings["sdr_gamma"]))
            if "hdr_brightness" in settings:
                self.hdr_brightness.set(int(settings["hdr_brightness"]))
            if "hdr_gamma" in settings:
                self.hdr_gamma.set(float(settings["hdr_gamma"]))
            if "sdr_gamma_enabled" in settings:
                self.sdr_gamma_enabled.set(bool(settings["sdr_gamma_enabled"]))
            if "hdr_gamma_enabled" in settings:
                self.hdr_gamma_enabled.set(bool(settings["hdr_gamma_enabled"]))
            if "sdr_saturation_enabled" in settings:
                self.sdr_saturation_enabled.set(bool(settings["sdr_saturation_enabled"]))
            if "hdr_saturation_enabled" in settings:
                self.hdr_saturation_enabled.set(bool(settings["hdr_saturation_enabled"]))
            if "sdr_saturation" in settings:
                self.sdr_saturation.set(float(settings["sdr_saturation"]))
            if "hdr_saturation" in settings:
                self.hdr_saturation.set(float(settings["hdr_saturation"]))
            
            # HDR settings
            if "tonemap_enabled" in settings:
                self.tonemap_enabled.set(bool(settings["tonemap_enabled"]))
            if "hdr_tonemap_mode" in settings:
                self.hdr_tonemap_mode.set(str(settings["hdr_tonemap_mode"]))
            if "clip_nits" in settings:
                self.clip_nits.set(int(settings["clip_nits"]))
            
            # Calibration
            if "lut_size1" in settings:
                self.lut_size1.set(int(settings["lut_size1"]))
            if "lut_size2" in settings:
                self.lut_size2.set(int(settings["lut_size2"]))
            if "calibration1_enabled" in settings:
                self.calibration1_enabled.set(bool(settings["calibration1_enabled"]))
            if "calibration2_enabled" in settings:
                self.calibration2_enabled.set(bool(settings["calibration2_enabled"]))
            
            # Stream enable states (from saved config)
            if "first_stream_enabled" in settings:
                first_stream_state = bool(settings["first_stream_enabled"])
                self.first_stream_enabled.set(first_stream_state)
                self.stream1_enabled = first_stream_state
                # Call toggle handler to apply changes immediately (disable stream if needed)
                self.on_first_stream_toggle()
            if "second_stream_enabled" in settings:
                self.second_stream_enabled.set(bool(settings["second_stream_enabled"]))
                # Call toggle handler to apply changes immediately (disable stream if needed)
                self.on_second_stream_toggle()
            
            # Calibrations - async rebuild
            if "global_calibration" in settings:
                calib = settings["global_calibration"]
                for key in self.global_calibration.keys():
                    if key in calib and isinstance(calib[key], list) and len(calib[key]) >= 3:
                        self.global_calibration[key] = [float(x) for x in calib[key][:3]]
                # Rebuild LUT asynchronously
                from image_processor import generate_3d_lut_async as gen_lut_async
                gen_lut_async(
                    self.global_calibration,
                    size=self.lut_size1.get(),
                    callback=lambda lut: setattr(self, 'global_lut', lut)
                )
            
            if "global_calibration2" in settings:
                calib = settings["global_calibration2"]
                for key in self.global_calibration2.keys():
                    if key in calib and isinstance(calib[key], list) and len(calib[key]) >= 3:
                        self.global_calibration2[key] = [float(x) for x in calib[key][:3]]
                # Rebuild LUT asynchronously
                from image_processor import generate_3d_lut_async as gen_lut_async
                gen_lut_async(
                    self.global_calibration2,
                    size=self.lut_size2.get(),
                    callback=lambda lut: setattr(self, 'global_lut2', lut)
                )
            
            # Ambi modes
            if "ambi_mode1" in settings:
                self.ambi_mode1.set(str(settings["ambi_mode1"]))
            if "ambi_mode2" in settings:
                self.ambi_mode2.set(str(settings["ambi_mode2"]))
            
            # PQ Curve settings - load to Stream 1 as default, then rebuild both
            if "pq_curve_strength" in settings:
                strength_val = float(settings["pq_curve_strength"])
                self.pq_curve_strength1.set(strength_val)
                self.pq_curve_strength2.set(strength_val)  # Also set for Stream 2
            if "pq_curve_bias" in settings:
                bias_val = float(settings["pq_curve_bias"])
                self.pq_curve_bias1.set(bias_val)
                self.pq_curve_bias2.set(bias_val)  # Also set for Stream 2
            if "pq_rgb_mode1" in settings:
                self.pq_rgb_mode1.set(str(settings["pq_rgb_mode1"]))
            if "pq_rgb_mode2" in settings:
                self.pq_rgb_mode2.set(str(settings["pq_rgb_mode2"]))
            
            # Compute target removed - no longer using torch
            
            # External LUT enabled flag
            if "external_lut_enabled" in settings:
                self.external_lut_enabled.set(bool(settings["external_lut_enabled"]))
            
            # Load external LUT files from saved paths (if file exists)
            lut_paths = [
                ("external_lut_sdr_1_path", "external_lut_sdr_1", 1, "SDR"),
                ("external_lut_hdr_1_path", "external_lut_hdr_1", 1, "HDR"),
                ("external_lut_sdr_2_path", "external_lut_sdr_2", 2, "SDR"),
                ("external_lut_hdr_2_path", "external_lut_hdr_2", 2, "HDR"),
            ]
            
            for path_key, lut_attr, stream, mode in lut_paths:
                if path_key in settings and settings[path_key]:
                    path = settings[path_key]
                    if os.path.exists(path):
                        try:
                            print(f"[INFO] Auto-loading LUT: S{stream} {mode} from {path}")
                            if path.endswith(".npy"):
                                lut = np.load(path)
                                lut = lut[..., ::-1]  # RGB to BGR
                            elif path.endswith(".cube"):
                                lut = self.load_cube_lut(path)
                            else:
                                continue  # Skip unsupported formats
                            
                            setattr(self, lut_attr, lut)
                            print(f"[OK] LUT loaded automatically: S{stream} {mode}")
                        except Exception as e:
                            print(f"[WARN] Failed to auto-load LUT S{stream} {mode}: {e}")
            
            # PQ curve values (RGB) - Stream 1
            if "pq_values_r1" in settings and len(settings["pq_values_r1"]) == PQ_POINTS:
                self.pq_values_r1 = np.array([float(x) for x in settings["pq_values_r1"]], dtype=np.float32)
            if "pq_values_g1" in settings and len(settings["pq_values_g1"]) == PQ_POINTS:
                self.pq_values_g1 = np.array([float(x) for x in settings["pq_values_g1"]], dtype=np.float32)
            if "pq_values_b1" in settings and len(settings["pq_values_b1"]) == PQ_POINTS:
                self.pq_values_b1 = np.array([float(x) for x in settings["pq_values_b1"]], dtype=np.float32)
            
            # PQ curve values (RGB) - Stream 2
            if "pq_values_r2" in settings and len(settings["pq_values_r2"]) == PQ_POINTS:
                self.pq_values_r2 = np.array([float(x) for x in settings["pq_values_r2"]], dtype=np.float32)
            if "pq_values_g2" in settings and len(settings["pq_values_g2"]) == PQ_POINTS:
                self.pq_values_g2 = np.array([float(x) for x in settings["pq_values_g2"]], dtype=np.float32)
            if "pq_values_b2" in settings and len(settings["pq_values_b2"]) == PQ_POINTS:
                self.pq_values_b2 = np.array([float(x) for x in settings["pq_values_b2"]], dtype=np.float32)
            
            # Saved custom gamma curve/bias/enabled values (Stream 1)
            if "saved_curve_strength1" in settings:
                self.saved_curve_strength1.set(float(settings["saved_curve_strength1"]))
            if "saved_bias1" in settings:
                self.saved_bias1.set(float(settings["saved_bias1"]))
            if "saved_custom_gamma_enabled1" in settings:
                self.saved_custom_gamma_enabled1.set(bool(settings["saved_custom_gamma_enabled1"]))
            
            # Saved custom gamma curve/bias/enabled values (Stream 2)
            if "saved_curve_strength2" in settings:
                self.saved_curve_strength2.set(float(settings["saved_curve_strength2"]))
            if "saved_bias2" in settings:
                self.saved_bias2.set(float(settings["saved_bias2"]))
            if "saved_custom_gamma_enabled2" in settings:
                self.saved_custom_gamma_enabled2.set(bool(settings["saved_custom_gamma_enabled2"]))
            
            # WLED devices with mappings and auto-reconnect
            if "wled_devices" in settings:
                global WLED_DEVICES, MASTER_MAPPING_DIRTY
                
                devices_to_reconnect = []
                
                WLED_DEVICES.clear()
                for dev_settings in settings["wled_devices"]:
                    mapping = None
                    if dev_settings.get("mapping"):
                        # Convert list of lists back to list of tuples
                        mapping = [tuple(coord) for coord in dev_settings["mapping"]]
                    
                    device_info = {
                        "ip": dev_settings["ip"],
                        "name": dev_settings.get("name", "WLED"),
                        "mapping": mapping,
                        "offset": 0,
                        "length": len(mapping) if mapping else 0,
                        "stream": dev_settings.get("stream", 1),
                        "online": False  # Default status, will be updated by ping loop
                    }
                    
                    # Check if device should be auto-reconnected (connected=True or not present for backward compatibility)
                    should_reconnect = dev_settings.get("connected", True)
                    
                    if should_reconnect:
                        devices_to_reconnect.append(device_info)
                        print(f"[INFO] Queueing WLED {dev_settings['ip']} for auto-reconnect")
                    
                    WLED_DEVICES.append(device_info)
                
                MASTER_MAPPING_DIRTY = True
                print(f"[OK] Loaded {len(WLED_DEVICES)} WLED devices from config")
                
                # Auto-connect to queued devices and update their length if mapping exists
                if devices_to_reconnect:
                    print(f"[INFO] Auto-connecting to {len(devices_to_reconnect)} WLED device(s)...")
                    for dev in devices_to_reconnect:
                        self._connect_wled_device(dev)
                
                # Update UI list after loading devices
                self.update_wled_list()
                self.rebuild_master_mapping()
                
                # Start WLED ping monitoring thread for offline devices (10s interval)
                print("[INFO] Starting WLED ping monitoring...")
                self.start_wled_ping_thread()
            
            # Rebuild PQ curve
            self.rebuild_pq_curve()
            
            # Sync UI
            self.sync_ui_from_stream()
            
        except Exception as e:
            print(f"[ERROR] Failed to apply settings: {e}")
    
    def start_threads(self):
        """Start processing threads after creating mainloop"""
        print("[INFO] Starting processing threads...")
        
        # Start WLED ping monitoring thread for offline devices (10s interval)
        self.start_wled_ping_thread()
        
        if self.bridge:
            threading.Thread(target=self.capture_loop, daemon=True).start()
        threading.Thread(target=self.process_loop, daemon=True).start()
        threading.Thread(target=self.ddp_send_loop, daemon=True).start()
        threading.Thread(target=preview_s1_loop, args=(self,), daemon=True).start()
        
        # Stream 2 threads
        threading.Thread(target=preview_s2_loop, args=(self,), daemon=True).start()
        if self.bridge:
            threading.Thread(target=self.process2_loop, daemon=True).start()
        threading.Thread(target=self.ddp2_send_loop, daemon=True).start()
    
    def copy_wallet_address(self):
        """Copy USDT TRC20 wallet address to clipboard"""
        try:
            # Clear clipboard and append wallet address
            self.root.clipboard_clear()
            self.root.clipboard_append(self.usdt_wallet_address)
            print(f"[INFO] Wallet address copied: {self.usdt_wallet_address}")
            
            # Show temporary notification on the copy button itself
            original_text = self.copy_btn.cget("text")
            self.copy_btn.config(text="Copied!", state="disabled")
            
            # Reset after 2 seconds
            def reset_button():
                self.copy_btn.config(text=original_text, state="normal")
            
            self.root.after(2000, reset_button)
        except Exception as e:
            print(f"[ERROR] Failed to copy wallet address: {e}")

    def update_gui_fps(self):
        """Update FPS display"""
        if self.stream2_enabled:
            second_text = (
                f"Stream2 FPS: {self.second_fps_real}\n"
                f"Preview2 FPS: {self.preview2_fps_real}\n"
            )
        else:
            second_text = "Stream2: OFF\n"
        
        info_text = (
            f"Capture: {self.capture_fps_real} fps\n"
            f"Preview: {self.preview_fps_real} fps\n"
            f"DDP: {self.ddp_fps_real} frames/s\n\n"
            + second_text +
            f"\nCapture: {self.capture_delay_ms:.2f} ms\n"
            f"DDP: {self.ddp_delay_ms:.2f} ms\n"
            f"Preview: {self.preview_delay_ms:.2f} ms\n"
            f"Pipeline: {self.pipeline_delay_ms:.2f} ms"
        )
        
        self.info_metrics_label.config(text=info_text)
        
        self.root.after(200, self.update_gui_fps)
    
    def restore_from_tray(self):
        """Restore window from tray"""
        try:
            print("[INFO] Restoring window from tray...")
            # Show window
            self.root.deiconify()
            
            # If state was 'zoomed' (maximized), restore it
            if getattr(self, '_maximized_before_minimize', False):
                self.root.state('zoomed')
                print("[INFO] Window restored to maximized state")
            else:
                print("[INFO] Window restored to normal state")
            
        except Exception as e:
            print(f"[ERROR] Failed to restore window: {e}")
    
    def exit_application(self):
        """Full application shutdown"""
        try:
            print("[INFO] Exiting application...")
            self.running = False
            
            # Close all open child windows
            child_windows = [
                ('calibration_window1', 'Stream 1 Calibration Window'),
                ('calibration_window2', 'Stream 2 Calibration Window'),
                ('pq_window', 'PQ Curve Editor'),
                ('custom_gamma_window_s1', 'Custom Gamma S1'),
                ('custom_gamma_window_s2', 'Custom Gamma S2')
            ]
            
            for attr_name, window_name in child_windows:
                try:
                    window = getattr(self, attr_name, None)
                    if window is not None and window.winfo_exists():
                        print(f"[INFO] Closing {window_name}...")
                        window.destroy()
                        setattr(self, attr_name, None)
                except Exception as e:
                    print(f"[WARN] Failed to close {window_name}: {e}")
            
            # Stop tray icon if exists
            if hasattr(self, 'tray_manager') and self.tray_manager:
                try:
                    self.tray_manager.stop()
                except:
                    pass
            
            # Close main window
            self.root.quit()
            
            # Delete mapping_data.json if exists
            try:
                mapping_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mapping_data.json")
                if os.path.exists(mapping_path):
                    os.remove(mapping_path)
                    print(f"[INFO] Deleted mapping_data.json")
            except Exception as e:
                print(f"[WARN] Failed to delete mapping_data.json: {e}")
            
        except Exception as e:
            print(f"[ERROR] Exit error: {e}")


def main():
    """Application entry point"""
    # First show splash screen
    print("[INFO] Starting splash screen...")
    if show_splash_screen(image_path="main.png", duration_ms=5000):
        print("[INFO] Splash screen finished, creating main window...")
    
    # Create main window after splash screen closes (hidden initially to prevent white flash)
    root = tk.Tk()
    
    # Hide window during initialization to prevent empty/white window flash
    root.withdraw()

    hwnd = root.winfo_id()
    set_window_dark_mode(hwnd)
    
    # === DPI Scaling and Modern Title Bar Support ===
    try:
        # Enable DPI awareness for Windows 10/11
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    
    # Set dark theme for entire program
    try:
        style = ttk.Style()
        style.theme_use("clam")
        
        # Dark theme color settings for Windows (if supported)
        root.configure(bg="#1a1b26")
    except:
        pass
    
    # === APPLY DARK TITLE BAR TO MAIN WINDOW ===
    # Apply dark mode title bar using Windows DWM API
    try:
        hwnd = int(root.winfo_id())
        set_window_dark_mode(hwnd)  # Enable dark title bar
    except Exception:
        pass  # Dark mode not supported on older Windows versions
    
    # Set window icon if available
    try:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)
    except:
        pass
    
    # === WINDOW CONFIGURATION WITH ADAPTIVE RESIZING ===
    # Set minimum window size for usability
    root.minsize(800, 600)
    
    # Enable scaling on window resize
    root.columnconfigure(0, weight=1)
    root.rowconfigure(0, weight=1)
    
    # Note: No modern title bar applied - using default Tkinter window style
    
    app = GPUCaptureApp(root)
    
    # Show main window after full initialization (prevents white flash from hidden state)
    print("[INFO] GUI initialization complete, showing main window...")
    root.deiconify()
    
    # Update FPS loop after GUI is ready
    root.after(200, app.update_gui_fps)
    
    # Initialize and start tray icon
    if app.has_tray_support:
        try:
            from pystray import MenuItem as item
            
            def create_tray_menu():
                return (
                    item('Show', app.restore_from_tray),
                    item('Exit', app.exit_application)
                )
            
            # Load tray icon - use ico.png (using path_utils)
            icon_path = resolve_resource_path("ico.png")
            if not os.path.exists(icon_path):
                print("[WARN] ico.png not found, trying main.png...")
                icon_path = resolve_resource_path("main.png")
            if not os.path.exists(icon_path):
                print("[WARN] main.png not found, trying SpectrLed.png...")
                icon_path = resolve_resource_path("SpectrLed.png")
            
            # Create icon with transparency
            tray_image = Image.open(icon_path).convert("RGBA") if os.path.exists(icon_path) else None
            
            # Create tray menu
            def on_click_icon(icon, item):
                app.restore_from_tray()
            
            menu = (
                item('Show', app.restore_from_tray),
                item('Exit', app.exit_application)
            )
            
            # Create tray icon
            import pystray
            app.tray_manager = pystray.Icon(
                "Spectr aLED",
                tray_image,
                "Spectr aLED",
                menu
            )
            
            # Start tray icon in separate thread
            def run_tray():
                try:
                    app.tray_manager.run()
                except Exception as e:
                    print(f"[ERROR] Tray icon error: {e}")
            
            if tray_image is not None:
                app.tray_manager = pystray.Icon(
                    "Spectr aLED",
                    tray_image,
                    "Spectr aLED",
                    menu
                )
                
                # Start tray icon in separate thread
                def run_tray():
                    try:
                        app.tray_manager.run()
                    except Exception as e:
                        print(f"[ERROR] Tray icon error: {e}")
                
                tray_thread = threading.Thread(target=run_tray, daemon=True)
                tray_thread.start()
                
                print("[OK] Tray icon started")
            else:
                print("[WARN] Failed to load tray icon - all image files not found")
        except Exception as e:
            print(f"[ERROR] Failed to start tray: {e}")
    
    # Start processing threads after mainloop is running (use a small delay to ensure Tk is fully initialized)
    root.after(500, app.start_threads)
    
    # Window close handler - full application close via X button
    def on_closing():
        print("[INFO] Closing application via close button...")
        try:
            app.exit_application()
        except Exception as e:
            print(f"[ERROR] Exit error: {e}")
            root.quit()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Window minimize handler - hide GUI on minimize, keep background process and tray
    def on_minimize(event):
        """Hide window on minimize"""
        if app.tray_manager and root.state() == 'iconic':
            print("[INFO] Window minimized - hiding all GUI windows...")
            
            # Disable preview before minimizing
            app.preview_enabled = False
            app.preview2_enabled = False
            print("[INFO] Preview disabled before minimize")
            
            # Close all open child windows
            child_windows = [
                ('calibration_window1', 'Stream 1 Calibration Window'),
                ('calibration_window2', 'Stream 2 Calibration Window'),
                ('pq_window', 'PQ Curve Editor'),
                ('custom_gamma_window_s1', 'Custom Gamma S1'),
                ('custom_gamma_window_s2', 'Custom Gamma S2'),
                ('mapping_window', 'Mapping Window')
            ]
            
            for attr_name, window_name in child_windows:
                try:
                    window = getattr(app, attr_name, None)
                    if window is not None and window.winfo_exists():
                        print(f"[INFO] Closing {window_name}...")
                        window.destroy()
                        setattr(app, attr_name, None)
                except Exception as e:
                    print(f"[WARN] Failed to close {window_name}: {e}")
            
            try:
                root.withdraw()
            except Exception as e:
                print(f"[ERROR] Failed to hide window on minimize: {e}")
    
    # Window restore handler from minimized state
    def on_restore(event):
        """Show window when restored from minimized state"""
        if app.tray_manager and root.state() == 'normal':
            print("[INFO] Window restored from minimized state...")
    
    # Track window state changes (for minimize and restore)
    root.bind('<Unmap>', on_minimize)  # Event on minimize
    root.bind('<Map>', on_restore)     # Event on restore
    
    print("[INFO] Minimize behavior updated - click tray icon to restore window")
    
    root.mainloop()
    
    # Cleanup on exit
    from image_processor import shutdown_lut_pool
    shutdown_lut_pool()
    
    for dev in WLED_DEVICES:
        restore_wled(dev["ip"])
    
    ddp_socket.close()


if __name__ == "__main__":
    main()
