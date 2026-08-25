"""
WLED Color Window — individual color picker window for a WLED module.

Contents:
  - HSV wheel (hue = angle, saturation = radius) sets the BASE color;
  - vertical brightness slider (0-255) with a real gradient
    "black -> base color" — scales the base color;
  - R/G/B and HEX fields always show the FINAL color (base x bri / 255) —
    changing brightness updates the RGB channels in real time;
  - typing R/G/B or HEX sets the FINAL color and simultaneously determines
    the wheel position and brightness: brightness = max channel,
    wheel = color normalized to the max channel
    (example: (10,10,10) -> pure white on the wheel, brightness = 10);
  - values are applied IN REAL TIME as you type;
    empty/incomplete values are ignored (empty-field protection)
    and the current color is restored on focus loss;
  - 3 action buttons: "Apply to All", "Default Color" (the color selected
    in settings, passed as default_rgb; 10,10,10 only as fallback)
    and "Apply Default to All" (via the on_apply_all callback);
  - preview of the final color.

Brightness is just a linear scale of the color: final = base x bri / 255.
Every change immediately calls self.on_color_change(r, g, b, bri)
(r,g,b — final color 0-255, bri — brightness 0-255), i.e. in real time.
"""

import math
import tkinter as tk
# Import dark mode utility for Windows title bar
try:
    from window_utils import apply_dark_mode_to_tk_window
except ImportError:
    def apply_dark_mode_to_tk_window(window):
        pass



# =========================
# HSV <-> RGB (V = 0-255)
# =========================

def hsv_to_rgb(h, s, v=255):
    """h: 0-360, s: 0-1, v: 0-255 -> (r, g, b) 0-255"""
    h = ((float(h) % 360.0) + 360.0) % 360.0
    s = max(0.0, min(1.0, float(s)))
    v = max(0, min(255, int(v))) / 255.0
    c = v * s
    hp = h / 60.0
    x = c * (1.0 - abs(hp % 2.0 - 1.0))
    if hp < 1:
        rp, gp, bp = c, x, 0.0
    elif hp < 2:
        rp, gp, bp = x, c, 0.0
    elif hp < 3:
        rp, gp, bp = 0.0, c, x
    elif hp < 4:
        rp, gp, bp = 0.0, x, c
    elif hp < 5:
        rp, gp, bp = x, 0.0, c
    else:
        rp, gp, bp = c, 0.0, x
    m = v - c
    return (int(round((rp + m) * 255)), int(round((gp + m) * 255)), int(round((bp + m) * 255)))


def rgb_to_hsv(r, g, b):
    """(r, g, b) 0-255 -> (h 0-360, s 0-1, v 0-255)"""
    r = max(0, min(255, int(r))) / 255.0
    g = max(0, min(255, int(g))) / 255.0
    b = max(0, min(255, int(b))) / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    d = mx - mn
    if d < 1e-6:
        h = 0.0
        s = 0.0
    else:
        s = d / mx
        if mx == r:
            h = 60.0 * (((g - b) / d) % 6.0)
        elif mx == g:
            h = 60.0 * (((b - r) / d) + 2.0)
        else:
            h = 60.0 * (((r - g) / d) + 4.0)
    return (h % 360.0, s, int(round(mx * 255)))


def _clamp_channel(text):
    """Parse 0-255 channel value; None if invalid"""
    t = str(text).strip()
    if not t.isdigit():
        return None
    v = int(t)
    if v < 0 or v > 255:
        return None
    return v


def _mix_hex(c1, c2, frac):
    """Linear mix (c1)->(c2) by frac; returns #rrggbb (c1,c2 — tuples 0-255)"""
    r = int(round(c1[0] + (c2[0] - c1[0]) * frac))
    g = int(round(c1[1] + (c2[1] - c1[1]) * frac))
    b = int(round(c1[2] + (c2[2] - c1[2]) * frac))
    return "#%02x%02x%02x" % (r, g, b)


# =========================
# WINDOW
# =========================

