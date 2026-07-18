"""
Small shared helpers used across the UI/controller.

- Window dragging (make_draggable)
- Parsing and formatting time values
"""

import logging
import queue
import re
import sys
import threading
import time
import tkinter as tk

logger = logging.getLogger(__name__)


class TkMainThreadDispatcher:
    """Queue callbacks from workers and execute them only on Tk's owner thread."""

    def __init__(self, root: tk.Misc, poll_ms: int = 10) -> None:
        self.root = root
        self.poll_ms = max(1, int(poll_ms or 10))
        self.owner_thread_id = threading.get_ident()
        self._queue: queue.Queue[tuple] = queue.Queue()
        self._closed = False
        self._job = None
        setattr(root, "_tk_main_thread_dispatcher", self)
        self._schedule_next()

    def submit(self, callback, *args, delay_ms: int = 0, **kwargs):
        if self._closed or not callable(callback):
            return None
        delay_ms = max(0, int(delay_ms or 0))
        if threading.get_ident() == self.owner_thread_id:
            if delay_ms:
                return self.root.after(
                    delay_ms,
                    lambda: self._invoke(callback, args, kwargs),
                )
            self._invoke(callback, args, kwargs)
            return None
        self._queue.put((callback, args, kwargs, delay_ms))
        return None

    def call(self, callback, *args, **kwargs):
        """Run a callback on Tk's owner thread and return its result to a worker."""
        if self._closed or not callable(callback):
            raise RuntimeError("Tk dispatcher is closed")
        if threading.get_ident() == self.owner_thread_id:
            return callback(*args, **kwargs)

        completed = threading.Event()
        outcome = {}

        def _run() -> None:
            try:
                outcome["result"] = callback(*args, **kwargs)
            except BaseException:
                outcome["error"] = sys.exc_info()
            finally:
                completed.set()

        self._queue.put((_run, (), {}, 0))
        while not completed.wait(0.05):
            if self._closed:
                raise RuntimeError("Tk dispatcher closed before the callback ran")

        error = outcome.get("error")
        if error:
            _exc_type, exc, tb = error
            raise exc.with_traceback(tb)
        return outcome.get("result")

    def _invoke(self, callback, args, kwargs) -> None:
        try:
            callback(*args, **kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException:
            handler = getattr(self.root, "report_callback_exception", None)
            if callable(handler):
                handler(*sys.exc_info())
            else:
                logger.exception("Unhandled dispatched Tk callback exception")

    def _drain(self) -> None:
        self._job = None
        if self._closed:
            return
        try:
            for _ in range(200):
                try:
                    callback, args, kwargs, delay_ms = self._queue.get_nowait()
                except queue.Empty:
                    break
                if delay_ms:
                    self.root.after(
                        delay_ms,
                        lambda cb=callback, a=args, kw=kwargs: self._invoke(cb, a, kw),
                    )
                else:
                    self._invoke(callback, args, kwargs)
        finally:
            self._schedule_next()

    def _schedule_next(self) -> None:
        if self._closed:
            return
        try:
            self._job = self.root.after(self.poll_ms, self._drain)
        except Exception:
            self._job = None

    def close(self) -> None:
        self._closed = True
        job = self._job
        self._job = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


def _tk_dispatcher_for(widget: tk.Misc):
    dispatcher = getattr(widget, "_tk_main_thread_dispatcher", None)
    if dispatcher is not None:
        return dispatcher
    try:
        root = widget._root()
    except Exception:
        root = None
    return getattr(root, "_tk_main_thread_dispatcher", None) if root is not None else None


def dispatch_to_tk(root: tk.Misc, callback, *args, delay_ms: int = 0, **kwargs):
    dispatcher = _tk_dispatcher_for(root)
    if dispatcher is not None:
        return dispatcher.submit(callback, *args, delay_ms=delay_ms, **kwargs)
    if threading.current_thread() is threading.main_thread():
        if int(delay_ms or 0) > 0:
            return root.after(
                int(delay_ms),
                lambda: callback(*args, **kwargs),
            )
        return callback(*args, **kwargs)
    logger.error("Dropped worker UI callback because no Tk dispatcher is installed")
    return None


def dispatch_to_tk_sync(root: tk.Misc, callback, *args, **kwargs):
    dispatcher = _tk_dispatcher_for(root)
    if dispatcher is not None:
        return dispatcher.call(callback, *args, **kwargs)
    if threading.current_thread() is threading.main_thread():
        return callback(*args, **kwargs)
    raise RuntimeError("Cannot synchronously call Tk from a worker without a dispatcher")


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


def _split_window_title_filters(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip().lower() for part in re.split(r"[,;|]", text) if part.strip()]


def find_window_by_title(title_filters: str) -> int | None:
    """Return the first visible top-level Windows hwnd whose title contains any filter."""
    filters = _split_window_title_filters(title_filters)
    if not filters:
        return None
    try:
        import ctypes
        import sys
        from ctypes import wintypes

        if not sys.platform.startswith("win"):
            return None

        user32 = ctypes.windll.user32
        matches: list[tuple[int, str]] = []

        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def _callback(hwnd, _lparam):
            try:
                hwnd = int(hwnd)
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = int(user32.GetWindowTextLengthW(hwnd))
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = str(buffer.value or "").strip()
                if not title:
                    return True
                lowered = title.lower()
                if any(part in lowered for part in filters):
                    matches.append((hwnd, title))
                    return False
            except Exception:
                return True
            return True

        user32.EnumWindows(enum_proc_type(_callback), 0)
        if matches:
            return int(matches[0][0])
    except Exception:
        logger.debug("Failed to find window by title filters: %s", title_filters, exc_info=True)
    return None


def list_visible_windows(exclude_hwnds=None) -> list[dict]:
    """Return visible Windows top-level windows with stable matching metadata."""
    try:
        import ctypes
        import os
        from ctypes import wintypes

        if not sys.platform.startswith("win"):
            return []

        excluded = {int(hwnd) for hwnd in (exclude_hwnds or ()) if hwnd}
        current_pid = int(os.getpid())
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        process_query_limited_information = 0x1000
        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        windows: list[dict] = []

        def _process_name(hwnd: int) -> str:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not pid.value:
                return ""
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
            if not handle:
                return ""
            try:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                    return os.path.basename(str(buffer.value or ""))
            finally:
                kernel32.CloseHandle(handle)
            return ""

        def _callback(hwnd, _lparam):
            try:
                hwnd = int(hwnd)
                if hwnd in excluded or not user32.IsWindowVisible(hwnd):
                    return True
                window_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
                if int(window_pid.value or 0) == current_pid:
                    return True
                length = int(user32.GetWindowTextLengthW(hwnd))
                if length <= 0:
                    return True
                title_buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title_buffer, length + 1)
                title = str(title_buffer.value or "").strip()
                if not title:
                    return True
                class_buffer = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
                windows.append(
                    {
                        "hwnd": hwnd,
                        "title": title,
                        "process": _process_name(hwnd),
                        "class_name": str(class_buffer.value or ""),
                    }
                )
            except Exception:
                logger.debug("Failed to inspect a visible window", exc_info=True)
            return True

        user32.EnumWindows(enum_proc_type(_callback), 0)
        windows.sort(key=lambda item: (str(item.get("process") or "").casefold(), str(item.get("title") or "").casefold()))
        return windows
    except Exception:
        logger.debug("Failed to enumerate visible windows", exc_info=True)
        return []


