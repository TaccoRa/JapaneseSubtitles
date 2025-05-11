import tkinter as tk

def make_draggable(self, drag_handle: tk.Widget, target: tk.Toplevel, sync_windows: list[tk.Toplevel] = None) -> None:
    def start_drag(event):
        drag_handle._drag_start_x = event.x_root
        drag_handle._drag_start_y = event.y_root
    def do_drag(event):
        dx = event.x_root - drag_handle._drag_start_x
        dy = event.y_root - drag_handle._drag_start_y
        new_x = target.winfo_x() + dx
        new_y = target.winfo_y() + dy
        target.geometry(f"+{new_x}+{new_y}")

        if sync_windows:
            for win in sync_windows:
                win.geometry(f"+{new_x}+{new_y}")

        drag_handle._drag_start_x = event.x_root
        drag_handle._drag_start_y = event.y_root

        if target == self.sub_window:
            self.bottom_anchor = new_y + target.winfo_height()
    drag_handle.bind("<ButtonPress-1>", start_drag)
    drag_handle.bind("<B1-Motion>", do_drag)

def parse_time_value(text: str, default_skip: float) -> float:
    text = text.strip()
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            try:
                minutes = int(parts[0])
                seconds = int(parts[1])
                return minutes * 60 + seconds
            except ValueError:
                return default_skip
        else:
            return default_skip
    else:
        if text.isdigit():
            return float(text) if len(text) <= 2 else int(text[:-2]) * 60 + int(text[-2:])
        try:
            return float(text)
        except ValueError:
            return default_skip
            
def reformat_time_entry(entry: tk.Entry, parse_func) -> None:
    text = entry.get()
    secs = parse_func(text)
    minutes, seconds = divmod(int(secs), 60)
    formatted = f"{minutes:02d}:{seconds:02d}"
    entry.delete(0, tk.END)
    entry.insert(0, formatted)

def format_skip_entry(skip_entry: tk.Entry, parse_func) -> None:
    reformat_time_entry(skip_entry, parse_func)
