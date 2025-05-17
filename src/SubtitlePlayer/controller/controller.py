import os
import time
import bisect
import tkinter as tk
from pynput.mouse import Button, Listener as MouseListener
from pynput.keyboard import Key, Listener as KeyboardListener

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from utils import parse_time_value
from view.popup import CopyPopup

class SubtitleController:

    def __init__(self,
                manager: SubtitleManager,
                renderer: SubtitleRenderer,
                settings_ui: SettingsUI,
                overlay_ui: SubtitleOverlayUI,
                popup: CopyPopup,
                config: ConfigManager):
        self.manager = manager
        self.renderer = renderer
        self.settings = settings_ui
        self.overlay = overlay_ui
        self.popup   = popup
        self.config  = config
        
        self.default_start_time = config.get("DEFAULT_START_TIME")
        self.current_time = self.default_start_time
        self.default_skip = config.get("DEFAULT_SKIP")
        self.extra_offset = self.config.get("EXTRA_OFFSET")
        self.phone_hide_control_window_ms = config.get("PHONEMODE_CONTROL_HIDE_DELAY_MS")   # hides control window after # ms in phone mode
        self.hide_subtitles_ms = self.config.get("SUBTITLE_TIMEOUT_MS")                     # clears subtitle canvas after # ms
        self.update_interval_ms = config.get("UPDATE_INTERVAL_MS")                        # updates the time display every # ms
        
        self.playing      = False
        self.time_editing = False
        self.user_hidden = False
        self.alt_pressed = False
        self.subtitle_timeout_job = None
        self.last_subtitle_text = ""

        self.settings.bind_back(self.go_back)
        self.settings.bind_forward(self.go_forward)
        self.settings.bind_play_pause(self.toggle_play)
        self.settings.bind_slider(
            on_chg    = self.on_slider_change,
            on_pr     = self.on_slider_press,
            on_rl     = self.on_slider_release
        )
        self.settings.bind_episode_change(
            on_ent    = self.on_episode_change,
            on_inc    = self.increment_episode,
            on_dec    = self.decline_episode
        )
        self.settings.bind_open_srt                  (self.handle_open_srt)
        self.settings.bind_set_to_time               (self.on_set_to_time)
        self.settings.bind_time_entry_return         (self.control_time_entry_return)
        self.settings.bind_time_entry_clear          (self.control_clear_time_entry)
        self.settings.bind_setting_clear_offset_entry(self.setting_clear_offset_entry)
        self.settings.bind_setting_clear_skip_entry  (self.setting_clear_skip_entry)
        self.settings.bind_show_subtitle_handle      (self.show_subtitle_handle)
        # self.settings.slider.config(to=self.manager.get_total_duration())

        self.overlay.subtitle_canvas.bind("<Button-3>", lambda e: self.popup.open_copy_popup(self.last_subtitle_text))
        self.overlay.sub_window.bind("<Enter>", lambda e: self._on_sub_hover(True))
        self.overlay.sub_window.bind("<Leave>", lambda e: self._on_sub_hover(False))


        MouseListener(on_click=self.on_global_click).start()
        KeyboardListener(on_press=self._on_key_press, on_release=self._on_key_release).start()

        self.last_update  = time.time()
        self.update_time_displays()
        self.update_subtitle_display()


    # ——— Subtitle display ———————————————————————————————————
    def update_time_displays(self):
        value = SubtitleRenderer._format_time(self.current_time)
        self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=value)
        # self.settings.time_overlay.config(value) 
        if not self.time_editing:
            self.settings.play_time_var.set(value)


    def update_subtitle_display(self):
        offset = parse_time_value(self.settings.offset_var.get(), default_skip=self.default_skip) + self.extra_offset
        eff = self.current_time - offset
        idx = bisect.bisect_right(self.manager.start_times, eff) - 1
        new_text = self.manager.cleaned_subtitles[idx] if idx >= 0 else ""
        if new_text == self.last_subtitle_text and self.user_hidden:
            return
        if new_text != self.last_subtitle_text:
            if self.subtitle_timeout_job is not None:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            self.last_subtitle_text = new_text
            self.user_hidden = False
            self.renderer.render(
                text=new_text,
                max_width=self.overlay.max_width,
                bottom_anchor=self.overlay.bottom_anchor,
                sub_window=self.overlay.sub_window
            )

        if self.playing and not self.subtitle_timeout_job:
            self.subtitle_timeout_job = self.overlay.root.after(
                self.hide_subtitles_ms,
                self.hide_subtitles_temporarily
                )

    def hide_subtitles_temporarily(self):
        if not self.playing:
            self.subtitle_timeout_job = None
            return
        if not self.user_hidden:
            self.renderer.canvas.delete("all")
            self.user_hidden = True
        self.subtitle_timeout_job = None


    # ——— Keyboard handlers —————————————————————————————————————
    def _on_key_press(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = True
        elif self.alt_pressed:
            if hasattr(key, "char") and key.char:
                if key.char.lower() == "x":
                    self.on_alt_x()
                elif key.char.lower() == "c":
                    self.increment_episode()
                elif key.char.lower() == "y":
                    self.decline_episode()

    def _on_key_release(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = False

    def on_alt_x(self, event=None):
        self.settings.control_window.attributes("-topmost", True)


    # ——— Time handling ———————————————————————————————————
    def on_set_to_time(self, text: str):
        secs = parse_time_value(text, default_skip=self.default_skip)
        if secs is None:
            return
        self.set_current_time(secs)
        self.settings.setto_entry.delete(0, tk.END)

    def control_time_entry_return(self, event):
        self.time_editing = False
        content = self.settings.play_time_var.get().strip()
        if content == "":
            self.force_update_entry()
            return
        try:
            new_time = parse_time_value(content, default_skip=self.default_skip)
            self.set_current_time(new_time)
        except ValueError:
            pass
        self.force_update_entry()

    def control_clear_time_entry(self, event):
        self.time_editing = True
        event.widget.delete(0, tk.END)

    def setting_clear_offset_entry(self, event):
        self.settings.offset_entry.delete(0, tk.END)

    def setting_clear_skip_entry(self, event):
        self.settings.skip_entry.delete(0, tk.END)

    def force_update_entry(self, event=None):
        formatted = SubtitleRenderer._format_time(self.current_time)
        self.settings.play_time_var.set(formatted)
        
    # ——— Episode switching ———————————————————————————————————
    def handle_open_srt(self):
        new_path = self.manager.prompt_srt_file()
        if new_path:
            self.load_srt_file(new_path)
            self._after_episode_change()

    def load_srt_file(self, path: str) -> None:
        self.manager.srt_file = path
        filename = os.path.basename(path)
        self.manager.current_season = self.manager._extract_number(self.manager.SEASON_PATTERN, filename, default=None)
        self.manager.current_episode = self.manager._extract_number(self.manager.EPISODE_PATTERN, filename, default=None)
        self.manager.subtitles = self.manager._load_subtitles(path)
        self.manager.cleaned_subtitles = [self.manager._clean_text(s.content) for s in self.manager.subtitles]
        self.manager.start_times = [s.start.total_seconds() for s in self.manager.subtitles]
        self.manager.srt_dir = os.path.dirname(path)
        self.manager._srt_file_list = [f for f in os.listdir(self.manager.srt_dir) if f.lower().endswith('.srt')]

    def on_episode_change(self):
        if self.manager.current_episode is None or self.settings.episode_var.get() == "Movie":
            self.settings.episode_var.set("Movie")
            return
        ep = int(self.settings.episode_var.get() or 1)
        season = self.manager.current_season
        self.manager.set_episode(season, ep)
        self._after_episode_change()
        
    def increment_episode(self):
        if self.manager.current_episode is None or self.settings.episode_var.get() == "Movie":
            self.settings.episode_var.set("Movie")
            return
        current = int(self.settings.episode_var.get() or 1)
        next_ep = current + 1
        season = self.manager.current_season
        try:
            self.manager.set_episode(season, next_ep)
            self._after_episode_change()
        except FileNotFoundError:
            self.settings.episode_var.set(str(self.manager.current_episode))
            raise FileNotFoundError(f"No .srt found for S{season}E{next_ep}")

    def decline_episode(self):
        if self.manager.current_episode is None or self.settings.episode_var.get() == "Movie":
            self.settings.episode_var.set("Movie")
            return
        current = int(self.settings.episode_var.get() or 1)
        next_ep = current - 1
        season = self.manager.current_season
        try:
            self.manager.set_episode(season, next_ep)
            self._after_episode_change()
        except FileNotFoundError:
            self.settings.episode_var.set(str(self.manager.current_episode))
            raise FileNotFoundError(f"No .srt found for S{season}E{next_ep}")
        
    def _after_episode_change(self):
        self.settings.slider.set(self.default_start_time)
        self.current_time = self.default_start_time
        self.settings.slider.config(to=self.manager.get_total_duration())
        self.update_time_displays()
        self.update_subtitle_display()
        self.settings.update_time_overlay_position()
        if self.manager.current_episode is None:
            self.settings.episode_var.set("Movie")
        else:
            self.settings.episode_var.set(str(self.manager.current_episode))

    # ——— Playback controls ———————————————————————————————————
    def toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            self.settings.play_pause_btn.config(text="Stop", bg="red", activebackground="red")
            self.last_update = time.time()
            self.schedule_update()
        else:
            self.settings.play_pause_btn.config(text="Play", bg="green", activebackground="green")
            if self.subtitle_timeout_job is not None:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            if self.user_hidden and self.last_subtitle_text:
                    self.user_hidden = False
                    self.renderer.render(text=self.last_subtitle_text,
                        max_width=self.overlay.max_width,
                        bottom_anchor=self.overlay.bottom_anchor,
                        sub_window=self.overlay.sub_window
                    )

    def go_forward(self):
        skip = parse_time_value(self.settings.skip_entry.get(), default_skip=self.default_skip)
        self.set_current_time(self.current_time + skip)
    def go_back(self):
        skip = parse_time_value(self.settings.skip_entry.get(), default_skip=self.default_skip)
        self.set_current_time(self.current_time - skip)

    def on_slider_change(self, value):
        if getattr(self, "slider_dragging", False):
            self.set_current_time(float(value))

    def on_slider_press(self, event):
        self.slider_dragging = True
    def on_slider_release(self, event):
        self.slider_dragging = False
        self.set_current_time(self.settings.slider.get())

    def set_current_time(self, t: float):
        if t is None:
            return
        dur = self.manager.get_total_duration()
        self.current_time = max(0.0, min(t, dur))
        self.settings.slider.set(self.current_time)
        self.update_time_displays()
        self.update_subtitle_display()
        self.settings.update_time_overlay_position()


    # ——— Loop & scheduling ———————————————————————————————————
    def schedule_update(self):
        self.overlay.root.after(self.update_interval_ms, self.update_loop)

    def update_loop(self):
        if self.playing:
            now = time.time()
            delta = now - self.last_update
            self.last_update = now
            self.set_current_time(self.current_time + delta)
            self.schedule_update()
    

    # ——— Global mouse click handler ———————————————————————————
    def on_global_click(self, x, y, button, pressed) -> None:
        if button == Button.x2 and pressed:
            self._on_subtitle_click(None)        



    # ——— Subtitle hover & click handlers ———————————————————————
    def _on_sub_hover(self, inside: bool):
        # called on enter/leave subtitle area
        if inside:
            # show controls
            self.settings.control_window.lift()
            self.overlay.sub_window.attributes("-transparentcolor", "")
        else:
            # maybe hide controls after a delay
            self.overlay.sub_window.attributes("-transparentcolor", "grey")
            self.schedule_hide_controls()

    def _on_subtitle_click(self, event):
        # toggle subtitle visibility
        self.renderer.canvas.delete("all") if not getattr(self, "sub_hidden", False) else self.update_subtitle_display()
        self.sub_hidden = not getattr(self, "sub_hidden", False)
        
    def schedule_hide_controls(self):
        # only in phone mode do we auto‐hide
        if self.settings.phone_mode.get():
            if getattr(self, "_hide_job", None):
                self.overlay.root.after_cancel(self._hide_job)
            self._hide_job = self.overlay.root.after(
                self.phone_hide_control_window_ms,
                lambda: self.settings.control_window.lower()
            )

    def show_subtitle_handle(self, is_phone):
        if is_phone:
            self.overlay.show_handle()
        else:
            self.overlay.hide_handle()