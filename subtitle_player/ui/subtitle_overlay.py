import tkinter as tk
from tkinter import font as tkFont
from typing import List, Optional

from config_manager import ConfigManager

class SubtitleOverlayUI:

    def __init__(self, root: tk.Tk, config: ConfigManager, font: tkFont.Font, line_height: int,cleaned_subs: Optional[List[str]] = None) -> None:
        self.root = root
        self.config = config
        self.font = font
        self.line_height = line_height
        self.cleaned_subs = cleaned_subs or []

        self.sub_window: tk.Toplevel = None
        self.handle_win: tk.Toplevel = None
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
        self.bottom_anchor = pos_y + init_height

        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(
            self.border_frame,
            bg="grey",
            highlightthickness=0
        )
        self.subtitle_canvas.pack(fill="both", expand=True)

        # drag handle
        drag_w, drag_h = 60, init_height
        self.handle_win = tk.Toplevel(self.root)
        self.handle_win.overrideredirect(True)
        self.handle_win.attributes("-topmost", True)
        self.handle_win.attributes("-alpha", 0.0)
        self.handle_win.geometry(f"{drag_w}x{drag_h}+{pos_x}+{pos_y}")

        self.make_draggable(self.handle_win, self.sub_window, sync_windows=[self.handle_win])
        self.make_draggable(self.sub_window, self.sub_window)

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
                self.bottom_anchor = new_y + target.winfo_height()
        drag_handle.bind("<ButtonPress-1>", start_drag)
        drag_handle.bind("<B1-Motion>", do_drag)

    def compute_max_width(self, cleaned_subs: List[str]) -> int:
        return max(
            self.font.measure(line)
            for text in cleaned_subs
            for line in text.splitlines()
        )
