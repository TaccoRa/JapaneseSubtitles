class SubtitleOverlayUI:

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
                self.sub_window_bottom_anchor = new_y + target.winfo_height()
        drag_handle.bind("<ButtonPress-1>", start_drag)
        drag_handle.bind("<B1-Motion>", do_drag)

    def subtitle_hover_enter(self, event) -> None:
        self.sub_window.attributes("-transparentcolor", "")
        self.control_window.attributes("-topmost", True)
    def subtitle_hover_leave(self, event) -> None:
        self.sub_window.attributes("-transparentcolor", "grey")
        self.control_window.attributes("-topmost", False)
