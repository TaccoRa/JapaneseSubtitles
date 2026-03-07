"""
Controller glue between:
- SubtitleManager (model)
- SettingsUI + SubtitleOverlayUI + CopyPopup (view)
- SubtitleRenderer (rendering)

Handles input (buttons, keyboard, global mouse), time updates, and episode changes.
"""

import time
import tkinter as tk
from pynput.mouse import Button, Listener as MouseListener
from pynput.keyboard import Key, Listener as KeyboardListener
import pyautogui
import bisect
from model.config_manager import ConfigManager
from model.anki_client import AnkiClient
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
        self.anki_busy_cursor = (self.config.get("ANKI_BUSY_CURSOR") or "wait")
        self.anki = AnkiClient(self.config)

        self.playing      = False
        self.entry_editing  = False
        self.subtitle_deleted = False
        self.alt_pressed = False
        self.subtitle_timeout_job = None
        self.last_subtitle_text = ""
        self.last_subtitle_raw = ""
        self.sub_hidden = False
        self.slider_dragging = False
        self.last_rendered_sub_time = None

        self.settings.bind_back(self.go_back)
        self.settings.bind_forward(self.go_forward)
        self.settings.bind_play_pause(self.toggle_play)
        self.settings.bind_slider(
            on_chg    = self.on_slider_change,
            on_pr     = self.on_slider_press,
            on_rl     = self.on_slider_release
        )
        self.settings.bind_episode_change(
            on_ent    = lambda: self.change_episode('set'),
            on_inc    = lambda: self.change_episode('inc'),
            on_dec    = lambda: self.change_episode('dec')
        )
        self.settings.bind_open_srt                  (self._on_open_srt)
        self.settings.bind_set_to_return             (self.on_set_to_return)
        self.settings.bind_time_entry_return         (self.control_time_entry_return)
        self.settings.bind_time_entry_clear          (self.control_clear_time_entry)
        self.settings.bind_control_window_enter      (self.control_window_enter)
        self.settings.bind_control_window_leave      (self.control_window_leave)
        self.settings.bind_show_subtitle_handle      (self.show_subtitle_handle)
        self.settings.bind_refresh_subtitles         (self.on_refresh_subtitles)

        self.settings.bind_update_display            (self.update_time_and_subtitle_displays)
        
        self.overlay.subtitle_canvas.bind("<Button-3>", self._on_copy_popup)
        self.popup.bind_add_to_anki(self._add_selection_to_anki)
        self.overlay.bind_sub_window_enter(self.sub_window_enter)
        self.overlay.bind_sub_window_leave(self.sub_window_leave)
        self.overlay.bind_sub_handel_enter(self.sub_handel_enter)   


        MouseListener(on_click=self._on_global_click).start()
        KeyboardListener(on_press=self._on_key_press, on_release=self._on_key_release).start()

        self.last_update  = time.time()
        self.update_time_and_subtitle_displays()
        self._update_episode_nav_controls()


    def _update_episode_nav_controls(self) -> None:
        """
        Grey out + / - when we know from the episode maps/index that no prev/next exists.
        """
        try:
            can_dec, can_inc, is_movie = self.sub_manager.get_episode_nav_state()
        except Exception:
            # Unknown -> keep enabled, don't break the UI.
            can_dec, can_inc = True, True
            is_movie = (self.sub_manager.current_episode is None)
        try:
            self.settings.set_episode_nav_state(can_dec=can_dec, can_inc=can_inc, is_movie=is_movie)
        except Exception:
            pass

        # Update dropdown values for the episode entry (combobox).
        try:
            values = self.sub_manager.get_episode_dropdown_values()
            self.settings.set_episode_values(values)
        except Exception:
            pass

    def _on_copy_popup(self, event=None):
        # Create popup first so we can click relative to its position.
        self.popup.open_copy_popup(self.last_subtitle_raw)
        self.simulate_video_click()
        return "break"

    def _add_selection_to_anki(self, selected_text: str, subtitle_text: str = "") -> None:
        if not self.anki.ping():
            print("AnkiConnect not reachable. Start Anki + AnkiConnect and try again.")
            return

        started = time.perf_counter()
        self._set_busy_cursor(True)
        try:
            result = self.anki.add_from_selection(
                selection_text=selected_text,
                subtitle_text=subtitle_text,
            )
            elapsed = time.perf_counter() - started
            print(f"Anki note created in {elapsed:.2f}s")
            candidates = result.get("translation_candidates") or {}
            word_cands = candidates.get("word") or {}
            sentence_cands = candidates.get("sentence") or {}
            print(f"Note ID: {result.get('note_id', '')}")
            print(f"Marked Word: {(selected_text or '').strip()}")
            print(f"Word DeepL: {self._format_translation_csv(word_cands.get('deepl', ''))}")
            print(f"Word Jisho: {self._format_translation_csv(word_cands.get('jisho', ''))}")
            print(f"Sentence DeepL: {self._format_translation_csv(sentence_cands.get('deepl', ''))}")
            print(f"Sentence Google: {self._format_translation_csv(sentence_cands.get('google', ''))}")
            print("")
        except Exception as e:
            print(f"Anki add failed: {e}")
        finally:
            self._set_busy_cursor(False)

    def _format_translation_csv(self, value: str) -> str:
        text = (value or "").replace("\n", " ").replace("\r", " ").strip()
        text = " ".join(text.split())
        if not text:
            return "<empty>"
        text = text.replace(";", ",").replace("|", ",")
        parts = [part.strip() for part in text.split(",") if part.strip()]
        if not parts:
            return text
        return ", ".join(parts)

    def _set_busy_cursor(self, busy: bool) -> None:
        cursor = self.anki_busy_cursor if busy else ""
        windows = [
            getattr(self.settings, "root", None),
            getattr(self.settings, "control_window", None),
            getattr(self.overlay, "sub_window", None),
            getattr(self.popup, "_popup", None),
        ]
        for win in windows:
            if not win:
                continue
            try:
                win.configure(cursor=cursor)
            except Exception:
                pass
        try:
            self.popup.set_busy_cursor(cursor)
        except Exception:
            pass
        try:
            self.settings.root.update()
        except Exception:
            pass


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


    # ——— Time handling ———————————————————————————————————
    def set_current_time(self, t: float):
        if t is None:
            return
        offset   = self.settings._last_offset_value
        t = max(0, min(t, self.total_duration + offset))
        
        if t - offset >= self.total_duration and self.playing:
            self.toggle_play()

        self.current_time = t
        if not self.slider_dragging:
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




    # ——— Updating Logic —————————————————————————————————————

    def update_time_and_subtitle_displays(self):#updates settings time overlay and control window entry
        text = format_time(self.current_time)
        self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
        self.settings.update_time_overlay_position()
        if not self.entry_editing:
            self.settings.control_time_str.set(text)
        self._update_subtitle_display()

    def _update_subtitle_display(self):
        offset = self.settings._last_offset_value
        sub_t = self.current_time - offset
        if sub_t < 0 or sub_t > self.total_duration:
            self._reset_canvas()
            return
        
        start_times = [item[1] for item in self.sub_manager.display_data]
        idx = bisect.bisect_right(start_times, sub_t) - 1
        if idx < 0:
            self._reset_canvas()
            return
        clean, _, top, bottom = self.sub_manager.display_data[idx]
        joined = ''.join(base for base, _ in (top + bottom))
        self.last_subtitle_raw = clean
        if joined == self.last_subtitle_text:
            return
        if joined != self.last_subtitle_text:
            if self.subtitle_timeout_job:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None

            self.renderer.canvas.delete("all")
            self.last_subtitle_text = joined
            self.subtitle_deleted   = False
            self.renderer.render_subtitle(top, bottom, self.overlay)

            self.subtitle_timeout_job = self.overlay.root.after(
                self.hide_subtitles_ms,
                self._hide_subtitles_temporarily)

    def _reset_canvas(self):
        self.renderer.canvas.delete("all")
        self.subtitle_deleted = True
        self.last_subtitle_text = ""

    def _hide_subtitles_temporarily(self):
        if not self.playing:
            self.subtitle_timeout_job = None
            return
        if not self.subtitle_deleted:
            self.renderer.canvas.delete("all")
            self.subtitle_deleted = True
        self.subtitle_timeout_job = None

    def on_refresh_subtitles(self, event):
        # 1) cancel any pending hide‐job
        if self.subtitle_timeout_job:
            self.overlay.root.after_cancel(self.subtitle_timeout_job)
            self.subtitle_timeout_job = None
        self.last_subtitle_text = ""
        self.subtitle_deleted    = False

        self.update_time_and_subtitle_displays()


    # ——— Change srt file ———————————————————————————————————
    def _on_open_srt(self, event=None):
        path = self.sub_manager.set_new_file()
        if path:
            self._after_episode_change()

    def change_episode(self, action: str):
        raw = self.settings.episode_var.get().strip()
        if not raw:
            # restore to current known value
            if self.sub_manager.current_episode is None:
                self.settings.episode_var.set("Movie")
            else:
                self.settings.episode_var.set(str(self.sub_manager.current_episode))
            return
        if raw.lower() == 'movie':
            return
        try:
            raw_int = int(raw)
            if raw_int <= 0:
                raise ValueError()
        except ValueError:
            # invalid entry -> restore
            if self.sub_manager.current_episode is None:
                self.settings.episode_var.set("Movie")
            else:
                self.settings.episode_var.set(str(self.sub_manager.current_episode))
            return
        target_season,target_episode = self.sub_manager.change_episode(action, raw_int)
        if target_episode is not None:
            self.settings.episode_var.set(str(target_episode))
            self._after_episode_change() #reset all with new srt data
        else: #change not allowed
            if self.sub_manager.current_episode is None:
                self.settings.episode_var.set("Movie")
            else:
                self.settings.episode_var.set(str(self.sub_manager.current_episode))

    def _after_episode_change(self):
        if self.sub_manager.current_episode is None:
              self.settings.episode_var.set("Movie")
        else: 
            self.settings.episode_var.set(str(self.sub_manager.current_episode))
        self._update_episode_nav_controls()

        new_total = self.sub_manager.get_total_duration() ##maybe not needed anymore
        self.settings.set_total_duration(new_total)
        self.total_duration = new_total
        self.update_max_width()
        title= f'S{self.sub_manager.get_current_season()}E{self.sub_manager.get_current_episode()} {self.sub_manager.get_anime_name()}'
        self.settings.root.title(title)
        self.current_time = self.default_start_time
        self.set_current_time(self.current_time)
        
    def update_max_width(self) -> None:
        # Recompute content width + padding
        max_w, max_h = self.sub_manager.get_subtitle_geometry()
        max_w, max_h = int(max_w), int(max_h)

        # Apply geometry on overlay and update renderer's canvas ref
        self.overlay.update_geometry(max_w, max_h)
        self.renderer.update_canvas(self.overlay.subtitle_canvas)

        # ensure layout finalized so canvas.winfo_width() matches what renderer expects
        self.overlay.subtitle_canvas.update_idletasks()

        # Immediately re-render the subtitle at current_time (same logic as _update_subtitle_display)
        offset = self.settings._last_offset_value
        sub_t = self.current_time - offset
        start_times = [item[1] for item in self.sub_manager.display_data]
        idx = bisect.bisect_right(start_times, sub_t) - 1
        if idx < 0:
            # nothing to draw
            self.renderer.canvas.delete("all")
            return

        _, _, top_segments, bottom_segments = self.sub_manager.display_data[idx]
        # render freshly using updated overlay/canvas
        try:
            self.renderer.canvas.delete("all")
            self.renderer.render_subtitle(top_segments, bottom_segments, self.overlay)
        except Exception:
            # keep app alive if rendering fails; log if you have logger
            pass

        # # Re‐center around the stored center_x/center_y
        # center_x, center_y = self.config.get("LAST_SUB_CENTER_X"), self.config.get("LAST_SUB_CENTER_Y")
        # x = int(center_x - max_w / 2)
        # y = int(center_y - max_h / 2)

        # # Clamp to screen bounds
        # sw = self.overlay.root.winfo_vrootwidth()
        # sh = self.overlay.root.winfo_vrootheight()
        # x = max(0, min(x, sw  - x))
        # y = max(0, min(y, sh  - y))
        # # print("new width is ",max_w)
        # # Apply the new geometry
        # self.overlay.sub_window.geometry(f"{max_w}x{max_h}+{x}+{y}")
        # self.overlay.sub_window.update_idletasks()



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
        self._schedule_hide_controls()

    def go_forward(self):
        skip = self.settings._last_skip_value
        if self.current_time <= self.total_duration + float(self.settings.offset_entry.get().replace("s","").replace(" ","").replace(":","")):
            self.set_current_time(self.current_time + skip)
            self._schedule_hide_controls()

    def go_back(self):
        skip = self.settings._last_skip_value
        if self.current_time >= 0:
            self.set_current_time(self.current_time - skip)
            self._schedule_hide_controls()

    def _schedule_hide_controls(self):
        if self.settings.default_phone_mode:
            self._hide_controls_after(self.phone_windows_hide_control_ms)

    def _hide_controls_after(self, ms: int):
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job)
        self._con_hide_job = self.settings.control_window.after(
            ms,
            self.settings.control_window.lower
        )

    def on_slider_change(self, value):
        if self.slider_dragging:
            text = format_time(float(value))
            self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
            self.settings.update_time_overlay_position()
            if not self.entry_editing:
                self.settings.control_time_str.set(text)
            self.current_time = float(value)
            self._update_subtitle_display()#quite laggy but if you want to see the subtitles during dragging

    def on_slider_press(self, event):
        self.slider_dragging = True
    def on_slider_release(self, event):
        self.slider_dragging = False
        self.set_current_time(self.settings.slider.get())

    def simulate_video_click(self, above_window=None):
        if not self.video_click: return
        def _rect(win):
            if win is None:
                return None
            try:
                win.update_idletasks()
            except Exception:
                pass
            try:
                x = int(win.winfo_rootx())
                y = int(win.winfo_rooty())
                w = int(win.winfo_width()) or int(win.winfo_reqwidth())
                h = int(win.winfo_height()) or int(win.winfo_reqheight())
                return (x, y, x + w, y + h)
            except Exception:
                return None

        def _contains(r, x, y):
            if not r:
                return False
            l, t, rr, bb = r
            return l <= x <= rr and t <= y <= bb

        original_pos = pyautogui.position()
        try:
            screen_w, screen_h = pyautogui.size()
        except Exception:
            screen_w, screen_h = (1920, 1080)

        # Build a list of windows we want to click *outside* of so the click can land on the video.
        block_wins = [
            above_window,
            getattr(self.popup, "_popup", None),
            getattr(self.overlay, "sub_window", None),
            getattr(self.settings, "control_window", None),
            getattr(self.settings, "root", None),
        ]
        block_rects = [r for r in (_rect(w) for w in block_wins) if r]

        def _clamp(x, y):
            # Avoid corners (pyautogui FAILSAFE triggers on (0,0)).
            x = max(5, min(int(x), int(screen_w) - 5))
            y = max(5, min(int(y), int(screen_h) - 5))
            return x, y

        def _blocked(x, y):
            return any(_contains(r, x, y) for r in block_rects)

        candidates = []
        # Preferred: click above the *current mouse position*.
        # This tends to land on the video area even if the cursor is near our UI.
        try:
            mx = int(original_pos.x)
            my = int(original_pos.y) - 80
            # If a popup window is provided, ensure we click above it (not inside it).
            if above_window is not None:
                try:
                    above_window.update_idletasks()
                    popup_top = int(above_window.winfo_rooty())
                    my = min(my, popup_top - 80)
                except Exception:
                    pass
            candidates.append((mx, my))
        except Exception:
            pass

        r_popup = _rect(above_window) if above_window is not None else None
        if r_popup:
            l, t, rr, bb = r_popup
            cx = int((l + rr) / 2)
            cy = int((t + bb) / 2)
            candidates.extend([
                (cx, t - 80),        # above popup (preferred)
                (cx, bb + 80),       # below popup
                (l - 80, cy),        # left of popup
                (rr + 80, cy),       # right of popup
                (cx, t - 160),       # further above
            ])

        # Fallback: click above the control window.
        r_ctrl = _rect(getattr(self.settings, "control_window", None))
        if r_ctrl:
            l, t, rr, bb = r_ctrl
            candidates.append((int(l + 50), int(t - 80)))

        # Final fallback: a safe spot near the top-middle of the primary screen.
        candidates.append((int(screen_w / 2), 80))

        target_x, target_y = None, None
        for (cx, cy) in candidates:
            x, y = _clamp(cx, cy)
            # If this point is still blocked by one of our windows, walk upwards a bit.
            for _ in range(10):
                if not _blocked(x, y):
                    break
                x, y = _clamp(x, y - 40)
            if not _blocked(x, y):
                target_x, target_y = x, y
                break

        if target_x is None or target_y is None:
            # Worst-case: just use the first candidate.
            target_x, target_y = _clamp(*candidates[0])

        try:
            pyautogui.moveTo(target_x, target_y)
            pyautogui.click(target_x, target_y)
        except Exception:
            # Don't crash the app (and don't show a warning popup), but do log for debugging.
            print("simulate_video_click failed")
        finally:
            try:
                # Bring our UI back in front.
                self.settings.control_window.attributes("-topmost", True)
                self.settings.control_window.lift()
            except Exception:
                pass
            try:
                # Keep the popup above our other topmost windows.
                self.popup.ensure_on_top()
            except Exception:
                pass
            try:
                pyautogui.moveTo(original_pos.x, original_pos.y)
            except Exception:
                pass

    def _on_global_click(self, x, y, button, pressed):
        if button == Button.x2 and pressed:
            self.renderer.canvas.delete("all")
            # Keep last_subtitle_text intact so _update_subtitle_display() won't immediately redraw
            # the same subtitle on the next timer tick. It will render again once the subtitle changes.
            self.subtitle_deleted = True


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
        try:
            self.popup.ensure_on_top()
        except Exception:
            pass


    # ——— Hide window logic —————————————————————————————————————

    def sub_window_enter(self, event):
        self.overlay.sub_window.attributes("-transparentcolor", "") #not transparent
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        try:
            self.popup.ensure_on_top()
        except Exception:
            pass
        if getattr(self, "_con_hide_job", None) is not None:
            self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
            self._con_hide_job = None
        self.subtitle_deleted = False

    def sub_window_leave(self, event):
        self.overlay.sub_window.attributes("-transparentcolor", "grey")
        if not self.settings.default_phone_mode:
            self._hide_controls_after(self.windows_hide_control_ms)

    def sub_handel_enter(self, event):
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        try:
            self.popup.ensure_on_top()
        except Exception:
            pass
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
            self._con_hide_job = None
        self.subtitle_deleted = False

    def control_window_enter(self, event):
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        try:
            self.popup.ensure_on_top()
        except Exception:
            pass
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
            self._con_hide_job = None
        self.subtitle_deleted = False

    def control_window_leave(self, event):
        delay = (self.phone_windows_hide_control_ms
                 if self.settings.default_phone_mode
                 else self.windows_hide_control_ms)
        self._hide_controls_after(delay)

    def show_subtitle_handle(self, is_phone):
        if is_phone:
            self.overlay.show_handle()
        else:
            self.overlay.hide_handle()

    def _on_app_close(self):
        # Persist window positions/state before destroying any windows.
        try:
            x, y = self.settings.root.winfo_x(), self.settings.root.winfo_y()
            if (x, y) != (self.config.get("LAST_SETTINGS_WINDOW_X"),
                          self.config.get("LAST_SETTINGS_WINDOW_Y")):
                self.config.set("LAST_SETTINGS_WINDOW_X", x)
                self.config.set("LAST_SETTINGS_WINDOW_Y", y)
        except Exception:
            pass
        try:
            self.settings.save_state()  # control window position
        except Exception:
            pass
        try:
            self.overlay.save_state()   # subtitle overlay center position
        except Exception:
            pass
        try:
            self.sub_manager.save_state()
        except Exception:
            pass

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
