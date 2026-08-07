"""
Custom Gamma Menu Module for Stream 1
Для управления окном редактора Custom Gamma для Stream 1
"""

import sys
import os
import tkinter as tk
from tkinter import ttk, filedialog
import numpy as np
from PIL import Image, ImageTk

# Import path utilities
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from path_utils import resolve_resource_path


def generate_custom_gamma_curve(strength: float = 3.0, points: int = 64) -> np.ndarray:
    """Generate custom gamma curve with given strength - matches PQ behavior"""
    x = np.linspace(0.0, 1.0, points)
    y = np.power(x, strength)
    return np.clip(y * 255.0, 0.0, 255.0).astype(np.float32)


def apply_shadow_bias_to_custom_gamma(values: np.ndarray, bias: float) -> np.ndarray:
    """Apply shadow bias to custom gamma curve - matches PQ behavior"""
    if bias <= 0.0:
        return values
    
    y = values / 255.0
    n = len(y)
    idx = np.arange(n)
    
    start = 0
    peak = 10
    mid = 14
    end = 20
    
    weight = np.zeros_like(y)
    
    t1 = (idx - start) / (peak - start)
    t1 = np.clip(t1, 0.0, 1.0)
    rise = np.sin(t1 * np.pi / 2.0)
    
    t2 = (idx - peak) / (mid - peak)
    t2 = np.clip(t2, 0.0, 1.0)
    fall1 = 1.0 - 0.01 * np.sin(t2 * np.pi / 2.0)
    
    w_mid = 0.99
    
    t3 = (idx - mid) / (end - mid)
    t3 = np.clip(t3, 0.0, 1.0)
    
    target = 0.01
    fall2 = target + (w_mid - target) * np.cos(t3 * np.pi / 2.0)
    
    mask_rise = (idx >= start) & (idx <= peak)
    mask_fall1 = (idx >= peak) & (idx <= mid)
    mask_fall2 = (idx >= mid) & (idx <= end)
    
    weight[mask_rise] = rise[mask_rise]
    weight[mask_fall1] = fall1[mask_fall1]
    weight[mask_fall2] = fall2[mask_fall2]
    
    weight[idx > end] = 0.0
    weight[idx < start] = 0.0
    
    lift = (1.0 - y)
    # Match PQ bias calculation: bias is already in range [0, 1], apply directly without extra power
    out = y + bias * lift * weight
    
    return np.clip(out, 0.0, 1.0) * 255.0


def apply_highlight_pull_to_custom_gamma(values: np.ndarray, pull: float) -> np.ndarray:
    """Apply highlight pull to custom gamma curve - сильнее влияет на верхнюю часть графика"""
    if pull <= 0.0:
        return values
    
    y = values / 255.0
    n = len(y)
    idx = np.arange(n)
    
    # Определяем диапазон для верхней части (яркие области)
    start_highlight = 35  # Начало верхней части (приблизительно 14/64 * 255 ≈ 55, но берем чуть раньше)
    end_highlight = 63    # Конец верхней части
    
    # Создаем весовую маску - больше влияние в верхней части
    weight = np.zeros_like(y)
    
    # Для верхней части: создаем градуированный эффект
    # Чем выше значение, тем сильнее влияние
    for i in range(start_highlight, end_highlight + 1):
        # Нормализованный индекс от 0 до 1 в диапазоне highlight
        highlight_ratio = (i - start_highlight) / (end_highlight - start_highlight)
        
        # Вес увеличивается по квадратичной кривой к верху
        # Это создает эффект: сильнее просаживает вверху, меньше внизу
        weight[i] = highlight_ratio ** 1.5
    
    # pull_strength - сила эффекта (чем больше, тем сильнее "подтягивается" кривая)
    # Сжимаем верхнюю часть вниз (вычитаем из y)
    pull_strength = pull * 0.6  # Нормализация силы
    
    # Эффект: сильнее влияет на яркие области
    out = y - pull_strength * weight * y
    
    return np.clip(out, 0.0, 1.0) * 255.0


