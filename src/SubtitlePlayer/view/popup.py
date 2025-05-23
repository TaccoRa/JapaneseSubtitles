import tkinter as tk
from tkinter import font as tkFont
from model.config_manager import ConfigManager

class CopyPopup:
    
    def __init__(self, root: tk.Tk, config: ConfigManager) -> None:
        self.root = root
        self.config = config

        self._popup: tk.Toplevel | None = None
        self._close_job: str | None = None
        self._pinned = False
        self.root.bind("<Destroy>", lambda e: self._cancel_close())

        self.close_delay = self.config.get("POPUP_CLOSE_TIMER")
        # bg_color = self.config.get("POPUP_BG_COLOR", "white")
        # font_name = self.config.get("POPUP_FONT", "Arial")
        # font_size = self.config.get("POPUP_FONT_SIZE", 14)

    def open_copy_popup(self, subtitle_text = None) -> None:
        if self._popup:
            self._cancel_close()
            self._popup.destroy()
            self._popup = None

        if not subtitle_text:
            return
        
        popup = tk.Toplevel(self.root)
        self._popup = popup
        popup.overrideredirect(True)
        popup.configure(bg="white")
        popup.attributes("-topmost", True)
        self._pinned  = False

        font = tkFont.Font(family="Arial", size=14, weight="bold")

        label = tk.Label(popup,text=subtitle_text, font=font,justify="center",
                        anchor="center",padx=8, pady=4, bg="white")
        label.pack()

        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery()
        popup.geometry(f"+{x}+{y-label.winfo_reqheight()-5}")
        self._close_job = popup.after(self.close_delay, self._close)

        popup.bind("<Enter>", lambda e: self._cancel_close())
        popup.bind("<Leave>", lambda e: self._restart_close() if not self._pinned else None)
        popup.bind("<Button-3>", lambda e: self._pin(popup))
        popup.bind("<Destroy>", lambda e: setattr(self, "_popup", None))


    def _close(self) -> None:
        """Destroy the popup immediately."""
        if self._popup:
            self._popup.destroy()
        self._popup = None
        self._close_job = None

    def _cancel_close(self) -> None:
        """Cancel the pending close timer, if any."""
        if self._popup and self._close_job:
            try:
                self._popup.after_cancel(self._close_job)
            except tk.TclError:
                pass
        self._close_job = None

    def _restart_close(self) -> None:
        """Restart the auto-close timer."""
        self._cancel_close()
        if self._popup:
            self._close_job = self._popup.after(self.close_delay, self._close)

    def _pin(self, popup: tk.Toplevel) -> None:
        self._cancel_close()
        self._pinned = True
        popup.overrideredirect(False)
        popup.lift()