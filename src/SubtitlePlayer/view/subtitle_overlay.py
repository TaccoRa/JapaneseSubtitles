import tkinter as tk
from tkinter import font as tkFont
from typing import List, Optional

from model.config_manager import ConfigManager
from utils import make_draggable

class SubtitleOverlayUI:

    def __init__(self, root: tk.Tk, config: ConfigManager,cleaned_subs: Optional[List[str]] = None, control_ui=None) -> None:
        self.root = root
        self.config = config
        self.font = tkFont.Font(family=config.get("SUBTITLE_FONT"),size=config.get("SUBTITLE_FONT_SIZE"),weight="bold")
        self.line_height = self.font.metrics("linespace")
        self.cleaned_subs = cleaned_subs or []

        self.control_ui = control_ui
        self.sub_window: tk.Toplevel = None
        self.handle_win: tk.Toplevel = None
        self.subtitle_handle = None
        self.subtitle_canvas: tk.Canvas = None
        self.max_width: int = 0
        self.bottom_anchor: int = 0
        self.build_overlay()

    def build_overlay(self) -> None:
        self.max_width = self.compute_max_width(self.cleaned_subs)
        init_height = self.line_height * 2
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos_x = (sw - self.max_width) // 2
        pos_y = sh - init_height - 215

        # main subtitle window
        self.sub_window = tk.Toplevel(self.root)
        self.sub_window.overrideredirect(True)
        self.sub_window.attributes("-topmost", True)
        self.sub_window.attributes("-transparentcolor", "grey")
        self.sub_window.geometry(f"{self.max_width}x{init_height}+{pos_x}+{pos_y}")
        self.sub_window.update_idletasks()
        self.bottom_anchor = pos_y + init_height

        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(
            self.border_frame,
            bg="grey",
            highlightthickness=0
        )
        self.subtitle_canvas.pack(fill="both", expand=True)
        make_draggable(self.sub_window, self.sub_window,on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))

    def show_handle(self):
        if self.subtitle_handle is not None:
            return  # Already exists
        drag_w, drag_h = 60, self.line_height * 2
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos_x = (sw - self.max_width) // 2
        pos_y = sh - drag_h - 215
        self.subtitle_handle = tk.Toplevel(self.root)
        self.subtitle_handle.overrideredirect(True)
        self.subtitle_handle.attributes("-topmost", True)
        self.subtitle_handle.attributes("-alpha", 0.05)
        self.subtitle_handle.geometry(f"{drag_w}x{drag_h}+{pos_x}+{pos_y}")
        make_draggable(self.subtitle_handle, self.sub_window, sync_windows=[self.subtitle_handle],on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))
        make_draggable(self.sub_window, self.sub_window, sync_windows=[self.subtitle_handle],on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))

    def hide_handle(self):
        if self.subtitle_handle is not None:
            self.subtitle_handle.destroy()
            self.subtitle_handle = None

    def compute_max_width(self, cleaned_subs: List[str]) -> int:
        return max(
            self.font.measure(line)
            for text in cleaned_subs
            for line in text.splitlines()
        )