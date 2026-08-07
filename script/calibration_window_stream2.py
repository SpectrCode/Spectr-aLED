"""
LED Calibration Window for Stream 2
"""

import sys
import tkinter as tk
from tkinter import ttk
import os
from PIL import Image, ImageTk

# Import path utilities
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from path_utils import resolve_resource_path


def open_calibration_window2(parent_app, calibration: dict):
    """
    Open Stream 2 LED Calibration window
    
    Args:
        parent_app: The GPUCaptureApp instance (self from main app)
        calibration: Calibration dictionary with color coefficients
    """
    # Check if window is already open
    if parent_app.calibration_window2 is not None and parent_app.calibration_window2.winfo_exists():
        # If window exists, focus on it
        try:
            parent_app.calibration_window2.lift()
            parent_app.calibration_window2.focus_force()
        except:
            pass
        return
    
    win = tk.Toplevel(parent_app.root)
    parent_app.calibration_window2 = win
    win.title("LED Calibration (Stream 2)")
    
    # Set window to always stay on top
    win.attributes("-topmost", True)
    
    # Adaptive window size
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    max_width = int(screen_w * 0.9)
    max_height = int(screen_h * 0.85)
    
    # Calibration window - fixed size 1280x300
    win.geometry("1280x300")
    
    colors = parent_app.colors
    
    # === FIXED: Background Canvas should be at bottom with container inside ===
    # Create main Canvas (background)
    main_canvas = tk.Canvas(win, highlightthickness=0, bd=0)
    main_canvas.pack(fill="both", expand=True)
    
    # Load background image for window - save original
    bg_item = None
    calib_bg_original2 = None
    try:
        # Use path_utils to resolve background image location
        bg_path = resolve_resource_path("background.png")
        if os.path.exists(bg_path):
            calib_bg_original2 = Image.open(bg_path).convert("RGBA")
            # Create initial scaled background
            initial_width = win.winfo_width() if win.winfo_width() > 1 else 1280
            initial_height = win.winfo_height() if win.winfo_height() > 1 else 300
            bg_img = calib_bg_original2.resize((initial_width, initial_height), Image.Resampling.LANCZOS)
            parent_app.calibration_window2_bg = ImageTk.PhotoImage(bg_img)
            bg_item = main_canvas.create_image(0, 0, image=parent_app.calibration_window2_bg, anchor="nw")
    except Exception as e:
        print(f"[WARN] Failed to load background for calibration window 2: {e}")
    
    # Main container (inside canvas)
    main_container = ttk.Frame(main_canvas, padding=(12, 8))
    
    main_window = main_canvas.create_window(
        10,
        10,
        anchor="nw",
        window=main_container
    )
    
    # Stretch Canvas on window resize - SCALE BACKGROUND IN BOTH DIRECTIONS
    def _resize_background(event):
        if bg_item and calib_bg_original2:
            try:
                new_width = event.width
                new_height = event.height
                # Stretch background to entire Canvas (in both directions)
                resized_img = calib_bg_original2.resize((new_width, new_height), Image.Resampling.LANCZOS)
                parent_app.calibration_window2_bg = ImageTk.PhotoImage(resized_img)
                main_canvas.itemconfig(bg_item, image=parent_app.calibration_window2_bg)
            except:
                pass
        main_canvas.itemconfigure(main_window, width=event.width - 20)
    
    main_canvas.bind("<Configure>", _resize_background)
    
    container = ttk.Frame(main_container, padding=(5, 0))
    container.pack(fill="both", expand=True)
    
    # Top bar
    top = ttk.Frame(container, padding=(0, 4))
    top.pack(fill="x")
    
    ttk.Label(top, text="LUT Size:").pack(side="left")
    
    lut_combo = ttk.Combobox(
        top,
        textvariable=parent_app.lut_size2,
        values=[32, 64, 96, 128, 160, 192, 224, 256],
        width=6,
        state="readonly"
    )
    lut_combo.pack(side="left", padx=(8, 12))
    
    ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=12)
    
    parent_app.create_checkbox_with_var(top, "Use external LUT", parent_app.external_lut_enabled)
    
    def load_sdr():
        parent_app.load_external_lut(2, "SDR")
    
    ttk.Button(
        top,
        text="📂 Load SDR LUT",
        command=load_sdr
    ).pack(side="left", padx=(8, 4))
    
    def load_hdr():
        parent_app.load_external_lut(2, "HDR")
    
    ttk.Button(
        top,
        text="📂 Load HDR LUT",
        command=load_hdr
    ).pack(side="left", padx=4)
    
    # Calibration display order
    display_order = [
        ("White", "white", ["R", "G", "B"]),
        ("RGB", None, [
            ("Red", "blue", "R"),
            ("Green", "green", "G"),
            ("Blue", "red", "B"),
        ]),
        ("Yellow", "cyan", ["R", "G"]),
        ("Cyan", "yellow", ["G", "B"]),
        ("Magenta", "magenta", ["R", "B"]),
    ]
    
    channel_map = {"R": 2, "G": 1, "B": 0}
    
    sliders = []
    
    def get_lut_size():
        return max(2, int(parent_app.lut_size2.get()))
    
    def slider_to_coeff(v):
        return float(v) / (get_lut_size() - 1)
    
    def coeff_to_slider(v):
        return int(float(v) * (get_lut_size() - 1))
    
    def on_lut_generated(lut):
        """LUT async generation completion handler"""
        parent_app.global_lut2 = lut
    
    def rebuild_lut():
        # Asynchronous LUT generation with callback
        from image_processor import generate_3d_lut_async
        generate_3d_lut_async(
            calibration,
            size=get_lut_size(),
            callback=lambda lut: parent_app.root.after(0, lambda l=lut: setattr(parent_app, 'global_lut2', l))
        )
    
    def update_all_sliders():
        size = get_lut_size()
        max_val = size - 1
        
        for scale, value_var, zone, idx in sliders:
            scale.config(to=max_val)
            
            val = coeff_to_slider(calibration[zone][idx])
            scale.set(val)
            value_var.set(str(val))
    
    def on_lut_change(event=None):
        update_all_sliders()
        rebuild_lut()
    
    lut_combo.bind("<<ComboboxSelected>>", on_lut_change)
    
    def add_slider_row(parent, zone, ch_name):
        idx = channel_map[ch_name]
        
        row = tk.Frame(parent, bg=colors["bg"])
        row.pack(fill="x", pady=2, padx=4)
        
        tk.Label(row, text=ch_name, width=3, bg=colors["bg"], fg=colors["text_main"]).pack(side="left")
        
        value_var = tk.StringVar(value=str(coeff_to_slider(calibration[zone][idx])))
        
        ttk.Label(row, textvariable=value_var, width=4).pack(side="right", padx=(8, 0))
        
        slider_var = tk.IntVar(value=coeff_to_slider(calibration[zone][idx]))
        
        def update(v, z=zone, i=idx, vv=value_var):
            iv = int(float(v))
            vv.set(str(iv))
            calibration[z][i] = slider_to_coeff(iv)
            rebuild_lut()
        
        scale = tk.Scale(
            row,
            from_=0,
            to=get_lut_size() - 1,
            orient="horizontal",
            variable=slider_var,
            showvalue=False,
            resolution=1,
            command=update,
            bg=colors["bg"],
            fg=colors["text_main"],
            troughcolor=colors["border"],
            highlightthickness=0,
            width=8
        )
        scale.pack(side="left", fill="x", expand=True)
        
        sliders.append((scale, value_var, zone, idx))
        
        val = coeff_to_slider(calibration[zone][idx])
        scale.set(val)
        value_var.set(str(val))
    
    for item in display_order:
        title = item[0]
        
        block = tk.LabelFrame(
            container,
            text=title,
            bg=colors["bg"],
            fg=colors["text_main"],
            font=("Segoe UI", 9),
            bd=2,
            relief="flat",
            highlightthickness=1,
            highlightbackground=colors["border"]
        )
        block.pack(side="left", fill="y", expand=True, padx=(4, 8))
        
        if title == "RGB":
            for label, zone, ch_name in item[2]:
                add_slider_row(block, zone, ch_name)
        else:
            zone = item[1]
            for ch_name in item[2]:
                add_slider_row(block, zone, ch_name)
    
    # Window close handler - reset reference
    win.bind("<Destroy>", lambda e: setattr(parent_app, 'calibration_window2', None))