"""
Small shared helpers used across the UI/controller.

- Window dragging (make_draggable)
- Parsing and formatting time values
"""

import tkinter as tk


def _get_windows_hwnd(win: tk.Misc):
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(win.winfo_id())
        try:
            root_hwnd = int(user32.GetAncestor(hwnd, 2))  # GA_ROOT
            if root_hwnd:
                hwnd = root_hwnd
        except Exception:
            parent_hwnd = int(user32.GetParent(hwnd))
            if parent_hwnd:
                hwnd = parent_hwnd
        return hwnd
    except Exception:
        return None


def make_nonactivating_tool_window(win: tk.Toplevel, topmost: bool = True) -> bool:
    """
    Mark a passive utility window so clicking/showing it does not steal foreground focus
    on Windows. This helps keep fullscreen video from revealing the taskbar.
    """
    try:
        import ctypes
        import sys

        if not sys.platform.startswith("win"):
            return False
        try:
            win.update_idletasks()
        except Exception:
            pass
        hwnd = _get_windows_hwnd(win)
        if not hwnd:
            return False

        user32 = ctypes.windll.user32
        gwl_exstyle = -20
        ws_ex_toolwindow = 0x00000080
        ws_ex_appwindow = 0x00040000
        ws_ex_noactivate = 0x08000000
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        hwnd_topmost = -1
        hwnd_notopmost = -2

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_style(hwnd, gwl_exstyle))
        style |= ws_ex_toolwindow | ws_ex_noactivate
        style &= ~ws_ex_appwindow
        set_style(hwnd, gwl_exstyle, style)
        user32.SetWindowPos(
            hwnd,
            hwnd_topmost if topmost else hwnd_notopmost,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_noactivate | swp_framechanged,
        )
        return True
    except Exception:
        return False


def make_nonactivating_window(win: tk.Toplevel, topmost: bool = True) -> bool:
    """
    Mark a normal window as no-activate while keeping its regular window chrome.
    Unlike make_nonactivating_tool_window, this preserves normal minimize behavior.
    """
    try:
        import ctypes
        import sys

        if not sys.platform.startswith("win"):
            return False
        try:
            win.update_idletasks()
        except Exception:
            pass
        hwnd = _get_windows_hwnd(win)
        if not hwnd:
            return False

        user32 = ctypes.windll.user32
        gwl_exstyle = -20
        ws_ex_noactivate = 0x08000000
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        hwnd_topmost = -1
        hwnd_notopmost = -2

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_style(hwnd, gwl_exstyle))
        style |= ws_ex_noactivate
        set_style(hwnd, gwl_exstyle, style)
        user32.SetWindowPos(
            hwnd,
            hwnd_topmost if topmost else hwnd_notopmost,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_noactivate | swp_framechanged,
        )
        return True
    except Exception:
        return False


def show_window_no_activate_minimizable(win: tk.Toplevel, topmost: bool = True) -> None:
    """Show a normal minimizable window without asking Windows to foreground this process."""
    if make_nonactivating_window(win, topmost=topmost):
        try:
            import ctypes

            try:
                win.deiconify()
            except Exception:
                pass
            hwnd = _get_windows_hwnd(win)
            if hwnd:
                user32 = ctypes.windll.user32
                hwnd_topmost = -1
                hwnd_top = 0
                swp_nosize = 0x0001
                swp_nomove = 0x0002
                swp_noactivate = 0x0010
                swp_showwindow = 0x0040
                user32.SetWindowPos(
                    hwnd,
                    hwnd_topmost if topmost else hwnd_top,
                    0,
                    0,
                    0,
                    0,
                    swp_nomove | swp_nosize | swp_noactivate | swp_showwindow,
                )
                return
        except Exception:
            pass
    try:
        win.deiconify()
        if topmost:
            win.attributes("-topmost", True)
        win.lift()
    except Exception:
        pass


def show_window_no_activate(win: tk.Toplevel, topmost: bool = True) -> None:
    """Show a passive window without asking Windows to foreground this process."""
    if make_nonactivating_tool_window(win, topmost=topmost):
        try:
            import ctypes

            try:
                win.deiconify()
            except Exception:
                pass
            hwnd = _get_windows_hwnd(win)
            if hwnd:
                user32 = ctypes.windll.user32
                hwnd_topmost = -1
                hwnd_top = 0
                swp_nosize = 0x0001
                swp_nomove = 0x0002
                swp_noactivate = 0x0010
                swp_showwindow = 0x0040
                user32.SetWindowPos(
                    hwnd,
                    hwnd_topmost if topmost else hwnd_top,
                    0,
                    0,
                    0,
                    0,
                    swp_nomove | swp_nosize | swp_noactivate | swp_showwindow,
                )
                return
        except Exception:
            pass
    try:
        win.deiconify()
        if topmost:
            win.attributes("-topmost", True)
        win.lift()
    except Exception:
        pass

