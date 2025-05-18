import tkinter as tk
from tkinter import font as tkFont
from model.config_manager import ConfigManager

class CopyPopup:
    
    def __init__(self, root: tk.Tk, config: ConfigManager) -> None:
        self.root = root
        self.config = config
        self.copy_popup: tk.Toplevel | None = None
        self._close_timer: str | None = None
        self._is_unlocked = False
        self.close_time = self.config.get("POPUP_CLOSE_TIMER")
        # bg_color = self.config.get("POPUP_BG_COLOR", "white")
        # font_name = self.config.get("POPUP_FONT", "Arial")
        # font_size = self.config.get("POPUP_FONT_SIZE", 14)

    def open_copy_popup(self, subtitle_text = None) -> None:
        if self.copy_popup:
            self._cancel_close_timer()
            self.copy_popup.destroy()
            self.copy_popup = None
    
        popup = tk.Toplevel(self.root)
        self.copy_popup = popup
        popup.overrideredirect(True)
        popup.configure(bg="white")
        popup.attributes("-topmost", True)
        self._is_unlocked = False

        copy_font = tkFont.Font(family="Arial", size=14, weight="bold")
        lines = (subtitle_text or "").splitlines()
        line_height = copy_font.metrics("linespace")
        req_height = line_height * (len(lines) + 1)
        req_width = max(copy_font.measure(line) for line in lines) if lines else 100

        text_widget = tk.Text(popup, wrap="none", font=copy_font, borderwidth=0,
                              highlightthickness=0, padx=0, pady=0, bg="white")
        text_widget.insert("1.0", subtitle_text)
        text_widget.config(state="disabled")
        text_widget.place(x=0, y=0, width=req_width, height=req_height)

        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        popup.geometry(f"{req_width}x{req_height}+{x}+{y-req_height-5}")

        def close_popup():
            popup.destroy()
            self.copy_popup = None
            self._close_timer = None

        self._close_timer = popup.after(self.close_time, close_popup)
    
        def on_enter(_):
            self._cancel_close_timer()

        def on_leave(_):
            if self._is_unlocked:
                return
            self._cancel_close_timer()
            self._close_timer = popup.after(self.close_time, close_popup)

        def on_right_click(event):
            self._cancel_close_timer()
            self._is_unlocked = True
            # Make window have borders and movable
            popup.overrideredirect(False)
            popup.lift()

        popup.bind("<Enter>", on_enter)
        popup.bind("<Leave>", on_leave)
        popup.bind("<Destroy>", lambda e: setattr(self, "copy_popup", None))
        popup.bind("<Button-3>", on_right_click)

    def _cancel_close_timer(self):
        if self._close_timer and self.copy_popup:
            try:
                self.copy_popup.after_cancel(self._close_timer)
            except tk.TclError:
                pass
            self._close_timer = None