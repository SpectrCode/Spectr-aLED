"""
PQ Curve Editor Module for Stream 1
Для управления окном редактора PQ кривой для Stream 1
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

# Import dark mode utility for Windows title bar
try:
    from window_utils import apply_dark_mode_to_tk_window
except ImportError:
    def apply_dark_mode_to_tk_window(window):
        pass


def open_pq_curve_s1(app):
    """
    Открыть окно PQ Curve Editor для Stream 1
    
    Args:
        app: Экземпляр приложения GPUCaptureApp
    """
    # Установка режима для Stream 1
    pq_mode_var = app.pq_rgb_mode1
    values_r = app.pq_values_r1
    values_g = app.pq_values_g1
    values_b = app.pq_values_b1
    strength_var = app.pq_curve_strength1
    bias_var = app.pq_curve_bias1
    
    # Проверка, открыто ли окно уже (закрыть старое)
    if app.pq_window is not None and app.pq_window.winfo_exists():
        try:
            app.pq_window.destroy()
        except:
            pass
        app.pq_window = None
    
    win = tk.Toplevel(app.root)
    app.pq_window = win
    win.title("PQ Curve Editor (Stream 1)")
    
    # Apply dark mode to title bar
    apply_dark_mode_to_tk_window(win)
    
    # Set window to always stay on top
    win.attributes("-topmost", True)
    
    # Адаптивный размер окна под разрешение экрана (как в custom_gamma)
    # Рассчитываем размер, чтобы все ползунки + график поместились без скроллинга (RGB режим по умолчанию)
    win.update_idletasks()
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    
    # Расчёт необходимых размеров для контента:
    # Каждый panel: graph(240px) + label(~15px) + slider(240px) = ~510px
    # Controls bar: ~60px
    # Padding + scrollbar + frame overhead: ~100px
    # RGB режим (по умолчанию): 1 панель видна = ~670px высота
    # Ширина: рассчитываем на основе количества точек PQ
    
    num_columns = app.pq_points if hasattr(app, 'pq_points') else 17
    
    # Базовый размер - рассчитываем чтобы вместить все ползунки и график в RGB режиме
    optimal_width = max(1400, int(screen_width * 0.85))   # Достаточно для колонок с запасом
    optimal_height = max(700, int(screen_height * 0.70))  # graph(240) + sliders(240) + controls(~60) + padding
    
    # Ограничиваем доступным экраном
    margin_percent = 0.99
    usable_width = int(screen_width * margin_percent)
    usable_height = int(screen_height * margin_percent)
    
    base_width = min(optimal_width, usable_width)
    base_height = min(optimal_height, usable_height)
    
    # Установка размера и позиции по центру экрана
    x_position = int((screen_width - base_width) / 2)
    y_position = int((screen_height - base_height) / 2)
    win.geometry(f"{base_width}x{base_height}+{x_position}+{y_position}")
    
    # Гарантируем центрирование: пересчитываем позицию после полной отрисовки окна
    def center_window_on_update():
        win.update_idletasks()
        actual_w = win.winfo_width()
        actual_h = win.winfo_height()
        # Если размеры ещё не определены (Tkinter возвращает 1 по умолчанию), выходим
        if actual_w < 100 or actual_h < 100:
            win.after(100, center_window_on_update)
            return
        center_x = int((screen_width - actual_w) / 2)
        center_y = int((screen_height - actual_h) / 2)
        win.geometry(f"+{center_x}+{center_y}")
    
    win.after(200, center_window_on_update)
    
    # Минимальный размер - достаточно для отображения всех ползунков (сжимаем но не скрываем)
    win.minsize(1200, 550)
    
    colors = app.colors
    
    # === FIXED: Background Canvas should be at bottom with container inside ===
    # Create main Canvas (background)
    main_canvas = tk.Canvas(win, highlightthickness=0, bd=0)
    main_canvas.pack(fill="both", expand=True)
    
    # Load background image for window - save original
    bg_item = None
    pq_bg_original = None
    try:
        # Use path_utils to resolve background image location
        bg_path = resolve_resource_path("background.png")
        if os.path.exists(bg_path):
            pq_bg_original = Image.open(bg_path).convert("RGBA")
            # Create initial scaled background - используем размеры окна
            initial_width = base_width
            initial_height = base_height
            bg_img = pq_bg_original.resize((initial_width, initial_height), Image.Resampling.LANCZOS)
            app.pq_window_bg = ImageTk.PhotoImage(bg_img)
            bg_item = main_canvas.create_image(0, 0, image=app.pq_window_bg, anchor="nw")
    except Exception as e:
        print(f"[WARN] Failed to load background for PQ window: {e}")
    
    # Main container (inside canvas) - use padding only on sides
    main_container = ttk.Frame(main_canvas, padding=(10, 8))
    
    main_window = main_canvas.create_window(
        5,
        5,
        anchor="nw",
        window=main_container
    )
    
    # Stretch Canvas on window resize - SCALE BACKGROUND IN BOTH DIRECTIONS
    def _resize_background(event):
        if bg_item and pq_bg_original:
            try:
                new_width = event.width
                new_height = event.height
                # Stretch background to entire Canvas (in both directions)
                resized_img = pq_bg_original.resize((new_width, new_height), Image.Resampling.LANCZOS)
                app.pq_window_bg = ImageTk.PhotoImage(resized_img)
                main_canvas.itemconfig(bg_item, image=app.pq_window_bg)
            except:
                pass
            main_canvas.itemconfigure(
                main_window,
                width=event.width - 10,
                height=event.height - 10
            )
    
    main_canvas.bind("<Configure>", _resize_background)
    
    # Main scrollable container with compact padding
    canvas_frame = ttk.Frame(main_container)
    canvas_frame.pack(fill="both", expand=True, padx=2, pady=2)
    
    canvas = tk.Canvas(canvas_frame, bg=colors["bg"], highlightthickness=0)
    scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
    container = ttk.Frame(canvas, padding=(0, 0))
    
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
    
    ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=4)
    
    tk.Label(controls, text="Mode:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
    
    def set_pq_mode(mode):
        pq_mode_var.set(mode)
        update_mode_visibility()
    
    ttk.Radiobutton(
        controls,
        text="Mono",
        variable=pq_mode_var,
        value="rgb",
        command=lambda: set_pq_mode("rgb")
    ).pack(side="left", padx=(12, 4))
    
    ttk.Radiobutton(
        controls,
        text="RGB",
        variable=pq_mode_var,
        value="separate",
        command=lambda: set_pq_mode("separate")
    ).pack(side="left", padx=4)
    
    ttk.Separator(controls, orient="vertical").pack(side="left", fill="y", padx=12)
    
    tk.Label(controls, text="Curve Strength:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
    
    strength_scale = tk.Scale(
        controls,
        from_=0.5,
        to=4.0,
        resolution=0.1,
        orient="horizontal",
        variable=strength_var,
        length=250,
        bg=colors["bg"],
        fg=colors["text_main"],
        troughcolor=colors["border"],
        highlightthickness=0
    )
    strength_scale.pack(side="left", padx=(8, 16))
    
    tk.Label(controls, text="Shadow Bias:", bg=colors["bg"], fg=colors["text_dim"]).pack(side="left")
    
    bias_scale = tk.Scale(
        controls,
        from_=0.0,
        to=0.050,
        resolution=0.005,
        orient="horizontal",
        variable=bias_var,
        length=200,
        bg=colors["bg"],
        fg=colors["text_main"],
        troughcolor=colors["border"],
        highlightthickness=0
    )
    bias_scale.pack(side="left", padx=(8, 16))
    
    def reset_curve():
        strength_var.set(3.0)
        bias_var.set(0.0)
        rebuild_from_controls()
    
    def save_pq_curve():
        """Save PQ curve values to file"""
        path = filedialog.asksaveasfilename(parent=win, title="Save PQ Curve", filetypes=[("JSON files", "*.json")], defaultextension=".json")
        if not path:
            return
        try:
            import json
            pq_values = {
                "stream": 1,
                "mode": app.pq_rgb_mode1.get(),
                "strength": strength_var.get(),
                "bias": bias_var.get(),
                "values_r": [float(values_r[i]) for i in range(app.pq_points)],
                "values_g": [float(values_g[i]) for i in range(app.pq_points)],
                "values_b": [float(values_b[i]) for i in range(app.pq_points)]
            }
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(pq_values, f, indent=2)
            print(f"[OK] PQ Curve S1 saved to: {path}")
        except Exception as e:
            print(f"[ERROR] Failed to save PQ Curve S1: {e}")
    
    def load_pq_curve():
        """Load PQ curve values from file"""
        path = filedialog.askopenfilename(parent=win, title="Load PQ Curve", filetypes=[("JSON files", "*.json")], defaultextension=".json")
        if not path:
            return
        try:
            import json
            with open(path, 'r', encoding='utf-8') as f:
                pq_values = json.load(f)
            
            # Update values from loaded data
            n = min(len(values_r), len(pq_values.get("values_r", [])))
            for i in range(n):
                values_r[i] = float(pq_values["values_r"][i])
                values_g[i] = float(pq_values["values_g"][i])
                values_b[i] = float(pq_values["values_b"][i])
            
            # Update UI controls
            strength_var.set(float(pq_values.get("strength", 3.0)))
            bias_var.set(float(pq_values.get("bias", 0.025)))
            
            if "mode" in pq_values:
                app.pq_rgb_mode1.set(pq_values["mode"])
            
            rebuild_from_controls()
            print(f"[OK] PQ Curve S1 loaded from: {path}")
        except Exception as e:
            print(f"[ERROR] Failed to load PQ Curve S1: {e}")
    
    # Buttons row for Save/Load
    buttons_frame = tk.Frame(controls, bg=colors["bg"])
    buttons_frame.pack(side="left", padx=(8, 4))
    
    ttk.Button(
        buttons_frame,
        text="💾 Save",
        command=save_pq_curve
    ).pack(side="left", padx=(0, 4))
    
    ttk.Button(
        buttons_frame,
        text="📂 Load",
        command=load_pq_curve
    ).pack(side="left", padx=(0, 4))
    
    ttk.Button(
        controls,
        text="🔄 Reset",
        command=reset_curve
    ).pack(side="left", padx=(24, 8))
    
    # Body
    body = tk.Frame(container, bg=colors["bg"])
    body.pack(fill="both", expand=True)
    
    app.pq_sliders.clear()
    
    def create_panel(parent, values_ref, color):
        panel = tk.Frame(parent, bg=colors["bg"])
        
        # Store slider variables for sync
        var_list = []
        
        # Graph - fills full width
        graph_height = 240
        graph = tk.Canvas(panel, height=graph_height, bg="#111", highlightthickness=0)
        graph.pack(fill="x", pady=(0, 6))
        
        sliders_frame = tk.Frame(panel, bg=colors["bg"])
        sliders_frame.pack(fill="x")
        
        # Grid container for columns - distributes width evenly
        grid_container = tk.Frame(sliders_frame, bg=colors["bg"])
        grid_container.pack(fill="both", expand=True)
        
        n_cols = app.pq_points
        
        # Draw graph function
        def draw_graph():
            graph.delete("curve")
            
            w = graph.winfo_width()
            h = graph.winfo_height()
            
            if w < 10:
                return
            
            pts = []
            
            for i in range(len(values_ref)):
                x = int(i / (len(values_ref) - 1) * w)
                y = int((1.0 - values_ref[i]) * h)
                pts.extend([x, y])
            
            graph.create_line(*pts, fill=color, width=2, smooth=1, tags="curve")
        
        def on_slider(idx, val):
            values_ref[idx] = float(val) / 10000.0
            draw_graph()
        
        # Create columns in grid - each gets equal width (no padding for tight layout)
        for i in range(n_cols):
            col = tk.Frame(grid_container, bg=colors["bg"])
            col.grid(row=0, column=i, sticky="nsew")
            
            # Nits value label (top of column)
            tk.Label(
                col,
                text=str(int(app.pq_nits[i])),
                font=("Consolas", 7),
                bg=colors["bg"],
                fg=colors["text_dim"]
            ).pack()
            
            var = tk.IntVar(value=int(values_ref[i] * 10000))
            var_list.append(var)
            
            scale = tk.Scale(
                col,
                from_=10000,
                to=0,
                variable=var,
                orient="vertical",
                resolution=9.765625,
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
        
        graph.bind("<Configure>", lambda e: draw_graph())
        
        return panel, var_list, draw_graph
    
    # RGB panel - use stream-specific values
    rgb_panel, rgb_sliders, rgb_draw = create_panel(body, values_r, "#00ff88")
    
    # R panel - use stream-specific values
    r_panel, r_sliders, r_draw = create_panel(body, values_r, "#ff4040")
    
    # G panel - use stream-specific values
    g_panel, g_sliders, g_draw = create_panel(body, values_g, "#40ff40")
    
    # B panel - use stream-specific values
    b_panel, b_sliders, b_draw = create_panel(body, values_b, "#4090ff")
    
    def update_mode_visibility():
        rgb_panel.pack_forget()
        r_panel.pack_forget()
        g_panel.pack_forget()
        b_panel.pack_forget()
        
        if pq_mode_var.get() == "rgb":
            rgb_panel.pack(fill="both", expand=True)
        else:
            r_panel.pack(fill="x", pady=4)
            g_panel.pack(fill="x", pady=4)
            b_panel.pack(fill="x", pady=4)
    
    # Stream-specific base generation
    def generate_stream_base():
        return app.generate_pq_exponential(
            strength=strength_var.get(),
            points=app.pq_points
        )
    
    def rebuild_from_controls():
        base = generate_stream_base()
        
        base = app.apply_shadow_bias_to_curve(base, bias_var.get())
        
        # Update stream-specific values (Stream 1)
        app.pq_values_r1[:] = base
        app.pq_values_g1[:] = base
        app.pq_values_b1[:] = base
        
        # Also update old variables for compatibility (use Stream 1 as default)
        app.pq_values[:] = base
        app.pq_values_r[:] = base
        app.pq_values_g[:] = base
        app.pq_values_b[:] = base
        
        for i, s in enumerate(rgb_sliders):
            s.set(int(base[i] * 10000))
        
        for i, s in enumerate(r_sliders):
            s.set(int(base[i] * 10000))
        
        for i, s in enumerate(g_sliders):
            s.set(int(base[i] * 10000))
        
        for i, s in enumerate(b_sliders):
            s.set(int(base[i] * 10000))
        
        rgb_draw()
        r_draw()
        g_draw()
        b_draw()
    
    strength_scale.config(command=lambda e: rebuild_from_controls())
    bias_scale.config(command=lambda e: rebuild_from_controls())
    
    update_mode_visibility()
    
    # Window close handler - reset reference
    win.bind("<Destroy>", lambda e: setattr(app, 'pq_window', None))
    
    win.after(50, lambda: (rgb_draw(), r_draw(), g_draw(), b_draw()))