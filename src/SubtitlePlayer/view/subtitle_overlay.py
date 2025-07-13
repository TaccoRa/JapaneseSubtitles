import tkinter as tk
from tkinter import font as tkFont
from typing import List, Optional
import regex

from model.config_manager import ConfigManager
from utils import make_draggable

class SubtitleOverlayUI:

    def __init__(self, root: tk.Tk, config: ConfigManager,cleaned_subs: Optional[List[str]] = None, control_ui=None) -> None:
        self.root = root
        self.control_ui = control_ui
        self.sub_window: tk.Toplevel = None
        self.handle_win: tk.Toplevel = None   
        self.subtitle_canvas: tk.Canvas = None
        self.subtitle_handle = None

        self.config = config
        self.font = tkFont.Font(family=config.get("SUBTITLE_FONT"),size=config.get("SUBTITLE_FONT_SIZE"),weight="bold")
        self.line_height = self.font.metrics("linespace")

        self.cleaned_subs = cleaned_subs or []


        self.max_width: int = 0
        self.max_ruby_h = int(self.font.metrics('linespace') * 0.6)
        self.max_total_height = self.max_ruby_h * 2 + self.line_height * 2

        self.pad_x = 5
        self.ref_x = 0
        self.ref_y = 0

        self.build_overlay()

    def build_overlay(self) -> None:
        self.sub_window = tk.Toplevel(self.root)
        self.sub_window.overrideredirect(True)
        self.sub_window.attributes("-topmost", True)
        self.sub_window.attributes("-transparentcolor", "grey")

        max_content_w = self.compute_max_width(self.cleaned_subs)
        max_content_h = self.max_ruby_h * 2 + self.line_height * 2
        init_w = max_content_w + 2*self.pad_x
        init_h = max_content_h 
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        default_x = (sw - init_w)//2
        default_y = (sh - init_h - 215)

        ref_x  = self.config.get("LAST_SUBTITLE_WINDOW_X", default_x)
        ref_y  = self.config.get("LAST_SUBTITLE_WINDOW_Y", default_y)
        self.ref_x, self.ref_y = ref_x, ref_y 

        self.sub_window.geometry(f"{init_w}x{init_h}+{ref_x}+{ref_y}")
        self.sub_window.update_idletasks()
        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(
            self.border_frame, bg="grey", highlightthickness=0
        )
        self.subtitle_canvas.pack(fill="both", expand=True)

        make_draggable(
            self.sub_window, self.sub_window,
            on_drag=self.on_sub_drag,
            save_position=None
        )

        self.sub_window.bind("<Enter>", lambda ev: self.on_sub_window_enter(ev))
        self.sub_window.bind("<Leave>", lambda ev: self.on_sub_window_leave(ev))

    def on_sub_drag(self, bottom: int) -> None:
        win_x = self.sub_window.winfo_x()
        win_y = self.sub_window.winfo_y()
        win_w = self.sub_window.winfo_width()
        win_h = self.sub_window.winfo_height()

        ref_w = self.max_width + 2 * self.pad_x
        ref_h = self.max_total_height

        ref_x = win_x - (ref_w - win_w) // 2
        ref_y = win_y - (ref_h - win_h) // 2

        self.config.set("LAST_SUBTITLE_WINDOW_X", ref_x)
        self.config.set("LAST_SUBTITLE_WINDOW_Y", ref_x)

    # Subtitle overlay
    def bind_sub_window_enter(self, cb): self.on_sub_window_enter = cb
    def bind_sub_window_leave(self, cb): self.on_sub_window_leave = cb
    def bind_sub_handel_enter(self, cb): self.on_handle_enter = cb
    
    def show_handle(self):
        # if self.subtitle_handle is not None:
        #     return  # Already exists
        self.sub_window.update_idletasks()
        sub_x = self.sub_window.winfo_x()
        sub_y = self.sub_window.winfo_y()
        drag_w, drag_h = 80, self.sub_window.winfo_height()

        self.subtitle_handle = tk.Toplevel(self.root)
        self.subtitle_handle.overrideredirect(True)
        self.subtitle_handle.attributes("-topmost", True)
        self.subtitle_handle.attributes("-alpha", 0.05)

        self.subtitle_handle.geometry(f"{drag_w}x{drag_h}+{sub_x}+{sub_y}")
        self.subtitle_handle.bind("<Enter>", lambda ev: self.on_handle_enter(ev))
        make_draggable(self.subtitle_handle, self.sub_window, sync_windows=[self.subtitle_handle],on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))
        make_draggable(self.sub_window, self.sub_window, sync_windows=[self.subtitle_handle],on_drag=lambda bottom: setattr(self, "bottom_anchor", bottom))

    def hide_handle(self):
        self.subtitle_handle.attributes("-alpha", 0.0)

    def compute_max_width(self, cleaned_subs: List[str]) -> int:
        max_width = 0
        for text in cleaned_subs:
            base_text = regex.sub(r'\p{Han}+\([^)]+\)', lambda m: regex.match(r'(\p{Han}+)', m.group()).group(), text)
            for line in base_text.splitlines():
                width = self.font.measure(line)
                max_width = max(max_width, width)
        return max_width
