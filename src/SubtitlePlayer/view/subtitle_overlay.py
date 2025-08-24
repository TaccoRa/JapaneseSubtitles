import tkinter as tk
from typing import List, Optional

from model.config_manager import ConfigManager
from utils import make_draggable

class SubtitleOverlayUI:

    def __init__(self, root: tk.Tk, config: ConfigManager,cleaned_subs: Optional[List[str]] = None, overlay_geometry: dict = None) -> None:
        self.root = root
        self.sub_window: tk.Toplevel = None 
        self.subtitle_canvas: tk.Canvas = None
        self.subtitle_handle = None
        self.config = config
        self.cleaned_subs = cleaned_subs
        self.max_h = overlay_geometry["max_height"]
        self.max_w = overlay_geometry["max_width"]
        self.center_x = self.config.get("LAST_SUB_CENTER_X")
        self.center_y = self.config.get("LAST_SUB_CENTER_Y")

        self.build_overlay()

    def build_overlay(self) -> None:
        self.sub_window = tk.Toplevel(self.root)
        self.sub_window.overrideredirect(True)
        self.sub_window.attributes("-topmost", True)
        self.sub_window.attributes("-transparentcolor", "grey")

        x = int(self.center_x - self.max_w / 2)
        y = int(self.center_y - self.max_h / 2)
        sw, sh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        x = max(0, min(x, sw - self.max_w))
        y = max(0, min(y, sh - self.max_h))
        self.sub_window.geometry(f"{self.max_w}x{self.max_h}+{x}+{y}")
        self.sub_window.update_idletasks()

        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(self.border_frame, bg="grey", highlightthickness=0)
        self.subtitle_canvas.pack(fill="both", expand=True)
        if self.config.get("PHONEMODE_DEFAULT"):
            self.show_handle()
        else:
            self.hide_handle()
        
        make_draggable(self.sub_window, self.sub_window,
                       on_release=self._save_center_position)

        self.sub_window.bind("<Enter>", lambda ev: self.on_sub_window_enter(ev))
        self.sub_window.bind("<Leave>", lambda ev: self.on_sub_window_leave(ev))

    # Subtitle overlay
    def bind_sub_window_enter(self, cb): self.on_sub_window_enter = cb
    def bind_sub_window_leave(self, cb): self.on_sub_window_leave = cb
    def bind_sub_handel_enter(self, cb): self.on_handle_enter = cb
    
    def show_handle(self):
        self.subtitle_handle = tk.Toplevel(self.root)
        self.subtitle_handle.overrideredirect(True)
        self.subtitle_handle.attributes("-topmost", True)
        self.sub_window.update_idletasks()
        sub_x = self.sub_window.winfo_x()
        sub_y = self.sub_window.winfo_y()
        drag_w, drag_h = 80, self.sub_window.winfo_height()
        self.subtitle_handle.geometry(f"{drag_w}x{drag_h}+{sub_x}+{sub_y}")
        self.subtitle_handle.attributes("-alpha", 0.05)

        self.subtitle_handle.bind("<Enter>", lambda ev: self.on_handle_enter(ev))
        make_draggable(self.subtitle_handle, self.sub_window,
                       sync_windows=[self.subtitle_handle],
                       on_release=self._save_center_position)
        make_draggable(self.sub_window, self.sub_window,
                       sync_windows=[self.subtitle_handle], 
                       on_release=self._save_center_position)

    def hide_handle(self):
        if self.subtitle_handle:
            self.subtitle_handle.attributes("-alpha", 0.0)

    def _save_center_position(self, win_x, win_y, win_w, win_h):
        cx = win_x + win_w / 2
        cy = win_y + win_h / 2
        self.config.set("LAST_SUB_CENTER_X", cx)
        self.config.set("LAST_SUB_CENTER_Y", cy)
        self.center_x = cx
        self.center_y = cy