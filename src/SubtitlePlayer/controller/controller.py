"""
Controller glue between:
- SubtitleManager (model)
- SettingsUI + SubtitleOverlayUI + CopyPopup (view)
- SubtitleRenderer (rendering)

Handles input (buttons, keyboard, global mouse), time updates, and episode changes.
"""

import time
import threading
import queue
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
    SHORTCUT_DEFAULTS = {
        "SHORTCUT_TOGGLE_PLAY": "space",
        "SHORTCUT_GO_BACK": "left",
        "SHORTCUT_GO_FORWARD": "right",
        "SHORTCUT_SUBTITLE_BACK": "shift+left",
        "SHORTCUT_SUBTITLE_FORWARD": "shift+right",
        "SHORTCUT_MODE2_TOGGLE_PLAY": "numpad0",
        "SHORTCUT_MODE2_GO_BACK": "4",
        "SHORTCUT_MODE2_GO_FORWARD": "6",
        "SHORTCUT_MODE2_SUBTITLE_BACK": "alt+4",
        "SHORTCUT_MODE2_SUBTITLE_FORWARD": "alt+6",
        "SHORTCUT_BRING_TO_FRONT": "alt+x",
        "SHORTCUT_EPISODE_INC": "alt+c",
        "SHORTCUT_EPISODE_DEC": "alt+y",
        "SHORTCUT_JUMP_SUB_END": "ctrl+shift+y",
    }
    
    
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
        self.ctrl_pressed = False
        self.shift_pressed = False
        self._single_fire_actions = set()
        self.subtitle_timeout_job = None
        self.last_subtitle_text = ""
        self.last_subtitle_raw = ""
        self.sub_hidden = False
        self.slider_dragging = False
        self.last_rendered_sub_time = None
        self._shutting_down = False
        self._input_actions: "queue.Queue[str]" = queue.Queue()
        self._input_pump_job = None

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
        self.settings.bind_advanced_apply            (self.apply_advanced_settings)

        self.settings.bind_update_display            (self.update_time_and_subtitle_displays)
        
        self.overlay.subtitle_canvas.bind("<Button-3>", self._on_copy_popup)
        self.popup.bind_add_to_anki(self._add_selection_to_anki)
        self.overlay.bind_sub_window_enter(self.sub_window_enter)
        self.overlay.bind_sub_window_leave(self.sub_window_leave)
        self.overlay.bind_sub_handel_enter(self.sub_handel_enter)   


        self._mouse_listener = MouseListener(on_click=self._on_global_click)
        self._mouse_listener.start()
        self._keyboard_listener = KeyboardListener(on_press=self._on_key_press, on_release=self._on_key_release)
        self._keyboard_listener.start()
        self._input_pump_job = self.settings.root.after(15, self._process_input_queue)

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
            selected = (selected_text or "").strip()

            def worker():
                if not self.anki.ping():
                    print("AnkiConnect not reachable. Start Anki + AnkiConnect and try again.")
                    return

                started = time.perf_counter()

                try:
                    result = self.anki.add_from_selection(
                        selection_text=selected,
                        subtitle_text=subtitle_text,
                    )

                    elapsed = time.perf_counter() - started
                    print(f"Anki note created in {elapsed:.2f}s")

                    candidates = result.get("translation_candidates") or {}
                    word_cands = candidates.get("word") or {}
                    sentence_cands = candidates.get("sentence") or {}

                    print(f"Note ID: {result.get('note_id', '')}")
                    print(f"Marked Word: {selected}")
                    print(f"Word Jisho: {self._format_translation_csv(word_cands.get('jisho', ''))}")
                    print(f"Word Google: {self._format_translation_csv(word_cands.get('google', ''))}")
                    print(f"Sentence DeepL: {self._format_translation_csv(sentence_cands.get('deepl', ''))}")
                    print(f"Sentence Google: {self._format_translation_csv(sentence_cands.get('google', ''))}")

                    fields = result.get("stroke_svg_sync_fields")

                    if fields:
                        self.anki.sync_missing_stroke_svgs_async(selected, fields)

                except Exception as e:
                    print(f"Anki add failed: {e}")

            threading.Thread(target=worker, daemon=True).start()
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

    def apply_advanced_settings(self, values: dict, persist: bool = False) -> None:
        if not isinstance(values, dict):
            return

        # Keep the in-memory config in sync so model/view code that reads config.get(...)
        # picks up values immediately (even without saving to disk).
        try:
            cfg = getattr(self.config, "config", None)
            if isinstance(cfg, dict):
                cfg.update(values)
        except Exception:
            pass

        def _as_int(key: str, default: int) -> int:
            try:
                return int(values.get(key, default))
            except Exception:
                return int(default)

        self.update_interval_ms = max(15, _as_int("UPDATE_INTERVAL_MS", self.update_interval_ms))
        self.hide_subtitles_ms = max(100, _as_int("SUBTITLE_TIMEOUT_MS", self.hide_subtitles_ms))
        self.windows_hide_control_ms = max(100, _as_int("WINDOWS_HIDE_DELAY_MS", self.windows_hide_control_ms))
        self.phone_windows_hide_control_ms = max(100, _as_int("PHONEMODE_WINDOWS_HIDE_DELAY_MS", self.phone_windows_hide_control_ms))

        if "VIDEO_CLICK" in values:
            try:
                self.video_click = bool(values.get("VIDEO_CLICK"))
            except Exception:
                pass

        if "POPUP_CLOSE_TIMER" in values:
            try:
                self.popup.close_delay = max(100, int(values.get("POPUP_CLOSE_TIMER")))
                popup = getattr(self.popup, "_popup", None)
                if popup is not None and popup.winfo_exists():
                    if (not getattr(self.popup, "_pinned", False)
                            and not getattr(self.popup, "_menu_open", False)
                            and not getattr(self.popup, "_dragging", False)):
                        self.popup._restart_close()
            except Exception:
                pass

        # Popup style fields apply immediately for newly opened popups, and update the
        # currently open popup widget where possible.
        try:
            if "POPUP_FONT" in values:
                self.popup.font_name = str(values.get("POPUP_FONT") or self.popup.font_name)
            if "POPUP_FONT_COLOR" in values:
                self.popup.font_color = str(values.get("POPUP_FONT_COLOR") or self.popup.font_color)
            if "POPUP_BG_COLOR" in values:
                self.popup.bg_color = str(values.get("POPUP_BG_COLOR") or self.popup.bg_color)
            if "POPUP_FONT_SIZE" in values:
                self.popup.font_size = max(8, int(values.get("POPUP_FONT_SIZE")))

            popup_win = getattr(self.popup, "_popup", None)
            entry = getattr(self.popup, "_entry_widget", None)
            if popup_win is not None and popup_win.winfo_exists():
                try:
                    popup_win.configure(bg=self.popup.bg_color)
                except Exception:
                    pass
            if entry is not None:
                try:
                    entry.configure(
                        bg=self.popup.bg_color,
                        fg=self.popup.font_color,
                        font=(self.popup.font_name, self.popup.font_size, "bold"),
                    )
                except Exception:
                    pass
        except Exception:
            pass

        # Subtitle style fields are read by renderer on each draw; trigger a refresh now.
        subtitle_style_keys = {
            "SUBTITLE_FONT",
            "SUBTITLE_FONT_SIZE",
            "SUBTITLE_COLOR",
            "SUBTITLE_WRAP_LIMIT_PX",
            "GLOW_COLOR",
            "GLOW_RADIUS",
        }
        if any(k in values for k in subtitle_style_keys):
            try:
                self.last_subtitle_text = ""
                self.update_time_and_subtitle_displays()
            except Exception:
                pass

        if "SUBTITLE_AUTO_RUBY" in values:
            try:
                srt_path = getattr(self.sub_manager, "srt_file", None)
                if srt_path:
                    self.sub_manager.set_subtitle_display_data(srt_path)
                    self.last_subtitle_text = ""
                    self.update_time_and_subtitle_displays()
            except Exception:
                pass

        startup_value_keys = {"DEFAULT_START_TIME", "EXTRA_OFFSET", "DEFAULT_SKIP"}
        if any(k in values for k in startup_value_keys):
            try:
                self.default_start_time = float(values.get("DEFAULT_START_TIME", self.default_start_time))
            except Exception:
                pass
            try:
                if "EXTRA_OFFSET" in values:
                    off = float(values.get("EXTRA_OFFSET"))
                    self.default_offset = off
                    self.settings._last_offset_value = off
                    self.settings.offset_var.set(f"{self.settings._format_number(off)} s")
                    self.settings._apply_offset_change(off, persist=False)
            except Exception:
                pass
            try:
                if "DEFAULT_SKIP" in values:
                    skip = float(values.get("DEFAULT_SKIP"))
                    self.default_skip = skip
                    self.settings._last_skip_value = skip
                    self.settings.skip_var.set(f"{self.settings._format_number(skip)} s")
            except Exception:
                pass

        if "SHORTCUTS_DISABLED" in values:
            try:
                self.settings.set_hotkeys_disabled(bool(values.get("SHORTCUTS_DISABLED")))
            except Exception:
                pass
            if self._hotkeys_disabled():
                self._reset_hotkey_state()

        if any(str(k).startswith("ANKI_") for k in values.keys()):
            try:
                self.anki = AnkiClient(self.config)
                self.anki_busy_cursor = (self.config.get("ANKI_BUSY_CURSOR") or "wait")
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
        def _restore_entry():
            if self.sub_manager.current_episode is None:
                self.settings.episode_var.set("Movie")
            else:
                self.settings.episode_var.set(str(self.sub_manager.current_episode))

        raw = self.settings.episode_var.get().strip()
        if action in ("inc", "dec"):
            target_season, target_episode = self.sub_manager.change_episode(action)
            if target_episode is not None:
                self.settings.episode_var.set(str(target_episode))
                self._after_episode_change()
            else:
                _restore_entry()
            return

        if not raw:
            _restore_entry()
            return
        if raw.lower() == 'movie':
            return

        raw_int = None
        season_hint = None
        parsed_global = None
        try:
            candidate = int(raw)
            if candidate > 0:
                raw_int = candidate
        except ValueError:
            try:
                parsed_s, parsed_e, parsed_g = self.sub_manager.extract_season_episode_global(raw)
            except Exception:
                parsed_s, parsed_e, parsed_g = None, None, None
            if parsed_s is not None and parsed_e is not None:
                season_hint = int(parsed_s)
                raw_int = int(parsed_e)
                parsed_global = int(parsed_g) if parsed_g is not None else None
            elif parsed_g is not None:
                raw_int = int(parsed_g)

        if raw_int is None or raw_int <= 0:
            _restore_entry()
            return

        before = (
            getattr(self.sub_manager, "srt_file", None),
            getattr(self.sub_manager, "current_season", None),
            getattr(self.sub_manager, "current_episode", None),
        )
        target_season, target_episode = self.sub_manager.change_episode("set", raw_int, season_hint)
        after = (
            getattr(self.sub_manager, "srt_file", None),
            getattr(self.sub_manager, "current_season", None),
            getattr(self.sub_manager, "current_episode", None),
        )

        # Fallback: if explicit SxxEyy did not resolve, and parser also gave a global
        # candidate, try the global target once.
        if before == after and season_hint is not None and parsed_global is not None and parsed_global > 0:
            target_season, target_episode = self.sub_manager.change_episode("set", parsed_global, None)

        if target_episode is not None:
            self.settings.episode_var.set(str(target_episode))
            self._after_episode_change() #reset all with new srt data
        else: #change not allowed
            _restore_entry()

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
        max_time = self.total_duration + float(self.settings._last_offset_value or 0.0)
        if self.current_time <= max_time:
            self.set_current_time(self.current_time + skip)
            self._schedule_hide_controls()

    def go_back(self):
        skip = self.settings._last_skip_value
        if self.current_time >= 0:
            self.set_current_time(self.current_time - skip)
            self._schedule_hide_controls()

    def on_jump_sub_end(self, event=None):
        start_times = [item[1] for item in getattr(self.sub_manager, "display_data", [])]
        if not start_times:
            return

        offset = float(self.settings._last_offset_value or 0.0)
        sub_t = max(0.0, float(self.current_time) - offset)
        epsilon = 0.05

        idx = bisect.bisect_right(start_times, sub_t + epsilon) - 1
        if idx < 0 or idx >= len(self.sub_manager.subtitles):
            return

        sub = self.sub_manager.subtitles[idx]

        padding = 0.1  # 100 ms
        target_time = sub.end.total_seconds() + padding + offset

        self.set_current_time(target_time)
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
            self._enqueue_input_action("clear_subtitle")


    # ——— Keyboard handlers —————————————————————————————————————
    def _enqueue_input_action(self, action: str) -> None:
        if self._shutting_down:
            return
        try:
            self._input_actions.put_nowait(action)
        except Exception:
            pass

    def _process_input_queue(self):
        if self._shutting_down:
            return
        try:
            for _ in range(50):
                try:
                    action = self._input_actions.get_nowait()
                except queue.Empty:
                    break
                self._dispatch_input_action(action)
        finally:
            try:
                self._input_pump_job = self.settings.root.after(15, self._process_input_queue)
            except Exception:
                self._input_pump_job = None

    def _dispatch_input_action(self, action: str) -> None:
        if action == "toggle_play":
            self.toggle_play()
        elif action == "go_back":
            self.go_back()
        elif action == "go_forward":
            self.go_forward()
        elif action == "subtitle_back":
            self.jump_subtitle_segment("prev")
        elif action == "subtitle_forward":
            self.jump_subtitle_segment("next")
        elif action == "jump_sub_end":
            self.on_jump_sub_end()
        elif action == "alt_x":
            self.on_alt_x()
        elif action == "episode_inc":
            self.change_episode("inc")
        elif action == "episode_dec":
            self.change_episode("dec")
        elif action == "clear_subtitle":
            self.renderer.canvas.delete("all")
            # Keep last_subtitle_text intact so _update_subtitle_display() won't immediately redraw
            # the same subtitle on the next timer tick. It will render again once the subtitle changes.
            self.subtitle_deleted = True

    def jump_subtitle_segment(self, direction: str) -> None:
        """
        Jump to subtitle boundaries:
        - prev: start of current segment (or previous if already at boundary)
        - next: start of next segment
        """
        start_times = [item[1] for item in getattr(self.sub_manager, "display_data", [])]
        if not start_times:
            return

        offset = float(self.settings._last_offset_value or 0.0)
        sub_t = max(0.0, float(self.current_time) - offset)
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
        self._schedule_hide_controls()

    @staticmethod
    def _is_numpad_vk_key(key, *codes: int) -> bool:
        try:
            return hasattr(key, "vk") and int(getattr(key, "vk")) in codes
        except Exception:
            return False

    def _get_shortcut_value(self, config_key: str) -> str:
        default = self.SHORTCUT_DEFAULTS.get(config_key, "")
        raw = self.config.get(config_key)
        if not isinstance(raw, str) or not raw.strip():
            value = default
        else:
            value = raw
        value = str(value).strip().lower()
        if config_key == "SHORTCUT_TOGGLE_PLAY":
            try:
                if bool(self.config.get("DISABLE_SPACE_HOTKEY") or False):
                    if self._normalize_shortcut_token(value) == "space":
                        return ""
            except Exception:
                pass
        return value

    @staticmethod
    def _normalize_shortcut_token(token: str) -> str:
        t = str(token or "").strip().lower().replace(" ", "")
        alias = {
            "arrowleft": "left",
            "arrowright": "right",
            "spacebar": "space",
            "ins": "insert",
            "num0": "numpad0",
            "np0": "numpad0",
            "num4": "numpad4",
            "np4": "numpad4",
            "num6": "numpad6",
            "np6": "numpad6",
        }
        return alias.get(t, t)

    def _split_shortcut(self, binding: str):
        text = str(binding or "").strip().lower().replace(" ", "")
        if not text:
            return set(), None
        parts = [p for p in text.split("+") if p]
        mods = set()
        key_token = None
        for p in parts:
            token = self._normalize_shortcut_token(p)
            if token in {"shift", "alt", "ctrl"}:
                mods.add(token)
            elif key_token is None:
                key_token = token
        return mods, key_token

    def _key_tokens(self, key) -> set[str]:
        tokens = set()

        def _add(*vals):
            for v in vals:
                if v:
                    tokens.add(str(v).lower())

        if key == Key.space:
            _add("space")
        if key == Key.left:
            _add("left")
        if key == Key.right:
            _add("right")
        if key == Key.insert:
            _add("insert")
        if hasattr(key, "char") and key.char:
            ch = key.char
            # Convert Ctrl-letter control codes (e.g. '\x19') back to letters
            if len(ch) == 1 and ord(ch) < 32:
                ch = chr(ord(ch) + 96)
            _add(ch.lower())

        if self._is_numpad_vk_key(key, 96, 45):
            _add("numpad0", "insert", "0")
        if self._is_numpad_vk_key(key, 100):
            _add("numpad4", "left", "4")
        if self._is_numpad_vk_key(key, 102):
            _add("numpad6", "right", "6")

        return {self._normalize_shortcut_token(t) for t in tokens}

    def _shortcut_matches(self, binding: str, key) -> bool:
        mods, key_token = self._split_shortcut(binding)
        if not key_token:
            return False

        if "shift" in mods and not self.shift_pressed:
            return False
        if "alt" in mods and not self.alt_pressed:
            return False
        if "ctrl" in mods and not self.ctrl_pressed:
            return False

        return key_token in self._key_tokens(key)

    def _single_fire_bindings(self):
        return [
            ("toggle_play", self._get_shortcut_value("SHORTCUT_TOGGLE_PLAY")),
            ("toggle_play", self._get_shortcut_value("SHORTCUT_MODE2_TOGGLE_PLAY")),
            ("alt_x", self._get_shortcut_value("SHORTCUT_BRING_TO_FRONT")),
            ("episode_inc", self._get_shortcut_value("SHORTCUT_EPISODE_INC")),
            ("episode_dec", self._get_shortcut_value("SHORTCUT_EPISODE_DEC")),
            ("jump_sub_end", self._get_shortcut_value("SHORTCUT_JUMP_SUB_END")),
        ]

    def _hotkeys_disabled(self) -> bool:
        try:
            if int(getattr(self.settings, "input_mode", 1)) == 3:
                return True
        except Exception:
            pass
        try:
            return bool(self.config.get("SHORTCUTS_DISABLED") or False)
        except Exception:
            return False

    def _reset_hotkey_state(self) -> None:
        self.shift_pressed = False
        self.alt_pressed = False
        self.ctrl_pressed = False
        self._single_fire_actions.clear()

    # def _on_key_press(self, key):
    #     # don't return for modifier presses — only update flags
    #     if self._hotkeys_disabled():
    #         self._reset_hotkey_state()
    #         return

    #     if key in (Key.shift_l, Key.shift_r):
    #         self.shift_pressed = True
    #     if key in (Key.alt_l, Key.alt_r):
    #         self.alt_pressed = True
    #     if key in (Key.ctrl_l, Key.ctrl_r):
    #         self.ctrl_pressed = True

    #     try:
    #         mode2_numpad = int(getattr(self.settings, "input_mode", 1)) == 2
    #     except Exception:
    #         mode2_numpad = bool(getattr(self.settings, "numpad_mode_enabled", False))

    #     if mode2_numpad:
    #         mapping = [
    #             (self._get_shortcut_value("SHORTCUT_MODE2_TOGGLE_PLAY"), "toggle_play", True),
    #             (self._get_shortcut_value("SHORTCUT_TOGGLE_PLAY"), "toggle_play", True),
    #             (self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_BACK"), "subtitle_back", False),
    #             (self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_FORWARD"), "subtitle_forward", False),
    #             (self._get_shortcut_value("SHORTCUT_MODE2_GO_BACK"), "go_back", False),
    #             (self._get_shortcut_value("SHORTCUT_MODE2_GO_FORWARD"), "go_forward", False),
    #         ]
    #     else:
    #         mapping = [
    #             (self._get_shortcut_value("SHORTCUT_TOGGLE_PLAY"), "toggle_play", True),
    #             (self._get_shortcut_value("SHORTCUT_SUBTITLE_BACK"), "subtitle_back", False),
    #             (self._get_shortcut_value("SHORTCUT_SUBTITLE_FORWARD"), "subtitle_forward", False),
    #             (self._get_shortcut_value("SHORTCUT_GO_BACK"), "go_back", False),
    #             (self._get_shortcut_value("SHORTCUT_GO_FORWARD"), "go_forward", False),
    #         ]

    #     # keep jump_sub_end out of the press-mapping; we'll trigger on release
    #     mapping.extend([
    #         (self._get_shortcut_value("SHORTCUT_BRING_TO_FRONT"), "alt_x", True),
    #         (self._get_shortcut_value("SHORTCUT_EPISODE_INC"), "episode_inc", True),
    #         (self._get_shortcut_value("SHORTCUT_EPISODE_DEC"), "episode_dec", True),
    #     ])

    #     for binding, action, single_fire in mapping:
    #         if not self._shortcut_matches(binding, key):
    #             continue
    #         if single_fire and action in self._single_fire_actions:
    #             return
    #         if single_fire:
    #             self._single_fire_actions.add(action)
    #         self._enqueue_input_action(action)
    #         return
        
    # def _on_key_release(self, key):
    #     if self._hotkeys_disabled():
    #         self._reset_hotkey_state()
    #         return

    #     # capture modifier state BEFORE clearing it
    #     was_shift = self.shift_pressed
    #     was_ctrl = self.ctrl_pressed
    #     was_alt = self.alt_pressed

    #     # figure out released character (if any)
    #     released_char = None
    #     try:
    #         if hasattr(key, "char") and key.char:
    #             released_char = str(key.char).lower()
    #     except Exception:
    #         released_char = None

    #     # trigger our combo on release: ctrl+shift + release of 'y'
    #     if released_char == "y" and was_shift and was_ctrl:
    #         # ensure this action can be repeated each time (don't mark as single-fire here)
    #         self._enqueue_input_action("jump_sub_end")

    #     # now clear modifier flags if modifiers were actually released
    #     if key in (Key.shift_l, Key.shift_r):
    #         self.shift_pressed = False
    #     if key in (Key.alt_l, Key.alt_r):
    #         self.alt_pressed = False
    #     if key in (Key.ctrl_l, Key.ctrl_r):
    #         self.ctrl_pressed = False

    #     # cleanup for single-fire actions when their key token is released
    #     released_tokens = self._key_tokens(key)
    #     for action, binding in self._single_fire_bindings():
    #         _, key_token = self._split_shortcut(binding)
    #         if key_token and key_token in released_tokens:
    #             self._single_fire_actions.discard(action)




    def _on_key_press(self, key):
        if self._hotkeys_disabled():
            self._reset_hotkey_state()
            return

        if key in (Key.shift_l, Key.shift_r):
            self.shift_pressed = True
            return
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = True
            return
        if key in (Key.ctrl_l, Key.ctrl_r):
            self.ctrl_pressed = True
            return

        try:
            mode2_numpad = int(getattr(self.settings, "input_mode", 1)) == 2
        except Exception:
            mode2_numpad = bool(getattr(self.settings, "numpad_mode_enabled", False))
        if mode2_numpad:
            mapping = [
                (self._get_shortcut_value("SHORTCUT_MODE2_TOGGLE_PLAY"), "toggle_play", True),
                (self._get_shortcut_value("SHORTCUT_TOGGLE_PLAY"), "toggle_play", True),
                (self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_BACK"), "subtitle_back", False),
                (self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_FORWARD"), "subtitle_forward", False),
                (self._get_shortcut_value("SHORTCUT_MODE2_GO_BACK"), "go_back", False),
                (self._get_shortcut_value("SHORTCUT_MODE2_GO_FORWARD"), "go_forward", False),
            ]
        else:
            mapping = [
                (self._get_shortcut_value("SHORTCUT_TOGGLE_PLAY"), "toggle_play", True),
                (self._get_shortcut_value("SHORTCUT_SUBTITLE_BACK"), "subtitle_back", False),
                (self._get_shortcut_value("SHORTCUT_SUBTITLE_FORWARD"), "subtitle_forward", False),
                (self._get_shortcut_value("SHORTCUT_GO_BACK"), "go_back", False),
                (self._get_shortcut_value("SHORTCUT_GO_FORWARD"), "go_forward", False),
            ]

        mapping.extend([
            (self._get_shortcut_value("SHORTCUT_BRING_TO_FRONT"), "alt_x", True),
            (self._get_shortcut_value("SHORTCUT_EPISODE_INC"), "episode_inc", True),
            (self._get_shortcut_value("SHORTCUT_EPISODE_DEC"), "episode_dec", True),
            (self._get_shortcut_value("SHORTCUT_JUMP_SUB_END"), "jump_sub_end", True),
        ])

        for binding, action, single_fire in mapping:
            if not self._shortcut_matches(binding, key):
                continue
            if single_fire and action in self._single_fire_actions:
                return
            if single_fire:
                self._single_fire_actions.add(action)
            self._enqueue_input_action(action)
            return

    def _on_key_release(self, key):
        if self._hotkeys_disabled():
            self._reset_hotkey_state()
            return

        if key in (Key.shift_l, Key.shift_r):
            self.shift_pressed = False
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = False
        if key in (Key.ctrl_l, Key.ctrl_r):
            self.ctrl_pressed = False

        released_tokens = self._key_tokens(key)
        for action, binding in self._single_fire_bindings():
            _, key_token = self._split_shortcut(binding)
            if key_token and key_token in released_tokens:
                self._single_fire_actions.discard(action)

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
        self._shutting_down = True
        def _read_settings_geometry():
            try:
                geo = self.settings.root.winfo_geometry()
                size, pos = geo.split("+", 1)
                w_s, h_s = size.split("x", 1)
                x_s, y_s = pos.split("+", 1)
                return int(x_s), int(y_s), int(w_s), int(h_s)
            except Exception:
                try:
                    return (
                        int(self.settings.root.winfo_x()),
                        int(self.settings.root.winfo_y()),
                        int(self.settings.root.winfo_width()),
                        int(self.settings.root.winfo_height()),
                    )
                except Exception:
                    return None
        # Persist window positions/state before destroying any windows.
        try:
            geom = _read_settings_geometry()
            if geom is None:
                raise ValueError("Could not read settings geometry")
            x, y, w, h = geom
            if (x, y) != (self.config.get("LAST_SETTINGS_WINDOW_X"),
                          self.config.get("LAST_SETTINGS_WINDOW_Y")):
                self.config.set("LAST_SETTINGS_WINDOW_X", x)
                self.config.set("LAST_SETTINGS_WINDOW_Y", y)
            if (w, h) != (self.config.get("LAST_SETTINGS_WINDOW_WIDTH"),
                          self.config.get("LAST_SETTINGS_WINDOW_HEIGHT")):
                self.config.set("LAST_SETTINGS_WINDOW_WIDTH", w)
                self.config.set("LAST_SETTINGS_WINDOW_HEIGHT", h)
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

        for job in ("subtitle_timeout_job", "_con_hide_job", "_input_pump_job"):
            handle = getattr(self, job, None)
            if handle is not None:
                try:
                    self.settings.root.after_cancel(handle)
                except Exception:
                    pass
        for listener_attr in ("_mouse_listener", "_keyboard_listener"):
            listener = getattr(self, listener_attr, None)
            if listener is not None:
                try:
                    listener.stop()
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
