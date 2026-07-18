"""
UI overlays used by SubtitlePlayer.

This module currently provides LoadingOverlay: a small runtime "please wait" overlay.
"""
import tkinter as tk
from tkinter import ttk
from typing import Optional
from utils import dispatch_to_tk

class LoadingOverlay:
    """
    Simple modal "loading" overlay that blocks UI interaction (grab_set).
    """
    def __init__(self, root: tk.Tk, text: str = "Loading...", modal: bool = True, anchor_window: Optional[tk.Misc] = None, y_offset: int = 0) -> None:
        self.root = root
        self.anchor_window = anchor_window
        self.y_offset = int(y_offset or 0)
        self._anchor_prev_topmost = None

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        if modal:
            self._win.grab_set()

        if self.anchor_window is not None:
            try:
                self._anchor_prev_topmost = bool(self.anchor_window.attributes("-topmost"))
            except Exception:
                self._anchor_prev_topmost = None
            try:
                self.anchor_window.attributes("-topmost", True)
                self.anchor_window.lift()
            except Exception:
                pass

        frame = tk.Frame(self._win, bg="#111111", bd=2, relief="solid")
        frame.pack(fill="both", expand=True)

        lbl = tk.Label(frame, text=text, fg="white", bg="#111111", font=("Arial", 12, "bold"))
        lbl.pack(padx=20, pady=(16, 10))

        # A looping 0..100 progress bar (not tied to actual progress).
        pb = ttk.Progressbar(frame, mode="determinate", maximum=15, value=0, length=240)
        pb.pack(padx=20, pady=(0, 16))
        try:
            pb.start(50)
        except Exception:
            pass

        _center_toplevel(self._win, root=self.root, anchor_window=self.anchor_window, y_offset=self.y_offset)

        # Force at least one paint before the caller blocks the UI thread.
        try:
            self._win.update_idletasks()
            self._win.update()
        except Exception:
            pass
        try:
            # Ensure overlay is above the anchor window.
            self._win.lift()
            self._win.attributes("-topmost", True)
        except Exception:
            pass

    def close(self) -> None:
        if not getattr(self, "_win", None):
            return
        try:
            self._win.grab_release()
        except Exception:
            pass
        try:
            self._win.destroy()
        except Exception:
            pass
        self._win = None
        # Restore anchor window topmost state.
        if self.anchor_window is not None and self._anchor_prev_topmost is not None:
            try:
                self.anchor_window.attributes("-topmost", bool(self._anchor_prev_topmost))
            except Exception:
                pass

    def hide(self) -> None:
        """Temporarily hide the overlay (e.g. while a modal dialog is shown)."""
        if not getattr(self, "_win", None):
            return
        try:
            self._win.withdraw()
        except Exception:
            pass

    def show(self) -> None:
        """Show the overlay again after hide()."""
        if not getattr(self, "_win", None):
            return
        try:
            self._win.deiconify()
            self._win.lift()
            self._win.attributes("-topmost", True)
        except Exception:
            pass

def _center_toplevel(
    win: tk.Toplevel,
    root: tk.Tk,
    anchor_window: Optional[tk.Misc] = None,
    y_offset: int = 0,
    margin: int = 20,
) -> None:
    """Center a toplevel on screen or relative to an anchor window (clamped to desktop)."""
    win.update_idletasks()
    w = int(win.winfo_reqwidth())
    h = int(win.winfo_reqheight())

    # Virtual desktop bounds (handles multi-monitor + negative origins on Windows).
    try:
        vx = int(root.winfo_vrootx())
        vy = int(root.winfo_vrooty())
        vw = int(root.winfo_vrootwidth())
        vh = int(root.winfo_vrootheight())
    except Exception:
        vx = 0
        vy = 0
        vw = int(win.winfo_screenwidth())
        vh = int(win.winfo_screenheight())

    def _primary_screen_size() -> tuple[int, int]:
        try:
            import ctypes
            user32 = ctypes.windll.user32  # pyright: ignore[reportAttributeAccessIssue]
            sw = int(user32.GetSystemMetrics(0))
            sh = int(user32.GetSystemMetrics(1))
            if sw > 0 and sh > 0:
                return sw, sh
        except Exception:
            pass
        return int(win.winfo_screenwidth()), int(win.winfo_screenheight())

    x: int
    y: int
    if anchor_window is not None:
        try:
            anchor_window.update_idletasks()
        except Exception:
            pass
        try:
            # rootx/rooty are always screen coords even for nested widgets.
            ax = int(anchor_window.winfo_rootx())
            ay = int(anchor_window.winfo_rooty())
            aw = int(anchor_window.winfo_width()) or int(anchor_window.winfo_reqwidth())
            ah = int(anchor_window.winfo_height()) or int(anchor_window.winfo_reqheight())
            cx = ax + aw / 2
            cy = ay + ah / 2
            x = int(cx - w / 2)
            y = int(cy - h / 2) + int(y_offset)
        except Exception:
            sw, sh = _primary_screen_size()
            x = int((sw - w) / 2)
            y = int((sh - h) / 2) + int(y_offset)
    else:
        sw, sh = _primary_screen_size()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2) + int(y_offset)

    x = max(vx + margin, min(x, vx + vw - margin - w))
    y = max(vy + margin, min(y, vy + vh - margin - h))
    win.geometry(f"{w}x{h}+{x}+{y}")

# ---------------------- Startup Splash Control ----------------------
# The app keeps a single "startup splash" overlay alive while background initialization runs.
# Some workflows must show Tk dialogs; while those are visible we hide the splash temporarily.
_STARTUP_OVERLAY: Optional[LoadingOverlay] = None
_STARTUP_HIDE_COUNT: int = 0


def set_startup_overlay(overlay: Optional[LoadingOverlay]) -> None:
    """
    Register the current startup overlay.
    When set to None, the hidden-counter is reset as well.
    """
    global _STARTUP_OVERLAY, _STARTUP_HIDE_COUNT
    _STARTUP_OVERLAY = overlay
    if overlay is None:
        _STARTUP_HIDE_COUNT = 0


def get_startup_overlay() -> Optional[LoadingOverlay]:
    return _STARTUP_OVERLAY


def hide_startup_overlay() -> None:
    """Re-entrant hide: nested calls are reference-counted."""
    global _STARTUP_HIDE_COUNT
    ov = _STARTUP_OVERLAY
    if ov is None:
        return
    _STARTUP_HIDE_COUNT += 1
    try:
        dispatch_to_tk(ov.root, ov.hide)
    except Exception:
        pass


def show_startup_overlay() -> None:
    """Re-entrant show: only shows once the hide counter returns to 0."""
    global _STARTUP_HIDE_COUNT
    ov = _STARTUP_OVERLAY
    if ov is None:
        return

    _STARTUP_HIDE_COUNT = max(0, _STARTUP_HIDE_COUNT - 1)
    if _STARTUP_HIDE_COUNT != 0:
        return
    try:
        dispatch_to_tk(ov.root, ov.show)
    except Exception:
        pass
