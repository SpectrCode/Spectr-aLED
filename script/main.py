"""
Main application file for GPU Capture + WLED
Combines all modules and launches the application"
"""

import sys
import os

# Import path utilities first
from path_utils import resolve_resource_path, resolve_config_path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Mapping auto-save file - handled in maping.py module

import tkinter as tk
from tkinter import ttk, filedialog
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

# === THERMAL MODEL (background LED heat simulation) ===
try:
    from thermal_model import ThermalConfig, LEDThermalModel, thermal_colormap
    THERMAL_AVAILABLE = True
except Exception:
    THERMAL_AVAILABLE = False

# === WLED OFF-MODE COLOR WINDOW (per-module color picker) ===
try:
    from wled_color_window import WLEDColorWindow
    WLED_COLOR_WINDOW_AVAILABLE = True
except Exception:
    WLED_COLOR_WINDOW_AVAILABLE = False

# System tray support for Windows
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

# === Enable process-wide dark title bar (Windows 10/11) ===
# Must be called BEFORE any windows are created.
# This calls SetPreferredAppMode(1) from uxtheme.dll which tells Windows
# to use dark caption buttons and dark title bars for ALL windows in this process.
# NOTE: SetPreferredAppMode is an UNDOCUMENTED export and is missing from
# uxtheme.dll on some Windows builds (e.g. Windows 11 25H2, build 26200).
# That is normal, not an error: the per-window DWM fallback (applied after
# deiconify()) darkens the title bar on those systems as well.
try:
    from window_utils import (
        enable_process_dark_mode,
        set_window_dark_mode as _wm_set_dark,
        process_dark_mode_export_present,
    )
    if enable_process_dark_mode():
        print("[OK] SetPreferredAppMode(1) succeeded - process-wide dark title bar enabled")
    elif not process_dark_mode_export_present():
        # Export is absent from this OS's uxtheme.dll - expected fallback
        print("[INFO] SetPreferredAppMode not exported by this uxtheme.dll - using per-window DWM dark mode (expected fallback)")
    else:
        print("[WARN] SetPreferredAppMode(1) returned an error - falling back to per-window DWM dark mode")
except Exception as e:
    print(f"[WARN] SetPreferredAppMode not available ({e}) - using per-window DWM dark mode")


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
    """
    Enable dark title bar for a specific window.
    Uses GetParent() to resolve the correct frame HWND (handles Toplevel inner-HWND).
    Sets both DWM attr 20 and 19 for maximum Windows version compatibility.
    """
    # Resolve the real frame HWND (for Toplevel, winfo_id() returns inner child HWND)
    parent = ctypes.windll.user32.GetParent(wintypes.HWND(int(hwnd)))
    frame_hwnd = int(parent) if parent else int(hwnd)
    
    value = c_int(1)
    
    # attr 20: Windows 11 / Win10 2004+
    res20 = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(frame_hwnd), 20, byref(value), sizeof(value)
    )
    # attr 19: Windows 10 before 20H1 (always set too for compat)
    res19 = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(frame_hwnd), 19, byref(value), sizeof(value)
    )
    
    # Force frame repaint: SWP_NOMOVE|SWP_NOSIZE|SWP_NOZORDER|SWP_FRAMECHANGED
    ctypes.windll.user32.SetWindowPos(
        wintypes.HWND(frame_hwnd), 0, 0, 0, 0, 0, 0x0027
    )
    
    return (res20 == 0) or (res19 == 0)


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
# === ASPECT RATIO MODES ===
# === LUT SIZES ===
# === HDR TONEMAP MODES ===
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
BLACK_RESTART_DELAY = 1.

# === FPS UPDATE INTERVAL (ms) ===
# === CONFIG FILE PATH ===
# Configs are stored in %APPDATA%\Spectr_alLED\
CONFIG_FILE_PATH = resolve_config_path("app_config.json")


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
        
        # Auto-interpolation checkbox states (per stream, per mode)
        "interp_sdr_1": False,
        "interp_hdr_1": False,
        "interp_sdr_2": False,
        "interp_hdr_2": False,
        
        # PQ curve values (RGB) - Stream 1 (base curve for compatibility)
        "pq_values_r": [0.0] * PQ_POINTS,
        "pq_values_g": [0.0] * PQ_POINTS,
        "pq_values_b": [0.0] * PQ_POINTS,

        # Shader optimization (from the optimization window)
        "shader_precision": {
            "coordinate": "fp32",
            "weights": "fp32",
            "color": "fp32",
            "accumulator": "fp32",
        },
        "pixel_limit": 0,  # 0 = unlimited
        "coordinate_recalc_mode": "once",
        "use_separable": True,

        # LED settings (Power panel, per stream) — incl. Temp Map calculation toggle
        "led_settings_s1": default_led_settings(),
        "led_settings_s2": default_led_settings(),

        # WLED module OFF-mode colors: ip -> {"r":, "g":, "b":, "bri":}
        # (set in the per-module "Color" window / settings)
        "wled_off_colors": {},

        # Current send protocol: "DDP" or "E1.31"
        "current_protocol": "DDP",
    }


