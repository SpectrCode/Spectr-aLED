"""
Optimization Window Module
Shader optimization window: FP16/FP32 switching per task
and pixel limit adjustment (8 steps + unlimited).
Adaptive window, dark theme, no scroll and extra buttons.
"""

import sys
import os
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
try:
    from path_utils import resolve_resource_path
except Exception:
    def resolve_resource_path(p):
        return p

try:
    from window_utils import apply_dark_mode_to_tk_window
except Exception:
    def apply_dark_mode_to_tk_window(window):
        pass


# Shader task list
SHADER_TASKS = [
    ("coordinate",  "Coordinate Calculation"),
    ("weights",     "Weights"),
    ("color",       "Color"),
    ("accumulator", "Accumulator"),
]

# Pixel limit values: 8 steps of 1,036,800 + unlimited
PIXEL_STEP = 1_036_800
PIXEL_LIMIT_VALUES = [PIXEL_STEP * i for i in range(1, 9)] + [0]  # 0 = unlimited
UNLIMITED = 0


def _push_to_dll(app):
    """
    Push current shader params from app state to the DLL in real-time.
    Safe to call multiple times; no-ops if DLL is not running.
    """
    bridge = getattr(app, "bridge", None)
    if not bridge:
        return

    prec = getattr(app, "shader_precision", None) or {k: "fp32" for k, _ in SHADER_TASKS}
    pixel_limit = getattr(app, "pixel_limit", 0)
    coord_mode = getattr(app, "coordinate_recalc_mode", "once")

    # Use a persistent lock from app to synchronize with capture thread
    lock = getattr(app, "dll_lock", None)
    if lock is None:
        import threading
        lock = threading.Lock()
        app.dll_lock = lock

    try:
        with lock:
            bridge.set_shader_params(
                pixel_limit=pixel_limit,
                coord_mode=coord_mode,
                prec_coord=prec.get("coordinate", "fp32"),
                prec_weights=prec.get("weights", "fp32"),
                prec_color=prec.get("color", "fp32"),
                prec_accum=prec.get("accumulator", "fp32"),
            )
    except Exception as e:
        print(f"[WARN] _push_to_dll failed: {e}")