def find_window_target(target, exclude_hwnds=None) -> dict | None:
    """Resolve a saved voice-playback target against currently visible windows."""
    if not isinstance(target, dict):
        return None
    title_text = str(target.get("title_filter") or target.get("title") or "").strip().casefold()
    title_filters = [title_text] if title_text else []
    process = str(target.get("process") or "").strip().casefold()
    for window in list_visible_windows(exclude_hwnds=exclude_hwnds):
        if process and str(window.get("process") or "").casefold() != process:
            continue
        if title_filters:
            title = str(window.get("title") or "").casefold()
            if not any(part in title for part in title_filters):
                continue
        if title_filters or process:
            return window
    return None


def focus_windows_hwnd(hwnd: int) -> bool:
    """Try to bring a Windows hwnd to foreground so an external hotkey reaches it."""
    try:
        import ctypes
        import sys
        if not sys.platform.startswith("win") or not hwnd:
            return False

        user32 = ctypes.windll.user32
        hwnd = int(hwnd)
        sw_restore = 9
        user32.ShowWindow(hwnd, sw_restore)
        user32.SetForegroundWindow(hwnd)
        try:
            root_hwnd = int(user32.GetAncestor(hwnd, 2)) or hwnd
        except Exception:
            root_hwnd = hwnd
        deadline = time.perf_counter() + 0.06
        while True:
            foreground = int(user32.GetForegroundWindow())
            try:
                foreground = int(user32.GetAncestor(foreground, 2)) or foreground
            except Exception:
                pass
            if int(foreground) == int(root_hwnd):
                return True
            if time.perf_counter() >= deadline:
                return False
            time.sleep(0.005)
    except Exception:
        logger.debug("Failed to focus hwnd %s", hwnd, exc_info=True)
        return False


