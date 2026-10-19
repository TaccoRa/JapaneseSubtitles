import time
import bisect

class PlaybackController:
    def __init__(self, controller=None):
        self.controller = controller

    def set_controller(self, controller):
        self.controller = controller

    def _now(self) -> float:
        return time.perf_counter()

    def _browser(self):
        browser = getattr(self.controller, "browser_sync", None)
        return browser if browser is not None and browser.following else None

    def source_time(self, value):
        browser = self._browser()
        return browser.source_time(value) if browser else value - self.controller.get_offset_value()

    def video_time(self, value):
        browser = self._browser()
        return browser.video_time(value) if browser else value + self.controller.get_offset_value()

    def _advance_playing_time_to_now(self, **_):
        return 0.0


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
        # BrowserSyncController owns observations and the only timed update loop.
        self.controller._update_loop_job = None


    def schedule_update(self):
        return


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

        browser = self._browser()
        if browser:
            try:
                browser.command("seek", time=float(t))
            except (ConnectionError, ValueError) as exc:
                browser._set_status(str(exc))
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

    def toggle_play(self, event_time=None):
        if self.controller._shutting_down:
            return
        self.set_playing(not bool(self.controller.playing), event_time=event_time)


    def set_playing(self, playing, event_time=None):
        browser = self._browser()
        if not browser:
            self.controller.playing = False
            self._set_play_button_state()
            return False
        if bool(self.controller.playing) == bool(playing):
            return False
        try:
            browser.command("play" if playing else "pause")
        except ConnectionError as exc:
            browser.report_error(str(exc))
            return False
        return True


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

    def change_fast_forward_speed(self, delta, *, persist=True):
        browser = self._browser()
        if browser and browser.snapshot:
            speed = self.controller._coerce_fast_forward_speed(browser.snapshot.rate + float(delta))
            try:
                browser.command("rate", rate=speed)
            except ConnectionError as exc:
                browser.report_error(str(exc))
            return speed
        return 1.0


    def seek_relative(self, delta, event_time=None):
        browser = self._browser()
        if browser and browser.snapshot:
            try:
                browser.seek_relative(float(delta))
            except ConnectionError as exc:
                browser.report_error(str(exc))
        else:
            self.set_current_time(float(self.controller.current_time or 0) + float(delta))
        self.controller._schedule_hide_controls()


    def go_forward(self, event_time=None):
        self.seek_relative(float(self.controller.settings._last_skip_value), event_time=event_time)


    def go_back(self, event_time=None):
        self.seek_relative(-float(self.controller.settings._last_skip_value), event_time=event_time)


    def jump_subtitle_segment(self, direction: str, event_time: float | None = None) -> None:
        browser = self._browser()
        if browser:
            if not browser.mapping:
                browser._set_status("Finding timing…")
                return
            starts = [x[0] for x in browser.mapping.video]
            current = float(self.controller.current_time)
            idx = bisect.bisect_right(starts, current + (.05 if direction == "next" else -.05))
            if direction == "prev":
                idx -= 1
            if 0 <= idx < len(starts):
                self.set_current_time(starts[idx])
            return
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
        sub_t = max(0.0, self.source_time(float(self.controller.current_time)))
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

        self.set_current_time(self.video_time(float(start_times[target_idx])))
        self.controller._schedule_hide_controls()

    def on_jump_sub_end(self, event=None, event_time: float | None = None):
        browser = self._browser()
        if browser:
            if not browser.mapping:
                return
            starts = [x[0] for x in browser.mapping.video]
            idx = browser.mapping.cue_at(float(self.controller.current_time))
            if idx is None:
                idx = bisect.bisect_right(starts, float(self.controller.current_time))
            if idx < len(starts):
                self.set_current_time(browser.mapping.video[idx][1] + float(self.controller.audio_padding) * .001)
            return
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
        sub_t = max(0.0, self.source_time(float(self.controller.current_time)))
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
                target_time = self.video_time(float(end_times[next_idx])) + padding_sec
                self.set_current_time(target_time)
                self.controller._schedule_hide_controls()
                return
            if synced:
                self._publish_current_time()
            return

        target_time = self.video_time(end_time) + padding_sec
        if target_time < float(self.controller.current_time or 0.0) - epsilon:
            if synced:
                self._publish_current_time()
            return

        self.set_current_time(target_time)
        self.controller._schedule_hide_controls()