class WLEDColorWindow(tk.Toplevel):
    """Individual color picker window for a WLED module (title shows the IP).

    Wheel = base color (max channel = 255), slider = brightness (0-255).
    Final color = base x bri / 255 (brightness is a simple linear scale).
    Buttons: "Apply to All" / "Default Color" (the color selected in
    settings, default_rgb; 10,10,10 only as fallback) /
    "Apply Default to All" (on_apply_all callback).
    R/G/B and HEX fields show the final color and change while moving
    the brightness slider; typing into them sets the final color and
    simultaneously defines the wheel and brightness (brightness = max
    channel, e.g. (10,10,10) -> pure white on the wheel, brightness 10).
    """

    WHEEL_SIZE = 190
    WHEEL_SUPERSAMPLE = 2  # render at Nx resolution, then subsample -> smooth AA edges
    DEFAULT_RGB = (10, 10, 10)  # default color (brightness = max channel = 10)
    # Orange overheat warning: shown when the sum of the three final
    # color channels (r + g + b, each 0-255) exceeds this threshold
    OVERHEAT_THRESHOLD = 90

    def __init__(self, master, ip, initial_rgb=(10, 10, 10),
                 initial_bri=255, default_rgb=None, colors=None,
                 on_color_change=None, on_apply_all=None):
        super().__init__(master)
        self.ip = ip
        self.on_color_change = on_color_change  # (r, g, b, bri) -> None
        # (r, g, b, bri) -> None: apply the color to ALL WLED modules
        self.on_apply_all = on_apply_all

        # "Default" color = the color selected in settings (passed by the
        # app as default_rgb); (10,10,10) is only the ultimate fallback.
        if default_rgb is not None:
            try:
                d = tuple(max(0, min(255, int(x))) for x in default_rgb)
                if max(d) > 0:
                    self.DEFAULT_RGB = d
            except Exception:
                pass

        c = colors or {}
        self.colors = c
        bg = c.get("bg", "#1a1b26")
        panel_bg = c.get("panel_bg", "#24283b")
        text_main = c.get("text_main", "#c0caf5")
        text_dim = c.get("text_dim", "#565f89")
        accent = c.get("accent", "#7aa2f7")

        # --- State -------------------------------------------------
        # Initial RGB (final color) defines both the wheel and the brightness:
        # base = color normalized to the max channel; bri = max channel.
        # (initial_bri is kept in the signature for compatibility and unused:
        #  in the new model brightness is always the max channel of the color.)
        rgb0 = tuple(max(0, min(255, int(x))) for x in initial_rgb)
        mx0 = max(rgb0)
        self.base_rgb = (tuple(int(round(v * 255.0 / mx0)) for v in rgb0)
                         if mx0 > 0 else (255, 255, 255))
        h0, s0, v0 = rgb_to_hsv(*self.base_rgb)
        self.h = h0
        self.s = s0
        self.bri = mx0

        self.title("WLED Color — " + ip)
        self.configure(bg=bg)
        # Dark title bar (matches the other app windows)
        self.after(100, lambda: apply_dark_mode_to_tk_window(self))
        self.resizable(False, False)
        try:
            master.wait_visibility(self)
            x = master.winfo_rootx() + 240
            y = master.winfo_rooty() + 40
            self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        except Exception:
            pass

        # =========================
        # BOTTOM: global buttons (packed first — full width)
        # =========================
        bottom = tk.Frame(self, bg=bg)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        def _make_action_btn(text, cmd):
            return tk.Button(
                bottom, text=text, font=("Segoe UI", 9, "bold"), command=cmd,
                bg=panel_bg, fg=text_main,
                activebackground=accent, activeforeground=bg,
                relief="flat", bd=0, cursor="hand2",
                highlightthickness=1, highlightbackground=panel_bg,
                highlightcolor=accent, padx=8, pady=7,
            )

        self.btn_apply_all = _make_action_btn("Apply to All", self._apply_to_all)
        self.btn_apply_all.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self.btn_default = _make_action_btn("Default Color", self._set_default_color)
        self.btn_default.pack(side="left", expand=True, fill="x", padx=3)
        self.btn_default_all = _make_action_btn(
            "Apply Default to All", self._apply_default_to_all)
        self.btn_default_all.pack(side="left", expand=True, fill="x", padx=(6, 0))

        if self.on_apply_all is None:
            self.btn_apply_all.config(state="disabled")
            self.btn_default_all.config(state="disabled")

        # Orange overheat warning — packed right after the bottom frame
        # (BEFORE the wheel/bright/RGB columns) so it always spans the
        # FULL WIDTH directly above the buttons. Hidden by matching the
        # text color to the window background (no re-layout on toggle).
        self._warn_bg = bg
        self._warn_orange = "#ff9e2c"
        self.warn_lbl = tk.Label(
            self,
            text="⚠ High static brightness may cause LED overheating ⚠",
            font=("Segoe UI", 9, "bold"),
            bg=bg, fg=bg, justify="center",
        )
        self.warn_lbl.pack(side="bottom", fill="x", padx=14, pady=(0, 6))

        # =========================
        # LEFT: HSV wheel (hue = angle, saturation = radius) — base color
        # =========================
        left = tk.Frame(self, bg=bg)
        left.pack(side="left", padx=(14, 10), pady=14)

        tk.Label(left, text="COLOR WHEEL", font=("Segoe UI", 8, "bold"),
                 bg=bg, fg=text_dim).pack(pady=(0, 6))

        self._wheel_cx = self.WHEEL_SIZE / 2.0
        self._wheel_r = (self.WHEEL_SIZE / 2.0) - 3.0
        self.wheel = tk.Canvas(left, width=self.WHEEL_SIZE, height=self.WHEEL_SIZE,
                               bg=panel_bg, highlightthickness=0, cursor="crosshair")
        self.wheel.pack()
        self._draw_wheel(panel_bg)
        self._wheel_indicator = self.wheel.create_oval(0, 0, 0, 0, width=2, outline="#ffffff")
        self._place_wheel_indicator()

        self.wheel.bind("<Button-1>", self._on_wheel_pick)
        self.wheel.bind("<B1-Motion>", self._on_wheel_pick)

        # =========================
        # MIDDLE: brightness slider with REAL gradient (black -> base color)
        # =========================
        mid = tk.Frame(self, bg=bg)
        mid.pack(side="left", padx=10)

        tk.Label(mid, text="BRIGHT", font=("Segoe UI", 8, "bold"),
                 bg=bg, fg=text_dim).pack(pady=(0, 6))

        BW, BH = 30, 160
        self._bri_w = BW
        self._bri_h = BH
        self.bri_canvas = tk.Canvas(mid, width=BW, height=BH, bg=panel_bg,
                                    highlightthickness=0, cursor="crosshair")
        self.bri_canvas.pack()
        self._bri_strips = []
        n_strip = 40
        for i in range(n_strip):
            frac = i / (n_strip - 1)  # 0 = bottom (black), 1 = top (base color)
            y = BH - (i + 1) * (BH / n_strip)
            strip_color = _mix_hex((0, 0, 0), self.base_rgb, frac)
            rect = self.bri_canvas.create_rectangle(1, y, BW - 1, y + BH / n_strip + 1,
                                                    fill=strip_color, outline="")
            self._bri_strips.append(rect)
        self._bri_marker = self.bri_canvas.create_line(0, 0, BW, 0,
                                                       fill="#ffffff", width=2)

        self.bri_val_lbl = tk.Label(mid, text=str(self.bri), font=("Consolas", 10, "bold"),
                                    bg=bg, fg=text_main)
        self.bri_val_lbl.pack(pady=(8, 0))
        self._update_bri_slider()

        self.bri_canvas.bind("<Button-1>", self._on_bri_pick)
        self.bri_canvas.bind("<B1-Motion>", self._on_bri_pick)

        # =========================
        # RIGHT: RGB / HEX entries (final color, real-time) + preview + status
        # =========================
        right = tk.Frame(self, bg=bg)
        right.pack(side="left", padx=(10, 14), pady=14)

        tk.Label(right, text="RGB / HEX", font=("Segoe UI", 8, "bold"),
                 bg=bg, fg=text_dim).pack(pady=(0, 8))

        self.entries = []
        for ch, val in zip("RGB", self.base_rgb):
            row = tk.Frame(right, bg=bg)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=ch, width=2, anchor="e", font=("Consolas", 10, "bold"),
                     bg=bg, fg=text_main).pack(side="left")
            e = tk.Entry(row, width=5, justify="center", font=("Consolas", 10),
                         bg=panel_bg, fg=text_main, insertbackground=text_main,
                         highlightthickness=1, highlightbackground=panel_bg,
                         highlightcolor=accent)
            e.insert(0, str(val))
            e.pack(side="left", padx=(4, 0))
            e.bind("<KeyRelease>", lambda ev: self._on_rgb_change())
            e.bind("<FocusOut>", lambda ev: self._on_rgb_focus_out())
            e.bind("<Return>", lambda ev: self._on_rgb_focus_out())
            self.entries.append(e)

        hex_row = tk.Frame(right, bg=bg)
        hex_row.pack(fill="x", pady=(10, 2))
        tk.Label(hex_row, text="#", width=2, anchor="e", font=("Consolas", 10, "bold"),
                 bg=bg, fg=text_main).pack(side="left")
        self.hex_entry = tk.Entry(hex_row, width=9, justify="center", font=("Consolas", 10),
                                  bg=panel_bg, fg=text_main, insertbackground=text_main,
                                  highlightthickness=1, highlightbackground=panel_bg,
                                  highlightcolor=accent)
        self.hex_entry.insert(0, "%02x%02x%02x" % self.base_rgb)
        self.hex_entry.pack(side="left", padx=(4, 0))
        self.hex_entry.bind("<KeyRelease>", lambda ev: self._on_hex_change())
        self.hex_entry.bind("<FocusOut>", lambda ev: self._on_hex_focus_out())
        self.hex_entry.bind("<Return>", lambda ev: self._on_hex_focus_out())

        # Preview swatch — FINAL color (base x brightness)
        tk.Label(right, text="PREVIEW", font=("Segoe UI", 8, "bold"),
                 bg=bg, fg=text_dim).pack(anchor="w", pady=(12, 4))
        self.preview = tk.Canvas(right, width=70, height=26, bg=panel_bg,
                                 highlightthickness=1, highlightbackground=text_dim)
        self._preview_rect = self.preview.create_rectangle(0, 0, 70, 26,
                                                           fill=self._final_hex(),
                                                           outline="")
        self.preview.pack(anchor="w")

        self.status_lbl = tk.Label(right, text="", font=("Segoe UI", 8),
                                   bg=bg, fg=text_dim, justify="left")
        self.status_lbl.pack(anchor="w", pady=(10, 0))

        self._refresh_entries()

    # =========================
    # COLOR HELPERS
    # =========================

    def _final_rgb(self):
        """Final RGB = base color scaled by brightness."""
        f = self.bri / 255.0
        return (int(round(self.base_rgb[0] * f)),
                int(round(self.base_rgb[1] * f)),
                int(round(self.base_rgb[2] * f)))

    def _final_hex(self):
        r, g, b = self._final_rgb()
        return "#%02x%02x%02x" % (r, g, b)

    # =========================
    # WHEEL (base color: hue x saturation)
    # =========================

    def _draw_wheel(self, panel_bg="#24283b"):
        """Draw the hue (angle) x saturation (radius) wheel with maximum
        possible detail: every single pixel gets its exact HSV color.

        The wheel is rendered at WHEEL_SUPERSAMPLE x resolution into a raw
        PPM buffer and loaded via PhotoImage(data=...) — Tk decodes the raw
        pixel data in C (fast) and the result is a perfectly smooth,
        section-free gradient. The 2x buffer is then subsampled back to
        the display size, which anti-aliases the outer edge of the wheel.
        Pixels outside the circle are filled with the panel background
        color (the canvas background is the same, so the square image
        border is invisible)."""
        ss = self.WHEEL_SUPERSAMPLE
        S = self.WHEEL_SIZE * ss
        cx = S / 2.0
        cy = S / 2.0
        r = self._wheel_r * ss

        # corners fill: panel background (hex -> bytes)
        hx = str(panel_bg).lstrip("#")
        try:
            bg3 = bytes((int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)))
        except (ValueError, IndexError):
            bg3 = bytes((36, 40, 59))

        # full 360-entry lookup table of pure hues (s=1, v=255)
        hues = [hsv_to_rgb(a, 1.0, 255) for a in range(360)]

        atan2 = math.atan2
        hypot = math.hypot
        degrees = math.degrees
        buf = bytearray()
        for y in range(S):
            dy = y + 0.5 - cy
            for x in range(S):
                dx = x + 0.5 - cx
                d = hypot(dx, dy)
                if d > r:
                    buf += bg3
                    continue
                s = d / r
                a = degrees(atan2(dx, -dy)) % 360.0
                base = hues[int(a)]
                # saturation blend: center (s -> 0) approaches white
                buf += bytes((int(255 + (base[0] - 255) * s),
                              int(255 + (base[1] - 255) * s),
                              int(255 + (base[2] - 255) * s)))

        header = b"P6\n%d %d\n255\n" % (S, S)
        try:
            img = tk.PhotoImage(data=header + bytes(buf))
        except tk.TclError:
            # Tk < 8.6 fallback: build the image with put() row strings
            img = tk.PhotoImage(width=S, height=S)
            rows = []
            p = 0
            for _ in range(S):
                chunk = buf[p:p + 3 * S]
                p += 3 * S
                rows.append(" ".join("#%02x%02x%02x" % (chunk[i], chunk[i + 1], chunk[i + 2])
                                     for i in range(0, len(chunk), 3)))
            img.put(rows)

        img = img.subsample(ss, ss)
        self._wheel_img = img  # keep a reference so GC does not destroy it
        self.wheel.delete("wheel_bg")
        self.wheel.create_image(self.WHEEL_SIZE / 2.0, self.WHEEL_SIZE / 2.0,
                                image=img, tags="wheel_bg")

    def _place_wheel_indicator(self):
        x = self._wheel_cx + self._wheel_r * self.s * math.cos(math.radians(self.h - 90.0))
        y = self.WHEEL_SIZE / 2.0 + self._wheel_r * self.s * math.sin(math.radians(self.h - 90.0))
        r = 5.0
        self.wheel.coords(self._wheel_indicator, x - r, y - r, x + r, y + r)

    def _on_wheel_pick(self, event):
        dx = event.x - self._wheel_cx
        dy = event.y - self.WHEEL_SIZE / 2.0
        dist = math.hypot(dx, dy)
        s = max(0.0, min(1.0, dist / self._wheel_r))
        h = math.degrees(math.atan2(dx, -dy)) % 360.0
        if abs(h - self.h) < 0.5 and abs(s - self.s) < 0.005:
            return
        self.h = h
        self.s = s
        self.base_rgb = hsv_to_rgb(self.h, self.s, 255)
        self._place_wheel_indicator()
        self._redraw_bri_gradient()
        self._refresh_entries()
        self._emit_change()

    # =========================
    # BRIGHTNESS SLIDER (real gradient of the base color)
    # =========================

    def _redraw_bri_gradient(self):
        """Rebuild the slider gradient: black (bottom) -> base color (top)."""
        n = len(self._bri_strips)
        for i, rect in enumerate(self._bri_strips):
            frac = i / (n - 1)
            self.bri_canvas.itemconfigure(rect, fill=_mix_hex((0, 0, 0), self.base_rgb, frac))

    def _update_bri_slider(self):
        y = self._bri_h - self.bri / 255.0 * (self._bri_h - 2) - 1
        self.bri_canvas.coords(self._bri_marker, 0, y, self._bri_w, y)
        if hasattr(self, "bri_val_lbl"):
            self.bri_val_lbl.config(text=str(self.bri))

    def _on_bri_pick(self, event):
        self.bri = max(0, min(255, int(round((1.0 - event.y / self._bri_h) * 255))))
        self._update_bri_slider()
        self._refresh_entries()
        self._emit_change()

    # =========================
    # RGB / HEX INPUTS (final color: base * bri / 255)
    # =========================

    def _apply_final_rgb(self, rgb):
        """RGB/HEX input sets the FINAL color and simultaneously defines
        brightness (max channel) and the wheel position (color normalized
        to the max channel).
        Example: (10,10,10) -> wheel at pure white, brightness = 10."""
        rgb = tuple(max(0, min(255, int(x))) for x in rgb)
        mx = max(rgb)
        base = (tuple(int(round(v * 255.0 / mx)) for v in rgb)
                if mx > 0 else (255, 255, 255))
        self.base_rgb = base
        self.bri = mx
        h, s, v = rgb_to_hsv(*base)
        if s > 0.005:
            self.h = h
        self.s = s  # gray/white -> s = 0 -> wheel center (pure white)
        self._refresh_entries()
        self._place_wheel_indicator()
        self._redraw_bri_gradient()
        self._update_bri_slider()
        self._emit_change()

    def _on_rgb_change(self, event=None):
        """Real time: apply the values immediately on input, but only
        when all three channels are filled and valid (0-255). Empty or
        invalid fields are ignored — the current state is not changed
        (empty-value protection)."""
        vals = []
        for e in self.entries:
            v = _clamp_channel(e.get())
            if v is None:
                return  # field empty/invalid — do not apply
            vals.append(v)
        # R/G/B fields = final color: defines brightness and wheel
        self._apply_final_rgb(tuple(vals))

    def _on_rgb_focus_out(self, event=None):
        """Empty-value protection: if on focus loss a field is still
        empty/invalid — restore the current color values."""
        for e in self.entries:
            if _clamp_channel(e.get()) is None:
                self._refresh_entries()
                return

    def _on_hex_change(self, event=None):
        """Real time: apply the HEX immediately, but only when the full
        code is entered (RRGGBB or RRGGBBAA). Empty/short values or
        invalid characters are ignored (empty-value protection)."""
        t = self.hex_entry.get().strip().lstrip("#")
        if len(t) not in (6, 8):
            return  # still typing — do not apply
        try:
            rgb = (int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16))
        except ValueError:
            return  # not all hex chars — leave the state untouched
        # HEX = final color: defines brightness and wheel
        self._apply_final_rgb(rgb)

    def _on_hex_focus_out(self, event=None):
        """Empty-value protection: if the HEX is incomplete or invalid —
        restore the current color."""
        t = self.hex_entry.get().strip().lstrip("#")
        ok = len(t) in (6, 8)
        if ok:
            try:
                int(t[0:2], 16)
                int(t[2:4], 16)
                int(t[4:6], 16)
            except ValueError:
                ok = False
        if not ok:
            self._refresh_entries()

    # =========================
    # EMIT / REFRESH
    # =========================

    def _refresh_entries(self):
        """Sync RGB/HEX entries and preview with the FINAL color
        (base scaled by brightness: base * bri / 255).

        A field is rewritten only if its value changed; if the user is
        typing in this field, the cursor stays at the end — so that
        real-time updates do not break the input."""
        final = self._final_rgb()
        for e, v in zip(self.entries, final):
            new = str(v)
            if e.get() != new:
                e.delete(0, "end")
                e.insert(0, new)
                if self.focus_get() is e:
                    e.icursor("end")
        hex_new = "%02x%02x%02x" % final
        if self.hex_entry.get().strip().lstrip("#") != hex_new:
            self.hex_entry.delete(0, "end")
            self.hex_entry.insert(0, hex_new)
            if self.focus_get() is self.hex_entry:
                self.hex_entry.icursor("end")
        self.preview.itemconfigure(self._preview_rect, fill=self._final_hex())
        self._update_overheat_warn()

    def _update_overheat_warn(self):
        """Show/hide the orange overheat warning above the buttons:
        visible when the sum of the three final color channels
        (r + g + b, each 0-255) exceeds OVERHEAT_THRESHOLD.

        The label is always packed (full width, directly above the
        buttons); it is hidden by matching its text color to the window
        background, so toggling never disturbs the layout."""
        warn = getattr(self, "warn_lbl", None)
        if warn is None:
            return
        try:
            r, g, b = self._final_rgb()
        except Exception:
            return
        if (r + g + b) > self.OVERHEAT_THRESHOLD:
            warn.config(fg=self._warn_orange)
        else:
            warn.config(fg=self._warn_bg)

    def _emit_change(self):
        """Notify the app of the final color + brightness in real time."""
        if self.on_color_change is not None:
            try:
                r, g, b = self._final_rgb()
                self.on_color_change(r, g, b, self.bri)
            except Exception:
                pass

    # =========================
    # GLOBAL ACTIONS (all modules)
    # =========================

    def _flash_status(self, text, color_key, color_fallback):
        try:
            self.status_lbl.config(
                text=text, fg=self.colors.get(color_key, color_fallback))
        except Exception:
            pass

    def _apply_to_all(self):
        """'Apply to All' button: current color for all WLED modules."""
        if self.on_apply_all is None:
            return
        r, g, b, bri = self.get_state()
        try:
            self.on_apply_all(r, g, b, bri)
            self._flash_status(
                f"Applied to all modules: {r}, {g}, {b} (bri={bri})",
                "success", "#9ece6a")
        except Exception as e:
            self._flash_status(f"Error: {e}", "error", "#f7768e")

    def _set_default_color(self):
        """'Default Color' button: restore the color selected in settings
        (the default_rgb passed by the app; (10,10,10) only as fallback)."""
        self.set_color(self.DEFAULT_RGB)  # already emits on_color_change
        r, g, b = self.DEFAULT_RGB
        self._flash_status(
            f"Default color applied ({r}, {g}, {b})",
            "success", "#9ece6a")

    def _apply_default_to_all(self):
        """'Apply Default to All' button: the color selected in settings
        for all WLED modules (and in this window)."""
        if self.on_apply_all is None:
            return
        self.set_color(self.DEFAULT_RGB)  # syncs this window + emits
        r, g, b = self.DEFAULT_RGB
        bri = max(self.DEFAULT_RGB)
        try:
            self.on_apply_all(r, g, b, bri)
            self._flash_status(
                f"Default color ({r}, {g}, {b}) applied to all modules",
                "success", "#9ece6a")
        except Exception as e:
            self._flash_status(f"Error: {e}", "error", "#f7768e")

    # =========================
    # EXTERNAL API
    # =========================

    def set_color(self, rgb, bri=None):
        """Update the window from outside. rgb — FINAL color (0-255);
        bri — brightness; if not given — the max channel of the color."""
        if bri is None:
            self._apply_final_rgb(rgb)
            return
        rgb = tuple(max(0, min(255, int(x))) for x in rgb)
        self.bri = max(0, min(255, int(bri)))
        if self.bri > 0:
            base = tuple(max(0, min(255, int(round(v * 255.0 / self.bri))))
                         for v in rgb)
        else:
            base = (255, 255, 255)
        self.base_rgb = base
        h, s, v = rgb_to_hsv(*base)
        if s > 0.005:
            self.h = h
        self.s = s
        self._refresh_entries()
        self._place_wheel_indicator()
        self._redraw_bri_gradient()
        self._update_bri_slider()

    def get_state(self):
        """(final r, final g, final b, bri)"""
        return self._final_rgb() + (self.bri,)
