import time
import os
import re
import sys

from pynput.mouse import Listener
from pynput.keyboard import Key, Listener as KeyboardListener

from config_manager import ConfigManager
from ui.subtitle_overlay import SubtitleOverlayUI
from ui.control_ui import ControlUI
from subtitle.manager import SubtitleManager
from subtitle.renderer import SubtitleRenderer


class SubtitleController:

    def __init__(self,
                 manager: SubtitleManager,
                 renderer: SubtitleRenderer,
                 control_ui: ControlUI,
                 overlay_ui: SubtitleOverlayUI,
                 config: ConfigManager):
        srt_path_debug = config['DEBUGGING']
        self.srt_path = config['DEBUG_SRT_FILE'] if srt_path_debug else self.prompt_srt_file()
        if not self.srt_path: sys.exit(0)

        self.global_mouse_listener = Listener(on_click=self.on_global_click)
        self.global_mouse_listener.start()
        self.alt_pressed = False
        self.keyboard_listener = KeyboardListener(
            on_press=self._on_key_press,
            on_release=self._on_key_release)
        self.keyboard_listener.start()
        self.manager = manager
        self.renderer = renderer
        self.control = control_ui
        self.overlay = overlay_ui
        self.config = config
        self.playing = False
        self.alt_pressed = False
        self.start_time = config.get('DEFAULT_START_TIME')
        self.update_interval_ms = self.config.get('UPDATE_INTERVAL_MS')

    def _on_key_press(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = True
        elif hasattr(key, "char") and key.char and key.char.lower() == "x" and self.alt_pressed:
            self.on_alt_x()
        elif hasattr(key, "char") and key.char and key.char.lower() == "c" and self.alt_pressed:
            self.on_alt_c()
        elif hasattr(key, "char") and key.char and key.char.lower() == "y" and self.alt_pressed:
            self.on_alt_y()

    def _on_key_release(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = False

    def on_alt_x(self, event=None):
        self.control_window.attributes("-topmost", True)
    def on_alt_c(self, event=None):
        self.increment_episode()
    def on_alt_y(self, event=None):
        self.decline_episode()

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

    def set_current_time(self, new_time: float) -> None:
        self.current_time = max(0.0, min(new_time, self.total_duration))
        self.last_update = time.time()
        self.slider.set(self.current_time)
        self.update_time_displays()
        self.update_subtitle_display()

    def update_subtitle_display(self) -> None:
        self.user_offset = float(self.offset_var.get())
        effective_time = self.current_time - (self.user_offset + config['EXTRA_OFFSET'])
        index = bisect.bisect_right(self.start_times, effective_time) - 1
        new_text = self.cleaned_subtitles[index] if index >= 0 else ""
        if new_text == self.last_subtitle_text and self.user_hidden:
            return
        if new_text != self.last_subtitle_text:
            if self.subtitle_timeout_job is not None:
                self.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            self.last_subtitle_text = new_text                
            self.user_hidden = False
            self._render_subtitle(new_text)

        # schedule hide
        if self.playing and not self.subtitle_timeout_job:
            self.subtitle_timeout_job = self.root.after(
                config['SUBTITLE_TIMEOUT_MS'],
                self.hide_subtitles_temporarily
            )

    def hide_subtitles_temporarily(self):
        if not self.playing:
            self.subtitle_timeout_job = None
            return
        if not self.user_hidden:
            self.subtitle_canvas.delete("all")
            self.user_hidden = True
        self.subtitle_timeout_job = None

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

    def measure_max_subtitle_width(self) -> int:
        return max(
            self.subtitle_font.measure(line)
            for text in self.cleaned_subtitles
            for line in text.splitlines())
