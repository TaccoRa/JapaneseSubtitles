import tkinter as tk
from tkinter import font as tkFont

class CopyPopup:
    
    def __init__(self, root: tk.Tk, config) -> None:
        self.root = root
        self.config = config

        self._popup: tk.Toplevel | None = None
        self._close_job: str | None = None
        self._pinned = False
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

        x = self.root.winfo_pointerx()
        y = self.root.winfo_pointery() - total_height - 20
        popup.geometry(f"{total_width}x{total_height}+{x}+{y}")
        
        self._pinned  = False
        popup.bind("<Enter>", lambda e: self._cancel_close())
        popup.bind("<Leave>", lambda e: self._restart_close() if not self._pinned else None)
        popup.bind("<Button-3>", lambda e: self._pin(popup))
        popup.bind("<Destroy>", lambda e: setattr(self, "_popup", None))


    def _close(self) -> None:
        if self._popup: self._popup.destroy()
        self._popup = None
        self._close_job = None

    def _cancel_close(self) -> None:
        if self._popup and self._close_job:
            self._popup.after_cancel(self._close_job)
        self._close_job = None

    def _restart_close(self) -> None: #restart close timer
        self._cancel_close()
        if self._popup:
            self._close_job = self._popup.after(self.close_delay, self._close)

    def _pin(self, popup: tk.Toplevel) -> None:
        self._cancel_close()
        self._pinned = True
        popup.overrideredirect(False)
        popup.lift()