def focus_window_by_title(title_filters: str) -> bool:
    hwnd = find_window_by_title(title_filters)
    if not hwnd:
        logger.warning("No external capture target window matched title filters: %s", title_filters)
        return False
    return focus_windows_hwnd(hwnd)


def send_global_hotkey(hotkey: str) -> bool:
    """Send a hotkey to the current foreground window using pyautogui."""
    text = str(hotkey or "").strip().lower()
    if not text:
        return False
    keys = [part.strip() for part in re.split(r"\s*\+\s*", text) if part.strip()]
    if not keys:
        return False
    aliases = {
        "control": "ctrl",
        "ctl": "ctrl",
        "cmd": "win",
        "command": "win",
        "windows": "win",
        "period": ".",
        "dot": ".",
        "comma": ",",
        "return": "enter",
        "esc": "escape",
        "arrowleft": "left",
        "arrowright": "right",
        "spacebar": "space",
    }
    keys = [aliases.get(key, key) for key in keys]
    try:
        import pyautogui

        old_pause = getattr(pyautogui, "PAUSE", 0.0)
        pyautogui.PAUSE = 0.0
        try:
            pyautogui.hotkey(*keys)
        finally:
            pyautogui.PAUSE = old_pause
        return True
    except Exception:
        logger.debug("Failed to send global hotkey %s", hotkey, exc_info=True)
        return False


def send_hotkey_to_window(
    target,
    hotkey: str,
    *,
    exclude_hwnds=None,
    restore_foreground: bool = True,
    repeat_count: int = 1,
    repeat_interval_ms: int = 40,
    before_send=None,
) -> dict:
    """Focus a matched window, send one or more hotkeys, then restore foreground focus."""
    window = None
    if isinstance(target, dict):
        try:
            if int(target.get("hwnd") or 0) > 0:
                window = dict(target)
        except Exception:
            window = None
    if window is None:
        window = find_window_target(target, exclude_hwnds=exclude_hwnds)
    if not window:
        return {"ok": False, "reason": "target_not_found"}
    hwnd = int(window.get("hwnd") or 0)
    previous = get_foreground_root_hwnd()
    if not hwnd or not focus_windows_hwnd(hwnd):
        return {"ok": False, "reason": "target_focus_failed", "window": window}
    try:
        repeat_count = max(1, min(20, int(repeat_count)))
    except Exception:
        repeat_count = 1
    try:
        repeat_interval_ms = max(0, min(2000, int(repeat_interval_ms)))
    except Exception:
        repeat_interval_ms = 40
    sent_count = 0
    try:
        if callable(before_send):
            try:
                before_send(dict(window))
            except Exception:
                logger.debug("External hotkey pre-send callback failed", exc_info=True)
                return {
                    "ok": False,
                    "reason": "before_send_failed",
                    "window": window,
                    "sent_count": 0,
                }
        for index in range(repeat_count):
            if not send_global_hotkey(hotkey):
                break
            sent_count += 1
            if index + 1 < repeat_count:
                time.sleep(repeat_interval_ms / 1000.0)
    finally:
        if restore_foreground and previous and int(previous) != hwnd:
            try:
                focus_windows_hwnd(int(previous))
            except Exception:
                logger.debug("Failed to restore the previous foreground window", exc_info=True)
    if sent_count != repeat_count:
        return {
            "ok": False,
            "reason": "hotkey_send_failed",
            "window": window,
            "sent_count": sent_count,
        }
    return {"ok": True, "reason": "sent", "window": window, "sent_count": sent_count}


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
        dispatcher = getattr(root, "_tk_main_thread_dispatcher", None) if root is not None else None
        root_is_thread_safe = (
            root is not None
            and (
                (
                    dispatcher is not None
                    and threading.get_ident() == getattr(dispatcher, "owner_thread_id", None)
                )
                or (
                    dispatcher is None
                    and threading.current_thread() is threading.main_thread()
                )
            )
        )
        if root_is_thread_safe:
            sw = int(root.winfo_vrootwidth() or root.winfo_screenwidth())
            sh = int(root.winfo_vrootheight() or root.winfo_screenheight())
        else:
            sw, sh = 1920, 1080
    except Exception:
        sw, sh = 1920, 1080
    return [(0, 0, int(sw), int(sh))]


