"""
Controller glue between:
- SubtitleManager (model)
- SettingsUI + SubtitleOverlayUI + CopyPopup (view)
- SubtitleRenderer (rendering)

Handles input (buttons, keyboard, global mouse), time updates, and episode changes.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from typing import Any

from pynput.keyboard import Listener as KeyboardListener
from pynput.mouse import Listener as MouseListener

from model.anki_client import AnkiClient
from model.config_manager import ConfigManager
from model.renderer import SubtitleRenderer
from model.subtitle_manager import SubtitleManager
from view.popup import CopyPopup
from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI

from controller.anki_controller import AnkiController
from controller.episode_controller import EpisodeController
from controller.hotkey_controller import HotkeyController
from controller.ocr_controller import OCRController
from controller.overlay_controller import OverlayController
from controller.playback_controller import PlaybackController
from controller.subtitle_navigation import SubtitleNavigationController

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
    HOTKEY_DISABLE_KEYS = {
        "toggle_play": "DISABLE_HOTKEY_TOGGLE_PLAY",
        "go_back": "DISABLE_HOTKEY_GO_BACK",
        "go_forward": "DISABLE_HOTKEY_GO_FORWARD",
        "subtitle_back": "DISABLE_HOTKEY_SUBTITLE_BACK",
        "subtitle_forward": "DISABLE_HOTKEY_SUBTITLE_FORWARD",
        "jump_sub_end": "DISABLE_HOTKEY_JUMP_SUB_END",
    }
    OCR_TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)[/\\|](\d{1,2}:\d{2}(?::\d{2})?)")
    
    def __init__(self,
                config: ConfigManager,
                manager: SubtitleManager,
                renderer: SubtitleRenderer,
                settings_ui: SettingsUI,
                overlay_ui: SubtitleOverlayUI,
                popup: CopyPopup,
                total_duration: float = 0.0):
        
        self.config  = config
        self.sub_manager = manager
        self.renderer = renderer
        self.settings = settings_ui
        self.overlay = overlay_ui
        self.popup   = popup
        self.total_duration = total_duration


        self._init_runtime_state()
        self._create_services_and_controllers()
        self._bind_ui_events()
        self._start_input_listeners()

        self.episode_controller.restore_startup_time_and_mode()

        self.last_update = time.time()
        self.playback.set_current_time(float(self.current_time or 0.0))
        self.update_episode_nav_controls()
        self.ocr_controller._schedule_ocr_time_jump("startup")
        if self._startup_resume_play: self.playback.toggle_play()

    def _init_runtime_state(self) -> None:
        self.default_start_time = self.current_time = self.config.get("DEFAULT_START_TIME")
        self._startup_resume_play = self.config.get("STARTUP_RESUME_PLAY")
        self.default_skip = self.config.get("DEFAULT_SKIP")
        self.default_offset = self.config.get("EXTRA_OFFSET")
        self.audio_padding = self.config.get("AUDIO_PADDING")
        self.phone_windows_hide_control_ms = self.config.get("PHONEMODE_WINDOWS_HIDE_DELAY_MS")
        self.windows_hide_control_ms = self.config.get("WINDOWS_HIDE_DELAY_MS")
        self.hide_subtitles_ms = self.config.get("SUBTITLE_TIMEOUT_MS")
        self.update_interval_ms = self.config.get("UPDATE_INTERVAL_MS")
        self.anki_busy_cursor = self.config.get("ANKI_BUSY_CURSOR") or "wait"

        self.playing = False
        self.entry_editing = False
        self.subtitle_deleted = False
        self.alt_pressed = False
        self.ctrl_pressed = False
        self.shift_pressed = False
        self.sub_hidden = False
        self.slider_dragging = False
        self._shutting_down = False

        self._single_fire_actions: set[str] = set()
        self._input_actions: "queue.Queue[str]" = queue.Queue()
        self._input_pump_job = None
        self._repeat_job = None
        self._repeat_lock = threading.Lock()
        self._held_repeat_next_fire: dict[str, float] = {}
        self._held_repeat_fired: set[str] = set()
        self._repeat_initial_delay_sec = 0.22
        self._repeat_interval_sec = 0.04
        self._pending_seek_delta = 0.0

        self._ocr_job = None
        self._ocr_thread = None
        self._ocr_generation = 0
        self._ocr_pending_time = None
        self._ocr_sync_generation = 0
        self._ocr_sync_thread = None

        self._anki_wait_window = None
        self._anki_wait_status_var = None
        self._anki_wait_thread = None
        self._pending_anki_payload = None
        self._anki_success_popup = None
        self._anki_success_popup_job = None

        self.subtitle_timeout_job = None
        self.last_subtitle_text = ""
        self.last_subtitle_raw = ""
        self.last_rendered_index = None
        self.last_rendered_sub_time = None

    def _create_services_and_controllers(self) -> None:
        self.playback = PlaybackController(self)
        self.subtitle_navigation = SubtitleNavigationController(self)
        self.episode_controller = EpisodeController(self)
        self.overlay_controller = OverlayController(self)
        self.hotkey_controller = HotkeyController(self)
        self.anki_controller = AnkiController(self)
        self.ocr_controller = OCRController(self)
        self.anki = AnkiClient(self.config)

    def _bind_ui_events(self) -> None:
        self.settings.root.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.settings.bind_play_pause(self.playback.toggle_play)
        self.settings.bind_back(self.playback.go_back)
        self.settings.bind_forward(self.playback.go_forward)
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
        self.settings.bind_open_srt                 (self.episode_controller.on_open_srt)
        self.settings.bind_set_to_return            (self.subtitle_navigation.on_set_to_return)
        self.settings.bind_time_entry_return        (self.subtitle_navigation.control_time_entry_return)
        self.settings.bind_time_entry_clear         (self.subtitle_navigation.control_clear_time_entry)
        self.settings.bind_control_window_enter     (self.overlay_controller.control_window_enter)
        self.settings.bind_control_window_leave     (self.overlay_controller.control_window_leave)
        self.settings.bind_show_subtitle_handle     (self.overlay_controller.show_subtitle_handle)
        self.settings.bind_refresh_subtitles        (self.subtitle_navigation.on_refresh_subtitles)
        self.settings.bind_advanced_apply           (self.apply_advanced_settings)
        self.settings.bind_ocr_read_now             (self.ocr_controller.on_ocr_read_now)
        self.settings.bind_ocr_sync_now             (self.ocr_controller.on_ocr_sync_now)
        self.settings.bind_anki_check               (self.anki_controller.on_anki_check_connection)
        self.settings.bind_settings_open            (self._hide_subtitle_handle_for_settings)
        self.settings.bind_update_display           (self.update_time_and_subtitle_displays)
        self.overlay.subtitle_canvas.bind           ("<Button-3>", self._on_copy_popup)
        self.popup.bind_add_to_anki                 (self._add_selection_to_anki)
        self.overlay.bind_sub_window_enter          (self.sub_window_enter)
        self.overlay.bind_sub_window_leave          (self.sub_window_leave)
        self.overlay.bind_sub_handle_enter          (self.sub_handle_enter)   
        self.settings.root.bind                     ("<Enter>", lambda _e: self._hide_subtitle_handle_for_settings(), add="+")
        self.settings.root.bind                     ("<Leave>", lambda _e: self._restore_subtitle_handle_after_settings(), add="+")

    def _start_input_listeners(self) -> None:
        self._mouse_listener = MouseListener(on_click=self.hotkey_controller._on_global_click)
        self._mouse_listener.start()
        self._keyboard_listener = KeyboardListener(on_press=self._on_key_press, on_release=self._on_key_release)
        self._keyboard_listener.start()
        self._input_pump_job = self.settings.root.after(15, self.hotkey_controller._process_input_queue)
        self._repeat_job =     self.settings.root.after(16, self.hotkey_controller._process_repeat_actions)

    # ---------------------------------------------------------------------
    # Small delegation helpers
    # ---------------------------------------------------------------------
    def get_offset_value(self) -> float:
        return self.subtitle_navigation.get_offset_value()

    def update_episode_nav_controls(self) -> None:
        return self.episode_controller.update_episode_nav_controls()

    def _get_display_start_times(self):
        return self.episode_controller._get_display_start_times()

    def _on_copy_popup(self, event=None):
        self.popup.open_copy_popup(self.last_subtitle_raw)
        return "break"

    def _skip_buttons_use_subtitle_segments(self) -> bool:
        return self.hotkey_controller._skip_buttons_use_subtitle_segments()

    def _add_selection_to_anki(self, selected_text: str, subtitle_text: str = "") -> None:
        return self.anki_controller._add_selection_to_anki(selected_text, subtitle_text)

    def _set_busy_cursor(self, busy: bool) -> None:
        return self.anki_controller._set_busy_cursor(busy)

    def _hotkeys_disabled(self) -> bool:
        return self.hotkey_controller._hotkeys_disabled()

    def _reset_hotkey_state(self) -> None:
        return self.hotkey_controller._reset_hotkey_state()

    # ---------------------------------------------------------------------
    # Runtime config changes
    # ---------------------------------------------------------------------
    def apply_advanced_settings(self, values: dict) -> None:
        if not isinstance(values, dict):
            return

        cfg = getattr(self.config, "config", None)
        if isinstance(cfg, dict):
            cfg.update(values)

        def _as_int(key: str, default: int) -> int:
            try:
                return int(values.get(key, default))
            except Exception:
                return int(default)

        self.update_interval_ms = max(15, _as_int("UPDATE_INTERVAL_MS", self.update_interval_ms))
        self.hide_subtitles_ms = max(100, _as_int("SUBTITLE_TIMEOUT_MS", self.hide_subtitles_ms))
        self.windows_hide_control_ms = max(100, _as_int("WINDOWS_HIDE_DELAY_MS", self.windows_hide_control_ms))
        self.phone_windows_hide_control_ms = max(
            100, _as_int("PHONEMODE_WINDOWS_HIDE_DELAY_MS", self.phone_windows_hide_control_ms)
        )

        if "AUDIO_PADDING" in values:
            self.audio_padding = float(values.get("AUDIO_PADDING"))

        if "POPUP_CLOSE_TIMER" in values:
            self.popup.close_delay = max(100, int(values.get("POPUP_CLOSE_TIMER")))
            popup = getattr(self.popup, "_popup", None)
            if popup is not None and popup.winfo_exists():
                if (
                    not getattr(self.popup, "_pinned", False)
                    and not getattr(self.popup, "_menu_open", False)
                    and not getattr(self.popup, "_dragging", False)
                ):
                    self.popup._restart_close()

        self._apply_popup_style_settings(values)
        self._apply_subtitle_style_settings(values)
        self._apply_subtitle_cleaning_settings(values)
        self._apply_startup_settings(values)
        self._apply_hotkey_settings(values)
        self._apply_anki_settings(values)

    def _apply_popup_style_settings(self, values: dict) -> None:
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
            popup_win.configure(bg=self.popup.bg_color)
        if entry is not None:
            entry.configure(
                bg=self.popup.bg_color,
                fg=self.popup.font_color,
                font=(self.popup.font_name, self.popup.font_size, "bold"),
            )

    def _apply_subtitle_style_settings(self, values: dict) -> None:
        subtitle_style_keys = {
            "SUBTITLE_FONT",
            "SUBTITLE_FONT_SIZE",
            "SUBTITLE_COLOR",
            "SUBTITLE_WRAP_LIMIT_PX",
            "SUBTITLE_HOVER_RUBY",
            "GLOW_COLOR",
            "GLOW_RADIUS",
        }
        if any(k in values for k in subtitle_style_keys):
            self.last_subtitle_text = ""
            self.update_time_and_subtitle_displays()

    def _apply_subtitle_cleaning_settings(self, values: dict) -> None:
        subtitle_cleaning_keys = {
            "SUBTITLE_AUTO_RUBY",
            "SUBTITLE_CUSTOM_HTML_TAGS",
            "SUBTITLE_SPEAKER_MODE",
            "SUBTITLE_KEEP_SPEAKER_NAMES",
            "SUBTITLE_SPEAKER_TEMPLATE",
            "SUBTITLE_STRIP_PAREN_NOTES",
        }
        if any(k in values for k in subtitle_cleaning_keys):
            srt_path = getattr(self.sub_manager, "srt_file", None)
            if srt_path:
                self.sub_manager.set_subtitle_display_data(srt_path)
                self.last_subtitle_text = ""
                self.update_time_and_subtitle_displays()

    def _apply_startup_settings(self, values: dict) -> None:
        startup_value_keys = {"DEFAULT_START_TIME", "EXTRA_OFFSET", "DEFAULT_SKIP"}
        if not any(k in values for k in startup_value_keys):
            return

        self.default_start_time = float(values.get("DEFAULT_START_TIME", self.default_start_time))

        if "EXTRA_OFFSET" in values:
            off = float(values.get("EXTRA_OFFSET"))
            old_off = float(getattr(self.settings, "_last_offset_value", self.default_offset) or 0.0)
            self.default_offset = off
            self.settings._last_offset_value = off
            self.settings.offset_var.set(f"{self.settings._format_number(off)} s")
            self.settings._apply_offset_change(off, persist=False, previous_value=old_off)

        if "DEFAULT_SKIP" in values:
            skip = float(values.get("DEFAULT_SKIP"))
            self.default_skip = skip
            self.settings._last_skip_value = skip
            self.settings.skip_var.set(f"{self.settings._format_number(skip)} s")
            self.settings._apply_skip_change(skip, persist=False)

        self.settings._sync_advanced_startup_vars_from_runtime()

    def _apply_hotkey_settings(self, values: dict) -> None:
        if "SHORTCUTS_DISABLED" in values:
            self.settings.set_hotkeys_disabled(bool(values.get("SHORTCUTS_DISABLED")))
            if self._hotkeys_disabled():
                self._reset_hotkey_state()

        if (
            "SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS" in values
            or any(str(k).startswith("DISABLE_HOTKEY_") for k in values.keys())
        ):
            self._reset_hotkey_state()

    def _apply_anki_settings(self, values: dict) -> None:
        if any(str(k).startswith("ANKI_") for k in values.keys()):
            self.anki = AnkiClient(self.config)
            self.anki_busy_cursor = self.anki_busy_cursor or "wait"

    # ---------------------------------------------------------------------
    # Time handling
    # ---------------------------------------------------------------------
    def update_loop(self):
        return self.playback.update_loop()

    def schedule_update(self):
        return self.playback.schedule_update()

    def set_current_time(self, t: float):
        return self.playback.set_current_time(t)

    # ---------------------------------------------------------------------
    # Subtitle / episode / playback routing
    # ---------------------------------------------------------------------
    def update_time_and_subtitle_displays(self):
        return self.subtitle_navigation.update_time_and_subtitle_displays()

    def on_open_srt(self, event=None):
        return self.episode_controller.on_open_srt(event)

    def change_episode(self, action: str):
        return self.episode_controller.change_episode(action)

    def _after_episode_change(self):
        return self.episode_controller._after_episode_change()

    def update_max_width(self) -> None:
        return self.episode_controller.update_max_width()

    def toggle_play(self):
        return self.playback.toggle_play()

    def go_forward(self):
        return self.playback.go_forward()

    def go_back(self):
        return self.playback.go_back()

    def jump_subtitle_segment(self, direction: str) -> None:
        return self.playback.jump_subtitle_segment(direction)

    def on_jump_sub_end(self, event=None):
        return self.playback.on_jump_sub_end(event)

    def _schedule_hide_controls(self):
        return self.overlay_controller._schedule_hide_controls()

    def _hide_controls_after(self, ms: int):
        return self.overlay_controller._hide_controls_after(ms)

    def on_slider_press(self, event):
        return self.subtitle_navigation.on_slider_press(event)

    def on_slider_change(self, value):
        return self.subtitle_navigation.on_slider_change(value)

    def on_slider_release(self, event):
        return self.subtitle_navigation.on_slider_release(event)

    def _on_key_press(self, key):
        return self.hotkey_controller._on_key_press(key)

    def _on_key_release(self, key):
        return self.hotkey_controller._on_key_release(key)

    def on_alt_x(self, event=None):
        return self.hotkey_controller.on_alt_x(event)

    # ---------------------------------------------------------------------
    # Overlay / hide-window logic
    # ---------------------------------------------------------------------
    def sub_window_enter(self, event):
        return self.overlay_controller.sub_window_enter(event)

    def sub_window_leave(self, event):
        return self.overlay_controller.sub_window_leave(event)

    def sub_handle_enter(self, event):
        return self.overlay_controller.sub_handle_enter(event)

    def _hide_subtitle_handle_for_settings(self):
        return self.overlay_controller._hide_subtitle_handle_for_settings()

    def _restore_subtitle_handle_after_settings(self):
        return self.overlay_controller._restore_subtitle_handle_after_settings()

    # ---------------------------------------------------------------------
    # OCR / Anki public routing
    # ---------------------------------------------------------------------
    def on_ocr_read_now(self, override: dict | None = None) -> None:
        return self.ocr_controller.on_ocr_read_now(override)

    def on_ocr_sync_now(self, override: dict | None = None) -> None:
        return self.ocr_controller.on_ocr_sync_now(override)

    # ---------------------------------------------------------------------
    # Shutdown
    # ---------------------------------------------------------------------
    def _on_app_close(self):
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        root = self.settings.root
        if root is None or not root.winfo_exists():
            return

        self._save_geometry_and_session_state()
        self._stop_listeners_and_jobs()
        self._finalize_shutdown()

    def _save_geometry_and_session_state(self) -> None:
        def _read_settings_geometry():
            geo = self.settings.root.winfo_geometry()
            size, pos = geo.split("+", 1)
            w_s, h_s = size.split("x", 1)
            x_s, y_s = pos.split("+", 1)
            return int(x_s), int(y_s), int(w_s), int(h_s)
        
        x, y, w, h = _read_settings_geometry()

        if (x, y) != (self.config.get("LAST_SETTINGS_WINDOW_X"),
                        self.config.get("LAST_SETTINGS_WINDOW_Y")):
            self.config.set("LAST_SETTINGS_WINDOW_X", x)
            self.config.set("LAST_SETTINGS_WINDOW_Y", y)
        if (w, h) != (self.config.get("LAST_SETTINGS_WINDOW_WIDTH"),
                        self.config.get("LAST_SETTINGS_WINDOW_HEIGHT")):
            self.config.set("LAST_SETTINGS_WINDOW_WIDTH", w)
            self.config.set("LAST_SETTINGS_WINDOW_HEIGHT", h)

        session_anime = self.sub_manager.get_anime_name()
        updates = {
            "LAST_ANIME_NAME": str(session_anime or ""),
            "LAST_SESSION_TIME_SEC": float(self.current_time or 0.0),
            "LAST_SESSION_PLAY_MODE": bool(self.playing),
        }
        if hasattr(self.config, "set_many"):
            self.config.set_many(updates)
        else:
            for key, value in updates.items():
                self.config.set(key, value)
        self.settings.save_state()  # control window position
        self.overlay.save_state()   # subtitle overlay center position
        self.sub_manager.save_state()

    def _stop_listeners_and_jobs(self) -> None:
        for listener_attr in ("_mouse_listener", "_keyboard_listener"):
            listener = getattr(self, listener_attr, None)
            if listener is not None:
                listener.stop()
        root = self.settings.root
        for job in ("subtitle_timeout_job", "_con_hide_job", "_input_pump_job", "_repeat_job", "_ocr_job"):
            handle = getattr(self, job, None)
            if handle is not None:
                root.after_cancel(handle)
                setattr(self, job, None)
        self.popup._cancel_close()

    def _finalize_shutdown(self) -> None:
        try:
            self.settings.root.destroy()
        except Exception:
            pass
    





    # ---------------------------------------------------------------------
    # Compatibility helpers for benchmarks / older call sites
    # ---------------------------------------------------------------------

    def _segments_to_copy_text(self, top_segments, bottom_segments):
        return self.subtitle_navigation.segments_to_copy_text(top_segments, bottom_segments)

    def _update_subtitle_display(self, force: bool = False):
        return self.subtitle_navigation._update_subtitle_display(force)
    

# from video_sync_server import get_video_time

        #   self.settings.root.after(self.video_sync_interval_ms, self._sync_loop)
        #   self.video_sync_interval_ms = self.config.get("VIDEO_SYNC_INTERVAL_MS") or 500
        #   self.video_sync_threshold = self.config.get("VIDEO_SYNC_THRESHOLD") or 0.5

    # def sync_with_video(self):
    #     video_time, video_duration = get_video_time()
    #     if video_duration <= 0:
    #         return
    #     drift = video_time - self.current_time
    #     if abs(drift) > self.video_sync_threshold:
    #         print(f"[SYNC] correcting drift: {drift:.2f}s → {video_time:.2f}")
    #         self.playback.set_current_time(video_time)
    # def _sync_loop(self):
    #     if not self._shutting_down:
    #         self.sync_with_video()
    #         self.settings.root.after(self.video_sync_interval_ms, self._sync_loop)
