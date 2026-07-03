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
from typing import Any
import logging

from pynput.keyboard import Listener as KeyboardListener
from pynput.mouse import Listener as MouseListener

from model.anki_client import AnkiClient, AnkiConnectRequestError
from model.anki_word_sync import AnkiSyncSettings, AnkiWordSync, split_csv_values
from model.annotation_provider import AnnotationProvider
from model.config_manager import ConfigManager
from model.renderer import SubtitleRenderer
from model.subtitle_manager import SubtitleManager
from model.wanikani_client import WaniKaniClient
from model.word_database import WordDatabase, WordEntry
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
from utils import get_window_screen_rect

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
        "SHORTCUT_TOGGLE_FAST_FORWARD": "f",
        "SHORTCUT_TOGGLE_DEBUGGING": "ctrl+shift+d",
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
        "toggle_fast_forward": "DISABLE_HOTKEY_TOGGLE_FAST_FORWARD",
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
        self.hide_subtitles_ms = self.config.get("SUBTITLE_TIMEOUT_MS")
        self.update_interval_ms = self.config.get("UPDATE_INTERVAL_MS")
        self.anki_busy_cursor = self.config.get("ANKI_BUSY_CURSOR") or "wait"
        self.video_click = bool(self.config.get("VIDEO_CLICK") or False)
        self.video_click_play = True if self.config.get("VIDEO_CLICK_PLAY") is None else bool(self.config.get("VIDEO_CLICK_PLAY"))
        self.video_click_window = False if self.config.get("VIDEO_CLICK_WINDOW") is None else bool(self.config.get("VIDEO_CLICK_WINDOW"))
        self.subtitle_hover_pause_video = bool(self.config.get("SUBTITLE_HOVER_PAUSE_VIDEO") or False)
        self.fast_forward_active = False
        self.fast_forward_speed = self._coerce_fast_forward_speed(self.config.get("FAST_FORWARD_SPEED"))
        self._hover_video_pause_active = False
        self._hover_timer_pause_active = False
        self._suppress_synthetic_space_until = 0.0

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
        speed = max(1.0, min(8.0, speed))
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
        self.settings.bind_set_to_return            (self.subtitle_navigation.on_set_to_return)
        self.settings.bind_time_entry_return        (self.subtitle_navigation.control_time_entry_return)
        self.settings.bind_time_entry_clear         (self.subtitle_navigation.control_clear_time_entry)
        self.settings.bind_control_window_enter     (self.overlay_controller.control_window_enter)
        self.settings.bind_control_window_leave     (self.overlay_controller.control_window_leave)
        self.settings.bind_show_subtitle_handle     (self.overlay_controller.show_subtitle_handle)
        self.settings.bind_refresh_subtitles        (self.subtitle_navigation.on_refresh_subtitles)
        self.settings.bind_toggle_subtitles         (self.subtitle_navigation.toggle_subtitle_visibility)
        self.settings.bind_advanced_apply           (self.apply_advanced_settings)
        self.settings.bind_ocr_read_now             (self.ocr_controller.on_ocr_read_now)
        self.settings.bind_ocr_sync_now             (self.ocr_controller.on_ocr_sync_now)
        self.settings.bind_ocr_show_boxes           (self.ocr_controller.show_ocr_boxes)
        self.settings.bind_anki_check               (self.anki_controller.on_anki_check_connection)
        self.settings.bind_performance_snapshot     (self.get_performance_snapshot)
        self.settings.bind_performance_reset        (self.reset_performance_stats)
        self.settings.bind_settings_open            (self._hide_subtitle_handle_for_settings)
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
        self.popup.bind_translation_state           (lambda: bool(self.translation_pressed))
        self.popup.bind_translation_provider        (lambda: str(self.translation_provider or "deepl"))
        self.popup.bind_anchor_window               (self._popup_anchor_window)
        self.renderer.bind_dictionary_lookup        (self._lookup_dictionary_entry)
        self.renderer.bind_word_tokenizer           (self._word_spans_for_lookup)
        self.renderer.bind_annotation_provider      (self.annotation_provider if self._annotation_is_enabled() else None)
        self.renderer.bind_shift_state              (lambda: self._plain_shift_hover_active() and not self._popup_is_open())
        self.overlay.bind_sub_window_enter          (self.sub_window_enter)
        self.overlay.bind_sub_window_leave          (self.sub_window_leave)
        self.overlay.bind_sub_handle_enter          (self.sub_handle_enter)   
        self.settings.root.bind                     ("<Enter>", lambda _e: self._hide_subtitle_handle_for_settings(), add="+")
        self.settings.root.bind                     ("<Leave>", lambda _e: self._restore_subtitle_handle_after_settings(), add="+")
        self._settings_pointer_job = self.settings.root.after(150, self._poll_settings_pointer_for_handle)

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

    def _get_display_end_times(self):
        return self.episode_controller._get_display_end_times()

    def _on_copy_popup(self, event=None):
        try:
            self.renderer._clear_hover_ruby()
        except Exception:
            pass
        self.popup.open_copy_popup(self.last_subtitle_raw)
        return "break"

    def _skip_buttons_use_subtitle_segments(self) -> bool:
        return self.hotkey_controller._skip_buttons_use_subtitle_segments()

    def _add_selection_to_anki(self, selected_text: str, subtitle_text: str = "") -> None:
        return self.anki_controller._add_selection_to_anki(selected_text, subtitle_text)

    def _lookup_dictionary_entry(self, text: str, allow_translation_fallback: bool = False) -> str:
        try:
            return self.anki.lookup_dictionary_entry(
                text,
                allow_translation_fallback=allow_translation_fallback,
            )
        except Exception:
            return ""

    def _translate_hover_selection(self, text: str, provider: str = "deepl") -> str:
        try:
            return self.anki.translate_hover_selection(text, provider=provider)
        except Exception:
            return ""

    def _popup_is_open(self) -> bool:
        try:
            return bool(self.popup.is_open())
        except Exception:
            return False

    def _plain_shift_hover_active(self) -> bool:
        if not bool(self.config.get("SHIFT_HOVER_KANJI_DICTIONARY") or False):
            return False
        return bool(self.shift_pressed) and not bool(self.ctrl_pressed) and not bool(self.alt_pressed)

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
                f"Renderer timing: renders={int(timing.get('render_count', 0) or 0)} "
                f"render_total={float(timing.get('render_subtitle_time', 0.0) or 0.0) * 1000:.2f} ms "
                f"measure_total={float(timing.get('font_measure_time', 0.0) or 0.0) * 1000:.2f} ms",
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
        if "SUBTITLE_HOVER_PAUSE_VIDEO" in values:
            self.subtitle_hover_pause_video = bool(values.get("SUBTITLE_HOVER_PAUSE_VIDEO"))
        if "FAST_FORWARD_SPEED" in values:
            self.fast_forward_speed = self._coerce_fast_forward_speed(values.get("FAST_FORWARD_SPEED"))
            self.playback._set_play_button_state()

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
        self._apply_annotation_settings(values)

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
            "SHIFT_HOVER_KANJI_DICTIONARY",
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
            if inside != bool(getattr(self, "_settings_pointer_inside", False)):
                self._settings_pointer_inside = inside
                if inside:
                    self._hide_subtitle_handle_for_settings()
                else:
                    self._restore_subtitle_handle_after_settings()
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
            rect = get_window_screen_rect(win)
            if not rect:
                continue
            left, top, right, bottom = rect
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

        for attr in ("_anki_wait_window", "_anki_success_popup"):
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