def open_custom_gamma_menu_s1(app):
    """
    Открыть окно Custom Gamma для Stream 1
    
    Args:
        app: Экземпляр приложения GPUCaptureApp
    """
    # Проверка, открыто ли окно уже
    if app.custom_gamma_window_s1 is not None and app.custom_gamma_window_s1.winfo_exists():
        try:
            app.custom_gamma_window_s1.lift()
            app.custom_gamma_window_s1.focus_force()
        except:
            pass
        return
    
    win = tk.Toplevel(app.root)
    app.custom_gamma_window_s1 = win
    win.title("Custom Gamma S1 - Curve Editor")
    
    # Set window to always stay on top
    win.attributes("-topmost", True)
    
    # Адаптивный размер окна под разрешение экрана
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    
    # Отступы от краев экрана (5% от каждого края)
    margin_percent = 0.95
    usable_width = int(screen_width * margin_percent)
    usable_height = int(screen_height * margin_percent)
    
    # Базовый размер окна
    base_width = min(2500, usable_width)
    base_height = min(800, usable_height)
    
    # Установка размера и позиции по центру
    x_position = int((screen_width - base_width) / 2)
    y_position = int((screen_height - base_height) / 2)
    win.geometry(f"{base_width}x{base_height}+{x_position}+{y_position}")
    
    # Минимальный размер для корректного отображения элементов
    win.minsize(600, 400)
    
    colors = app.colors
    
    # === FIXED: Background Canvas should be at bottom with container inside ===
    # Create main Canvas (background)
    main_canvas = tk.Canvas(win, highlightthickness=0, bd=0)
    main_canvas.pack(fill="both", expand=True)
    
    # Load background image for window - save original
    bg_item = None
    gamma_bg_original1 = None
    try:
        # Use path_utils to resolve background image location
        bg_path = resolve_resource_path("background.png")
        if os.path.exists(bg_path):
            gamma_bg_original1 = Image.open(bg_path).convert("RGBA")
            # Create initial scaled background - используем размеры окна
            initial_width = base_width
            initial_height = base_height
            bg_img = gamma_bg_original1.resize((initial_width, initial_height), Image.Resampling.LANCZOS)
            app.custom_gamma_s1_bg = ImageTk.PhotoImage(bg_img)
            bg_item = main_canvas.create_image(0, 0, image=app.custom_gamma_s1_bg, anchor="nw")
    except Exception as e:
        print(f"[WARN] Failed to load background for custom gamma window S1: {e}")
    
    # Main container (inside canvas)
    main_container = ttk.Frame(main_canvas, padding=(20, 15))
    
    main_window = main_canvas.create_window(
        10,
        10,
        anchor="nw",
        window=main_container
    )
    
    # Stretch Canvas on window resize - SCALE BACKGROUND IN BOTH DIRECTIONS
    def _resize_background(event):
        if bg_item and gamma_bg_original1:
            try:
                new_width = event.width
                new_height = event.height
                # Stretch background to entire Canvas (in both directions)
                resized_img = gamma_bg_original1.resize((new_width, new_height), Image.Resampling.LANCZOS)
                app.custom_gamma_s1_bg = ImageTk.PhotoImage(resized_img)
                main_canvas.itemconfig(bg_item, image=app.custom_gamma_s1_bg)
            except:
                pass
            main_canvas.itemconfigure(
                main_window,
                width=event.width - 20,
                height=event.height - 20
            )
    
    main_canvas.bind("<Configure>", _resize_background)
    
    canvas = tk.Canvas(main_container, bg=colors["bg"], highlightthickness=0)
    scrollbar = ttk.Scrollbar(main_container, orient="vertical", command=canvas.yview)
    container = ttk.Frame(canvas, padding=(0, 5))
    
    container.bind(
        "<Configure>",
        lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
    )
    
    canvas.create_window((0, 0), window=container, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")
    
    # Controls
    controls = tk.Frame(container, bg=colors["bg"])
    controls.pack(fill="x", pady=(0, 10))
    
    ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=8)
    
    tk.Label(controls, text="Mode:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
    
    def set_mode(mode):
        app.custom_gamma_rgb_mode1.set(mode)
        update_mode_visibility()
    
    ttk.Radiobutton(
        controls,
        text="RGB",
        variable=app.custom_gamma_rgb_mode1,
        value="rgb",
        command=lambda: set_mode("rgb")
    ).pack(side="left", padx=(12, 4))
    
    ttk.Radiobutton(
        controls,
        text="RGB separate",
        variable=app.custom_gamma_rgb_mode1,
        value="separate",
        command=lambda: set_mode("separate")
    ).pack(side="left", padx=4)
    
    ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=12)
    
    # Curve Strength slider
    tk.Label(controls, text="Curve:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
    
    curve_strength_var = tk.DoubleVar(value=2.0)
    strength_scale = tk.Scale(
        controls,
        from_=0.5,
        to=4.0,
        resolution=0.1,
        orient="horizontal",
        variable=curve_strength_var,
        length=200,
        bg=colors["bg"],
        fg=colors["text_main"],
        troughcolor=colors["border"],
        highlightthickness=0
    )
    strength_scale.pack(side="left", padx=(8, 16))
    
    # Shadow Bias slider
    tk.Label(controls, text="Bias:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
    
    shadow_bias_var = tk.DoubleVar(value=0.0)
    bias_scale = tk.Scale(
        controls,
        from_=0.0,
        to=0.050,
        resolution=0.005,
        orient="horizontal",
        variable=shadow_bias_var,
        length=150,
        bg=colors["bg"],
        fg=colors["text_main"],
        troughcolor=colors["border"],
        highlightthickness=0
    )
    bias_scale.pack(side="left", padx=(8, 16))
    
    # Enable/Disable toggle
    gamma_enabled_var = tk.BooleanVar(value=True)
    
    # Хранилище для сохранения значений при отключении
    saved_strength_on_disable = None
    saved_bias_on_disable = None
    
    def toggle_gamma():
        """Toggle custom gamma on/off"""
        global saved_strength_on_disable, saved_bias_on_disable
        
        if not gamma_enabled_var.get():
            # Disable: сохраняем текущие значения ползунков перед сбросом кривой
            global saved_strength_on_disable, saved_bias_on_disable
            saved_strength_on_disable = curve_strength_var.get()
            saved_bias_on_disable = shadow_bias_var.get()
            
            # Сохраняем в main.py для долгосрочного хранения
            app.saved_curve_strength1.set(saved_strength_on_disable)
            app.saved_bias1.set(saved_bias_on_disable)
            
            # Сбрасываем кривую на линейные значения (0, 4, 8... 252, 255)
            for i in range(64):
                if i == 63:
                    val = 255.0
                else:
                    val = float(i * 4)
                app.custom_gamma_sdr_r1[i] = val
                app.custom_gamma_sdr_g1[i] = val
                app.custom_gamma_sdr_b1[i] = val
            
            # ВАЖНО: Не сбрасываем ползунки - они показывают актуальные значения!
        else:
            # Enable: применяем сохраненные значения curve и bias к кривой
            update_curve_from_controls()
        
        rebuild_sliders()
    
    def update_curve_from_controls():
        """Update curve based on strength and bias sliders"""
        # Генерируем кривую только если включено
        if gamma_enabled_var.get():
            base = generate_custom_gamma_curve(strength=curve_strength_var.get(), points=64)
            biased = apply_shadow_bias_to_custom_gamma(base, shadow_bias_var.get())
            
            app.custom_gamma_sdr_r1[:] = biased[:]
            app.custom_gamma_sdr_g1[:] = biased[:]
            app.custom_gamma_sdr_b1[:] = biased[:]
            rebuild_sliders()
        
        # Save curve and bias values to main.py
        app.saved_curve_strength1.set(curve_strength_var.get())
        app.saved_bias1.set(shadow_bias_var.get())
    
    def on_slider_change(idx, val):
        """Handle individual slider changes"""
        idx_val = int(val)
        
        # Update all channels based on slider position (RGB mode only)
        app.custom_gamma_sdr_r1[idx] = float(idx_val)
        app.custom_gamma_sdr_g1[idx] = float(idx_val)
        app.custom_gamma_sdr_b1[idx] = float(idx_val)
        
        rgb_draw()
        r_draw()
        g_draw()
        b_draw()
    
    def rebuild_sliders():
        """Rebuild all sliders on reset"""
        for i, sl in enumerate(rgb_sliders):
            val = int(app.custom_gamma_sdr_r1[i])
            sl.set(val)
        
        for i, s in enumerate(r_sliders):
            val = int(app.custom_gamma_sdr_r1[i])
            s.set(val)
        
        for i, s in enumerate(g_sliders):
            val = int(app.custom_gamma_sdr_g1[i])
            s.set(val)
        
        for i, s in enumerate(b_sliders):
            val = int(app.custom_gamma_sdr_b1[i])
            s.set(val)
        
        # Update graphs
        rgb_draw()
        r_draw()
        g_draw()
        b_draw()
    
    def reset_curve():
        """Reset to linear gamma with values at step of 4: 0, 4, 8... 252, 255"""
        for i in range(64):
            if i == 63:
                val = 255.0
            else:
                val = float(i * 4)
            app.custom_gamma_sdr_r1[i] = val
            app.custom_gamma_sdr_g1[i] = val
            app.custom_gamma_sdr_b1[i] = val
        
        # Reset slider values
        for i, sl in enumerate(rgb_sliders):
            if i == 63:
                sl.set(255)
            else:
                sl.set(i * 4)
        
        for i, s in enumerate(r_sliders):
            if i != 63:
                s.set(i * 4)
            else:
                s.set(255)
        
        for i, s in enumerate(g_sliders):
            if i != 63:
                s.set(i * 4)
            else:
                s.set(255)
        
        for i, s in enumerate(b_sliders):
            if i != 63:
                s.set(i * 4)
            else:
                s.set(255)
        
        # Reset control sliders
        curve_strength_var.set(2.0)
        shadow_bias_var.set(0.0)
        
        # Also reset saved values in main.py to defaults
        app.saved_curve_strength1.set(3.0)
        app.saved_bias1.set(0.0)
        
        rebuild_sliders()
    
    def save_custom_gamma():
        """Save custom gamma values to file"""
        path = filedialog.asksaveasfilename(parent=win, title="Save Custom Gamma", filetypes=[("JSON files", "*.json")], defaultextension=".json")
        if not path:
            return
        try:
            import json
            gamma_values = {
                "stream": 1,
                "mode": app.custom_gamma_rgb_mode1.get(),
                "strength": curve_strength_var.get(),
                "bias": shadow_bias_var.get(),
                "enabled": gamma_enabled_var.get(),
                "values_r": [float(app.custom_gamma_sdr_r1[i]) for i in range(64)],
                "values_g": [float(app.custom_gamma_sdr_g1[i]) for i in range(64)],
                "values_b": [float(app.custom_gamma_sdr_b1[i]) for i in range(64)]
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(gamma_values, f, indent=2)
            print(f"[OK] Custom Gamma S1 saved to: {path}")
        except Exception as e:
            print(f"[ERROR] Failed to save Custom Gamma S1: {e}")
    
    def load_custom_gamma():
        """Load custom gamma values from file"""
        path = filedialog.askopenfilename(parent=win, title="Load Custom Gamma", filetypes=[("JSON files", "*.json")], defaultextension=".json")
        if not path:
            return
        try:
            import json
            with open(path, 'r', encoding='utf-8') as f:
                gamma_values = json.load(f)
            
            # Update values from loaded data
            for i in range(64):
                app.custom_gamma_sdr_r1[i] = float(gamma_values.get("values_r", [0]*64)[i])
                app.custom_gamma_sdr_g1[i] = float(gamma_values.get("values_g", [0]*64)[i])
                app.custom_gamma_sdr_b1[i] = float(gamma_values.get("values_b", [0]*64)[i])
            
            # Update UI controls
            curve_strength_var.set(float(gamma_values.get("strength", 2.0)))
            shadow_bias_var.set(float(gamma_values.get("bias", 0.0)))
            
            if "mode" in gamma_values:
                app.custom_gamma_rgb_mode1.set(gamma_values["mode"])
            
            # Update saved values for future use
            app.saved_curve_strength1.set(curve_strength_var.get())
            app.saved_bias1.set(shadow_bias_var.get())
            
            rebuild_sliders()
            print(f"[OK] Custom Gamma S1 loaded from: {path}")
        except Exception as e:
            print(f"[ERROR] Failed to load Custom Gamma S1: {e}")

    # Buttons row for Save/Load
    buttons_frame = tk.Frame(controls, bg=colors["bg"])
    buttons_frame.pack(side="left", padx=(8, 4))
    
    ttk.Button(
        buttons_frame,
        text="💾 Save",
        command=save_custom_gamma
    ).pack(side="left", padx=(0, 4))
    
    ttk.Button(
        buttons_frame,
        text="📂 Load",
        command=load_custom_gamma
    ).pack(side="left", padx=(0, 4))
    
    ttk.Button(
        controls,
        text="🔄 Reset",
        command=reset_curve
    ).pack(side="left", padx=(24, 8))
    
    # Set up slider callbacks with proper binding
    def create_slider_callback(idx):
        return lambda v: on_slider_change(idx, v)
    
    for i in range(64):
        pass  # Prepare callback functions
    
    strength_scale.config(command=lambda e: win.after_idle(update_curve_from_controls))
    bias_scale.config(command=lambda e: win.after_idle(update_curve_from_controls))
    
    ttk.Checkbutton(
        controls,
        text="Enable",
        variable=gamma_enabled_var,
        command=toggle_gamma
    ).pack(side="left", padx=(12, 8))
    
    # Body - panels for R, G, B
    body = tk.Frame(container, bg=colors["bg"])
    body.pack(fill="both", expand=True)
    
    def create_gamma_panel(parent, values_ref, color):
        """Create panel with sliders and graph for one channel - Dense layout"""
        panel = tk.Frame(parent, bg=colors["bg"])
        
        # Graph (upper part) - same as PQ editor
        graph_height = 240
        graph = tk.Canvas(panel, height=graph_height, bg="#111", highlightthickness=0)
        graph.pack(fill="x", pady=(0, 6))
        
        sliders_frame = tk.Frame(panel, bg=colors["bg"])
        sliders_frame.pack(fill="x")
        
        # Grid container for columns - distributes width evenly (like PQ editor)
        grid_container = tk.Frame(sliders_frame, bg=colors["bg"])
        grid_container.pack(fill="both", expand=True)
        
        sliders = []
        slider_vars = []  # Store IntVar references
        
        def draw_graph():
            """Draw curve with interpolation"""
            graph.delete("curve")
            
            w = graph.winfo_width()
            h = graph.winfo_height()
            
            if w < 10:
                return
            
            pts = []
            
            for i in range(len(values_ref)):
                x = int(i / (len(values_ref) - 1) * w)
                y = int((1.0 - values_ref[i] / 255.0) * h)
                pts.extend([x, y])
            
            graph.create_line(*pts, fill=color, width=2, smooth=1, tags="curve")
        
        def on_slider(idx, val):
            values_ref[idx] = float(val)
            draw_graph()
        
        # Pre-compute nits values (table of values 0-4-8-12...252-255)
        nits_values = []
        for j in range(64):
            if j == 63:
                nits_values.append(255)
            else:
                nits_values.append(j * 4)
        
        # Create columns in grid - each gets equal width (no padding for tight layout)
        num_cols = 64
        
        for i in range(num_cols):
            col = tk.Frame(grid_container, bg=colors["bg"])
            col.grid(row=0, column=i, sticky="nsew")
            
            # Nits value label (top of column)
            tk.Label(
                col,
                text=str(nits_values[i]),
                font=("Consolas", 7),
                bg=colors["bg"],
                fg=colors["text_dim"]
            ).pack()
            
            var = tk.IntVar(value=int(values_ref[i]))
            slider_vars.append(var)
            
            scale = tk.Scale(
                col,
                from_=256,
                to=0,
                variable=var,
                orient="vertical",
                resolution=0.5,
                showvalue=False,
                command=lambda v, idx=i: on_slider(idx, v),
                length=240,
                bg=colors["bg"],
                fg=colors["text_main"],
                troughcolor=color,
                highlightthickness=0,
                width=8
            )
            scale.pack()
            
            # Configure grid column - expand to fill all available space
            grid_container.grid_columnconfigure(i, weight=1)
            
            sliders.append(scale)
        
        graph.bind("<Configure>", lambda e: draw_graph())
        
        return panel, sliders, slider_vars, draw_graph
    
    # Panel for RGB mode (single combined panel using R values as base)
    rgb_panel, rgb_sliders, rgb_slider_vars, rgb_draw = create_gamma_panel(body, app.custom_gamma_sdr_r1, "#00ff88")
    
    # Separate panels for each channel
    r_panel, r_sliders, r_slider_vars, r_draw = create_gamma_panel(body, app.custom_gamma_sdr_r1, "#ff4040")
    g_panel, g_sliders, g_slider_vars, g_draw = create_gamma_panel(body, app.custom_gamma_sdr_g1, "#40ff40")
    b_panel, b_sliders, b_slider_vars, b_draw = create_gamma_panel(body, app.custom_gamma_sdr_b1, "#4090ff")
    
    def update_mode_visibility():
        """Switch panel visibility"""
        rgb_panel.pack_forget()
        r_panel.pack_forget()
        g_panel.pack_forget()
        b_panel.pack_forget()
        
        if app.custom_gamma_rgb_mode1.get() == "rgb":
            rgb_panel.pack(fill="both", expand=True)
        else:
            r_panel.pack(fill="x", pady=4)
            g_panel.pack(fill="x", pady=4)
            b_panel.pack(fill="x", pady=4)
    
    def rebuild_sliders_full():
        """Rebuild all sliders and update graphs"""
        for i, sl in enumerate(rgb_sliders):
            val = int(app.custom_gamma_sdr_r1[i])
            sl.set(val)
        
        for i, s in enumerate(r_sliders):
            val = int(app.custom_gamma_sdr_r1[i])
            s.set(val)
        
        for i, s in enumerate(g_sliders):
            val = int(app.custom_gamma_sdr_g1[i])
            s.set(val)
        
        for i, s in enumerate(b_sliders):
            val = int(app.custom_gamma_sdr_b1[i])
            s.set(val)
        
        rgb_draw()
        r_draw()
        g_draw()
        b_draw()
    
    def on_slider_change_full(idx, val):
        """Handle individual slider changes"""
        idx_val = int(val)
        
        # Update all channels based on slider position (RGB mode only)
        app.custom_gamma_sdr_r1[idx] = float(idx_val)
        app.custom_gamma_sdr_g1[idx] = float(idx_val)
        app.custom_gamma_sdr_b1[idx] = float(idx_val)
        
        rebuild_sliders_full()
    
    strength_scale.config(command=lambda e: win.after_idle(update_curve_from_controls))
    bias_scale.config(command=lambda e: win.after_idle(update_curve_from_controls))
    
    # Override the on_slider callbacks for individual sliders
    def make_on_slider_callback(idx, values_ref):
        def callback(val):
            values_ref[idx] = float(val)

            # Только обновляем графики
            rgb_draw()
            r_draw()
            g_draw()
            b_draw()
        return callback
    
    # Rebind slider callbacks
    for i in range(64):
        rgb_sliders[i].config(command=make_on_slider_callback(i, app.custom_gamma_sdr_r1))
        r_sliders[i].config(command=make_on_slider_callback(i, app.custom_gamma_sdr_r1))
        g_sliders[i].config(command=make_on_slider_callback(i, app.custom_gamma_sdr_g1))
        b_sliders[i].config(command=make_on_slider_callback(i, app.custom_gamma_sdr_b1))
    
    update_mode_visibility()
    
    # === FIX: Apply current curve and bias from main.py immediately on window open ===
    # Sync saved values to tkinter variables first
    strength_val = app.saved_curve_strength1.get()
    bias_val = app.saved_bias1.get()
    
    # Update local slider variables with saved values
    curve_strength_var.set(strength_val)
    shadow_bias_var.set(bias_val)
    
    # Apply the curve immediately if enabled
    if gamma_enabled_var.get():
        update_curve_from_controls()
    
    # === Also call update_curve_from_controls to ensure it's applied ===
    win.after(10, lambda: (
        update_curve_from_controls(),
        rgb_draw(), r_draw(), g_draw(), b_draw()
    ))
    
    # Window close handler - reset reference
    win.bind("<Destroy>", lambda e: setattr(app, 'custom_gamma_window_s1', None))