def get_monitor_rects(root: tk.Tk | None = None):
    """
    Return a list of monitor rectangles as (x, y, w, h).
    On Windows, uses EnumDisplayMonitors; otherwise falls back to the primary screen.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG)]

        monitors = []

        def _callback(hMonitor, hdc, lprcMonitor, dwData):
            r = lprcMonitor.contents
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
            monitors.append((int(r.left), int(r.top), w, h))
            return 1

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                           ctypes.POINTER(RECT), wintypes.LPARAM)
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback_type(_callback), 0)
        if monitors:
            monitors.sort(key=lambda r: (r[0], r[1]))
            return monitors
    except Exception:
        pass

    # Fallback: use primary screen size
    try:
        if root is not None:
            sw = int(root.winfo_vrootwidth() or root.winfo_screenwidth())
            sh = int(root.winfo_vrootheight() or root.winfo_screenheight())
        else:
            sw, sh = 1920, 1080
    except Exception:
        sw, sh = 1920, 1080
    return [(0, 0, int(sw), int(sh))]

def make_draggable(drag_handle: tk.Widget,target: tk.Toplevel,sync_windows: list[tk.Toplevel] = None, on_release=None):

    drag_state = {}

    def start_drag(event):
        drag_state['start_x'] = event.x_root
        drag_state['start_y'] = event.y_root

    def do_drag(event):
        dx = event.x_root - drag_state.get('start_x', event.x_root)
        dy = event.y_root - drag_state.get('start_y', event.y_root)
        new_x = target.winfo_x() + dx
        new_y = target.winfo_y() + dy
        try:
            target.geometry(f"+{new_x}+{new_y}")
        except tk.TclError:
            return
        if sync_windows:
            for win in sync_windows:
                if win.winfo_exists():
                    try:
                        win.geometry(f"+{new_x}+{new_y}")
                    except tk.TclError:
                        pass

        drag_state['start_x'] = event.x_root
        drag_state['start_y'] = event.y_root

    def end_drag(event):
        if on_release:
            on_release(target.winfo_x(), target.winfo_y(),
                       target.winfo_width(), target.winfo_height())


    drag_handle.bind("<ButtonPress-1>", start_drag)
    drag_handle.bind("<B1-Motion>", do_drag)
    drag_handle.bind("<ButtonRelease-1>", end_drag)


def parse_time_value(time: str, last_subtitle = None) -> float:
    text = str(time or "").strip().lower()
    text = text.replace(" ", "").replace("s", "").replace(",", ".")
    if not text:
        return 0.0

    def _digits(s: str) -> str:
        return "".join(ch for ch in s if ch.isdigit())

    if ":" in text:
        parts = [p or "0" for p in text.split(":")]
        if len(parts) > 3:
            parts = parts[-3:]
        while len(parts) < 3:
            parts.insert(0, "0")
        h_s, m_s, s_s = parts
        h = int(_digits(h_s) or 0)
        m = int(_digits(m_s) or 0)
        if "." in s_s:
            sec_int, frac = s_s.split(".", 1)
        else:
            sec_int, frac = s_s, ""
        sec = int(_digits(sec_int) or 0)
        frac_digits = _digits(frac)
        frac_secs = float("0." + frac_digits) if frac_digits else 0.0
    else:
        if "." in text:
            int_part, frac = text.split(".", 1)
        else:
            int_part, frac = text, ""
        int_digits = _digits(int_part)
        if not int_digits:
            return 0.0
        if len(int_digits) <= 4:
            padded = int_digits.zfill(4)
            h = 0
            m = int(padded[:-2])
            sec = int(padded[-2:])
        else:
            h = int(int_digits[:-4] or 0)
            m = int(int_digits[-4:-2])
            sec = int(int_digits[-2:])
        frac_digits = _digits(frac)
        frac_secs = float("0." + frac_digits) if frac_digits else 0.0

    m += sec // 60
    sec %= 60
    h += m // 60
    m %= 60

    return h * 3600 + m * 60 + sec + frac_secs
   
def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{m:02d}:{s:02d}"
    else:
        return f"{m:02d}:{s:02d}"

