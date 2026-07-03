"""
Small shared helpers used across the UI/controller.

- Window dragging (make_draggable)
- Parsing and formatting time values
"""

import logging
import tkinter as tk

logger = logging.getLogger(__name__)


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


def get_window_root_hwnd(win: tk.Misc):
    return _get_windows_hwnd(win)


def get_foreground_root_hwnd() -> int | None:
    try:
        import ctypes
        import sys

        if not sys.platform.startswith("win"):
            return None
        user32 = ctypes.windll.user32
        foreground = int(user32.GetForegroundWindow())
        if not foreground:
            return 0
        try:
            return int(user32.GetAncestor(foreground, 2)) or foreground  # GA_ROOT
        except Exception:
            return foreground
    except Exception:
        return None


def get_window_screen_rect(win: tk.Misc):
    """
    Return full outer window bounds as (left, top, right, bottom).

    On Windows this includes the non-client titlebar/buttons, unlike Tk's
    winfo_root* geometry. Other platforms fall back to Tk client bounds.
    """
    try:
        import ctypes
        import sys
        from ctypes import wintypes

        if sys.platform.startswith("win"):
            hwnd = _get_windows_hwnd(win)
            if hwnd:
                rect = wintypes.RECT()
                if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return (
                        int(rect.left),
                        int(rect.top),
                        int(rect.right),
                        int(rect.bottom),
                    )
    except Exception:
        pass

    try:
        left = int(win.winfo_rootx())
        top = int(win.winfo_rooty())
        return (
            left,
            top,
            left + int(win.winfo_width()),
            top + int(win.winfo_height()),
        )
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
    try:
        import ctypes
        import sys

        if not sys.platform.startswith("win"):
            return False

        win.update_idletasks()

        hwnd = _get_windows_hwnd(win)
        if not hwnd:
            return False

        user32 = ctypes.windll.user32

        GWL_EXSTYLE = -20

        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_APPWINDOW = 0x00040000

        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

        style = int(get_style(hwnd, GWL_EXSTYLE))

        style |= WS_EX_NOACTIVATE
        style |= WS_EX_APPWINDOW

        set_style(hwnd, GWL_EXSTYLE, style)

        user32.SetWindowPos(
            hwnd,
            -1 if topmost else -2,
            0,
            0,
            0,
            0,
            SWP_NOMOVE
            | SWP_NOSIZE
            | SWP_NOACTIVATE
            | SWP_FRAMECHANGED,
        )

        return True

    except Exception as e:
        logger.debug("make_nonactivating_window failed: %s", e, exc_info=True)
        return False


def make_interactive_tool_window(win: tk.Toplevel, topmost: bool = False) -> bool:
    """Keep a normal owned window interactive, with standard titlebar buttons."""
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
        gwl_style = -16
        gwl_exstyle = -20
        ws_ex_toolwindow = 0x00000080
        ws_ex_appwindow = 0x00040000
        ws_caption = 0x00C00000
        ws_sysmenu = 0x00080000
        ws_thickframe = 0x00040000
        ws_minimizebox = 0x00020000
        ws_maximizebox = 0x00010000
        hwnd_topmost = -1
        hwnd_notopmost = -2
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_style(hwnd, gwl_style))
        style |= ws_caption | ws_sysmenu | ws_thickframe | ws_minimizebox | ws_maximizebox
        set_style(hwnd, gwl_style, style)
        exstyle = int(get_style(hwnd, gwl_exstyle))
        exstyle &= ~ws_ex_toolwindow
        exstyle &= ~ws_ex_appwindow
        set_style(hwnd, gwl_exstyle, exstyle)
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
    except Exception as e:
        logger.debug("make_interactive_tool_window failed: %s", e, exc_info=True)
        return False


def is_any_window_foreground(windows) -> bool | None:
    foreground = get_foreground_root_hwnd()
    if foreground is None:
        return None
    if not foreground:
        return False
    for win in windows or []:
        if win is None:
            continue
        if isinstance(win, int):
            hwnd = win
        else:
            hwnd = _get_windows_hwnd(win)
        if hwnd and int(hwnd) == int(foreground):
            return True
    return False


def set_window_topmost_no_activate(win: tk.Toplevel, topmost: bool) -> bool:
    """Change topmost state without asking Windows to activate the window."""
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
        hwnd_topmost = -1
        hwnd_notopmost = -2
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        user32.SetWindowPos(
            hwnd,
            hwnd_topmost if topmost else hwnd_notopmost,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_noactivate,
        )
        return True
    except Exception as e:
        logger.debug("set_window_topmost_no_activate failed: %s", e, exc_info=True)
        return False


def show_normal_window_no_activate(win: tk.Toplevel, topmost: bool = False) -> bool:
    """Show a normal interactive window without foreground activation on Windows."""
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
        sw_shownoactivate = 4
        hwnd_topmost = -1
        hwnd_top = 0
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        swp_showwindow = 0x0040

        make_interactive_tool_window(win, topmost=topmost)
        user32.ShowWindow(hwnd, sw_shownoactivate)
        user32.SetWindowPos(
            hwnd,
            hwnd_topmost if topmost else hwnd_top,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_noactivate | swp_showwindow,
        )
        return True
    except Exception as e:
        logger.debug("show_normal_window_no_activate failed: %s", e, exc_info=True)
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