def open_optimization_window(app):
    """
    Open the shader optimization window (adaptive, dark theme, no scroll).
    """
    # --- State ---
    if not hasattr(app, "shader_precision") or app.shader_precision is None:
        app.shader_precision = {key: "fp32" for key, _ in SHADER_TASKS}

    if not hasattr(app, "pixel_limit") or app.pixel_limit is None:
        app.pixel_limit = 0  # 0 = unlimited

    if not hasattr(app, "coordinate_recalc_mode") or app.coordinate_recalc_mode not in ("once", "frame"):
        app.coordinate_recalc_mode = "once"

    # --- Close previous window if exists ---
    if getattr(app, "optimization_window", None) is not None:
        try:
            if app.optimization_window.winfo_exists():
                app.optimization_window.destroy()
        except Exception:
            pass
        app.optimization_window = None

    colors = app.colors
    win = tk.Toplevel(app.root)
    app.optimization_window = win
    win.title("Shader Optimization")
    win.configure(bg=colors["bg"])

    # --- Dynamic size: let Tk compute natural height from content ---
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w = min(540, int(sw * 0.85))
    x = int((sw - w) / 2)
    # Use a small initial height; will be recalculated after all widgets are built
    y = max(0, int((sh - 300) / 2))
    win.geometry(f"{w}x300+{x}+{y}")
    win.minsize(w, 200)
    win.resizable(True, False)
    win.attributes("-topmost", True)

    # Apply dark title bar AFTER geometry is set (DWM requires full window)
    win.update_idletasks()
    apply_dark_mode_to_tk_window(win)
    # Re-apply after window is shown (like main.py does after deiconify)
    win.after(100, lambda: apply_dark_mode_to_tk_window(win))

    # --- Background canvas (like calibration window) ---
    main_canvas = tk.Canvas(win, highlightthickness=0, bd=0, bg=colors["bg"])
    main_canvas.pack(fill="both", expand=True)

    bg_original = None
    bg_item = None
    try:
        bg_path = resolve_resource_path("background.png")
        if os.path.exists(bg_path):
            bg_original = Image.open(bg_path).convert("RGBA")
            bg_img = bg_original.resize((w, 300), Image.Resampling.LANCZOS)
            app.optimization_window_bg = ImageTk.PhotoImage(bg_img)
            bg_item = main_canvas.create_image(0, 0, image=app.optimization_window_bg, anchor="nw")
    except Exception:
        pass

    # --- Main container (offset from edges, bg visible) ---
    main_container = tk.Frame(main_canvas, bg=colors["bg"], padx=14, pady=12)
    content_win = main_canvas.create_window(10, 10, anchor="nw", window=main_container)

    def _on_resize(event):
        if bg_item and bg_original:
            try:
                resized = bg_original.resize((event.width, event.height), Image.Resampling.LANCZOS)
                app.optimization_window_bg = ImageTk.PhotoImage(resized)
                main_canvas.itemconfig(bg_item, image=app.optimization_window_bg)
            except Exception:
                pass
        main_canvas.itemconfigure(content_win, width=event.width - 20)

    main_canvas.bind("<Configure>", _on_resize)

    # === HEADER ===
    tk.Label(
        main_container,
        text="⚙  Shader Optimization",
        font=("Segoe UI", 13, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
    ).pack(anchor="w", pady=(0, 4))

    tk.Frame(main_container, height=2, bg=colors["border"]).pack(fill="x", pady=(4, 12))

    # === PRECISION SECTION ===
    prec_section = tk.LabelFrame(
        main_container,
        text="",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
        bd=2,
        relief="flat",
        highlightthickness=1,
        highlightbackground=colors["border"],
        padx=10,
        pady=8,
    )
    prec_section.pack(fill="x", pady=(0, 12))

    tk.Label(
        prec_section,
        text="Precision per task",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
    ).pack(anchor="w", pady=(0, 4))

    for key, label in SHADER_TASKS:
        row = tk.Frame(prec_section, bg=colors["bg"])
        row.pack(fill="x", pady=2)

        tk.Label(row, text=label, width=22, anchor="w", bg=colors["bg"], fg=colors["text_main"]).pack(side="left")

        var = tk.StringVar(value=app.shader_precision.get(key, "fp32"))
        if not hasattr(app, "_optim_precision_vars"):
            app._optim_precision_vars = {}
        app._optim_precision_vars[key] = var

        tk.Radiobutton(
            row, text="FP16", variable=var, value="fp16",
            bg=colors["bg"], fg=colors["text_main"],
            selectcolor=colors["panel_bg"], activebackground=colors["bg"], activeforeground=colors["text_main"],
        ).pack(side="left", padx=(8, 4))
        tk.Radiobutton(
            row, text="FP32", variable=var, value="fp32",
            bg=colors["bg"], fg=colors["text_main"],
            selectcolor=colors["panel_bg"], activebackground=colors["bg"], activeforeground=colors["text_main"],
        ).pack(side="left", padx=4)

    def _sync_precision(*args):
        vars_ = getattr(app, "_optim_precision_vars", {})
        app.shader_precision = {k: v.get() for k, v in vars_.items()}
        print("[OPTIM] Precision:", app.shader_precision)
        _push_to_dll(app)

    for var in (getattr(app, "_optim_precision_vars", {}) or {}).values():
        var.trace_add("write", _sync_precision)

    # === PIXEL LIMIT ===
    samp_section = tk.LabelFrame(
        main_container,
        text="",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
        bd=2,
        relief="flat",
        highlightthickness=1,
        highlightbackground=colors["border"],
        padx=10,
        pady=8,
    )
    samp_section.pack(fill="x", pady=(0, 8))

    tk.Label(
        samp_section,
        text="Pixel Limit (source pixel limit)",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
    ).pack(anchor="w", pady=(0, 4))

    tk.Label(
        samp_section,
        text="If the image exceeds the limit, some pixels are skipped.\n"
             f"Step: {PIXEL_STEP:,} px.  0 = unlimited.",
        font=("Segoe UI", 8),
        bg=colors["bg"],
        fg=colors.get("text_secondary", colors["text_main"]),
    ).pack(anchor="w", pady=(0, 6))

    pixel_scale_var = tk.IntVar(
        value=PIXEL_LIMIT_VALUES.index(app.pixel_limit)
        if app.pixel_limit in PIXEL_LIMIT_VALUES else len(PIXEL_LIMIT_VALUES) - 1
    )

    pixel_label_var = tk.StringVar()

    def _resolve_pixel_label():
        idx = pixel_scale_var.get()
        val = PIXEL_LIMIT_VALUES[idx]
        if val == 0:
            pixel_label_var.set("∞  (unlimited)")
            app.pixel_limit = 0
        else:
            pixel_label_var.set(f"{val:,}")
            app.pixel_limit = val
        print(f"[OPTIM] Pixel limit = {val}")
        _push_to_dll(app)

    _resolve_pixel_label()

    # Slider row
    slider_row = tk.Frame(samp_section, bg=colors["bg"])
    slider_row.pack(fill="x", pady=(4, 6))

    scale = tk.Scale(
        slider_row,
        from_=0,
        to=len(PIXEL_LIMIT_VALUES) - 1,
        orient="horizontal",
        variable=pixel_scale_var,
        command=lambda _v: _resolve_pixel_label(),
        bg=colors["bg"],
        fg=colors["text_main"],
        troughcolor=colors["border"],
        highlightthickness=0,
        showvalue=0,
    )
    scale.pack(side="left", fill="x", expand=True, padx=(0, 10))

    tk.Label(
        slider_row,
        textvariable=pixel_label_var,
        font=("Consolas", 10, "bold"),
        width=16,
        anchor="e",
        bg=colors["bg"],
        fg=colors["text_main"],
    ).pack(side="left")

    # === COORDINATE RECALCULATION MODE ===
    coord_section = tk.LabelFrame(
        main_container,
        text="",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
        bd=2,
        relief="flat",
        highlightthickness=1,
        highlightbackground=colors["border"],
        padx=10,
        pady=8,
    )
    coord_section.pack(fill="x", pady=(0, 8))

    tk.Label(
        coord_section,
        text="Coordinate Calculation Mode",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
    ).pack(anchor="w", pady=(0, 4))

    # Ensure the mode is valid, but never hardcode "once" here —
    # respect the value already stored on the app (e.g. loaded from config).
    if getattr(app, "coordinate_recalc_mode", None) not in ("once", "frame"):
        app.coordinate_recalc_mode = "once"

    if not hasattr(app, "_optim_coord_var"):
        app._optim_coord_var = tk.StringVar(value=app.coordinate_recalc_mode)
    else:
        app._optim_coord_var.set(app.coordinate_recalc_mode)
    coord_var = app._optim_coord_var

    coord_row = tk.Frame(coord_section, bg=colors["bg"])
    coord_row.pack(fill="x", pady=(4, 4))

    tk.Radiobutton(
        coord_row,
        text="Once (cached)",
        variable=coord_var,
        value="once",
        bg=colors["bg"], fg=colors["text_main"],
        selectcolor=colors["panel_bg"],
        activebackground=colors["bg"], activeforeground=colors["text_main"],
    ).pack(side="left", padx=(0, 10))

    tk.Radiobutton(
        coord_row,
        text="Every frame (recalculate)",
        variable=coord_var,
        value="frame",
        bg=colors["bg"], fg=colors["text_main"],
        selectcolor=colors["panel_bg"],
        activebackground=colors["bg"], activeforeground=colors["text_main"],
    ).pack(side="left")

    def _sync_coord_mode(*args):
        try:
            mode = coord_var.get()
        except Exception:
            return
        if mode not in ("once", "frame"):
            return
        app.coordinate_recalc_mode = mode
        print(f"[OPTIM] Coordinate recalc mode = {mode}")
        _push_to_dll(app)

    coord_var.trace_add("write", _sync_coord_mode)

    # === SEPARABLE PIPELINE MODE ===
    sep_section = tk.LabelFrame(
        main_container,
        text="",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
        bd=2,
        relief="flat",
        highlightthickness=1,
        highlightbackground=colors["border"],
        padx=10,
        pady=8,
    )
    sep_section.pack(fill="x", pady=(0, 8))

    tk.Label(
        sep_section,
        text="Separable 2-Pass Pipeline",
        font=("Segoe UI", 10, "bold"),
        bg=colors["bg"],
        fg=colors["text_main"],
    ).pack(anchor="w", pady=(0, 2))

    tk.Label(
        sep_section,
        text="Eliminates warp divergence with non-proportional scale.\n"
             "Result is identical, no quality loss.",
        font=("Segoe UI", 8),
        bg=colors["bg"],
        fg=colors.get("text_secondary", colors["text_main"]),
    ).pack(anchor="w")

    # Initialize state
    if not hasattr(app, "use_separable") or app.use_separable is None:
        bridge = getattr(app, "bridge", None)
        if bridge and hasattr(bridge, "get_separable_mode"):
            app.use_separable = bridge.get_separable_mode()
        else:
            app.use_separable = True  # default: on

    if not hasattr(app, "_optim_sep_var"):
        app._optim_sep_var = tk.BooleanVar(value=app.use_separable)
    else:
        app._optim_sep_var.set(app.use_separable)
    sep_var = app._optim_sep_var

    def _sync_separable(*args):
        enabled = sep_var.get()
        app.use_separable = enabled
        print(f"[OPTIM] Separable mode = {'ON' if enabled else 'OFF'}")
        bridge = getattr(app, "bridge", None)
        if bridge and hasattr(bridge, "set_separable_mode"):
            lock = getattr(app, "dll_lock", None)
            if lock is None:
                import threading
                lock = threading.Lock()
                app.dll_lock = lock
            try:
                with lock:
                    bridge.set_separable_mode(enabled)
            except Exception as e:
                print(f"[WARN] set_separable_mode failed: {e}")

    sep_row = tk.Frame(sep_section, bg=colors["bg"])
    sep_row.pack(fill="x", pady=(4, 0))

    tk.Checkbutton(
        sep_row,
        text="Enabled",
        variable=sep_var,
        command=_sync_separable,
        bg=colors["bg"], fg=colors["text_main"],
        selectcolor=colors["panel_bg"],
        activebackground=colors["bg"], activeforeground=colors["text_main"],
    ).pack(side="left")

    # Status indicator
    sep_status_var = tk.StringVar(value="✓ Active" if app.use_separable else "✗ Disabled")
    sep_status_label = tk.Label(
        sep_row,
        textvariable=sep_status_var,
        font=("Consolas", 9, "bold"),
        bg=colors["bg"],
        fg=colors["accent"],
    )
    sep_status_label.pack(side="left", padx=(12, 0))

    # Update status when toggled
    def _update_sep_status(*args):
        if sep_var.get():
            sep_status_var.set("✓ Active")
            sep_status_label.configure(fg=colors["accent"])
        else:
            sep_status_var.set("✗ Disabled")
            sep_status_label.configure(fg=colors.get("text_secondary", "#888"))

    sep_var.trace_add("write", _update_sep_status)

    # Quick-pick buttons (in one row)
    quick_row = tk.Frame(samp_section, bg=colors["bg"])
    quick_row.pack(fill="x", pady=(4, 0))

    for i, v in enumerate(PIXEL_LIMIT_VALUES):
        text = "∞" if v == 0 else f"{v // 1_000_000}M"
        tk.Button(
            quick_row,
            text=text,
            width=4,
            command=lambda idx=i: (pixel_scale_var.set(idx), _resolve_pixel_label()),
            bg=colors["panel_bg"], fg=colors["text_main"],
            activebackground=colors["accent"], activeforeground=colors["bg"],
            relief="flat", bd=0,
        ).pack(side="left", padx=2, pady=2)

    # --- Auto-fit height to content ---
    def _auto_fit_height():
        win.update_idletasks()
        try:
            content_h = main_container.winfo_reqheight()
        except Exception:
            content_h = 500
        # Add top/bottom canvas offsets (10px top + 10px bottom) + safety margin
        final_h = content_h + 40
        # Cap at 90% of screen
        max_h = int(sh * 0.9)
        final_h = max(300, min(final_h, max_h))
        win.geometry(f"{w}x{final_h}+{x}+{y}")
        # Update background to new size
        if bg_item and bg_original:
            try:
                resized = bg_original.resize((w, final_h), Image.Resampling.LANCZOS)
                app.optimization_window_bg = ImageTk.PhotoImage(resized)
                main_canvas.itemconfig(bg_item, image=app.optimization_window_bg)
            except Exception:
                pass

    win.after(50, _auto_fit_height)

    # --- Protocol ---
    def _close():
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", _close)

    print("[OK] Optimization window opened")
    return win
