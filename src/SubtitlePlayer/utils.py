import tkinter as tk

def make_draggable(drag_handle: tk.Widget, target: tk.Toplevel, sync_windows: list[tk.Toplevel] = None, on_drag=None) -> None:
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

        if on_drag:
            on_drag(new_y + target.winfo_height())
            
    drag_handle.bind("<ButtonPress-1>", start_drag)
    drag_handle.bind("<B1-Motion>", do_drag)


def parse_time_value(time: str, default_skip: float) -> float:
    time = str(time).strip().replace(",", ".")
    if not time:
        return None
    if time.endswith("s"):
        time = time[:-1].strip()
    if ":" in time:
        parts = time.split(":")
        if len(parts) == 2:
            try:
                minutes = int(parts[0])
                sec_str = parts[1]
                if len(sec_str) == 1:
                    seconds = int(sec_str) * 10
                else:
                    seconds = int(sec_str)
                return minutes * 60 + seconds
            except ValueError:
                return default_skip
        else:
            return default_skip
    else:
        if time.isdigit():
            return float(time) if len(time) <= 2 else int(time[:-2]) * 60 + int(time[-2:])
        try:
            return float(time)
        except ValueError:
            return default_skip
            
def reformat_time_entry(entry: tk.Entry, parse_func, as_seconds=False) -> None:
    text = entry.get()
    secs = parse_func(text)
    if as_seconds:
        formatted = f"{secs:.1f} s"
    else:
        formatted = format_time(secs)
    entry.delete(0, tk.END)
    entry.insert(0, formatted)

@staticmethod
def format_time(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"