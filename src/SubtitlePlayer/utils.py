import tkinter as tk

def make_draggable(drag_handle: tk.Widget,target: tk.Toplevel,sync_windows: list[tk.Toplevel] = None, on_release=None):

    drag_state = {}

    def start_drag(event):
        drag_state['start_x'] = event.x_root
        drag_state['start_y'] = event.y_root

    def do_drag(event):
        dx = event.x_root - drag_state.get('start_x', event.x_root)
        dy = event.y_root - drag_state.get('start_y', event.y_root)
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

        drag_state['start_x'] = event.x_root
        drag_state['start_y'] = event.y_root

    def end_drag(event):
        if on_release:
            on_release(target.winfo_x(), target.winfo_y(),
                       target.winfo_width(), target.winfo_height())


    drag_handle.bind("<ButtonPress-1>", start_drag)
    drag_handle.bind("<B1-Motion>", do_drag)
    drag_handle.bind("<ButtonRelease-1>", end_drag)


def parse_time_value(time: str) -> float:
    if not time:
        return ""
    time = str(time).replace("s","").replace(" ","").replace(":","").replace(",", ".")
    if time.isdigit():
        z = time.zfill(7)
        h, m, s, ds = int(z[:-5]), int(z[-5:-3]), int(z[-3:-1]), int(z[-1:])
        print(f"Parsed time: {h:02d}:{m:02d}:{s:02d}.{ds}")
        m += s // 60;  s %= 60
        h += m // 60;  m %= 60
    return float(h * 3600 + m * 60 + s + ds / 10)
   
def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{m:02d}:{s:02d}"
    else:
        return f"{m:02d}:{s:02d}"
             
# def reformat_time_entry(entry: tk.Entry, parse_func, as_seconds=False) -> None:
#     text = entry.get()
#     secs = parse_func(text)
#     if as_seconds:
#         formatted = f"{secs:.1f} s"
#     else:
#         formatted = format_time(secs)
#     entry.delete(0, tk.END)
#     entry.insert(0, formatted)
