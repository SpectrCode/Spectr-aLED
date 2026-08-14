"""
LED Matrix Mapping Module
Modern mapping editor in dark theme style GPU Capture + WLED
Automatic save/load of mapping data
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import json
import os

# Import dark mode utility for Windows title bar
try:
    from window_utils import apply_dark_mode_to_tk_window
except ImportError:
    def apply_dark_mode_to_tk_window(window):
        pass


class LEDMapper:
    def __init__(self, root):
        self.root = root
        self.root.title("LED Matrix Mapping")
        
        # Color scheme (dark theme)
        self.colors = {
            "bg": "#1a1b26",
            "panel_bg": "#24283b",
            "accent": "#7aa2f7",
            "text_main": "#c0caf5",
            "text_dim": "#777c9e",
            "border": "#414868"
        }
        
        # Get screen dimensions
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        
        # Set window size (max 80% of screen)
        width = int(screen_w * 0.85)
        height = int(screen_h * 0.85)
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(600, 450)
        
        # Set window to always stay on top
        root.attributes("-topmost", True)
        
        # Apply dark theme to window
        root.configure(bg=self.colors["bg"])
        
        # Configure ttk styles
        style = ttk.Style()
        style.theme_use("clam")
        
        style.configure(".", background=self.colors["bg"], foreground=self.colors["text_main"])
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text_main"])
        style.configure("TLabelframe", background=self.colors["bg"], foreground=self.colors["text_main"])
        style.configure("TLabelframe.Label", background=self.colors["bg"], foreground=self.colors["text_main"])
        style.configure("TButton", 
                       background=self.colors["panel_bg"],
                       foreground=self.colors["text_main"],
                       borderwidth=0,
                       padding=(8, 6))
        style.map("TButton",
                 background=[("active", self.colors["accent"])],
                 foreground=[("disabled", "#5c6370")])
        
        # Main container
        main_frame = ttk.Frame(root, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Top control panel
        top_frame = tk.Frame(main_frame, bg=self.colors["bg"])
        top_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Matrix size controls
        matrix_control_frame = tk.Frame(top_frame, bg=self.colors["bg"])
        matrix_control_frame.pack(side="left")
        
        tk.Label(matrix_control_frame, text="Columns:", bg=self.colors["bg"], fg=self.colors["text_main"]).pack(side="left", padx=(0, 5))
        
        self.cols_var = tk.StringVar(value="10")
        cols_entry = ttk.Entry(matrix_control_frame, textvariable=self.cols_var, width=6)
        cols_entry.pack(side="left", padx=(0, 15))
        
        tk.Label(matrix_control_frame, text="Rows:", bg=self.colors["bg"], fg=self.colors["text_main"]).pack(side="left")
        
        self.rows_var = tk.StringVar(value="10")
        rows_entry = ttk.Entry(matrix_control_frame, textvariable=self.rows_var, width=6)
        rows_entry.pack(side="left", padx=(0, 15))
        
        # Cell size control
        tk.Label(matrix_control_frame, text="Cell:", bg=self.colors["bg"], fg=self.colors["text_main"]).pack(side="left")
        
        self.cell_size_var = tk.StringVar(value="25")
        cell_size_entry = ttk.Entry(matrix_control_frame, textvariable=self.cell_size_var, width=6)
        cell_size_entry.pack(side="left", padx=(0, 20))
        
        # Control buttons - left side (undo)
        btn_row_left = tk.Frame(top_frame, bg=self.colors["bg"])
        btn_row_left.pack(side="right")
        
        ttk.Button(btn_row_left, text="Undo", command=self.undo).pack(side="left", padx=(0, 5))
        
        # Control buttons - right side
        btn_row = tk.Frame(top_frame, bg=self.colors["bg"])
        btn_row.pack(side="right")
        
        ttk.Button(btn_row, text="Reset", command=self.reset).pack(side="left", padx=(0, 5))
        ttk.Button(btn_row, text="Save", command=self.save).pack(side="left", padx=(0, 5))
        ttk.Button(btn_row, text="Load", command=self.load).pack(side="left", padx=(0, 5))
        
        # Canvas container with scrollbars
        self.canvas_container = tk.Frame(main_frame, bg="#2b3041")
        self.canvas_container.pack(fill="both", expand=True)
        
        # Horizontal scrollbar
        self.h_scrollbar = ttk.Scrollbar(self.canvas_container, orient="horizontal")
        self.h_scrollbar.pack(side="bottom", fill="x")
        
        # Vertical scrollbar
        self.v_scrollbar = ttk.Scrollbar(self.canvas_container, orient="vertical")
        self.v_scrollbar.pack(side="right", fill="y")
        
        # Canvas for matrix with scrollbar bindings
        self.canvas = tk.Canvas(
            self.canvas_container,
            bg="#2b3041",
            highlightthickness=0,
            xscrollcommand=self.h_scrollbar.set,
            yscrollcommand=self.v_scrollbar.set
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        
        # Scrollbar bindings to canvas
        self.h_scrollbar.config(command=self.canvas.xview)
        self.v_scrollbar.config(command=self.canvas.yview)
        
        # Frame inside canvas for content (for scrolling)
        self.canvas_content = tk.Frame(self.canvas, bg="#2b3041")
        self.canvas.create_window((0, 0), window=self.canvas_content, anchor="nw")
        
        # Initialize variables
        self.rows = 10
        self.cols = 10
        self.cell_size = 25  # Fixed cell size
        self.segments = []  # List of segments [((r1,c1), (r2,c2)), ...] - coordinates instead of rect ID
        self.history = []
        self.cells = {}
        self.coords_to_rect = {}
        self.start_index = 1
        self.expecting_second_point = False  # Waiting for second point to create segment
        self.occupied_cells = set()  # Set of occupied cells (r, c) for intersection check optimization
        
        # Path to auto-save file
        self.auto_save_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mapping_data.json")
        
        # Event bindings for automatic update on size change
        self.rows_var.trace_add("write", lambda *args: self.on_size_change())
        self.cols_var.trace_add("write", lambda *args: self.on_size_change())
        self.cell_size_var.trace_add("write", lambda *args: self.on_cell_size_change())
        
        # Window close binding for saving
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)
        
        # Window resize binding for automatic centering
        self.canvas.bind("<Configure>", lambda e: self.redraw_on_resize())
        
        self.canvas.bind("<Button-1>", self.handle_left_click)
        self.canvas.bind("<Button-3>", self.handle_right_click)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)  # For wheel scrolling
        
        # Initialize rendering
        self.root.update_idletasks()  # Update canvas dimensions before first render
        # Small delay to get accurate canvas dimensions after window display
        self.root.after(10, lambda: self._draw_grid_later_with_load())
    
    def _draw_grid_later_with_load(self):
        """Delayed initialization of grid rendering with loaded data"""
        if hasattr(self, 'canvas') and self.canvas.winfo_exists():
            # First create the grid
            self.on_size_change()
            # Then load data (via after so cells are already created)
            self.root.after(10, self.load_auto_save)
    
    def update_scroll_region(self):
        """Update scroll region"""
        total_width = self.cols * self.cell_size
        total_height = self.rows * self.cell_size
        self.canvas_content.configure(width=total_width, height=total_height)
        self.canvas.config(scrollregion=(0, 0, total_width, total_height))
    
    def on_mouse_wheel(self, event):
        """Handle mouse wheel scrolling"""
        if event.delta > 0:
            # Scroll up
            self.canvas.yview_scroll(-1, "units")
        else:
            # Scroll down
            self.canvas.yview_scroll(1, "units")
    
    def on_size_change(self, event=None):
        """Handle matrix size change"""
        try:
            rows_text = self.rows_var.get().strip()
            cols_text = self.cols_var.get().strip()
            
            # Check for empty values
            if not rows_text or not cols_text:
                return
            
            new_rows = int(rows_text)
            new_cols = int(cols_text)
            
            # Protection against 0 and negative values
            if new_rows <= 0 or new_cols <= 0:
                return
            
            self.rows = new_rows
            self.cols = new_cols
            # Clear state and occupied cells before redraw
            self.expecting_second_point = False
            if hasattr(self, 'temp_segment_start'):
                delattr(self, 'temp_segment_start')
            self.occupied_cells.clear()
            self.draw_grid()
        except ValueError:
            pass
    
    def on_cell_size_change(self):
        """Handle cell size change with protection against 0 and empty value"""
        try:
            cell_size_text = self.cell_size_var.get().strip()
            
            # Check for empty value
            if not cell_size_text:
                return
            
            new_cell_size = int(cell_size_text)
            
            # Protection against 0 and negative values
            if new_cell_size <= 0:
                return
            
            self.cell_size = new_cell_size
            self.redraw_on_resize()
        except ValueError:
            pass
    
    def redraw_on_resize(self):
        """Redraw grid on canvas size change for centering"""
        # Check that canvas still exists and has dimensions
        if not hasattr(self, 'canvas') or not self.canvas.winfo_exists():
            return
        
        current_width = self.canvas.winfo_width()
        current_height = self.canvas.winfo_height()
        
        # If canvas is not initialized or too small - ignore
        if current_width <= 1 or current_height <= 1:
            return
        
        # Update scroll region
        self.update_scroll_region()
        
        # Redraw grid with new centering coordinates
        self.draw_grid()
    
    def _check_segment_collision(self, start_coords, end_coords):
        """Check segment intersection with existing occupied cells"""
        new_cells = set(self._get_segment_cells(start_coords, end_coords))
        
        # Check if there is intersection with already occupied cells
        if new_cells & self.occupied_cells:
            return True
        
        return False
    
    def _add_segment_to_occupied(self, start_coords, end_coords):
        """Add segment cells to occupied set"""
        cells = self._get_segment_cells(start_coords, end_coords)
        self.occupied_cells.update(cells)
    
    def _remove_segment_from_occupied(self, start_coords, end_coords):
        """Remove segment cells from occupied set"""
        # Rebuild from all remaining segments
        self._rebuild_occupied_cells()
    
    def draw_grid(self):
        """Draw matrix grid"""
        self.canvas.delete("all")
        self.cells.clear()
        self.coords_to_rect.clear()
        
        # Calculate offset for grid centering
        canvas_width = self.canvas.winfo_width() if self.canvas.winfo_width() > 1 else int(self.cols * self.cell_size)
        canvas_height = self.canvas.winfo_height() if self.canvas.winfo_height() > 1 else int(self.rows * self.cell_size)
        
        total_grid_width = self.cols * self.cell_size
        total_grid_height = self.rows * self.cell_size
        
        # Center grid in canvas
        offset_x = max(0, (canvas_width - total_grid_width) // 2)
        offset_y = max(0, (canvas_height - total_grid_height) // 2)
        
        for r in range(self.rows):
            for c in range(self.cols):
                x1 = offset_x + c * self.cell_size
                y1 = offset_y + r * self.cell_size
                x2 = x1 + self.cell_size
                y2 = y1 + self.cell_size
                
                rect = self.canvas.create_rectangle(
                    x1, y1, x2, y2,
                    fill="#3b4260",
                    outline=self.colors["border"],
                    width=1
                )
                self.cells[rect] = (r, c)
                self.coords_to_rect[(r, c)] = rect
        
        # Recalculate occupied cells during full grid redraw
        self._rebuild_occupied_cells()
        self.redraw_path()
    
    def _get_segment_cells(self, start_coords, end_coords):
        """Get all cells on segment (for checking and adding to occupied_cells)
           Direction: from point A (start) to point B (end)"""
        r1, c1 = start_coords
        r2, c2 = end_coords
        
        line_cells = []
        
        if r1 == r2:  # Horizontal line - direction from A to B
            if c1 <= c2:
                for c in range(c1, c2 + 1):
                    line_cells.append((r1, c))
            else:
                for c in range(c1, c2 - 1, -1):
                    line_cells.append((r1, c))
        elif c1 == c2:  # Vertical line - direction from A to B
            if r1 <= r2:
                for r in range(r1, r2 + 1):
                    line_cells.append((r, c1))
            else:
                for r in range(r1, r2 - 1, -1):
                    line_cells.append((r, c1))
        
        return line_cells
    
    def _rebuild_occupied_cells(self):
        """Rebuild occupied cells set from all segments"""
        self.occupied_cells.clear()
        for start_coords, end_coords in self.segments:
            cells = self._get_segment_cells(start_coords, end_coords)
            self.occupied_cells.update(cells)
    
    def get_cell_under_cursor(self, event):
        """Get cell under cursor"""
        # Transform coordinates accounting for canvas scrolling
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        items = self.canvas.find_overlapping(x, y, x, y)
        for item in items:
            if item in self.cells:
                return item
        return None
    
    def handle_left_click(self, event):
        """Handle left click on cell - line mode"""
        rect = self.get_cell_under_cursor(event)
        if rect is None:
            return
        
        # Line mode - create segments from two points (stores coordinates r, c)
        if not self.expecting_second_point:
            # Waiting for first point (or new starting point) - save coordinates
            start_r, start_c = self.cells[rect]
            self.temp_segment_start = (start_r, start_c)  # Store coordinates
            self.expecting_second_point = True
            self.redraw_path()
            # Auto-save state when starting segment creation
            self.save_auto_save_callback()
        else:
            # Second point - create segment
            start = self.temp_segment_start  # These are coordinates (r, c)
            end_rect = rect
            end_r, end_c = self.cells[end_rect]
            
            if start != (end_r, end_c):  # Avoid creating zero-length segments
                r1, c1 = start
                r2, c2 = end_r, end_c
                # Only vertical or horizontal lines allowed
                if r1 == r2 or c1 == c2:
                    self.save_state()
                    new_segment = ((r1, c1), (r2, c2))
                    self.segments.append(new_segment)
                    # Add new cells to occupied_cells (including duplicates - they will be rebuilt at redraw_path)
                    self._add_segment_to_occupied((r1, c1), (r2, c2))
            self.expecting_second_point = False
            self.temp_segment_start = None  # Clear coordinates
            self.redraw_path()
            # Auto-save state when creating segment
            self.save_auto_save_callback()
    
    def save_auto_save_callback(self):
        """Call save_auto_save via root.after to avoid canvas issues"""
        if hasattr(self, 'canvas') and self.canvas.winfo_exists():
            self.root.after(10, self.save_auto_save)
    
    @staticmethod
    def get_rainbow_color(index, total_colors=2048):
        """Get color from full RGB gradient (cyclic)"""
        import colorsys
        # Divide circle into 360 degrees for full RGB
        # 2048 shades = 360 degrees, each shade ~0.175 degree
        hue = (index * 360) // total_colors
        r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 1.0, 1.0)
        return int(r * 255), int(g * 255), int(b * 255)
    
    def redraw_path(self):
        """Redraw path on matrix - line mode with bidirectional numbering based on A and B positions"""
        # Calculate offset for grid centering (same as in draw_grid)
        canvas_width = self.canvas.winfo_width() if self.canvas.winfo_width() > 1 else int(self.cols * self.cell_size)
        canvas_height = self.canvas.winfo_height() if self.canvas.winfo_height() > 1 else int(self.rows * self.cell_size)
        
        total_grid_width = self.cols * self.cell_size
        total_grid_height = self.rows * self.cell_size
        
        offset_x = max(0, (canvas_width - total_grid_width) // 2)
        offset_y = max(0, (canvas_height - total_grid_height) // 2)
        
        # Clear cell colors
        for rect in self.cells:
            self.canvas.itemconfig(rect, fill="#3b4260")
        
        self.canvas.delete("text")
        self.canvas.delete("line")
        
        global_num = 0  # Global counter for numbering all cells
        
        # Draw each segment
        for seg_index, (start_coords, end_coords) in enumerate(self.segments):
            r1, c1 = start_coords
            r2, c2 = end_coords
            
            # Gather all cells on current segment - direction depends on A and B positions
            line_cells = []
            
            if r1 == r2:  # Horizontal line
                # Direction: from point A (start) to point B (end)
                # If start_c < end_c: A is left, go left-to-right
                # If start_c > end_c: A is right, go right-to-left
                if c1 <= c2:
                    for c in range(c1, c2 + 1):
                        line_cells.append((r1, c))
                else:
                    for c in range(c1, c2 - 1, -1):
                        line_cells.append((r1, c))
            elif c1 == c2:  # Vertical line
                # Direction: from point A (start) to point B (end)
                # If start_r < end_r: A is top, go top-to-bottom
                # If start_r > end_r: A is bottom, go bottom-to-top
                if r1 <= r2:
                    for r in range(r1, r2 + 1):
                        line_cells.append((r, c1))
                else:
                    for r in range(r1, r2 - 1, -1):
                        line_cells.append((r, c1))
            # Diagonal lines prohibited - skip
            
            # Draw cells with unique numbering (direction depends on A and B positions)
            for i, (r, c) in enumerate(line_cells):
                rect = self.coords_to_rect.get((r, c))
                if rect:
                    # Check if cell is occupied by previous segments
                    is_occupied = False
                    for prev_index in range(seg_index):
                        prev_start, prev_end = self.segments[prev_index]
                        pr1, pc1 = prev_start  # Now these are coordinates (r, c)
                        pr2, pc2 = prev_end
                        
                        # Check if cell (r,c) is on previous line
                        if pr1 == pr2:  # Horizontal
                            if r == pr1 and min(pc1, pc2) <= c <= max(pc1, pc2):
                                is_occupied = True
                                break
                        elif pc1 == pc2:  # Vertical
                            if c == pc1 and min(pr1, pr2) <= r <= max(pr1, pr2):
                                is_occupied = True
                                break
                        # Diagonal lines prohibited - skip occupancy check
                    
                    if not is_occupied:
                        global_num += 1
                        # Use full RGB gradient with 2048 shades and cyclic behavior
                        r, g, b = self.get_rainbow_color(global_num - 1)
                        color = f'#{r:02x}{g:02x}{b:02x}'
                        self.canvas.itemconfig(rect, fill=color)
                        
                        # Find coordinates for text (accounting for offset)
                        coords = self.canvas.coords(rect)
                        cx = (coords[0] + coords[2]) / 2
                        cy = (coords[1] + coords[3]) / 2
                        
                        # Show only numbers - no letters
                        text = str(global_num)
                        
                        self.canvas.create_text(cx, cy, text=text, fill="white", font=("Consolas", 9), tags=["text"])
        
        # If waiting for second point to create new segment - show first as point A with blue highlight
        if hasattr(self, 'temp_segment_start') and self.temp_segment_start is not None:
            start_coords = self.temp_segment_start  # These are coordinates (r, c)
            rect = self.coords_to_rect.get(start_coords)
            
            if rect:
                x1, y1, x2, y2 = self.canvas.coords(rect)
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                
                # Highlight first point of segment as A with blue color and "A" text
                self.canvas.itemconfig(rect, fill="#7aa2f7")
                self.canvas.create_text(cx, cy, text="A", fill="white", font=("Consolas", 9), tags=["text"])
            
    
    def load_auto_save(self):
        """Load auto-saved data from JSON file"""
        try:
            if os.path.exists(self.auto_save_file):
                with open(self.auto_save_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                # Restore matrix dimensions (only if different)
                new_rows = int(data.get("rows", 10))
                new_cols = int(data.get("cols", 10))
                new_cell_size = int(data.get("cell_size", 25))
                
                expecting = self.expecting_second_point
                
                # Update cell size if different
                if new_cell_size != self.cell_size:
                    self.cell_size_var.set(str(new_cell_size))
                    self.cell_size = new_cell_size
                
                # Restore segments - data now stored as coordinates
                loaded_segments = data.get("segments", [])
                
                if new_rows != self.rows or new_cols != self.cols:
                    # Change dimensions and create new grid
                    self.rows_var.set(str(new_rows))
                    self.cols_var.set(str(new_cols))
                    self.on_size_change()
                
                coords_map = {v: k for k, v in self.cells.items()}
                
                self.segments.clear()
                for seg in loaded_segments:
                    r1, c1 = seg["start"]
                    r2, c2 = seg["end"]
                    # Restore as coordinates (r, c)
                    start_coords = (r1, c1)
                    end_coords = (r2, c2)
                    self.segments.append((start_coords, end_coords))
                
                # Restore waiting state for second point
                self.expecting_second_point = data.get("expecting_second_point", False)
                temp_start = data.get("temp_segment_start")
                if temp_start:
                    r, c = temp_start
                    start_coords = (r, c)
                    rect = coords_map.get(start_coords)
                    if rect:
                        self.temp_segment_start = start_coords  # Save coordinates
                
                # Rebuild occupied cells after loading data
                self._rebuild_occupied_cells()
                
                # Redraw with restored data
                self.redraw_path()
        except Exception as e:
            print(f"[WARN] Failed to load auto-save: {e}")
    
    def save_auto_save(self):
        """Save current data to JSON file for automatic restoration"""
        try:
            # Save coordinates directly (r, c)
            segments_data = []
            for start_coords, end_coords in self.segments:
                segments_data.append({
                    "start": list(start_coords),
                    "end": list(end_coords)
                })
            
            data = {
                "rows": int(self.rows_var.get()),
                "cols": int(self.cols_var.get()),
                "cell_size": self.cell_size,
                "segments": segments_data,
                "expecting_second_point": self.expecting_second_point,
                "temp_segment_start": None
            }
            
            # If there is temp_segment_start - save its coordinates
            if hasattr(self, 'temp_segment_start') and self.temp_segment_start is not None:
                data["temp_segment_start"] = list(self.temp_segment_start)
            
            with open(self.auto_save_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[WARN] Failed to save auto-save: {e}")
    
    def on_window_close(self):
        """Handle window close - save current state"""
        self.save_auto_save()
        self.root.destroy()
    
    def reset(self):
        """Reset mapping and delete auto-save file"""
        self.segments.clear()
        self.history.clear()
        self.expecting_second_point = False
        if hasattr(self, 'temp_segment_start'):
            delattr(self, 'temp_segment_start')
        
        # Clear occupied cells on reset
        self.occupied_cells.clear()
        
        # Delete auto-save file on reset
        try:
            if os.path.exists(self.auto_save_file):
                os.remove(self.auto_save_file)
        except Exception as e:
            print(f"[WARN] Failed to delete auto-save file: {e}")
        
        self.redraw_path()
    
    def handle_right_click(self, event):
        """Handle right click - place single point"""
        rect = self.get_cell_under_cursor(event)
        if rect is None:
            return
        
        # Add single point as segment (start and end same) - save coordinates
        r, c = self.cells[rect]
        
        self.save_state()
        self.segments.append(((r, c), (r, c)))
        # Add point to occupied_cells
        self.occupied_cells.add((r, c))
        self.redraw_path()
        # Auto-save state
        self.save_auto_save_callback()
    
    def save_state(self):
        """Save current state for undo capability"""
        # Save copy of segments and waiting flag for second point
        state = {
            'segments': [(s[0], s[1]) for s in self.segments],
            'expecting_second_point': self.expecting_second_point,
            'temp_start': getattr(self, 'temp_segment_start', None)
        }
        self.history.append(state)
    
    def undo(self):
        """Undo last action"""
        if not self.history:
            return
        
        # Get previous state
        prev_state = self.history.pop()
        
        # Restore segments and state
        self.segments.clear()
        self.segments.extend(prev_state['segments'])
        self.expecting_second_point = False  # Reset flag on undo
        
        # Remove temp_segment_start if it was (on line undo)
        if hasattr(self, 'temp_segment_start'):
            delattr(self, 'temp_segment_start')
        
        # Rebuild occupied cells after undo
        self._rebuild_occupied_cells()
        
        self.redraw_path()
        # Auto-save state after undo
        self.save_auto_save_callback()
    
    def save_async(self, file_path, callback):
        """Asynchronous save in separate thread - with bidirectional numbering based on A and B"""
        def worker():
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    # Save each point as independent record
                    global_num = 0
                    for seg_index, (start_coords, end_coords) in enumerate(self.segments):
                        r1, c1 = start_coords
                        r2, c2 = end_coords
                        
                        line_cells = []
                        
                        if r1 == r2:  # Horizontal line - direction from A to B
                            if c1 <= c2:
                                for c in range(c1, c2 + 1):
                                    line_cells.append((r1, c))
                            else:
                                for c in range(c1, c2 - 1, -1):
                                    line_cells.append((r1, c))
                        elif c1 == c2:  # Vertical line - direction from A to B
                            if r1 <= r2:
                                for r in range(r1, r2 + 1):
                                    line_cells.append((r, c1))
                            else:
                                for r in range(r1, r2 - 1, -1):
                                    line_cells.append((r, c1))
                        
                        for r, c in line_cells:
                            global_num += 1
                            # Format: number: x,y where x=r (row), y=c (column) - swapped places
                            f.write(f"{global_num}: {r},{c}\n")
                # Return result via root.after in main thread
                self.root.after(0, lambda: callback(True, None))
            except Exception as e:
                self.root.after(0, lambda: callback(False, str(e)))
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    
    def save(self):
        """Save mapping to file with topmost priority"""
        if not self.segments:
            messagebox.showwarning("Warning", "No defined segments!")
            return
        
        # Create a temporary window to host the dialog
        temp_window = tk.Toplevel(self.root)
        temp_window.withdraw()
        temp_window.attributes("-topmost", True)
        
        file_path = filedialog.asksaveasfilename(
            parent=temp_window,
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")]
        )
        
        temp_window.destroy()
        if not file_path:
            return
        
        # Show loading indicator
        status_label = tk.Label(self.root, text="Saving...", fg=self.colors["accent"], bg=self.colors["bg"])
        status_label.pack(side="bottom")
        
        def on_complete(success, error_msg=None):
            status_label.destroy()
            if success:
                messagebox.showinfo("Success", "Mapping saved!")
            else:
                messagebox.showerror("Error", f"Failed to save: {error_msg}")
        
        # Start save in background thread
        self.save_async(file_path, on_complete)
    
    def load(self):
        """Load mapping from file with topmost priority"""
        # Create a temporary window to host the dialog
        temp_window = tk.Toplevel(self.root)
        temp_window.withdraw()
        temp_window.attributes("-topmost", True)
        
        file_path = filedialog.askopenfilename(
            parent=temp_window,
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        
        temp_window.destroy()
        if not file_path:
            return
        
        # Show loading indicator
        status_label = tk.Label(self.root, text="Loading...", fg=self.colors["accent"], bg=self.colors["bg"])
        status_label.pack(side="bottom")
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            
            # Load all points with their numbers
            # Format: number: x,y
            points_by_number = {}  # number -> (x, y)
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                parts = line.split(":")
                if len(parts) != 2:
                    continue
                
                try:
                    num = int(parts[0].strip())
                    coords = parts[1].strip().split(",")
                    
                    if len(coords) == 2:
                        x = int(coords[0].strip())  # Now x is row
                        y = int(coords[1].strip())  # Now y is column
                        points_by_number[num] = (x, y)
                except ValueError:
                    continue
            
            # Create independent segments for each point
            # Each point becomes separate segment (point-to-itself)
            self.segments.clear()
            
            sorted_nums = sorted(points_by_number.keys())
            
            for num in sorted_nums:
                x, y = points_by_number[num]  # x=row(r), y=column(c)
                rect = self.coords_to_rect.get((x, y))  # Use (row, col) directly
                if rect:
                    r, c = self.cells[rect]
                    # Each point is separate segment from itself to itself
                    self.segments.append(((r, c), (r, c)))
            
            self.expecting_second_point = False
            if hasattr(self, 'temp_segment_start'):
                delattr(self, 'temp_segment_start')
            
            # Rebuild occupied cells after loading data from file
            self._rebuild_occupied_cells()
            
            self.redraw_path()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load: {e}")
        finally:
            status_label.destroy()


def open_mapping_window(root):
    """Function to open mapping window (alternative constructor)"""
    try:
        # Check if window is already open
        if hasattr(root, '_mapping_window') and root._mapping_window is not None:
            try:
                root._mapping_window.lift()
                root._mapping_window.focus_force()
                return
            except:
                pass
        
        win = tk.Toplevel(root)
        win.title("LED Matrix Mapping")
        
        # Apply dark mode to title bar
        apply_dark_mode_to_tk_window(win)
        
        # Set window to always stay on top
        win.attributes("-topmost", True)
        
        # Color scheme (dark theme)
        colors = {
            "bg": "#1a1b26",
            "panel_bg": "#24283b",
            "accent": "#7aa2f7",
            "text_main": "#c0caf5",
            "text_dim": "#777c9e",
            "border": "#414868"
        }
        
        # Apply dark theme to window
        win.configure(bg=colors["bg"])
        
        # Get screen dimensions
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        
        # Set window size (max 80% of screen)
        width = int(screen_w * 0.85)
        height = int(screen_h * 0.85)
        x = (screen_w - width) // 2
        y = (screen_h - height) // 2
        
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(600, 450)
        
        # Save reference to window in root window (for compatibility)
        root._mapping_window = win
        
        # Configure ttk styles
        style = ttk.Style()
        style.theme_use("clam")
        
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
                 background=[("active", colors["accent"])],
                 foreground=[("disabled", "#5c6370")])
        
        # Main container
        main_frame = ttk.Frame(win, padding=10)
        main_frame.pack(fill="both", expand=True)
        
        # Top control panel
        top_frame = tk.Frame(main_frame, bg=colors["bg"])
        top_frame.pack(side="top", fill="x", pady=(0, 10))
        
        # Matrix size controls
        matrix_control_frame = tk.Frame(top_frame, bg=colors["bg"])
        matrix_control_frame.pack(side="left")
        
        tk.Label(matrix_control_frame, text="Columns:", bg=colors["bg"], fg=colors["text_main"]).pack(side="left", padx=(0, 5))
        
        cols_var = tk.StringVar(value="10")
        cols_entry = ttk.Entry(matrix_control_frame, textvariable=cols_var, width=6)
        cols_entry.pack(side="left", padx=(0, 15))
        
        tk.Label(matrix_control_frame, text="Rows:", bg=colors["bg"], fg=colors["text_main"]).pack(side="left")
        
        rows_var = tk.StringVar(value="10")
        rows_entry = ttk.Entry(matrix_control_frame, textvariable=rows_var, width=6)
        rows_entry.pack(side="left", padx=(0, 15))
        
        # Cell size control
        tk.Label(matrix_control_frame, text="Cell:", bg=colors["bg"], fg=colors["text_main"]).pack(side="left")
        
        cell_size_var = tk.StringVar(value="25")
        cell_size_entry = ttk.Entry(matrix_control_frame, textvariable=cell_size_var, width=6)
        cell_size_entry.pack(side="left", padx=(0, 20))
        
        # Canvas container with scrollbars
        canvas_container = tk.Frame(main_frame, bg="#2b3041")
        canvas_container.pack(fill="both", expand=True)
        
        # Horizontal scrollbar
        h_scrollbar = ttk.Scrollbar(canvas_container, orient="horizontal")
        h_scrollbar.pack(side="bottom", fill="x")
        
        # Vertical scrollbar
        v_scrollbar = ttk.Scrollbar(canvas_container, orient="vertical")
        v_scrollbar.pack(side="right", fill="y")
        
        # Canvas for matrix with scrollbar bindings
        canvas = tk.Canvas(
            canvas_container,
            bg="#2b3041",
            highlightthickness=0,
            xscrollcommand=h_scrollbar.set,
            yscrollcommand=v_scrollbar.set
        )
        canvas.pack(side="left", fill="both", expand=True)
        
        # Scrollbar bindings to canvas
        h_scrollbar.config(command=canvas.xview)
        v_scrollbar.config(command=canvas.yview)
        
        # Frame inside canvas for content (for scrolling)
        canvas_content = tk.Frame(canvas, bg="#2b3041")
        canvas.create_window((0, 0), window=canvas_content, anchor="nw")
        
        # Initialize variables
        current_rows = 10
        current_cols = 10
        cell_size = 25  # Cell size
        segments = []  # List of segments [(point_a, point_b), ...]
        history = []
        cells = {}
        coords_to_rect = {}
        start_index = 1
        expecting_second_point = False
        
        def update_scroll_region():
            """Update scroll region"""
            total_width = current_cols * cell_size
            total_height = current_rows * cell_size
            canvas_content.configure(width=total_width, height=total_height)
            canvas.config(scrollregion=(0, 0, total_width, total_height))
        
        def save_state():
            """Save current state for undo capability"""
            state = {
                'segments': [(s[0], s[1]) for s in segments],
                'expecting_second_point': expecting_second_point,
                'temp_start': getattr(win, 'temp_segment_start', None)
            }
            history.append(state)
        
        def undo():
            """Undo last action"""
            if not history:
                return
            
            prev_state = history.pop()
            
            segments.clear()
            segments.extend(prev_state['segments'])
            expecting_second_point = False  # Reset flag on undo
            
            # Remove temp_segment_start if it was (on line undo)
            if hasattr(win, 'temp_segment_start'):
                delattr(win, 'temp_segment_start')
            
            redraw_path()
        
        def reset_mapping():
            segments.clear()
            history.clear()
            expecting_second_point = False
            if hasattr(win, 'temp_segment_start'):
                delattr(win, 'temp_segment_start')
            redraw_path()
        
        @staticmethod
        def get_rainbow_color(index, total_colors=2048):
            """Get color from full RGB gradient (cyclic)"""
            import colorsys
            hue = (index * 360) // total_colors
            r, g, b = colorsys.hsv_to_rgb(hue / 360.0, 1.0, 1.0)
            return int(r * 255), int(g * 255), int(b * 255)
        
        def draw_matrix():
            canvas.delete("all")
            cells.clear()
            coords_to_rect.clear()
            
            # Calculate offset for grid centering
            canvas_width = canvas.winfo_width() if canvas.winfo_width() > 1 else int(current_cols * cell_size)
            canvas_height = canvas.winfo_height() if canvas.winfo_height() > 1 else int(current_rows * cell_size)
            
            total_grid_width = current_cols * cell_size
            total_grid_height = current_rows * cell_size
            
            # Center grid in canvas
            offset_x = max(0, (canvas_width - total_grid_width) // 2)
            offset_y = max(0, (canvas_height - total_grid_height) // 2)
            
            for r in range(current_rows):
                for c in range(current_cols):
                    x1 = offset_x + c * cell_size
                    y1 = offset_y + r * cell_size
                    x2 = x1 + cell_size
                    y2 = y1 + cell_size
                    
                    rect = canvas.create_rectangle(
                        x1, y1, x2, y2,
                        fill="#3b4260",
                        outline=colors["border"],
                        width=1
                    )
                    cells[rect] = (r, c)
                    coords_to_rect[(r, c)] = rect
            
            # Do not clear segments during matrix redraw - save data!
            expecting_second_point = False
            if hasattr(win, 'temp_segment_start'):
                delattr(win, 'temp_segment_start')
            
            update_scroll_region()
            redraw_path(offset_x, offset_y)
        
        def get_cell_under_cursor(event):
            # Transform coordinates accounting for canvas scrolling
            x = canvas.canvasx(event.x)
            y = canvas.canvasy(event.y)
            items = canvas.find_overlapping(x, y, x, y)
            for item in items:
                if item in cells:
                    return item
            return None
        
        def handle_left_click(event):
            nonlocal expecting_second_point
            
            rect = get_cell_under_cursor(event)
            if rect is None:
                return
            
            # Line mode - create segments from two points
            if not expecting_second_point:
                # Save coordinates, not rect ID
                win.temp_segment_start = cells[rect]
                expecting_second_point = True
                redraw_path()
            else:
                start = win.temp_segment_start  # These are already coordinates (r, c)
                end = cells[rect]  # Get end coordinates
                
                if start != end:
                    r1, c1 = start
                    r2, c2 = end
                    # Only vertical or horizontal lines allowed
                    if r1 == r2 or c1 == c2:
                        save_state()
                        segments.append((start, end))
                expecting_second_point = False
                win.temp_segment_start = None  # Clear coordinates
                redraw_path()
        
        def handle_right_click(event):
            """Handle right click - place single point"""
            rect = get_cell_under_cursor(event)
            if rect is None:
                return
            
            save_state()
            
            # Add single point as segment (start and end same) - save coordinates
            r, c = cells[rect]
            segments.append(((r, c), (r, c)))
            redraw_path()
        
        def redraw_path(offset_x=0, offset_y=0):
            """Redraw path on matrix - line mode with bidirectional numbering based on A and B positions"""
            # Calculate offset for grid centering (same as in draw_matrix)
            if offset_x == 0 or offset_y == 0:
                canvas_width = canvas.winfo_width() if canvas.winfo_width() > 1 else int(current_cols * cell_size)
                canvas_height = canvas.winfo_height() if canvas.winfo_height() > 1 else int(current_rows * cell_size)
                
                total_grid_width = current_cols * cell_size
                total_grid_height = current_rows * cell_size
                
                offset_x = max(0, (canvas_width - total_grid_width) // 2)
                offset_y = max(0, (canvas_height - total_grid_height) // 2)
            
            # Clear cell colors
            for rect in cells:
                canvas.itemconfig(rect, fill="#3b4260")
            
            canvas.delete("text")
            canvas.delete("line")
            
            global_num = 0
            
            # Draw each segment with bidirectional numbering
            for seg_index, (start_rect, end_rect) in enumerate(segments):
                start_coords = start_rect if isinstance(start_rect, tuple) else cells.get(start_rect)
                end_coords = end_rect if isinstance(end_rect, tuple) else cells.get(end_rect)
                
                # Skip if coordinates not found
                if not start_coords or not end_coords:
                    continue
                
                r1, c1 = start_coords
                r2, c2 = end_coords
                
                line_cells = []
                
                if r1 == r2:  # Horizontal line - direction from A to B
                    if c1 <= c2:
                        for c in range(c1, c2 + 1):
                            line_cells.append((r1, c))
                    else:
                        for c in range(c1, c2 - 1, -1):
                            line_cells.append((r1, c))
                elif c1 == c2:  # Vertical line - direction from A to B
                    if r1 <= r2:
                        for r in range(r1, r2 + 1):
                            line_cells.append((r, c1))
                    else:
                        for r in range(r1, r2 - 1, -1):
                            line_cells.append((r, c1))
                # Diagonal lines prohibited
                
                for i, (r, c) in enumerate(line_cells):
                    rect = coords_to_rect.get((r, c))
                    if rect:
                        # Check if cell is occupied by previous segments
                        is_occupied = False
                        for prev_index in range(seg_index):
                            prev_start, prev_end = segments[prev_index]
                            # Get coordinates from prev_start/prev_end (may be rect ID or tuple)
                            spc = prev_start if isinstance(prev_start, tuple) else cells.get(prev_start)
                            epc = prev_end if isinstance(prev_end, tuple) else cells.get(prev_end)
                            
                            # Skip if coordinates not found
                            if not spc or not epc:
                                continue
                            
                            pr1, pc1 = spc
                            pr2, pc2 = epc
                            
                            # Check if cell (r,c) is on previous line
                            if pr1 == pr2:  # Horizontal
                                if r == pr1 and min(pc1, pc2) <= c <= max(pc1, pc2):
                                    is_occupied = True
                                    break
                            elif pc1 == pc2:  # Vertical
                                if c == pc1 and min(pr1, pr2) <= r <= max(pr1, pr2):
                                    is_occupied = True
                                    break
                        # Diagonal lines prohibited - skip occupancy check
                        
                        if is_occupied:
                            continue
                        
                        global_num += 1
                        # Use full RGB gradient with 2048 shades and cyclic behavior
                        r_color, g_color, b_color = get_rainbow_color(global_num - 1)
                        color = f'#{r_color:02x}{g_color:02x}{b_color:02x}'
                        canvas.itemconfig(rect, fill=color)
                        
                        coords = canvas.coords(rect)
                        cx = (coords[0] + coords[2]) / 2
                        cy = (coords[1] + coords[3]) / 2
                        
                        # Show only numbers - no letters
                        text = str(global_num)
                        canvas.create_text(cx, cy, text=text, fill="white", font=("Consolas", 9), tags=["text"])
            
            # Calculate offset for grid centering
            if offset_x == 0 or offset_y == 0:
                canvas_width = canvas.winfo_width() if canvas.winfo_width() > 1 else int(current_cols * cell_size)
                canvas_height = canvas.winfo_height() if canvas.winfo_height() > 1 else int(current_rows * cell_size)
                
                total_grid_width = current_cols * cell_size
                total_grid_height = current_rows * cell_size
                
                offset_x = max(0, (canvas_width - total_grid_width) // 2)
                offset_y = max(0, (canvas_height - total_grid_height) // 2)
            
            if hasattr(win, 'temp_segment_start'):
                start = win.temp_segment_start
                
                # If temp_segment_start is coordinates (tuple), find corresponding rect
                if isinstance(start, tuple):
                    r, c = start
                    start_rect = coords_to_rect.get((r, c))
                else:
                    start_rect = start
                
                if start_rect:
                    x1, y1, x2, y2 = canvas.coords(start_rect)
                    cx = (x1 + x2) / 2
                    cy = (y1 + y2) / 2
                    
                    # Highlight first point of segment as A
                    canvas.itemconfig(start_rect, fill="#7aa2f7")
                    canvas.create_text(cx, cy, text="A", fill="white", font=("Consolas", 9), tags=["text"])
                
        
        def save_mapping():
            """Save in format: number: x,y with topmost priority and bidirectional numbering"""
            if not segments:
                messagebox.showwarning("Warning", "No defined segments!")
                return
            
            # Create a temporary window to host the dialog
            temp_window = tk.Toplevel(win)
            temp_window.withdraw()
            temp_window.attributes("-topmost", True)
            
            file_path = filedialog.asksaveasfilename(
                parent=temp_window,
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt")]
            )
            
            temp_window.destroy()
            if not file_path:
                return
            
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    # Save all cells with their global numbers
                    global_num = 0
                    for seg_index in range(len(segments)):
                        start_rect = segments[seg_index][0]
                        end_rect = segments[seg_index][1]
                        
                        # Get coordinates
                        if isinstance(start_rect, tuple) and len(start_rect) == 2:
                            r1, c1 = start_rect
                        else:
                            coords = cells.get(start_rect)
                            if not coords:
                                continue
                            r1, c1 = coords
                        
                        if isinstance(end_rect, tuple) and len(end_rect) == 2:
                            r2, c2 = end_rect
                        else:
                            coords = cells.get(end_rect)
                            if not coords:
                                continue
                            r2, c2 = coords
                        
                        line_cells = []
                        
                        # Horizontal line - direction from A to B
                        if r1 == r2:
                            if c1 <= c2:
                                for c in range(c1, c2 + 1):
                                    line_cells.append((r1, c))
                            else:
                                for c in range(c1, c2 - 1, -1):
                                    line_cells.append((r1, c))
                        # Vertical line - direction from A to B
                        elif c1 == c2:
                            if r1 <= r2:
                                for r in range(r1, r2 + 1):
                                    line_cells.append((r, c1))
                            else:
                                for r in range(r1, r2 - 1, -1):
                                    line_cells.append((r, c1))
                        
                        for r, c in line_cells:
                            global_num += 1
                            # Format: number: x,y where x=r (row), y=c (column) - swapped places
                            f.write(f"{global_num}: {r},{c}\n")
                messagebox.showinfo("Success", "Mapping saved!")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save: {e}")
        
        def load_mapping():
            """Load from format: number: x,y with topmost priority"""
            # Create a temporary window to host the dialog
            temp_window = tk.Toplevel(win)
            temp_window.withdraw()
            temp_window.attributes("-topmost", True)
            
            file_path = filedialog.askopenfilename(
                parent=temp_window,
                filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
            )
            
            temp_window.destroy()
            if not file_path:
                return
            
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                
                # Load all points with their numbers
                # Format: number: x,y
                points_by_number = {}  # number -> (x, y)
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split(":")
                    if len(parts) != 2:
                        continue
                    
                    try:
                        num = int(parts[0].strip())
                        coords = parts[1].strip().split(",")
                        
                        if len(coords) == 2:
                            x = int(coords[0].strip())  # Now x is row
                            y = int(coords[1].strip())  # Now y is column
                            points_by_number[num] = (x, y)
                    except ValueError:
                        continue
                
                # Create independent segments for each point
                # Each point becomes separate segment (coordinates)
                segments.clear()
                
                sorted_nums = sorted(points_by_number.keys())
                
                for num in sorted_nums:
                    x, y = points_by_number[num]  # x=row(r), y=column(c)
                    rect = coords_to_rect.get((x, y))  # Use (row, col) directly
                    if rect:
                        # Get coordinates (r, c) from cells
                        r, c = cells[rect]
                        # Each point is separate segment (coordinates)
                        segments.append(((r, c), (r, c)))
                
                expecting_second_point = False
                if hasattr(win, 'temp_segment_start'):
                    delattr(win, 'temp_segment_start')
                
                redraw_path()
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load: {e}")
        
        # Control buttons - left side (undo)
        btn_row_left = tk.Frame(top_frame, bg=colors["bg"])
        btn_row_left.pack(side="right")
        
        ttk.Button(btn_row_left, text="Undo", command=undo).pack(side="left", padx=(0, 5))
        
        # Control buttons - right side
        btn_row = tk.Frame(top_frame, bg=colors["bg"])
        btn_row.pack(side="right")
        
        ttk.Button(btn_row, text="Reset", command=reset_mapping).pack(side="left", padx=(0, 5))
        ttk.Button(btn_row, text="Save", command=save_mapping).pack(side="left", padx=(0, 5))
        ttk.Button(btn_row, text="Load", command=load_mapping).pack(side="left", padx=(0, 5))
        
        # Window resize binding for automatic centering
        canvas.bind("<Configure>", lambda e: redraw_on_resize_wrapper())
        
        # Event bindings
        canvas.bind("<Button-1>", handle_left_click)
        canvas.bind("<Button-3>", handle_right_click)
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(1 if e.delta < 0 else -1, "units"))
        
        # Event bindings for automatic update on size change
        rows_var.trace_add("write", lambda *args: on_size_change_wrapper())
        cols_var.trace_add("write", lambda *args: on_size_change_wrapper())
        
        def redraw_on_resize_wrapper():
            """Redraw grid on canvas size change for centering"""
            # Check that canvas still exists and has dimensions
            if not win.winfo_exists():
                return
            
            current_width = canvas.winfo_width()
            current_height = canvas.winfo_height()
            
            # If canvas is not initialized or too small - ignore
            if current_width <= 1 or current_height <= 1:
                return
            
            # Update scroll region
            update_scroll_region()
            
            # Redraw grid with new centering coordinates
            draw_matrix()
        
        def on_size_change_wrapper():
            """Handle matrix size change for mapping window"""
            try:
                rows_text = rows_var.get().strip()
                cols_text = cols_var.get().strip()
                
                # Check for empty values
                if not rows_text or not cols_text:
                    return
                
                new_rows = int(rows_text)
                new_cols = int(cols_text)
                
                # Protection against 0 and negative values
                if new_rows <= 0 or new_cols <= 0:
                    return
                
                nonlocal current_rows, current_cols
                current_rows = new_rows
                current_cols = new_cols
                # Clear state before redraw (but NOT segments!)
                expecting_second_point = False
                if hasattr(win, 'temp_segment_start'):
                    delattr(win, 'temp_segment_start')
                draw_matrix()
            except ValueError:
                pass
        
        def on_cell_size_change_wrapper():
            """Handle cell size change with protection against 0 and empty value"""
            try:
                cell_size_text = cell_size_var.get().strip()
                
                # Check for empty value
                if not cell_size_text:
                    return
                
                new_cell_size = int(cell_size_text)
                
                # Protection against 0 and negative values
                if new_cell_size <= 0:
                    return
                
                nonlocal cell_size
                cell_size = new_cell_size
                redraw_on_resize_wrapper()
            except ValueError:
                pass
        
        # Initialize rendering
        update_scroll_region()
        win.update_idletasks()  # Update canvas dimensions before first render
        
        def _draw_matrix_later():
            """Delayed initialization of grid rendering after window rendering"""
            if win.winfo_exists():
                draw_matrix()
        
        def _init_mapping():
            """Initialization: loading saved data and drawing matrix"""
            if win.winfo_exists():
                load_mapping_auto_save()  # Load saved data when opening window
        
        win.after(10, _draw_matrix_later)
        win.after(20, _init_mapping)  # Load data after matrix rendering
        
        # Window close handler - reset reference in root window
        def on_destroy(event):
            if root._mapping_window == win:
                root._mapping_window = None
        
        win.bind("<Destroy>", on_destroy)
        
        # Add auto-save support for mapping window
        auto_save_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mapping_data.json")
        
        def save_mapping_auto_save():
            """Save current data to JSON file for automatic restoration"""
            try:
                segments_data = []
                for start, end in segments:
                    # Always save coordinates (r, c)
                    if isinstance(start, tuple) and len(start) == 2:
                        r1, c1 = start
                    else:
                        coords = cells.get(start)
                        if not coords:
                            continue
                        r1, c1 = coords
                    
                    if isinstance(end, tuple) and len(end) == 2:
                        r2, c2 = end
                    else:
                        coords = cells.get(end)
                        if not coords:
                            continue
                        r2, c2 = coords
                    
                    segments_data.append({"start": [r1, c1], "end": [r2, c2]})
                
                data = {
                    "rows": current_rows,
                    "cols": current_cols,
                    "cell_size": cell_size,
                    "segments": segments_data,
                    "expecting_second_point": expecting_second_point,
                    "temp_segment_start": None
                }
                
                if hasattr(win, 'temp_segment_start'):
                    temp_start_val = getattr(win, 'temp_segment_start')
                    data["temp_segment_start"] = cells.get(temp_start_val)
                
                with open(auto_save_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[WARN] Failed to save auto-save: {e}")
        
        def load_mapping_auto_save():
            """Load auto-saved data from JSON file"""
            nonlocal current_rows, current_cols, cell_size
            try:
                if os.path.exists(auto_save_file):
                    with open(auto_save_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    
                    # Restore matrix dimensions in widgets
                    new_rows = int(data.get("rows", 10))
                    new_cols = int(data.get("cols", 10))
                    new_cell_size = int(data.get("cell_size", 25))
                    
                    rows_var.set(str(new_rows))
                    cols_var.set(str(new_cols))
                    
                    # Update cell size if different
                    if new_cell_size != cell_size:
                        cell_size_var.set(str(new_cell_size))
                        cell_size = new_cell_size
                    
                    # Restore segments - data now stored as coordinates (r, c)
                    loaded_segments = []
                    
                    if new_rows != current_rows or new_cols != current_cols:
                        current_rows = new_rows
                        current_cols = new_cols
                        # Recreate grid on dimension change
                        draw_matrix()
                    
                    for seg in data.get("segments", []):
                        r1, c1 = seg["start"]
                        r2, c2 = seg["end"]
                        # Store as coordinates, not rect ID
                        loaded_segments.append(((r1, c1), (r2, c2)))
                    
                    segments.clear()
                    segments.extend(loaded_segments)
                    
                    expecting_second_point = data.get("expecting_second_point", False)
                    temp_start = data.get("temp_segment_start")
                    if temp_start and isinstance(temp_start, list):
                        # temp_segment_start stored as coordinates [r, c]
                        r, c = temp_start
                        start_coords = (r, c)
                        win.temp_segment_start = start_coords
                    
                    redraw_path()
            except Exception as e:
                print(f"[WARN] Failed to load auto-save: {e}")
        
        # Event bindings for automatic update on size change
        rows_var.trace_add("write", lambda *args: on_size_change_wrapper())
        cols_var.trace_add("write", lambda *args: on_size_change_wrapper())
        cell_size_var.trace_add("write", lambda *args: on_cell_size_change_wrapper())
        
        # Window close binding for saving
        def on_destroy_with_save(event):
            save_mapping_auto_save()
        
        win.bind("<Destroy>", on_destroy_with_save)
        
        return win
    
    except Exception as e:
        print(f"[ERROR] Failed to open mapping window: {e}")


if __name__ == "__main__":
    root = tk.Tk()
    app = LEDMapper(root)
    root.mainloop()