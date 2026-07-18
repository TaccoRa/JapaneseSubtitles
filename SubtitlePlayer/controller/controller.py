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
import os
import tkinter as tk
import unicodedata
from datetime import date
from tkinter import ttk
from typing import Any
import logging

from pynput.keyboard import Listener as KeyboardListener
from pynput.mouse import Listener as MouseListener

from model.anki_activity import increment_daily_history, migrate_daily_history
from model.anki_client import AnkiClient, AnkiConnectRequestError
from model.anki_word_sync import AnkiSyncSettings, AnkiWordSync, split_csv_values
from model.annotation_provider import AnnotationProvider
from model.config_manager import ConfigManager
from model.renderer import SubtitleRenderer
from model.subtitle_manager import SubtitleManager
from model.voice_service import VoiceCommandService
from model.wanikani_client import WaniKaniClient
from model.word_database import WordDatabase, WordEntry, normalize_word
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
from logging_setup import set_debug_logging
from utils import (
    dispatch_to_tk,
    dispatch_to_tk_sync,
    format_time,
    get_monitor_rects,
    get_window_screen_rect,
    list_visible_windows,
    send_hotkey_to_window,
)

logger = logging.getLogger(__name__)

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
        "SHORTCUT_TOGGLE_SUBTITLES": "s",
        "SHORTCUT_POPUP_DEEPL_TRANSLATE": "t",
        "SHORTCUT_POPUP_GOOGLE_TRANSLATE": "g",
        "SHORTCUT_POPUP_ADD_ANKI": "a",
        "SHORTCUT_POPUP_ADD_ANKI_CAPTURE": "v",
        "SHORTCUT_FAST_FORWARD_SPEED_UP": "shift+.",
        "SHORTCUT_FAST_FORWARD_SPEED_DOWN": "shift+comma",
        "SHORTCUT_TOGGLE_DEBUGGING": "ctrl+shift+d",
        "SHORTCUT_TOGGLE_VOICE": "shift+l",
        "HOVER_DICTIONARY_HOTKEY": "shift",
        "HOVER_STATUS_HOTKEY": "ctrl",
        "HOVER_TRANSLATION_HOTKEY": "alt",
    }
    HOTKEY_DISABLE_KEYS = {
        "toggle_play": "DISABLE_HOTKEY_TOGGLE_PLAY",
        "toggle_subtitles": "DISABLE_HOTKEY_TOGGLE_SUBTITLES",
        "clear_subtitle": "DISABLE_HOTKEY_TOGGLE_SUBTITLES",
        "go_back": "DISABLE_HOTKEY_GO_BACK",
        "go_forward": "DISABLE_HOTKEY_GO_FORWARD",
        "subtitle_back": "DISABLE_HOTKEY_SUBTITLE_BACK",
        "subtitle_forward": "DISABLE_HOTKEY_SUBTITLE_FORWARD",
        "jump_sub_end": "DISABLE_HOTKEY_JUMP_SUB_END",
        "fast_forward_speed_up": "DISABLE_HOTKEY_FAST_FORWARD_SPEED_UP",
        "fast_forward_speed_down": "DISABLE_HOTKEY_FAST_FORWARD_SPEED_DOWN",
        "popup_add_anki_capture": "DISABLE_HOTKEY_POPUP_ADD_ANKI_CAPTURE",
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
        self._publish_anki_add_counts()
        self._apply_saved_offset_for_current_anime()
        self._start_input_listeners()
        self.voice.start()

        self.episode_controller.restore_startup_time_and_mode()

        self.last_update = time.perf_counter()
        self.playback.set_current_time(float(self.current_time or 0.0))
        self.update_episode_nav_controls()
        try:
            self.sub_manager.schedule_episode_preload_around_current()
        except Exception:
            pass
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
        self.control_show_on_subtitle_hover = self.config.get("CONTROL_SHOW_ON_SUBTITLE_HOVER") is not False
        self.phone_subtitle_handle_enabled = self.config.get("PHONEMODE_SUBTITLE_HANDLE_ENABLED") is not False
        self.hide_subtitles_ms = self.config.get("SUBTITLE_TIMEOUT_MS")
        self.update_interval_ms = self.config.get("UPDATE_INTERVAL_MS")
        self.anki_busy_cursor = self.config.get("ANKI_BUSY_CURSOR") or "wait"
        self.anki_add_session_count = 0
        self._anki_add_history = {}
        self._load_anki_add_history_state(persist=True)
        self.video_click = bool(self.config.get("VIDEO_CLICK") or False)
        self.video_click_play = True if self.config.get("VIDEO_CLICK_PLAY") is None else bool(self.config.get("VIDEO_CLICK_PLAY"))
        self.video_click_window = False if self.config.get("VIDEO_CLICK_WINDOW") is None else bool(self.config.get("VIDEO_CLICK_WINDOW"))
        self.subtitle_hover_pause_video = bool(self.config.get("SUBTITLE_HOVER_PAUSE_VIDEO") or False)
        self.fast_forward_enabled = not bool(self.config.get("FAST_FORWARD_DISABLED") or False)
        self.fast_forward_active = bool(self.fast_forward_enabled)
        self.fast_forward_speed = self._coerce_fast_forward_speed(self.config.get("FAST_FORWARD_SPEED"))
        self._hover_video_pause_active = False
        self._hover_timer_pause_active = False
        self._suppress_synthetic_space_until = 0.0
        self._suppress_synthetic_tokens_until: dict[str, float] = {}

        self.playing = False
        self.entry_editing = False
        self.subtitle_deleted = False
        self.subtitles_user_hidden = False
        self.subtitles_hidden_until_index = None
        self.alt_pressed = False
        self.ctrl_pressed = False
        self.shift_pressed = False
        self.translation_pressed = False
        self.translation_provider = "deepl"
        self.sub_hidden = False
        self.slider_dragging = False
        self._slider_render_job = None
        self._slider_pending_value: float | None = None
        self._defer_auto_ruby_once = False
        self._auto_ruby_generation_id = 0
        self._auto_ruby_thread = None
        self._shutting_down = False
        self._perf_stats = {
            "slider_change_count": 0,
            "slider_change_total_ms": 0.0,
            "slider_change_max_ms": 0.0,
            "slider_preview_count": 0,
            "slider_preview_total_ms": 0.0,
            "slider_preview_max_ms": 0.0,
            "slider_release_count": 0,
            "slider_release_total_ms": 0.0,
            "slider_release_max_ms": 0.0,
            "subtitle_render_count": 0,
            "subtitle_render_total_ms": 0.0,
            "subtitle_render_max_ms": 0.0,
            "episode_switch_count": 0,
            "episode_switch_total_ms": 0.0,
            "episode_switch_max_ms": 0.0,
        }
        self._last_ocr_duration_ms = 0.0

        self._single_fire_actions: set[str] = set()
        self._input_actions: "queue.Queue[str]" = queue.Queue()
        self._shutdown_event = threading.Event()
        self._update_loop_job = None
        self._input_pump_job = None
        self._repeat_job = None
        self._settings_pointer_job = None
        self._settings_pointer_inside = False
        self._repeat_lock = threading.Lock()
        self._held_repeat_next_fire: dict[str, float] = {}
        self._held_repeat_fired: set[str] = set()
        self._held_repeat_press_time: dict[str, float] = {}
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
        self._anki_preview_window = None
        self._anki_add_busy = False
        self._voice_anki_action_queued = False
        self._voice_playback_pending = False
        self._voice_target_window = None
        self._voice_last_status = {"state": "disabled", "message": "Voice commands disabled."}
        self._voice_mic_test_thread = None
        self._voice_mic_test_stop = None
        self._voice_mic_test_monitor_enabled = True
        self._voice_mic_test_restart_recognition = False
        self._latest_post_add_capture_note_id = 0
        self._latest_post_add_capture_window_hwnd = 0

        self.subtitle_timeout_job = None
        self._last_time_overlay_text = None
        self.last_subtitle_text = ""
        self.last_subtitle_raw = ""
        self.last_rendered_index = None
        self.last_rendered_sub_time = None

    @staticmethod
    def _coerce_fast_forward_speed(value) -> float:
        try:
            speed = float(value)
        except Exception:
            speed = 1.5
        speed = max(0.1, min(8.0, speed))
        return round(speed, 1)

    def _create_services_and_controllers(self) -> None:
        self.playback = PlaybackController(self)
        self.subtitle_navigation = SubtitleNavigationController(self)
        self.episode_controller = EpisodeController(self)
        self.overlay_controller = OverlayController(self)
        self.hotkey_controller = HotkeyController(self)
        self.anki_controller = AnkiController(self)
        self.ocr_controller = OCRController(self)
        self.word_database = None
        self.annotation_provider = None
        if self._annotation_is_enabled():
            self._ensure_annotation_services()
        self.anki = AnkiClient(self.config)
        self.voice = VoiceCommandService(
            self.config,
            action_callback=self._on_voice_recognized,
            status_callback=self._on_voice_status_from_worker,
        )

    def _annotation_database_path(self) -> str:
        configured = str(self.config.get("ANNOTATION_LOCAL_DB_PATH") or "").strip()
        if configured:
            if os.path.isabs(configured):
                return configured
            base_dir = os.path.dirname(os.path.abspath(getattr(self.config, "path", "config.json")))
            return os.path.join(base_dir, configured)
        base_dir = os.path.dirname(os.path.abspath(getattr(self.config, "path", "config.json")))
        return os.path.join(base_dir, "annotation_words.json")

    def _bind_ui_events(self) -> None:
        self.settings.root.protocol("WM_DELETE_WINDOW", self._on_app_close)
        self.settings.root.bind("<Destroy>", self._on_root_destroy, add="+")
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
        self.settings.bind_refresh_episodes        (self.episode_controller.refresh_remote_episodes)
        self.settings.bind_set_to_return            (self.subtitle_navigation.on_set_to_return)
        self.settings.bind_time_entry_return        (self.subtitle_navigation.control_time_entry_return)
        self.settings.bind_time_entry_clear         (self.subtitle_navigation.control_clear_time_entry)
        self.settings.bind_control_window_enter     (self.overlay_controller.control_window_enter)
        self.settings.bind_control_window_leave     (self.overlay_controller.control_window_leave)
        self.settings.bind_show_subtitle_handle     (self.overlay_controller.show_subtitle_handle)
        self.settings.bind_refresh_subtitles        (self.subtitle_navigation.on_refresh_subtitles)
        self.settings.bind_toggle_subtitles         (self.subtitle_navigation.toggle_subtitle_visibility)
        self.settings.bind_advanced_apply           (self.apply_advanced_settings)
        self.settings.bind_offset_change            (self._on_runtime_offset_changed)
        self.settings.bind_fast_forward_controls    (
            speed_delta=self.playback.change_fast_forward_speed,
        )
        self.settings.bind_ocr_read_now             (self.ocr_controller.on_ocr_read_now)
        self.settings.bind_ocr_sync_now             (self.ocr_controller.on_ocr_sync_now)
        self.settings.bind_ocr_show_boxes           (self.ocr_controller.show_ocr_boxes)
        self.settings.bind_anki_check               (self.anki_controller.on_anki_check_connection)
        self.settings.bind_performance_snapshot     (self.get_performance_snapshot)
        self.settings.bind_performance_reset        (self.reset_performance_stats)
        self.settings.bind_settings_open            (self._hide_subtitle_handle_for_settings)
        self.settings.bind_voice_callbacks          (
            toggle_enabled=self.toggle_voice_enabled,
            list_devices=self.voice_list_input_devices,
            model_status=self.voice_model_status,
            download_model=self.voice_download_model,
            cancel_download=self.voice_cancel_model_download,
            remove_model=self.voice_remove_model,
            test_microphone=self.voice_test_microphone,
            stop_microphone_test=self.voice_stop_microphone_test,
            set_microphone_monitor=self.voice_set_microphone_monitor,
            list_windows=self.voice_list_windows,
        )
        self.settings.bind_annotation_callbacks     (
            list_words=self.annotation_list_words,
            add_word=self.annotation_add_word,
            delete_word=self.annotation_delete_word,
            import_words=self.annotation_import_words,
            export_words=self.annotation_export_words,
            refresh_words=self.annotation_refresh_words,
            anki_refresh=self.annotation_anki_refresh,
            anki_model_fields=self.annotation_anki_model_fields,
            anki_sync=self.annotation_sync_anki,
            wanikani_test=self.annotation_test_wanikani,
            wanikani_sync=self.annotation_sync_wanikani,
            wanikani_clear=self.annotation_clear_wanikani,
        )
        self.settings.bind_update_display           (self.update_time_and_subtitle_displays)
        self.overlay.subtitle_canvas.bind           ("<Button-3>", self._on_copy_popup)
        self.popup.bind_add_to_anki                 (self._add_selection_to_anki)
        self.popup.bind_dictionary_lookup           (self._lookup_dictionary_entry)
        self.popup.bind_translation_lookup          (self._translate_hover_selection)
        self.popup.bind_word_tokenizer              (self._word_spans_for_lookup)
        self.popup.bind_annotation_provider         (self.annotation_provider if self._annotation_is_enabled() else None)
        self.popup.bind_shift_state                 (self._plain_shift_hover_active)
        self.popup.bind_hover_mode                  (self._hover_modifier_mode)
        self.popup.bind_translation_state           (lambda: bool(self.translation_pressed))
        self.popup.bind_translation_provider        (
            lambda: str(self.translation_provider if self.translation_pressed else self._hover_translation_provider())
        )
        self.popup.bind_anchor_window               (self._popup_anchor_window)
        self.renderer.bind_dictionary_lookup        (self._lookup_dictionary_entry)
        self.renderer.bind_translation_lookup       (self._translate_hover_selection)
        self.renderer.bind_translation_provider     (
            lambda: str(self.translation_provider if self.translation_pressed else self._hover_translation_provider())
        )
        self.renderer.bind_word_tokenizer           (self._word_spans_for_lookup)
        self.renderer.bind_annotation_provider      (self.annotation_provider if self._annotation_is_enabled() else None)
        self.renderer.bind_shift_state              (lambda: self._plain_shift_hover_active() and not self._popup_is_open())
        self.renderer.bind_hover_mode               (lambda: "ruby" if self._popup_is_open() else self._hover_modifier_mode())
        self.overlay.bind_sub_window_enter          (self.sub_window_enter)
        self.overlay.bind_sub_window_leave          (self.sub_window_leave)
        self.overlay.bind_sub_handle_enter          (self.sub_handle_enter)   
        self.settings.root.bind                     ("<Enter>", lambda _e: self._hide_subtitle_handle_for_settings(), add="+")
        self.settings.root.bind                     ("<Leave>", lambda _e: self._restore_subtitle_handle_after_settings(), add="+")
        self._settings_pointer_job = self.settings.root.after(150, self._poll_settings_pointer_for_handle)

    def _start_input_listeners(self) -> None:
        try:
            self.hotkey_controller._refresh_ui_state_cache()
        except Exception:
            logger.debug("Failed to initialize hotkey UI state", exc_info=True)
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

    @staticmethod
    def _normalize_anime_offset_key(value) -> str:
        try:
            return re.sub(r"\s+", " ", str(value or "").strip()).casefold()
        except Exception:
            logger.debug("Failed to normalize anime offset key", exc_info=True)
            return ""

    def _current_anime_offset_key(self) -> str:
        try:
            anime = self.sub_manager.get_anime_name()
        except Exception:
            anime = getattr(self.sub_manager, "anime_folder_name", "")
        return self._normalize_anime_offset_key(anime)

    def _anime_offsets(self) -> dict:
        try:
            raw = self.config.get("ANIME_OFFSETS")
        except Exception:
            raw = None
        return dict(raw) if isinstance(raw, dict) else {}

    def _remember_current_anime_offset(self, value: float | None = None) -> None:
        key = self._current_anime_offset_key()
        if not key:
            return
        try:
            offset = float(self.settings._last_offset_value if value is None else value)
        except Exception:
            logger.debug("Failed to read current offset for anime-specific save", exc_info=True)
            return
        offsets = self._anime_offsets()
        saved = offsets.get(key)
        try:
            if saved is not None and abs(float(saved) - offset) < 0.001:
                return
        except Exception:
            pass
        offsets[key] = round(offset, 3)
        try:
            self.config.set("ANIME_OFFSETS", offsets)
        except Exception:
            cfg = getattr(self.config, "config", None)
            if isinstance(cfg, dict):
                cfg["ANIME_OFFSETS"] = offsets
            logger.debug("Failed to persist anime-specific offset", exc_info=True)

    def _refresh_subtitle_after_offset_change(self) -> None:
        navigation = getattr(self, "subtitle_navigation", None)
        if navigation is None:
            return
        try:
            navigation.refresh_after_offset_change()
        except Exception:
            logger.debug("Failed to refresh subtitle after offset change", exc_info=True)

    def _on_runtime_offset_changed(self, value: float) -> None:
        self._remember_current_anime_offset(value)
        self._refresh_subtitle_after_offset_change()

    def _saved_offset_for_current_anime(self) -> float | None:
        key = self._current_anime_offset_key()
        if not key:
            return None
        offsets = self._anime_offsets()
        if key not in offsets:
            return None
        try:
            return float(offsets[key])
        except Exception:
            logger.debug("Stored anime offset is invalid for %s", key, exc_info=True)
            return None

    def _set_runtime_offset(self, value: float, *, persist_global: bool = True, remember_anime: bool = False) -> None:
        try:
            offset = float(value)
        except Exception:
            return
        old_offset = float(getattr(self.settings, "_last_offset_value", self.default_offset) or 0.0)
        self.default_offset = offset
        self.settings.default_offset = offset
        self.settings._last_offset_value = offset
        self.settings.offset_var.set(f"{self.settings._format_number(offset)} s")
        try:
            self.settings._apply_offset_change(
                offset,
                persist=False,
                previous_value=old_offset,
                adjust_current=False,
            )
        except TypeError:
            self.settings._apply_offset_change(offset, persist=False, previous_value=old_offset)
        if persist_global:
            try:
                self.config.set("EXTRA_OFFSET", offset)
            except Exception:
                logger.debug("Failed to persist global offset", exc_info=True)
        if remember_anime:
            self._remember_current_anime_offset(offset)
        self._refresh_subtitle_after_offset_change()

    def _apply_saved_offset_for_current_anime(self) -> bool:
        offset = self._saved_offset_for_current_anime()
        if offset is None:
            return False
        current = float(getattr(self.settings, "_last_offset_value", self.default_offset) or 0.0)
        if abs(current - offset) < 0.001:
            return False
        self._set_runtime_offset(offset, persist_global=True, remember_anime=False)
        return True

    def update_episode_nav_controls(self) -> None:
        return self.episode_controller.update_episode_nav_controls()

    def _get_display_start_times(self):
        return self.episode_controller._get_display_start_times()

    def _get_display_end_times(self):
        return self.episode_controller._get_display_end_times()

    def _on_copy_popup(self, event=None):
        try:
            self.renderer._clear_hover_ruby()
        except Exception:
            pass
        try:
            sync = getattr(self.playback, "_sync_playing_time_to_event", None)
            if callable(sync):
                sync(update_display=False, update_slider=False)
        except Exception:
            logger.debug("Failed to sync playback before opening subtitle popup", exc_info=True)
        text = str(getattr(self, "last_subtitle_raw", "") or "")
        try:
            getter = getattr(self.subtitle_navigation, "copy_text_for_current_subtitle", None)
            if callable(getter):
                text = getter()
        except Exception:
            logger.debug("Failed to resolve current subtitle text for popup", exc_info=True)
        line_segments = None
        try:
            getter = getattr(self.subtitle_navigation, "line_segments_for_current_subtitle", None)
            if callable(getter):
                line_segments = getter()
        except Exception:
            logger.debug("Failed to resolve current subtitle segments for popup", exc_info=True)
        self.popup.open_copy_popup(text, line_segments=line_segments)
        return "break"

    def _skip_buttons_use_subtitle_segments(self) -> bool:
        return self.hotkey_controller._skip_buttons_use_subtitle_segments()

    def _add_selection_to_anki(
        self,
        selected_text: str,
        subtitle_text: str = "",
        post_add_capture: bool = False,
    ) -> None:
        return self.anki_controller._add_selection_to_anki(
            selected_text,
            subtitle_text,
            post_add_capture=bool(post_add_capture),
        )

    def _publish_anki_add_counts(self) -> None:
        update = getattr(self.settings, "set_anki_add_counts", None)
        if not callable(update):
            return
        try:
            update(self.anki_add_session_count, self.anki_add_today_count)
        except Exception:
            logger.debug("Failed to publish Anki add counters", exc_info=True)

    def _load_anki_add_history_state(self, *, persist: bool) -> None:
        today = date.today().isoformat()
        history = migrate_daily_history(
            self.config.get("ANKI_ADD_HISTORY"),
            self.config.get("ANKI_ADD_COUNT_DATE"),
            self.config.get("ANKI_ADD_COUNT_TODAY"),
        )
        self._anki_add_history = history
        self._anki_add_count_date = today
        self.anki_add_today_count = max(0, int(history.get(today, 0) or 0))
        if not persist:
            return
        try:
            self.config.set_many(
                {
                    "ANKI_ADD_HISTORY": history,
                    "ANKI_ADD_COUNT_DATE": today,
                    "ANKI_ADD_COUNT_TODAY": self.anki_add_today_count,
                }
            )
        except Exception:
            logger.debug("Failed to persist Anki add history", exc_info=True)
        persist_profile = getattr(self.settings, "persist_anki_add_history", None)
        if callable(persist_profile):
            try:
                persist_profile(history, today, self.anki_add_today_count)
            except Exception:
                logger.debug("Failed to persist profile Anki add history", exc_info=True)

    def _record_successful_anki_add(self) -> None:
        today = date.today().isoformat()
        self._anki_add_history = increment_daily_history(
            getattr(self, "_anki_add_history", {}),
            today,
        )
        self._anki_add_count_date = today
        self.anki_add_session_count = max(0, int(getattr(self, "anki_add_session_count", 0) or 0)) + 1
        self.anki_add_today_count = max(0, int(self._anki_add_history.get(today, 0) or 0))
        try:
            self.config.set_many(
                {
                    "ANKI_ADD_HISTORY": self._anki_add_history,
                    "ANKI_ADD_COUNT_DATE": today,
                    "ANKI_ADD_COUNT_TODAY": self.anki_add_today_count,
                }
            )
        except Exception:
            logger.debug("Failed to persist Anki add counters", exc_info=True)
        persist_profile = getattr(self.settings, "persist_anki_add_history", None)
        if callable(persist_profile):
            try:
                persist_profile(self._anki_add_history, today, self.anki_add_today_count)
            except Exception:
                logger.debug("Failed to persist profile Anki add history", exc_info=True)
        self._publish_anki_add_counts()

    def _lookup_dictionary_entry(self, text: str, allow_translation_fallback: bool = False) -> str:
        try:
            return self.anki.lookup_dictionary_entry(
                text,
                allow_translation_fallback=allow_translation_fallback,
            )
        except Exception:
            return ""

    def _translate_hover_selection(self, text: str, provider: str = "deepl") -> str:
        database_translation = self._word_database_translation_for_lookup(text)
        if database_translation:
            return database_translation
        try:
            return self.anki.translate_hover_selection(text, provider=provider)
        except Exception:
            return ""

    def _word_database_translation_for_lookup(self, text: str) -> str:
        query = str(text or "").strip()
        if not query:
            return ""

        exact_keys: list[str] = []
        lookup_keys: list[str] = []

        def _add_key(target: list[str], value: str) -> None:
            key = normalize_word(value)
            if key and key not in target:
                target.append(key)

        _add_key(exact_keys, query)

        try:
            spans = self._word_spans_for_lookup(query)
        except Exception:
            spans = []
        if isinstance(spans, list) and len(spans) == 1:
            span = spans[0] if isinstance(spans[0], dict) else {}
            _add_key(exact_keys, str(span.get("surface") or ""))
            _add_key(lookup_keys, str(span.get("lookup") or ""))

        database = getattr(self, "word_database", None)
        if database is None:
            try:
                database, _provider = self._ensure_annotation_services()
            except Exception:
                database = None
        if database is None:
            return ""

        def _find_meaning(keys: list[str]) -> str:
            if not keys:
                return ""
            try:
                entries = database.list_entries()
            except Exception:
                return ""
            for entry in entries:
                entry_keys = {
                    normalize_word(getattr(entry, "normalized", "")),
                    normalize_word(getattr(entry, "surface", "")),
                    normalize_word(getattr(entry, "base", "")),
                }
                if not any(key in entry_keys for key in keys):
                    continue
                meaning = str(getattr(entry, "meaning", "") or "").strip()
                if meaning:
                    return meaning
            return ""

        meaning = _find_meaning(exact_keys) or _find_meaning(lookup_keys)
        if not meaning:
            return ""
        return f"{query} — {meaning}"

    def _hover_translation_provider(self) -> str:
        try:
            provider = str(getattr(self.anki, "sentence_translate_provider", "") or "").strip().lower()
            if provider in {"deepl", "google"}:
                return provider
        except Exception:
            pass
        return "deepl"

    def _hover_modifier_mode(self) -> str:
        candidates = [
            (
                "translation",
                self._hover_hold_score("HOVER_TRANSLATION_ENABLED", "HOVER_TRANSLATION_HOTKEY", "alt"),
                3,
            ),
            (
                "status",
                self._hover_hold_score("HOVER_STATUS_ENABLED", "HOVER_STATUS_HOTKEY", "ctrl"),
                2,
            ),
            (
                "dictionary",
                self._hover_hold_score(
                    "HOVER_DICTIONARY_ENABLED",
                    "HOVER_DICTIONARY_HOTKEY",
                    "shift",
                    legacy_enabled_key="SHIFT_HOVER_KANJI_DICTIONARY",
                ),
                1,
            ),
        ]
        active = [candidate for candidate in candidates if candidate[1] > 0]
        if active:
            return max(active, key=lambda item: (item[1], item[2]))[0]
        if bool(getattr(self, "translation_pressed", False)):
            return "translation"
        return "ruby"

    def _hover_hold_active(
        self,
        enabled_key: str,
        hotkey_key: str,
        default_hotkey: str,
        *,
        legacy_enabled_key: str | None = None,
    ) -> bool:
        helper = getattr(getattr(self, "hotkey_controller", None), "hover_hold_active", None)
        if not callable(helper):
            return False
        try:
            return bool(
                helper(
                    enabled_key,
                    hotkey_key,
                    default_hotkey,
                    legacy_enabled_key=legacy_enabled_key,
                )
            )
        except Exception:
            return False

    def _hover_hold_score(
        self,
        enabled_key: str,
        hotkey_key: str,
        default_hotkey: str,
        *,
        legacy_enabled_key: str | None = None,
    ) -> int:
        helper = getattr(getattr(self, "hotkey_controller", None), "hover_hold_active_score", None)
        if not callable(helper):
            return int(
                self._hover_hold_active(
                    enabled_key,
                    hotkey_key,
                    default_hotkey,
                    legacy_enabled_key=legacy_enabled_key,
                )
            )
        try:
            return int(
                helper(
                    enabled_key,
                    hotkey_key,
                    default_hotkey,
                    legacy_enabled_key=legacy_enabled_key,
                )
                or 0
            )
        except Exception:
            return 0

    def _popup_is_open(self) -> bool:
        try:
            return bool(self.popup.is_open())
        except Exception:
            return False

    def _plain_shift_hover_active(self) -> bool:
        if bool(getattr(self, "ctrl_pressed", False)) or bool(getattr(self, "alt_pressed", False)):
            return False
        if bool(getattr(self, "translation_pressed", False)):
            return False
        return self._hover_hold_active(
            "HOVER_DICTIONARY_ENABLED",
            "HOVER_DICTIONARY_HOTKEY",
            "shift",
            legacy_enabled_key="SHIFT_HOVER_KANJI_DICTIONARY",
        )

    def _popup_anchor_window(self):
        return getattr(self.overlay, "sub_window", None)

    def _word_spans_for_lookup(self, text: str):
        try:
            return self.anki.word_spans(text)
        except Exception:
            return []

    def _annotation_is_enabled(self) -> bool:
        return bool(self.config.get("ANNOTATION_ENABLED") or False)

    def _apply_annotation_runtime_config(self, values: dict) -> None:
        cfg = getattr(self.config, "config", None)
        if not isinstance(cfg, dict):
            return
        for key, value in values.items():
            if str(key).startswith("ANNOTATION_"):
                cfg[key] = value

    def _ensure_annotation_services(self):
        if self.word_database is None:
            self.word_database = WordDatabase(self._annotation_database_path())
        if self.annotation_provider is None:
            self.annotation_provider = AnnotationProvider(self.config, self.word_database)
        return self.word_database, self.annotation_provider

    @staticmethod
    def _annotation_disabled_result(**extra) -> dict:
        result = {"ok": False, "error": "Annotation is disabled."}
        result.update(extra)
        return result

    @staticmethod
    def _is_anki_connection_error(value) -> bool:
        if isinstance(value, AnkiConnectRequestError):
            return True
        text = str(value or "").lower()
        if "ankiconnect" in text and any(
            part in text
            for part in ("not reachable", "unavailable", "failed", "closed", "restarted")
        ):
            return True
        if "http request to" in text and (
            "127.0.0.1" in text or ":8765" in text or "ankiconnect" in text
        ):
            return True
        return False

    def _prompt_for_anki_connection(self) -> None:
        prompt = getattr(getattr(self, "anki_controller", None), "prompt_anki_connection", None)
        if not callable(prompt):
            return
        try:
            prompt()
        except Exception:
            logger.debug("Failed to show Anki connection prompt", exc_info=True)

    def _anki_connection_unavailable_result(self, *, prompt: bool = True, **extra) -> dict:
        if prompt:
            self._prompt_for_anki_connection()
        result = {
            "ok": False,
            "error": "AnkiConnect is not reachable. Please open Anki, then press Anki opened.",
        }
        result.update(extra)
        return result

    def _anki_connect_available_or_prompt(self) -> bool:
        ping = getattr(getattr(self, "anki", None), "ping", None)
        if not callable(ping):
            return True
        try:
            connected = bool(ping())
        except Exception as exc:
            if self._is_anki_connection_error(exc):
                self._prompt_for_anki_connection()
            return False
        if not connected:
            self._prompt_for_anki_connection()
        return connected

    def _refresh_annotation_runtime(self, *, redraw: bool = True) -> None:
        dispatcher = getattr(self.settings.root, "_tk_main_thread_dispatcher", None)
        if (
            dispatcher is not None
            and threading.get_ident() != getattr(dispatcher, "owner_thread_id", threading.get_ident())
        ):
            dispatch_to_tk(
                self.settings.root,
                self._refresh_annotation_runtime,
                redraw=bool(redraw),
            )
            return
        provider = None
        if self._annotation_is_enabled():
            try:
                _database, provider = self._ensure_annotation_services()
                provider.refresh()
            except Exception:
                logger.exception("Failed to refresh annotation index")
                provider = None
        try:
            self.renderer.bind_annotation_provider(provider)
        except Exception:
            logger.debug("Failed to rebind renderer annotation provider", exc_info=True)
        try:
            self.popup.bind_annotation_provider(provider)
        except Exception:
            logger.debug("Failed to rebind popup annotation provider", exc_info=True)
        if redraw:
            self.last_subtitle_text = ""
            self.update_time_and_subtitle_displays()

    @staticmethod
    def _bool_setting(value, default: bool = False) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, str):
            return value.strip().lower() not in {"", "0", "false", "no", "off"}
        return bool(value)

    def _annotation_anki_sync_settings(self, settings: dict | None = None) -> AnkiSyncSettings:
        settings = settings or {}

        anime_names: list[str] = []

        def _add_anime_name(value) -> None:
            name = str(value or "").strip()
            if name and name not in anime_names:
                anime_names.append(name)

        configured_anime_names = settings.get("anime_names")
        if isinstance(configured_anime_names, (list, tuple, set)):
            for value in configured_anime_names:
                _add_anime_name(value)
        elif configured_anime_names:
            _add_anime_name(configured_anime_names)
        for config_key in ("LAST_DISPLAY_ANIME_NAME", "LAST_ANIME_NAME"):
            _add_anime_name(self.config.get(config_key))
        manager = getattr(self, "sub_manager", None)
        for getter_name in ("get_display_anime_name", "get_anime_name"):
            getter = getattr(manager, getter_name, None)
            if callable(getter):
                try:
                    _add_anime_name(getter())
                except Exception:
                    pass
        database = getattr(self, "word_database", None)
        if database is not None:
            try:
                for entry in database.list_entries():
                    _add_anime_name(getattr(entry, "anime", ""))
            except Exception:
                logger.debug("Failed to collect known anime names for Anki tag sync", exc_info=True)

        def _fields(settings_key: str, config_key: str, fallback_key: str | None = None) -> list[str]:
            values = split_csv_values(settings.get(settings_key) or self.config.get(config_key))
            if values or not fallback_key:
                return values
            fallback = self.config.get(fallback_key)
            return split_csv_values(fallback)

        sentence_fields = split_csv_values(settings.get("sentence_fields") or self.config.get("ANNOTATION_ANKI_SENTENCE_FIELDS"))
        if not sentence_fields:
            sentence_fields = split_csv_values(self.config.get("ANKI_FIELD_ADD_RUBIES_SENTENCE_JA"))
            sentence_fields.extend(
                field
                for field in split_csv_values(self.config.get("ANKI_FIELD_SENTENCE_JA"))
                if field not in sentence_fields
            )

        return AnkiSyncSettings(
            decks=split_csv_values(settings.get("decks") or self.config.get("ANNOTATION_ANKI_DECKS")),
            word_fields=_fields("word_fields", "ANNOTATION_ANKI_WORD_FIELDS", "ANKI_FIELD_FRONT") or ["Front"],
            sentence_fields=sentence_fields,
            sentence_translated_fields=_fields(
                "sentence_translated_fields",
                "ANNOTATION_ANKI_SENTENCE_TRANSLATED_FIELDS",
                "ANKI_FIELD_SENTENCE_DE",
            ),
            reading_fields=split_csv_values(settings.get("reading_fields") or self.config.get("ANNOTATION_ANKI_READING_FIELDS")),
            meaning_fields=_fields("meaning_fields", "ANNOTATION_ANKI_MEANING_FIELDS", "ANKI_FIELD_BACK"),
            include_sentence_words=self._bool_setting(
                settings.get("include_sentence_words")
                if "include_sentence_words" in settings
                else self.config.get("ANNOTATION_ANKI_INCLUDE_SENTENCE_WORDS")
            ),
            use_mature_threshold=self._bool_setting(
                settings.get("use_mature_threshold")
                if "use_mature_threshold" in settings
                else self.config.get("ANNOTATION_USE_ANKI_MATURE_THRESHOLD"),
                default=True,
            ),
            mature_interval_days=int(settings.get("mature_interval_days") or self.config.get("ANNOTATION_ANKI_MATURE_INTERVAL_DAYS") or 21),
            suspended_as=str(settings.get("suspended_as") or self.config.get("ANNOTATION_ANKI_SUSPENDED_AS") or "normal"),
            tokenizer=self._word_spans_for_lookup,
            anime_names=anime_names,
        )

    def _annotation_entries_for_added_anki_note(self, result: dict) -> list[WordEntry]:
        if not self._annotation_is_enabled():
            return []
        try:
            note_id = int((result or {}).get("note_id") or 0)
        except Exception:
            note_id = 0
        if note_id <= 0:
            return []
        try:
            return AnkiWordSync.from_client(self.anki).entries_for_note(
                note_id,
                self._annotation_anki_sync_settings({}),
                anime_name=str((result or {}).get("anime_name") or ""),
            )
        except Exception:
            logger.warning("Failed to prepare added Anki note for annotation database", exc_info=True)
            return []

    def _store_added_anki_annotation_entries(self, entries: list[WordEntry]) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result(added=0)
        try:
            entries = [entry for entry in (entries or []) if isinstance(entry, WordEntry)]
            if not entries:
                return {"ok": True, "added": 0}
            database, _provider = self._ensure_annotation_services()
            added = database.upsert_many(entries, preserve_existing=False, replace_source=None)
            self._refresh_annotation_runtime()
            annotation_tab = getattr(self.settings, "_annotation_tab_ui", None)
            if annotation_tab is not None:
                try:
                    annotation_tab.refresh_words()
                except Exception:
                    logger.debug("Failed to refresh Annotation tab after Anki add", exc_info=True)
            logger.info("Added %d freshly-created Anki word(s) to annotation database", added)
            return {"ok": True, "added": int(added)}
        except Exception as exc:
            logger.warning("Failed to store added Anki note in annotation database: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc), "added": 0}

    def annotation_list_words(self, query: str = "") -> list[dict]:
        if not self._annotation_is_enabled():
            return []
        try:
            database, _provider = self._ensure_annotation_services()
            return [entry.to_dict() | {"key": entry.key} for entry in database.search(query)]
        except Exception:
            logger.exception("Failed to list annotation words")
            return []

    def annotation_add_word(self, payload: dict) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result()
        try:
            database, _provider = self._ensure_annotation_services()
            old_key = str(payload.get("old_key") or "")
            existing = database.get_key(old_key) if old_key else None
            extra = dict(existing.extra or {}) if existing is not None else {}
            if isinstance(payload.get("extra"), dict):
                extra.update(payload.get("extra") or {})
            surface = payload.get("surface") or payload.get("word") or ""
            base = payload.get("base") or ""
            normalized = payload.get("normalized") or ""
            word_changed = existing is not None and (
                str(surface or "") != str(existing.surface or "") or str(base or "") != str(existing.base or "")
            )
            if word_changed:
                normalized = ""
            entry = WordEntry.from_dict(
                {
                    "surface": surface,
                    "normalized": normalized or (existing.normalized if existing is not None and not word_changed else ""),
                    "base": base,
                    "reading": payload.get("reading") or "",
                    "meaning": payload.get("meaning") or "",
                    "anime": payload.get("anime") or (existing.anime if existing is not None else ""),
                    "source": payload.get("source") or "local",
                    "status": payload.get("status") or "local_known",
                    "created_at": payload.get("created_at") or (existing.created_at if existing is not None else ""),
                    "notes": payload.get("notes") or "",
                    "extra": extra,
                }
            )
            if not entry.normalized:
                return {"ok": False, "error": "Word is empty."}
            if entry.source == "anki":
                anki_result = self._annotation_write_entry_to_anki(entry, existing)
                if not anki_result.get("ok"):
                    return anki_result
                entry.extra.update(anki_result.get("extra") or {})
            database.upsert(entry)
            if old_key and old_key != entry.key:
                database.delete_key(old_key)
            self._refresh_annotation_runtime()
            return {"ok": True, "entry": entry.to_dict() | {"key": entry.key}}
        except Exception as exc:
            logger.exception("Failed to add annotation word")
            return {"ok": False, "error": str(exc)}

    def _annotation_write_entry_to_anki(self, entry: WordEntry, existing: WordEntry | None = None) -> dict:
        extra = dict(existing.extra or {}) if existing is not None else {}
        extra.update(entry.extra or {})
        try:
            note_id = int(extra.get("note_id") or 0)
        except Exception:
            note_id = 0
        if note_id <= 0:
            return {"ok": False, "error": "Cannot write this word back to Anki because the note id is missing."}

        fields: dict[str, str] = {}

        def _changed(attr: str) -> bool:
            if existing is None:
                return bool(str(getattr(entry, attr) or "").strip())
            return str(getattr(entry, attr) or "") != str(getattr(existing, attr) or "")

        def _configured_first(key: str) -> str:
            values = split_csv_values(self.config.get(key))
            return values[0] if values else ""

        word_field = str(extra.get("field") or "").strip() or _configured_first("ANNOTATION_ANKI_WORD_FIELDS")
        if _changed("surface") and word_field:
            fields[word_field] = entry.surface
        if _changed("reading"):
            field = str(extra.get("reading_field") or "").strip() or _configured_first("ANNOTATION_ANKI_READING_FIELDS")
            if field:
                fields[field] = entry.reading
        if _changed("meaning"):
            field = str(extra.get("meaning_field") or "").strip() or _configured_first("ANNOTATION_ANKI_MEANING_FIELDS")
            if field:
                fields[field] = entry.meaning

        changed_anki_fields = _changed("surface") or _changed("reading") or _changed("meaning")
        if not fields and changed_anki_fields:
            return {"ok": False, "error": "Cannot write this edit back to Anki because the target Anki field is not configured."}
        if not fields:
            return {"ok": True, "extra": extra}

        if not self._anki_connect_available_or_prompt():
            return self._anki_connection_unavailable_result(prompt=False)

        try:
            AnkiWordSync.from_client(self.anki).update_note_fields(note_id, fields)
        except Exception as exc:
            if self._is_anki_connection_error(exc):
                return self._anki_connection_unavailable_result()
            logger.warning("Failed to write annotation word edit back to Anki note %s: %s", note_id, exc, exc_info=True)
            return {"ok": False, "error": f"Failed to update Anki note {note_id}: {exc}"}

        extra["note_modified"] = time.strftime("%H:%M %d-%m-%Y")
        return {"ok": True, "extra": extra}

    def annotation_delete_word(self, key: str) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result()
        try:
            database, _provider = self._ensure_annotation_services()
            ok = database.delete_key(str(key or ""))
            self._refresh_annotation_runtime()
            return {"ok": bool(ok)}
        except Exception as exc:
            logger.exception("Failed to delete annotation word")
            return {"ok": False, "error": str(exc)}

    def annotation_import_words(self, path: str) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result()
        try:
            database, _provider = self._ensure_annotation_services()
            result = database.import_file(path, source="local", status="local_known")
            self._refresh_annotation_runtime()
            result["ok"] = True
            return result
        except Exception as exc:
            logger.exception("Failed to import annotation words")
            return {"ok": False, "error": str(exc)}

    def annotation_export_words(self, path: str) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result()
        try:
            database, _provider = self._ensure_annotation_services()
            result = database.export_file(path)
            result["ok"] = True
            return result
        except Exception as exc:
            logger.exception("Failed to export annotation words")
            return {"ok": False, "error": str(exc)}

    def annotation_refresh_words(self) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result(count=0)
        try:
            database, _provider = self._ensure_annotation_services()
            database.load()
            self._refresh_annotation_runtime()
            return {"ok": True, "count": len(database.list_entries())}
        except Exception as exc:
            logger.exception("Failed to refresh annotation words")
            return {"ok": False, "error": str(exc)}

    def annotation_anki_refresh(self) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result(decks=[], models=[])
        if not self._anki_connect_available_or_prompt():
            return self._anki_connection_unavailable_result(prompt=False, decks=[], models=[])
        try:
            sync = AnkiWordSync.from_client(self.anki)
            return {
                "ok": True,
                "decks": sync.deck_names(),
                "models": sync.model_names(),
            }
        except Exception as exc:
            if self._is_anki_connection_error(exc):
                return self._anki_connection_unavailable_result(decks=[], models=[])
            logger.warning("Failed to refresh Anki annotation metadata: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc), "decks": [], "models": []}

    def annotation_anki_model_fields(self, model_name: str) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result(fields=[])
        if not self._anki_connect_available_or_prompt():
            return self._anki_connection_unavailable_result(prompt=False, fields=[])
        try:
            sync = AnkiWordSync.from_client(self.anki)
            return {"ok": True, "fields": sync.model_field_names(model_name)}
        except Exception as exc:
            if self._is_anki_connection_error(exc):
                return self._anki_connection_unavailable_result(fields=[])
            logger.warning("Failed to refresh Anki model fields: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc), "fields": []}

    def annotation_sync_anki(self, settings: dict) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result(collected=0, skipped=0, failed=0)
        if not self._anki_connect_available_or_prompt():
            return self._anki_connection_unavailable_result(prompt=False, collected=0, skipped=0, skip_reasons={}, failed=0)
        try:
            database, _provider = self._ensure_annotation_services()
            sync_settings = self._annotation_anki_sync_settings(settings)
            result = AnkiWordSync.from_client(self.anki).sync(sync_settings)
            if result.error:
                if self._is_anki_connection_error(result.error):
                    return self._anki_connection_unavailable_result(
                        collected=result.collected,
                        skipped=result.skipped,
                        skip_reasons=dict(result.skip_reasons),
                        failed=result.failed,
                    )
                return {
                    "ok": False,
                    "error": result.error,
                    "collected": result.collected,
                    "skipped": result.skipped,
                    "skip_reasons": dict(result.skip_reasons),
                    "failed": result.failed,
                }
            missing_only = self._bool_setting(settings.get("missing_only"), default=False)
            if missing_only:
                entries_to_save = [
                    entry
                    for entry in result.entries
                    if database.get(entry.source, entry.normalized or entry.surface or entry.base) is None
                ]
                database.upsert_many(entries_to_save, preserve_existing=False, replace_source=None)
            else:
                entries_to_save = result.entries
                database.upsert_many(entries_to_save, preserve_existing=False, replace_source="anki")
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            self.config.set_many(
                {
                    "ANNOTATION_ANKI_LAST_SYNC": now,
                    "ANNOTATION_ANKI_LAST_COUNT": int(len(entries_to_save) if missing_only else result.collected),
                    "ANNOTATION_ANKI_LAST_SKIPPED": int(result.skipped),
                    "ANNOTATION_ANKI_LAST_FAILED": int(result.failed),
                }
            )
            self._refresh_annotation_runtime()
            return {
                "ok": True,
                "last_sync": now,
                "collected": result.collected,
                "added": len(entries_to_save),
                "missing_only": bool(missing_only),
                "skipped": result.skipped,
                "skip_reasons": dict(result.skip_reasons),
                "failed": result.failed,
                "elapsed_ms": result.elapsed_ms,
            }
        except Exception as exc:
            if self._is_anki_connection_error(exc):
                return self._anki_connection_unavailable_result(collected=0, skipped=0, skip_reasons={}, failed=0)
            logger.exception("Failed to sync annotation words from Anki")
            return {"ok": False, "error": str(exc)}

    def annotation_test_wanikani(self, token: str = "") -> dict:
        if not self._annotation_is_enabled():
            return {"ok": False, "message": "Annotation is disabled."}
        try:
            client = WaniKaniClient(token or self.config.get("ANNOTATION_WANIKANI_API_TOKEN") or "")
            ok, message = client.test_token()
            return {"ok": bool(ok), "message": message}
        except Exception as exc:
            logger.warning("Failed to test WaniKani token: %s", exc, exc_info=True)
            return {"ok": False, "message": str(exc)}

    def annotation_sync_wanikani(self, token: str = "") -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result()
        try:
            database, _provider = self._ensure_annotation_services()
            token_value = str(token or self.config.get("ANNOTATION_WANIKANI_API_TOKEN") or "").strip()
            client = WaniKaniClient(token_value)
            result = client.sync()
            if result.error:
                return {"ok": False, "error": result.error}
            database.upsert_many(result.entries, preserve_existing=False, replace_source="wanikani")
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            self.config.set_many(
                {
                    "ANNOTATION_WANIKANI_LAST_SYNC": now,
                    "ANNOTATION_WANIKANI_VOCAB_COUNT": int(result.vocabulary_count),
                    "ANNOTATION_WANIKANI_KANJI_COUNT": int(result.kanji_count),
                }
            )
            self._refresh_annotation_runtime()
            return {
                "ok": True,
                "last_sync": now,
                "vocabulary_count": result.vocabulary_count,
                "kanji_count": result.kanji_count,
                "failed": result.failed,
                "elapsed_ms": result.elapsed_ms,
            }
        except Exception as exc:
            logger.exception("Failed to sync WaniKani annotation words")
            return {"ok": False, "error": str(exc)}

    def annotation_clear_wanikani(self) -> dict:
        if not self._annotation_is_enabled():
            return self._annotation_disabled_result(removed=0)
        try:
            database, _provider = self._ensure_annotation_services()
            removed = database.delete_source("wanikani")
            self._refresh_annotation_runtime()
            return {"ok": True, "removed": removed}
        except Exception as exc:
            logger.exception("Failed to clear WaniKani annotation cache")
            return {"ok": False, "error": str(exc)}

    def _record_perf_sample(self, name: str, elapsed_ms: float) -> None:
        try:
            elapsed_ms = max(0.0, float(elapsed_ms))
        except Exception:
            return
        stats = getattr(self, "_perf_stats", None)
        if not isinstance(stats, dict):
            return
        count_key = f"{name}_count"
        total_key = f"{name}_total_ms"
        max_key = f"{name}_max_ms"
        stats[count_key] = int(stats.get(count_key, 0) or 0) + 1
        stats[total_key] = float(stats.get(total_key, 0.0) or 0.0) + elapsed_ms
        stats[max_key] = max(float(stats.get(max_key, 0.0) or 0.0), elapsed_ms)

    @staticmethod
    def _perf_avg(stats: dict, name: str) -> float:
        count = int(stats.get(f"{name}_count", 0) or 0)
        if count <= 0:
            return 0.0
        return float(stats.get(f"{name}_total_ms", 0.0) or 0.0) / count

    def get_performance_snapshot(self) -> str:
        stats = dict(getattr(self, "_perf_stats", {}) or {})
        renderer = getattr(self, "renderer", None)
        sub_manager = getattr(self, "sub_manager", None)
        ruby_stats = dict(getattr(sub_manager, "_ruby_stats", {}) or {})
        timing = dict(getattr(renderer, "_timing_data", {}) or {})

        def _line(label: str, name: str) -> str:
            return (
                f"{label}: count={int(stats.get(name + '_count', 0) or 0)} "
                f"avg={self._perf_avg(stats, name):.2f} ms "
                f"max={float(stats.get(name + '_max_ms', 0.0) or 0.0):.2f} ms"
            )

        dl_queue = getattr(sub_manager, "_dl_q", None)
        prepare_queue = getattr(sub_manager, "_episode_prepare_q", None)
        try:
            dl_size = dl_queue.qsize() if dl_queue is not None else 0
        except Exception:
            dl_size = 0
        try:
            prepare_size = prepare_queue.qsize() if prepare_queue is not None else 0
        except Exception:
            prepare_size = 0

        return "\n".join(
            [
                _line("Slider callback", "slider_change"),
                _line("Slider preview render", "slider_preview"),
                _line("Slider release", "slider_release"),
                _line("Subtitle render path", "subtitle_render"),
                _line("Episode switch", "episode_switch"),
                "",
                f"Renderer layout cache: hits={getattr(renderer, '_layout_cache_hits', 0)} "
                f"misses={getattr(renderer, '_layout_cache_misses', 0)} "
                f"entries={len(getattr(renderer, '_layout_cache', {}) or {})}",
                f"Renderer annotation cache: hits={getattr(renderer, '_annotation_segment_cache_hits', 0)} "
                f"misses={getattr(renderer, '_annotation_segment_cache_misses', 0)} "
                f"entries={len(getattr(renderer, '_annotation_segment_cache', {}) or {})}",
                f"Renderer preview cache: hits={getattr(renderer, '_preview_render_cache_hits', 0)} "
                f"misses={getattr(renderer, '_preview_render_cache_misses', 0)} "
                f"entries={len(getattr(renderer, '_preview_render_cache', {}) or {})}",
                f"Renderer timing: renders={int(timing.get('render_count', 0) or 0)} "
                f"render_total={float(timing.get('render_subtitle_time', 0.0) or 0.0) * 1000:.2f} ms "
                f"measure_total={float(timing.get('font_measure_time', 0.0) or 0.0) * 1000:.2f} ms "
                f"draw_total={float(timing.get('draw_outlined_text_time', 0.0) or 0.0) * 1000:.2f} ms "
                f"delete_total={float(timing.get('canvas_delete_time', 0.0) or 0.0) * 1000:.2f} ms "
                f"preview_show_total={float(timing.get('preview_cache_show_time', 0.0) or 0.0) * 1000:.2f} ms "
                f"text_items={int(timing.get('canvas_text_item_count', 0) or 0)}",
                f"Auto-ruby: hits={int(ruby_stats.get('cache_hits', 0) or 0)} "
                f"misses={int(ruby_stats.get('cache_misses', 0) or 0)} "
                f"generator_calls={int(ruby_stats.get('generator_calls', 0) or 0)} "
                f"generator_time={float(ruby_stats.get('generator_time', 0.0) or 0.0) * 1000:.2f} ms",
                f"OCR last duration: {float(getattr(self, '_last_ocr_duration_ms', 0.0) or 0.0):.2f} ms",
                f"Queues: downloads={dl_size} episode_preload={prepare_size}",
            ]
        )

    def reset_performance_stats(self) -> None:
        for key in list(getattr(self, "_perf_stats", {}) or {}):
            self._perf_stats[key] = 0.0 if key.endswith("_ms") else 0
        renderer = getattr(self, "renderer", None)
        if renderer is not None:
            renderer._layout_cache_hits = 0
            renderer._layout_cache_misses = 0
            renderer._annotation_segment_cache_hits = 0
            renderer._annotation_segment_cache_misses = 0
            renderer._preview_render_cache_hits = 0
            renderer._preview_render_cache_misses = 0
            timing = getattr(renderer, "_timing_data", None)
            if isinstance(timing, dict):
                for key in timing:
                    timing[key] = 0
        sub_manager = getattr(self, "sub_manager", None)
        ruby_stats = getattr(sub_manager, "_ruby_stats", None)
        if isinstance(ruby_stats, dict):
            for key, value in list(ruby_stats.items()):
                ruby_stats[key] = [] if isinstance(value, list) else 0

    def toggle_debugging(self) -> bool:
        enabled = not bool(self.config.get("DEBUGGING") or False)
        cfg = getattr(self.config, "config", None)
        if isinstance(cfg, dict):
            cfg["DEBUGGING"] = enabled
        try:
            self.config.set("DEBUGGING", enabled)
        except Exception:
            logger.debug("Failed to persist DEBUGGING=%s", enabled, exc_info=True)
        set_debug_logging(enabled)
        renderer = getattr(self, "renderer", None)
        if renderer is not None:
            try:
                renderer._timing_enabled = enabled
            except Exception:
                pass
        logger.info("Debug logging %s", "enabled" if enabled else "disabled")
        refresh_debugging_visibility = getattr(self.settings, "refresh_debugging_visibility", None)
        if callable(refresh_debugging_visibility):
            try:
                refresh_debugging_visibility()
            except Exception:
                logger.debug("Failed to refresh debug UI visibility", exc_info=True)
        status_var = getattr(self.settings, "_advanced_status_var", None)
        if status_var is not None:
            try:
                status_var.set(
                    "Debugging enabled. Performance tab is visible."
                    if enabled else
                    "Debugging disabled. Performance tab is hidden."
                )
            except Exception:
                pass
        return enabled

    def _set_busy_cursor(self, busy: bool) -> None:
        return self.anki_controller._set_busy_cursor(busy)

    def _hotkeys_disabled(self) -> bool:
        return self.hotkey_controller._hotkeys_disabled()

    def _reset_hotkey_state(self, reset_shift: bool = True) -> None:
        return self.hotkey_controller._reset_hotkey_state(reset_shift=reset_shift)

    # ---------------------------------------------------------------------
    # Runtime config changes
    # ---------------------------------------------------------------------
    def apply_advanced_settings(self, values: dict, persist: bool = True) -> None:
        if not isinstance(values, dict):
            return

        cfg = getattr(self.config, "config", None)
        if isinstance(cfg, dict):
            cfg.update(values)

        if {
            "ACTIVE_SETTINGS_PROFILE",
            "ANKI_ADD_HISTORY",
            "ANKI_ADD_COUNT_DATE",
            "ANKI_ADD_COUNT_TODAY",
        } & set(values):
            if "ACTIVE_SETTINGS_PROFILE" in values:
                self.anki_add_session_count = 0
            self._load_anki_add_history_state(persist=True)
            self._publish_anki_add_counts()

        def _as_int(key: str, default: int) -> int:
            try:
                return int(values.get(key, default))
            except Exception:
                return int(default)

        self.update_interval_ms = max(15, _as_int("UPDATE_INTERVAL_MS", self.update_interval_ms))
        self.hide_subtitles_ms = max(0, _as_int("SUBTITLE_TIMEOUT_MS", self.hide_subtitles_ms))
        self.windows_hide_control_ms = max(0, _as_int("WINDOWS_HIDE_DELAY_MS", self.windows_hide_control_ms))
        self.phone_windows_hide_control_ms = max(
            0, _as_int("PHONEMODE_WINDOWS_HIDE_DELAY_MS", self.phone_windows_hide_control_ms)
        )
        if "DEBUGGING" in values:
            renderer = getattr(self, "renderer", None)
            if renderer is not None:
                try:
                    renderer._timing_enabled = bool(values.get("DEBUGGING"))
                except Exception:
                    pass
        if "SUBTITLE_HOVER_PAUSE_VIDEO" in values:
            self.subtitle_hover_pause_video = bool(values.get("SUBTITLE_HOVER_PAUSE_VIDEO"))
        if "CONTROL_SHOW_ON_SUBTITLE_HOVER" in values:
            self.control_show_on_subtitle_hover = bool(values.get("CONTROL_SHOW_ON_SUBTITLE_HOVER"))
        if "PHONEMODE_SUBTITLE_HANDLE_ENABLED" in values:
            self.phone_subtitle_handle_enabled = bool(values.get("PHONEMODE_SUBTITLE_HANDLE_ENABLED"))
            self.overlay_controller.show_subtitle_handle(bool(self.settings.default_phone_mode))
        if "FAST_FORWARD_DISABLED" in values or "FAST_FORWARD_SPEED" in values:
            if self.playing:
                now = time.perf_counter()
                self.playback._advance_playing_time_to_now(
                    now=now,
                    allow_end_toggle=False,
                    update_display=False,
                )
                self.last_update = now
        if "FAST_FORWARD_DISABLED" in values:
            self.fast_forward_enabled = not bool(values.get("FAST_FORWARD_DISABLED"))
            self.fast_forward_active = bool(self.fast_forward_enabled)
        if "FAST_FORWARD_SPEED" in values:
            self.fast_forward_speed = self._coerce_fast_forward_speed(values.get("FAST_FORWARD_SPEED"))
        if "FAST_FORWARD_DISABLED" in values or "FAST_FORWARD_SPEED" in values:
            self.playback._set_play_button_state()

        if "AUDIO_PADDING" in values:
            self.audio_padding = float(values.get("AUDIO_PADDING"))

        if "POPUP_CLOSE_TIMER" in values:
            self.popup.close_delay = max(0, int(values.get("POPUP_CLOSE_TIMER")))
            popup = getattr(self.popup, "_popup", None)
            if popup is not None and popup.winfo_exists():
                if (
                    not getattr(self.popup, "_pinned", False)
                    and not getattr(self.popup, "_menu_open", False)
                    and not getattr(self.popup, "_dragging", False)
                ):
                    self.popup._restart_close()
        if "POPUP_HOVER_CLEAR_DELAY_MS" in values:
            coerce = getattr(self.popup, "_coerce_hover_clear_delay", None)
            if callable(coerce):
                self.popup.hover_clear_delay = coerce(values.get("POPUP_HOVER_CLEAR_DELAY_MS"))
            else:
                self.popup.hover_clear_delay = max(0, int(values.get("POPUP_HOVER_CLEAR_DELAY_MS")))

        self._apply_popup_style_settings(values)
        self._apply_subtitle_style_settings(values)
        self._apply_subtitle_cleaning_settings(values)
        self._apply_startup_settings(values)
        self._apply_hotkey_settings(values)
        self._apply_anki_settings(values)
        self._apply_annotation_settings(values)
        self._apply_voice_settings(values)

    def _apply_voice_settings(self, values: dict) -> None:
        if not any(str(key).startswith("VOICE_") for key in values):
            return
        if self._voice_microphone_test_running():
            self.voice_stop_microphone_test(restart_recognition=True)
            return
        try:
            self.voice.reconfigure()
        except Exception:
            logger.exception("Failed to apply voice command settings")

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
            "HOVER_DEFAULT_RUBY",
            "HOVER_DEFAULT_DICTIONARY",
            "HOVER_DEFAULT_STATUS",
            "HOVER_DEFAULT_TRANSLATION",
            "HOVER_POPUP_DEFAULT_RUBY",
            "HOVER_POPUP_DEFAULT_DICTIONARY",
            "HOVER_POPUP_DEFAULT_STATUS",
            "HOVER_POPUP_DEFAULT_TRANSLATION",
            "HOVER_LAYER_DELAY_MS",
            "SUBTITLE_HOVER_CLEAR_DELAY_MS",
            "SHIFT_HOVER_KANJI_DICTIONARY",
            "HOVER_DICTIONARY_ENABLED",
            "HOVER_DICTIONARY_HOTKEY",
            "HOVER_STATUS_ENABLED",
            "HOVER_STATUS_HOTKEY",
            "HOVER_TRANSLATION_ENABLED",
            "HOVER_TRANSLATION_HOTKEY",
            "GLOW_COLOR",
            "GLOW_RADIUS",
        }
        if any(k in values for k in subtitle_style_keys):
            self.last_subtitle_text = ""
            self.update_time_and_subtitle_displays()

    def _apply_subtitle_cleaning_settings(self, values: dict) -> None:
        subtitle_cleaning_keys = {
            "SUBTITLE_AUTO_RUBY",
            "ANKI_SPLIT_KANJI_MORAS",
            "SUBTITLE_SPEAKER_MODE",
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
            self._set_runtime_offset(off, persist_global=False, remember_anime=True)

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
            or any(str(k).startswith("SHORTCUT_") for k in values.keys())
            or any(str(k).startswith("DISABLE_HOTKEY_") for k in values.keys())
        ):
            self._reset_hotkey_state()

    def _apply_anki_settings(self, values: dict) -> None:
        if any(str(k).startswith("ANKI_") for k in values.keys()):
            try:
                close = getattr(self.anki, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass
            self.anki = AnkiClient(self.config)
            self.anki_busy_cursor = self.anki_busy_cursor or "wait"

    def _apply_annotation_settings(self, values: dict) -> None:
        if not any(str(k).startswith("ANNOTATION_") for k in values.keys()):
            return
        self._apply_annotation_runtime_config(values)
        self._refresh_annotation_runtime(redraw=True)

    # ---------------------------------------------------------------------
    # Time handling
    # ---------------------------------------------------------------------
    def update_loop(self):
        return self.playback.update_loop()

    def schedule_update(self):
        return self.playback.schedule_update()

    def set_current_time(self, t: float):
        return self.playback.set_current_time(t)

    def control_time_entry_return(self, event):
        return self.subtitle_navigation.control_time_entry_return(event)

    # ---------------------------------------------------------------------
    # Subtitle / episode / playback routing
    # ---------------------------------------------------------------------
    def update_time_and_subtitle_displays(self):
        return self.subtitle_navigation.update_time_and_subtitle_displays()

    def update_time_display(self):
        return self.subtitle_navigation.update_time_display()

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

    def toggle_fast_forward(self):
        return self.playback.toggle_fast_forward()

    def go_forward(self):
        return self.playback.go_forward()

    def go_back(self):
        return self.playback.go_back()

    def jump_subtitle_segment(self, direction: str) -> None:
        return self.playback.jump_subtitle_segment(direction)

    def on_jump_sub_end(self, event=None):
        return self.playback.on_jump_sub_end(event)

    def toggle_subtitle_visibility(self, event=None):
        return self.subtitle_navigation.toggle_subtitle_visibility(event)

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

    def _poll_settings_pointer_for_handle(self) -> None:
        if self._shutting_down:
            return

        inside = False
        try:
            inside = self._pointer_inside_settings_windows()
            if inside:
                self._hide_subtitle_handle_for_settings()
            elif bool(getattr(self, "_settings_pointer_inside", False)):
                self._restore_subtitle_handle_after_settings()
            if inside != bool(getattr(self, "_settings_pointer_inside", False)):
                self._settings_pointer_inside = inside
        except Exception:
            logger.debug("Failed to poll settings pointer for subtitle handle", exc_info=True)

        try:
            self._settings_pointer_job = self.settings.root.after(150, self._poll_settings_pointer_for_handle)
        except Exception:
            self._settings_pointer_job = None

    def _pointer_inside_settings_windows(self) -> bool:
        root = getattr(self.settings, "root", None)
        if root is None:
            return False
        px = int(root.winfo_pointerx())
        py = int(root.winfo_pointery())
        for win in (root, getattr(self.settings, "advanced_window", None)):
            if win is None:
                continue
            try:
                if not win.winfo_exists():
                    continue
                if str(win.state()) == "withdrawn":
                    continue
            except Exception:
                continue
            rects = []
            rect = get_window_screen_rect(win)
            if rect:
                rects.append(rect)
            try:
                rects.append(
                    (
                        int(win.winfo_rootx()),
                        int(win.winfo_rooty()),
                        int(win.winfo_rootx()) + int(win.winfo_width()),
                        int(win.winfo_rooty()) + int(win.winfo_height()),
                    )
                )
            except Exception:
                pass
            for left, top, right, bottom in rects:
                if left <= px <= right and top <= py <= bottom:
                    return True
        return False

    # ---------------------------------------------------------------------
    # OCR / Anki public routing
    # ---------------------------------------------------------------------
    def on_ocr_read_now(self, override: dict | None = None) -> None:
        return self.ocr_controller.on_ocr_read_now(override)

    def on_ocr_sync_now(self, override: dict | None = None) -> None:
        return self.ocr_controller.on_ocr_sync_now(override)

    def on_ocr_show_boxes(self, override: dict | None = None) -> int:
        return self.ocr_controller.show_ocr_boxes(override)

    # ---------------------------------------------------------------------
    # Offline voice commands
    # ---------------------------------------------------------------------
    def _on_voice_status_from_worker(self, status: dict) -> None:
        try:
            dispatch_to_tk(self.settings.root, self._publish_voice_status, dict(status or {}))
        except Exception:
            logger.debug("Failed to dispatch voice status", exc_info=True)

    def _publish_voice_status(self, status: dict) -> None:
        if self._shutting_down:
            return
        normalized = dict(status or {})
        normalized.setdefault("state", "disabled")
        normalized.setdefault("message", "")
        self._voice_last_status = normalized
        try:
            self.settings.set_voice_status(normalized)
        except Exception:
            logger.debug("Failed to publish voice status to settings", exc_info=True)

    def _report_voice_status(self, state: str, message: str, **extra) -> None:
        status = {"state": str(state), "message": str(message), **extra}
        try:
            dispatch_to_tk(self.settings.root, self._publish_voice_status, status)
        except Exception:
            logger.debug("Failed to report voice status", exc_info=True)

    def _on_voice_recognized(self, action: str, phrase: str, repeat_count: int = 1) -> None:
        del phrase
        if self._shutting_down:
            return
        self.hotkey_controller.enqueue_action(
            action,
            source="voice",
            repeat_count=repeat_count,
        )

    def toggle_voice_enabled(self) -> bool:
        return self.set_voice_enabled(not bool(self.config.get("VOICE_ENABLED") or False))

    def set_voice_enabled(self, enabled: bool) -> bool:
        enabled = bool(enabled)
        self.config.set("VOICE_ENABLED", enabled)
        try:
            self.settings.set_voice_enabled_value(enabled)
        except Exception:
            pass
        if self._voice_microphone_test_running():
            self._voice_mic_test_restart_recognition = bool(enabled)
        else:
            self.voice.reconfigure()
        return enabled

    def handle_voice_input_mode(self, mode: int | None = None) -> bool:
        try:
            current = int(getattr(self.settings, "input_mode", 1) or 1)
        except Exception:
            current = 1
        target = (1 if current == 3 else current + 1) if mode is None else mode
        setter = getattr(self.settings, "set_input_mode", None)
        if not callable(setter) or not setter(target):
            self._report_voice_status("error", f"Could not switch to input mode {target}.")
            return False
        self.hotkey_controller._reset_hotkey_state(reset_shift=False)
        self._report_voice_status("listening", f"Input mode M{int(target)} is active.")
        return True

    def voice_list_input_devices(self) -> list[dict]:
        return VoiceCommandService.list_input_devices()

    def voice_model_status(self, language: str) -> dict:
        return self.voice.model_status(language)

    def voice_download_model(self, language: str) -> bool:
        if self._voice_microphone_test_running():
            self.voice_stop_microphone_test(restart_recognition=False)
        return self.voice.download_model(language)

    def voice_cancel_model_download(self) -> bool:
        return self.voice.cancel_download()

    def voice_remove_model(self, language: str) -> bool:
        return self.voice.remove_model(language)

    def _voice_microphone_test_running(self) -> bool:
        thread = getattr(self, "_voice_mic_test_thread", None)
        return bool(thread is not None and thread.is_alive())

    def voice_set_microphone_monitor(self, enabled: bool) -> None:
        self._voice_mic_test_monitor_enabled = bool(enabled)

    def voice_test_microphone(
        self,
        requested: dict | None = None,
        monitor: bool = True,
    ) -> bool:
        if self._voice_microphone_test_running():
            self.voice_stop_microphone_test(restart_recognition=True)
            return False
        if str((self.voice.status or {}).get("state") or "") == "downloading":
            self._report_voice_status("error", "Finish or cancel the model download before testing the microphone.")
            return False

        requested = requested if isinstance(requested, dict) else self.config.get("VOICE_INPUT_DEVICE")
        self._voice_mic_test_monitor_enabled = bool(monitor)
        self._voice_mic_test_restart_recognition = bool(self.config.get("VOICE_ENABLED") or False)
        stop_event = threading.Event()
        self._voice_mic_test_stop = stop_event
        self.voice.stop(emit=False)
        self._publish_voice_status({"state": "testing", "message": "Opening microphone test..."})
        try:
            self.settings.set_voice_microphone_test_state(True, monitor_available=True)
            self.settings.set_voice_microphone_level(0.0, 0.0)
        except Exception:
            pass

        def _event_callback(event: dict) -> None:
            dispatch_to_tk(
                self.settings.root,
                self._publish_voice_microphone_test_event,
                stop_event,
                dict(event or {}),
            )

        def _worker() -> None:
            error = None
            try:
                VoiceCommandService.run_input_device_test(
                    requested or {},
                    stop_event,
                    monitor_getter=lambda: bool(self._voice_mic_test_monitor_enabled),
                    event_callback=_event_callback,
                )
            except Exception as exc:
                error = exc
                logger.exception("Voice microphone test failed")
            finally:
                dispatch_to_tk(
                    self.settings.root,
                    self._finish_voice_microphone_test,
                    stop_event,
                    error,
                )

        thread = threading.Thread(target=_worker, daemon=True, name="voice-microphone-test")
        self._voice_mic_test_thread = thread
        thread.start()
        return True

    def _publish_voice_microphone_test_event(self, stop_event: threading.Event, event: dict) -> None:
        if self._shutting_down or stop_event is not self._voice_mic_test_stop:
            return
        event_type = str(event.get("type") or "")
        if event_type == "level":
            try:
                self.settings.set_voice_microphone_level(
                    float(event.get("level") or 0.0),
                    float(event.get("peak") or 0.0),
                )
            except Exception:
                pass
            return
        if event_type != "started":
            return
        input_name = str((event.get("input_device") or {}).get("name") or "microphone").strip()
        output_name = str((event.get("output_device") or {}).get("name") or "").strip()
        monitor_available = bool(event.get("monitor_available"))
        message = f"Microphone test running on {input_name}."
        if output_name:
            message += f" Monitor output: {output_name}."
        else:
            message += " Audio monitoring is unavailable; the level meter is still active."
        self._publish_voice_status({"state": "testing", "message": message})
        try:
            self.settings.set_voice_microphone_test_state(True, monitor_available=monitor_available)
        except Exception:
            pass

    def _finish_voice_microphone_test(
        self,
        stop_event: threading.Event,
        error: Exception | None = None,
    ) -> None:
        if stop_event is not self._voice_mic_test_stop:
            return
        self._voice_mic_test_stop = None
        self._voice_mic_test_thread = None
        restart = bool(self._voice_mic_test_restart_recognition)
        self._voice_mic_test_restart_recognition = False
        try:
            self.settings.set_voice_microphone_level(0.0, 0.0)
            self.settings.set_voice_microphone_test_state(False, monitor_available=True)
        except Exception:
            pass
        if self._shutting_down:
            return
        voice_state = str((self.voice.status or {}).get("state") or "")
        if error is not None:
            self._publish_voice_status({"state": "error", "message": f"Microphone test failed: {error}"})
        elif voice_state != "downloading":
            self._publish_voice_status({"state": "ready", "message": "Microphone test stopped."})
        if restart and bool(self.config.get("VOICE_ENABLED") or False):
            self.voice.start()

    def voice_stop_microphone_test(
        self,
        *,
        restart_recognition: bool = True,
        wait: bool = False,
    ) -> bool:
        stop_event = getattr(self, "_voice_mic_test_stop", None)
        thread = getattr(self, "_voice_mic_test_thread", None)
        if stop_event is None:
            return False
        self._voice_mic_test_restart_recognition = bool(restart_recognition)
        stop_event.set()
        try:
            self.settings.set_voice_microphone_test_stopping()
        except Exception:
            pass
        if wait and thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        return True

    def voice_list_windows(self) -> list[dict]:
        excluded = set(getattr(self, "_app_window_hwnds", set()) or set())
        windows = [dict(item) for item in list_visible_windows(exclude_hwnds=excluded)]
        anime_candidates = self._voice_anime_title_candidates()
        for window in windows:
            title = self._normalize_voice_window_text(window.get("title"))
            window["anime_match"] = any(candidate and candidate in title for candidate in anime_candidates)
        windows.sort(
            key=lambda item: (
                0 if item.get("anime_match") else 1,
                str(item.get("process") or "").casefold(),
                str(item.get("title") or "").casefold(),
            )
        )
        return windows

    @staticmethod
    def _normalize_voice_window_text(value) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold()
        text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
        return re.sub(r"\s+", " ", text).strip()

    def _voice_anime_title_candidates(self) -> list[str]:
        values = []
        manager = getattr(self, "sub_manager", None)
        for getter_name in ("get_display_anime_name", "get_anime_name"):
            getter = getattr(manager, getter_name, None)
            if callable(getter):
                try:
                    values.append(getter())
                except Exception:
                    pass
        for key in ("LAST_DISPLAY_ANIME_NAME", "LAST_ANIME_NAME", "LAST_REMOTE_SEARCH_QUERY"):
            values.append(self.config.get(key))
        result = []
        for value in values:
            normalized = self._normalize_voice_window_text(value)
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def _resolve_voice_video_target(self) -> dict | None:
        windows = self.voice_list_windows()
        if not windows:
            return None
        saved = self.config.get("VOICE_PLAYBACK_TARGET")
        saved = saved if isinstance(saved, dict) else {}
        saved_title = self._normalize_voice_window_text(
            saved.get("title_filter") or saved.get("title")
        )
        saved_process = str(saved.get("process") or "").strip().casefold()

        def process_matches(window: dict) -> bool:
            return not saved_process or str(window.get("process") or "").casefold() == saved_process

        def saved_title_matches(window: dict) -> bool:
            return not saved_title or saved_title in self._normalize_voice_window_text(window.get("title"))

        for matcher in (
            lambda window: bool(window.get("anime_match")) and process_matches(window),
            lambda window: bool(saved_title) and saved_title_matches(window) and process_matches(window),
            lambda window: bool(window.get("anime_match")),
            lambda window: bool(saved_title) and saved_title_matches(window),
        ):
            match = next((window for window in windows if matcher(window)), None)
            if match is not None:
                return match

        capture_titles = [
            self._normalize_voice_window_text(value)
            for value in re.split(r"[,;]", str(self.config.get("POST_ADD_CAPTURE_TARGET_TITLE") or ""))
            if self._normalize_voice_window_text(value)
        ]
        match = next(
            (
                window
                for window in windows
                if process_matches(window)
                and any(
                    title_filter in self._normalize_voice_window_text(window.get("title"))
                    for title_filter in capture_titles
                )
            ),
            None,
        )
        if match is not None:
            return match
        process_windows = [window for window in windows if process_matches(window)] if saved_process else []
        return process_windows[0] if len(process_windows) == 1 else None

    @staticmethod
    def _complete_voice_video_request(request: dict, succeeded: bool) -> None:
        callback = request.get("on_complete") if isinstance(request, dict) else None
        if callable(callback):
            try:
                callback(bool(succeeded))
            except Exception:
                logger.debug("Voice video completion callback failed", exc_info=True)

    def handle_voice_playback_action(self, desired: bool | None, *, on_complete=None) -> None:
        if self._shutting_down:
            self._complete_voice_video_request({"on_complete": on_complete}, False)
            return
        if desired is None:
            desired = not bool(self.playing)
        else:
            desired = bool(desired)
            if bool(self.playing) == desired:
                state = "playing" if desired else "paused"
                self._report_voice_status("listening", f"Playback is already {state}.")
                self._complete_voice_video_request({"on_complete": on_complete}, True)
                return
        self._handle_voice_video_action(
            {
                "kind": "playback",
                "desired": bool(desired),
                "repeat_count": 1,
                "on_complete": on_complete,
            }
        )

    def handle_voice_seek_action(self, direction: str, *, repeat_count: int = 1, on_complete=None) -> None:
        direction = "back" if str(direction or "").strip().lower() == "back" else "forward"
        try:
            repeat_count = max(1, min(20, int(repeat_count)))
        except Exception:
            repeat_count = 1
        self._handle_voice_video_action(
            {
                "kind": "seek",
                "direction": direction,
                "repeat_count": repeat_count,
                "on_complete": on_complete,
            }
        )

    def handle_voice_time_jump(self, seconds: float) -> None:
        try:
            seconds = max(0.0, float(seconds))
        except Exception:
            return
        self.playback.set_current_time(seconds)
        self._report_voice_status("listening", f"Jumped subtitles to {format_time(seconds)}.")

    def resume_video_and_subtitles_after_capture(self, note_id: int) -> None:
        if not bool(self.config.get("POST_ADD_CAPTURE_RESUME_PLAYBACK") or False):
            return
        try:
            note_id = int(note_id)
        except Exception:
            return
        if note_id != int(getattr(self, "_latest_post_add_capture_note_id", 0) or 0):
            logger.debug("Skipping playback resume for superseded capture note %s", note_id)
            return
        if self._voice_playback_pending:
            try:
                self.settings.root.after(
                    100,
                    lambda: self.resume_video_and_subtitles_after_capture(note_id),
                )
            except Exception:
                pass
            return
        self._handle_voice_video_action(
            {
                "kind": "playback",
                "desired": True,
                "repeat_count": 1,
                "source": "capture",
                "target": {
                    "hwnd": int(getattr(self, "_latest_post_add_capture_window_hwnd", 0) or 0),
                },
            }
        )

    def _handle_voice_video_action(self, request: dict) -> None:
        if self._shutting_down:
            self._complete_voice_video_request(request, False)
            return
        if self._voice_playback_pending:
            self._report_voice_status("listening", "A voice video command is already running.")
            self._complete_voice_video_request(request, False)
            return
        picker = getattr(self, "_voice_target_window", None)
        try:
            if picker is not None and picker.winfo_exists():
                picker.deiconify()
                picker.lift()
                self._report_voice_status(
                    "listening",
                    "Choose the pending video target before sending another video command.",
                )
                self._complete_voice_video_request(request, False)
                return
        except Exception:
            pass
        requested_target = request.get("target")
        target = (
            dict(requested_target)
            if isinstance(requested_target, dict) and int(requested_target.get("hwnd") or 0) > 0
            else self._resolve_voice_video_target()
        )
        if target is None:
            self._show_voice_target_picker(dict(request))
            return
        self._run_voice_video_delivery(dict(request), target, allow_prompt=True)

    @staticmethod
    def _voice_video_hotkey_config(request: dict) -> tuple[str, str]:
        if str(request.get("kind") or "") == "playback":
            return "VOICE_PLAYBACK_HOTKEY", "play / pause"
        if str(request.get("direction") or "") == "back":
            return "VOICE_PLAYBACK_BACK_HOTKEY", "back"
        return "VOICE_PLAYBACK_FORWARD_HOTKEY", "forward"

    def _suppress_external_hotkey_events(
        self,
        hotkey: str,
        repeat_count: int,
        repeat_interval_ms: int = 120,
    ) -> None:
        aliases = {
            "arrowleft": "left",
            "arrowright": "right",
            "spacebar": "space",
            "control": "ctrl",
            "ctl": "ctrl",
            "rightalt": "altgr",
            "altgraph": "altgr",
        }
        tokens = []
        for raw_token in re.split(r"\s*\+\s*", str(hotkey or "").strip().casefold()):
            token = aliases.get(raw_token.strip().replace(" ", ""), raw_token.strip().replace(" ", ""))
            if token == "altgr":
                tokens.extend(("ctrl", "alt"))
            elif token:
                tokens.append(token)
        repeat_duration = max(0, int(repeat_count) - 1) * max(0, int(repeat_interval_ms)) / 1000.0
        deadline = time.perf_counter() + max(0.6, repeat_duration + 0.45)
        for token in tokens:
            self._suppress_synthetic_tokens_until[token] = deadline
        if "space" in tokens:
            self._suppress_synthetic_space_until = deadline

    def _run_voice_video_delivery(self, request: dict, target: dict, *, allow_prompt: bool) -> None:
        if self._voice_playback_pending:
            self._complete_voice_video_request(request, False)
            return
        config_key, action_label = self._voice_video_hotkey_config(request)
        default_hotkey = {"VOICE_PLAYBACK_HOTKEY": "space", "VOICE_PLAYBACK_BACK_HOTKEY": "left"}.get(
            config_key,
            "right",
        )
        hotkey = str(self.config.get(config_key) or default_hotkey).strip()
        if not hotkey:
            self._report_voice_status("error", f"Voice {action_label} hotkey is empty.")
            self._complete_voice_video_request(request, False)
            return
        try:
            repeat_count = max(1, min(20, int(request.get("repeat_count") or 1)))
        except Exception:
            repeat_count = 1
        try:
            repeat_interval_ms = max(
                40,
                min(1000, int(self.config.get("VOICE_REPEAT_HOTKEY_INTERVAL_MS") or 120)),
            )
        except Exception:
            repeat_interval_ms = 120
        previous_playing = bool(self.playing)
        local_state = {"applied": False}
        self._voice_playback_pending = True
        excluded = set(getattr(self, "_app_window_hwnds", set()) or set())
        self._report_voice_status("working", f"Sending {action_label} command to the video window...")

        def _worker() -> None:
            def _sync_local_playback_before_send(_window: dict) -> None:
                if str(request.get("kind") or "") != "playback":
                    return
                def _apply() -> None:
                    if not self._shutting_down:
                        self.playback.set_playing(bool(request.get("desired")))
                        local_state["applied"] = True

                dispatch_to_tk_sync(self.settings.root, _apply)

            self._suppress_external_hotkey_events(hotkey, repeat_count, repeat_interval_ms)
            result = send_hotkey_to_window(
                target,
                hotkey,
                exclude_hwnds=excluded,
                restore_foreground=True,
                repeat_count=repeat_count,
                repeat_interval_ms=repeat_interval_ms,
                before_send=(
                    _sync_local_playback_before_send
                    if str(request.get("kind") or "") == "playback"
                    else None
                ),
            )

            def _finish() -> None:
                self._voice_playback_pending = False
                if self._shutting_down:
                    return
                if result.get("ok"):
                    if str(request.get("kind") or "") == "playback":
                        desired = bool(request.get("desired"))
                        self.playback.set_playing(desired)
                        state = "playing" if desired else "paused"
                        self._report_voice_status("listening", f"Video and subtitles are now {state}.")
                    else:
                        direction = "back" if str(request.get("direction") or "") == "back" else "forward"
                        callback = self.playback.go_back if direction == "back" else self.playback.go_forward
                        for _ in range(repeat_count):
                            callback()
                        repetitions = f" {repeat_count} times" if repeat_count > 1 else ""
                        self._report_voice_status(
                            "listening",
                            f"Moved video and subtitles {direction}{repetitions}.",
                        )
                    self._complete_voice_video_request(request, True)
                    return
                if allow_prompt:
                    if local_state.get("applied"):
                        self.playback.set_playing(previous_playing)
                    self._show_voice_target_picker(dict(request))
                    return
                if local_state.get("applied"):
                    self.playback.set_playing(previous_playing)
                reason = str(result.get("reason") or "unknown error").replace("_", " ")
                self._report_voice_status(
                    "error",
                    f"Video {action_label} command failed ({reason}); subtitles were not changed.",
                )
                self._complete_voice_video_request(request, False)

            dispatch_to_tk(self.settings.root, _finish)

        threading.Thread(target=_worker, daemon=True, name="voice-video-target").start()

    def _show_voice_target_picker(self, request: dict) -> None:
        existing = getattr(self, "_voice_target_window", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                return
        except Exception:
            pass

        windows = self.voice_list_windows()
        if not windows:
            self._report_voice_status(
                "error",
                "No external video window is available; the command was not applied.",
            )
            self._complete_voice_video_request(request, False)
            return

        win = tk.Toplevel(self.settings.root)
        self._voice_target_window = win
        win.title("Select Video Window")
        win.attributes("-topmost", True)
        win.resizable(True, False)
        outer = tk.Frame(win, padx=12, pady=12)
        outer.pack(fill="both", expand=True)
        tk.Label(
            outer,
            text="The saved video window was not found. Select the window that should receive the video hotkey.",
            justify="left",
            anchor="w",
            wraplength=620,
        ).pack(fill="x", pady=(0, 8))
        selected = tk.StringVar(value="")
        combo = ttk.Combobox(outer, textvariable=selected, state="readonly", width=78)
        combo.pack(fill="x")
        mapping: dict[str, dict] = {}

        def _refresh() -> None:
            mapping.clear()
            for item in self.voice_list_windows():
                label = f"{item.get('title', '')} - {item.get('process', '')}".strip(" -")
                unique = label
                suffix = 2
                while unique in mapping:
                    unique = f"{label} ({suffix})"
                    suffix += 1
                mapping[unique] = item
            combo.configure(values=list(mapping))
            if mapping:
                selected.set(next(iter(mapping)))

        def _close(message: str | None = None) -> None:
            try:
                win.grab_release()
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass
            self._voice_target_window = None
            if message:
                self._report_voice_status("error", message)

        def _use_selected() -> None:
            item = mapping.get(selected.get())
            if not item:
                self.settings.root.bell()
                return
            target = {
                "title_filter": str(item.get("title") or ""),
                "process": str(item.get("process") or ""),
            }
            self.config.set("VOICE_PLAYBACK_TARGET", target)
            try:
                self.settings.set_voice_playback_target(target)
            except Exception:
                pass
            _close()
            self._run_voice_video_delivery(dict(request), target, allow_prompt=False)

        def _cancel() -> None:
            _close("Video target selection cancelled; the command was not applied.")
            self._complete_voice_video_request(request, False)

        buttons = tk.Frame(outer)
        buttons.pack(fill="x", pady=(10, 0))
        tk.Button(buttons, text="Refresh", width=10, command=_refresh).pack(side="left")
        tk.Button(buttons, text="Use Selected", width=14, command=_use_selected).pack(side="right")
        tk.Button(
            buttons,
            text="Cancel",
            width=10,
            command=_cancel,
        ).pack(side="right", padx=(0, 6))
        _refresh()
        win.protocol(
            "WM_DELETE_WINDOW",
            _cancel,
        )
        win.bind("<Return>", lambda _event: _use_selected())
        win.bind(
            "<Escape>",
            lambda _event: _cancel(),
        )
        win.update_idletasks()
        width = max(560, int(win.winfo_reqwidth()))
        height = max(150, int(win.winfo_reqheight()))
        try:
            px, py = self.settings.root.winfo_pointerxy()
            monitor = next(
                (
                    rect
                    for rect in get_monitor_rects(self.settings.root)
                    if rect[0] <= px < rect[0] + rect[2] and rect[1] <= py < rect[1] + rect[3]
                ),
                (0, 0, self.settings.root.winfo_screenwidth(), self.settings.root.winfo_screenheight()),
            )
            mx, my, mw, mh = monitor
            x = mx + max(0, (mw - width) // 2)
            y = my + max(0, (mh - height) // 2)
            win.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass
        try:
            win.grab_set()
            win.focus_force()
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Shutdown
    # ---------------------------------------------------------------------
    def _on_app_close(self):
        self.shutdown(destroy_root=True, save_state=True)

    def _on_root_destroy(self, event=None):
        try:
            if event is not None and event.widget is not self.settings.root:
                return
        except Exception:
            pass
        self.shutdown(destroy_root=False, save_state=False)

    def shutdown(self, destroy_root: bool = True, save_state: bool = True) -> None:
        if getattr(self, "_shutting_down", False):
            if destroy_root:
                self._finalize_shutdown()
            return

        self._shutting_down = True
        try:
            self._shutdown_event.set()
        except Exception:
            pass

        root = getattr(self.settings, "root", None)
        root_alive = False
        try:
            root_alive = root is not None and root.winfo_exists()
        except Exception:
            root_alive = False

        if root_alive and save_state:
            try:
                self._save_geometry_and_session_state()
            except Exception as e:
                logger.debug("Failed to save state during shutdown: %s", e, exc_info=True)

        self._stop_listeners_and_jobs()
        self._close_auxiliary_windows()
        self._shutdown_background_services()

        if destroy_root:
            self._finalize_shutdown()

    def _save_geometry_and_session_state(self) -> None:
        def _read_settings_geometry():
            geo = self.settings.root.winfo_geometry()
            size, pos = geo.split("+", 1)
            w_s, h_s = size.split("x", 1)
            x_s, y_s = pos.split("+", 1)
            return int(x_s), int(y_s), int(w_s), int(h_s)

        try:
            self.episode_controller.save_current_episode_position()
        except Exception as e:
            logger.debug("Failed to save current episode resume position: %s", e, exc_info=True)
        try:
            self._remember_current_anime_offset()
        except Exception as e:
            logger.debug("Failed to save current anime offset: %s", e, exc_info=True)
        
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
                try:
                    listener.stop()
                except Exception:
                    pass
                setattr(self, listener_attr, None)
        root = getattr(self.settings, "root", None)
        for job in ("subtitle_timeout_job", "_con_hide_job", "_input_pump_job",
                    "_repeat_job", "_ocr_job", "_update_loop_job", "_slider_render_job",
                    "_anki_success_popup_job", "_ocr_box_preview_job",
                    "_settings_pointer_job"):
            handle = getattr(self, job, None)
            if handle is not None:
                try:
                    if root is not None and root.winfo_exists():
                        root.after_cancel(handle)
                except Exception:
                    pass
                setattr(self, job, None)
        try:
            self.popup._cancel_close()
        except Exception:
            pass

    def _close_auxiliary_windows(self) -> None:
        for close_call in (
            lambda: self.popup._close(),
            lambda: self.renderer.destroy_hover_windows(),
            lambda: self.ocr_controller._close_ocr_box_previews(),
        ):
            try:
                close_call()
            except Exception:
                pass

        for attr in ("_anki_wait_window", "_anki_preview_window", "_anki_success_popup", "_voice_target_window"):
            win = getattr(self, attr, None)
            if win is None:
                continue
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
            setattr(self, attr, None)

        for win in (
            getattr(self.settings, "advanced_window", None),
            getattr(self.overlay, "subtitle_handle", None),
            getattr(self.overlay, "sub_window", None),
            getattr(self.settings, "control_window", None),
        ):
            if win is None:
                continue
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass

    def _shutdown_background_services(self) -> None:
        try:
            self.voice_stop_microphone_test(restart_recognition=False, wait=True)
        except Exception:
            pass
        try:
            self.voice.shutdown()
        except Exception:
            pass
        try:
            self._ocr_generation += 1
            self._ocr_sync_generation += 1
        except Exception:
            pass
        try:
            shutdown = getattr(self.sub_manager, "shutdown", None)
            if callable(shutdown):
                shutdown()
        except Exception:
            pass
        try:
            close = getattr(self.anki, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    def _finalize_shutdown(self) -> None:
        try:
            root = getattr(self.settings, "root", None)
            if root is not None and root.winfo_exists():
                dispatcher = getattr(root, "_tk_main_thread_dispatcher", None)
                if dispatcher is not None:
                    try:
                        dispatcher.close()
                    except Exception:
                        pass
                root.quit()
                root.destroy()
        except Exception:
            pass
    





    # ---------------------------------------------------------------------
    # Compatibility helpers for benchmarks / older call sites
    # ---------------------------------------------------------------------

    def _segments_to_copy_text(self, top_segments, bottom_segments):
        return self.subtitle_navigation.segments_to_copy_text(top_segments, bottom_segments)

    def _update_subtitle_display(
        self,
        force: bool = False,
        allow_auto_ruby: bool = True,
        schedule_auto_ruby: bool = False,
        preview: bool = False,
    ):
        return self.subtitle_navigation._update_subtitle_display(
            force=force,
            allow_auto_ruby=allow_auto_ruby,
            schedule_auto_ruby=schedule_auto_ruby,
            preview=preview,
        )
    

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
