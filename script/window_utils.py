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

def _get_frame_hwnd(hwnd):
    """
    Resolve the frame (caption) HWND from a Tkinter HWND.

    For tk.Toplevel, winfo_id() returns the *inner* child HWND, not the
    top-level frame window that DWM paints the caption on.  GetParent()
    gives us the real frame HWND.  For tk.Tk (root) winfo_id() already
    returns the frame HWND, so GetParent() will return 0 and we keep the
    original value.

    Args:
        hwnd: Integer HWND from winfo_id()

    Returns:
        int: The frame HWND to pass to DWM / Win32 APIs.
    """
    parent = ctypes.windll.user32.GetParent(wintypes.HWND(int(hwnd)))
    if parent:
        return int(parent)
    return int(hwnd)


def set_window_dark_mode(hwnd):
    """
    Enable dark mode for a specific window via DwmSetWindowAttribute.

    Uses:
      DWMWA_USE_IMMERSIVE_DARK_MODE          = 20   (Win10 20H1+, Win11)
      DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19 (older Win10)

    IMPORTANT: For Toplevel windows, winfo_id() returns the *inner*
    child HWND.  This function automatically resolves the correct
    frame HWND via GetParent() before calling DWM.

    Both attribute 20 AND 19 are set (not just as fallback) because
    some Windows builds silently accept attr=20 (return S_OK) without
    actually applying it, while only honouring attr=19, and vice-versa.

    Args:
        hwnd: Window handle from tkinter winfo_id() (integer or HWND)

    Returns:
        bool: True if at least one DWM call succeeded, False otherwise
    """
    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19

    # Resolve the real frame HWND (handles Toplevel inner-HWND issue)
    frame_hwnd = _get_frame_hwnd(hwnd)

    value = c_int(1)

    # --- attr 20 (Win11 / Win10 2004+) ---
    res20 = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(frame_hwnd),
        DWMWA_USE_IMMERSIVE_DARK_MODE,
        byref(value),
        sizeof(value)
    )

    # --- attr 19 (Win10 before 20H1) ---
    # Always call this too: some Win10 builds silently accept attr=20
    # (return S_OK) but only honour attr=19.
    res19 = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(frame_hwnd),
        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1,
        byref(value),
        sizeof(value)
    )

    # Force the window frame to repaint so the dark title bar appears immediately.
    # SWP_NOMOVE(2) | SWP_NOSIZE(1) | SWP_NOZORDER(4) | SWP_FRAMECHANGED(32)
    ctypes.windll.user32.SetWindowPos(
        wintypes.HWND(frame_hwnd),
        0,  # hWndInsertAfter (ignored)
        0, 0, 0, 0,  # x, y, cx, cy (ignored)
        0x0027,  # SWP_NOMOVE(2)|SWP_NOSIZE(1)|SWP_NOZORDER(4)|SWP_FRAMECHANGED(32)
    )

    return (res20 == 0) or (res19 == 0)


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

    The function automatically resolves the correct frame HWND via
    GetParent(), so it works for both Tk (root) and Toplevel windows.

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


# ──────────────────────────────────────────────
# Dark mode for non-Tk windows (cv2/OpenCV, etc.)
# ──────────────────────────────────────────────

def apply_dark_mode_to_window_by_title(title):
    """
    Apply dark mode title bar to a window found by its title (caption text).

    Use this for OpenCV (cv2) windows, which are created via native Win32
    and do not inherit the tkinter dark mode settings.

    Args:
        title: The window caption text (e.g. "Preview", "Preview2")

    Returns:
        bool: True if dark mode was applied, False otherwise
    """
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if not hwnd:
            return False
        # Ensure process-wide dark mode is enabled
        enable_process_dark_mode()
        return set_window_dark_mode(int(hwnd))
    except Exception:
        return False
