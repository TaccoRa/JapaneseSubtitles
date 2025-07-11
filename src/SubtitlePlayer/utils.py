import tkinter as tk
from model.config_manager import ConfigManager

def make_draggable(drag_handle: tk.Widget, target: tk.Toplevel, sync_windows: list[tk.Toplevel] = None, on_drag=None, save_position: tuple[ConfigManager, str, str] = None) -> None:
    def start_drag(event):
        drag_handle._drag_start_x = event.x_root
        drag_handle._drag_start_y = event.y_root
    def do_drag(event):
        if not target.winfo_exists():
            drag_handle.unbind("<B1-Motion>")
            return
        dx = event.x_root - drag_handle._drag_start_x
        dy = event.y_root - drag_handle._drag_start_y
        new_x = target.winfo_x() + dx
        new_y = target.winfo_y() + dy
        try:
            target.geometry(f"+{new_x}+{new_y}")
        except tk.TclError:
            return
        
        if sync_windows:
            for win in sync_windows:
                if win.winfo_exists():
                    try:
                        win.geometry(f"+{new_x}+{new_y}")
                    except tk.TclError:
                        pass

        drag_handle._drag_start_x = event.x_root
        drag_handle._drag_start_y = event.y_root
        bottom_y = new_y + target.winfo_height()

        if on_drag:
            on_drag(bottom_y)

        if save_position:
            cfg, key_x, key_y = save_position
            try:
                cfg.set(key_x, new_x)
                cfg.set(key_y, new_y)
            except Exception:
                pass

    drag_handle.bind("<ButtonPress-1>", start_drag)
    drag_handle.bind("<B1-Motion>", do_drag)


def parse_time_value(time: str, default_skip: float) -> float:
    time = str(time).strip().rstrip("s").replace(",", ".")
    if not time:
        return default_skip  

    if ":" in time:
        parts = list(map(int, time.split(":")))
        h, m, s = ([0] * (3 - len(parts)) + parts)
    elif time.isdigit():
        z = time.zfill(6)
        h, m, s = int(z[:-4]), int(z[-4:-2]), int(z[-2:])
    # decimal seconds
    else:
        try:
            return float(time)
        except ValueError:
            return default_skip
    m += s // 60;  s %= 60
    h += m // 60;  m %= 60
    return float(h * 3600 + m * 60 + s)
   
def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{m:02d}:{s:02d}"
    else:
        return f"{m:02d}:{s:02d}"
             
def reformat_time_entry(entry: tk.Entry, parse_func, as_seconds=False) -> None:
    text = entry.get()
    secs = parse_func(text)
    if as_seconds:
        formatted = f"{secs:.1f} s"
    else:
        formatted = format_time(secs)
    entry.delete(0, tk.END)
    entry.insert(0, formatted)
