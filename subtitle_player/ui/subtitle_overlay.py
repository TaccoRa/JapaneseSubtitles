import tkinter as tk
from tkinter import font as tkFont

from config_manager import ConfigManager

class SubtitleOverlayUI:

    def __init__(self, root: tk.Tk, config: ConfigManager, font: tkFont.Font, line_height: int):
        self.config = config
        self.sub_window = tk.Toplevel(root)
        self.handle_win = tk.Toplevel(root)
        self.build_overlay()

    def build_overlay(self):
        self.sub_window = tk.Toplevel(self.root)
        self.sub_window.overrideredirect(True)
        self.sub_window.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        init_height = self.line_height * 2
        pos_x = (sw - self.max_width) // 2
        pos_y = sh - init_height - 215
        self.sub_window.geometry(f"{self.max_width}x{init_height}+{pos_x}+{pos_y}")
        self.sub_window.attributes("-transparentcolor", "grey")
        self.sub_window_bottom_anchor = pos_y + init_height

        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(self.border_frame, bg="grey", highlightthickness=0)
        self.subtitle_canvas.pack(fill="both", expand=True)
        self.subtitle_canvas.bind("<Button-3>", self.open_copy_popup)  

        drag_w, drag_h = 60, init_height
        self.handle_win = tk.Toplevel(self.root)
        self.handle_win.attributes("-topmost", True)
        self.handle_win.overrideredirect(True)
        is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
        self.handle_win.attributes("-alpha", 0.05 if is_phone_mode else 0.0)
        self.handle_win.geometry(f"{drag_w}x{drag_h}+{pos_x}+{pos_y}")
        self.make_draggable(self.handle_win, self.sub_window, sync_windows=[self.handle_win]) # type: ignore

        self.make_draggable(self.sub_window, self.sub_window) # type: ignore

    def bind_hover(self, on_enter, on_leave):
        for widget in [self.sub_window, self.border_frame]:
            widget.bind("<Enter>", self.on_sub_enter)
            widget.bind("<Leave>", self.on_sub_leave)
        self.control_window.bind("<Enter>", self.on_controls_enter)
        self.control_window.bind("<Leave>", self.on_controls_leave)
        self.control_window.bind("<Motion>", self.on_controls_enter)
        self.sub_window.bind("<Enter>", self.subtitle_hover_enter)
        self.sub_window.bind("<Leave>", self.subtitle_hover_leave)
    
    def on_controls_enter(self, event):
        self.set_mouse_over("controls", True)
    def on_controls_leave(self, event):
        self.set_mouse_over("controls", False)
    
    def on_sub_enter(self, event):
        self.set_mouse_over("subtitles", True)
    def on_sub_leave(self, event):
        self.set_mouse_over("subtitles", False)


    def make_draggable(self, drag_handle: tk.Widget, target: tk.Toplevel, sync_windows: list[tk.Toplevel] = None) -> None:
        def start_drag(event):
            drag_handle._drag_start_x = event.x_root
            drag_handle._drag_start_y = event.y_root
        def do_drag(event):
            dx = event.x_root - drag_handle._drag_start_x
            dy = event.y_root - drag_handle._drag_start_y
            new_x = target.winfo_x() + dx
            new_y = target.winfo_y() + dy
            target.geometry(f"+{new_x}+{new_y}")

            if sync_windows:
                for win in sync_windows:
                    win.geometry(f"+{new_x}+{new_y}")

            drag_handle._drag_start_x = event.x_root
            drag_handle._drag_start_y = event.y_root

            if target == self.sub_window:
                self.sub_window_bottom_anchor = new_y + target.winfo_height()
        drag_handle.bind("<ButtonPress-1>", start_drag)
        drag_handle.bind("<B1-Motion>", do_drag)

    def subtitle_hover_enter(self, event) -> None:
        self.sub_window.attributes("-transparentcolor", "")
        self.control_window.attributes("-topmost", True)
    def subtitle_hover_leave(self, event) -> None:
        self.sub_window.attributes("-transparentcolor", "grey")
        self.control_window.attributes("-topmost", False)

    def on_subtitle_click(self, event: tk.Event) -> None:
        self.user_hidden = not self.user_hidden
        if self.user_hidden:
            self.subtitle_canvas.delete("all")
    
    def schedule_hide_controls(self) -> None:
        is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
        if is_phone_mode:
            if self.control_hide_timer_job is not None:
                self.root.after_cancel(self.control_hide_timer_job)
            self.control_hide_timer_job = self.root.after(self.disappear_timer, self.lower_controls)