def default_led_settings():
    """Default per-stream LED settings for the Power panel."""
    return {
        "r_ma": 12.0,        # max current per red crystal, mA
        "g_ma": 12.0,        # max current per green crystal, mA
        "b_ma": 12.0,        # max current per blue crystal, mA
        "mcu_ma": 1.0,       # LED controller (MCU) consumption per LED, mA
        "eff_r_pct": 10.0,   # red crystal efficiency (КПД), %
        "eff_g_pct": 14.0,   # green crystal efficiency (КПД), %
        "eff_b_pct": 28.0,   # blue crystal efficiency (КПД), %
        "loss_pct": 5.0,     # PSU + wire losses, %
        "ambient_c": 25.0,   # ambient air temperature, °C (Temp Map scale minimum)
        "density_w": 100.0,  # LED density along width, LEDs/m
        "density_h": 100.0,  # LED density along height, LEDs/m
        "temp_map_enabled": True,  # background Temp Map calculation on/off
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
from wled_controller import WLEDController

# Import DDP controller module for StreamManager class
import ddp_controller

# Import E1.31 sACN controller module
try:
    from e131_controller import (
        get_sacn_socket,
        close_sacn_socket,
        is_host_online as sacn_is_host_online,
        set_wled_live as sacn_set_wled_live,
        restore_wled as sacn_restore_wled,
        StreamManager as E131StreamManager,
        DEVICE_MODES as sacn_device_modes,
        run_sacn_loop,
        stop_sacn_loops,
        SACN_PORT,
        build_sacn_packet,
        LEDS_PER_UNIVERSE,
        CHANNELS_PER_UNIVERSE,
        START_UNIVERSE,
    )
    HAS_SACN = True
except ImportError:
    print("[WARN] e131_controller.py not found - sACN mode disabled")
    HAS_SACN = False

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
    apply_lut_generic,
    apply_ambilight, 
    apply_saturation, 
    generate_pq_exponential,
    apply_shadow_bias_to_curve, 
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

# Import shader optimization window module
from optimization_window import open_optimization_window


# Global variables for WLED devices and mapping
WLED_DEVICES = []

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

class GPUCaptureApp:
    """Main application class - combines all functions"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Spectr aLED")
        
        # Get work area (excluding taskbar) using SystemParametersInfoW
        try:
            SPI_GETWORKAREA = 0x0030
            
            class _RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]
            
            wa = _RECT()
            windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
            work_x = wa.left
            work_y = wa.top
            work_w = wa.right - wa.left
            work_h = wa.bottom - wa.top
        except Exception:
            work_w, work_h, work_x, work_y = 1280, 680, 0, 0
        
        # Ensure minimum usable size
        work_w = max(work_w, 800)
        work_h = max(work_h, 500)
        
        # Set window to work area (excludes taskbar)
        self.root.geometry(f"{work_w}x{work_h}+{work_x}+{work_y}")
        
        # Maximize, then constrain to work area (Tkinter zoom can overflow under taskbar)
        try:
            self.root.state('zoomed')
            self.root.update_idletasks()
            # Re-constrain window to work area bounds after zoom
            hwnd = int(self.root.winfo_id())
            # SWP_NOZORDER | SWP_NOACTIVATE
            windll.user32.SetWindowPos(
                hwnd, 0, work_x, work_y, work_w, work_h, 0x0002 | 0x0010
            )
        except Exception:
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
        self.optimization_window = None
        
        # === SHADER OPTIMIZATION STATE ===
        self.shader_precision = {
            "coordinate": "fp32",
            "weights": "fp32",
            "color": "fp32",
            "accumulator": "fp32",
            "output": "fp32",
        }
        # Pixel limit: 0 = unlimited, >0 = max total source pixels to sample
        self.pixel_limit = 0
        # Coordinate recalculation mode: "frame" (every frame) or "once" (cached, 1 time)
        self.coordinate_recalc_mode = "once"
        
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
        self.capture_fps = tk.StringVar(value="adaptive")  # "adaptive" | "24" | "30" | "60" | "120" | "144"
        self.wled_ip_var = tk.StringVar()
        self.wled_discovered = []
        
        # WLED ping status tracking
        self._wled_ping_thread_running = False
        self._wled_ping_stop_event = threading.Event()
        self.running = True
        self.preview_enabled = False
        
        # DDP send loop control flags (for protocol switching)
        self.ddp_send_loop_running = True  # Controls whether DDP loops poll the queue
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
        
        # === PER-DEVICE SEND MODES ===
        # Maps IP -> "stream" (LIVE) | "pause" (freeze last frame) | "off" (no live)
        # Rebuilt in rebuild_master_mapping(), updated in set_dev_mode().
        self.dev_modes = {}
        
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
        # E1.31 sACN frame counters - Stream 1
        self.sacn1_frame_count = 0
        self.sacn1_fps_real = 0
        
        # Restart handling: a single worker thread is the ONLY code that
        # performs DLL shutdown/init; UI actions just request a restart
        self.restart_requested = False
        self.restart_lock = threading.Lock()
        self._restart_cv = threading.Condition(self.restart_lock)
        self._restart_worker_thread = None
        self._restart_worker_lock = threading.Lock()
        self.capture_paused = False
        # "capture thread is inside a DLL call" tracking:
        # the restart worker waits for this to reach 0 before touching the device
        self._dll_inflight = 0
        self._dll_state_cv = threading.Condition()
        
        # Mapping stats
        self.mapping_fps = {}
        self._sync_lock = False
        self.mapping_counts = {}
        self.dll_restarting = False
        self.last_ddp_frame = None
        self.capture_delay_ms = 0.0
        self.scale_delay_ms = 0.0
        
        # LED power consumption tracking
        # Per-stream LED settings (live-editable from the Power panel)
        self.led_settings_s1 = default_led_settings()
        self.led_settings_s2 = default_led_settings()
        self.led_power_s1 = {"leds": 0, "current_ma": 0.0, "power_w": 0.0}
        self.led_power_s2 = {"leds": 0, "current_ma": 0.0, "power_w": 0.0}
        # Real per-device power (computed from per-crystal brightness after mapping)
        # ip -> {"current_ma": float, "power_w": float}
        self.wled_dev_power = {}
        # Per-module OFF-mode color (color picker window):
        # ip -> {"r": 0-255, "g": 0-255, "b": 0-255, "bri": 0-255}
        # (r,g,b) — итоговый цвет, bri = максимальный канал; при отправке
        # на WLED уходит base = final × 255 / bri, и устройство само делит
        # цвет на яркость (одна операция, без двойного затемнения);
        # default (10,10,10) == the old fixed 10/10/10 OFF residual
        self.wled_off_colors = {}
        # Open color-picker windows: ip -> WLEDColorWindow (one per module)
        self.wled_color_windows = {}
        # Throttled real-time WLED color pushes: ip -> {"busy": bool, "pending": (r,g,b,bri)}
        self._off_color_send_state = {}
        # "Last frame" snapshots for modules in PAUSE mode
        # ip -> (n, 3) float 0.0-1.0 (per-crystal brightness frozen on pause)
        self._dev_frozen_rgb = {}
        self._dev_frozen_streams = {}
        # Boolean masks of ONLINE device LEDs (aligned to exp_pixel_indices / exp_pixel_indices2)
        self.online_mask1 = None
        self.online_mask2 = None

        # === THERMAL MAP (background LED heat simulation) ===
        # Latest per-LED brightness (N, 3) 0..1, offline LEDs masked to 0
        self.thermal_frame_s1 = None
        self.thermal_frame_s2 = None
        # Live thermal models (keep accumulating heat between frames)
        self.thermal_model_s1 = self._create_thermal_model(1) if THERMAL_AVAILABLE else None
        self.thermal_model_s2 = self._create_thermal_model(2) if THERMAL_AVAILABLE else None
        
        # Stream 2 specific - active stream selector
        self.active_stream = tk.IntVar(value=1)
        
        # Delays
        self.ddp_delay_ms = 0.0
        self.sacn_delay_ms = 0.0
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
        self.ddp2_fps_real = 0
        self.ddp2_delay_ms = 0.0
        self.last_ddp2_frame_time = 0.0
        # E1.31 sACN frame counters - Stream 2
        self.sacn2_frame_count = 0
        self.sacn2_fps_real = 0
        
        # === PROTOCOL SELECTION VARIABLES ===
        # Protocol selection - "DDP" or "E1.31"
        self.current_protocol = tk.StringVar(value="DDP")
        
        # E1.31 Stream Managers for each stream (created when protocol switches)
        self.sacn_manager1 = None
        self.sacn_manager2 = None
        
        # DDP Stream Managers (always active)
        self.ddp_manager1 = ddp_controller.StreamManager()
        self.ddp_manager2 = ddp_controller.StreamManager()
        
        # Protocol buttons references
        self.ddp_protocol_btn = None
        self.sacn_protocol_btn = None
        
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

        # Auto-interpolation checkboxes (per stream, per mode)
        self.interp_sdr_1 = tk.BooleanVar(value=False)
        self.interp_hdr_1 = tk.BooleanVar(value=False)
        self.interp_sdr_2 = tk.BooleanVar(value=False)
        self.interp_hdr_2 = tk.BooleanVar(value=False)
        
        self.external_lut = None
        self.external_lut2 = None
        
        # === INPUT (GUI) VARIABLES ===
        self.input_target_w = tk.IntVar(value=TARGET_W)
        self.input_target_h = tk.IntVar(value=TARGET_H)
        
        self.input_target2_w = tk.IntVar(value=120)
        self.input_target2_h = tk.IntVar(value=68)
        
        # Debounce timers for real-time resolution apply
        self._res_debounce_s1 = None
        self._res_debounce_s2 = None
        
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
        
        # Stream 1 PQ Curve settings (RGB)
        self.pq_curve_strength1 = tk.DoubleVar(value=3.0)
        self.pq_curve_bias1 = tk.DoubleVar(value=0.025)
        
        # Stream 1 PQ Curve settings (Mono - independent from RGB)
        self.pq_curve_strength_mono1 = tk.DoubleVar(value=3.0)
        self.pq_curve_bias_mono1 = tk.DoubleVar(value=0.025)
        
        # Stream 2 PQ Curve settings (RGB)
        self.pq_curve_strength2 = tk.DoubleVar(value=3.0)
        self.pq_curve_bias2 = tk.DoubleVar(value=0.025)
        
        # Stream 2 PQ Curve settings (Mono - independent from RGB)
        self.pq_curve_strength_mono2 = tk.DoubleVar(value=3.0)
        self.pq_curve_bias_mono2 = tk.DoubleVar(value=0.025)
        
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
        
        # PQ values MONO - Stream 1 (independent from R/G/B)
        self.pq_values_mono1 = np.copy(self.pq_values)
        
        # PQ values - Stream 2 (initialize with same base curve but will be independent)
        self.pq_values_r2 = np.copy(self.pq_values)
        self.pq_values_g2 = np.copy(self.pq_values)
        self.pq_values_b2 = np.copy(self.pq_values)
        
        # PQ values MONO - Stream 2 (independent from R/G/B)
        self.pq_values_mono2 = np.copy(self.pq_values)
        
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
        
        # Stream 1: MONO curve (independent from RGB)
        self.custom_gamma_mono1 = np.copy(self.custom_gamma_sdr_values)
        
        self.custom_gamma_hdr_r1 = np.copy(self.custom_gamma_hdr_values)
        self.custom_gamma_hdr_g1 = np.copy(self.custom_gamma_hdr_values)
        self.custom_gamma_hdr_b1 = np.copy(self.custom_gamma_hdr_values)
        
        # Stream 2
        self.custom_gamma_sdr_r2 = np.copy(self.custom_gamma_sdr_values)
        self.custom_gamma_sdr_g2 = np.copy(self.custom_gamma_sdr_values)
        self.custom_gamma_sdr_b2 = np.copy(self.custom_gamma_sdr_values)
        
        # Stream 2: MONO curve (independent from RGB)
        self.custom_gamma_mono2 = np.copy(self.custom_gamma_sdr_values)
        
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
        
        # Save Stream 1 MONO gamma (independent from RGB)
        self.saved_custom_gamma_mono1 = np.copy(self.custom_gamma_mono1)
        
        # Save Stream 2 SDR gamma values (R, G, B)
        self.saved_custom_gamma_sdr_r2 = np.copy(self.custom_gamma_sdr_r2)
        self.saved_custom_gamma_sdr_g2 = np.copy(self.custom_gamma_sdr_g2)
        self.saved_custom_gamma_sdr_b2 = np.copy(self.custom_gamma_sdr_b2)
        
        # Save Stream 2 MONO gamma (independent from RGB)
        self.saved_custom_gamma_mono2 = np.copy(self.custom_gamma_mono2)
        
        # Save Stream 1 custom gamma parameters (RGB - curve, bias, and enabled state)
        self.saved_curve_strength1 = tk.DoubleVar(value=2.0)
        self.saved_bias1 = tk.DoubleVar(value=0.025)
        self.saved_custom_gamma_enabled1 = tk.BooleanVar(value=True)
        
        # Save Stream 1 custom gamma parameters (Mono - independent from RGB)
        self.saved_curve_strength_mono1 = tk.DoubleVar(value=2.0)
        self.saved_bias_mono1 = tk.DoubleVar(value=0.025)
        self.saved_custom_gamma_enabled_mono1 = tk.BooleanVar(value=True)
        
        # Save Stream 2 custom gamma parameters (RGB - curve, bias, and enabled state)
        self.saved_curve_strength2 = tk.DoubleVar(value=2.0)
        self.saved_bias2 = tk.DoubleVar(value=0.025)
        self.saved_custom_gamma_enabled2 = tk.BooleanVar(value=True)
        
        # Save Stream 2 custom gamma parameters (Mono - independent from RGB)
        self.saved_curve_strength_mono2 = tk.DoubleVar(value=2.0)
        self.saved_bias_mono2 = tk.DoubleVar(value=0.025)
        self.saved_custom_gamma_enabled_mono2 = tk.BooleanVar(value=True)
        
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
            self.custom_gamma_mono1[:] = biased1[:64]
        
        # Save to saved arrays
        if len(self.saved_custom_gamma_sdr_r1) == 64:
            self.saved_custom_gamma_sdr_r1[:] = biased1[:64]
            self.saved_custom_gamma_sdr_g1[:] = biased1[:64]
            self.saved_custom_gamma_sdr_b1[:] = biased1[:64]
            self.saved_custom_gamma_mono1[:] = biased1[:64]
        
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
            self.custom_gamma_mono2[:] = biased2[:64]
        
        # Save to saved arrays
        if len(self.saved_custom_gamma_sdr_r2) == 64:
            self.saved_custom_gamma_mono2[:] = biased2[:64]
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

        # === FPS / Capture rate combobox (next to monitor) ===
        tk.Label(
            monitor_container,
            text="⚡ FPS",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"]
        ).pack(side="left", padx=(20, 6))

        self.capture_fps_combo = ttk.Combobox(
            monitor_container,
            textvariable=self.capture_fps,
            values=["adaptive", "144", "120", "60", "30", "24"],
            state="readonly",
            width=10
        )
        self.capture_fps_combo.current(0)
        self.capture_fps_combo.pack(side="left")
        self.capture_fps_combo.bind("<<ComboboxSelected>>", self.on_capture_fps_change)
                
        # =========================
        # CAPTURE + TEMP PANELS (side by side)
        # =========================
        capture_temp_row = tk.Frame(main, bg=colors["bg"])
        capture_temp_row.pack(fill="x", pady=(15, 0))
        
        capture = tk.LabelFrame(
            capture_temp_row,
            text=" 🎥 Capture",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        capture.pack(side="left", fill="y", padx=(0, 8))
        
        # --- POWER PANEL (LED power) - minimalist ---
        temp = tk.LabelFrame(
            capture_temp_row,
            text=" ⚡ Power",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        temp.pack(side="right", fill="both", expand=True, padx=(8, 0))
        
        # --- One row per stream: value label (left) + Settings / Temp Map (right) ---
        self.temp_s1_label = self._create_temp_row(temp, 1)
        self.temp_s2_label = self._create_temp_row(temp, 2)
        
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
        
        # Real-time apply for Stream 1 resolution inputs (debounced)
        self.input_target_w.trace_add("write", lambda *args: self._debounce_res_s1())
        self.input_target_h.trace_add("write", lambda *args: self._debounce_res_s1())
        
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
        
        # Real-time apply for Stream 2 resolution inputs (debounced)
        self.input_target2_w.trace_add("write", lambda *args: self._debounce_res_s2())
        self.input_target2_h.trace_add("write", lambda *args: self._debounce_res_s2())
        
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
            text="Active Stream Settings:",
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
        
        # FPS panels side by side with separator
        fps_frame = tk.Frame(info_content, bg=colors["bg"])
        fps_frame.pack(fill="x", pady=(0, 5))

        # Stream 1 FPS label (left side)
        self.info_stream1_label = tk.Label(
            fps_frame,
            text="Waiting for metrics...",
            justify="left",
            anchor="w",
            font=("Consolas", 9),
            bg=colors["bg"],
            fg=colors["text_main"]
        )
        self.info_stream1_label.pack(side="left", fill="x", expand=True, padx=(0, 4))

        # Vertical separator
        separator = tk.Frame(
            fps_frame,
            width=2,
            bg=colors.get("border", "#444455")
        )
        separator.pack(side="left", fill="y", padx=2)

        # Stream 2 FPS label (right side)
        self.info_stream2_label = tk.Label(
            fps_frame,
            text="Waiting for metrics...",
            justify="left",
            anchor="w",
            font=("Consolas", 9),
            bg=colors["bg"],
            fg=colors["text_main"]
        )
        self.info_stream2_label.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Horizontal separator between FPS panels and delays (colored like info panel border)
        sep1 = tk.Frame(info_content, height=2, bg=colors["border"])
        sep1.pack(fill="x", pady=(8, 5))

        # Common delays label (below FPS panels)
        self.info_delays_label = tk.Label(
            info_content,
            text="",
            justify="left",
            anchor="nw",
            font=("Consolas", 9),
            bg=colors["bg"],
            fg=colors["text_main"]
        )
        self.info_delays_label.pack(fill="x", pady=(5, 5))

        # Horizontal separator between delays and support section (colored like info panel border)
        sep2 = tk.Frame(info_content, height=2, bg=colors["border"])
        sep2.pack(fill="x", pady=(10, 8))

        #

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
        
        # Middle - Optimization block (separate, right of Configuration)
        opt_frame = tk.LabelFrame(
            config_mapping_row,
            text=" ⚡ Optimization",
            font=("Segoe UI", 10, "bold"),
            bg=colors["bg"],
            fg=colors["text_main"],
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        opt_frame.pack(side="left", fill="x", padx=(0, 8))
        
        ttk.Button(
            opt_frame,
            text="⚙ Shader Optimizer",
            command=self.open_shader_optimizer
        ).pack(side="left", padx=10, pady=8)
        
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
            text="ver. 1.0.3",
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
        
        # Mouse wheel scrolling for WLED device list
        def _wled_on_mousewheel(event):
            """Scroll WLED device list with mouse wheel"""
            self.wled_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        def _wled_bind_wheel(event=None):
            self.wled_container.bind_all("<MouseWheel>", _wled_on_mousewheel)
        
        def _wled_unbind_wheel(event=None):
            try:
                self.wled_container.unbind_all("<MouseWheel>")
            except:
                pass
        
        # Bind mouse wheel when container enters, unbind when it leaves
        self.wled_container.bind("<Enter>", _wled_bind_wheel)
        self.wled_container.bind("<Leave>", _wled_unbind_wheel)
        
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
        """Initialize PQ curve values ONLY if they are all zeros (uninitialized).
        Does NOT overwrite values that were explicitly loaded from config or set by user."""
        # Stream 1 - RGB
        base_rgb1 = generate_pq_exponential(
            strength=self.pq_curve_strength1.get(),
            points=self.pq_points
        )
        base_rgb1 = self.apply_shadow_bias_to_curve(base_rgb1, self.pq_curve_bias1.get())
        
        # Stream 1 - Mono (independent)
        base_mono1 = generate_pq_exponential(
            strength=self.pq_curve_strength_mono1.get(),
            points=self.pq_points
        )
        base_mono1 = self.apply_shadow_bias_to_curve(base_mono1, self.pq_curve_bias_mono1.get())
        
        # Stream 2 - RGB
        base_rgb2 = generate_pq_exponential(
            strength=self.pq_curve_strength2.get(),
            points=self.pq_points
        )
        base_rgb2 = self.apply_shadow_bias_to_curve(base_rgb2, self.pq_curve_bias2.get())
        
        # Stream 2 - Mono (independent)
        base_mono2 = generate_pq_exponential(
            strength=self.pq_curve_strength_mono2.get(),
            points=self.pq_points
        )
        base_mono2 = self.apply_shadow_bias_to_curve(base_mono2, self.pq_curve_bias_mono2.get())
        
        # Fill only uninitialized (all-zero) values
        if np.all(self.pq_values_r1 == 0):
            self.pq_values_r1[:] = base_rgb1
        if np.all(self.pq_values_g1 == 0):
            self.pq_values_g1[:] = base_rgb1
        if np.all(self.pq_values_b1 == 0):
            self.pq_values_b1[:] = base_rgb1
        if np.all(self.pq_values_mono1 == 0):
            self.pq_values_mono1[:] = base_mono1
        
        if np.all(self.pq_values_r2 == 0):
            self.pq_values_r2[:] = base_rgb2
        if np.all(self.pq_values_g2 == 0):
            self.pq_values_g2[:] = base_rgb2
        if np.all(self.pq_values_b2 == 0):
            self.pq_values_b2[:] = base_rgb2
        if np.all(self.pq_values_mono2 == 0):
            self.pq_values_mono2[:] = base_mono2
        
        # For compatibility update old variables (use Stream 1 as base)
        if np.all(self.pq_values_r == 0):
            self.pq_values_r[:] = base_rgb1
        if np.all(self.pq_values_g == 0):
            self.pq_values_g[:] = base_rgb1
        if np.all(self.pq_values_b == 0):
            self.pq_values_b[:] = base_rgb1
    
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
    
    
    def apply_pq_curve(self, hdr_tensor: np.ndarray, stream: int = 1) -> np.ndarray:
        """Apply PQ curve to HDR tensor.
        
        - mode "rgb" (Mono): applies the independent mono curve to all channels
        - mode "separate" (RGB): applies per-channel R/G/B curves
        """
        
        x = np.clip(hdr_tensor * 80.0, 0.0, 10000.0)
        
        if stream == 1:
            mode = self.pq_rgb_mode1.get()
            mono = self.pq_values_mono1
            values_r = self.pq_values_r1
            values_g = self.pq_values_g1
            values_b = self.pq_values_b1
        else:
            mode = self.pq_rgb_mode2.get()
            mono = self.pq_values_mono2
            values_r = self.pq_values_r2
            values_g = self.pq_values_g2
            values_b = self.pq_values_b2
        
        if mode == "rgb":
            # Mono mode - use independent mono curve for all channels
            y = np.interp(x, self.pq_nits, mono)
            return y.astype(np.float32)
        
        # Separate RGB mode
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
            
            # Auto-interpolate to 256 if checkbox is enabled
            try:
                interp_var = getattr(self, f"interp_{mode.lower()}_{stream}", None)
                if interp_var is not None and interp_var.get():
                    self.interpolate_lut_to_256(stream, mode)
            except Exception as ae:
                print(f"[WARN] Auto-interpolation check failed: {ae}")
        
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
    
    def interpolate_lut_to_256(self, stream: int = 1, mode: str = "SDR"):
        """Interpolate external LUT to 256x256x256 using trilinear interpolation"""
        # Get the LUT to interpolate
        if stream == 1:
            if mode == "SDR":
                lut = getattr(self, "external_lut_sdr_1", None)
            else:
                lut = getattr(self, "external_lut_hdr_1", None)
        else:
            if mode == "SDR":
                lut = getattr(self, "external_lut_sdr_2", None)
            else:
                lut = getattr(self, "external_lut_hdr_2", None)
        
        if lut is None:
            print(f"[WARN] No {mode} LUT loaded for Stream {stream} to interpolate")
            return
        
        old_size = lut.shape[0]
        target_size = 256
        
        if old_size == target_size:
            print(f"[INFO] S{stream} {mode} LUT already at 256x256x256, skipping interpolation")
            return
        
        print(f"[INFO] Interpolating S{stream} {mode} LUT: {old_size}^3 -> {target_size}^3 ...")
        
        try:
            # Create normalized coordinate grid for all points in the new LUT
            grid = np.linspace(0.0, 1.0, target_size, dtype=np.float32)
            coords = np.meshgrid(grid, grid, grid, indexing='ij')
            # Stack as [R, G, B] to match frame format expected by apply_lut_generic
            frame = np.stack([coords[0], coords[1], coords[2]], axis=-1).astype(np.float32)
            
            # Apply trilinear interpolation using existing function
            result = apply_lut_generic(frame, lut)
            
            # Save interpolated LUT back
            if stream == 1:
                if mode == "SDR":
                    self.external_lut_sdr_1 = result
                else:
                    self.external_lut_hdr_1 = result
            else:
                if mode == "SDR":
                    self.external_lut_sdr_2 = result
                else:
                    self.external_lut_hdr_2 = result
            
            print(f"[OK] S{stream} {mode} LUT interpolated: {old_size}^3 -> {target_size}^3")
        
        except Exception as e:
            print(f"[ERROR] LUT interpolation failed: {e}")
    
    def apply_ambilight(self, frame: np.ndarray, percent: float, power: float = 2.0) -> np.ndarray:
        """Apply Ambilight effect"""
        return apply_ambilight(frame, percent, power)
    
    def get_stream1_resolution(self) -> tuple:
        """Get Stream 1 resolution"""
        return self.target_w.get(), self.target_h.get()
    
    def request_restart(self, full: bool = False):
        """Request restart capture.

        Non-blocking: a single persistent worker thread performs the actual
        DLL shutdown/init. Rapid repeated requests are coalesced, and any
        settings changed while a restart is in flight are applied afterwards.
        """
        with self._restart_worker_lock:
            worker = self._restart_worker_thread
            if worker is None or not worker.is_alive():
                worker = threading.Thread(
                    target=self._restart_worker_loop,
                    name="RestartWorker",
                    daemon=True
                )
                self._restart_worker_thread = worker
                worker.start()

        with self.restart_lock:
            self.restart_requested = True
            self._restart_cv.notify()
    
    def _restart_worker_loop(self):
        """Restart worker: the ONLY code that calls shutdown_capture/init_capture."""
        while True:
            with self.restart_lock:
                while not self.restart_requested:
                    self._restart_cv.wait()
                self.restart_requested = False

            try:
                self._perform_restart()
            except Exception as e:
                print(f"[ERROR] Restart worker failed: {e}")
                self.capture_paused = False
                self.dll_restarting = False
    
    def _tkget(self, var, fallback=0, retries: int = 0, retry_delay: float = 0.05):
        """Thread-safe read of a Tk variable from ANY thread.

        Tk 8.6 allows cross-thread Tk access only while the main thread is
        inside its event loop; otherwise it raises
        RuntimeError("main thread is not in main loop") — e.g. while the UI
        thread is executing a long synchronous callback (apply_resolution /
        rebuild_master_mapping / thermal rebuild) or after shutdown.
        An unguarded .get() in a worker thread KILLS that thread, which froze
        the capture stream permanently when the resolution was changed.

        Behavior: try immediately; while the UI thread is busy, retry briefly
        (transient window), then return `fallback` instead of raising.
        """
        attempts = max(1, int(retries))
        for attempt in range(attempts):
            try:
                return var.get()
            except Exception:
                if attempt + 1 < attempts:
                    time.sleep(retry_delay)
        return fallback

    def _snapshot_target_state(self) -> tuple:
        """Fingerprint of all capture settings a restart must apply."""
        return (
            bool(self.stream1_enabled),
            self._tkget(self.second_stream_enabled,
                        bool(getattr(self, "stream2_enabled", False)), retries=100),
            self.recalc_resolution_for_current_state(),
            self.recalc_resolution_stream2(),
            self._get_capture_fps_value(),
            self._get_active_monitor_name(),
        )
    
    def _dll_call_begin(self) -> bool:
        """Enter a DLL call region. Returns False if a restart is in progress."""
        with self._dll_state_cv:
            if self.dll_restarting:
                return False
            self._dll_inflight += 1
            return True
    
    def _dll_call_end(self):
        """Leave a DLL call region."""
        with self._dll_state_cv:
            self._dll_inflight = max(0, self._dll_inflight - 1)
            self._dll_state_cv.notify_all()
    
    def _wait_dll_idle(self, timeout: float = 5.0) -> bool:
        """Wait until the capture thread is outside the DLL (bounded, no deadlocks)."""
        with self._dll_state_cv:
            deadline = time.monotonic() + timeout
            while self._dll_inflight > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._dll_state_cv.wait(remaining)
            return True
    
    def _perform_restart(self):
        print("\n[WARN] Async restart...")
        self.dll_restarting = True
        self.capture_paused = True
        
        ok = False
        for _attempt in range(2):
            # Re-read settings at execution time: if the user changed
            # something while we were waiting, run the restart again
            # with the new values
            state = self._snapshot_target_state()
            
            # 1) the capture thread must be OUT of the DLL before we
            #    release the device
            if not self._wait_dll_idle(timeout=5.0):
                print("[WARN] DLL still busy after 5s, forcing restart anyway")
            
            w, h = state[2]
            w2, h2 = state[3]
            
            # 2) clear stale frames
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
            
            # 3) stop the device
            with self.dll_lock:
                try:
                    if self.bridge:
                        self.bridge.shutdown_capture()
                except Exception:
                    pass
            time.sleep(0.3)
            
            # 4) start the device
            with self.dll_lock:
                ok = False
                if self.bridge:
                    ok = self.bridge.init_capture(
                        self._tkget(self.monitor_index, 0, retries=100),
                        w,
                        h
                    )
                s2_enabled = self._tkget(
                    self.second_stream_enabled,
                    bool(getattr(self, "stream2_enabled", False)), retries=100)
                if ok and s2_enabled and w2 > 0 and h2 > 0:
                    try:
                        if self.bridge:
                            self.bridge.set_second_resolution(w2, h2)
                    except Exception:
                        pass
            
            # 5) reset buffers / frame counters
            self.frame_buffer = np.empty((h, w, 3), dtype=np.float32)
            self.frame_buffer.fill(0.0)
            self.last_frame_id = -1
            self.last_frame_time = time.perf_counter()
            
            if self._tkget(self.second_stream_enabled,
                           bool(getattr(self, "stream2_enabled", False)), retries=100) and w2 > 0 and h2 > 0:
                self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
                self.frame_buffer2.fill(0.0)
            else:
                self.frame_buffer2 = None
            if getattr(self, "last_frame2_valid", None) is not None:
                self.last_frame2_valid = self.last_frame2_valid.copy()
            self.last_frame2_id = -1
            self.last_frame2_time = time.perf_counter()
            
            # 6) settings changed mid-restart -> do it once more with new values
            if self._snapshot_target_state() != state:
                print("[INFO] Settings changed during restart - reapplying")
                continue
            break
        
        # Re-apply all settings now that no restart is in progress
        self.capture_paused = False
        self.dll_restarting = False
        
        try:
            self._apply_capture_fps_to_dll()
        except Exception:
            pass
        self._push_shader_params_to_dll()
        try:
            self.rebuild_master_mapping()
        except Exception:
            pass
        
        print("[OK] Restart done" if ok else "[ERROR] Restart failed")
    
    def recalc_resolution_stream2(self) -> tuple:
        """Recalculate Stream 2 resolution"""
        w = self._tkget(self.input_target2_w, 0, retries=100)
        h = self._tkget(self.input_target2_h, 0, retries=100)
        
        aspect = self._tkget(self.aspect2, "full", retries=100)
        
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
        w = self._tkget(self.input_target_w, 0, retries=100)
        h = self._tkget(self.input_target_h, 0, retries=100)
        
        aspect = self._tkget(self.aspect1, "full", retries=100)
        
        if aspect == "full":
            return w, h
        
        ratio = self.parse_ratio(aspect)
        return self.compute_aspect_adjusted(w, h, ratio)
    
    def compute_aspect_adjusted(self, w: int, h: int, target_ratio: tuple) -> tuple:
        """Compute resolution with aspect ratio adjustment"""

        idx = self._tkget(self.monitor_index, 0, retries=100)
        if not (0 <= idx < len(self.monitors)):
            idx = 0
        mon = self.monitors[idx]
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
    
    def _debounce_res_s1(self):
        """Debounce for Stream 1 resolution apply (100ms after last keystroke)"""
        if self._res_debounce_s1:
            self.root.after_cancel(self._res_debounce_s1)
        self._res_debounce_s1 = self.root.after(100, self.apply_resolution)
    
    def _debounce_res_s2(self):
        """Debounce for Stream 2 resolution apply (100ms after last keystroke)"""
        if self._res_debounce_s2:
            self.root.after_cancel(self._res_debounce_s2)
        self._res_debounce_s2 = self.root.after(100, self.apply_second_resolution)
    
    def apply_resolution(self):
        """Apply resolution for Stream 1"""
        try:
            w = self.input_target_w.get()
            h = self.input_target_h.get()
        except (tk.TclError, ValueError):
            return
        
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
        
        # The DLL restart (shutdown/init + re-apply of FPS/shaders/stream2)
        # is performed by the single restart worker thread, never from the UI
        self.request_restart(full=False)
        
        # Clear Stream 2 buffer for recreation in capture_loop
        if self.second_stream_enabled.get():
            self.frame_buffer2 = None
        
        self.rebuild_master_mapping()
    
    def apply_second_resolution(self):
        """Apply resolution for Stream 2"""
        try:
            w = self.input_target2_w.get()
            h = self.input_target2_h.get()
        except (tk.TclError, ValueError):
            return
        
        if w <= 0 or h <= 0:
            print("[INFO] Disable second stream")
            # If a restart is in flight, the worker reads the same inputs
            # and applies "no stream 2" right after reinit
            if not self.dll_restarting:
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
        
        if self.dll_restarting:
            # the restart worker will apply the new resolution after reinit
            self.frame_buffer2 = None
            self.rebuild_master_mapping()
            return
        
        with self.dll_lock:
            if self.bridge:
                self.bridge.set_second_resolution(0, 0)
                time.sleep(0.05)
                self.bridge.set_second_resolution(w2, h2)
        
        self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
        
        self.rebuild_master_mapping()
    
    def _init_second_stream(self):
        """Initialize second stream"""
        w2 = self.target2_w.get()
        h2 = self.target2_h.get()
        
        if w2 <= 0 or h2 <= 0:
            return
        
        print(f"[INFO] Init second stream {w2}x{h2}")
        
        if self.dll_restarting:
            print("[INFO] Restart in progress - stream 2 will be applied by the worker")
        else:
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
        # Update WLED list to refresh stream button colors
        self.update_wled_list()
        
        if not self.stream1_enabled:
            print("[INFO] Disable stream1 ONLY")
            
            self.exp_pixel_indices = None
            self.last_ddp_frame = None
            # Reset power to 0 - do not keep the last calculated value while stream is off
            self.led_power_s1 = self._zero_led_power(1)
            self._update_dev_power(1, None)

            for q in [self.capture_queue, self.ddp_queue, self.preview_queue]:
                try:
                    while True:
                        q.get_nowait()
                except Empty:
                    pass
        else:
            # Auto-resume Stream 1 capture (no need to click Apply)
            print("[INFO] Enable stream1 - auto resume capture")
            # Re-initialize capture with current settings
            # (the restart worker performs the actual DLL restart and
            # re-applies the FPS limit afterwards)
            try:
                self.apply_resolution()
            except Exception as e:
                print(f"[WARN] Failed to auto-resume stream1: {e}")
    
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
        # Update WLED list to refresh stream button colors
        self.update_wled_list()
        
        if not self.second_stream_enabled.get():
            print("[INFO] HARD disable second stream (DLL)")
            
            self.preview2_enabled = False
            # Reset power to 0 - do not keep the last calculated value while stream is off
            self.led_power_s2 = self._zero_led_power(2)
            self._update_dev_power(2, None)

            if hasattr(self, "preview2_window") and self.preview2_window is not None:
                try:
                    self.preview2_window.destroy()
                except:
                    pass
                self.preview2_window = None
            
            # Pausing belongs to the restart worker. If a restart is in
            # flight, the worker applies the disabled state after reinit
            if not self.dll_restarting:
                with self.dll_lock:
                    if self.bridge:
                        self.bridge.set_second_resolution(0, 0)
            
            self.frame_buffer2 = None
            
            try:
                while True:
                    self.preview2_queue.get_nowait()
            except Empty:
                pass
        else:
            # Auto-resume Stream 2 capture (no need to click Apply)
            print("[INFO] Enable stream2 - auto resume capture")
            try:
                w2 = self.target2_w.get()
                h2 = self.target2_h.get()
                if w2 > 0 and h2 > 0:
                    if not self.dll_restarting:
                        with self.dll_lock:
                            if self.bridge:
                                self.bridge.set_second_resolution(0, 0)
                                time.sleep(0.05)
                                self.bridge.set_second_resolution(w2, h2)
                    self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
                    self.last_frame2_time = time.perf_counter()
                    self.last_frame2_id = -1
                    self.rebuild_master_mapping()
                else:
                    # No resolution set yet - apply from inputs
                    self.apply_second_resolution()
            except Exception as e:
                print(f"[WARN] Failed to auto-resume stream2: {e}")
    
    def toggle_preview2(self):
        """Stream 2 preview toggle"""
        self.preview2_enabled = not self.preview2_enabled

    def _on_preview_window_closed(self, stream):
        """Окно превью закрыто крестиком в заголовке.

        Вызывается из потока превью (preview_s1/preview_s2): флаг превью
        уже выключен потоком, здесь только сбрасываем счётчик кадров и
        логируем событие (эквивалент нажатия кнопки Preview).
        """
        try:
            if stream == 1:
                self.preview_count = 0
            else:
                self.preview2_count = 0
            print(f"[INFO] Preview window closed via titlebar X (stream {stream})")
        except Exception:
            pass
    
    def open_global_calibration(self):
        """Open Stream 1 calibration window"""
        open_calibration_stream1(self, self.global_calibration)
    
    def open_shader_optimizer(self):
        """Open shader optimization window"""
        open_optimization_window(self)

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
            if len(self.saved_custom_gamma_mono1) == 64:
                self.custom_gamma_mono1[:] = self.saved_custom_gamma_mono1[:64]

            if len(self.saved_custom_gamma_sdr_r2) == 64:
                self.custom_gamma_sdr_r2[:] = self.saved_custom_gamma_sdr_r2[:64]
                self.custom_gamma_sdr_g2[:] = self.saved_custom_gamma_sdr_g2[:64]
                self.custom_gamma_sdr_b2[:] = self.saved_custom_gamma_sdr_b2[:64]
            if len(self.saved_custom_gamma_mono2) == 64:
                self.custom_gamma_mono2[:] = self.saved_custom_gamma_mono2[:64]

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
            if len(self.custom_gamma_mono1) == 64:
                self.saved_custom_gamma_mono1[:] = self.custom_gamma_mono1[:64]

            if len(self.custom_gamma_sdr_r2) == 64:
                self.saved_custom_gamma_sdr_r2[:] = self.custom_gamma_sdr_r2[:64]
                self.saved_custom_gamma_sdr_g2[:] = self.custom_gamma_sdr_g2[:64]
                self.saved_custom_gamma_sdr_b2[:] = self.custom_gamma_sdr_b2[:64]
            if len(self.custom_gamma_mono2) == 64:
                self.saved_custom_gamma_mono2[:] = self.custom_gamma_mono2[:64]

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

                # Reset for both Stream 1 and Stream 2 (RGB + mono)
                self.custom_gamma_sdr_r1[i] = val
                self.custom_gamma_sdr_g1[i] = val
                self.custom_gamma_sdr_b1[i] = val
                self.custom_gamma_mono1[i] = val
                self.custom_gamma_sdr_r2[i] = val
                self.custom_gamma_sdr_g2[i] = val
                self.custom_gamma_sdr_b2[i] = val
                self.custom_gamma_mono2[i] = val
    
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
    
        # Button states are now managed per-device in update_wled_list()
    
    # =========================
    # PER-MODULE SEND MODE (LIVE / PAUSE / OFF)
    # =========================
    
    def cycle_dev_mode(self, dev):
        """Cycle module mode: LIVE (stream) -> PAUSE (last frame) -> OFF (no live)"""
        order = ("stream", "pause", "off")
        cur = dev.get("mode", "stream")
        if cur not in order:
            cur = "stream"
        new = order[(order.index(cur) + 1) % len(order)]
        self.set_dev_mode(dev, new)
        self.update_wled_list()
    
    def set_dev_mode(self, dev, mode):
        """Apply a send mode to one WLED module.
        
        stream - live mode: new frames are sent to this module;
        pause  - sending to this module is suspended, the WLED keeps
                 displaying the last received frame;
        off    - live mode is disabled on the WLED (live: False).
        """
        ip = dev.get("ip")
        if not ip:
            return
        
        if mode not in ("stream", "pause", "off"):
            mode = "stream"
        
        dev["mode"] = mode
        self.dev_modes[ip] = mode
        if HAS_SACN:
            sacn_device_modes[ip] = mode
        
        try:
            if mode == "off":
                # Disable live mode on the WLED (broadcast stopped)
                restore_wled(ip)
                # Apply the module's stored OFF-mode color (real-time push)
                self._push_off_color(ip)
            elif mode == "stream" and dev.get("mapping"):
                # Re-enable live mode (WLED will show the last received frame)
                set_wled_ddp_mode(ip, keep_last_frame=True)
            # "pause": WLED stays in live mode, simply stops receiving new data
        except Exception as e:
            print(f"[WARN] Failed to apply mode '{mode}' to WLED {ip}: {e}")
        
        print(f"[INFO] Module {ip} mode: {mode}")
    
    def _update_sacn_managers(self):
        """Update E1.31 stream managers with current device mappings"""
        # Rebuild mapping first to get latest state
        self.rebuild_master_mapping()
        
        if self.sacn_manager1:
            self.sacn_manager1.devices.clear()
            for dev in self.device_slices:
                self.sacn_manager1.add_device(
                    ip=dev["ip"],
                    start=dev["start"],
                    end=dev["end"],
                    stream=1
                )
        
        if self.sacn_manager2:
            self.sacn_manager2.devices.clear()
            for dev in self.device_slices2:
                self.sacn_manager2.add_device(
                    ip=dev["ip"],
                    start=dev["start"],
                    end=dev["end"],
                    stream=2
                )
        
        # Sync per-module send modes into the sACN registry
        if HAS_SACN:
            for d in WLED_DEVICES:
                sacn_device_modes[d["ip"]] = d.get("mode", "stream")
    
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
            self._refresh_wled_combo_display()
            
            print("[INFO] Found:", results)
        
        threading.Thread(target=run_async, daemon=True).start()
    
    def _refresh_wled_combo_display(self):
        """Update the WLED search combo to show [ADDED] marker for devices already in WLED_DEVICES"""
        if not self.wled_discovered:
            return
        
        added_ips = {dev["ip"] for dev in WLED_DEVICES}
        updated_values = []
        
        for entry in self.wled_discovered:
            # entry looks like "192.168.1.100 (My WLED)"
            ip = entry.split(" ")[0]
            if ip in added_ips:
                updated_values.append(f"{entry}  ✓ ADDED")
            else:
                # Strip any stale "✓ ADDED" marker
                cleaned = entry.replace("  ✓ ADDED", "").rstrip()
                updated_values.append(cleaned)
        
        self.wled_ip_combo["values"] = updated_values
        
        # Preserve current selection if possible
        current_val = self.wled_ip_var.get().strip()
        if current_val:
            for idx, v in enumerate(updated_values):
                if v.startswith(current_val.split(" ")[0]):
                    self.wled_ip_combo.current(idx)
                    break
        elif updated_values:
            self.wled_ip_combo.current(0)
    
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
            "online": False,  # Default status
            "protocol": "DDP",  # Default protocol
        }
        
        WLED_DEVICES.append(new_device)
        
        self.rebuild_master_mapping()
        self.update_wled_list()
        self._refresh_wled_combo_display()
        
        print(f"WLED ADDED: {ip} ({name}) - waiting for mapping")
    
    def load_mapping_for_device(self, index: int):
        """Load mapping for device and switch to DDP mode if not already in it"""
        if index >= len(WLED_DEVICES):
            return
        
        mapping = load_mapping_file()
        if mapping is None:
            return
        
        dev = WLED_DEVICES[index]
        
        dev["mapping"] = mapping
        dev["length"] = len(mapping)
        
        self.rebuild_master_mapping()
        self.update_wled_list()
        
        # Switch WLED to live mode (accepts external input via DDP or sACN),
        # EXCEPT when the module is in OFF mode — then keep live disabled and
        # simply re-send the stored color to the WLED via the JSON API.
        ip = dev.get("ip")
        if ip:
            if dev.get("mode", "stream") == "off":
                # OFF: disable live mode + re-apply the module's stored color
                restore_wled(ip)
                self._push_off_color(ip)
                print(f"[OK] Mapping loaded for {ip} ({len(mapping)} LEDs) - "
                      f"OFF mode, stored color re-sent via API")
            else:
                set_wled_ddp_mode(ip, keep_last_frame=True)
                print(f"[OK] Mapping loaded for {ip} ({len(mapping)} LEDs) - live mode ON")
        
        # CRITICAL FIX: If E1.31 sACN protocol is active, update the sACN managers
        # so the new device is included in sACN send loops.
        # Without this, the new device won't receive any frames from sACN loops,
        # resulting in black pixels until protocol is toggled.
        if self.current_protocol.get() == "E1.31":
            self._update_sacn_managers()
            print(f"[OK] E1.31 sACN managers updated with new device {ip}")
        
    
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
                
                # Return the color selected in settings (stored OFF-mode
                # color of this module, 10,10,10 only if never set)
                self._send_off_color_sync(ip)
            
            except Exception as e:
                print("[TEST ERROR]", e)
        
        threading.Thread(target=run, daemon=True).start()
    
    # =========================
    # PER-MODULE OFF-MODE COLOR (picker window + real-time WLED push)
    # =========================
    
    def _get_off_color(self, ip):
        """Return (r, g, b, bri) 0-255 stored for the module; default (10,10,10,255)."""
        c = (getattr(self, "wled_off_colors", {}) or {}).get(ip)
        if not c:
            return (10, 10, 10, 255)
        return (c["r"], c["g"], c["b"], c["bri"])
    
    def _send_off_color_to_wled(self, ip: str, r: int, g: int, b: int, bri: int):
        """POST the color state to the WLED JSON API (off-mode: live disabled).

        Хранимые (r, g, b) — итоговый цвет. WLED сам масштабирует цвет
        яркостью (color × bri / 255), поэтому отправляем базовый цвет
        base = final × 255 / bri — яркость просто делит тензор цвета,
        двойного затемнения нет.
        """
        bcl = max(1, min(255, int(bri)))
        col = [int(round(min(255.0, c * 255.0 / bcl))) for c in (r, g, b)]
        payload = {
            "on": True,
            "bri": max(0, min(255, int(bri))),
            "live": False,
            "seg": [{
                "id": 0,
                "fx": 0,
                "col": [col]
            }]
        }
        req = urllib.request.Request(
            f"http://{ip}/json/state",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=2)
    
    def _push_off_color(self, ip: str):
        """Send the stored OFF-mode color of one module to the WLED, in real time.
        
        Throttled: fast consecutive changes collapse into the latest value,
        so the last state always reaches the device. Runs in a background thread.
        """
        r, g, b, bri = self._get_off_color(ip)
        if not hasattr(self, "_off_color_send_state"):
            self._off_color_send_state = {}
        st = self._off_color_send_state.setdefault(ip, {"busy": False, "pending": None})
        st["pending"] = (r, g, b, bri)
        if st["busy"]:
            return  # worker will pick up the latest value
        st["busy"] = True
        
        def worker():
            try:
                while True:
                    p = st["pending"]
                    if p is None:
                        break
                    st["pending"] = None
                    try:
                        self._send_off_color_to_wled(ip, *p)
                        print(f"[OK] OFF-mode color applied to WLED {ip}: "
                              f"rgb({p[0]},{p[1]},{p[2]}) bri={p[3]}")
                    except Exception as e:
                        print(f"[COLOR ERROR] Failed to set OFF color on WLED {ip}: {e}")
            finally:
                st["busy"] = False
        
        threading.Thread(target=worker, daemon=True).start()
    
    def _send_off_color_sync(self, ip: str):
        """Send the stored (settings) OFF-mode color to the WLED synchronously.

        Used on situations where the color must be guaranteed to reach the
        device (test finished, module removed, application closing) — unlike
        _push_off_color, this runs in the current thread, so the send is
        complete before the caller continues (e.g. process exit).
        """
        try:
            r, g, b, bri = self._get_off_color(ip)
            self._send_off_color_to_wled(ip, r, g, b, bri)
            print(f"[OK] Settings color applied to WLED {ip}: "
                  f"rgb({r},{g},{b}) bri={bri}")
        except Exception as e:
            print(f"[COLOR ERROR] Failed to set OFF color on WLED {ip}: {e}")
    
    def _iter_all_wled_ips(self):
        """Yield unique IPs of every known WLED module (devices, stored
        colors, open color windows) without duplicates."""
        seen = set()
        for d in WLED_DEVICES:
            ip = d.get("ip")
            if ip and ip not in seen:
                seen.add(ip)
                yield ip
        for ip in (getattr(self, "wled_off_colors", {}) or {}):
            if ip not in seen:
                seen.add(ip)
                yield ip
        for ip in (getattr(self, "wled_color_windows", {}) or {}):
            if ip not in seen:
                seen.add(ip)
                yield ip
    
    def _apply_settings_color_to_all_wled(self):
        """Push the stored (settings) color to EVERY WLED module.

        Called on app exit / removal / any situation where the device must
        end up in the user-selected state.
        """
        for ip in self._iter_all_wled_ips():
            try:
                restore_wled(ip)  # normal mode, live off
            except Exception:
                pass
            self._send_off_color_sync(ip)
    
    def _on_wled_color_change(self, ip: str, r: int, g: int, b: int, bri: int):
        """Real-time callback from the per-module color picker window."""
        self.wled_off_colors[ip] = {"r": int(r), "g": int(g), "b": int(b), "bri": int(bri)}
        dev = next((d for d in WLED_DEVICES if d.get("ip") == ip), None)
        mode = dev.get("mode", "stream") if dev else "stream"
        win = (getattr(self, "wled_color_windows", {}) or {}).get(ip)
        if mode == "off":
            # OFF mode: the color reaches the WLED immediately
            self._push_off_color(ip)
            msg, fg = "Applied to WLED (OFF)", self.colors.get("success", "#73daca")
        else:
            # other modes: stored, will be applied when OFF mode is enabled
            msg, fg = "Stored — applies on OFF", self.colors.get("text_dim", "#565f89")
        if win is not None:
            try:
                win.status_lbl.config(text=msg, fg=fg)
            except Exception:
                pass
    
    def apply_color_to_all_wled(self, r: int, g: int, b: int, bri: int,
                               exclude_ip=None):
        """'Apply to All' from the color window: store the color for all
        WLED modules, update all open color picker windows (except the
        one the command came from) and push to OFF-mode modules now."""
        ips = []
        for d in WLED_DEVICES:
            ip = d.get("ip")
            if ip and ip not in ips:
                ips.append(ip)
        for ip in (getattr(self, "wled_color_windows", {}) or {}):
            if ip not in ips:
                ips.append(ip)
        for ip in ips:
            self.wled_off_colors[ip] = {"r": int(r), "g": int(g), "b": int(b), "bri": int(bri)}
            dev = next((d for d in WLED_DEVICES if d.get("ip") == ip), None)
            mode = dev.get("mode", "stream") if dev else "stream"
            if mode == "off":
                self._push_off_color(ip)
            if ip == exclude_ip:
                continue  # the source window is already synced itself
            win = (getattr(self, "wled_color_windows", {}) or {}).get(ip)
            if win is not None:
                try:
                    win.set_color((int(r), int(g), int(b)), int(bri))
                    win.status_lbl.config(
                        text="Applied from another window",
                        fg=self.colors.get("text_dim", "#777c9e"))
                except Exception:
                    pass
        print(f"[OK] Color rgb({r},{g},{b}) bri={bri} "
              f"applied to all {len(ips)} WLED modules")

    def close_all_wled_color_windows(self):
        """Close ALL open WLED module color picker windows."""
        for i, w in list((getattr(self, "wled_color_windows", {}) or {}).items()):
            try:
                if w is not None and w.winfo_exists():
                    w.destroy()
            except Exception:
                pass
        try:
            self.wled_color_windows.clear()
        except Exception:
            pass

    def open_wled_color_window(self, dev):
        """Open the individual color picker window for one WLED module.

        Only ONE color window can be open at a time:
        if another module's window is open — it is closed.
        """
        ip = dev.get("ip")
        if not ip or not WLED_COLOR_WINDOW_AVAILABLE:
            return

        # Re-focus an already open window for this module (one window per module)
        existing = (getattr(self, "wled_color_windows", {}) or {}).get(ip)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
            self.wled_color_windows.pop(ip, None)

        # Close any other module's color window — only one window stays open
        self.close_all_wled_color_windows()
        
        stored = self.wled_off_colors.get(ip)
        if stored:
            init_rgb = (stored["r"], stored["g"], stored["b"])
            init_bri = stored["bri"]
        else:
            init_rgb = (10, 10, 10)
            init_bri = 255
        
        win = WLEDColorWindow(
            self.root,
            ip,
            initial_rgb=init_rgb,
            initial_bri=init_bri,
            default_rgb=init_rgb,  # the color selected in settings = "Default"
            colors=self.colors,
            on_color_change=lambda r, g, b, bri, ip=ip: self._on_wled_color_change(ip, r, g, b, bri),
            on_apply_all=lambda r, g, b, bri, ip=ip: self.apply_color_to_all_wled(r, g, b, bri, exclude_ip=ip)
        )
        
        def _on_close(i=ip, w=win):
            try:
                if self.wled_color_windows.get(i) is w:
                    del self.wled_color_windows[i]
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass
        
        win.protocol("WM_DELETE_WINDOW", _on_close)
        self.wled_color_windows[ip] = win
    def _send_sacn_frame(self, ip: str, rgb_data: bytes, led_count: int, seq_counters: dict):
        """Send frame via E1.31 sACN protocol
        
        Args:
            ip: Device IP
            rgb_data: RGB data bytes
            led_count: Number of LEDs
            seq_counters: Dictionary of sequence counters per device
        """
        if not HAS_SACN:
            return
        
        sock = get_sacn_socket()
        if not sock:
            return
        
        universe_count = (led_count + LEDS_PER_UNIVERSE - 1) // LEDS_PER_UNIVERSE
        seq = seq_counters.get(ip, 0)
        destination = (ip, SACN_PORT)
        
        try:
            for uni_index in range(universe_count):
                led_start = uni_index * LEDS_PER_UNIVERSE
                led_end = min(led_start + LEDS_PER_UNIVERSE, led_count)
                if led_start >= led_count:
                    break
                
                channel_start = uni_index * CHANNELS_PER_UNIVERSE
                channel_end = min(channel_start + CHANNELS_PER_UNIVERSE, len(rgb_data))
                if channel_start >= len(rgb_data):
                    break
                
                universe_data = rgb_data[channel_start:channel_end]
                universe_num = START_UNIVERSE + uni_index
                
                packet = build_sacn_packet(
                    universe=universe_num,
                    data=universe_data,
                    sequence=seq
                )
                sock.sendto(packet, destination)
            
            seq_counters[ip] = (seq + 1) & 0xFF
        except Exception as e:
            print(f"[ERROR] sACN send failed for {ip}: {e}")
    
    def ddp_send_loop(self):
        """Unified send loop for Stream 1 - supports both DDP and E1.31 sACN per device.
        
        Each device can independently use DDP or E1.31 sACN protocol.
        The loop consumes frames from the queue and routes them to each device
        using its configured protocol.
        """
        # sACN sequence counters per device
        sacn_seq_counters = {}
        
        while self.running:
            if not self.stream1_enabled:
                time.sleep(0.01)
                continue
            
            try:
                frame = self.ddp_queue.get(timeout=0.5)
            except Empty:
                # Keepalive - resend last frame to all devices
                if (
                    self.last_ddp_frame is not None
                    and self.streaming_enabled
                    and self.stream1_enabled
                ):
                    frame_view = memoryview(self.last_ddp_frame)
                    
                    for dev in self.device_slices:
                        # Skip paused / off modules - they freeze on the last frame
                        if self.dev_modes.get(dev["ip"], "stream") != "stream":
                            continue
                        start = dev["start"] * 3
                        end = dev["end"] * 3
                        chunk = bytes(frame_view[start:end])
                        
                        protocol = dev.get("protocol", "DDP")
                        led_count = dev["end"] - dev["start"]
                        
                        if protocol == "DDP":
                            send_ddp(dev["ip"], chunk)
                        elif protocol == "E1.31":
                            self._send_sacn_frame(dev["ip"], chunk, led_count, sacn_seq_counters)
                    
                    continue
                
                time.sleep(0.01)
                continue
            
            if not self.streaming_enabled or not self.stream1_enabled:
                continue
            
            send_start = time.perf_counter()
            
            frame_view = memoryview(frame)
            
            ddp_sent = False
            sacn_sent = False
            
            # Only modules in "stream" (LIVE) mode receive new frames
            ddp_devices = [dev for dev in self.device_slices
                           if dev.get("protocol", "DDP") == "DDP"
                           and self.dev_modes.get(dev["ip"], "stream") == "stream"]
            sacn_devices = [dev for dev in self.device_slices
                            if dev.get("protocol", "DDP") == "E1.31"
                            and self.dev_modes.get(dev["ip"], "stream") == "stream"]
            
            # Send DDP frames
            ddp_send_start = time.perf_counter()
            for dev in ddp_devices:
                start = dev["start"] * 3
                end = dev["end"] * 3
                chunk = bytes(frame_view[start:end])
                send_ddp(dev["ip"], chunk)
                ddp_sent = True
            if ddp_devices:
                self.ddp_delay_ms = (time.perf_counter() - ddp_send_start) * 1000
            
            # Send sACN frames
            sacn_send_start = time.perf_counter()
            for dev in sacn_devices:
                start = dev["start"] * 3
                end = dev["end"] * 3
                chunk = bytes(frame_view[start:end])
                led_count = dev["end"] - dev["start"]
                self._send_sacn_frame(dev["ip"], chunk, led_count, sacn_seq_counters)
                sacn_sent = True
            if sacn_devices:
                self.sacn_delay_ms = (time.perf_counter() - sacn_send_start) * 1000
            
            self.last_ddp_frame = frame
            
            if ddp_sent:
                self.ddp_frame_count += 1
            if sacn_sent:
                self.sacn1_frame_count += 1
            self.last_ddp_frame_time = time.perf_counter()
    
    def ddp2_send_loop(self):
        """Unified send loop for Stream 2 - supports both DDP and E1.31 sACN per device.
        
        Each device can independently use DDP or E1.31 sACN protocol.
        The loop consumes frames from the queue and routes them to each device
        using its configured protocol.
        """
        # sACN sequence counters per device
        sacn_seq_counters = {}
        
        while self.running:
            if not self.stream2_enabled:
                time.sleep(0.01)
                continue
            
            try:
                frame = self.ddp2_queue.get(timeout=0.5)
            
            except Empty:
                # Keepalive - resend last frame to all devices
                if (
                    self.last_ddp2_frame is not None
                    and self.streaming2_enabled
                    and self.stream2_enabled
                ):
                    frame_view = memoryview(self.last_ddp2_frame)
                    
                    for dev in self.device_slices2:
                        # Skip paused / off modules - they freeze on the last frame
                        if self.dev_modes.get(dev["ip"], "stream") != "stream":
                            continue
                        start = dev["start"] * 3
                        end = dev["end"] * 3
                        chunk = bytes(frame_view[start:end])
                        
                        protocol = dev.get("protocol", "DDP")
                        led_count = dev["end"] - dev["start"]
                        
                        if protocol == "DDP":
                            send_ddp(dev["ip"], chunk)
                        elif protocol == "E1.31":
                            self._send_sacn_frame(dev["ip"], chunk, led_count, sacn_seq_counters)
                    
                    continue
                
                time.sleep(0.01)
                continue
            
            if not self.streaming2_enabled or not self.stream2_enabled:
                 continue
            
            send_start = time.perf_counter()
            
            frame_view = memoryview(frame)
            
            ddp2_sent = False
            sacn2_sent = False
            
            # Only modules in "stream" (LIVE) mode receive new frames
            ddp_devices2 = [dev for dev in self.device_slices2
                            if dev.get("protocol", "DDP") == "DDP"
                            and self.dev_modes.get(dev["ip"], "stream") == "stream"]
            sacn_devices2 = [dev for dev in self.device_slices2
                             if dev.get("protocol", "DDP") == "E1.31"
                             and self.dev_modes.get(dev["ip"], "stream") == "stream"]
            
            # Send DDP frames
            ddp_send_start2 = time.perf_counter()
            for dev in ddp_devices2:
                start = dev["start"] * 3
                end = dev["end"] * 3
                chunk = bytes(frame_view[start:end])
                send_ddp(dev["ip"], chunk)
                ddp2_sent = True
            if ddp_devices2:
                self.ddp2_delay_ms = (time.perf_counter() - ddp_send_start2) * 1000
            
            # Send sACN frames
            sacn_send_start2 = time.perf_counter()
            for dev in sacn_devices2:
                start = dev["start"] * 3
                end = dev["end"] * 3
                chunk = bytes(frame_view[start:end])
                led_count = dev["end"] - dev["start"]
                self._send_sacn_frame(dev["ip"], chunk, led_count, sacn_seq_counters)
                sacn2_sent = True
            if sacn_devices2:
                self.sacn_delay_ms = (time.perf_counter() - sacn_send_start2) * 1000
            
            self.last_ddp2_frame = frame
            
            if ddp2_sent:
                self.ddp2_frame_count += 1
            if sacn2_sent:
                self.sacn2_frame_count += 1
            self.last_ddp2_frame_time = time.perf_counter()
    
    def remove_wled_device(self, index: int):
        """Remove WLED device"""
        if index < 0 or index >= len(WLED_DEVICES):
            return
        
        dev = WLED_DEVICES[index]
        ip = dev["ip"]
        
        try:
            restore_wled(dev["ip"])
        except:
            pass
        
        # Set the color selected in settings on the module before removal,
        # so the device keeps the user's chosen color (not the fixed 10,10,10)
        self._send_off_color_sync(ip)
        
        WLED_DEVICES.pop(index)
        
        if ip in self.dev_modes:
            del self.dev_modes[ip]
        if HAS_SACN and ip in sacn_device_modes:
            del sacn_device_modes[ip]
        
        self.rebuild_master_mapping()
        
        # CRITICAL FIX: If E1.31 sACN protocol is active, update the sACN managers
        # to remove the device from sACN send loops.
        if self.current_protocol.get() == "E1.31":
            self._update_sacn_managers()
            print(f"[OK] E1.31 sACN managers updated after removing {ip}")
        
        self.mapping_fps.clear()
        self.mapping_counts.clear()
        
        self.update_wled_list()
        self._refresh_wled_combo_display()
        
        print("REMOVED:", ip)
    
    def update_wled_list(self):
        """Update WLED device list in UI"""
        # Clear old widgets
        for widget in self.wled_frame.winfo_children():
            widget.destroy()
        
        colors = self.colors
        
        # Check device count - if more than 6, set fixed height and enable scrolling
        # Max height scales with screen resolution:
        #   1024px screen → 150px max, 2048px screen → 300px max
        device_count = len(WLED_DEVICES)
        screen_height = self.root.winfo_screenheight()
        max_panel_height = int(screen_height * 150 / 1024)
        if max_panel_height < 100:
            max_panel_height = 100
        
        if device_count > 6:
            # Show max 6 rows with scrolling, capped at max_panel_height
            self.wled_canvas.configure(height=max_panel_height, yscrollincrement=55)
            self.wled_canvas.config(scrollregion=self.wled_canvas.bbox("all"))
        else:
            # Canvas takes space needed but not more than max_panel_height
            needed = device_count * 55 + 10 if device_count > 0 else 20
            self.wled_canvas.configure(height=min(needed, max_panel_height))
        
        for i, dev in enumerate(WLED_DEVICES):
            row = tk.Frame(self.wled_frame, bg=colors["bg"])
            row.pack(fill="x", pady=(2, 0))
            
            name = dev.get("name", "WLED")
            led_info = f"{dev['length']} LEDs" if dev["mapping"] else "no mapping"
            # Reflect the per-module send mode in the status text
            _mode = dev.get("mode", "stream")
            _mode_names = {"stream": "LIVE", "pause": "PAUSE", "off": "OFF"}
            status = _mode_names.get(_mode, "LIVE") if dev["mapping"] else "Waiting"

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

            # Real power consumption label for THIS module only
            # (per-crystal brightness after mapping, live-updated in update_gui_fps)
            _p0 = (self.wled_dev_power or {}).get(dev["ip"])
            power_label = tk.Label(
                row,
                text=f"{_p0['current_ma'] / 1000.0:.2f}A / {_p0['power_w']:.1f}W"
                if _p0 else "0.00A / 0.0W",
                font=("Consolas", 9),
                bg=colors["bg"],
                fg=colors["text_dim"]
            )
            power_label.pack(side="left", padx=(0, 12))
            if not hasattr(self, "wled_dev_power_labels"):
                self.wled_dev_power_labels = []
            else:
                self.wled_dev_power_labels = [
                    (ip, lbl) for ip, lbl in self.wled_dev_power_labels
                    if lbl.winfo_exists()
                ]
            self.wled_dev_power_labels.append((dev["ip"], power_label))
            
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
            
            # Protocol selector — INDEPENDENT per device (DDP / E1.31)
            if HAS_SACN:
                device_proto = dev.get("protocol", "DDP")
                proto_btn_text = "⚡ DDP" if device_proto == "DDP" else "📡 E1.31"
                
                def _toggle_proto(d_ip=dev["ip"]):
                    for d in WLED_DEVICES:
                        if d["ip"] == d_ip:
                            cur = d.get("protocol", "DDP")
                            d["protocol"] = "E1.31" if cur == "DDP" else "DDP"
                            print(f"[INFO] Device {d_ip} protocol: {cur} -> {d['protocol']}")
                            break
                    self.rebuild_master_mapping()
                    self.update_wled_list()
                
                proto_btn = tk.Button(
                    row,
                    text=proto_btn_text,
                    font=("Segoe UI", 8),
                    bg=colors["accent"],
                    fg="#1a1b26",
                    bd=0,
                    command=_toggle_proto,
                    cursor="hand2",
                    relief="flat"
                )
                proto_btn.pack(side="right", padx=(2, 6))
            
            create_button(row, "🧪 Test", lambda d=dev: self.test_wled_device(d["ip"], d["length"]))
            create_button(row, "🎨 Color", lambda d=dev: self.open_wled_color_window(d))
            create_button(row, "📂 Load Mapping", lambda i=i: self.load_mapping_for_device(i))
            create_button(row, "🗑 Delete", lambda i=i: self.remove_wled_device(i))
            
            # === Three-position mode button: LIVE / PAUSE / OFF ===
            # Packed right AFTER the "Stream 1/2" button (visually to its right).
            # Cycle on click: LIVE (stream) -> PAUSE (freeze last frame) -> OFF (no live)
            _dev_mode = dev.get("mode", "stream")
            _mode_btn_cfg = {
                "stream": ("▶ LIVE", colors["success"]),
                "pause":  ("⏸ PAUSE", colors["warning"]),
                "off":    ("⏻ OFF", colors["error"]),
            }
            mode_text, mode_fg = _mode_btn_cfg.get(_dev_mode, _mode_btn_cfg["stream"])
            mode_btn = tk.Button(
                row,
                text=mode_text,
                font=("Segoe UI", 8),
                bg=colors["panel_bg"],
                fg=mode_fg,
                bd=0,
                command=lambda d=dev: self.cycle_dev_mode(d),
                cursor="hand2",
                relief="flat"
            )
            mode_btn.pack(side="right", padx=(2, 6))
            
            def toggle_stream_cmd(dev=dev):
                idx = WLED_DEVICES.index(dev)
                self.toggle_stream(idx)

            # Determine stream color based on whether the assigned stream is active
            dev_stream = dev.get('stream', 1)
            if dev_stream == 1:
                stream_active = self.stream1_enabled
            else:
                stream_active = self.stream2_enabled

            btn_text = f"Stream {dev_stream}"
            stream_fg = colors["success"] if stream_active else colors["error"]

            stream_btn = tk.Button(
                row,
                text=btn_text,
                font=("Segoe UI", 8),
                bg=colors["panel_bg"],
                fg=stream_fg,
                bd=0,
                command=toggle_stream_cmd,
                cursor="hand2",
                relief="flat"
            )
            stream_btn.pack(side="right", padx=(2, 6))
    
    def _update_dev_power(self, stream: int, mapped_rgb_full):
        """Реальное потребление каждого WLED-модуля по яркости кристаллов
        после маппинга (то же _compute_led_power, что и в Power-панели).

        mapped_rgb_full — (N_LEDS, 3) float 0.0-1.0 в порядке master-mapping
        (без online-маски), либо None — обнулить модули этого стрима.
        """
        res = {}
        if mapped_rgb_full is not None and len(mapped_rgb_full) > 0:
            # Порядок строк = порядок WLED_DEVICES с маппингом в этом стриме
            total = sum(len(dev["mapping"]) for dev in WLED_DEVICES
                        if dev["mapping"] and dev.get("stream", 1) == stream)
            if total == len(mapped_rgb_full):
                pos = 0
                for dev in WLED_DEVICES:
                    if dev["mapping"] and dev.get("stream", 1) == stream:
                        n = len(dev["mapping"])
                        if dev.get("online"):
                            p = self._compute_led_power(
                                mapped_rgb_full[pos:pos + n], stream
                            )
                            res[dev["ip"]] = {
                                "current_ma": float(p["current_ma"]),
                                "power_w": float(p["power_w"]),
                            }
                        pos += n
        self.wled_dev_power = self._merge_dev_power(stream, res)

    def _merge_dev_power(self, stream: int, new_values: dict) -> dict:
        """Слить обновление wled_dev_power для одного стрима."""
        cur = dict(getattr(self, "wled_dev_power", {}) or {})
        if not hasattr(self, "_wled_dev_power_streams"):
            self._wled_dev_power_streams = {}
        for ip in [ip for ip, s in self._wled_dev_power_streams.items() if s == stream]:
            cur.pop(ip, None)
        for ip, v in new_values.items():
            cur[ip] = v
            self._wled_dev_power_streams[ip] = stream
        return cur

    # Residual per-crystal brightness used when a WLED module is in "OFF" mode:
    # the module stops receiving live data, its consumption is estimated as
    # the controller (IC) draw + the color selected in the per-module
    # color picker (default 10/10/10 if never set).
    OFF_MODE_BRIGHTNESS = 10.0 / 255.0

    def _mode_adjusted_rgb(self, mapped_rgb, stream: int) -> np.ndarray:
        """
        Build the per-LED brightness array (N, 3) float 0.0-1.0 (master-mapping
        order) taking the per-module send mode of every WLED into account:

          - "stream" (LIVE): the live frame for this module's crystals;
          - "pause"  (PAUSE): the frozen last frame — a snapshot of this
            module's crystals taken on the first frame after the module was
            paused (the WLED keeps displaying exactly this frame);
          - "off"    (OFF):  only the IC consumption + the color selected
            in the per-module color picker (default 10/10/10) on all
            crystals.

        The result feeds the per-module power (wled_dev_power), the global
        Power-panel value (led_power_sN) and the Temp Map (thermal_frame_sN).
        """
        out = np.asarray(mapped_rgb, dtype=np.float32)
        if out.ndim == 1:
            out = out.reshape(-1, 3)
        out = out.copy()

        frozen = getattr(self, "_dev_frozen_rgb", None)
        if frozen is None:
            frozen = self._dev_frozen_rgb = {}
        if not hasattr(self, "_dev_frozen_streams"):
            self._dev_frozen_streams = {}

        pos = 0
        current_ips = set()
        for dev in WLED_DEVICES:
            if not dev["mapping"] or dev.get("stream", 1) != stream:
                continue
            ip = dev.get("ip")
            n = len(dev["mapping"])
            rows = out[pos:pos + n]
            mode = dev.get("mode", "stream")
            if ip is not None:
                current_ips.add(ip)
            if mode == "off":
                # OFF: IC (MCU) draw + the color selected in the per-module
                # color picker (kept instead of black; 10/10/10 only if never
                # set). The stored (r, g, b) is ALREADY the final color the
                # WLED displays (base x bri / 255), so use it directly as the
                # per-channel brightness. The separate "bri" must NOT be
                # applied again — doing so dimmed the estimate twice and
                # under-reported the crystals' consumption at low brightness.
                if rows.size:
                    col = self._get_off_color(ip)
                    rows[:, 0] = col[0] / 255.0
                    rows[:, 1] = col[1] / 255.0
                    rows[:, 2] = col[2] / 255.0
            elif mode == "pause" and ip is not None:
                # PAUSE: keep the last frame the WLED is frozen on
                snap = frozen.get(ip)
                if snap is None or snap.shape[0] != n:
                    snap = rows.copy()
                    frozen[ip] = snap
                if rows.size:
                    rows[:] = snap
                self._dev_frozen_streams[ip] = stream
            else:
                # LIVE: a stale snapshot is no longer needed
                if ip in frozen:
                    frozen.pop(ip, None)
            pos += n

        # Drop snapshots of modules that no longer belong to this stream
        # (removed from the list or moved to the other stream)
        for ip in [ip for ip, s in self._dev_frozen_streams.items()
                   if s == stream and ip not in current_ips]:
            frozen.pop(ip, None)
            self._dev_frozen_streams.pop(ip, None)
        return out

    def rebuild_master_mapping(self):
        """Rebuild master mapping"""
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
                "end": start + dev["length"],
                "protocol": dev.get("protocol", "DDP"),  # Per-device protocol
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

        self.streaming_enabled = len(self.device_slices) > 0
        self.streaming2_enabled = len(self.device_slices2) > 0
        
        # If no WLED modules are left in a stream, reset its power to 0
        # (otherwise the last calculated value would stay visible forever)
        if not self.streaming_enabled:
            self.led_power_s1 = self._zero_led_power(1)
            self._update_dev_power(1, None)
        if not self.streaming2_enabled:
            self.led_power_s2 = self._zero_led_power(2)
            self._update_dev_power(2, None)

        # Rebuild per-module send mode registry from WLED_DEVICES
        self.dev_modes = {d["ip"]: d.get("mode", "stream") for d in WLED_DEVICES}
        if HAS_SACN:
            for ip, m in self.dev_modes.items():
                sacn_device_modes[ip] = m
        
        # Refresh online-LED masks for power calculation
        self._refresh_online_masks()

        # Rebuild thermal models with the new LED positions after mapping
        for s in (1, 2):
            self._rebuild_thermal(s)
    
    def _refresh_online_masks(self):
        """
        Rebuild boolean masks of ONLINE WLED device LEDs, aligned to the rows of
        exp_pixel_indices / exp_pixel_indices2 (same order as rebuild_master_mapping).
        Used to calculate power only for devices currently shown as Online in the status list.
        """
        for stream, idx_attr, mask_attr in ((1, "exp_pixel_indices", "online_mask1"),
                                            (2, "exp_pixel_indices2", "online_mask2")):
            idx = getattr(self, idx_attr, None)
            if idx is None or len(idx) == 0:
                setattr(self, mask_attr, None)
                continue
            
            # Same order as rebuild_master_mapping: concatenated mappings of this stream's devices
            total = sum(len(dev["mapping"]) for dev in WLED_DEVICES
                        if dev["mapping"] and dev.get("stream", 1) == stream)
            if total != len(idx):
                # Index array structure differs (e.g. some coords filtered out) - count all LEDs to be safe
                setattr(self, mask_attr, np.ones(len(idx), dtype=bool))
                continue
            
            mask = np.zeros(len(idx), dtype=bool)
            pos = 0
            for dev in WLED_DEVICES:
                if dev["mapping"] and dev.get("stream", 1) == stream:
                    n = len(dev["mapping"])
                    if dev.get("online"):
                        mask[pos:pos + n] = True
                    pos += n
            setattr(self, mask_attr, mask)
    
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
                self._tkget(self.monitor_index, 0, retries=40),
                TARGET_W,
                TARGET_H
            )
        
        if not ok:
            print("Failed to initialize capture DLL")
            return
        
        # Apply FPS limit right after init (DLL state is fresh)
        try:
            with self.dll_lock:
                self.bridge.set_capture_fps(self._get_capture_fps_value())
        except Exception as e:
            print(f"[WARN] Failed to set initial capture FPS: {e}")
        
        # Push shader params right after init (DLL state is fresh)
        self._push_shader_params_to_dll()
        
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
            
            # Never touch the DLL while a restart is in progress:
            # the restart worker owns shutdown/init and all reapply work
            if self.capture_paused or self.dll_restarting:
                time.sleep(0.005)
                continue
            
            capture_start = time.perf_counter()
            now = time.perf_counter()
            
            # STREAM 1
            if not self._dll_call_begin():
                time.sleep(0.005)
                continue
            try:
                with self.dll_lock:
                    ok = self.bridge.capture_frame()
                    
                    if not ok:
                        ok_copy = False
                        frame_id = None
                    else:
                        frame_id = self.bridge.get_frame_id()
                        
                        if frame_id == self.last_frame_id:
                            # Watchdog for stuck frame: restart if same frame persists too long
                            if now - self.last_frame_time > self.black_restart_delay:
                                self.request_restart(full=False)
                            continue
                        
                        self.frame_buffer.fill(0.0)
                        
                        ok_copy = self.bridge.copy_frame(
                            self.frame_buffer.ctypes.data_as(
                                ctypes.POINTER(ctypes.c_float)
                            ),
                            self.frame_buffer.nbytes
                        )
            finally:
                self._dll_call_end()
            
            self.capture_delay_ms = (time.perf_counter() - capture_start) * 1000
            
            if not ok or not ok_copy:
                if now - self.last_frame_time > self.black_restart_delay:
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
            
            tw = self._tkget(self.input_target_w, 0)
            th = self._tkget(self.input_target_h, 0)
            
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
                    w2 = self._tkget(self.target2_w, 0)
                    h2 = self._tkget(self.target2_h, 0)
                    
                    if w2 > 0 and h2 > 0:
                        if not self._dll_call_begin():
                            continue
                        try:
                            with self.dll_lock:
                                self.bridge.set_second_resolution(w2, h2)
                        finally:
                            self._dll_call_end()
                        
                        self.frame_buffer2 = np.empty((h2, w2, 3), dtype=np.float32)
                
                ok2 = False
                frame2 = None
                
                if self.frame_buffer2 is not None:
                    if not self._dll_call_begin():
                        continue
                    try:
                        with self.dll_lock:
                            ok2 = self.bridge.copy_frame2(
                                self.frame_buffer2.ctypes.data_as(
                                    ctypes.POINTER(ctypes.c_float)
                                ),
                                self.frame_buffer2.nbytes
                            )
                    finally:
                        self._dll_call_end()
                    
                    if ok2:
                        frame2 = self.frame_buffer2.copy()
                        self.last_frame2_valid = frame2.copy()
                    else:
                        if self.last_frame2_valid is not None:
                            frame2 = self.last_frame2_valid.copy()
                            ok2 = True
                    
                    if now - self.ddp2_last_ping > self.ddp2_ping_interval:
                        if self._dll_call_begin():
                            try:
                                try:
                                    with self.dll_lock:
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
                                except Exception:
                                    pass
                            finally:
                                self._dll_call_end()
                        
                        self.ddp2_last_ping = now
                
                if ok2 and frame2 is not None:
                    
                    self.second_capture_count += 1
                    
                    tw2 = self._tkget(self.input_target2_w, 0)
                    th2 = self._tkget(self.input_target2_h, 0)
                    
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
                    
                    if self._dll_call_begin():
                        try:
                            with self.dll_lock:
                                self.bridge.set_second_resolution(0, 0)
                        finally:
                            self._dll_call_end()
                    
                    self.frame_buffer2 = None
                    
                    try:
                        while True:
                            self.preview2_queue.get_nowait()
                    except Empty:
                        pass
            
            self.update_fps_counters()
    
    def apply_custom_gamma_to_tensor(self, tensor: np.ndarray, stream: int = 1) -> np.ndarray:
        """Apply custom gamma to tensor.
        
        - mode "rgb" (mono curve): applies a single 64-point curve to all channels
          (independent from the separate R/G/B curves).
        - mode "separate": applies per-channel 64-point curves R/G/B.
        """
        if stream == 1:
            gamma_mode = self._tkget(self.custom_gamma_rgb_mode1, "rgb")
            mono = self.custom_gamma_mono1
            values_r = self.custom_gamma_sdr_r1
            values_g = self.custom_gamma_sdr_g1
            values_b = self.custom_gamma_sdr_b1
        else:
            gamma_mode = self._tkget(self.custom_gamma_rgb_mode2, "rgb")
            mono = self.custom_gamma_mono2
            values_r = self.custom_gamma_sdr_r2
            values_g = self.custom_gamma_sdr_g2
            values_b = self.custom_gamma_sdr_b2
        
        if gamma_mode == "rgb":
            # Use the independent mono curve for all channels
            return apply_custom_gamma(tensor, mono, gamma_mode="rgb")
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
            # (cross-thread Tk calls can fail while the UI thread is busy —
            #  never let that kill the processing thread)
            try:
                if hdr_active != last_hdr_state:
                    self.root.after(0, self.update_mode_highlight, hdr_active)
                    last_hdr_state = hdr_active
                else:
                    # If mode not changed, update only text label with current values
                    self.root.after(0, self.update_nits_labels)
            except Exception:
                pass
            
            p = self.stream1_vars
            
            if hdr_active:
                brightness = self._tkget(p["brightness_hdr"], 255) / 255.0
                gamma = self._tkget(p["gamma_hdr"], 1.0)
                gamma_enabled = self._tkget(p["gamma_hdr_en"], False)
                sat_enabled = self._tkget(p["sat_hdr_en"], False)
                sat_strength = self._tkget(p["sat_hdr"], 1.0)
            else:
                brightness = self._tkget(p["brightness_sdr"], 255) / 255.0
                gamma = self._tkget(p["gamma_sdr"], 1.0)
                gamma_enabled = self._tkget(p["gamma_sdr_en"], False)
                
                # Gamma mode unified for both streams - applied immediately to SDR and HDR
                gamma_mode = self._tkget(self.gamma_mode_sdr, "stream")
                
                sat_enabled = self._tkget(p["sat_sdr_en"], False)
                sat_strength = self._tkget(p["sat_sdr"], 1.0)
            
            # TONEMAP
            if hdr_active and self._tkget(self.tonemap_enabled, True):
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
            if self._tkget(self.calibration1_enabled, False):
                tensor_wled = self.apply_led_calibration(tensor_wled)
            
            # SATURATION
            if sat_enabled:
                tensor_wled = self.apply_saturation(tensor_wled, sat_strength)
            
            # BRIGHTNESS
            tensor_wled *= brightness
            tensor_wled = np.clip(tensor_wled, 0.0, 1.0)
            
            # AMBI
            mode = self._tkget(self.ambi_mode1, "Matrix")
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
            
            # LED POWER CALCULATION - Stream 1 (only ONLINE WLED modules)
            # NOTE: frames queued before a mapping/resolution rebuild may be
            # smaller than exp_pixel_indices — a stale index must never crash
            # this thread (IndexError would kill the whole pipeline)
            _idx = self.exp_pixel_indices
            _flat = tensor_wled.reshape(-1, 3) if _idx is not None else None
            if (_idx is not None and len(_idx) > 0
                    and _flat is not None and len(_flat) > int(np.max(_idx))):
                # (N_LEDS, 3) RGB 0.0-1.0, full mapping order with the
                # per-module send mode applied (see _mode_adjusted_rgb):
                #   LIVE  - the live frame;
                #   PAUSE - the frozen last frame (the WLED shows it too);
                #   OFF   - IC consumption + the selected color on all crystals
                mapped_rgb_full = self._mode_adjusted_rgb(_flat[_idx], 1)
                if self.online_mask1 is not None:
                    mapped_rgb = mapped_rgb_full[self.online_mask1]
                else:
                    mapped_rgb = mapped_rgb_full
                led_count = len(mapped_rgb)
                # Current / power are computed from the live
                # per-stream settings (self.led_settings_s1), see _compute_led_power
                self.led_power_s1 = self._compute_led_power(mapped_rgb, 1)
                # Real per-device power (per-crystal brightness, pre-mask rows)
                self._update_dev_power(1, mapped_rgb_full)

                # THERMAL MAP - publish latest per-LED brightness
                # (full mapping order; offline devices masked to 0)
                thermal_rgb = mapped_rgb_full
                if self.online_mask1 is not None:
                    thermal_rgb = np.where(self.online_mask1[:, None], thermal_rgb, 0.0)
                self.thermal_frame_s1 = thermal_rgb
            else:
                # No WLED modules in Stream 1 (or stale frame) - show 0
                # instead of the last fixed value
                self.led_power_s1 = self._zero_led_power(1)
                self._update_dev_power(1, None)
                self.thermal_frame_s1 = None
            
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
            # (cross-thread Tk calls can fail while the UI thread is busy —
            #  never let that kill the processing thread)
            try:
                if hdr_active != last_hdr_state:
                    self.root.after(0, self.update_mode_highlight, hdr_active)
                    last_hdr_state = hdr_active
                else:
                    # If mode not changed, update only text label with current values
                    self.root.after(0, self.update_nits_labels)
            except Exception:
                pass
            
            p = self.stream2_vars
            
            if hdr_active:
                brightness = self._tkget(p["brightness_hdr"], 255) / 255.0
                gamma = self._tkget(p["gamma_hdr"], 1.0)
                gamma_enabled = self._tkget(p["gamma_hdr_en"], False)
                sat_enabled = self._tkget(p["sat_hdr_en"], False)
                sat_strength = self._tkget(p["sat_hdr"], 1.0)
            else:
                brightness = self._tkget(p["brightness_sdr"], 255) / 255.0
                gamma = self._tkget(p["gamma_sdr"], 1.0)
                gamma_enabled = self._tkget(p["gamma_sdr_en"], False)
                
                # Gamma mode unified for both streams - applied immediately to SDR and HDR
                gamma_mode = self._tkget(self.gamma_mode_sdr, "stream")
                
                sat_enabled = self._tkget(p["sat_sdr_en"], False)
                sat_strength = self._tkget(p["sat_sdr"], 1.0)
            
            # TONEMAP
            if hdr_active and self._tkget(self.tonemap_enabled, True):
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
            if self._tkget(self.calibration2_enabled, False):
                tensor_wled = self.apply_led_calibration2(tensor_wled)
            
            # SATURATION
            if sat_enabled:
                tensor_wled = self.apply_saturation(tensor_wled, sat_strength)
            
            # BRIGHTNESS
            tensor_wled *= brightness
            tensor_wled = np.clip(tensor_wled, 0.0, 1.0)
            
            # AMBILIGHT
            mode = self._tkget(self.ambi_mode2, "Matrix")
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
            
            # LED POWER CALCULATION - Stream 2 (only ONLINE WLED modules)
            # NOTE: frames queued before a mapping/resolution rebuild may be
            # smaller than exp_pixel_indices2 — a stale index must never crash
            # this thread (IndexError would kill the whole pipeline)
            _idx2 = self.exp_pixel_indices2
            _flat2 = tensor_wled.reshape(-1, 3) if _idx2 is not None else None
            if (_idx2 is not None and len(_idx2) > 0
                    and _flat2 is not None and len(_flat2) > int(np.max(_idx2))):
                # (N_LEDS, 3) RGB 0.0-1.0, full mapping order with the
                # per-module send mode applied (see _mode_adjusted_rgb):
                #   LIVE  - the live frame;
                #   PAUSE - the frozen last frame (the WLED shows it too);
                #   OFF   - IC consumption + the selected color on all crystals
                mapped_rgb2_full = self._mode_adjusted_rgb(_flat2[_idx2], 2)
                if self.online_mask2 is not None:
                    mapped_rgb2 = mapped_rgb2_full[self.online_mask2]
                else:
                    mapped_rgb2 = mapped_rgb2_full
                # Current / power are computed from the live
                # per-stream settings (self.led_settings_s2), see _compute_led_power
                self.led_power_s2 = self._compute_led_power(mapped_rgb2, 2)
                # Real per-device power (per-crystal brightness, pre-mask rows)
                self._update_dev_power(2, mapped_rgb2_full)

                # THERMAL MAP - publish latest per-LED brightness
                # (full mapping order; offline devices masked to 0)
                thermal_rgb2 = mapped_rgb2_full
                if self.online_mask2 is not None:
                    thermal_rgb2 = np.where(self.online_mask2[:, None], thermal_rgb2, 0.0)
                self.thermal_frame_s2 = thermal_rgb2
            else:
                # No WLED modules in Stream 2 (or stale frame) - show 0
                # instead of the last fixed value
                self.led_power_s2 = self._zero_led_power(2)
                self._update_dev_power(2, None)
                self.thermal_frame_s2 = None
            
            # PREVIEW
            self.push_latest(self.preview2_queue, tensor_u8)
            
            self.pipeline_delay_ms = (time.perf_counter() - pipeline_start) * 1000
    
    def on_capture_fps_change(self, event=None):
        """Capture FPS change handler (0 = adaptive)"""
        try:
            val = str(self.capture_fps.get()).strip()
            fps = 0 if val.lower() in ("adaptive", "0", "") else int(val)
        except Exception:
            fps = 0
        
        print(f"[INFO] Capture FPS set to: {fps if fps > 0 else 'adaptive'}")
        
        # Skip while a restart is in progress: the worker re-applies FPS
        # after init, and settings are re-read at execution time
        if self.bridge and not self.dll_restarting:
            try:
                with self.dll_lock:
                    self.bridge.set_capture_fps(fps)
            except Exception as e:
                print(f"[WARN] Failed to set capture FPS: {e}")
    
    def _get_capture_fps_value(self) -> int:
        """Get the FPS value to apply to DLL (0 = adaptive)"""
        try:
            val = str(self.capture_fps.get()).strip()
            return 0 if val.lower() in ("adaptive", "0", "") else int(val)
        except Exception:
            return 0
    
    def _apply_capture_fps_to_dll(self):
        """Apply current FPS setting to the DLL"""
        fps = self._get_capture_fps_value()
        # Never call the DLL while a restart is in progress
        if self.bridge and not self.dll_restarting:
            try:
                self.bridge.set_capture_fps(fps)
            except Exception as e:
                print(f"[WARN] Failed to apply capture FPS: {e}")
    
    def _push_shader_params_to_dll(self):
        """Push current shader optimization params to the DLL"""
        if not self.bridge:
            return
        if self.dll_restarting:
            return
        
        prec = self.shader_precision
        # pixel_limit: 0 = unlimited, >0 = max total source pixels to sample
        dll_samples = max(0, int(self.pixel_limit))
        coord_mode = self.coordinate_recalc_mode
        
        # Use persistent lock to synchronize with capture thread
        if not hasattr(self, "dll_lock"):
            import threading
            self.dll_lock = threading.Lock()
        
        try:
            with self.dll_lock:
                self.bridge.set_shader_params(
                    pixel_limit=dll_samples,
                    coord_mode=coord_mode,
                    prec_coord=prec.get("coordinate", "fp32"),
                    prec_weights=prec.get("weights", "fp32"),
                    prec_color=prec.get("color", "fp32"),
                    prec_accum=prec.get("accumulator", "fp32"),
                )
            print(f"[OK] Shader params pushed to DLL: samples={dll_samples}, coord={coord_mode}, "
                  f"prec=[{prec.get('coordinate')},{prec.get('weights')},{prec.get('color')},{prec.get('accumulator')}]")
        except Exception as e:
            print(f"[WARN] Failed to push shader params to DLL: {e}")
    
    def _apply_optimization_to_dll(self):
        """Apply the saved optimization-window params to the DLL
        (separable pipeline mode + shader precision / pixel limit / coord mode)."""
        bridge = getattr(self, "bridge", None)
        if not bridge:
            return
        if self.dll_restarting:
            return
        
        use_separable = bool(getattr(self, "use_separable", True))
        try:
            pixel_limit = int(getattr(self, "pixel_limit", 0))
        except (TypeError, ValueError):
            pixel_limit = 0
        coord_mode = getattr(self, "coordinate_recalc_mode", "once")
        prec = getattr(self, "shader_precision", None) or {}
        
        # Use a persistent lock to synchronize with the capture thread
        lock = getattr(self, "dll_lock", None)
        if lock is None:
            import threading
            lock = threading.Lock()
            self.dll_lock = lock
        
        try:
            with lock:
                if hasattr(bridge, "set_separable_mode"):
                    bridge.set_separable_mode(use_separable)
                bridge.set_shader_params(
                    pixel_limit=pixel_limit,
                    coord_mode=coord_mode,
                    prec_coord=prec.get("coordinate", "fp32"),
                    prec_weights=prec.get("weights", "fp32"),
                    prec_color=prec.get("color", "fp32"),
                    prec_accum=prec.get("accumulator", "fp32"),
                )
            print(f"[OK] Optimization params applied to DLL: separable={use_separable}, "
                  f"pixel_limit={pixel_limit}, coord={coord_mode}")
        except Exception as e:
            print(f"[WARN] Failed to apply optimization params to DLL: {e}")
    
    def _get_active_monitor_name(self) -> str:
        """Return the device name of the currently selected monitor (empty if unavailable)"""
        try:
            idx = self.monitor_index.get()
            if 0 <= idx < len(self.monitors):
                return self.monitors[idx]["name"]
        except Exception:
            pass
        return ""
    
    def _resolve_monitor_index_by_name(self, name: str) -> int:
        """
        Resolve a monitor index by its device name among currently connected monitors.
        Falls back to the primary monitor (index 0) if the named monitor is not connected.
        """
        if name:
            for i, m in enumerate(self.monitors):
                if m.get("name") == name:
                    if i != 0:
                        print(f"[OK] Reconnected to saved monitor by name: {name} (index {i})")
                    return i
            print(f"[WARN] Saved monitor '{name}' not found among connected - falling back to primary (index 0)")
        return 0
    
    def on_monitor_change(self, event=None):
        """Monitor change handler"""
        self.monitor_index.set(self.monitor_combo.current())
        
        w, h = self.recalc_resolution_for_current_state()
        w2, h2 = self.recalc_resolution_stream2()
        
        # The device switch (shutdown/init + buffers + re-apply of
        # FPS/shaders/stream2) is done exclusively by the restart worker
        # thread; the UI must never block on the DLL here
        self.request_restart(full=False)
        
        # Sync UI state
        self.target2_w.set(w2)
        self.target2_h.set(h2)
    
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
            self.sacn1_fps_real = self.sacn1_frame_count
            self.capture_count = 0
            self.scale_count = 0
            self.preview_count = 0
            self.ddp_frame_count = 0
            self.sacn1_frame_count = 0
            self.last_fps_time = now
            
            self.second_fps_real = self.second_capture_count
            self.preview2_fps_real = self.preview2_count
            self.ddp2_fps_real = self.ddp2_frame_count
            self.sacn2_fps_real = self.sacn2_frame_count
            self.second_capture_count = 0
            self.preview2_count = 0
            self.ddp2_frame_count = 0
            self.sacn2_frame_count = 0
    
    def save_config_default(self):
        """Save default config to app_config.json with dark confirmation dialog"""
        # Create custom dark-themed confirmation dialog
        dialog = tk.Toplevel(self.root)
        dialog.title("Confirm")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        # Apply dark mode to title bar
        try:
            from window_utils import apply_dark_mode_to_tk_window
            dialog.update_idletasks()
            apply_dark_mode_to_tk_window(dialog)
        except ImportError:
            pass
        
        # Dialog size
        dialog_width = 320
        dialog_height = 120
        
        # Center on the actual screen (not parent window)
        screen_width = dialog.winfo_screenwidth()
        screen_height = dialog.winfo_screenheight()
        
        x = (screen_width - dialog_width) // 2
        y = (screen_height - dialog_height) // 2
        
        dialog.geometry(f"{dialog_width}x{dialog_height}+{x}+{y}")
        
        # Force Tk to calculate geometry after positioning
        dialog.update_idletasks()
        
        # Dark theme colors matching the app
        dialog.configure(bg=self.colors["bg"])
        
        # Remove default window border decorations for cleaner look
        try:
            dialog.attributes("-topmost", True)
        except:
            pass
        
        # Question label
        label = tk.Label(
            dialog,
            text="Save default config?",
            font=("Segoe UI", 11),
            bg=self.colors["bg"],
            fg=self.colors["text_main"]
        )
        label.pack(pady=(15, 15))
        
        # Button frame
        btn_frame = tk.Frame(dialog, bg=self.colors["bg"])
        btn_frame.pack(side="bottom", pady=10)
        
        # Result holder
        result_holder = {"result": False}
        
        def on_yes():
            result_holder["result"] = True
            dialog.destroy()
        
        def on_no():
            result_holder["result"] = False
            dialog.destroy()
        
        # Yes button
        yes_btn = tk.Button(
            btn_frame,
            text="Yes",
            font=("Segoe UI", 9),
            bg=self.colors["accent"],
            fg="#1a1b26",
            activebackground=self.colors["accent_hover"],
            activeforeground="#1a1b26",
            bd=0,
            relief="flat",
            cursor="hand2",
            padx=20,
            pady=5,
            command=on_yes
        )
        yes_btn.pack(side="left", padx=5)
        
        # No button
        no_btn = tk.Button(
            btn_frame,
            text="No",
            font=("Segoe UI", 9),
            bg=self.colors["panel_bg"],
            fg=self.colors["text_main"],
            activebackground=self.colors["accent"],
            activeforeground="#1a1b26",
            bd=0,
            relief="flat",
            cursor="hand2",
            padx=20,
            pady=5,
            command=on_no
        )
        no_btn.pack(side="left", padx=5)
        
        # Wait for dialog to close
        dialog.wait_window()
        
        if result_holder["result"]:
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
                
                # Power calculation must reflect the new online/offline state
                self._refresh_online_masks()
                
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
            # Save the active monitor's device name so we can reconnect to the
            # exact same display on startup (with fallback to primary if absent)
            "monitor_name": self._get_active_monitor_name(),
            "capture_fps": self._get_capture_fps_value(),
            "capture_fps_label": str(self.capture_fps.get()) if hasattr(self, "capture_fps") else "adaptive",
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
            
            # Auto-interpolation checkbox states (per stream, per mode)
            "interp_sdr_1": self.interp_sdr_1.get(),
            "interp_hdr_1": self.interp_hdr_1.get(),
            "interp_sdr_2": self.interp_sdr_2.get(),
            "interp_hdr_2": self.interp_hdr_2.get(),
            
            # External LUT file paths (for auto-load on config load)
            "external_lut_sdr_1_path": getattr(self, 'external_lut_sdr_1_path', ''),
            "external_lut_hdr_1_path": getattr(self, 'external_lut_hdr_1_path', ''),
            "external_lut_sdr_2_path": getattr(self, 'external_lut_sdr_2_path', ''),
            "external_lut_hdr_2_path": getattr(self, 'external_lut_hdr_2_path', ''),
            
            # PQ curve values (RGB) - Stream 1
            "pq_values_r1": [float(x) for x in self.pq_values_r1],
            "pq_values_g1": [float(x) for x in self.pq_values_g1],
            "pq_values_b1": [float(x) for x in self.pq_values_b1],
            # PQ curve values MONO - Stream 1 (independent from RGB)
            "pq_values_mono1": [float(x) for x in self.pq_values_mono1],
            
            # PQ curve values (RGB) - Stream 2
            "pq_values_r2": [float(x) for x in self.pq_values_r2],
            "pq_values_g2": [float(x) for x in self.pq_values_g2],
            "pq_values_b2": [float(x) for x in self.pq_values_b2],
            # PQ curve values MONO - Stream 2 (independent from RGB)
            "pq_values_mono2": [float(x) for x in self.pq_values_mono2],
            
            # Saved custom gamma values (Stream 1)
            "saved_custom_gamma_sdr_r1": [float(x) for x in self.saved_custom_gamma_sdr_r1],
            "saved_custom_gamma_sdr_g1": [float(x) for x in self.saved_custom_gamma_sdr_g1],
            "saved_custom_gamma_sdr_b1": [float(x) for x in self.saved_custom_gamma_sdr_b1],
            # Saved custom gamma MONO values (Stream 1) - independent from RGB
            "saved_custom_gamma_mono1": [float(x) for x in self.saved_custom_gamma_mono1],
            
            # Saved custom gamma values (Stream 2)
            "saved_custom_gamma_sdr_r2": [float(x) for x in self.saved_custom_gamma_sdr_r2],
            "saved_custom_gamma_sdr_g2": [float(x) for x in self.saved_custom_gamma_sdr_g2],
            "saved_custom_gamma_sdr_b2": [float(x) for x in self.saved_custom_gamma_sdr_b2],
            # Saved custom gamma MONO values (Stream 2) - independent from RGB
            "saved_custom_gamma_mono2": [float(x) for x in self.saved_custom_gamma_mono2],
            
            # Saved custom gamma curve/bias/enabled values (Stream 1 RGB)
            "saved_curve_strength1": self.saved_curve_strength1.get(),
            "saved_bias1": self.saved_bias1.get(),
            "saved_custom_gamma_enabled1": self.saved_custom_gamma_enabled1.get(),
            
            # Saved custom gamma curve/bias/enabled values (Stream 1 Mono)
            "saved_curve_strength_mono1": self.saved_curve_strength_mono1.get(),
            "saved_bias_mono1": self.saved_bias_mono1.get(),
            "saved_custom_gamma_enabled_mono1": self.saved_custom_gamma_enabled_mono1.get(),
            
            # Custom gamma RGB mode (Stream 1): "rgb" = Mono, "separate" = RGB
            "custom_gamma_rgb_mode1": self.custom_gamma_rgb_mode1.get(),
            
            # Saved custom gamma curve/bias/enabled values (Stream 2 RGB)
            "saved_curve_strength2": self.saved_curve_strength2.get(),
            "saved_bias2": self.saved_bias2.get(),
            "saved_custom_gamma_enabled2": self.saved_custom_gamma_enabled2.get(),
            
            # Saved custom gamma curve/bias/enabled values (Stream 2 Mono)
            "saved_curve_strength_mono2": self.saved_curve_strength_mono2.get(),
            "saved_bias_mono2": self.saved_bias_mono2.get(),
            "saved_custom_gamma_enabled_mono2": self.saved_custom_gamma_enabled_mono2.get(),
            
            # Custom gamma RGB mode (Stream 2): "rgb" = Mono, "separate" = RGB
            "custom_gamma_rgb_mode2": self.custom_gamma_rgb_mode2.get(),
            
            # PQ Mono curve settings (Stream 1)
            "pq_curve_strength_mono1": self.pq_curve_strength_mono1.get(),
            "pq_curve_bias_mono1": self.pq_curve_bias_mono1.get(),
            
            # PQ Mono curve settings (Stream 2)
            "pq_curve_strength_mono2": self.pq_curve_strength_mono2.get(),
            "pq_curve_bias_mono2": self.pq_curve_bias_mono2.get(),
            
            # WLED devices with mappings and connection state
            "wled_devices": [
                {
                    "ip": dev["ip"],
                    "name": dev.get("name", "WLED"),
                    "mapping": [list(coord) for coord in dev.get("mapping", [])] if dev.get("mapping") else None,
                    "stream": dev.get("stream", 1),
                    "protocol": dev.get("protocol", "DDP"),
                    "mode": dev.get("mode", "stream"),  # LIVE / PAUSE / OFF
                    "connected": True  # Mark device as connected for auto-reconnect on load
                }
                for dev in WLED_DEVICES
            ],
            
            # Shader optimization (optimization window)
            "shader_precision": {
                "coordinate": self.shader_precision.get("coordinate", "fp32"),
                "weights": self.shader_precision.get("weights", "fp32"),
                "color": self.shader_precision.get("color", "fp32"),
                "accumulator": self.shader_precision.get("accumulator", "fp32"),
            },
            "pixel_limit": int(getattr(self, "pixel_limit", 0)),
            "coordinate_recalc_mode": getattr(self, "coordinate_recalc_mode", "once"),
            "use_separable": bool(getattr(self, "use_separable", True)),

            # LED settings (Power panel: currents, efficiencies, density,
            # ambient temperature) + Temp Map calculation toggle
            "led_settings_s1": dict(self.led_settings_s1),
            "led_settings_s2": dict(self.led_settings_s2),

            # WLED module OFF-mode colors (per IP, set in the "Color" window):
            # ip -> {"r":, "g":, "b":, "bri":}
            "wled_off_colors": {
                str(ip): {
                    "r": int(c.get("r", 0)),
                    "g": int(c.get("g", 0)),
                    "b": int(c.get("b", 0)),
                    "bri": int(c.get("bri", 255)),
                }
                for ip, c in (getattr(self, "wled_off_colors", {}) or {}).items()
            },

            # Current send protocol: "DDP" or "E1.31"
            "current_protocol": self.current_protocol.get() if hasattr(self, "current_protocol") else "DDP",
        }
    
    def apply_settings(self, settings: dict):
        """Apply settings from dictionary"""
        try:
            # Capture settings — resolve the active monitor:
            #   1) by saved device name (reconnect to the exact same display)
            #   2) by saved positional index (legacy configs / name lookup failed)
            #   3) the primary monitor (index 0) as the final fallback
            saved_name = str(settings.get("monitor_name", "") or "")
            try:
                saved_index = int(settings.get("monitor_index", 0))
            except (TypeError, ValueError):
                saved_index = 0
            
            resolved_index = -1
            if saved_name:
                for i, m in enumerate(self.monitors):
                    if m.get("name") == saved_name:
                        resolved_index = i
                        if i != 0:
                            print(f"[OK] Reconnected to saved monitor by name: {saved_name} (index {i})")
                        break
            
            if resolved_index < 0 and 0 <= saved_index < len(self.monitors):
                resolved_index = saved_index
            
            if resolved_index < 0 or resolved_index >= len(self.monitors):
                resolved_index = 0
                if saved_name:
                    print(f"[WARN] Saved monitor '{saved_name}' not connected - using primary monitor (index 0)")
            
            self.monitor_index.set(resolved_index)
            try:
                self.monitor_combo.current(resolved_index)
            except Exception:
                pass
            
            # Restore capture FPS (0 = adaptive)
            fps_label = settings.get("capture_fps_label")
            if fps_label is None and "capture_fps" in settings:
                fps_label = "adaptive" if int(settings["capture_fps"]) <= 0 else str(int(settings["capture_fps"]))
            if fps_label is not None and hasattr(self, "capture_fps"):
                try:
                    self.capture_fps.set(str(fps_label))
                except Exception:
                    pass
            # Apply to DLL if already initialized
            self._apply_capture_fps_to_dll()
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
            if "saved_custom_gamma_mono1" in settings and len(settings["saved_custom_gamma_mono1"]) == 64:
                self.saved_custom_gamma_mono1 = np.array([float(x) for x in settings["saved_custom_gamma_mono1"]], dtype=np.float32)
            
            if "saved_custom_gamma_sdr_r2" in settings and len(settings["saved_custom_gamma_sdr_r2"]) == 64:
                self.saved_custom_gamma_sdr_r2 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_r2"]], dtype=np.float32)
            if "saved_custom_gamma_sdr_g2" in settings and len(settings["saved_custom_gamma_sdr_g2"]) == 64:
                self.saved_custom_gamma_sdr_g2 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_g2"]], dtype=np.float32)
            if "saved_custom_gamma_sdr_b2" in settings and len(settings["saved_custom_gamma_sdr_b2"]) == 64:
                self.saved_custom_gamma_sdr_b2 = np.array([float(x) for x in settings["saved_custom_gamma_sdr_b2"]], dtype=np.float32)
            if "saved_custom_gamma_mono2" in settings and len(settings["saved_custom_gamma_mono2"]) == 64:
                self.saved_custom_gamma_mono2 = np.array([float(x) for x in settings["saved_custom_gamma_mono2"]], dtype=np.float32)
            
            # Apply saved custom gamma values to current (use 64 slider values as source of truth)
            # IMPORTANT: Use in-place assignment with [:] to preserve array references for slider callbacks
            if len(self.saved_custom_gamma_sdr_r1) == 64:
                self.custom_gamma_sdr_r1[:] = self.saved_custom_gamma_sdr_r1[:64]
                self.custom_gamma_sdr_g1[:] = self.saved_custom_gamma_sdr_g1[:64]
                self.custom_gamma_sdr_b1[:] = self.saved_custom_gamma_sdr_b1[:64]
            if len(self.saved_custom_gamma_mono1) == 64:
                self.custom_gamma_mono1[:] = self.saved_custom_gamma_mono1[:64]
            
            if len(self.saved_custom_gamma_sdr_r2) == 64:
                self.custom_gamma_sdr_r2[:] = self.saved_custom_gamma_sdr_r2[:64]
                self.custom_gamma_sdr_g2[:] = self.saved_custom_gamma_sdr_g2[:64]
                self.custom_gamma_sdr_b2[:] = self.saved_custom_gamma_sdr_b2[:64]
            if len(self.saved_custom_gamma_mono2) == 64:
                self.custom_gamma_mono2[:] = self.saved_custom_gamma_mono2[:64]
            
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
            
            # Auto-interpolation checkbox states (per stream, per mode)
            if "interp_sdr_1" in settings:
                self.interp_sdr_1.set(bool(settings["interp_sdr_1"]))
            if "interp_hdr_1" in settings:
                self.interp_hdr_1.set(bool(settings["interp_hdr_1"]))
            if "interp_sdr_2" in settings:
                self.interp_sdr_2.set(bool(settings["interp_sdr_2"]))
            if "interp_hdr_2" in settings:
                self.interp_hdr_2.set(bool(settings["interp_hdr_2"]))
            
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
                            
                            # Apply auto-interpolation if checkbox is enabled
                            interp_var = getattr(self, f"interp_{mode.lower()}_{stream}", None)
                            if interp_var and interp_var.get():
                                try:
                                    print(f"[INFO] Auto-interpolating S{stream} {mode} on config load")
                                    self.interpolate_lut_to_256(stream, mode)
                                except Exception as e:
                                    print(f"[WARN] Auto-interpolation failed S{stream} {mode}: {e}")
                        except Exception as e:
                            print(f"[WARN] Failed to auto-load LUT S{stream} {mode}: {e}")
            
            # PQ curve values (RGB) - Stream 1
            if "pq_values_r1" in settings and len(settings["pq_values_r1"]) == PQ_POINTS:
                self.pq_values_r1[:] = np.array([float(x) for x in settings["pq_values_r1"]], dtype=np.float32)
            if "pq_values_g1" in settings and len(settings["pq_values_g1"]) == PQ_POINTS:
                self.pq_values_g1[:] = np.array([float(x) for x in settings["pq_values_g1"]], dtype=np.float32)
            if "pq_values_b1" in settings and len(settings["pq_values_b1"]) == PQ_POINTS:
                self.pq_values_b1[:] = np.array([float(x) for x in settings["pq_values_b1"]], dtype=np.float32)
            
            # PQ curve values (RGB) - Stream 2
            if "pq_values_r2" in settings and len(settings["pq_values_r2"]) == PQ_POINTS:
                self.pq_values_r2[:] = np.array([float(x) for x in settings["pq_values_r2"]], dtype=np.float32)
            if "pq_values_g2" in settings and len(settings["pq_values_g2"]) == PQ_POINTS:
                self.pq_values_g2[:] = np.array([float(x) for x in settings["pq_values_g2"]], dtype=np.float32)
            if "pq_values_b2" in settings and len(settings["pq_values_b2"]) == PQ_POINTS:
                self.pq_values_b2[:] = np.array([float(x) for x in settings["pq_values_b2"]], dtype=np.float32)
            
            # PQ curve values MONO - Stream 1
            if "pq_values_mono1" in settings and len(settings["pq_values_mono1"]) == PQ_POINTS:
                self.pq_values_mono1[:] = np.array([float(x) for x in settings["pq_values_mono1"]], dtype=np.float32)
            
            # PQ curve values MONO - Stream 2
            if "pq_values_mono2" in settings and len(settings["pq_values_mono2"]) == PQ_POINTS:
                self.pq_values_mono2[:] = np.array([float(x) for x in settings["pq_values_mono2"]], dtype=np.float32)
            
            # Saved custom gamma curve/bias/enabled values (Stream 1 RGB)
            if "saved_curve_strength1" in settings:
                self.saved_curve_strength1.set(float(settings["saved_curve_strength1"]))
            if "saved_bias1" in settings:
                self.saved_bias1.set(float(settings["saved_bias1"]))
            if "saved_custom_gamma_enabled1" in settings:
                self.saved_custom_gamma_enabled1.set(bool(settings["saved_custom_gamma_enabled1"]))
            
            # Saved custom gamma curve/bias/enabled values (Stream 1 Mono)
            if "saved_curve_strength_mono1" in settings:
                self.saved_curve_strength_mono1.set(float(settings["saved_curve_strength_mono1"]))
            if "saved_bias_mono1" in settings:
                self.saved_bias_mono1.set(float(settings["saved_bias_mono1"]))
            if "saved_custom_gamma_enabled_mono1" in settings:
                self.saved_custom_gamma_enabled_mono1.set(bool(settings["saved_custom_gamma_enabled_mono1"]))
            
            # Custom gamma RGB mode (Stream 1)
            if "custom_gamma_rgb_mode1" in settings:
                self.custom_gamma_rgb_mode1.set(str(settings["custom_gamma_rgb_mode1"]))
            
            # Saved custom gamma curve/bias/enabled values (Stream 2 RGB)
            if "saved_curve_strength2" in settings:
                self.saved_curve_strength2.set(float(settings["saved_curve_strength2"]))
            if "saved_bias2" in settings:
                self.saved_bias2.set(float(settings["saved_bias2"]))
            if "saved_custom_gamma_enabled2" in settings:
                self.saved_custom_gamma_enabled2.set(bool(settings["saved_custom_gamma_enabled2"]))
            
            # Saved custom gamma curve/bias/enabled values (Stream 2 Mono)
            if "saved_curve_strength_mono2" in settings:
                self.saved_curve_strength_mono2.set(float(settings["saved_curve_strength_mono2"]))
            if "saved_bias_mono2" in settings:
                self.saved_bias_mono2.set(float(settings["saved_bias_mono2"]))
            if "saved_custom_gamma_enabled_mono2" in settings:
                self.saved_custom_gamma_enabled_mono2.set(bool(settings["saved_custom_gamma_enabled_mono2"]))
            
            # Custom gamma RGB mode (Stream 2)
            if "custom_gamma_rgb_mode2" in settings:
                self.custom_gamma_rgb_mode2.set(str(settings["custom_gamma_rgb_mode2"]))
            
            # PQ Mono curve settings (Stream 1)
            if "pq_curve_strength_mono1" in settings:
                self.pq_curve_strength_mono1.set(float(settings["pq_curve_strength_mono1"]))
            if "pq_curve_bias_mono1" in settings:
                self.pq_curve_bias_mono1.set(float(settings["pq_curve_bias_mono1"]))
            
            # PQ Mono curve settings (Stream 2)
            if "pq_curve_strength_mono2" in settings:
                self.pq_curve_strength_mono2.set(float(settings["pq_curve_strength_mono2"]))
            if "pq_curve_bias_mono2" in settings:
                self.pq_curve_bias_mono2.set(float(settings["pq_curve_bias_mono2"]))
            
            # WLED module OFF-mode colors (per IP) — must be loaded BEFORE the
            # devices block, so that applying saved OFF modes pushes the
            # saved colors to the modules
            if "wled_off_colors" in settings and isinstance(settings["wled_off_colors"], dict):
                colors = {}
                for ip, c in settings["wled_off_colors"].items():
                    try:
                        colors[str(ip)] = {
                            "r": max(0, min(255, int(c.get("r", 0)))),
                            "g": max(0, min(255, int(c.get("g", 0)))),
                            "b": max(0, min(255, int(c.get("b", 0)))),
                            "bri": max(0, min(255, int(c.get("bri", 255)))),
                        }
                    except (AttributeError, TypeError, ValueError):
                        continue
                self.wled_off_colors = colors
                print(f"[OK] Loaded {len(colors)} WLED module color(s) from config")

            # Current send protocol (DDP / E1.31) — apply before the devices
            # block so the sACN manager update inside it uses the saved value
            if "current_protocol" in settings:
                proto = str(settings["current_protocol"])
                if proto in ("DDP", "E1.31"):
                    self.current_protocol.set(proto)
                    print(f"[OK] Applied protocol: {proto}")

            # WLED devices with mappings and auto-reconnect
            if "wled_devices" in settings:
                global WLED_DEVICES
                
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
                        "protocol": dev_settings.get("protocol", "DDP"),
                        "mode": dev_settings.get("mode", "stream"),  # LIVE / PAUSE / OFF
                        "online": False  # Default status, will be updated by ping loop
                    }
                    
                    # Check if device should be auto-reconnected (connected=True or not present for backward compatibility)
                    should_reconnect = dev_settings.get("connected", True)
                    
                    if should_reconnect:
                        devices_to_reconnect.append(device_info)
                        print(f"[INFO] Queueing WLED {dev_settings['ip']} for auto-reconnect")
                    
                    WLED_DEVICES.append(device_info)
                
                print(f"[OK] Loaded {len(WLED_DEVICES)} WLED devices from config")
                
                # Auto-connect to queued devices and update their length if mapping exists
                if devices_to_reconnect:
                    print(f"[INFO] Auto-connecting to {len(devices_to_reconnect)} WLED device(s)...")
                    for dev in devices_to_reconnect:
                        self._connect_wled_device(dev)
                    # Respect per-module send modes saved in config (PAUSE / OFF)
                    for dev in devices_to_reconnect:
                        m = dev.get("mode", "stream")
                        if m != "stream":
                            self.set_dev_mode(dev, m)
                
                # Update UI list after loading devices
                self.update_wled_list()
                self.rebuild_master_mapping()
                
                # CRITICAL FIX: If E1.31 sACN protocol is active, update the sACN managers
                # so all loaded devices are included in sACN send loops.
                # Without this, devices loaded from config won't receive frames in sACN mode.
                if self.current_protocol.get() == "E1.31":
                    self._update_sacn_managers()
                    print("[OK] E1.31 sACN managers updated after loading devices from config")
                
                # Start WLED ping monitoring thread for offline devices (10s interval)
                print("[INFO] Starting WLED ping monitoring...")
                self.start_wled_ping_thread()
            
            # Shader optimization (optimization window)
            if "shader_precision" in settings and isinstance(settings["shader_precision"], dict):
                sp = settings["shader_precision"]
                self.shader_precision = {
                    "coordinate": str(sp.get("coordinate", "fp32")),
                    "weights": str(sp.get("weights", "fp32")),
                    "color": str(sp.get("color", "fp32")),
                    "accumulator": str(sp.get("accumulator", "fp32")),
                }
            if "pixel_limit" in settings:
                try:
                    self.pixel_limit = int(settings["pixel_limit"])
                except (TypeError, ValueError):
                    self.pixel_limit = 0
            if "coordinate_recalc_mode" in settings:
                _cmode = str(settings["coordinate_recalc_mode"])
                self.coordinate_recalc_mode = _cmode if _cmode in ("once", "frame") else "once"
            if "use_separable" in settings:
                self.use_separable = bool(settings["use_separable"])

            # LED settings (Power panel, per stream): currents, efficiencies,
            # PSU losses, density, ambient temperature, Temp Map toggle
            for _attr in ("led_settings_s1", "led_settings_s2"):
                _raw = settings.get(_attr)
                if not isinstance(_raw, dict):
                    continue
                _base = getattr(self, _attr, None)
                if not isinstance(_base, dict):
                    continue
                for _k, _v in _raw.items():
                    if _k not in _base:
                        continue  # ignore unknown/legacy keys
                    try:
                        if isinstance(_base[_k], bool):
                            _base[_k] = bool(_v)
                        else:
                            _base[_k] = max(0.0, float(_v))
                    except (TypeError, ValueError):
                        pass
                print(f"[OK] Applied LED settings: {_attr}")

            # Apply saved optimization params to the DLL (separable mode + shader params)
            self._apply_optimization_to_dll()
            
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
        
        # Stream 2 threads
        threading.Thread(target=preview_s2_loop, args=(self, self._on_preview_window_closed), daemon=True).start()
        if self.bridge:
            threading.Thread(target=self.process2_loop, daemon=True).start()
        
        # Start appropriate send loop based on protocol
        protocol = self.current_protocol.get()
        if protocol == "E1.31" and HAS_SACN:
            print("[INFO] Starting E1.31 sACN mode...")
            
            # Initialize E1.31 managers for both streams
            self.sacn_manager1 = E131StreamManager()
            self.sacn_manager2 = E131StreamManager()
            
            # Update managers with current device mappings
            self._update_sacn_managers()
            
            # Start E1.31 send loops
            thread1 = threading.Thread(
                target=run_sacn_loop,
                args=(self.sacn_manager1, self.ddp_queue, 1),
                daemon=True
            )
            thread2 = threading.Thread(
                target=run_sacn_loop,
                args=(self.sacn_manager2, self.ddp2_queue, 2),
                daemon=True
            )
            thread1.start()
            thread2.start()
            
            # Store threads for cleanup
            self._sacn_threads = [thread1, thread2]
            
            print("[OK] E1.31 sACN mode enabled")
        else:
            print("[INFO] Starting DDP mode...")
            # Start DDP send loops
            threading.Thread(target=self.ddp_send_loop, daemon=True).start()
            threading.Thread(target=preview_s1_loop, args=(self, self._on_preview_window_closed), daemon=True).start()
            
            threading.Thread(target=self.ddp2_send_loop, daemon=True).start()
        
        # Background thermal simulation (heating / heat accumulation / cooling)
        # Runs from the very start of the application, in the background
        if THERMAL_AVAILABLE:
            threading.Thread(target=self._thermal_loop, args=(1,), daemon=True).start()
            threading.Thread(target=self._thermal_loop, args=(2,), daemon=True).start()
            print("[OK] Thermal simulation threads started (S1, S2)")
    
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

    # =========================
    # POWER PANEL: LED SETTINGS + TEMP MAP
    # =========================
    def _led_settings(self, stream: int) -> dict:
        """Per-stream LED settings dict (live)."""
        return self.led_settings_s1 if stream == 1 else self.led_settings_s2

    def _zero_led_power(self, stream: int) -> dict:
        """Zero power state for a stream."""
        return {
            "leds": 0,
            "current_ma": 0.0,
            "power_w": 0.0,
        }

    def _compute_led_power(self, mapped_rgb, stream: int) -> dict:
        """
        Compute current / power for one stream using the live
        per-stream settings (self.led_settings_s1 / s2).

        - mapped_rgb: (N, 3) float array, 0.0-1.0 per channel
        - current_ma : total load current INCLUDING MCU, WITHOUT PSU loss
        - power_w    : supply power INCLUDING PSU + wire losses
        """
        st = self._led_settings(stream)
        n = int(mapped_rgb.shape[0]) if mapped_rgb is not None else 0
        if n == 0:
            return self._zero_led_power(stream)

        v_led = 5.0  # LED supply voltage, V

        sum_r = float(np.sum(mapped_rgb[:, 0]))
        sum_g = float(np.sum(mapped_rgb[:, 1]))
        sum_b = float(np.sum(mapped_rgb[:, 2]))

        led_current_ma = (sum_r * float(st["r_ma"]) +
                          sum_g * float(st["g_ma"]) +
                          sum_b * float(st["b_ma"]))
        total_ma = led_current_ma + n * float(st["mcu_ma"])

        total_elec_w = (total_ma / 1000.0) * v_led

        power_w = total_elec_w * (1.0 + float(st["loss_pct"]) / 100.0)

        return {
            "leds": n,
            "current_ma": float(total_ma),
            "power_w": float(power_w),
        }

    def _on_led_setting_change(self, stream: int, key: str, var):
        """Live-apply a settings field change (no Save button needed)."""
        try:
            val = float(str(var.get()).replace(",", ".").strip())
        except Exception:
            return  # keep the last valid value until the field can be parsed
        if val < 0:
            val = 0.0
        st = self._led_settings(stream)
        if st.get(key) != val:
            st[key] = val
            # Потребление по WLED-модулям пересчитается автоматически
            # (значения в списке обновляются из wled_dev_power каждые ~200 мс)

    def _create_temp_row(self, parent, stream: int):
        """One stream row inside the Power panel:
        value label (left) + Settings / Temp Map buttons (right)."""
        colors = self.colors
        row = tk.Frame(parent, bg=colors["bg"])
        row.pack(pady=(10, 2) if stream == 1 else (2, 10), padx=10, fill="x", expand=True)

        ttk.Button(
            row,
            text="🗺 Temp Map",
            width=13,
            command=lambda s=stream: self.open_temp_map(s)
        ).pack(side="right", padx=(4, 0))

        ttk.Button(
            row,
            text="⚙ Settings",
            width=11,
            command=lambda s=stream: self.open_led_settings(s)
        ).pack(side="right", padx=(4, 0))

        label = tk.Label(
            row,
            text=f"S{stream} 0 LED 0.0A / 0.0W",
            font=("Consolas", 10),
            bg=colors["bg"],
            fg=colors["text_main"],
            anchor="w"
        )
        label.pack(side="left", fill="x", expand=True)
        return label

    def update_gui_fps(self):
        """Update FPS display - Stream1 left, Stream2 right, delays below"""
        # Stream 1 FPS text (left panel)
        if self.stream1_enabled:
            first_text = (
                f"Stream 1\n"
                f"Capture: {self.capture_fps_real} fps\n"
                f"Preview: {self.preview_fps_real} fps\n"
                f"DDP: {self.ddp_fps_real} fps\n"
                f"E1.31 sACN: {self.sacn1_fps_real} fps"
            )
        else:
            first_text = "Stream 1\nOFF"
        
        # Stream 2 FPS text (right panel)
        if self.stream2_enabled:
            second_text = (
                f"Stream 2\n"
                f"Capture: {self.second_fps_real} fps\n"
                f"Preview: {self.preview2_fps_real} fps\n"
                f"DDP: {self.ddp2_fps_real} fps\n"
                f"E1.31 sACN: {self.sacn2_fps_real} fps"
            )
        else:
            second_text = "Stream 2\nOFF"
        
        # Delays text (bottom panel)
        delays_text = (
            f"Capture: {self.capture_delay_ms:.2f} ms\n"
            f"DDP: {self.ddp_delay_ms:.2f} ms\n"
            f"E1.31 sACN: {self.sacn_delay_ms:.2f} ms\n"
            f"Preview: {self.preview_delay_ms:.2f} ms\n"
            f"Pipeline: {self.pipeline_delay_ms:.2f} ms"
        )
        
        self.info_stream1_label.config(text=first_text)
        self.info_stream2_label.config(text=second_text)
        self.info_delays_label.config(text=delays_text)
        
        # Update LED power panel
        # Show 0 if the stream is disabled or has no WLED modules (no stale value)
        zero_power = self._zero_led_power(1)
        try:
            p1 = self.led_power_s1 if (self.streaming_enabled and self.stream1_enabled) else zero_power
            self.temp_s1_label.config(
                text=f"S1 {p1['leds']} LED {p1['current_ma']/1000.0:.1f}A / {p1['power_w']:.1f}W"
            )
            
            p2 = self.led_power_s2 if (self.streaming2_enabled and self.stream2_enabled) else self._zero_led_power(2)
            self.temp_s2_label.config(
                text=f"S2 {p2['leds']} LED {p2['current_ma']/1000.0:.1f}A / {p2['power_w']:.1f}W"
            )
        except Exception:
            pass

        # Update real per-module power labels in the WLED device list
        try:
            for ip, label in getattr(self, "wled_dev_power_labels", []):
                if not label.winfo_exists():
                    continue
                dp = (getattr(self, "wled_dev_power", None) or {}).get(ip)
                if dp is not None:
                    label.config(text=f"{dp['current_ma']/1000.0:.2f}A / {dp['power_w']:.1f}W")
                else:
                    label.config(text="0.00A / 0.0W")
        except Exception:
            pass

        self.root.after(200, self.update_gui_fps)
    
    def open_led_settings(self, stream: int):
        """Open (or focus) the live LED settings window for one stream.

        All fields apply instantly on change - no Save / Close buttons.
        The window is resizable.
        """
        attr = f"led_settings_win_s{stream}"
        prev = getattr(self, attr, None)
        if prev is not None:
            try:
                if prev.winfo_exists():
                    prev.lift()
                    prev.focus_set()
                    return
            except Exception:
                pass

        st = self._led_settings(stream)
        colors = self.colors

        win = tk.Toplevel(self.root)
        setattr(self, attr, win)
        win.title(f"LED Settings — Stream {stream}")
        win.configure(bg=colors["bg"])
        win.resizable(True, True)
        win.minsize(400, 460)

        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w = min(480, int(sw * 0.9))
        h = 780
        x = int((sw - w) / 2)
        y = max(0, int((sh - h) / 2))
        win.geometry(f"{w}x{h}+{x}+{y}")

        try:
            from window_utils import apply_dark_mode_to_tk_window
            win.update_idletasks()
            apply_dark_mode_to_tk_window(win)
            win.after(100, lambda: apply_dark_mode_to_tk_window(win))
        except Exception:
            pass

        def _var(key):
            v = tk.StringVar(value=f"{st[key]:g}")
            v.trace_add("write", lambda *a, k=key: self._on_led_setting_change(stream, k, v))
            return v

        # --- Background canvas (same pattern as Shader Optimization window) ---
        main_canvas = tk.Canvas(win, highlightthickness=0, bd=0, bg=colors["bg"])
        main_canvas.pack(fill="both", expand=True)

        bg_original = None
        bg_item = None
        try:
            # Use path_utils to resolve background image location
            bg_path = resolve_resource_path("background.png")
            if os.path.exists(bg_path):
                bg_original = Image.open(bg_path).convert("RGBA")
                bg_photo = ImageTk.PhotoImage(
                    bg_original.resize((w, h), Image.Resampling.LANCZOS))
                setattr(self, f"led_settings_bg_s{stream}", bg_photo)
                bg_item = main_canvas.create_image(0, 0, image=bg_photo, anchor="nw")
        except Exception:
            pass

        # --- Main container (offset from edges so the background frame is visible) ---
        body = tk.Frame(main_canvas, bg=colors["bg"], padx=14, pady=12)
        content_win = main_canvas.create_window(10, 10, anchor="nw", window=body)
        body.columnconfigure(0, weight=1)

        _bg_last_size = [w, h]

        def _on_bg_resize(event):
            if bg_item and bg_original:
                ew, eh = event.width, event.height
                if ew > 1 and eh > 1 and (ew, eh) != tuple(_bg_last_size):
                    _bg_last_size[:] = [ew, eh]
                    try:
                        resized = bg_original.resize((ew, eh), Image.Resampling.LANCZOS)
                        bg_photo = ImageTk.PhotoImage(resized)
                        setattr(self, f"led_settings_bg_s{stream}", bg_photo)
                        main_canvas.itemconfig(bg_item, image=bg_photo)
                    except Exception:
                        pass
            main_canvas.itemconfigure(content_win, width=event.width - 20)

        main_canvas.bind("<Configure>", _on_bg_resize)

        def section(title):
            """A block with the same frame style as the main GUI panels."""
            frame = tk.LabelFrame(
                body,
                text=f" {title}",
                font=("Segoe UI", 10, "bold"),
                bg=colors["bg"],
                fg=colors["text_main"],
                bd=2,
                relief="flat",
                highlightthickness=1,
                highlightbackground=colors["border"]
            )
            frame.pack(fill="x", pady=8)
            frame.columnconfigure(0, weight=1)
            return frame

        def add_fields(frame, entries):
            for i, (key, label) in enumerate(entries):
                tk.Label(frame, text=label, anchor="e",
                         bg=colors["bg"], fg=colors["text_main"]).grid(
                    row=i, column=0, sticky="e", padx=(0, 8), pady=2)
                entry = tk.Entry(frame, textvariable=_var(key), width=10, justify="right",
                                 bg=colors["panel_bg"], fg=colors["text_main"],
                                 insertbackground=colors["text_main"], relief="flat",
                                 highlightthickness=1, highlightbackground=colors["border"],
                                 highlightcolor=colors["accent"])
                entry.grid(row=i, column=1, sticky="we", pady=2)

        add_fields(section("Crystal Current (mA)"), [
            ("r_ma", "Red R"),
            ("g_ma", "Green G"),
            ("b_ma", "Blue B"),
        ])

        add_fields(section("LED Driver IC (mA)"), [
            ("mcu_ma", "Consumption per LED"),
        ])

        add_fields(section("Crystal Efficiency (%)"), [
            ("eff_r_pct", "Red R"),
            ("eff_g_pct", "Green G"),
            ("eff_b_pct", "Blue B"),
        ])

        add_fields(section("Losses"), [
            ("loss_pct", "PSU and wire losses, %"),
        ])

        add_fields(section("LED Density"), [
            ("density_w", "Along width (LED/m)"),
            ("density_h", "Along height (LED/m)"),
        ])

        add_fields(section("Environment"), [
            ("ambient_c", "Air temperature (°C)"),
        ])

        # --- TEMP MAP: calculation on/off toggle ---
        tm_frame = section("🌡 Temp Map")

        def _tm_enabled():
            return bool(st.get("temp_map_enabled", True))

        def _tm_refresh():
            if _tm_enabled():
                tm_var.set("⏸ Calculation: ON  (click to disable)")
                tm_status.config(text="Background heat simulation is running",
                                 fg=colors["success"])
            else:
                tm_var.set("▶ Calculation: OFF  (click to enable)")
                tm_status.config(text="Background heat simulation is paused",
                                 fg=colors["text_dim"])

        def _tm_toggle():
            st["temp_map_enabled"] = not _tm_enabled()
            _tm_refresh()
            print(f"[INFO] S{stream} Temp Map calculation: "
                  f"{'enabled' if st['temp_map_enabled'] else 'disabled'}")

        tm_var = tk.StringVar()
        ttk.Button(tm_frame, textvariable=tm_var, command=_tm_toggle).grid(
            row=0, column=0, sticky="we", padx=8, pady=(8, 2))
        tm_status = tk.Label(
            tm_frame, text="", anchor="w",
            font=("Segoe UI", 9),
            bg=colors["bg"], fg=colors["text_dim"]
        )
        tm_status.grid(row=1, column=0, sticky="we", padx=10, pady=(0, 8))
        _tm_refresh()

        # --- Live readout (auto-updated while the window is open) ---
        readout_frame = section("Current Values")
        readout = tk.Label(readout_frame, text="", anchor="w", justify="left",
                           font=("Consolas", 10),
                           bg=colors["panel_bg"], fg=colors["text_main"])
        readout.grid(row=0, column=0, sticky="we", padx=8, pady=(6, 8), ipady=6)

        def _poll():
            try:
                if not win.winfo_exists():
                    return
                p = getattr(self, f"led_power_s{stream}", None) or {}
                if p.get("leds", 0) > 0:
                    readout.config(
                        text=(
                            f"LED:     {p['leds']}\n"
                            f"Current: {p['current_ma'] / 1000.0:.2f} A\n"
                            f"Power:   {p['power_w']:.2f} W (incl. losses)"
                        ),
                        fg=colors["success"]
                    )
                else:
                    readout.config(text="Stream is off or has no WLED modules",
                                   fg=colors["text_dim"])
            except Exception:
                pass
            try:
                win.after(300, _poll)
            except Exception:
                pass

        win.after(300, _poll)

        def _on_close():
            try:
                setattr(self, attr, None)
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", _on_close)

    # =========================
    # THERMAL MAP (background LED heat simulation)
    # =========================
    def _stream_grid_size(self, stream: int):
        """Matrix grid size (W, H) in LED cells for the stream."""
        # Thread-safe Tk read: this is also called from the thermal loop
        # worker thread (real-time density rebuild). An unguarded .get()
        # from a worker thread KILLS that thread (see _tkget).
        try:
            if stream == 1:
                w = self._tkget(self.input_target_w, None, retries=200)
                h = self._tkget(self.input_target_h, None, retries=200)
            else:
                w = self._tkget(self.input_target2_w, None, retries=200)
                h = self._tkget(self.input_target2_h, None, retries=200)
            w = int(w)
            h = int(h)
        except Exception:
            w, h = TARGET_W, TARGET_H
        return max(2, w), max(2, h)

    def _create_thermal_model(self, stream: int):
        """Create a thermal model for the stream from the live LED settings."""
        try:
            st = self._led_settings(stream)
            w, h = self._stream_grid_size(stream)
            cfg = ThermalConfig(
                leds_x=w, leds_y=h,
                density_x=max(1.0, float(st.get("density_w", 100.0))),
                density_y=max(1.0, float(st.get("density_h", 100.0))),
                ambient_temperature_c=float(st.get("ambient_c", 25.0)),
            )
            model = LEDThermalModel(cfg)
            self._apply_thermal_positions(model, stream)
            print(f"[OK] Thermal model S{stream}: {w}x{h} LEDs, grid {model.nx}x{model.ny}")
            return model
        except Exception as e:
            print(f"[WARN] Failed to create thermal model S{stream}: {e}")
            return None

    def _apply_thermal_positions(self, model, stream: int):
        """Set LED positions (row, col) after WLED mapping."""
        if model is None:
            return
        try:
            w, h = self._stream_grid_size(stream)
            want = 1 if stream == 1 else 2
            rows, cols = [], []
            for dev in WLED_DEVICES:
                if dev.get("stream", 1) != want or not dev.get("mapping"):
                    continue
                for r, c in dev["mapping"]:
                    if 0 <= int(r) < h and 0 <= int(c) < w:
                        rows.append(int(r))
                        cols.append(int(c))
            if rows:
                model.set_led_positions(
                    np.array(rows, dtype=np.int32),
                    np.array(cols, dtype=np.int32)
                )
        except Exception:
            pass

    def _rebuild_thermal(self, stream: int):
        """(Re)create the thermal model after mapping / grid size / LED
        density changes (density is applied in real time from the
        LED Settings window).

        The accumulated temperature state of the previous model is carried
        over, so a DLL restart, switching between screens / changing the
        mapping or editing the LED density does NOT reset the simulation
        back to ambient.
        """
        if not THERMAL_AVAILABLE:
            return
        old = getattr(self, f"thermal_model_s{stream}", None)
        model = self._create_thermal_model(stream)
        if model is not None:
            self._carry_over_thermal_state(old, model)
            setattr(self, f"thermal_model_s{stream}", model)

    def _carry_over_thermal_state(self, old, new):
        """Copy the temperature state from an old model into a new one.

        Same grid size: direct copy. Different grid size: bilinear resample
        of the old temperature field onto the new grid, so the accumulated
        heat is preserved even when the matrix size changes.
        """
        if old is None or new is None:
            return
        try:
            t_old = np.asarray(old.temperature_c, dtype=np.float64)
            if t_old.ndim != 2 or t_old.size == 0 or not np.all(np.isfinite(t_old)):
                return
            t_new = np.asarray(new.temperature_c)
            if t_new.ndim != 2 or t_new.size == 0:
                return
            if t_old.shape == t_new.shape:
                t_new[:] = t_old
            else:
                ny_o, nx_o = t_old.shape
                ny_n, nx_n = t_new.shape
                # horizontal (x) pass
                xs = np.linspace(0.0, 1.0, nx_n) * (nx_o - 1)
                xi = np.clip(np.floor(xs).astype(int), 0, nx_o - 1)
                xf = xs - xi
                ih = t_old[:, xi] + (
                    t_old[:, np.minimum(xi + 1, nx_o - 1)] - t_old[:, xi]
                ) * xf[None, :]
                # vertical (y) pass
                ys = np.linspace(0.0, 1.0, ny_n) * (ny_o - 1)
                yi = np.clip(np.floor(ys).astype(int), 0, ny_o - 1)
                yf = ys - yi
                t_new[:] = ih[yi] + (
                    ih[np.minimum(yi + 1, ny_o - 1)] - ih[yi]
                ) * yf[:, None]
            # 1-D air temperature profile (copy only when sizes match)
            ap_old = np.asarray(old.air_profile_c, dtype=np.float64)
            ap_new = np.asarray(new.air_profile_c)
            if (ap_old.ndim == 1 and ap_new.ndim == 1
                    and ap_old.size == ap_new.size and ap_old.size
                    and np.all(np.isfinite(ap_old))):
                ap_new[:] = ap_old
        except Exception as e:
            print(f"[WARN] Failed to carry over thermal state: {e}")

    def _thermal_loop(self, stream: int):
        """
        Background thermal simulation for one stream.

        Runs from the start of the application and keeps accumulating
        heat (or cooling the panel) using the latest per-LED brightness
        published by the process loop and the live LED settings.
        """
        if not THERMAL_AVAILABLE:
            return
        frame_attr = f"thermal_frame_s{stream}"
        model_attr = f"thermal_model_s{stream}"
        last = time.perf_counter()
        while self.running:
            # Temp Map calculation disabled in LED Settings - skip the work
            try:
                if not bool(self._led_settings(stream).get("temp_map_enabled", True)):
                    time.sleep(0.1)
                    last = time.perf_counter()
                    continue
            except Exception:
                pass
            model = getattr(self, model_attr, None)
            if model is None:
                time.sleep(0.05)
                last = time.perf_counter()
                continue
            # LED density (LED Settings window) — apply in REAL TIME.
            # The physical sheet size and the thermal grid are derived from
            # the density at model creation, so a density change requires a
            # model rebuild. The accumulated temperature state is carried
            # over (see _carry_over_thermal_state), so the panel does NOT
            # cool down to ambient when the user edits the density.
            try:
                st = self._led_settings(stream)
                new_dw = max(1.0, float(st.get("density_w", 100.0)))
                new_dh = max(1.0, float(st.get("density_h", 100.0)))
                if (abs(float(model.cfg.density_x) - new_dw) > 1e-6
                        or abs(float(model.cfg.density_y) - new_dh) > 1e-6):
                    print(f"[INFO] S{stream} LED density changed to "
                          f"{new_dw:g} x {new_dh:g} LED/m - rebuilding thermal model")
                    self._rebuild_thermal(stream)
                    last = time.perf_counter()
                    time.sleep(0.05)
                    continue
            except Exception:
                pass
            try:
                now = time.perf_counter()
                dt = min(max(now - last, 0.0), 0.5)
                last = now
                rgb = getattr(self, frame_attr, None)
                if rgb is not None and len(rgb) > 0 and len(rgb) == model.led_count:
                    heat = model.brightness_to_heat_power(rgb, self._led_settings(stream))
                else:
                    heat = None
                # Keep the ambient (air) temperature in sync with Settings,
                # so that the cooling baseline follows the user setting.
                try:
                    model.cfg.ambient_temperature_c = float(
                        self._led_settings(stream).get("ambient_c", model.cfg.ambient_temperature_c)
                    )
                except Exception:
                    pass
                if dt > 0.0:
                    max_dt = model.cfg.max_dt
                    steps = max(1, int(np.ceil(dt / max_dt)))
                    sdt = dt / steps
                    mcu_ma = float(self._led_settings(stream).get("mcu_ma", 0.0))
                    for _ in range(steps):
                        model.update(heat, sdt, mcu_ma=mcu_ma)
            except Exception as e:
                print(f"[WARN] Thermal loop S{stream}: {e}")
                last = time.perf_counter()
            time.sleep(0.008)

    def open_temp_map(self, stream: int):
        """Open (or focus) the live Temp Map window for one stream.

        Shows the temperature map of the LED matrix, stretched to fill
        the window (aspect ratio preserved) with the Min/Max/Mean readout.
        The simulation itself runs in the background from the start of the
        application. The image is refreshed 4 times per second.
        """
        attr = f"temp_map_win_s{stream}"
        prev = getattr(self, attr, None)
        if prev is not None:
            try:
                if prev.winfo_exists():
                    prev.lift()
                    prev.focus_set()
                    return
            except Exception:
                pass

        # Make sure a live thermal model exists for this stream
        model = getattr(self, f"thermal_model_s{stream}", None)
        if model is None and THERMAL_AVAILABLE:
            self._rebuild_thermal(stream)
            model = getattr(self, f"thermal_model_s{stream}", None)

        colors = self.colors
        win = tk.Toplevel(self.root)
        setattr(self, attr, win)
        win.title(f"Temp Map — Stream {stream}")
        win.configure(bg=colors["bg"])
        win.resizable(True, True)
        win.minsize(420, 380)

        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w = min(760, int(sw * 0.9))
        h = int(660 * 1.3)  # starting height is +30% (660 -> 858 px)
        x = int((sw - w) / 2)
        y = max(0, int((sh - h) / 2))
        win.geometry(f"{w}x{h}+{x}+{y}")

        try:
            from window_utils import apply_dark_mode_to_tk_window
            win.update_idletasks()
            apply_dark_mode_to_tk_window(win)
            win.after(100, lambda: apply_dark_mode_to_tk_window(win))
        except Exception:
            pass

        # === Рамка из background.png со всех четырёх сторон (как в окне PQ) ===
        # Основной Canvas (фон) внизу, внутренний контейнер поверх него
        main_canvas = tk.Canvas(win, highlightthickness=0, bd=0)
        main_canvas.pack(fill="both", expand=True)

        # Загружаем фоновое изображение - сохраняем оригинал для ресайза
        temp_bg_original = None
        bg_item = None
        try:
            # Use path_utils to resolve background image location
            bg_path = resolve_resource_path("background.png")
            if os.path.exists(bg_path):
                temp_bg_original = Image.open(bg_path).convert("RGBA")
                # Начальный фон под текущий размер окна
                initial_width = max(2, win.winfo_width())
                initial_height = max(2, win.winfo_height())
                bg_img = temp_bg_original.resize(
                    (initial_width, initial_height), Image.Resampling.LANCZOS
                )
                bg_photo = ImageTk.PhotoImage(bg_img)
                # Держим живую ссылку на PhotoImage
                setattr(self, f"temp_map_bg_s{stream}", bg_photo)
                bg_item = main_canvas.create_image(0, 0, image=bg_photo, anchor="nw")
        except Exception as e:
            print(f"[WARN] Failed to load background for Temp Map S{stream}: {e}")

        # Внутренний контейнер (внутри canvas) - фон виден рамкой по краям
        main_container = ttk.Frame(main_canvas)
        main_window_item = main_canvas.create_window(
            5,
            5,
            anchor="nw",
            window=main_container
        )

        # Растягиваем фон при изменении размера окна + держим контейнер внутри
        def _resize_background(event):
            if bg_item is not None and temp_bg_original is not None:
                try:
                    new_width = max(2, event.width)
                    new_height = max(2, event.height)
                    resized_img = temp_bg_original.resize(
                        (new_width, new_height), Image.Resampling.LANCZOS
                    )
                    new_photo = ImageTk.PhotoImage(resized_img)
                    setattr(self, f"temp_map_bg_s{stream}", new_photo)
                    main_canvas.itemconfig(bg_item, image=new_photo)
                except Exception:
                    pass
                main_canvas.itemconfigure(
                    main_window_item,
                    width=max(0, event.width - 10),
                    height=max(0, event.height - 10)
                )

        main_canvas.bind("<Configure>", _resize_background)
        # ========================================================================

        stats = tk.Label(
            main_container, text="", font=("Consolas", 10),
            bg=colors["bg"], fg=colors["text_main"], anchor="w", justify="left"
        )
        stats.pack(fill="x", padx=14, pady=(10, 4))

        canvas = tk.Canvas(main_container, bg="#0d0f17", highlightthickness=1,
                           highlightbackground=colors["border"])
        canvas.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        # Re-render immediately when the window/canvas is resized so the
        # map stretches with the window (dark zone stays the same size
        # as the window padding, aspect ratio preserved)
        def _on_canvas_resize(_event=None):
            try:
                m = getattr(self, f"thermal_model_s{stream}", None)
                if m is not None and win.winfo_exists():
                    self._render_temp_map(canvas, stats, legend_label, m, holder)
            except Exception:
                pass

        canvas.bind("<Configure>", _on_canvas_resize)

        bottom = tk.Frame(main_container, bg=colors["bg"])
        bottom.pack(fill="x", padx=14, pady=(0, 10))
        ttk.Button(
            bottom,
            text="Reset (cool down)",
            command=lambda s=stream: self._thermal_reset(s)
        ).pack(side="left")
        legend_label = tk.Label(
            bottom, text="", bg=colors["bg"], fg=colors["text_dim"],
            font=("Consolas", 9)
        )
        legend_label.pack(side="right")

        holder = {}

        def _poll():
            try:
                if not win.winfo_exists():
                    return
                model = getattr(self, f"thermal_model_s{stream}", None)
                if model is None:
                    stats.config(text="Model not created (thermal_model unavailable)")
                else:
                    self._render_temp_map(canvas, stats, legend_label, model, holder)
            except Exception as e:
                try:
                    stats.config(text=f"[WARN] {e}")
                except Exception:
                    pass
            try:
                win.after(250, _poll)  # 4 updates per second
            except Exception:
                pass

        win.after(250, _poll)  # 4 updates per second

        def _on_close():
            try:
                setattr(self, attr, None)
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", _on_close)

    def _render_temp_map(self, canvas, stats, legend_label, model, holder):
        """Draw the temperature map stretched to fill the canvas
        (aspect ratio preserved).

        Fixed color scale: minimum = ambient air temperature from the
        LED Settings, maximum = 70 °C. Everything above 70 °C is shown
        in white.
        """
        t = model.temperature_c.copy()
        vmin = max(0.0, float(model.cfg.ambient_temperature_c))
        vmax = 70.0  # fixed upper bound of the scale, °C

        rgb_arr = thermal_colormap(t, vmin, vmax, over_color=(255, 255, 255))
        img = Image.fromarray(rgb_arr, "RGB")

        # Display size: fill the canvas, matrix aspect ratio always
        # preserved (dark zone around the map shrinks to the same size
        # as the window padding / outer frame)
        cw = max(160, canvas.winfo_width())
        ch = max(160, canvas.winfo_height())
        scale = min(float(cw), float(ch)) / float(max(img.size))
        dw = max(2, int(img.size[0] * scale))
        dh = max(2, int(img.size[1] * scale))
        img = img.resize((dw, dh), Image.BILINEAR)

        photo = ImageTk.PhotoImage(img)
        canvas.delete("all")
        canvas.create_image(cw // 2, ch // 2, image=photo)
        holder["image"] = photo  # keep a reference

        # Sheet size in cm, derived from LED count and density
        # (width = leds_x / density_x, height = leds_y / density_y)
        sheet_w_cm = float(model.width_m) * 100.0
        sheet_h_cm = float(model.height_m) * 100.0
        stats.config(
            text=(
                f"Min {float(t.min()):.1f} °C | Max {float(t.max()):.1f} °C | "
                f"Mean {float(t.mean()):.1f} °C | "
                f"Grid {model.nx}x{model.ny} | LEDs {model.led_count} | "
                f"Sheet {sheet_w_cm:.1f} x {sheet_h_cm:.1f} cm"
            )
        )
        over_count = int(np.sum(t > vmax)) if t.size else 0
        legend_label.config(
            text=f"Scale: {vmin:.0f} ... {vmax:.0f} °C (above {vmax:.0f} °C — white)"
            + (f" | >{vmax:.0f} °C: {over_count} cl." if over_count else "")
        )

    def _thermal_reset(self, stream: int):
        """Reset the thermal state of a stream to ambient temperature."""
        model = getattr(self, f"thermal_model_s{stream}", None)
        if model is not None:
            model.reset()

    def restore_from_tray(self):
        """Restore window from tray (may be called from the pystray thread)"""
        try:
            print("[INFO] Restoring window from tray...")
            # Tk must be touched from the main thread - schedule on mainloop
            self.root.after(0, self._do_restore_from_tray)
        except Exception as e:
            print(f"[ERROR] Failed to restore window: {e}")

    def _do_restore_from_tray(self):
        """Actual Tk restore work, runs in the Tk main thread (only the main window)"""
        try:
            # Do NOT re-enable previews here: they are user-controlled toggles
            # and setting them on would open preview windows again.
            # Keep any preview windows closed - restore only the main window.
            if hasattr(self, 'preview_enabled'):
                self.preview_enabled = False
            if hasattr(self, 'preview2_enabled'):
                self.preview2_enabled = False

            # Show only the main window
            self.root.deiconify()

            # If state was 'zoomed' (maximized), restore it
            if getattr(self, '_maximized_before_minimize', False):
                self.root.state('zoomed')
                print("[INFO] Window restored to maximized state")
            else:
                print("[INFO] Window restored to normal state")

            self.root.lift()
            self.root.focus_force()
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
                ('custom_gamma_window_s2', 'Custom Gamma S2'),
                ('optimization_window', 'Shader Optimization Window'),
                ('led_settings_win_s1', 'LED Settings Stream 1'),
                ('led_settings_win_s2', 'LED Settings Stream 2'),
                ('temp_map_win_s1', 'Temp Map Stream 1'),
                ('temp_map_win_s2', 'Temp Map Stream 2')
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
            
            # On exit: set the color selected in settings on ALL WLED modules
            # (the device must end up in the user's chosen state)
            try:
                self._apply_settings_color_to_all_wled()
            except Exception as e:
                print(f"[WARN] Failed to apply settings colors on exit: {e}")
            
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

    # === SET WINDOW ICON IMMEDIATELY (critical for taskbar icon on Windows 10/11) ===
    # Must be called right after Tk() creation, before withdraw() or any other config
    try:
        from PIL import Image, ImageTk
        # Method 1: iconbitmap with .ico file
        icon_path = resolve_resource_path("ico.ico")
        print(f"[INFO] Icon path resolved to: {icon_path}")
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)
            print(f"[OK] Window icon set from .ico: {icon_path}")
        else:
            print(f"[WARN] .ico file not found: {icon_path}")
    except Exception as e:
        print(f"[ERROR] iconbitmap failed: {e}")

    # Method 2: iconphoto with PNG (Windows 10/11 taskbar often needs this too)
    try:
        from PIL import Image, ImageTk
        icon_png_path = resolve_resource_path("ico.png")
        if os.path.exists(icon_png_path):
            img = Image.open(icon_png_path).resize((32, 32), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            root.iconphoto(True, photo)
            # Keep a reference to prevent garbage collection
            root._icon_photo = photo
            print(f"[OK] iconphoto set from .png: {icon_png_path}")
        else:
            print(f"[WARN] .png icon not found: {icon_png_path}")
    except Exception as e:
        print(f"[ERROR] iconphoto failed: {e}")

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
    
    # === RE-APPLY DARK TITLE BAR AFTER SHOWING WINDOW ===
    # DWM dark mode must be applied AFTER deiconify() for it to take effect
    try:
        hwnd = int(root.winfo_id())
        set_window_dark_mode(hwnd)
    except Exception:
        pass
    
    # Update FPS loop after GUI is ready
    root.after(200, app.update_gui_fps)
    
    # Initialize and start tray icon
    if app.has_tray_support:
        try:
            from pystray import MenuItem as item
            
            def create_tray_menu():
                return (
                    item('Show', app.restore_from_tray, default=True),
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
            
            # Create tray menu (first item is 'default' -> single left click activates it)
            menu = (
                item('Show', app.restore_from_tray, default=True),
                item('Exit', app.exit_application)
            )
            
            if tray_image is not None:
                import pystray
                # Single left click on the tray icon calls the default menu item ('Show')
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
                
                print("[OK] Tray icon started (left click opens window)")
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
                ('mapping_window', 'Mapping Window'),
                ('optimization_window', 'Shader Optimization Window'),
                ('led_settings_win_s1', 'LED Settings Stream 1'),
                ('led_settings_win_s2', 'LED Settings Stream 2'),
                ('temp_map_win_s1', 'Temp Map Stream 1'),
                ('temp_map_win_s2', 'Temp Map Stream 2')
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
    
    # Final cleanup: every WLED module must end up with the color
    # selected in settings (synchronous push, before the process exits)
    try:
        app._apply_settings_color_to_all_wled()
    except Exception as e:
        print(f"[WARN] Failed to apply settings colors on exit: {e}")
    
    ddp_socket.close()


if __name__ == "__main__":
    main()
