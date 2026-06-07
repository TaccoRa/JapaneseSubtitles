"""
Copy popup window shown on right-click.

Displays subtitle text and provides a simple context menu for mouse-only copy.
"""

import tkinter as tk
from tkinter import font as tkFont
from utils import make_draggable, get_monitor_rects, make_nonactivating_tool_window, show_window_no_activate

class CopyPopup:
    
    def __init__(self, root: tk.Tk, config) -> None:
        self.root = root
        self.config = config

        self._popup: tk.Toplevel | None = None
        self._close_job: str | None = None
        self._pinned = False
        self._menu_open = False
        self._entry_widget: tk.Text | None = None
        self._drag_grip: tk.Label | None = None
        self._on_add_anki = None
        self._dragging = False
        self.root.bind("<Destroy>", lambda e: self._cancel_close())

        self.bg_color = self.config.get("POPUP_BG_COLOR")
        self.font_name = self.config.get("POPUP_FONT")
        self.font_color = self.config.get("POPUP_FONT_COLOR")
        self.font_size = self.config.get("POPUP_FONT_SIZE")
        self.close_delay = int(self.config.get("POPUP_CLOSE_TIMER") or 1000)

    def open_copy_popup(self, subtitle_text = None) -> None:
        if self._popup: #if already popup, close it and make a new one
            self._cancel_close()
            self._popup.destroy()
            self._popup = None
        
        popup = tk.Toplevel(self.root)
        self._popup = popup
        self._menu_open = False
        self._dragging = False
        self._entry_widget = None
        self._drag_grip = None
        popup.withdraw()
        popup.overrideredirect(True)
        # popup.configure(bg=self.bg_color)
        popup.attributes("-topmost", True)
        make_nonactivating_tool_window(popup)

        #calulate size of popup based on text
        font = tkFont.Font(family=self.font_name, size=self.font_size, weight="bold")
        lines = [l for l in (subtitle_text or "").splitlines() if l.strip()]
        pixel_widths = [font.measure(line) for line in lines]
        text_width = max(pixel_widths) if pixel_widths else font.measure((subtitle_text or "").strip() or " ")
        line_height = font.metrics("linespace")
        line_count = max(1, len(lines))
        text_height = line_height * line_count
        pad_x, pad_y = 10,5
        total_width  = text_width  + 2 * pad_x
        total_height = text_height + 2 * pad_y

        entry = tk.Text(popup, font=font, wrap="word",padx=8, pady=4,
                        bg=self.bg_color,fg= self.font_color,
                        cursor="xterm", height=line_count)
        entry.insert("1.0", subtitle_text)
        entry.tag_configure("center", justify="center")
        entry.tag_add("center", "1.0", "end")
        entry.config(state="disabled")
        entry.pack(fill="both", expand=True)
        self._entry_widget = entry

        # Drag grip (no title bar) to move popup without interfering with text selection.
        drag_grip = tk.Label(
            popup,
            text=":::",
            font=(self.font_name, max(8, int(self.font_size * 0.45))),
            fg=self.font_color,
            bg=self.bg_color,
            cursor="fleur",
            bd=0,
            padx=2,
            pady=0,
        )
        drag_grip.place(relx=1.0, rely=1.0, x=-2, y=-2, anchor="se")
        self._drag_grip = drag_grip
        make_draggable(drag_grip, popup, on_release=self._on_popup_drag_end)
        drag_grip.bind("<ButtonPress-1>", self._on_popup_drag_start, add="+")
        drag_grip.bind("<ButtonRelease-1>", self._on_popup_drag_end, add="+")

        # Right-click context menu to copy selected text using only the mouse.
        menu = tk.Menu(popup, tearoff=0)

        def _get_selection() -> str:
            try:
                return (entry.get("sel.first", "sel.last") or "").strip()
            except tk.TclError:
                return ""

        def _copy_selection():
            selected = _get_selection()
            if not selected:
                return
            try:
                popup.clipboard_clear()
                popup.clipboard_append(selected)
            except Exception:
                pass

        def _add_selection_to_anki():
            selected = _get_selection()
            if not selected:
                print("Add Selection To Anki: no text selected.")
                return
            if not callable(self._on_add_anki):
                print("Add Selection To Anki: callback not bound.")
                return
            def _run():
                try:
                    self._on_add_anki(selected_text=selected, subtitle_text=subtitle_text or "")
                except Exception as e:
                    print(f"Add Selection To Anki failed: {e}")
            # Run on next tick so the context menu can close and cursor change is visible.
            try:
                popup.after(1, _run)
            except Exception:
                _run()

        def _copy_all():
            try:
                popup.clipboard_clear()
                popup.clipboard_append(subtitle_text or "")
            except Exception:
                pass

        menu.add_command(label="Copy", command=_copy_selection)
        menu.add_command(label="Copy All", command=_copy_all)
        menu.add_command(label="Add Selection To Anki", command=_add_selection_to_anki)
        menu.add_separator()
        menu.add_command(label="Pin", command=lambda: self._pin(popup))

        def _show_menu(event):
            self._menu_open = True
            self._cancel_close()
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass
                self._menu_open = False
                if not self._pinned:
                    self._restart_close()
            return "break"

        entry.bind("<Button-3>", _show_menu)

        try:
            pointer_x = int(self.root.winfo_pointerx())
            pointer_y = int(self.root.winfo_pointery())
        except Exception:
            pointer_x, pointer_y = 0, 0

        monitor_rect = None
        try:
            rects = list(get_monitor_rects(self.root) or [])
            for rx, ry, rw, rh in rects:
                if rx <= pointer_x < rx + rw and ry <= pointer_y < ry + rh:
                    monitor_rect = (rx, ry, rw, rh)
                    break
            if monitor_rect is None and rects:
                monitor_rect = min(
                    rects,
                    key=lambda r: (abs(pointer_x - (r[0] + (r[2] // 2))) + abs(pointer_y - (r[1] + (r[3] // 2)))),
                )
        except Exception:
            monitor_rect = None

        if monitor_rect is not None:
            screen_x, screen_y, screen_w, screen_h = monitor_rect
        else:
            try:
                screen_x, screen_y = 0, 0
                screen_w = int(popup.winfo_screenwidth() or 1920)
                screen_h = int(popup.winfo_screenheight() or 1080)
            except Exception:
                screen_x, screen_y, screen_w, screen_h = 0, 0, 1920, 1080

        max_w = max(240, int(screen_w * 0.90))
        max_h = max(120, int(screen_h * 0.60))
        total_width = min(max_w, int(total_width))
        total_height = min(max_h, int(total_height))

        x = int(pointer_x - (total_width // 2))
        y = int(pointer_y - total_height - 16)
        max_x = screen_x + max(0, screen_w - total_width)
        max_y = screen_y + max(0, screen_h - total_height)
        x = max(screen_x, min(x, max_x))
        y = max(screen_y, min(y, max_y))
        popup.geometry(f"{total_width}x{total_height}+{x}+{y}")
        show_window_no_activate(popup)
        
        self._pinned  = False
        popup.bind("<Enter>", lambda e: self._cancel_close())
        popup.bind("<Leave>", lambda e: self._on_popup_leave())
        popup.bind("<Destroy>", lambda e: self._on_popup_destroy())
        self._restart_close()

    def bind_add_to_anki(self, callback) -> None:
        self._on_add_anki = callback

    def ensure_on_top(self) -> None:
        """
        Keep popup above the other always-on-top windows in this app.

        Note: other windows (overlay/control) also set -topmost, so z-order depends on who is lifted last.
        """
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        try:
            show_window_no_activate(popup)
        except Exception:
            pass

    def set_busy_cursor(self, cursor: str) -> None:
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        if cursor:
            self._cancel_close()
        try:
            popup.configure(cursor=cursor)
        except Exception:
            pass
        entry = getattr(self, "_entry_widget", None)
        if entry is not None:
            try:
                entry.configure(cursor=cursor or "xterm")
            except Exception:
                pass

    def mark_anki_success(self, duration_ms: int = 1200) -> None:
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        try:
            if not popup.winfo_exists():
                return
        except Exception:
            return

        success_bg = "#168a3a"
        self._cancel_close()
        try:
            popup.configure(bg=success_bg, cursor="")
        except Exception:
            pass
        entry = getattr(self, "_entry_widget", None)
        if entry is not None:
            try:
                entry.configure(bg=success_bg, cursor="xterm")
            except Exception:
                pass
        grip = getattr(self, "_drag_grip", None)
        if grip is not None:
            try:
                grip.configure(bg=success_bg)
            except Exception:
                pass
        try:
            self._close_job = popup.after(max(300, int(duration_ms)), self._close)
        except Exception:
            self._restart_close()

    def _close(self) -> None:
        if self._popup: self._popup.destroy()
        self._popup = None
        self._entry_widget = None
        self._drag_grip = None
        self._close_job = None

    def _cancel_close(self) -> None:
        if self._popup and self._close_job:
            self._popup.after_cancel(self._close_job)
        self._close_job = None

    def _restart_close(self) -> None: #restart close timer
        self._cancel_close()
        if self._popup:
            self._close_job = self._popup.after(self.close_delay, self._close)

    def _on_popup_leave(self) -> None:
        if self._pinned or self._menu_open or self._dragging:
            return
        self._restart_close()

    def _on_popup_drag_start(self, event=None) -> None:
        self._dragging = True
        self._cancel_close()

    def _on_popup_drag_end(self, *args, **kwargs) -> None:
        self._dragging = False
        if not self._pinned and not self._menu_open:
            self._restart_close()

    def _pin(self, popup: tk.Toplevel) -> None:
        self._cancel_close()
        self._pinned = True
        popup.overrideredirect(False)
        popup.lift()

    def _on_popup_destroy(self) -> None:
        self._popup = None
        self._entry_widget = None
        self._drag_grip = None
        self._dragging = False
