"""
Window utilities for dark mode title bar on Windows.

Uses Microsoft's recommended approach for Windows 11:
  SetPreferredAppMode (from uxtheme.dll) called once at process startup.
  This enables dark caption buttons and dark title bar for ALL windows
  created by the process, without needing per-window calls.

Fallback: DwmSetWindowAttribute with DWMWA_USE_IMMERSIVE_DARK_MODE
          (works on Win10 1809+ and older Win11 builds).
"""
import ctypes
from ctypes import wintypes, c_int, c_uint32, byref, sizeof

# ──────────────────────────────────────────────
# Process-wide dark mode (Win11 recommended)
# ──────────────────────────────────────────────

_SET_PREFERRED_APP_MODE_CALLED = False


def enable_process_dark_mode():
    """
    Call SetPreferredAppMode(1) from uxtheme.dll to enable dark title bars
    for ALL windows in this process.

    This is the Microsoft-recommended approach for Windows 11 and works on:
      - Windows 11 (all versions)
      - Windows 10 version 20H1+ (build 19041+)

    Must be called ONCE early in the application, before any windows are shown.
    Calling it multiple times is safe but unnecessary.

    Returns:
        bool: True on success, False otherwise
    """
    global _SET_PREFERRED_APP_MODE_CALLED

    if _SET_PREFERRED_APP_MODE_CALLED:
        return True  # Already done, no need to call again

    try:
        # SetPreferredAppMode is an undocumented export from uxtheme.dll
        # Return type: int (>=0 = success)
        # Parameter  : int  1 = Dark mode, 0 = Light mode, -1 = Default (system)
        preferred_app_mode = ctypes.windll.uxtheme.SetPreferredAppMode
        preferred_app_mode.restype = c_int
        preferred_app_mode.argtypes = [c_int]

        result = preferred_app_mode(1)  # 1 = Dark
        _SET_PREFERRED_APP_MODE_CALLED = True
        return result >= 0

    except Exception:
        return False


# ──────────────────────────────────────────────
# Per-window dark mode fallback (DWM API)
# ──────────────────────────────────────────────

def set_window_dark_mode(hwnd):
    """
    Enable dark mode for a specific window via DwmSetWindowAttribute.

    Uses:
      DWMWA_USE_IMMERSIVE_DARK_MODE          = 20   (Win10 20H1+, Win11)
      DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19 (older Win10)

    Also forces the non-client area to repaint so the change is visible
    immediately.

    Args:
        hwnd: Window handle (integer or HWND)

    Returns:
        bool: True on success, False otherwise
    """
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19

    value = c_int(1)

    # Try the newer attribute first (Windows 11 / Win10 2004+)
    res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        DWMWA_USE_IMMERSIVE_DARK_MODE,
        byref(value),
        sizeof(value)
    )

    # Fallback for older Windows 10 (before 20H1)
    if res != 0:
        res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1,
            byref(value),
            sizeof(value)
        )

    # Force the window frame to repaint so the dark title bar appears immediately.
    # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
    ctypes.windll.user32.SetWindowPos(
        wintypes.HWND(hwnd),
        0,  # hWndInsertAfter (ignored)
        0, 0, 0, 0,  # x, y, cx, cy (ignored)
        0x0047,  # SWP_NOMOVE(2)|SWP_NOSIZE(1)|SWP_NOZORDER(4)|SWP_NOACTIVATE(64)|SWP_FRAMECHANGED(32)
    )

    return res == 0


# ──────────────────────────────────────────────
# Tkinter convenience wrapper
# ──────────────────────────────────────────────

def apply_dark_mode_to_tk_window(window):
    """
    Apply dark mode title bar to a tkinter window (Tk or Toplevel).

    This calls BOTH the process-wide SetPreferredAppMode AND the per-window
    DwmSetWindowAttribute for maximum compatibility.

    IMPORTANT: Call this AFTER the OS window handle is fully created.
    For the main Tk window, call it after root.update_idletasks().
    For Toplevel windows, call it after geometry() or use win.after().

    Args:
        window: tkinter Tk or Toplevel instance

    Returns:
        bool: True on success, False otherwise
    """
    try:
        # Ensure process-wide dark mode is enabled
        enable_process_dark_mode()

        hwnd = int(window.winfo_id())
        return set_window_dark_mode(hwnd)
    except Exception:
        return False