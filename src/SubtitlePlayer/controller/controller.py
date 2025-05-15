import time
import bisect
import tkinter as tk
from pynput.mouse import Button, Listener as MouseListener
from pynput.keyboard import Key, Listener as KeyboardListener

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.control_ui import ControlUI
from view.subtitle_overlay import SubtitleOverlayUI
from utils import parse_time_value
from view.popup import CopyPopup

class SubtitleController:

    def __init__(self,
                manager: SubtitleManager,
                renderer: SubtitleRenderer,
                control_ui: ControlUI,
                overlay_ui: SubtitleOverlayUI,
                popup: CopyPopup,
                config: ConfigManager):
        self.manager = manager
        self.renderer = renderer
        self.control = control_ui
        self.overlay = overlay_ui
        self.popup   = popup
        self.config  = config
        
        self.current_time = config.get("DEFAULT_START_TIME")
        self.playing      = False
        self.last_update  = time.time()
        self.update_interval_ms = config.get("UPDATE_INTERVAL_MS")
        self.control_window_ms = config.get("PHONEMODE_CONTROL_HIDE_DELAY_MS")
        ov = self.overlay
        ov.sub_window.bind("<Enter>", lambda e: self._on_sub_hover(True))
        ov.sub_window.bind("<Leave>", lambda e: self._on_sub_hover(False))
        self.last_subtitle_text = ""
        ov.subtitle_canvas.bind("<Button-3>", lambda e: self.popup.open_copy_popup(self.last_subtitle_text))
        self.time_editing = False

        self.control.bind_back(self.go_back)
        self.control.bind_forward(self.go_forward)
        self.control.bind_play_pause(self.toggle_play)
        self.control.bind_slider(
            on_chg    = self.on_slider_change,
            on_pr     = self.on_slider_press,
            on_rl     = self.on_slider_release
        )
        self.control.bind_episode_change(
            on_ent    = self.on_episode_change,
            on_inc    = self.increment_episode,
            on_dec    = self.decline_episode
        )
        self.control.bind_set_to_time(self.on_set_to_time)
        control_ui.bind_time_entry_return(self.control_time_entry_return)
        control_ui.bind_time_entry_clear(self.control_clear_time_entry)

        self.control.bind_show_subtitle_handle(self.show_subtitle_handle)
        self.control.bind_open_srt(self.handle_open_srt)
        self.user_hidden = False
        self.subtitle_timeout_job = None

        self.alt_pressed = False
        MouseListener(on_click=self.on_global_click).start()
        KeyboardListener(on_press=self._on_key_press, on_release=self._on_key_release).start()

        self.control.slider.config(to=self.manager.get_total_duration())
        self.update_time_displays()
        self.update_subtitle_display()


    # ——— Keyboard handlers —————————————————————————————————————
    def _on_key_press(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = True
        elif self.alt_pressed:
            if getattr(key, "char", "").lower() == "x":
                self.on_alt_x()
            elif key.char.lower() == "c":
                self.increment_episode()
            elif key.char.lower() == "y":
                self.decline_episode()

    def _on_key_release(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = False

    def on_alt_x(self, event=None):
        self.control.control_window.attributes("-topmost", True)


    # ——— Time handling ———————————————————————————————————
    def on_set_to_time(self, text: str):
        secs = parse_time_value(text, default_skip = self.manager.get_skip_value)
        self.set_current_time(secs)
        self.control.setto_entry.delete(0, tk.END)

    def control_clear_time_entry(self, event):
        self.time_editing = True
        event.widget.delete(0, tk.END)

    def control_time_entry_return(self, event):
        self.time_editing = False
        content = self.control.play_time_var.get().strip()
        if content == "":
            self.force_update_entry()
            return
        try:
            new_time = parse_time_value(content, default_skip=self.manager.get_skip_value())
            self.set_current_time(new_time)
        except ValueError:
            pass
        self.force_update_entry()

    def force_update_entry(self, event=None):
        formatted = SubtitleRenderer._format_time(self.current_time)
        self.control.play_time_var.set(formatted)
        
    # ——— Episode switching ———————————————————————————————————
    def on_episode_change(self):
        ep = int(self.control.episode_var.get() or 1)
        season = self.manager.current_season
        self.manager.set_episode(season, ep)
        
        self.current_time = self.config.get("DEFAULT_START_TIME")
        self.control.slider.set(self.current_time)
        self.control.slider.config(to=self.manager.get_total_duration())
        self.update_time_displays()
        self.update_subtitle_display()
        self.control.episode_var.set(str(self.manager.current_episode))
        
    def handle_open_srt(self):
        new_path = self.manager.prompt_srt_file()
        if new_path:
            self.current_time = self.config.get("DEFAULT_START_TIME")
            self.control.slider.set(self.current_time)
            self.control.slider.config(to=self.manager.get_total_duration())
            self.update_time_displays()
            self.update_subtitle_display()
            self.control.episode_var.set(str(self.manager.current_episode))

    def increment_episode(self):
        current = int(self.control.episode_var.get() or 1)
        next_ep = current + 1
        season = self.manager.current_season
        try:
            self.manager.set_episode(season, next_ep)
            self.control.episode_var.set(str(self.manager.current_episode))
            self.on_episode_change()
        except FileNotFoundError:
            self.control.episode_var.set(str(self.manager.current_episode))
            raise FileNotFoundError(f"No .srt found for S{season}E{next_ep}")

    def decline_episode(self):
        current = int(self.control.episode_var.get() or 1)
        next_ep = current - 1
        season = self.manager.current_season
        try:
            self.manager.set_episode(season, next_ep)
            self.control.episode_var.set(str(self.manager.current_episode))
            self.on_episode_change()
        except FileNotFoundError:
            self.control.episode_var.set(str(self.manager.current_episode))
            raise FileNotFoundError(f"No .srt found for S{season}E{next_ep}")


    # ——— Playback controls ———————————————————————————————————
    def toggle_play(self):
        self.playing = not self.playing
        if self.playing:
            self.control.play_pause_button.config(text="Stop", bg="red", font = "bold", activebackground="red")
            self.last_update = time.time()
            self.schedule_update()
        else:
            self.control.play_pause_button.config(text="Play", bg="green", font = "bold", activebackground="green")
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
        skip = self.manager.get_skip_value()
        self.set_current_time(self.current_time + skip)
    def go_back(self):
        skip = self.manager.get_skip_value()
        self.set_current_time(self.current_time - skip)

    def on_slider_change(self, value):
        if getattr(self, "slider_dragging", False):
            self.set_current_time(float(value))
    def on_slider_press(self, event):
        self.slider_dragging = True
    def on_slider_release(self, event):
        self.slider_dragging = False
        self.set_current_time(self.control.slider.get())

    def set_current_time(self, t: float):
        dur = self.manager.get_total_duration()
        self.current_time = max(0.0, min(t, dur))
        self.control.slider.set(self.current_time)
        self.update_time_displays()
        self.update_subtitle_display()


    # ——— Subtitle display ———————————————————————————————————
    def update_time_displays(self):
        text = SubtitleRenderer._format_time(self.current_time)
        relx = self.config.get("RATIO") + (1 - 2*self.config.get("RATIO")) * (self.current_time / self.manager.get_total_duration())
        self.control.time_overlay.config(text=text) 
        self.control.time_overlay.place(in_=self.control.slider, relx=relx, rely=0.2)
        if not self.time_editing:
            self.control.play_time_var.set(text)


    def update_subtitle_display(self):
        offset = float(self.control.offset_var.get() or 0.0) + self.config.get("EXTRA_OFFSET")
        eff = self.current_time - offset
        idx = bisect.bisect_right(self.manager.start_times, eff) - 1
        text = self.manager.cleaned_subtitles[idx] if idx >= 0 else ""
        self.renderer.render(
            text=text,
            max_width=self.overlay.max_width,
            bottom_anchor=self.overlay.bottom_anchor,
            sub_window=self.overlay.sub_window
        )
        if self.playing and not self.subtitle_timeout_job:
            self.subtitle_timeout_job = self.overlay.root.after(
            self.config.get("SUBTITLE_TIMEOUT_MS"),
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
            self.control.control_window.lift()
            ov = self.overlay
            ov.sub_window.attributes("-transparentcolor", "")
        else:
            # maybe hide controls after a delay
            ov = self.overlay
            ov.sub_window.attributes("-transparentcolor", "grey")
            self.schedule_hide_controls()

    def _on_subtitle_click(self, event):
        # toggle subtitle visibility
        self.renderer.canvas.delete("all") if not getattr(self, "sub_hidden", False) else self.update_subtitle_display()
        self.sub_hidden = not getattr(self, "sub_hidden", False)
        
    def schedule_hide_controls(self):
        # only in phone mode do we auto‐hide
        if self.control.phone_mode.get():
            if getattr(self, "_hide_job", None):
                self.overlay.root.after_cancel(self._hide_job)
            self._hide_job = self.overlay.root.after(
                self.control_window_ms,
                lambda: self.control.control_window.lower()
            )

    def show_subtitle_handle(self, is_phone):
        if is_phone:
            self.overlay.show_handle()
        else:
            self.overlay.hide_handle()