def move_pointer_to_monitor_bottom(root: tk.Tk | None = None) -> bool:
    """Move the pointer to the bottom edge of its current monitor."""
    pointer_x = pointer_y = None
    try:
        import ctypes
        import sys
        from ctypes import wintypes

        if sys.platform.startswith("win"):
            point = wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
                pointer_x, pointer_y = int(point.x), int(point.y)
    except Exception:
        pass

    if pointer_x is None or pointer_y is None:
        try:
            pointer_x = int(root.winfo_pointerx()) if root is not None else 0
            pointer_y = int(root.winfo_pointery()) if root is not None else 0
        except Exception:
            pointer_x = pointer_y = 0

    monitors = get_monitor_rects(root)
    monitor = next(
        (
            rect
            for rect in monitors
            if rect[0] <= pointer_x < rect[0] + rect[2]
            and rect[1] <= pointer_y < rect[1] + rect[3]
        ),
        None,
    )
    if monitor is None:
        monitor = min(
            monitors,
            key=lambda rect: (
                max(rect[0] - pointer_x, 0, pointer_x - (rect[0] + rect[2] - 1)) ** 2
                + max(rect[1] - pointer_y, 0, pointer_y - (rect[1] + rect[3] - 1)) ** 2
            ),
        )

    left, top, width, height = monitor
    target_x = max(left, min(pointer_x, left + max(1, width) - 1))
    target_y = top + max(1, height) - 1
    try:
        import ctypes
        import sys

        if sys.platform.startswith("win"):
            return bool(ctypes.windll.user32.SetCursorPos(int(target_x), int(target_y)))
    except Exception:
        pass

    try:
        import pyautogui

        pyautogui.moveTo(int(target_x), int(target_y), duration=0)
        return True
    except Exception:
        logger.debug("Failed to move pointer to monitor bottom", exc_info=True)
        return False

def make_draggable(drag_handle: tk.Widget,target: tk.Toplevel,sync_windows: list[tk.Toplevel] = None, on_release=None):

    drag_state = {}

    def pointer_position(event):
        try:
            return int(drag_handle.winfo_pointerx()), int(drag_handle.winfo_pointery())
        except (tk.TclError, ValueError, TypeError):
            return int(event.x_root), int(event.y_root)

    def position_window(win, x, y):
        # Tk uses "-500" as an offset from the right/bottom edge. Prefixing the
        # signed value ("+-500") addresses an absolute coordinate on a monitor
        # positioned left of or above the primary monitor.
        win.geometry(f"+{int(x)}+{int(y)}")

    def start_drag(event):
        try:
            if int(getattr(event, "state", 0) or 0) & 0x0004:
                drag_state["active"] = False
                return "break"
        except Exception:
            pass
        drag_state["active"] = True
        try:
            target.update_idletasks()
        except tk.TclError:
            drag_state["active"] = False
            return None
        pointer_x, pointer_y = pointer_position(event)
        drag_state["pointer_x"] = pointer_x
        drag_state["pointer_y"] = pointer_y
        drag_state["window_x"] = int(target.winfo_x())
        drag_state["window_y"] = int(target.winfo_y())

    def do_drag(event):
        if not drag_state.get("active"):
            return None
        try:
            if int(getattr(event, "state", 0) or 0) & 0x0004:
                drag_state["active"] = False
                return "break"
        except Exception:
            pass
        pointer_x, pointer_y = pointer_position(event)
        dx = pointer_x - drag_state.get("pointer_x", pointer_x)
        dy = pointer_y - drag_state.get("pointer_y", pointer_y)
        new_x = drag_state.get("window_x", int(target.winfo_x())) + dx
        new_y = drag_state.get("window_y", int(target.winfo_y())) + dy
        try:
            position_window(target, new_x, new_y)
        except tk.TclError:
            return
        if sync_windows:
            for win in sync_windows:
                if win.winfo_exists():
                    try:
                        position_window(win, new_x, new_y)
                    except tk.TclError:
                        pass

    def end_drag(event):
        was_active = bool(drag_state.pop("active", False))
        if not was_active:
            return None
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
