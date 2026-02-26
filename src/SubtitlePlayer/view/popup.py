"""
Copy popup window shown on right-click.

Displays subtitle text and provides a simple context menu for mouse-only copy.
"""

import tkinter as tk
from tkinter import font as tkFont

class CopyPopup:
    
    def __init__(self, root: tk.Tk, config) -> None:
        self.root = root
        self.config = config

        self._popup: tk.Toplevel | None = None
        self._close_job: str | None = None
        self._pinned = False
        self._menu_open = False
        self._entry_widget: tk.Text | None = None
        self._on_add_anki = None
        self.root.bind("<Destroy>", lambda e: self._cancel_close())

        self.bg_color = self.config.get("POPUP_BG_COLOR")
        self.font_name = self.config.get("POPUP_FONT")
        self.font_color = self.config.get("POPUP_FONT_COLOR")
        self.font_size = self.config.get("POPUP_FONT_SIZE")
        self.close_delay = self.config.get("POPUP_CLOSE_TIMER")

    def open_copy_popup(self, subtitle_text = None) -> None:
        if self._popup: #if already popup, close it and make a new one
            self._cancel_close()
            self._popup.destroy()
            self._popup = None
        
        popup = tk.Toplevel(self.root)
        self._popup = popup
        self._menu_open = False
        self._entry_widget = None
        popup.overrideredirect(True)
        # popup.configure(bg=self.bg_color)
        popup.attributes("-topmost", True)

        #calulate size of popup based on text
        font = tkFont.Font(family=self.font_name, size=self.font_size, weight="bold")
        lines = [l for l in subtitle_text.splitlines() if l.strip()]
        pixel_widths = [font.measure(line) for line in lines]
        text_width = max(pixel_widths)
        line_height = font.metrics("linespace")
        text_height = line_height * len(lines)
        pad_x, pad_y = 10,5
        total_width  = text_width  + 2 * pad_x
        total_height = text_height + 2 * pad_y

        entry = tk.Text(popup, font=font, wrap="word",padx=8, pady=4,
                        bg=self.bg_color,fg= self.font_color,
                        cursor="xterm", height=len(lines))
        entry.insert("1.0", subtitle_text)
        entry.tag_configure("center", justify="center")
        entry.tag_add("center", "1.0", "end")
        entry.config(state="disabled")
        entry.pack()
        self._entry_widget = entry

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
                return
            if not callable(self._on_add_anki):
                return
            def _run():
                try:
                    self._on_add_anki(selected_text=selected, subtitle_text=subtitle_text or "")
                except Exception:
                    pass
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

        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery() - total_height - 20
        popup.geometry(f"{total_width}x{total_height}+{x}+{y}")
        self.ensure_on_top()
        
        self._pinned  = False
        popup.bind("<Enter>", lambda e: self._cancel_close())
        popup.bind("<Leave>", lambda e: self._on_popup_leave())
        popup.bind("<Destroy>", lambda e: self._on_popup_destroy())

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
            popup.attributes("-topmost", True)
            popup.lift()
        except Exception:
            pass

    def set_busy_cursor(self, cursor: str) -> None:
        popup = getattr(self, "_popup", None)
        if not popup:
            return
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

    def _close(self) -> None:
        if self._popup: self._popup.destroy()
        self._popup = None
        self._entry_widget = None
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
        if self._pinned or self._menu_open:
            return
        self._restart_close()

    def _pin(self, popup: tk.Toplevel) -> None:
        self._cancel_close()
        self._pinned = True
        popup.overrideredirect(False)
        popup.lift()

    def _on_popup_destroy(self) -> None:
        self._popup = None
        self._entry_widget = None
