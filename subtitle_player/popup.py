import tkinter as tk
from tkinter import font as tkFont
from config_manager import ConfigManager

class CopyPopup:
    
    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.close_timer = self.config.get('CLOSE_TIMER')

    def open_copy_popup(self, event: tk.Event) -> None:
        if hasattr(self, "copy_popup") and self.copy_popup is not None:
            try:
                if self.copy_popup.close_timer is not None:
                    self.copy_popup.after_cancel(self.copy_popup.close_timer)
            except Exception:
                pass
            self.copy_popup.destroy()
            self.copy_popup = None
    
        popup = tk.Toplevel(self.root)
        self.copy_popup = popup
        popup.overrideredirect(True)
        popup.configure(bg="white")
        popup.geometry("+0+0")
    
        copy_font = tkFont.Font(family="Arial", size=14, weight="bold")
        lines = self.last_subtitle_text.splitlines() or [""]
        num_lines = len(lines) + 1
        line_height = copy_font.metrics("linespace")
        req_height = line_height * num_lines
        req_width = max(copy_font.measure(line) for line in lines) if lines else 100
    
        text_widget = tk.Text(popup, wrap="none", font=copy_font, borderwidth=0,
                              highlightthickness=0, padx=0, pady=0, bg="white")
        text_widget.insert("1.0", self.last_subtitle_text)
        text_widget.config(state="disabled")
        text_widget.place(x=0, y=0, width=req_width, height=req_height)
        popup.geometry(f"{req_width}x{req_height}+0+0")
    
        popup.close_timer = popup.after(
            self.close_timer, lambda: (popup.destroy(), setattr(self, "copy_popup", None)))

        def on_enter(e):
            if popup.close_timer is not None:
                popup.after_cancel(popup.close_timer)
                popup.close_timer = None
        def on_leave(e):
            if popup.close_timer is None:
                popup.close_timer = popup.after(
                    self.close_timer, lambda: (popup.destroy(), setattr(self, "copy_popup", None)))

        popup.bind("<Enter>", on_enter)
        popup.bind("<Leave>", on_leave)
        popup.bind("<Destroy>", lambda e: setattr(self, "copy_popup", None))
