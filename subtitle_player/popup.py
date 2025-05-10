import tkinter as tk
from tkinter import font as tkFont
from config_manager import ConfigManager

class CopyPopup:
    
    def __init__(self, root: tk.Tk, config: ConfigManager) -> None:
        self.root = root
        self.config = config
        self.copy_popup: tk.Toplevel | None = None

    def open_copy_popup(self, event: tk.Event) -> None:
        if self.copy_popup:
            try:
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
        lines = subtitle_text.splitlines() or [""]
        line_height = copy_font.metrics("linespace")
        req_height = line_height * (len(lines) + 1)
        req_width = max(copy_font.measure(line) for line in lines) if lines else 100

        text_widget = tk.Text(popup, wrap="none", font=copy_font, borderwidth=0,
                              highlightthickness=0, padx=0, pady=0, bg="white")
        text_widget.insert("1.0", subtitle_text)
        text_widget.config(state="disabled")
        text_widget.place(x=0, y=0, width=req_width, height=req_height)
        popup.geometry(f"{req_width}x{req_height}+0+0")
    
        def close_popup():
            popup.destroy()
            self.copy_popup = None

        popup.close_timer = popup.after(self.config.get("CLOSE_TIMER"), close_popup)

        def on_enter(_):
            try:
                popup.after_cancel(popup.close_timer)
            except Exception:
                pass
        def on_leave(_):
            popup.close_timer = popup.after(self.config.get("CLOSE_TIMER"), close_popup)

        popup.bind("<Enter>", on_enter)
        popup.bind("<Leave>", on_leave)
        popup.bind("<Destroy>", lambda e: setattr(self, "copy_popup", None))
