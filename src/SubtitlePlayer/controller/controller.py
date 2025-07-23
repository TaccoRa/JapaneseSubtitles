import time
import tkinter as tk
from pynput.mouse import Button, Listener as MouseListener
from pynput.keyboard import Key, Listener as KeyboardListener
import pyautogui
import bisect

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from utils import parse_time_value, format_time
from view.popup import CopyPopup

class SubtitleController:
    
    
    def __init__(self,
                manager: SubtitleManager,
                renderer: SubtitleRenderer,
                settings_ui: SettingsUI,
                overlay_ui: SubtitleOverlayUI,
                popup: CopyPopup,
                config: ConfigManager,
                total_duration: float):
        
        self.sub_manager = manager
        self.renderer = renderer
        self.settings = settings_ui
        self.overlay = overlay_ui
        self.popup   = popup
        self.config  = config
        self.total_duration = total_duration
        self.settings.root.protocol("WM_DELETE_WINDOW", self._on_app_close)

        self.default_start_time = self.config.get("DEFAULT_START_TIME")
        self.current_time = self.default_start_time
        self.default_skip = self.config.get("DEFAULT_SKIP")
        self.default_offset = self.config.get("EXTRA_OFFSET")
        self.phone_windows_hide_control_ms = self.config.get("PHONEMODE_WINDOWS_HIDE_DELAY_MS")   # hides control window after # ms in phone mode
        self.windows_hide_control_ms = self.config.get("WINDOWS_HIDE_DELAY_MS")   # hides control window after # ms in phone mode
        self.hide_subtitles_ms = self.config.get("SUBTITLE_TIMEOUT_MS")                     # clears subtitle canvas after # ms
        self.update_interval_ms = self.config.get("UPDATE_INTERVAL_MS")                        # updates the time display every # ms
        self.video_click = self.config.get("VIDEO_CLICK")

        self.playing      = False
        self.entry_editing  = False
        self.subtitle_deleted = False
        self.alt_pressed = False
        self.subtitle_timeout_job = None
        self.last_subtitle_text = ""
        self.sub_hidden = False
        self.slider_dragging = False
        
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
        self._reset_after_srt_load()
        self.settings.bind_open_srt                  (self._on_open_srt)
        self.settings.bind_set_to_return             (self.on_set_to_return)
        self.settings.bind_time_entry_return         (self.control_time_entry_return)
        self.settings.bind_time_entry_clear          (self.control_clear_time_entry)
        self.settings.bind_control_window_enter      (self.control_window_enter)
        self.settings.bind_control_window_leave      (self.control_window_leave)
        self.settings.bind_show_subtitle_handle      (self.show_subtitle_handle)
        
        self.settings.bind_update_display            (self.update_time_and_subtitle_displays)
        
        self.overlay.subtitle_canvas.bind("<Button-3>", lambda e: self.popup.open_copy_popup(self.last_subtitle_raw))
        self.overlay.bind_sub_window_enter(self.sub_window_enter)
        self.overlay.bind_sub_window_leave(self.sub_window_leave)
        self.overlay.bind_sub_handel_enter(self.sub_handel_enter)   


        MouseListener(on_click=self._on_global_click).start()
        KeyboardListener(on_press=self._on_key_press, on_release=self._on_key_release).start()

        self.last_update  = time.time()
        # self.update_time_and_subtitle_displays()



    def _reset_after_srt_load(self):
        # 1) tell your manager about new file already happened before calling this…
        self.total_duration = self.sub_manager.get_total_duration()

        # 2) reconfigure the slider’s range
        offset = self.settings._last_offset_value
        self.settings.slider.config(to=self.total_duration + offset)

        # 3) reset play time / slider thumb
        self.current_time = self.default_start_time
        self.settings.slider.set(self.current_time)

        # 4) reset any play/stop state
        self.playing = False
        self.settings.play_pause_btn.config(text="Play", bg="green", activebackground="green")

        # 5) force a full redraw of everything
        self.update_time_and_subtitle_displays()






    def sub_window_enter(self, event):
        self.overlay.sub_window.attributes("-transparentcolor", "") #not transparent
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        if getattr(self, "_con_hide_job", None) is not None:
            self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
            self._con_hide_job = None
        self.subtitle_deleted = False

    def sub_window_leave(self, event):
        self.overlay.sub_window.attributes("-transparentcolor", "grey")
        if not self.settings.phone_mode.get():
            self._hide_controls_after(self.windows_hide_control_ms)

    def sub_handel_enter(self, event):
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
            self._con_hide_job = None
        self.subtitle_deleted = False

    def control_window_enter(self, event):
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
            self._con_hide_job = None
        self.subtitle_deleted = False

    def control_window_leave(self, event):
        delay = (self.phone_windows_hide_control_ms
                 if self.settings.phone_mode.get()
                 else self.windows_hide_control_ms)
        self._hide_controls_after(delay)


    def update_time_and_subtitle_displays(self):#updates settings time overlay and control window entry
        text = format_time(self.current_time)
        self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
        self.settings.update_time_overlay_position()
        if not self.entry_editing:
            self.settings.control_time_str.set(text)
        self.update_subtitle_display()





    def _reset_canvas(self):
        self.renderer.canvas.delete("all")
        self.subtitle_deleted = True
        self.last_subtitle_text = ""


    def update_subtitle_display(self):
        offset = self.settings._last_offset_value
        sub_t = self.current_time - offset
        if sub_t < 0 or sub_t > self.total_duration:
            self._reset_canvas()
            return
        
        idx = bisect.bisect_right(self.sub_manager.start_times, sub_t) - 1
        if idx < 0:
            self._reset_canvas()
            return
        clean, top, bottom = self.sub_manager.display_data[idx]
        joined = ''.join(base for base, _ in (top + bottom))

        if joined != self.last_subtitle_text:
            # cancel any old hide‐job
            if self.subtitle_timeout_job:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None

            # clear old and draw new
            self.renderer.canvas.delete("all")
            self.last_subtitle_text = joined
            self.subtitle_deleted   = False
            self.renderer.render_subtitle(top, bottom, self.overlay)

            # schedule its removal after your fixed timeout
            self.subtitle_timeout_job = self.overlay.root.after(
                self.hide_subtitles_ms,
                self.hide_subtitles_temporarily
            )

    def hide_subtitles_temporarily(self):
        if not self.playing:
            self.subtitle_timeout_job = None
            return
        if not self.subtitle_deleted:
            self.renderer.canvas.delete("all")
            self.subtitle_deleted = True
        self.subtitle_timeout_job = None






    def on_episode_change(self):
        if self.sub_manager.current_episode is None or self.settings.episode_var.get() == "Movie":
            self.settings.episode_var.set("Movie")
            return
        ep = int(self.settings.episode_var.get() or 1)
        season = self.sub_manager.current_season
        self.sub_manager.set_episode(season, ep)
        self._after_episode_change()
        
    def increment_episode(self):
        if self.sub_manager.current_episode is None or self.settings.episode_var.get() == "Movie":
            self.settings.episode_var.set("Movie")
            return
        current = int(self.settings.episode_var.get() or 1)
        next_ep = current + 1
        season = self.sub_manager.current_season
        try:
            self.sub_manager.set_episode(season, next_ep)
            self._after_episode_change()
        except FileNotFoundError:
            self.settings.episode_var.set(str(self.sub_manager.current_episode))
            raise FileNotFoundError(f"No .srt found for S{season}E{next_ep}")

    def decline_episode(self):
        if self.sub_manager.current_episode is None or self.settings.episode_var.get() == "Movie":
            self.settings.episode_var.set("Movie")
            return
        current = int(self.settings.episode_var.get() or 1)
        next_ep = current - 1
        season = self.sub_manager.current_season
        try:
            self.sub_manager.set_episode(season, next_ep)
            self._after_episode_change()
        except FileNotFoundError:
            self.settings.episode_var.set(str(self.sub_manager.current_episode))
            raise FileNotFoundError(f"No .srt found for S{season}E{next_ep}")
        
    def _after_episode_change(self):
        self.total_duration = self.sub_manager.get_total_duration()
        self.settings.slider.set(self.default_start_time)
        self.settings.slider.config(to=self.total_duration + self.settings._last_offset_value)
        if self.sub_manager.current_episode is None:
              self.settings.episode_var.set("Movie")
        else: self.settings.episode_var.set(str(self.sub_manager.current_episode))
        self.current_time = self.default_start_time
        self.set_current_time(self.current_time)



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
    def set_current_time(self, t: float):
        if t is None:
            return
        offset   = self.settings._last_offset_value
        t = max(0, min(t, self.total_duration + offset))
        
        if t - offset >= self.total_duration and self.playing:
            self.toggle_play()

        self.current_time = t
        self.settings.slider.set(t)
        self.update_time_and_subtitle_displays()

    def on_set_to_return(self, text: str):
        secs = parse_time_value(text)
        self.set_current_time(secs)
        self.settings.setto_entry.delete(0, tk.END)
        self.settings.root.focus_set()

    def control_time_entry_return(self, event):
        self.entry_editing  = False
        text = self.settings.control_time_str.get().strip()
        if text == "":
            self.settings.control_time_str.set(format_time(self.current_time))
            return
        new_time = parse_time_value(text)
        self.set_current_time(new_time)

    def control_clear_time_entry(self, event):
        if self.playing:
            self.toggle_play()
        self.entry_editing  = True
        event.widget.delete(0, tk.END)

    # ——— Buttons ———————————————————————————————————
    def _on_open_srt(self, event=None):
        if not self.sub_manager.load_srt_file():
            return
        self._after_episode_change()



    def _on_global_click(self, x, y, button, pressed):
        if button == Button.x2 and pressed:
            self.renderer.canvas.delete("all")
            self.last_subtitle_text = ""
            self.last_subtitle_raw = ""
            self.subtitle_deleted = True

    # ——— Playback controls ———————————————————————————————————
    def toggle_play(self):
        if self.entry_editing:
            self.control_time_entry_return(None)
        self.playing = not self.playing 
        if self.playing:
            self.settings.play_pause_btn.config(text="Stop", bg="red", activebackground="red")
            self.last_update = time.time()
            self.schedule_update()
        else:
            self.settings.play_pause_btn.config(text="Play", bg="green", activebackground="green")
            if self.subtitle_timeout_job:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            if self.subtitle_deleted and self.last_subtitle_text:
                self.subtitle_deleted = False
        if self.video_click: self.simulate_video_click()
        # self.control_time_entry_return()
        self.update_time_and_subtitle_displays()
        self.schedule_hide_controls()

    def go_forward(self):
        skip = self.settings._last_skip_value
        if self.current_time <= self.total_duration + float(self.settings.offset_entry.get().replace("s","").replace(" ","").replace(":","")):
            self.set_current_time(self.current_time + skip)
            self.schedule_hide_controls()

    def go_back(self):
        skip = self.settings._last_skip_value
        if self.current_time >= 0:
            self.set_current_time(self.current_time - skip)
            self.schedule_hide_controls()

    def on_slider_change(self, value):
        if self.slider_dragging:
            self.set_current_time(float(value))
    def on_slider_press(self, event):
        self.slider_dragging = True
    def on_slider_release(self, event):
        self.slider_dragging = False
        self.set_current_time(self.settings.slider.get())

    def simulate_video_click(self):
        original_pos = pyautogui.position()
        target_x = self.config.get("LAST_CONTROL_WINDOW_X") + 50
        target_y = self.config.get("LAST_CONTROL_WINDOW_Y") - 50
        pyautogui.click(target_x, target_y)
        self.settings.control_window.attributes("-topmost", True)
        pyautogui.moveTo(original_pos.x, original_pos.y)




    # ——— Loop & scheduling ———————————————————————————————————
    def update_loop(self):
        if self.playing:
            now = time.time()
            delta = now - self.last_update
            self.last_update = now
            self.set_current_time(self.current_time + delta)
            
        self.schedule_update()

    def schedule_update(self):
        self.overlay.root.after(self.update_interval_ms, self.update_loop)




    # ——— Hide helpers  ———————————————————————
    def _hide_controls_after(self, ms: int):
        """Cancel any pending hide-job and schedule a new one."""
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job)
        self._con_hide_job = self.settings.control_window.after(
            ms,
            self.settings.control_window.lower
        )

    def schedule_hide_controls(self):
        if self.settings.phone_mode.get():
            self._hide_controls_after(self.phone_windows_hide_control_ms)

    def show_subtitle_handle(self, is_phone):
        if is_phone:
            self.overlay.show_handle()
        else:
            self.overlay.hide_handle()

    def _on_app_close(self):
        for job in ("subtitle_timeout_job", "_con_hide_job"):
            handle = getattr(self, job, None)
            if handle is not None:
                try:
                    self.settings.root.after_cancel(handle)
                except Exception:
                    pass
        try:
            self.popup._cancel_close()
        except AttributeError:
            pass
        for w in (self.settings.control_window, self.overlay.sub_window, self.popup._popup):
            if w:
                try: w.destroy()
                except: pass

        self.settings.root.destroy()