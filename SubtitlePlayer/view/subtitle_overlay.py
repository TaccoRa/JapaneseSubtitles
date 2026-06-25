"""
Subtitle overlay window (transparent canvas) that renders the current subtitle text.

This is a separate always-on-top, borderless toplevel window that can be dragged.
"""

import tkinter as tk
import logging
from typing import List, Optional

from model.config_manager import ConfigManager
from utils import make_draggable, make_nonactivating_tool_window, show_window_no_activate

logger = logging.getLogger(__name__)

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
        self._handle_width = 80
        self.max_w, self.max_h = overlay_geometry
        self.center_x = self.config.get("LAST_SUB_CENTER_X")
        self.center_y = self.config.get("LAST_SUB_CENTER_Y")
        if not isinstance(self.center_x, (int, float)):
            try:
                self.center_x = float(self.root.winfo_vrootwidth() or self.root.winfo_screenwidth()) / 2.0
            except Exception:
                self.center_x = 960.0
        if not isinstance(self.center_y, (int, float)):
            try:
                self.center_y = float(self.root.winfo_vrootheight() or self.root.winfo_screenheight()) * 0.75
            except Exception:
                self.center_y = 810.0
        self.on_sub_window_enter = lambda _ev=None: None
        self.on_sub_window_leave = lambda _ev=None: None
        self.on_handle_enter = lambda _ev=None: None

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
        
        self._bind_subtitle_drag()

        self.sub_window.bind("<Enter>", lambda ev: self.on_sub_window_enter(ev))
        self.sub_window.bind("<Leave>", lambda ev: self.on_sub_window_leave(ev))

        if self._start_hidden:
            self.sub_window.withdraw()
            if self.subtitle_handle:
                self.subtitle_handle.withdraw()

    # Subtitle overlay
    def bind_sub_window_enter(self, cb): self.on_sub_window_enter = cb
    def bind_sub_window_leave(self, cb): self.on_sub_window_leave = cb
    def bind_sub_handle_enter(self, cb): self.on_handle_enter = cb

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
        self._sync_handle_to_subtitle()
        # Resize canvas to match coordinate system the renderer expects
        self.subtitle_canvas.config(width=self.max_w, height=self.max_h)
        self.subtitle_canvas.update_idletasks()

    def _sync_handle_to_subtitle(self):
        if not self.subtitle_handle:
            return
        if not self.subtitle_handle.winfo_exists():
            return
        self.sub_window.update_idletasks()
        sub_x = self.sub_window.winfo_x()
        sub_y = self.sub_window.winfo_y()
        drag_w = int(self._handle_width)
        drag_h = self.sub_window.winfo_height()
        self.subtitle_handle.geometry(f"{drag_w}x{drag_h}+{sub_x}+{sub_y}")

    def _bind_subtitle_drag(self):
        sync_windows = None
        try:
            if (
                self.subtitle_handle
                and self.subtitle_handle.winfo_exists()
                and str(self.subtitle_handle.state()) != "withdrawn"
            ):
                sync_windows = [self.subtitle_handle]
        except Exception as e:
            logger.debug("Failed to inspect subtitle handle during drag bind: %s", e, exc_info=True)
            sync_windows = None
        make_draggable(
            self.sub_window,
            self.sub_window,
            sync_windows=sync_windows,
            on_release=self._save_center_position,
        )

    def show_handle(self):
        if self.subtitle_handle:
            try:
                if self.subtitle_handle.winfo_exists():
                    self._sync_handle_to_subtitle()
                    self.subtitle_handle.attributes("-alpha", 0.05)
                    show_window_no_activate(self.subtitle_handle)
                    self._bind_subtitle_drag()
                    return
            except Exception as e:
                logger.debug("Failed to show subtitle handle: %s", e, exc_info=True)
                self.subtitle_handle = None

        self.subtitle_handle = tk.Toplevel(self.root)
        self.subtitle_handle.withdraw()
        self.subtitle_handle.overrideredirect(True)
        self.subtitle_handle.attributes("-topmost", True)
        make_nonactivating_tool_window(self.subtitle_handle)
        self._sync_handle_to_subtitle()
        self.subtitle_handle.attributes("-alpha", 0.05)

        self.subtitle_handle.bind("<Enter>", lambda ev: self.on_handle_enter(ev))
        make_draggable(self.subtitle_handle, self.sub_window,
                       sync_windows=[self.subtitle_handle],
                       on_release=self._save_center_position)
        make_draggable(self.sub_window, self.sub_window,
                       sync_windows=[self.subtitle_handle], 
                       on_release=self._save_center_position)
        if self._start_hidden:
            self.subtitle_handle.withdraw()
        else:
            show_window_no_activate(self.subtitle_handle)
        self._bind_subtitle_drag()

    def hide_handle(self):
        if self.subtitle_handle:
            try:
                self.subtitle_handle.withdraw()
            except Exception as e:
                logger.debug("Failed to hide subtitle handle: %s", e, exc_info=True)
                self.subtitle_handle.attributes("-alpha", 0.0)
        self._bind_subtitle_drag()

    def _save_center_position(self, x, y, w, h):
        self.center_x = x + w / 2
        self.center_y = y + h / 2
        
    def save_state(self):
        if (self.center_x, self.center_y) != (self.config.get("LAST_SUB_CENTER_X"), self.config.get("LAST_SUB_CENTER_Y")):
            self.config.set("LAST_SUB_CENTER_X", self.center_x)
            self.config.set("LAST_SUB_CENTER_Y", self.center_y)

    def show(self) -> None:
        """Show overlay (and handle if enabled). Used after startup splash."""
        self._start_hidden = False
        self.sub_window.deiconify()
        self.sub_window.lift()
        self.sub_window.attributes("-topmost", True)
        if self.config.get("PHONEMODE_DEFAULT"):
            self.show_handle()
