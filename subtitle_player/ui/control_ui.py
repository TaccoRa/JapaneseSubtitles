class ControlUI:
    def build_controls(self): ...

    def on_slider_press(self, event: tk.Event) -> None:
        self.slider_dragging = True
        self.force_update_entry()

    def on_slider_release(self, event: tk.Event) -> None:
        self.slider_dragging = False
        self.set_current_time(float(self.slider.get()))

    def on_slider_change(self, value: str) -> None:
        if self.slider_dragging:
            self.set_current_time(float(value))

    def update_time_displays(self) -> None:
        formatted = self.format_time(self.current_time)
        relx = config['RATIO'] + (1 - 2 * config['RATIO']) * (self.current_time / self.total_duration)
        self.time_overlay.config(text=formatted)
        self.time_overlay.place(in_=self.slider, relx=relx, rely=0.2)
        if not self.time_editing:
            self.play_time_var.set(formatted)
