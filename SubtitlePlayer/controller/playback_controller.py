import time
import bisect


class PlaybackController:
    def __init__(self, controller=None):
        self.controller = controller

    def set_controller(self, controller):
        self.controller = controller

    def _now(self) -> float:
        return time.perf_counter()

    def _advance_playing_time_to_now(
        self,
        *,
        now: float | None = None,
        allow_end_toggle: bool = True,
        update_display: bool = True,
        update_slider: bool = True,
    ) -> float:
        if not self.controller.playing:
            return 0.0

        if now is None:
            now = self._now()
        try:
            last_update = float(self.controller.last_update)
        except Exception:
            last_update = now

        delta = now - last_update
        self.controller.last_update = now
        if abs(delta) > 0.0:
            if bool(getattr(self.controller, "fast_forward_enabled", True)):
                try:
                    delta *= float(getattr(self.controller, "fast_forward_speed", 1.0) or 1.0)
                except Exception:
                    pass
            self.set_current_time(
                float(self.controller.current_time or 0.0) + delta,
                allow_end_toggle=allow_end_toggle,
                update_display=update_display,
                update_slider=update_slider,
            )
        return delta

    def _event_time_or_now(self, event_time: float | None = None) -> float:
        return self._now() if event_time is None else float(event_time)

    def _sync_playing_time_to_event(
        self,
        event_time: float | None = None,
        *,
        update_display: bool = True,
        update_slider: bool = True,
    ) -> bool:
        if not self.controller.playing:
            return False
        delta = self._advance_playing_time_to_now(
            now=self._event_time_or_now(event_time),
            allow_end_toggle=False,
            update_display=update_display,
            update_slider=update_slider,
        )
        return abs(delta) > 0.0

    def update_loop(self):
        if self.controller._shutting_down:
            self.controller._update_loop_job = None
            return

        self._advance_playing_time_to_now()

        self.schedule_update()

    def schedule_update(self):
        if self.controller._shutting_down:
            return
        root = self.controller.overlay.root
        if self.controller._update_loop_job is not None:
            try:
                root.after_cancel(self.controller._update_loop_job)
            except Exception:
                pass
            self.controller._update_loop_job = None
        self.controller._update_loop_job = self.controller.overlay.root.after(
            self.controller.update_interval_ms, self.update_loop
        )

    def _set_slider_value(self, value: float) -> bool:
        slider = self.controller.settings.slider
        if not slider.winfo_exists():
            return False
        try:
            if abs(float(slider.get()) - float(value)) < 0.0005:
                return True
        except Exception:
            pass
        slider.set(value)
        return True

    def _publish_current_time(
        self,
        *,
        update_display: bool = True,
        update_slider: bool = True,
    ) -> None:
        if self.controller.slider_dragging:
            return
        if update_slider and not self._set_slider_value(float(self.controller.current_time or 0.0)):
            return
        if update_display:
            self.controller.update_time_and_subtitle_displays()

    def set_current_time(
        self,
        t: float,
        *,
        allow_end_toggle: bool = True,
        update_display: bool = True,
        update_slider: bool = True,
    ):
        if self.controller._shutting_down:
            return
        if t is None:
            return

        offset = self.controller.get_offset_value()
        # print(offset, "test")##testing
        t = max(0, min(t, self.controller.total_duration + offset))

        if allow_end_toggle and t - offset >= self.controller.total_duration and self.controller.playing:
            self.toggle_play()

        self.controller.current_time = t
        self._publish_current_time(
            update_display=update_display,
            update_slider=update_slider,
        )

    def toggle_play(self, event_time: float | None = None):
        if self.controller._shutting_down:
            return
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        was_playing = bool(self.controller.playing)
        now = self._now() if event_time is None else float(event_time)
        if was_playing:
            self._advance_playing_time_to_now(
                now=now,
                allow_end_toggle=False,
                update_display=False,
            )

        self.controller.playing = not was_playing

        if self.controller.playing:
            self.controller.last_update = now
            self.schedule_update()
        else:
            if self.controller._update_loop_job:
                try:
                    self.controller.overlay.root.after_cancel(self.controller._update_loop_job)
                except Exception:
                    pass
                self.controller._update_loop_job = None
            if self.controller.subtitle_timeout_job:
                self.controller.overlay.root.after_cancel(self.controller.subtitle_timeout_job)
                self.controller.subtitle_timeout_job = None
            if self.controller.subtitle_deleted and self.controller.last_subtitle_text:
                self.controller.subtitle_deleted = False

        self._set_play_button_state()
        self.controller.update_time_and_subtitle_displays()
        self.controller._schedule_hide_controls()

    def _set_play_button_state(self) -> None:
        try:
            fast_speed = getattr(self.controller, "fast_forward_speed", None)
            if fast_speed is None:
                fast_speed = 1.0
            self.controller.settings.set_fast_forward_state(
                bool(getattr(self.controller, "fast_forward_enabled", True)),
                float(fast_speed),
            )
        except Exception:
            pass
        if self.controller.playing:
            self.controller.settings.play_pause_btn.config(text="Stop", bg="red", activebackground="red")
        else:
            self.controller.settings.play_pause_btn.config(text="Play", bg="green", activebackground="green")

    def toggle_fast_forward(self, event_time: float | None = None) -> None:
        if self.controller._shutting_down:
            return
        self._set_play_button_state()

    def change_fast_forward_speed(self, delta: float, *, persist: bool = True) -> float:
        if self.controller._shutting_down:
            return float(getattr(self.controller, "fast_forward_speed", 1.0) or 1.0)
        if not bool(getattr(self.controller, "fast_forward_enabled", True)):
            self._set_play_button_state()
            return 1.0
        try:
            delta = float(delta)
        except Exception:
            delta = 0.0
        if self.controller.playing:
            now = self._now()
            self._advance_playing_time_to_now(
                now=now,
                allow_end_toggle=False,
                update_display=False,
            )
            self.controller.last_update = now
        current = getattr(self.controller, "fast_forward_speed", None)
        if current is None:
            current = 1.5
        self.controller.fast_forward_speed = self.controller._coerce_fast_forward_speed(float(current) + delta)
        if persist:
            try:
                self.controller.config.set("FAST_FORWARD_SPEED", self.controller.fast_forward_speed)
            except Exception:
                pass
        self._set_play_button_state()
        self.controller.update_time_and_subtitle_displays()
        self.controller._schedule_hide_controls()
        return float(self.controller.fast_forward_speed)

    def seek_relative(self, delta: float, event_time: float | None = None) -> None:
        self._sync_playing_time_to_event(
            event_time,
            update_display=False,
            update_slider=False,
        )
        self.set_current_time(float(self.controller.current_time or 0.0) + float(delta or 0.0))
        self.controller._schedule_hide_controls()

    def go_forward(self, event_time: float | None = None):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        if self.controller._skip_buttons_use_subtitle_segments():
            self.jump_subtitle_segment("next", event_time=event_time)
            return

        skip = self.controller.settings._last_skip_value
        max_time = self.controller.total_duration + self.controller.get_offset_value()
        if self.controller.current_time <= max_time:
            self.seek_relative(skip, event_time=event_time)

    def go_back(self, event_time: float | None = None):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        if self.controller._skip_buttons_use_subtitle_segments():
            self.jump_subtitle_segment("prev", event_time=event_time)
            return

        skip = self.controller.settings._last_skip_value
        if self.controller.current_time >= 0:
            self.seek_relative(-skip, event_time=event_time)

    def jump_subtitle_segment(self, direction: str, event_time: float | None = None) -> None:
        start_times = self.controller._get_display_start_times()
        if not start_times:
            self._sync_playing_time_to_event(event_time)
            return

        synced = self._sync_playing_time_to_event(
            event_time,
            update_display=False,
            update_slider=False,
        )
        offset = self.controller.get_offset_value()
        sub_t = max(0.0, float(self.controller.current_time) - offset)
        epsilon = 0.05

        if direction == "prev":
            target_idx = bisect.bisect_right(start_times, sub_t - epsilon) - 1
        elif direction == "next":
            target_idx = bisect.bisect_right(start_times, sub_t + epsilon)
        else:
            if synced:
                self._publish_current_time()
            return

        if target_idx < 0 or target_idx >= len(start_times):
            if synced:
                self._publish_current_time()
            return

        self.set_current_time(float(start_times[target_idx]) + offset)
        self.controller._schedule_hide_controls()

    def on_jump_sub_end(self, event=None, event_time: float | None = None):
        start_times = self.controller._get_display_start_times()
        if not start_times:
            self._sync_playing_time_to_event(event_time)
            return

        synced = self._sync_playing_time_to_event(
            event_time,
            update_display=False,
            update_slider=False,
        )
        offset = self.controller.get_offset_value()
        sub_t = max(0.0, float(self.controller.current_time) - offset)
        epsilon = 0.05
        padding_sec = float(self.controller.audio_padding)*0.001

        idx = bisect.bisect_right(start_times, sub_t + epsilon) - 1
        try:
            end_times = self.controller._get_display_end_times()
        except Exception:
            end_times = getattr(self.controller.sub_manager, "display_end_times", [])
        if idx < 0 or idx >= len(start_times) or idx >= len(end_times):
            if synced:
                self._publish_current_time()
            return

        end_time = float(end_times[idx])
        if sub_t > end_time + epsilon:
            next_idx = bisect.bisect_right(start_times, sub_t + epsilon)
            if next_idx < len(start_times) and next_idx < len(end_times):
                target_time = float(end_times[next_idx]) + padding_sec + offset
                self.set_current_time(target_time)
                self.controller._schedule_hide_controls()
                return
            if synced:
                self._publish_current_time()
            return

        target_time = end_time + padding_sec + offset
        if target_time < float(self.controller.current_time or 0.0) - epsilon:
            if synced:
                self._publish_current_time()
            return

        self.set_current_time(target_time)
        self.controller._schedule_hide_controls()
