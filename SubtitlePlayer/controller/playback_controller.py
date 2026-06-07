import time
import bisect
import tkinter as tk


class PlaybackController:
    def __init__(self, controller=None):
        self.controller = controller

    def set_controller(self, controller):
        self.controller = controller

    def update_loop(self):
        if self.controller.playing:
            now = time.time()
            delta = now - self.controller.last_update
            self.controller.last_update = now
            self.set_current_time(self.controller.current_time + delta)

        self.schedule_update()

    def schedule_update(self):
        self.controller.overlay.root.after(self.controller.update_interval_ms, self.update_loop)

    def set_current_time(self, t: float):
        if t is None:
            return

        offset = self.controller.get_offset_value()
        t = max(0, min(t, self.controller.total_duration + offset))

        if t - offset >= self.controller.total_duration and self.controller.playing:
            self.toggle_play()

        self.controller.current_time = t
        if not self.controller.slider_dragging:
            self.controller.settings.slider.set(t)
            self.controller.update_time_and_subtitle_displays()

    def toggle_play(self):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        self.controller.playing = not self.controller.playing

        if self.controller.playing:
            self.controller.settings.play_pause_btn.config(text="Stop", bg="red", activebackground="red")
            self.controller.last_update = time.time()
            self.schedule_update()
        else:
            self.controller.settings.play_pause_btn.config(text="Play", bg="green", activebackground="green")
            if self.controller.subtitle_timeout_job:
                self.controller.overlay.root.after_cancel(self.controller.subtitle_timeout_job)
                self.controller.subtitle_timeout_job = None
            if self.controller.subtitle_deleted and self.controller.last_subtitle_text:
                self.controller.subtitle_deleted = False

        if self.controller.video_click:
            self.controller.simulate_video_click()

        self.controller.update_time_and_subtitle_displays()
        self.controller._schedule_hide_controls()

    def go_forward(self):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        if self.controller._skip_buttons_use_subtitle_segments():
            self.jump_subtitle_segment("next")
            return

        skip = self.controller.settings._last_skip_value
        max_time = self.controller.total_duration + self.controller.get_offset_value()
        if self.controller.current_time <= max_time:
            self.set_current_time(self.controller.current_time + skip)
            self.controller._schedule_hide_controls()

    def go_back(self):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        if self.controller._skip_buttons_use_subtitle_segments():
            self.jump_subtitle_segment("prev")
            return

        skip = self.controller.settings._last_skip_value
        if self.controller.current_time >= 0:
            self.set_current_time(self.controller.current_time - skip)
            self.controller._schedule_hide_controls()

    def jump_subtitle_segment(self, direction: str) -> None:
        start_times = self.controller._get_display_start_times()
        if not start_times:
            return

        offset = self.controller.get_offset_value()
        sub_t = max(0.0, float(self.controller.current_time) - offset)
        epsilon = 0.05

        if direction == "prev":
            target_idx = bisect.bisect_right(start_times, sub_t - epsilon) - 1
        elif direction == "next":
            target_idx = bisect.bisect_right(start_times, sub_t + epsilon)
        else:
            return

        if target_idx < 0 or target_idx >= len(start_times):
            return

        self.set_current_time(float(start_times[target_idx]) + offset)
        self.controller._schedule_hide_controls()

    def on_jump_sub_end(self, event=None):
        start_times = self.controller._get_display_start_times()
        if not start_times:
            return

        offset = self.controller.get_offset_value()
        sub_t = max(0.0, float(self.controller.current_time) - offset)
        epsilon = 0.05

        idx = bisect.bisect_right(start_times, sub_t + epsilon) - 1
        if idx < 0 or idx >= len(self.controller.sub_manager.subtitles):
            return

        sub = self.controller.sub_manager.subtitles[idx]
        target_time = sub.end.total_seconds() + float(self.controller.audio_padding or 0.0) + offset

        self.set_current_time(target_time)
        self.controller._schedule_hide_controls()
