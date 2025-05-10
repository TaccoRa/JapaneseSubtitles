import time
import os
import re
from config_manager import ConfigManager

class SubtitleController:

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.update_interval_ms = self.config.get('UPDATE_INTERVAL_MS')

    def toggle_play(self) -> None:
        self.playing = not self.playing
        if self.playing:
            self.play_pause_button.config(text="Stop", bg="red", font = "bold", activebackground="red")
            self.last_update = time.time()
        else:
            self.play_pause_button.config(text="Play", bg="green", font = "bold", activebackground="green")
            if self.subtitle_timeout_job is not None:
                self.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            if self.user_hidden and self.last_subtitle_text:
                self.user_hidden = False
                self._render_subtitle(self.last_subtitle_text)
        
        if self.video_click: self.simulate_video_click()
        self.update_time_displays()
        self.schedule_hide_controls()

    def go_forward(self) -> None:
        self.set_current_time(self.current_time + self.get_skip_value())
        self.schedule_hide_controls()
    def go_back(self) -> None:
        self.set_current_time(self.current_time - self.get_skip_value())
        self.schedule_hide_controls()

    def increment_episode(self) -> None:
        current_ep = int(self.episode_var.get())
        self.episode_var.set(str(current_ep + 1))
        self.on_episode_change()
        if self.playing:
            self.toggle_play()
        self.control_window.attributes("-topmost", True)
    def decline_episode(self) -> None:
        current_ep = int(self.episode_var.get())
        self.episode_var.set(str(current_ep - 1))
        self.on_episode_change()
        if self.playing:
            self.toggle_play()
        self.control_window.attributes("-topmost", True)

    def on_episode_change(self) -> None:
        try:
            episode = int(self.episode_var.get().strip())
        except ValueError:
            return

        match = re.search(r'S(\d+)', self.srt_path, re.IGNORECASE)
        season = int(match.group(1)) if match else 1

        subtitle_file = self._find_srt_for(season, episode)
        if not subtitle_file:
            print(f"No .srt found for S{season}E{episode}")
            return

        full_path = os.path.join(self.srt_dir, subtitle_file)
        if full_path == self.srt_path:
            return

        self._load_new_srt(subtitle_file, season, episode)

    def update_loop(self) -> None:
        if self.playing and not self.slider_dragging:
            now = time.time()
            delta = now - self.last_update
            self.current_time += delta
            self.last_update = now
            if self.current_time >= self.total_duration:
                self.current_time = self.total_duration
                self.playing = False
            self.slider.set(self.current_time)
            self.update_time_displays()
            self.update_subtitle_display()
        try:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
            sub_x, sub_y = self.sub_window.winfo_rootx(), self.sub_window.winfo_rooty()
            sub_w, sub_h = self.sub_window.winfo_width(), self.sub_window.winfo_height()
            inside = sub_x <= x <= sub_x + sub_w and sub_y <= y <= sub_y + sub_h
            if inside != self.mouse_over_subtitles:
                self.set_mouse_over("subtitles", inside)
        except Exception:
            pass
        self.root.after(self.update_interval_ms, self.update_loop)