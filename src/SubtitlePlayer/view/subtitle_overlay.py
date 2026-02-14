"""
Subtitle overlay window (transparent canvas) that renders the current subtitle text.

This is a separate always-on-top, borderless toplevel window that can be dragged.
"""

import tkinter as tk
from typing import List, Optional

from model.config_manager import ConfigManager
from utils import make_draggable

class SubtitleOverlayUI:

    def __init__(
        self,
        root: tk.Tk,
        config: ConfigManager,
        cleaned_subs: Optional[List[str]] = None,
        overlay_geometry=None,
        start_hidden: bool = False,
    ) -> None:
        self.root = root
        self.config = config
        self.cleaned_subs = cleaned_subs
        self._start_hidden = bool(start_hidden)

        self.sub_window: tk.Toplevel = None 
        self.subtitle_canvas: tk.Canvas = None
        self.subtitle_handle = None
        self.max_w, self.max_h = overlay_geometry
        self.center_x = self.config.get("LAST_SUB_CENTER_X")
        self.center_y = self.config.get("LAST_SUB_CENTER_Y")

        self.build_overlay()

    def build_overlay(self) -> None:
        self.sub_window = tk.Toplevel(self.root)
        self.sub_window.overrideredirect(True)
        self.sub_window.attributes("-topmost", True)
        self.sub_window.attributes("-transparentcolor", "grey")

        # Clamp overlay width to the visible desktop to avoid off-screen windows.
        sw = self.root.winfo_vrootwidth()
        margin = 20  # keep at least 20px visible margin on left+right
        max_w_allowed = max(100, int(sw) - margin * 2)
        self.max_w = max(100, min(int(self.max_w), max_w_allowed))
        self.max_h = max(80, int(self.max_h))

        x = int(self.center_x - self.max_w / 2)
        y = int(self.center_y - self.max_h / 2)
        sw, sh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        x = max(margin, min(x, sw - margin - self.max_w))
        y = max(0, min(y, sh - self.max_h))
        self.sub_window.geometry(f"{self.max_w}x{self.max_h}+{x}+{y}")
        self.sub_window.update_idletasks()

        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(
            self.border_frame,
            bg="grey",
            highlightthickness=0,
            width=self.max_w,
            height=self.max_h
        )
        self.subtitle_canvas.pack(fill="both", expand=True)
        if self.config.get("PHONEMODE_DEFAULT"):
            self.show_handle()
        else:
            self.hide_handle()
        
        make_draggable(self.sub_window, self.sub_window,
                       on_release=self._save_center_position)

        self.sub_window.bind("<Enter>", lambda ev: self.on_sub_window_enter(ev))
        self.sub_window.bind("<Leave>", lambda ev: self.on_sub_window_leave(ev))

        if self._start_hidden:
            try:
                self.sub_window.withdraw()
            except Exception:
                pass
            try:
                if self.subtitle_handle:
                    self.subtitle_handle.withdraw()
            except Exception:
                pass

    # Subtitle overlay
    def bind_sub_window_enter(self, cb): self.on_sub_window_enter = cb
    def bind_sub_window_leave(self, cb): self.on_sub_window_leave = cb
    def bind_sub_handel_enter(self, cb): self.on_handle_enter = cb

    def update_geometry(self, new_w, new_h):
        """Resize overlay window and internal canvas to the new width/height (integers)."""
        sw = self.root.winfo_vrootwidth()
        margin = 20  # keep at least 20px visible margin on left+right
        max_w_allowed = max(100, int(sw) - margin * 2)

        self.max_w = max(100, min(int(new_w), max_w_allowed))
        self.max_h = max(80, int(new_h))

        # Recenter around stored center_x/center_y
        x = int(self.center_x - self.max_w / 2)
        y = int(self.center_y - self.max_h / 2)

        # Clamp to screen
        sw = self.root.winfo_vrootwidth()
        sh = self.root.winfo_vrootheight()
        x = max(margin, min(x, sw - margin - self.max_w))
        y = max(0, min(y, sh - self.max_h))

        # Apply geometry
        self.sub_window.geometry(f"{self.max_w}x{self.max_h}+{x}+{y}")
        # Resize canvas to match coordinate system the renderer expects
        self.subtitle_canvas.config(width=self.max_w, height=self.max_h)
        self.subtitle_canvas.update_idletasks()


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
        if self._start_hidden:
            try:
                self.subtitle_handle.withdraw()
            except Exception:
                pass

    def hide_handle(self):
        if self.subtitle_handle:
            self.subtitle_handle.attributes("-alpha", 0.0)

    def _save_center_position(self, x, y, w, h):
        self.center_x = x + w / 2
        self.center_y = y + h / 2
        
    def save_state(self):
        if (self.center_x, self.center_y) != (self.config.get("LAST_SUB_CENTER_X"), self.config.get("LAST_SUB_CENTER_Y")):
            self.config.set("LAST_SUB_CENTER_X", self.center_x)
            self.config.set("LAST_SUB_CENTER_Y", self.center_y)

    def show(self) -> None:
        """Show overlay (and handle if enabled). Used after startup splash."""
        try:
            self.sub_window.deiconify()
            self.sub_window.lift()
            self.sub_window.attributes("-topmost", True)
        except Exception:
            pass
        try:
            if self.subtitle_handle and self.config.get("PHONEMODE_DEFAULT"):
                self.subtitle_handle.deiconify()
                self.subtitle_handle.lift()
                self.subtitle_handle.attributes("-topmost", True)
        except Exception:
            pass
