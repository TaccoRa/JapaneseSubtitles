"""Keyboard shortcut, repeat-action, and global hotkey helper."""

import ctypes
import logging
import re
import time
import queue
import unicodedata
from pynput.mouse import Button
from typing import Any
from pynput.keyboard import Key
from model.voice_commands import decode_voice_hover_layers, decode_voice_sequence
from utils import (
    get_window_root_hwnd,
    get_window_screen_rect,
    is_any_window_foreground,
    move_pointer_to_monitor_bottom,
)

logger = logging.getLogger(__name__)


class _QueuedInputAction(str):
    """A queue-compatible semantic action with its input origin attached.

    Existing callers and tests intentionally treat queued actions as strings inside
    two-item ``(action, event_time)`` tuples.  A string subclass lets the input
    pump retain that shape while carrying enough provenance to safely drop global
    actions while a modal search/file dialog owns keyboard input.
    """

    __slots__ = ("source",)

    def __new__(cls, action: str, source: str = "internal"):
        value = str.__new__(cls, str(action))
        value.source = str(source or "internal").strip().lower() or "internal"
        return value


class _ControllerProxy:
    """Proxy base that forwards attribute access and assignment to SubtitleController."""

    def __init__(self, controller: Any) -> None:
        object.__setattr__(self, "controller", controller)

    def __getattr__(self, name: str):
        return getattr(self.controller, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "controller":
            object.__setattr__(self, name, value)
        else:
            setattr(self.controller, name, value)

class HotkeyController(_ControllerProxy):
    """Keyboard shortcut, repeat-action, and global hotkey helper."""

    _SHORTCUT_DEFAULT_OVERRIDES = {
        "SHORTCUT_CAPTURE_ONLY": "shift+v",
    }
    _HOTKEY_DISABLE_OVERRIDES = {
        "capture_only": "DISABLE_HOTKEY_CAPTURE_ONLY",
    }

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)
        if not hasattr(controller, "_pressed_key_tokens"):
            controller._pressed_key_tokens = set()
        if not hasattr(controller, "_single_fire_actions"):
            controller._single_fire_actions = set()
        controller._last_polled_hover_mode = "ruby"
        controller._hover_refresh_requested = False
        controller._physical_toggle_subtitles_active = False
        # pynput callbacks run on listener threads, so they may only read this Tk-derived cache.
        controller._cached_text_input_focused = False
        controller._cached_popup_open = False
        controller._cached_app_window_rects = []
        controller._cached_subtitle_window_rect = None
        controller._cached_modal_input_active = False
        controller._voice_sequence_steps = []
        controller._voice_sequence_running = False
        if not hasattr(controller, "_voice_action_generation"):
            controller._voice_action_generation = 0
        if not hasattr(controller, "_voice_frozen_targets"):
            controller._voice_frozen_targets = {}
        if not hasattr(controller, "_voice_frozen_target_counter"):
            controller._voice_frozen_target_counter = 0
        self._active_voice_target = None

    def _focus_owner_windows(self) -> list[Any]:
            return [
                getattr(self.settings, "root", None),
                getattr(self.settings, "control_window", None),
                getattr(self.settings, "advanced_window", None),
                getattr(getattr(self.controller, "overlay", None), "sub_window", None),
                getattr(self.popup, "_popup", None),
                getattr(self.popup, "_hover_ruby_window", None),
            ]

    @staticmethod
    def _window_exists(window) -> bool:
            if window is None:
                return False
            try:
                return bool(window.winfo_exists())
            except Exception:
                return False

    def _point_inside_any_app_window(self, x: int, y: int) -> bool:
            for rect in list(getattr(self.controller, "_cached_app_window_rects", ()) or ()):
                try:
                    left, top, right, bottom = rect
                    if int(left) <= int(x) < int(right) and int(top) <= int(y) < int(bottom):
                        return True
                except Exception:
                    continue
            return False

    def _point_inside_subtitle_window(self, x: int, y: int) -> bool:
            rect = getattr(self.controller, "_cached_subtitle_window_rect", None)
            if not rect:
                return False
            try:
                left, top, right, bottom = rect
                return int(left) <= int(x) < int(right) and int(top) <= int(y) < int(bottom)
            except Exception:
                return False

    def _reset_transient_hotkey_state_for_external_click(self, x: int, y: int, button) -> None:
            if button in {Button.x1, Button.x2, Button.middle}:
                return
            try:
                if self._point_inside_any_app_window(int(x), int(y)):
                    return
            except Exception:
                pass
            self._reset_hotkey_state(reset_shift=True)

    def _refresh_app_window_hwnds(self) -> set[int]:
            hwnds: set[int] = set()
            rects = []
            subtitle_window = getattr(getattr(self.controller, "overlay", None), "sub_window", None)
            subtitle_rect = None
            for owner in self._focus_owner_windows():
                if owner is None:
                    continue
                hwnd = get_window_root_hwnd(owner)
                if hwnd:
                    hwnds.add(int(hwnd))
                if not self._window_exists(owner):
                    continue
                rect = get_window_screen_rect(owner)
                if rect:
                    rects.append(tuple(int(value) for value in rect))
                    if owner is subtitle_window and self._window_is_shown(owner):
                        subtitle_rect = tuple(int(value) for value in rect)
            self.controller._app_window_hwnds = hwnds
            self.controller._cached_app_window_rects = rects
            self.controller._cached_subtitle_window_rect = subtitle_rect
            return hwnds

    def _refresh_ui_state_cache(self) -> None:
            self._refresh_app_window_hwnds()
            self.controller._cached_text_input_focused = self._read_text_input_focused()
            self.controller._cached_popup_open = self._read_popup_open()
            previous = bool(getattr(self.controller, "_cached_modal_input_active", False))
            active = self._read_modal_input_active()
            self.controller._cached_modal_input_active = active
            if active and not previous:
                self._clear_global_input_state_for_modal()

    @staticmethod
    def _action_source(action) -> str:
            return str(getattr(action, "source", "internal") or "internal").strip().lower() or "internal"

    @staticmethod
    def _is_modal_blocked_source(source: str) -> bool:
            return str(source or "").strip().lower() in {"physical", "mouse", "voice", "synthetic"}

    def _read_modal_input_active(self) -> bool:
            """Read modal input state only from the Tk-thread input pump."""
            manager = getattr(self.controller, "sub_manager", None)
            for name in ("is_modal_input_active", "is_search_dialog_active"):
                checker = getattr(manager, name, None)
                if not callable(checker):
                    continue
                try:
                    if bool(checker()):
                        return True
                except Exception:
                    logger.debug("Failed to read modal input state", exc_info=True)
            root = getattr(getattr(self.controller, "settings", None), "root", None)
            try:
                return bool(int(getattr(root, "_subtitleplayer_modal_input_open_count", 0) or 0) > 0)
            except Exception:
                return False

    def _modal_input_blocked(self) -> bool:
            """Listener-thread-safe modal state cached by ``_refresh_ui_state_cache``."""
            return bool(getattr(self.controller, "_cached_modal_input_active", False))

    def _discard_frozen_voice_target(self, action) -> None:
            value = str(action or "")
            if not value.startswith("voice_target:"):
                return
            _prefix, _separator, rest = value.partition(":")
            target_id_text, separator, _actual = rest.partition(":")
            if not separator:
                return
            try:
                target_id = int(target_id_text)
            except (TypeError, ValueError):
                return
            targets = getattr(self.controller, "_voice_frozen_targets", None)
            if isinstance(targets, dict):
                targets.pop(target_id, None)

    def _drop_pending_modal_blocked_actions(self) -> None:
            kept = []
            while True:
                try:
                    item = self._input_actions.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
                action = item[0] if isinstance(item, tuple) else item
                if self._is_modal_blocked_source(self._action_source(action)):
                    self._discard_frozen_voice_target(action)
                    continue
                kept.append(item)
            for item in kept:
                try:
                    self._input_actions.put_nowait(item)
                except Exception:
                    break

    def _clear_global_input_state_for_modal(self) -> None:
            """Stop held/repeated global actions when a modal dialog opens."""
            self._reset_hotkey_state(reset_shift=True)
            self._drop_pending_modal_blocked_actions()

    def _enqueue_input_action(
        self,
        action: str,
        event_time: float | None = None,
        *,
        source: str = "internal",
    ) -> None:
            if self._shutting_down:
                return
            timestamp = time.perf_counter() if event_time is None else float(event_time)
            queued_action = _QueuedInputAction(action, source)
            self._input_actions.put_nowait((queued_action, timestamp))

    def enqueue_action(
        self,
        action: str,
        event_time: float | None = None,
        *,
        source: str = "ui",
        repeat_count: int = 1,
    ) -> None:
            """Thread-safe public entry point for semantic input actions."""
            try:
                count = max(1, min(20, int(repeat_count)))
            except Exception:
                count = 1
            if count == 1:
                self._enqueue_input_action(str(action), event_time=event_time, source=source)
                return
            if self._shutting_down:
                return
            timestamp = time.perf_counter() if event_time is None else float(event_time)
            self._input_actions.put_nowait((_QueuedInputAction(action, source), timestamp, count))

    @staticmethod
    def _voice_action_uses_lookup_target(action: str) -> bool:
            value = str(action or "")
            if value.startswith("voice_hover_layers:"):
                return True
            return value in {
                "voice_add",
                "voice_add_together",
                "voice_capture",
                "voice_capture_together",
                "voice_dictionary",
                "voice_dictionary_together",
                "voice_translate",
                "voice_translate_each",
                "voice_status",
                "voice_database",
                "voice_copy",
                "voice_copy_kanji",
                "voice_copy_kanji_each",
                "voice_copy_dictionary",
                "voice_copy_dictionary_together",
                "voice_copy_translation",
                "voice_copy_translation_each",
            }

    def _freeze_voice_target(self) -> dict:
            """Capture the current popup/highlight/pointer target on Tk's thread."""
            popup_target = getattr(self.popup, "current_selection_target_for_voice", None)
            try:
                selected = popup_target() if callable(popup_target) else {}
            except Exception:
                selected = {}
            if bool(selected.get("ok")) and str(selected.get("text") or "").strip():
                return dict(selected)

            highlighted = self._highlighted_anki_payload()
            if str(highlighted.get("text") or "").strip():
                highlighted["ok"] = True
                highlighted["source"] = "highlight"
                return highlighted

            # Resolve the actual canvas hit now instead of trusting the delayed
            # hover tooltip.  The target is frozen before this voice action is
            # placed on the general input queue.
            hovered_getter = getattr(self.renderer, "subtitle_target_at_pointer_for_anki", None)
            if not callable(hovered_getter):
                hovered_getter = getattr(self.renderer, "hovered_subtitle_target_for_anki", None)
            try:
                hovered = dict(hovered_getter() or {}) if callable(hovered_getter) else {}
            except Exception:
                hovered = {}
            text = str(hovered.get("text") or "").strip()
            if text:
                subtitle_getter = getattr(self.subtitle_navigation, "copy_text_for_current_subtitle", None)
                hovered.update(
                    {
                        "ok": True,
                        "source": "hover",
                        "text": text,
                        "subtitle_text": str(subtitle_getter() or "") if callable(subtitle_getter) else "",
                        "words": [text],
                    }
                )
                return hovered
            return {"ok": False, "reason": "no_target"}

    def enqueue_voice_action(self, action: str, repeat_count: int = 1) -> None:
            """Queue a voice action after freezing any UI-dependent target."""
            # Voice command callbacks are dispatched onto Tk, so this direct read
            # closes the small gap before the next 15 ms input-pump cache refresh.
            if self._modal_input_blocked() or self._read_modal_input_active():
                self.controller._cached_modal_input_active = True
                return
            value = str(action or "")
            if self._voice_action_uses_lookup_target(value):
                target = self._freeze_voice_target()
                self.controller._voice_frozen_target_counter = int(
                    getattr(self.controller, "_voice_frozen_target_counter", 0) or 0
                ) + 1
                target_id = self.controller._voice_frozen_target_counter
                self.controller._voice_frozen_targets[target_id] = target
                value = f"voice_target:{target_id}:{value}"
            self.enqueue_action(value, source="voice", repeat_count=repeat_count)

    def _take_frozen_voice_target(self, action: str) -> tuple[str, dict | None]:
            value = str(action or "")
            if not value.startswith("voice_target:"):
                return value, None
            _prefix, _separator, rest = value.partition(":")
            target_id_text, separator, actual = rest.partition(":")
            if not separator:
                return value, None
            try:
                target_id = int(target_id_text)
            except (TypeError, ValueError):
                return actual, None
            targets = getattr(self.controller, "_voice_frozen_targets", {})
            target = targets.pop(target_id, None) if isinstance(targets, dict) else None
            return actual, dict(target) if isinstance(target, dict) else None

    def _drop_pending_input_actions(self, actions_to_remove: set[str]) -> None:
            if not actions_to_remove:
                return
            kept = []
            while True:
                try:
                    item = self._input_actions.get_nowait()
                except queue.Empty:#
                    break
                except Exception:#
                    break
                action = item[0] if isinstance(item, tuple) else item
                if action not in actions_to_remove:
                    kept.append(item)
            for action in kept:
                try:
                    self._input_actions.put_nowait(action)
                except Exception:#
                    break

    def _is_seek_repeat_action(self, action: str) -> bool:
            return (action in {"go_back", "go_forward"}) and (not self._skip_buttons_use_subtitle_segments())
    
    @staticmethod
    def _seek_step_action_name(action: str) -> str | None:
            if action == "go_back":
                return "_seek_step_back"
            if action == "go_forward":
                return "_seek_step_forward"
            return None

    def _queue_names_for_repeat_action(self, action: str, settle_seek: bool = True) -> set[str]:
            if self._is_seek_repeat_action(action):
                step_name = self._seek_step_action_name(action)
                return {step_name} if step_name else set()
            return {action}

    def _queue_repeat_step(self, action: str) -> None:
            step_name = self._seek_step_action_name(action)
            if step_name:
                self._enqueue_input_action(step_name, source="physical")
            else:
                self._enqueue_input_action(action, source="physical")

    def _seek_delta_for_action(self, action: str) -> float:
            if not self._is_seek_repeat_action(action):
                return 0.0
            skip = float(self.settings._last_skip_value or 0.0)
            if action == "go_back":
                return -skip
            if action == "go_forward":
                return skip
            return 0.0

    def _update_pending_seek_display(self) -> None:
            update = getattr(self.controller, "update_time_display", None)
            if callable(update):
                update()
                return
            self.update_time_and_subtitle_displays()

    def _accumulate_pending_seek(self, action: str) -> None:
            delta = self._seek_delta_for_action(action)
            if abs(delta) < 0.000001:
                return
            try:
                self._pending_seek_delta = float(self._pending_seek_delta) + delta
            except Exception:#
                logger.debug("Failed to accumulate pending seek delta", exc_info=True)
                self._pending_seek_delta = delta
            self._update_pending_seek_display()

    def _clear_pending_seek_preview(self) -> None:
            if abs(float(getattr(self, "_pending_seek_delta", 0.0) or 0.0)) < 0.000001:
                return
            self._pending_seek_delta = 0.0
            self._update_pending_seek_display()

    def _apply_pending_seek(self, event_time: float | None = None) -> None:
            delta = float(self._pending_seek_delta or 0.0)
            if abs(delta) < 0.000001:
                return
            self._pending_seek_delta = 0.0
            self.playback.seek_relative(delta, event_time=event_time)

    def _hold_repeat_action(self, action: str) -> bool:
            now = time.perf_counter()
            with self._repeat_lock:
                if action in self._held_repeat_next_fire:
                    return False
                self._held_repeat_next_fire[action] = now + self._repeat_initial_delay_sec
                if not hasattr(self.controller, "_held_repeat_press_time"):
                    self.controller._held_repeat_press_time = {}
                self._held_repeat_press_time[action] = now
                self._held_repeat_fired.discard(action)
            return True

    def _release_repeat_actions(self, actions: set[str], settle_seek: bool = True) -> None:
            if not actions:
                return
            released_seek_actions = set()
            released_seek_fired = {}
            released_seek_press_time = {}
            with self._repeat_lock:
                if not hasattr(self.controller, "_held_repeat_press_time"):
                    self.controller._held_repeat_press_time = {}
                for action in actions:
                    if self._is_seek_repeat_action(action) and action in self._held_repeat_next_fire:
                        released_seek_actions.add(action)
                        released_seek_fired[action] = (action in self._held_repeat_fired)
                        released_seek_press_time[action] = self._held_repeat_press_time.get(action)
                    self._held_repeat_next_fire.pop(action, None)
                    self._held_repeat_press_time.pop(action, None)
                    self._held_repeat_fired.discard(action)
                still_held = set(self._held_repeat_next_fire.keys())

            queue_names = set()
            for action in actions:
                queue_names.update(self._queue_names_for_repeat_action(action, settle_seek=settle_seek))
            self._drop_pending_input_actions(queue_names)

            if released_seek_actions:
                seek_still_held = bool(still_held & {"go_back", "go_forward"})
                if not seek_still_held:
                    pending = float(getattr(self, "_pending_seek_delta", 0.0) or 0.0)
                    if settle_seek:
                        if abs(pending) >= 0.000001:
                            self._enqueue_input_action("apply_pending_seek", source="physical")
                        else:
                            unresolved = [a for a in released_seek_actions if not released_seek_fired.get(a, False)]
                            if "go_forward" in unresolved and "go_back" not in unresolved:
                                self._enqueue_input_action(
                                    "go_forward",
                                    event_time=released_seek_press_time.get("go_forward"),
                                    source="physical",
                                )
                            elif "go_back" in unresolved and "go_forward" not in unresolved:
                                self._enqueue_input_action(
                                    "go_back",
                                    event_time=released_seek_press_time.get("go_back"),
                                    source="physical",
                                )
                    else:
                        if abs(pending) >= 0.000001:
                            self._enqueue_input_action("clear_pending_seek", source="physical")

    def _process_repeat_actions(self):
            if self._shutting_down:
                return
            if getattr(self.controller, "_browser_text_input_focused", False):
                return
            if self._modal_input_blocked():
                return
            now = time.perf_counter()
            due_actions = []
            with self._repeat_lock:
                for action, next_fire in list(self._held_repeat_next_fire.items()):
                    if now >= next_fire:
                        due_actions.append(action)
                        self._held_repeat_next_fire[action] = now + self._repeat_interval_sec

            for action in due_actions:
                with self._repeat_lock:
                    if action not in self._held_repeat_next_fire:
                        continue
                    self._held_repeat_fired.add(action)
                self._queue_repeat_step(action)

            try:
                self._repeat_job = self.settings.root.after(16, self._process_repeat_actions)
            except Exception as e:
                logger.debug("Failed to schedule repeat actions: %s", e, exc_info=True)
                self._repeat_job = None

    def _process_input_queue(self):
            if self._shutting_down:
                return
            try:
                try:
                    self._refresh_ui_state_cache()
                except Exception as e:
                    logger.debug("Failed to refresh hotkey UI state: %s", e, exc_info=True)
                self._poll_hover_mode()
                self._poll_global_toggle_subtitles()
                for _ in range(50):
                    try:
                        item = self._input_actions.get_nowait()
                    except queue.Empty:#
                        break
                    repeat_count = 1
                    source = self._action_source(item[0] if isinstance(item, tuple) else item)
                    if isinstance(item, tuple) and len(item) >= 3:
                        action, event_time, repeat_count = item[:3]
                    elif isinstance(item, tuple):
                        action, event_time = item
                    else:
                        action, event_time = item, None
                    if self._modal_input_blocked() and self._is_modal_blocked_source(source):
                        self._discard_frozen_voice_target(action)
                        continue
                    self._dispatch_input_action(
                        action,
                        event_time=event_time,
                        repeat_count=repeat_count,
                        source=source,
                    )
            finally:
                try:
                    self._input_pump_job = self.settings.root.after(15, self._process_input_queue)
                except Exception as e:
                    logger.debug("Failed to schedule input queue pump: %s", e, exc_info=True)
                    self._input_pump_job = None

    def _dispatch_input_action(
        self,
        action: str,
        event_time: float | None = None,
        repeat_count: int = 1,
        *,
        source: str = "ui",
    ) -> None:
            if self._modal_input_blocked() and self._is_modal_blocked_source(source):
                self._discard_frozen_voice_target(action)
                return
            action, frozen_target = self._take_frozen_voice_target(str(action))
            if frozen_target is not None:
                previous_target = self._active_voice_target
                self._active_voice_target = frozen_target
                try:
                    self._dispatch_input_action(
                        action,
                        event_time=event_time,
                        repeat_count=repeat_count,
                        source=source,
                    )
                finally:
                    self._active_voice_target = previous_target
                return
            try:
                repeat_count = max(1, min(20, int(repeat_count)))
            except Exception:
                repeat_count = 1
            if action in {"toggle_subtitles", "clear_subtitle"} and self._hotkey_action_disabled(action):
                return
            if action == "toggle_play":
                self.playback.toggle_play(event_time=event_time)
            elif action == "voice_play":
                self.controller.handle_voice_playback_action(None)
            elif action == "voice_pause":
                self.controller.handle_voice_playback_action(False)
            elif action == "voice_stop_listening":
                self.controller.set_voice_enabled(False)
            elif action == "voice_abort":
                self._abort_voice_action()
            elif action == "voice_cycle_mode":
                self.controller.handle_voice_input_mode(None)
            elif action == "voice_mode_1":
                self.controller.handle_voice_input_mode(1)
            elif action == "voice_mode_2":
                self.controller.handle_voice_input_mode(2)
            elif action == "voice_mode_3":
                self.controller.handle_voice_input_mode(3)
            elif action == "voice_language_english":
                self.controller.handle_voice_recognition_language("en")
            elif action == "voice_language_german":
                self.controller.handle_voice_recognition_language("de")
            elif action in {"voice_show_app", "voice_toggle_display"}:
                self._toggle_display_windows_for_voice()
            elif action == "voice_toggle_app":
                self._toggle_app_settings_for_voice()
            elif action == "voice_show_settings":
                self._show_settings_window_for_voice()
            elif action == "voice_open_advanced_settings":
                self._open_advanced_settings_for_voice()
            elif action == "voice_close_advanced_settings":
                self._close_advanced_settings_for_voice()
            elif action == "voice_go_back":
                self.controller.handle_voice_seek_action("back", repeat_count=repeat_count)
            elif action == "voice_go_forward":
                self.controller.handle_voice_seek_action("forward", repeat_count=repeat_count)
            elif str(action).startswith("voice_sequence:"):
                self._start_voice_sequence(decode_voice_sequence(action))
            elif str(action).startswith("voice_go_to_time:"):
                try:
                    target_seconds = float(str(action).split(":", 1)[1])
                except Exception:
                    return
                self.controller.handle_voice_time_jump(target_seconds)
            elif action == "toggle_subtitles":
                self.subtitle_navigation.toggle_subtitle_visibility()
                self._sync_control_window_for_subtitle_visibility()
            elif action == "voice_show_subtitles":
                self.subtitle_navigation.set_subtitle_visibility(True)
                self._sync_control_window_for_subtitle_visibility()
            elif action == "voice_hide_subtitles":
                self.subtitle_navigation.set_subtitle_visibility(False)
                self._sync_control_window_for_subtitle_visibility()
            elif action == "go_back":
                self.playback.go_back(event_time=event_time)
            elif action == "go_forward":
                self.playback.go_forward(event_time=event_time)
            elif action == "subtitle_back":
                for index in range(repeat_count):
                    self.playback.jump_subtitle_segment(
                        "prev",
                        event_time=event_time if index == 0 else None,
                    )
            elif action == "subtitle_forward":
                for index in range(repeat_count):
                    self.playback.jump_subtitle_segment(
                        "next",
                        event_time=event_time if index == 0 else None,
                    )
            elif action == "jump_sub_end":
                self.playback.on_jump_sub_end(event_time=event_time)
            elif action == "alt_x":
                self.on_alt_x()
            elif action == "episode_inc":
                self.change_episode("inc")
            elif action == "episode_dec":
                self.change_episode("dec")
            elif action == "clear_subtitle":
                self.subtitle_navigation.toggle_subtitle_visibility()
                self._sync_control_window_for_subtitle_visibility()
            elif action == "toggle_m3_mode":
                self._toggle_m3_mode()
            elif action == "toggle_debugging":
                self.toggle_debugging()
            elif action == "toggle_voice_listening":
                self.controller.toggle_voice_enabled()
            elif action == "fast_forward_speed_up":
                for _ in range(repeat_count):
                    self.playback.change_fast_forward_speed(0.1)
            elif action == "fast_forward_speed_down":
                for _ in range(repeat_count):
                    self.playback.change_fast_forward_speed(-0.1)
            elif action == "popup_add_anki":
                try:
                    self._add_current_anki_hotkey_target(post_add_capture=False)
                except Exception as e:
                    logger.exception("Failed to add current selection or hovered subtitle word to Anki: %s", e)
            elif action == "popup_add_anki_capture":
                try:
                    accepted = self._add_current_anki_hotkey_target(post_add_capture=True)
                except Exception as e:
                    logger.exception("Failed to add current selection or hovered subtitle word with capture: %s", e)
            elif action == "capture_only":
                self._trigger_capture_only_hotkey()
            elif action == "voice_add":
                self._start_voice_anki_command(post_add_capture=False, together=False)
            elif action == "voice_add_together":
                self._start_voice_anki_command(post_add_capture=False, together=True)
            elif action == "voice_capture":
                self._start_voice_anki_command(post_add_capture=True, together=False)
            elif action == "voice_capture_together":
                self._start_voice_anki_command(post_add_capture=True, together=True)
            elif str(action).startswith("voice_add_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._add_indexed_subtitle_word(position, post_add_capture=False)
            elif str(action).startswith("voice_capture_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._add_indexed_subtitle_word(position, post_add_capture=True)
            elif str(action).startswith("voice_highlight_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._highlight_subtitle_positions([position])
            elif str(action).startswith("voice_highlight_add_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._modify_subtitle_highlight_positions([position], add=True)
            elif str(action).startswith("voice_highlight_remove_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._modify_subtitle_highlight_positions([position], add=False)
            elif self._dispatch_voice_multi_position_action(action):
                pass
            elif action == "voice_highlight_clear":
                clear = getattr(self.renderer, "clear_voice_word_highlights", None)
                removed = int(clear() or 0) if callable(clear) else 0
                self.controller._report_voice_status(
                    "listening",
                    f"Removed {removed} highlighted subtitle word{'s' if removed != 1 else ''}.",
                )
            elif action == "voice_dictionary":
                self._show_voice_lookup_layers(("dictionary",), report=True)
            elif action == "voice_dictionary_together":
                self._show_voice_lookup_layers(("dictionary_together",), report=True)
            elif action == "voice_translate":
                self._show_voice_lookup_layers(("translation",), report=True)
            elif action == "voice_translate_each":
                self._show_voice_lookup_layers(("translation_each",), report=True)
            elif action == "voice_status":
                self._show_voice_lookup_layers(("status",), report=True)
            elif action == "voice_database":
                self._show_voice_lookup_layers(("status",), report=True)
            elif str(action).startswith("voice_hover_layers:"):
                layers = decode_voice_hover_layers(action)
                if layers:
                    self._show_voice_lookup_layers(layers, report=True)
            elif str(action).startswith("voice_dictionary_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._show_indexed_voice_layers(position, ("dictionary",))
            elif str(action).startswith("voice_translate_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._show_indexed_voice_layers(position, ("translation",))
            elif str(action).startswith("voice_status_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._show_indexed_voice_layers(position, ("status",))
            elif str(action).startswith("voice_database_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._show_indexed_voice_layers(position, ("status",))
            elif str(action).startswith("voice_copy_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._copy_indexed_subtitle_word(position)
            elif action == "voice_capture_only":
                generation = self._begin_voice_action_feedback()
                accepted = self._trigger_voice_capture_only(
                    use_capture_only_resume=True,
                    feedback_generation=generation,
                )
                if not accepted:
                    self._finish_voice_action_feedback(generation, False)
            elif action == "subtitle_hover_add_anki":
                try:
                    self._add_current_anki_hotkey_target(post_add_capture=False, subtitle_only=True)
                except Exception as e:
                    logger.exception("Failed to add hovered subtitle word to Anki: %s", e)
            elif action == "subtitle_hover_add_anki_capture":
                try:
                    accepted = self._add_current_anki_hotkey_target(
                        post_add_capture=True,
                        subtitle_only=True,
                    )
                except Exception as e:
                    logger.exception("Failed to add hovered subtitle word with capture: %s", e)
            elif action == "popup_copy_selection":
                renderer = getattr(self, "renderer", None)
                copy_hover = getattr(renderer, "copy_hover_selection_to_clipboard_if_pointer_inside", None)
                copy_selection = getattr(self.popup, "copy_selection_to_clipboard_if_pointer_inside", None)
                try:
                    if callable(copy_hover) and copy_hover():
                        return
                    if callable(copy_selection):
                        copy_selection()
                except Exception:
                    logger.debug("Failed to copy hover or popup selection from global shortcut", exc_info=True)
            elif action == "voice_copy":
                self._copy_voice_target()
            elif action == "voice_copy_kanji":
                self._copy_voice_highlight_value("kanji")
            elif action == "voice_copy_kanji_each":
                self._copy_voice_highlight_value("kanji_each")
            elif action == "voice_copy_dictionary":
                self._copy_voice_highlight_value("dictionary")
            elif action == "voice_copy_dictionary_together":
                self._copy_voice_highlight_value("dictionary_together")
            elif action == "voice_copy_translation":
                self._copy_voice_highlight_value("translation")
            elif action == "voice_copy_translation_each":
                self._copy_voice_highlight_value("translation_each")
            elif action == "_refresh_popup_translation":
                self._refresh_popup_translation_display()
            elif action == "_seek_step_back":
                self._accumulate_pending_seek("go_back")
            elif action == "_seek_step_forward":
                self._accumulate_pending_seek("go_forward")
            elif action == "apply_pending_seek":
                self._apply_pending_seek(event_time=event_time)
            elif action == "clear_pending_seek":
                self._clear_pending_seek_preview()

    def _begin_voice_action_feedback(self) -> int:
            generation = int(getattr(self.controller, "_voice_action_generation", 0) or 0) + 1
            self.controller._voice_action_generation = generation
            setter = getattr(self.renderer, "set_voice_action_indicator", None)
            if callable(setter):
                setter("working")
            return generation

    def _finish_voice_action_feedback(self, generation: int, succeeded: bool) -> None:
            if int(generation) != int(getattr(self.controller, "_voice_action_generation", 0) or 0):
                return
            setter = getattr(self.renderer, "set_voice_action_indicator", None)
            if callable(setter):
                setter("success" if succeeded else "idle")

    def _finish_voice_anki_feedback(
        self,
        generation: int,
        succeeded: bool,
        result=None,
        *,
        post_add_capture: bool,
    ) -> None:
            """Keep the voice action indicator alive until capture media is ready."""
            if bool((result or {}).get("capture_delivery_failed")):
                succeeded = False
            if not post_add_capture or not succeeded:
                if int(getattr(self.controller, "_voice_capture_pending_generation", -1) or -1) == int(generation):
                    self.controller._voice_capture_pending_generation = None
                self._finish_voice_action_feedback(generation, bool(succeeded))
                return
            try:
                note_id = int((result or {}).get("note_id") or 0)
            except Exception:
                note_id = 0
            if note_id > 0:
                pending = getattr(self.controller, "_voice_capture_feedback", None)
                if not isinstance(pending, dict):
                    pending = {}
                    self.controller._voice_capture_feedback = pending
                pending[note_id] = int(generation)
                return
            self._finish_voice_action_feedback(generation, True)

    def register_capture_feedback(self, note_id: int, generation: int | None = None) -> None:
            try:
                note_id = int(note_id)
            except Exception:
                return
            if note_id <= 0:
                return
            if generation is None:
                generation = getattr(self.controller, "_voice_capture_pending_generation", None)
                self.controller._voice_capture_pending_generation = None
            if generation is None:
                return
            pending = getattr(self.controller, "_voice_capture_feedback", None)
            if not isinstance(pending, dict):
                pending = {}
                self.controller._voice_capture_feedback = pending
            pending[note_id] = int(generation)

    def notify_capture_media_finished(self, note_id: int, succeeded: bool) -> None:
            try:
                note_id = int(note_id)
            except Exception:
                return
            pending = getattr(self.controller, "_voice_capture_feedback", None)
            generation = pending.pop(note_id, None) if isinstance(pending, dict) else None
            if generation is not None:
                self._finish_voice_action_feedback(int(generation), bool(succeeded))

    def _abort_voice_action(self) -> None:
            self.controller._voice_action_generation = int(
                getattr(self.controller, "_voice_action_generation", 0) or 0
            ) + 1
            self.controller._voice_sequence_steps = []
            self.controller._voice_sequence_running = False
            self.controller._voice_anki_batch_running = False
            self.controller._voice_anki_action_queued = False
            self.controller._voice_capture_pending_generation = None
            self.controller._voice_capture_feedback = {}
            cancel_sessions = getattr(getattr(self.controller, "anki_controller", None), "cancel_capture_sessions", None)
            if callable(cancel_sessions):
                try:
                    cancel_sessions(interaction_owner="voice", reason="voice_abort")
                except Exception:
                    logger.debug("Failed to cancel voice capture sessions", exc_info=True)
            abort_feedback = getattr(self.renderer, "abort_voice_action_feedback", None)
            if callable(abort_feedback):
                abort_feedback()
            self.controller._report_voice_status(
                "listening",
                "Current voice action aborted. A request already sent to Anki cannot be undone.",
            )

    def _voice_anki_action_in_flight(self) -> bool:
            if bool(getattr(self.controller, "_anki_add_busy", False)) or bool(
                getattr(self.controller, "_voice_anki_action_queued", False)
            ) or bool(getattr(self.controller, "_voice_anki_batch_running", False)):
                return True
            for attr in ("_anki_wait_window", "_anki_preview_window"):
                if self._window_exists(getattr(self.controller, attr, None)):
                    return True
            return False

    def _start_voice_sequence(self, steps: list[tuple[str, int]]) -> None:
            steps = [
                (str(action), max(1, min(20, int(repeat_count))))
                for action, repeat_count in steps
                if str(action or "").strip()
            ]
            if len(steps) < 2:
                self.controller._report_voice_status("error", "The combined voice command was invalid.")
                return
            if bool(getattr(self.controller, "_voice_sequence_running", False)):
                self.controller._report_voice_status(
                    "listening",
                    "Ignored the combined command because another combined command is still running.",
                )
                return
            self.controller._voice_sequence_steps = list(steps)
            self.controller._voice_sequence_running = True
            self._continue_voice_sequence(True)

    def _continue_voice_sequence(self, previous_succeeded: bool) -> None:
            if not bool(getattr(self.controller, "_voice_sequence_running", False)):
                return
            if self._modal_input_blocked() or self._read_modal_input_active():
                self.controller._cached_modal_input_active = True
                self.controller._voice_sequence_steps = []
                self.controller._voice_sequence_running = False
                return
            if not previous_succeeded:
                self.controller._voice_sequence_steps = []
                self.controller._voice_sequence_running = False
                self.controller._report_voice_status("error", "Combined voice command stopped after a failed step.")
                return
            steps = list(getattr(self.controller, "_voice_sequence_steps", []) or [])
            if not steps:
                self.controller._voice_sequence_running = False
                self.controller._report_voice_status("listening", "Combined voice command completed.")
                return
            action, repeat_count = steps.pop(0)
            self.controller._voice_sequence_steps = steps

            def _complete(succeeded: bool = True) -> None:
                try:
                    self.settings.root.after(0, lambda: self._continue_voice_sequence(bool(succeeded)))
                except Exception:
                    self._continue_voice_sequence(bool(succeeded))

            if action == "voice_go_back":
                self.controller.handle_voice_seek_action(
                    "back",
                    repeat_count=repeat_count,
                    on_complete=_complete,
                )
                return
            if action == "voice_go_forward":
                self.controller.handle_voice_seek_action(
                    "forward",
                    repeat_count=repeat_count,
                    on_complete=_complete,
                )
                return
            if action == "voice_play":
                self.controller.handle_voice_playback_action(None, on_complete=_complete)
                return
            if action == "voice_pause":
                self.controller.handle_voice_playback_action(False, on_complete=_complete)
                return
            self._dispatch_input_action(action, repeat_count=repeat_count, source="voice")
            _complete(True)

    @staticmethod
    def _indexed_position_label(position: int) -> str:
            if int(position) > 0:
                return str(int(position))
            return "last" if int(position) == -1 else f"last {abs(int(position))}"

    def _voice_target_for_current_action(self) -> dict:
            target = self._active_voice_target
            if isinstance(target, dict):
                return dict(target)
            return self._freeze_voice_target()

    def _add_frozen_voice_target_to_anki(
        self,
        target: dict,
        *,
        post_add_capture: bool,
        on_complete=None,
    ) -> bool:
            selected_text = str(target.get("text") or "").strip()
            subtitle_text = str(target.get("subtitle_text") or "")
            if not selected_text or not subtitle_text:
                return False
            add_to_anki = getattr(self.controller, "_add_selection_to_anki", None)
            if not callable(add_to_anki):
                return False
            add_to_anki(
                selected_text=selected_text,
                subtitle_text=subtitle_text,
                post_add_capture=bool(post_add_capture),
                on_complete=on_complete,
                cue_snapshot=target.get("snapshot"),
                selection_spans=tuple(target.get("spans") or ()),
                initiator="voice",
            )
            return True

    def _start_voice_anki_command(
        self,
        *,
        post_add_capture: bool,
        together: bool,
        force_highlight: bool = False,
    ) -> bool:
            target = self._voice_target_for_current_action()
            if force_highlight:
                target = self._highlighted_anki_payload()
                target.update({"ok": bool(target.get("text")), "source": "highlight"})
            highlighted_words = list(target.get("words") or []) if target.get("source") == "highlight" else []
            if highlighted_words and not together:
                return self._start_highlighted_anki_batch(
                    capture_after=bool(post_add_capture),
                    payload=target,
                )

            if self._voice_anki_action_in_flight():
                self.controller._report_voice_status(
                    "listening",
                    "Ignored the voice command because an Anki add is already running.",
                )
                return False

            self.controller._voice_anki_action_queued = True
            operation = "capture" if post_add_capture else "add"
            generation = self._begin_voice_action_feedback()
            if post_add_capture:
                self.controller._voice_capture_pending_generation = int(generation)

            def completed(success: bool, result=None) -> None:
                self._finish_voice_anki_feedback(
                    generation,
                    bool(success),
                    result,
                    post_add_capture=bool(post_add_capture),
                )

            try:
                accepted = self._add_frozen_voice_target_to_anki(
                    target,
                    post_add_capture=bool(post_add_capture),
                    on_complete=completed,
                )
                if not accepted:
                    self._finish_voice_action_feedback(generation, False)
                    self.controller._report_voice_status(
                        "listening",
                        f"No popup selection, highlighted subtitle word, or hovered subtitle word is available to {operation}.",
                    )
                return bool(accepted)
            except Exception as exc:
                self._finish_voice_action_feedback(generation, False)
                logger.exception("Voice Anki %s failed", operation)
                self.controller._report_voice_status("error", f"Voice Anki {operation} failed: {exc}")
                return False
            finally:
                if not bool(getattr(self.controller, "_anki_add_busy", False)):
                    self.controller._voice_anki_action_queued = False

    def _show_indexed_voice_layers(self, position: int, layers) -> bool:
            show = getattr(self.renderer, "show_indexed_subtitle_word_layers_for_voice", None)
            result = (
                show(int(position), tuple(layers or ()))
                if callable(show)
                else {"ok": False, "reason": "unavailable", "word_count": 0}
            )
            if bool(result.get("ok")):
                word = str(result.get("word") or "").strip()
                layer_label = " and ".join(str(layer) for layer in result.get("layers") or [])
                self.controller._report_voice_status(
                    "listening",
                    f"Showing {layer_label or 'information'} for subtitle word "
                    f"{result.get('index')} of {result.get('word_count')}: {word}",
                )
                return True
            self._report_indexed_voice_failure(position, result, "lookup")
            return False

    def _copy_text_for_voice(self, text: str) -> bool:
            value = str(text or "")
            if not value:
                return False
            try:
                owner = getattr(self.settings, "root", None)
                owner.clipboard_clear()
                owner.clipboard_append(value)
                return True
            except Exception:
                logger.debug("Failed to copy voice-selected subtitle text", exc_info=True)
                return False

    def _copy_indexed_subtitle_word(self, position: int) -> bool:
            resolve = getattr(self.renderer, "subtitle_word_at_position_for_voice", None)
            result = (
                resolve(int(position))
                if callable(resolve)
                else {"ok": False, "reason": "unavailable", "word_count": 0}
            )
            word = str(result.get("word") or "").strip()
            if bool(result.get("ok")) and self._copy_text_for_voice(word):
                self.controller._report_voice_status(
                    "listening",
                    f"Copied subtitle word {result.get('index')} of {result.get('word_count')}: {word}",
                )
                return True
            self._report_indexed_voice_failure(position, result, "copy")
            return False

    def _report_indexed_voice_failure(self, position: int, result: dict, operation: str) -> None:
            word_count = int(result.get("word_count") or 0)
            if str(result.get("reason") or "") == "out_of_range":
                self.controller._report_voice_status(
                    "listening",
                    f"Word {self._indexed_position_label(position)} is unavailable; "
                    f"the current subtitle has {word_count} selectable words.",
                )
            elif word_count <= 0:
                self.controller._report_voice_status(
                    "listening",
                    f"Indexed {operation} failed because the current subtitle has no selectable words.",
                )
            else:
                self.controller._report_voice_status("error", f"The indexed subtitle word could not be used for {operation}.")

    def _copy_voice_target(self) -> bool:
            target = self._active_voice_target if isinstance(self._active_voice_target, dict) else None
            popup_copy = getattr(self.popup, "voice_copy_text", None)
            if str((target or {}).get("source") or "") == "popup" and callable(popup_copy):
                try:
                    result = popup_copy("active")
                except Exception:
                    result = {}
                text = str((result or {}).get("text") or "").strip()
                if text and self._copy_text_for_voice(text):
                    kind = str((result or {}).get("kind") or "popup result").replace("_", " ")
                    self.controller._report_voice_status("listening", f"Copied {kind}.")
                    return True
            renderer = getattr(self, "renderer", None)
            active_result = getattr(renderer, "voice_copy_text", None)
            try:
                result = active_result("active") if callable(active_result) else {}
            except Exception:
                result = {}
            text = str((result or {}).get("text") or "").strip()
            if text and self._copy_text_for_voice(text):
                kind = str((result or {}).get("kind") or "hover result").replace("_", " ")
                self.controller._report_voice_status("listening", f"Copied {kind}.")
                return True

            popup_result = getattr(self.popup, "current_hover_result_for_voice", None)
            try:
                result = popup_result() if callable(popup_result) else {}
            except Exception:
                result = {}
            text = str((result or {}).get("text") or "").strip()
            if text and self._copy_text_for_voice(text):
                kind = str((result or {}).get("kind") or "popup result").replace("_", " ")
                self.controller._report_voice_status("listening", f"Copied popup {kind}.")
                return True

            selected = getattr(self.popup, "_current_or_last_selected_text", None)
            try:
                text = str(selected() or "").strip() if callable(selected) else ""
            except Exception:
                text = ""
            if text and self._copy_text_for_voice(text):
                self.controller._report_voice_status("listening", "Copied the popup text selection.")
                return True

            self.controller._report_voice_status(
                "listening",
                "No displayed hover result or popup text selection is available to copy. Say 'copy kanji' for highlighted Japanese.",
            )
            return False

    def _copy_voice_highlight_value(self, kind: str) -> bool:
            target = self._active_voice_target if isinstance(self._active_voice_target, dict) else None
            popup_copy = getattr(self.popup, "voice_copy_text", None)
            if str((target or {}).get("source") or "") == "popup" and callable(popup_copy):
                try:
                    result = popup_copy(kind)
                except Exception:
                    result = {}
                text = str((result or {}).get("text") or "").strip()
                if bool((result or {}).get("ok")) and text and self._copy_text_for_voice(text):
                    label = str(kind or "popup text").replace("_", " ")
                    self.controller._report_voice_status("listening", f"Copied popup {label}.")
                    return True
            resolver = getattr(self.renderer, "voice_copy_text", None)
            result = resolver(kind) if callable(resolver) else {"ok": False, "reason": "unavailable"}
            text = str((result or {}).get("text") or "").strip()
            if bool((result or {}).get("ok")) and text and self._copy_text_for_voice(text):
                label = str(kind or "highlighted text").replace("_", " ")
                self.controller._report_voice_status("listening", f"Copied highlighted {label}.")
                return True

            reason = str((result or {}).get("reason") or "")
            if reason == "translation_not_ready":
                if kind == "translation_each":
                    self._show_voice_highlight_translations_individually(report=False)
                else:
                    self._show_voice_highlight_layers(("translation",), report=False)
                message = "Translation is still being prepared. Say the copy command again when it appears."
            elif reason == "no_dictionary":
                message = "No dictionary result exists for the highlighted selection."
            elif reason == "no_highlight":
                message = "No subtitle words are highlighted."
            else:
                message = "The requested highlighted result is unavailable to copy."
            self.controller._report_voice_status("listening", message)
            return False

    def _add_indexed_subtitle_word(self, position: int, *, post_add_capture: bool) -> bool:
            operation = "capture" if post_add_capture else "add"
            if self._voice_anki_action_in_flight():
                self.controller._report_voice_status(
                    "listening",
                    f"Ignored indexed {operation} because an Anki add is already running.",
                )
                return False
            getter = getattr(self.renderer, "subtitle_words_for_anki", None)
            words = list(getter() or []) if callable(getter) else []
            word_count = len(words)
            index = int(position) - 1 if int(position) > 0 else word_count + int(position)
            if word_count <= 0:
                self.controller._report_voice_status(
                    "listening",
                    f"Indexed {operation} failed because the current subtitle has no selectable words.",
                )
                return False
            if index < 0 or index >= word_count:
                requested = self._indexed_position_label(position)
                self.controller._report_voice_status(
                    "listening",
                    f"Word {requested} is unavailable; the current subtitle has {word_count} selectable words.",
                )
                return False

            target_getter = getattr(self.renderer, "subtitle_word_at_position_for_voice", None)
            target = target_getter(int(position)) if callable(target_getter) else {}
            selected = str(target.get("word") or words[index] or "").strip()
            subtitle_getter = getattr(self.subtitle_navigation, "copy_text_for_current_subtitle", None)
            subtitle_text = str(subtitle_getter() or "") if callable(subtitle_getter) else ""
            if not selected or not subtitle_text:
                self.controller._report_voice_status(
                    "listening",
                    f"Indexed {operation} failed because the current subtitle is no longer available.",
                )
                return False

            self.controller._voice_anki_action_queued = True
            generation = self._begin_voice_action_feedback()

            def completed(success: bool, result=None) -> None:
                self._finish_voice_anki_feedback(
                    generation,
                    bool(success),
                    result,
                    post_add_capture=bool(post_add_capture),
                )

            try:
                self.controller._add_selection_to_anki(
                    selected_text=selected,
                    subtitle_text=subtitle_text,
                    post_add_capture=bool(post_add_capture),
                    on_complete=completed,
                    cue_snapshot=target.get("snapshot"),
                    selection_spans=target.get("spans"),
                    initiator="voice",
                )
                self.controller._report_voice_status(
                    "working",
                    f"{'Capturing' if post_add_capture else 'Adding'} subtitle word {index + 1} of {word_count}: {selected}",
                )
                return True
            except Exception as exc:
                self._finish_voice_action_feedback(generation, False)
                logger.exception("Indexed voice %s failed", operation)
                self.controller._report_voice_status("error", f"Indexed {operation} failed: {exc}")
                return False
            finally:
                if not bool(getattr(self.controller, "_anki_add_busy", False)):
                    self.controller._voice_anki_action_queued = False

    def _dispatch_voice_multi_position_action(self, action: str) -> bool:
            match = re.fullmatch(
                r"voice_(add_together|capture_together|dictionary_together|translate_each|highlight_add|highlight_remove|add|capture|highlight|dictionary|translate|status|database|copy)_(range|selection):(.+)",
                str(action or ""),
            )
            if match is None:
                return False

            operation, selection_kind, encoded_positions = match.groups()
            try:
                values = [int(value) for value in encoded_positions.split(",") if value.strip()]
            except (TypeError, ValueError):
                values = []
            if selection_kind == "range" and len(values) == 2:
                left, right = values
                step = 1 if right >= left else -1
                positions = list(range(left, right + step, step))
            elif selection_kind == "selection" and len(values) >= 2:
                positions = values
            else:
                self.controller._report_voice_status(
                    "error",
                    "The voice command contained an invalid subtitle-word position list.",
                )
                return True

            if operation in {"highlight_add", "highlight_remove"}:
                self._modify_subtitle_highlight_positions(
                    positions,
                    add=operation == "highlight_add",
                )
                return True

            highlighted = self._highlight_subtitle_positions(
                positions,
                show_automatic_layers=operation == "highlight",
            )
            if not highlighted or operation == "highlight":
                return True
            if operation == "add":
                self._start_voice_anki_command(post_add_capture=False, together=False, force_highlight=True)
            elif operation == "add_together":
                self._start_voice_anki_command(post_add_capture=False, together=True, force_highlight=True)
            elif operation == "capture":
                self._start_voice_anki_command(post_add_capture=True, together=False, force_highlight=True)
            elif operation == "capture_together":
                self._start_voice_anki_command(post_add_capture=True, together=True, force_highlight=True)
            elif operation == "dictionary":
                self._show_voice_lookup_layers(("dictionary",), report=True, force_highlight=True)
            elif operation == "dictionary_together":
                self._show_voice_lookup_layers(("dictionary_together",), report=True, force_highlight=True)
            elif operation == "translate":
                self._show_voice_lookup_layers(("translation",), report=True, force_highlight=True)
            elif operation == "translate_each":
                self._show_voice_lookup_layers(("translation_each",), report=True, force_highlight=True)
            elif operation == "status":
                self._show_voice_lookup_layers(("status",), report=True, force_highlight=True)
            elif operation == "database":
                self._show_voice_lookup_layers(("status",), report=True, force_highlight=True)
            elif operation == "copy":
                self._copy_voice_highlight_value("kanji")
            return True

    def _modify_subtitle_highlight_positions(self, positions: list[int], *, add: bool) -> bool:
            modify = getattr(self.renderer, "modify_subtitle_word_highlights_for_voice", None)
            result = (
                modify(positions, add=bool(add))
                if callable(modify)
                else {"ok": False, "reason": "unavailable", "word_count": 0}
            )
            if bool(result.get("ok")):
                operation = "Added" if add else "Removed"
                indexes = ", ".join(str(index) for index in result.get("indexes") or [])
                self.controller._report_voice_status(
                    "listening",
                    f"{operation} highlight position{'s' if len(result.get('indexes') or []) != 1 else ''} "
                    f"{indexes}. {int(result.get('selected_count') or 0)} word(s) remain highlighted.",
                )
                if add:
                    automatic_layers = self._voice_highlight_automatic_layers()
                    if automatic_layers:
                        self._show_voice_highlight_layers(automatic_layers, report=False)
                return True
            self._report_indexed_voice_failure(
                int(result.get("position") or (positions[0] if positions else 0)),
                result,
                "highlight update",
            )
            return False

    def _highlight_subtitle_positions(
        self,
        positions: list[int],
        *,
        show_automatic_layers: bool = True,
    ) -> bool:
            highlight = getattr(self.renderer, "highlight_subtitle_words_for_voice", None)
            result = highlight(positions) if callable(highlight) else {"ok": False, "reason": "unavailable", "word_count": 0}
            if bool(result.get("ok")):
                indexes = ", ".join(str(index) for index in result.get("indexes") or [])
                words = " | ".join(str(word) for word in result.get("words") or [])
                automatic_layers = (
                    self._voice_highlight_automatic_layers()
                    if show_automatic_layers
                    else ()
                )
                if automatic_layers:
                    self._show_voice_highlight_layers(automatic_layers, report=False)
                self.controller._report_voice_status(
                    "listening",
                    f"Highlighted subtitle words {indexes} of {result['word_count']}: {words}",
                )
                return True
            word_count = int(result.get("word_count") or 0)
            if str(result.get("reason") or "") == "out_of_range":
                invalid_position = int(result.get("position") or 0)
                self.controller._report_voice_status(
                    "listening",
                    f"Highlight word {self._indexed_position_label(invalid_position)} is unavailable; "
                    f"the current subtitle has {word_count} selectable words.",
                )
            elif word_count <= 0:
                self.controller._report_voice_status(
                    "listening",
                    "Highlight failed because the current subtitle has no selectable words.",
                )
            else:
                self.controller._report_voice_status("error", "The subtitle word could not be highlighted.")
            return False

    def _highlighted_anki_payload(self) -> dict:
            getter = getattr(self.renderer, "highlighted_subtitle_selection_for_anki", None)
            selection = dict(getter() or {}) if callable(getter) else {}
            subtitle_getter = getattr(self.subtitle_navigation, "copy_text_for_current_subtitle", None)
            subtitle_text = str(subtitle_getter() or "") if callable(subtitle_getter) else ""
            return {
                "text": str(selection.get("text") or "").strip(),
                "words": [str(word or "").strip() for word in selection.get("words") or [] if str(word or "").strip()],
                "indexes": list(selection.get("indexes") or []),
                "subtitle_text": subtitle_text,
                "spans": tuple(selection.get("spans") or ()),
                "snapshot": selection.get("snapshot"),
            }

    def _trigger_voice_capture_only(
        self,
        *,
        use_capture_only_resume: bool,
        feedback_generation: int | None = None,
    ) -> bool:
            capture = getattr(self.controller.anki_controller, "trigger_external_capture_only", None)
            result = (
                capture(use_capture_only_resume=bool(use_capture_only_resume), interaction_owner="voice")
                if callable(capture)
                else {"accepted": False}
            )
            if not isinstance(result, dict):
                result = {"accepted": bool(result), "queued": False, "source_note_id": 0}
            if bool(result.get("queued")):
                if feedback_generation is not None:
                    self.controller._voice_capture_pending_generation = int(feedback_generation)
                self.controller._report_voice_status(
                    "working",
                    "Resolving the latest Anki note before capture-only.",
                )
                return True
            if bool(result.get("accepted")):
                source_note_id = int(result.get("source_note_id") or 0)
                if feedback_generation is not None:
                    if source_note_id > 0:
                        self.register_capture_feedback(source_note_id, feedback_generation)
                    else:
                        self.controller._voice_capture_pending_generation = int(feedback_generation)
                self.controller._report_voice_status(
                    "working",
                    f"Capture sent for note {source_note_id}; recording fresh audio and image.",
                )
                return True
            lookup_running = str(result.get("reason") or "") == "latest_note_lookup_running"
            self.controller._report_voice_status(
                "working" if lookup_running else "error",
                (
                    "The latest-note lookup for capture-only is already running."
                    if lookup_running
                    else self._capture_only_failure_message(result)
                ),
            )
            return False

    @staticmethod
    def _capture_only_failure_message(result):
        if result.get("error"):
            return "Capture-only: " + str(result["error"])
        if result.get("reason") in {"no_current_run_target", "no_recent_note", "no_session_note", "missing_source_note"}:
            return "Capture-only requires a card added by SubtitlePlayer in this app session."
        return "Capture-only backend unavailable. Check Advanced Settings / Browser Recording."

    def _trigger_capture_only_hotkey(self) -> bool:
            """Run capture-only through the existing Anki backend without voice UI state."""
            capture = getattr(getattr(self.controller, "anki_controller", None), "trigger_external_capture_only", None)
            result = (
                capture(use_capture_only_resume=True, interaction_owner="physical")
                if callable(capture)
                else {"accepted": False, "reason": "unavailable"}
            )
            if not isinstance(result, dict):
                result = {"accepted": bool(result), "queued": False, "source_note_id": 0}
            if bool(result.get("accepted")) or bool(result.get("queued")):
                logger.info("Capture-only hotkey accepted: %s", result)
                return True
            message = self._capture_only_failure_message(result)
            report = getattr(self.controller, "_report_voice_status", None)
            if callable(report):
                report("error", message)
            logger.warning("%s", message)
            return False

    def _voice_highlight_automatic_layers(self) -> tuple[str, ...]:
            settings = (
                ("dictionary", "VOICE_HIGHLIGHT_AUTO_DICTIONARY"),
                ("translation", "VOICE_HIGHLIGHT_AUTO_TRANSLATION"),
                ("status", "VOICE_HIGHLIGHT_AUTO_STATUS"),
            )
            return tuple(layer for layer, key in settings if bool(self.config.get(key) or False))

    def _report_voice_lookup_result(self, result: dict, *, report: bool, target_label: str) -> bool:
            if bool(result.get("ok")):
                if report:
                    words = " | ".join(str(word) for word in result.get("words") or [] if str(word).strip())
                    layer_names = [str(layer).replace("_", " ") for layer in result.get("layers") or []]
                    layer_label = " and ".join(layer_names) or "information"
                    self.controller._report_voice_status(
                        "listening",
                        f"Showing {layer_label} for {target_label}: {words}",
                    )
                return True
            if report:
                reason = str(result.get("reason") or "unavailable")
                message = (
                    "No popup selection, highlighted subtitle word, or hovered subtitle word is available."
                    if reason in {"no_selection", "no_active_selection", "no_highlight", "no_hover", "no_target"}
                    else "The requested word information is unavailable."
                )
                self.controller._report_voice_status("listening", message)
            return False

    def _show_voice_lookup_layers(self, layers, *, report: bool, force_highlight: bool = False) -> bool:
            """Resolve voice lookup target with popup selection > highlights > pointer priority."""
            requested = tuple(layers or ())
            target = self._active_voice_target if isinstance(self._active_voice_target, dict) else None
            source = str((target or {}).get("source") or "")
            if force_highlight:
                source = "highlight"

            if source == "popup":
                show = getattr(self.popup, "show_selected_layers_for_voice", None)
                result = show(requested) if callable(show) else {"ok": False, "reason": "unavailable"}
                return self._report_voice_lookup_result(result, report=report, target_label="the popup selection")
            if source == "highlight":
                if "translation_each" in requested:
                    return self._show_voice_highlight_translations_individually(layers=requested, report=report)
                return self._show_voice_highlight_layers(requested, report=report)
            if source == "hover":
                show = getattr(self.renderer, "show_hovered_subtitle_layers_for_voice", None)
                normalized = tuple("translation" if layer == "translation_each" else layer for layer in requested)
                result = show(normalized) if callable(show) else {"ok": False, "reason": "unavailable"}
                return self._report_voice_lookup_result(result, report=report, target_label="the hovered subtitle word")

            popup_target = getattr(self.popup, "current_selection_target_for_voice", None)
            try:
                selected = popup_target() if callable(popup_target) else {}
            except Exception:
                selected = {}
            if bool(selected.get("ok")):
                show = getattr(self.popup, "show_selected_layers_for_voice", None)
                result = show(requested) if callable(show) else {"ok": False, "reason": "unavailable"}
                return self._report_voice_lookup_result(result, report=report, target_label="the popup selection")

            highlighted = self._highlighted_anki_payload()
            if str(highlighted.get("text") or "").strip():
                if "translation_each" in requested:
                    return self._show_voice_highlight_translations_individually(layers=requested, report=report)
                return self._show_voice_highlight_layers(requested, report=report)

            show = getattr(self.renderer, "show_hovered_subtitle_layers_for_voice", None)
            normalized = tuple("translation" if layer == "translation_each" else layer for layer in requested)
            result = show(normalized) if callable(show) else {"ok": False, "reason": "no_hover"}
            return self._report_voice_lookup_result(result, report=report, target_label="the hovered subtitle word")

    def _show_voice_highlight_layers(self, layers, *, report: bool) -> bool:
            show = getattr(self.renderer, "show_voice_highlight_layers", None)
            result = show(tuple(layers or ())) if callable(show) else {"ok": False, "reason": "unavailable"}
            if bool(result.get("ok")):
                if report:
                    words = " | ".join(str(word) for word in result.get("words") or [] if str(word).strip())
                    layer_names = [str(layer).replace("_", " ") for layer in result.get("layers") or []]
                    layer_label = " and ".join(layer_names) or "information"
                    self.controller._report_voice_status(
                        "listening",
                        f"Showing {layer_label} for highlighted word{'s' if len(result.get('words') or []) != 1 else ''}: {words}",
                    )
                return True
            if report:
                reason = str(result.get("reason") or "unavailable")
                message = (
                    "No subtitle word is highlighted."
                    if reason == "no_highlight"
                    else "The requested highlighted-word information is unavailable."
                )
                self.controller._report_voice_status("listening", message)
            return False

    def _show_voice_highlight_translations_individually(self, *, layers=(), report: bool) -> bool:
            show = getattr(self.renderer, "show_voice_highlight_translations_individually", None)
            result = show(tuple(layers or ())) if callable(show) else {"ok": False, "reason": "unavailable"}
            if bool(result.get("ok")):
                if report:
                    words = " | ".join(str(word) for word in result.get("words") or [] if str(word).strip())
                    layer_names = [str(layer).replace("_", " ") for layer in result.get("layers") or []]
                    layer_label = " and ".join(layer_names) or "translation each"
                    self.controller._report_voice_status(
                        "listening",
                        f"Showing {layer_label} for highlighted words: {words}",
                    )
                return True
            if report:
                reason = str(result.get("reason") or "unavailable")
                message = (
                    "No subtitle word is highlighted."
                    if reason == "no_highlight"
                    else "Individual highlighted-word translation is unavailable."
                )
                self.controller._report_voice_status("listening", message)
            return False

    def _start_highlighted_anki_batch(self, *, capture_after: bool, payload: dict | None = None) -> bool:
            if self._voice_anki_action_in_flight():
                self.controller._report_voice_status(
                    "listening",
                    "Ignored highlighted-word add because another Anki action is already running.",
                )
                return False
            payload = dict(payload or self._highlighted_anki_payload())
            words = list(payload.get("words") or [])
            subtitle_text = str(payload.get("subtitle_text") or "")
            selection_spans = tuple(payload.get("spans") or ())
            cue_snapshot = payload.get("snapshot")
            if not words or not subtitle_text:
                self.controller._report_voice_status(
                    "listening",
                    "No highlighted subtitle words are available to add.",
                )
                return False

            unique_words = []
            unique_spans = []
            seen_words = set()
            for index, word in enumerate(words):
                key = re.sub(
                    r"\s+",
                    " ",
                    unicodedata.normalize("NFKC", str(word or "")),
                ).strip().casefold()
                if not key or key in seen_words:
                    continue
                seen_words.add(key)
                unique_words.append(word)
                unique_spans.append(selection_spans[index] if index < len(selection_spans) else None)
            duplicate_count = len(words) - len(unique_words)
            words = unique_words
            selection_spans = tuple(unique_spans)
            if not words:
                self.controller._report_voice_status(
                    "listening",
                    "No unique highlighted subtitle words are available to add.",
                )
                return False

            self.controller._voice_anki_batch_running = True
            self.controller._voice_anki_action_queued = True
            generation = self._begin_voice_action_feedback()
            state = {"index": 0, "added": 0}

            def finish_batch(succeeded: bool, message: str = "") -> None:
                if generation != int(getattr(self.controller, "_voice_action_generation", 0) or 0):
                    return
                self.controller._voice_anki_batch_running = False
                self.controller._voice_anki_action_queued = False
                if not succeeded:
                    self._finish_voice_action_feedback(generation, False)
                    self.controller._report_voice_status(
                        "error",
                        message or f"Highlighted-word add stopped after {state['added']} of {len(words)} cards.",
                    )
                    return
                if capture_after:
                    accepted = self._trigger_voice_capture_only(
                        use_capture_only_resume=False,
                        feedback_generation=generation,
                    )
                    if not accepted:
                        self._finish_voice_action_feedback(generation, False)
                    return
                self._finish_voice_action_feedback(generation, True)
                self.controller._report_voice_status(
                    "listening",
                    f"Added {state['added']} highlighted subtitle words as separate Anki cards"
                    + (f"; skipped {duplicate_count} duplicate(s)." if duplicate_count else "."),
                )

            def add_next() -> None:
                if generation != int(getattr(self.controller, "_voice_action_generation", 0) or 0):
                    return
                index = int(state["index"])
                if index >= len(words):
                    finish_batch(True)
                    return
                selected = words[index]
                self.controller._report_voice_status(
                    "working",
                    f"Adding highlighted word {index + 1} of {len(words)}: {selected}",
                )

                def completed(success: bool, _result=None) -> None:
                    if generation != int(getattr(self.controller, "_voice_action_generation", 0) or 0):
                        return
                    if not success:
                        finish_batch(
                            False,
                            f"Highlighted-word add stopped at word {index + 1} of {len(words)}: {selected}",
                        )
                        return
                    state["added"] += 1
                    state["index"] += 1
                    try:
                        self.settings.root.after(0, add_next)
                    except Exception:
                        add_next()

                try:
                    self.controller._add_selection_to_anki(
                        selected_text=selected,
                        subtitle_text=subtitle_text,
                        post_add_capture=False,
                        on_complete=completed,
                        cue_snapshot=cue_snapshot,
                        selection_spans=(selection_spans[index],)
                        if index < len(selection_spans) and selection_spans[index] is not None
                        else (),
                        initiator="voice",
                    )
                except Exception as exc:
                    logger.exception("Highlighted-word Anki batch failed")
                    finish_batch(False, f"Highlighted-word add failed: {exc}")

            add_next()
            return True

    def _add_current_anki_hotkey_target(
        self,
        *,
        post_add_capture: bool,
        subtitle_only: bool = False,
        prefer_highlight: bool = False,
        on_complete=None,
    ) -> bool:
            renderer = getattr(self, "renderer", None)

            def add_highlighted() -> bool:
                payload = self._highlighted_anki_payload()
                selected_text = str(payload.get("text") or "").strip()
                subtitle_text = str(payload.get("subtitle_text") or "")
                if not selected_text or not subtitle_text:
                    return False
                self.controller._add_selection_to_anki(
                    selected_text=selected_text,
                    subtitle_text=subtitle_text,
                    post_add_capture=bool(post_add_capture),
                    on_complete=on_complete,
                    cue_snapshot=payload.get("snapshot"),
                    selection_spans=payload.get("spans"),
                )
                return True

            if prefer_highlight and add_highlighted():
                return True

            hovered_target_getter = getattr(renderer, "hovered_subtitle_target_for_anki", None)
            hovered_target = hovered_target_getter() if callable(hovered_target_getter) else {}
            hovered_word = str(hovered_target.get("text") or "").strip()
            if hovered_word:
                subtitle_text = ""
                subtitle_getter = getattr(self.subtitle_navigation, "copy_text_for_current_subtitle", None)
                if callable(subtitle_getter):
                    subtitle_text = str(subtitle_getter() or "")
                add_to_anki = getattr(self.controller, "_add_selection_to_anki", None)
                if not callable(add_to_anki):
                    return False
                add_to_anki(
                    selected_text=hovered_word,
                    subtitle_text=subtitle_text,
                    post_add_capture=bool(post_add_capture),
                    on_complete=on_complete,
                    cue_snapshot=hovered_target.get("snapshot"),
                    selection_spans=hovered_target.get("spans"),
                )
                return True

            if subtitle_only:
                return add_highlighted() if not prefer_highlight else False
            add_selected = getattr(self.popup, "add_selected_to_anki_if_pointer_inside", None)
            if callable(add_selected):
                if post_add_capture:
                    if bool(add_selected(post_add_capture=True, on_complete=on_complete)):
                        return True
                elif bool(add_selected(on_complete=on_complete)):
                    return True
            return add_highlighted() if not prefer_highlight else False

    def _skip_buttons_use_subtitle_segments(self) -> bool:
            return bool(self.config.get("SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS") or False)

    def _hotkey_action_disabled(self, action: str) -> bool:
            normalized_action = str(action or "").strip()
            key = self._HOTKEY_DISABLE_OVERRIDES.get(normalized_action)
            if not key:
                key = self.HOTKEY_DISABLE_KEYS.get(normalized_action)
            if not key:
                return False
            return bool(self.config.get(key) or False)
    
    @staticmethod
    def _is_numpad_vk_key(key, *codes: int) -> bool:
            try:
                return hasattr(key, "vk") and int(getattr(key, "vk")) in codes
            except Exception as e:
                logger.debug("Failed to inspect numpad virtual key: %s", e, exc_info=True)
                return False

    def _get_shortcut_value(self, config_key: str) -> str:
            default = self._SHORTCUT_DEFAULT_OVERRIDES.get(config_key, self.SHORTCUT_DEFAULTS.get(config_key, ""))
            raw = self.config.get(config_key)
            if raw is None:
                value = default
            elif isinstance(raw, str):
                value = raw
            else:
                value = str(raw)
            value = str(value).strip().lower()
            if config_key == "SHORTCUT_TOGGLE_PLAY":
                if bool(self.config.get("DISABLE_SPACE_HOTKEY") or False):
                    bindings = self._expand_shortcut_bindings(value, config_key=config_key)
                    bindings = [
                        binding
                        for binding in bindings
                        if self._split_shortcut(binding) != (set(), "space")
                    ]
                    return ", ".join(bindings)
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
                ".": "period",
                "dot": "period",
                ">": "period",
                ":": "period",
                ",": "comma",
                "<": "comma",
                ";": "comma",
                "altgraph": "altgr",
                "rightalt": "altgr",
            }
            return alias.get(t, t)

    def _split_shortcut(self, binding: str):
            text = str(binding or "").strip().lower().replace(" ", "")
            if not text:
                return set(), None
            parts = [p for p in text.split("+") if p]
            mods = set()
            key_token = None
            invalid = False
            for p in parts:
                token = self._normalize_shortcut_token(p)
                if token == "altgr":
                    mods.update({"ctrl", "alt"})
                elif token in {"shift", "alt", "ctrl"}:
                    mods.add(token)
                elif key_token is None:
                    key_token = token
                else:
                    invalid = True
            if invalid:
                return mods, None
            return mods, key_token

    def _warn_invalid_shortcut(self, config_key: str, binding: str) -> None:
            warned = getattr(self.controller, "_warned_invalid_shortcuts", None)
            if warned is None:
                warned = set()
                self.controller._warned_invalid_shortcuts = warned
            marker = (str(config_key or ""), str(binding or ""))
            if marker in warned:
                return
            warned.add(marker)
            logger.warning("Ignoring invalid shortcut for %s: %r", config_key, binding)

    def _expand_shortcut_bindings(self, value: str, config_key: str = "") -> list[str]:
            bindings: list[str] = []
            for raw_binding in re.split(r"[,;]", str(value or "")):
                binding = raw_binding.strip().lower()
                if not binding:
                    continue
                _mods, key_token = self._split_shortcut(binding)
                if not key_token:
                    self._warn_invalid_shortcut(config_key, binding)
                    continue
                bindings.append(binding)
            return bindings

    def _shortcut_bindings_for_key(self, config_key: str) -> list[str]:
            return self._expand_shortcut_bindings(
                self._get_shortcut_value(config_key),
                config_key=config_key,
            )
    
    @staticmethod
    def _is_text_input_widget(widget) -> bool:
            if widget is None:
                return False

            try:
                if not bool(widget.winfo_exists()):
                    return False
                cls = str(widget.winfo_class() or "").lower()
            except Exception:
                return False
            try:
                if not bool(widget.winfo_viewable()):
                    return False
            except Exception:
                pass

            if cls in {
                "entry",
                "tentry",
                "ttk::entry",
                "combobox",
                "tcombobox",
                "ttk::combobox",
                "spinbox",
                "ttk::spinbox",
            }:
                return True

            if cls == "text":
                try:
                    state = str(widget.cget("state") or "").lower()
                except Exception:
                    state = ""

                return state not in {"disabled", "readonly"}

            return False
    
    def _is_popup_display_text_widget(self, widget) -> bool:
            if widget is None:
                return False
            try:
                popup_entry = getattr(getattr(self.controller, "popup", None), "_entry_widget", None)
                return widget is popup_entry
            except Exception:
                return False


    def _read_text_input_focused(self) -> bool:
            cached_hwnds = getattr(self.controller, "_app_window_hwnds", None)
            app_foreground = is_any_window_foreground(
                cached_hwnds if cached_hwnds is not None else self._focus_owner_windows()
            )
            if app_foreground is False:
                return False

            for owner in self._focus_owner_windows():
                if owner is None:
                    continue
                try:
                    widget = owner.focus_get()
                except Exception as e:
                    logger.debug("Failed to inspect focused widget: %s", e, exc_info=True)
                    continue

                if widget is None:
                    continue
                if self._is_popup_display_text_widget(widget):
                    return False
                if self._is_text_input_widget(widget):
                    return True
            return False

    def _is_text_input_focused(self) -> bool:
            return bool(getattr(self.controller, "_cached_text_input_focused", False))

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
                _add("numpad0", "0")
            if self._is_numpad_vk_key(key, 100):
                _add("numpad4", "4")
            if self._is_numpad_vk_key(key, 102):
                _add("numpad6", "6")

            return {self._normalize_shortcut_token(t) for t in tokens}

    @staticmethod
    def _modifier_tokens_for_key(key) -> set[str]:
            alt_gr = getattr(Key, "alt_gr", None)
            if alt_gr is not None and key == alt_gr:
                return {"ctrl", "alt"}
            if key in tuple(filter(None, (getattr(Key, "shift", None), Key.shift_l, Key.shift_r))):
                return {"shift"}
            if key in tuple(filter(None, (getattr(Key, "alt", None), Key.alt_l, Key.alt_r))):
                return {"alt"}
            if key in tuple(filter(None, (getattr(Key, "ctrl", None), Key.ctrl_l, Key.ctrl_r))):
                return {"ctrl"}
            return set()

    def _set_modifier_tokens(self, modifier_tokens: set[str], pressed: bool) -> bool:
            changed = False
            for token, attr in (
                ("shift", "shift_pressed"),
                ("alt", "alt_pressed"),
                ("ctrl", "ctrl_pressed"),
            ):
                if token not in modifier_tokens:
                    continue
                old_value = bool(getattr(self, attr, False))
                new_value = bool(pressed)
                if old_value != new_value:
                    setattr(self, attr, new_value)
                    changed = True
            return changed


    def _shortcut_matches(self, binding: str, key) -> bool:
            mods, key_token = self._split_shortcut(binding)
            if not key_token:
                return False

            active_mods = set()
            if self.shift_pressed:
                active_mods.add("shift")
            if self.alt_pressed:
                active_mods.add("alt")
            if self.ctrl_pressed:
                active_mods.add("ctrl")
            if active_mods != mods:
                return False

            return key_token in self._key_tokens(key)

    def _active_modifier_tokens(self) -> set[str]:
            active_mods = set()
            if self.shift_pressed:
                active_mods.add("shift")
            if self.alt_pressed:
                active_mods.add("alt")
            if self.ctrl_pressed:
                active_mods.add("ctrl")
            return active_mods

    def _set_pressed_key_tokens(self, key, pressed: bool) -> None:
            tokens = self._key_tokens(key)
            if not tokens:
                return
            pressed_tokens = getattr(self.controller, "_pressed_key_tokens", None)
            if not isinstance(pressed_tokens, set):
                pressed_tokens = set()
                self.controller._pressed_key_tokens = pressed_tokens
            if pressed:
                pressed_tokens.update(tokens)
            else:
                pressed_tokens.difference_update(tokens)

    def _hold_binding_active(self, binding: str) -> bool:
            physical_state = self._physical_hold_binding_active(binding)
            if physical_state is not None:
                return physical_state

            mods, key_token = self._split_shortcut(binding)
            if not mods and not key_token:
                return False
            active_mods = self._active_modifier_tokens()
            if active_mods != mods:
                return False
            if key_token is None:
                return bool(mods)
            pressed_tokens = getattr(self.controller, "_pressed_key_tokens", set())
            return key_token in pressed_tokens

    @staticmethod
    def _windows_key_down(vk_code: int) -> bool | None:
            try:
                get_async_key_state = ctypes.windll.user32.GetAsyncKeyState
            except Exception:
                return None
            try:
                return bool(int(get_async_key_state(int(vk_code))) & 0x8000)
            except Exception:
                return None

    @staticmethod
    def _virtual_key_for_token(token: str | None) -> int | None:
            value = str(token or "").strip().lower()
            if len(value) == 1 and ("a" <= value <= "z" or "0" <= value <= "9"):
                return ord(value.upper())
            if value.startswith("f") and value[1:].isdigit():
                number = int(value[1:])
                if 1 <= number <= 24:
                    return 0x70 + number - 1
            virtual_keys = {
                "backspace": 0x08,
                "tab": 0x09,
                "enter": 0x0D,
                "return": 0x0D,
                "escape": 0x1B,
                "esc": 0x1B,
                "space": 0x20,
                "pageup": 0x21,
                "pagedown": 0x22,
                "end": 0x23,
                "home": 0x24,
                "left": 0x25,
                "up": 0x26,
                "right": 0x27,
                "down": 0x28,
                "insert": 0x2D,
                "delete": 0x2E,
                "numpad0": 0x60,
                "numpad1": 0x61,
                "numpad2": 0x62,
                "numpad3": 0x63,
                "numpad4": 0x64,
                "numpad5": 0x65,
                "numpad6": 0x66,
                "numpad7": 0x67,
                "numpad8": 0x68,
                "numpad9": 0x69,
                "comma": 0xBC,
                "period": 0xBE,
            }
            return virtual_keys.get(value)

    def _physical_hold_binding_active(self, binding: str) -> bool | None:
            mods, key_token = self._split_shortcut(binding)
            if not mods and not key_token:
                return False

            modifier_vks = {"shift": 0x10, "ctrl": 0x11, "alt": 0x12}
            active_mods: set[str] = set()
            for modifier, vk_code in modifier_vks.items():
                pressed = self._windows_key_down(vk_code)
                if pressed is None:
                    return None
                if pressed:
                    active_mods.add(modifier)
            if active_mods != mods:
                return False
            if key_token is None:
                return bool(mods)

            vk_code = self._virtual_key_for_token(key_token)
            if vk_code is None:
                return None
            return self._windows_key_down(vk_code)

    def _config_bool(self, key: str, default: bool = False) -> bool:
            try:
                value = self.config.get(key)
            except Exception:
                return bool(default)
            if value is None:
                return bool(default)
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)

    def hover_hold_active(
        self,
        enabled_key: str,
        hotkey_key: str,
        default_hotkey: str,
        *,
        legacy_enabled_key: str | None = None,
    ) -> bool:
            enabled_default = False
            if legacy_enabled_key:
                enabled_default = self._config_bool(legacy_enabled_key, False)
            enabled = self._config_bool(enabled_key, enabled_default)
            if legacy_enabled_key:
                enabled = enabled or self._config_bool(legacy_enabled_key, False)
            if not enabled:
                return False
            raw = self.config.get(hotkey_key)
            value = str(raw if raw is not None else default_hotkey or "").strip().lower()
            if not value:
                return False
            for raw_binding in re.split(r"[,;]", value):
                binding = raw_binding.strip().lower()
                if binding and self._hold_binding_active(binding):
                    return True
            return False

    def hover_hold_active_score(
        self,
        enabled_key: str,
        hotkey_key: str,
        default_hotkey: str,
        *,
        legacy_enabled_key: str | None = None,
    ) -> int:
            enabled_default = False
            if legacy_enabled_key:
                enabled_default = self._config_bool(legacy_enabled_key, False)
            enabled = self._config_bool(enabled_key, enabled_default)
            if legacy_enabled_key:
                enabled = enabled or self._config_bool(legacy_enabled_key, False)
            if not enabled:
                return 0
            raw = self.config.get(hotkey_key)
            value = str(raw if raw is not None else default_hotkey or "").strip().lower()
            if not value:
                return 0
            best = 0
            for raw_binding in re.split(r"[,;]", value):
                binding = raw_binding.strip().lower()
                if not binding or not self._hold_binding_active(binding):
                    continue
                mods, key_token = self._split_shortcut(binding)
                best = max(best, len(mods) + (1 if key_token else 0))
            return best

    def _current_hover_mode_for_refresh(self) -> str:
            callback = getattr(self.controller, "_hover_modifier_mode", None)
            if callable(callback):
                try:
                    return str(callback() or "ruby")
                except Exception:
                    pass
            return "ruby"

    def _refresh_hover_displays_if_mode_changed(self, old_mode: str) -> None:
            new_mode = self._current_hover_mode_for_refresh()
            if str(old_mode or "ruby") != str(new_mode or "ruby"):
                self.controller._hover_refresh_requested = True

    def _poll_hover_mode(self) -> None:
            current_mode = self._current_hover_mode_for_refresh()
            previous_mode = str(getattr(self.controller, "_last_polled_hover_mode", "ruby") or "ruby")
            refresh_requested = bool(getattr(self.controller, "_hover_refresh_requested", False))
            self.controller._last_polled_hover_mode = current_mode
            self.controller._hover_refresh_requested = False
            if refresh_requested or current_mode != previous_mode:
                self._refresh_hover_displays()

    def _poll_global_toggle_subtitles(self) -> None:
            if getattr(self.controller, "_browser_text_input_focused", False):
                return
            if self._modal_input_blocked():
                return
            bindings = self._shortcut_bindings_for_key("SHORTCUT_TOGGLE_SUBTITLES")
            physical_states = [self._physical_hold_binding_active(binding) for binding in bindings]
            supported_states = [state for state in physical_states if state is not None]
            if not supported_states:
                return

            active = any(supported_states)
            was_active = bool(getattr(self.controller, "_physical_toggle_subtitles_active", False))
            self.controller._physical_toggle_subtitles_active = active
            if not active:
                if was_active:
                    self._single_fire_actions.discard("toggle_subtitles")
                return
            if was_active or self._is_text_input_focused():
                return
            if self._hotkeys_disabled() or self._hotkey_action_disabled("toggle_subtitles"):
                return
            if "toggle_subtitles" in self._single_fire_actions:
                return
            self._single_fire_actions.add("toggle_subtitles")
            self._enqueue_input_action("toggle_subtitles", source="physical")

    def _set_hover_hold_key_state(self, key, pressed: bool) -> None:
            old_hover_mode = self._current_hover_mode_for_refresh()
            self._set_pressed_key_tokens(key, bool(pressed))
            self._set_modifier_tokens(self._modifier_tokens_for_key(key), bool(pressed))
            self._refresh_hover_displays_if_mode_changed(old_hover_mode)

    def _mode2_numpad_enabled(self) -> bool:
            try:
                return int(getattr(self.settings, "input_mode", 1)) == 2
            except Exception as e:
                logger.debug("Failed to inspect input mode: %s", e, exc_info=True)
                return bool(getattr(self.settings, "numpad_mode_enabled", False))

    def _repeat_action_bindings(self):
            mode2_numpad = self._mode2_numpad_enabled()
            if mode2_numpad:
                binding_specs = [
                    ("subtitle_back", "SHORTCUT_MODE2_SUBTITLE_BACK"),
                    ("subtitle_forward", "SHORTCUT_MODE2_SUBTITLE_FORWARD"),
                    ("go_back", "SHORTCUT_MODE2_GO_BACK"),
                    ("go_forward", "SHORTCUT_MODE2_GO_FORWARD"),
                ]
            else:
                binding_specs = [
                    ("subtitle_back", "SHORTCUT_SUBTITLE_BACK"),
                    ("subtitle_forward", "SHORTCUT_SUBTITLE_FORWARD"),
                    ("go_back", "SHORTCUT_GO_BACK"),
                    ("go_forward", "SHORTCUT_GO_FORWARD"),
                ]
            bindings = []
            for action, config_key in binding_specs:
                if self._hotkey_action_disabled(action):
                    continue
                for binding in self._shortcut_bindings_for_key(config_key):
                    bindings.append((action, binding))
            return bindings

    def _single_fire_bindings(self):
            play_shortcut_key = (
                "SHORTCUT_MODE2_TOGGLE_PLAY"
                if self._mode2_numpad_enabled()
                else "SHORTCUT_TOGGLE_PLAY"
            )
            binding_specs = [
                ("toggle_play", play_shortcut_key),
                ("alt_x", "SHORTCUT_BRING_TO_FRONT"),
                ("episode_inc", "SHORTCUT_EPISODE_INC"),
                ("episode_dec", "SHORTCUT_EPISODE_DEC"),
                ("jump_sub_end", "SHORTCUT_JUMP_SUB_END"),
                ("toggle_subtitles", "SHORTCUT_TOGGLE_SUBTITLES"),
                ("fast_forward_speed_up", "SHORTCUT_FAST_FORWARD_SPEED_UP"),
                ("fast_forward_speed_down", "SHORTCUT_FAST_FORWARD_SPEED_DOWN"),
                ("toggle_debugging", "SHORTCUT_TOGGLE_DEBUGGING"),
            ]
            bindings = []
            for action, config_key in binding_specs:
                if self._hotkey_action_disabled(action):
                    continue
                for binding in self._shortcut_bindings_for_key(config_key):
                    bindings.append((action, binding))
            return bindings

    def _always_available_single_fire_bindings(self):
            bindings = []
            for binding in self._shortcut_bindings_for_key("SHORTCUT_TOGGLE_VOICE"):
                bindings.append(("toggle_voice_listening", binding))
            return bindings

    def _popup_translation_bindings(self):
            bindings = []
            for action, config_key, provider in (
                ("popup_deepl_translate", "SHORTCUT_POPUP_DEEPL_TRANSLATE", "deepl"),
                ("popup_google_translate", "SHORTCUT_POPUP_GOOGLE_TRANSLATE", "google"),
            ):
                for binding in self._shortcut_bindings_for_key(config_key):
                    bindings.append((action, binding, provider))
            return bindings

    def _popup_add_anki_bindings(self) -> list[tuple[str, str]]:
            specs = [
                ("popup_add_anki", "SHORTCUT_POPUP_ADD_ANKI"),
                ("popup_add_anki_capture", "SHORTCUT_POPUP_ADD_ANKI_CAPTURE"),
                ("capture_only", "SHORTCUT_CAPTURE_ONLY"),
            ]
            bindings: list[tuple[str, str]] = []
            for action, config_key in specs:
                if self._hotkey_action_disabled(action):
                    continue
                for binding in self._shortcut_bindings_for_key(config_key):
                    bindings.append((action, binding))
            return bindings

    def _popup_add_anki_release_actions(self, key) -> set[str]:
            actions: set[str] = set()
            for action, binding in self._popup_add_anki_bindings():
                if self._binding_release_matches(binding, key):
                    actions.add(action)
            return actions

    def _copy_popup_selection_shortcut(self, key) -> bool:
            if not self.ctrl_pressed or "c" not in self._key_tokens(key):
                return False
            hover_window = getattr(getattr(self, "renderer", None), "_hover_text_window", None)
            hover_open = self._window_exists(hover_window)
            if hover_open:
                try:
                    hover_open = str(hover_window.state()) != "withdrawn"
                except Exception:
                    hover_open = False
            if not self._is_popup_open() and not hover_open:
                return False
            self._enqueue_input_action("popup_copy_selection", source="physical")
            return True

    def _binding_release_matches(self, binding: str, key) -> bool:
            mods, key_token = self._split_shortcut(binding)
            released_tokens = self._key_tokens(key)
            if key_token and key_token in released_tokens:
                return True
            return bool(mods & self._modifier_tokens_for_key(key))

    def _translation_release_matches(self, action: str, key) -> bool:
            active_binding = getattr(self, "_active_translation_binding", None)
            if active_binding:
                return self._binding_release_matches(active_binding, key)
            for candidate, binding, _provider in self._popup_translation_bindings():
                if candidate != action:
                    continue
                if self._binding_release_matches(binding, key):
                    return True
            return False

    def _shortcut_candidate_identity(self, candidate: dict) -> tuple:
            return (
                candidate.get("kind"),
                candidate.get("action"),
                candidate.get("provider"),
                bool(candidate.get("single_fire")),
            )

    def _dedupe_shortcut_candidates(self, candidates: list[dict]) -> list[dict]:
            deduped = []
            seen = set()
            for candidate in candidates:
                marker = (
                    self._shortcut_candidate_identity(candidate),
                    str(candidate.get("binding") or ""),
                )
                if marker in seen:
                    continue
                seen.add(marker)
                deduped.append(candidate)
            return deduped

    def _warn_ambiguous_shortcut(self, candidates: list[dict]) -> None:
            warned = getattr(self.controller, "_warned_ambiguous_shortcuts", None)
            if warned is None:
                warned = set()
                self.controller._warned_ambiguous_shortcuts = warned
            marker = tuple(
                sorted(
                    (
                        str(candidate.get("binding") or ""),
                        str(candidate.get("kind") or ""),
                        str(candidate.get("action") or ""),
                        str(candidate.get("provider") or ""),
                    )
                    for candidate in candidates
                )
            )
            if marker in warned:
                return
            warned.add(marker)
            details = ", ".join(
                f"{candidate.get('binding')} -> {candidate.get('action')}"
                + (f"/{candidate.get('provider')}" if candidate.get("provider") else "")
                for candidate in candidates
            )
            logger.warning("Ignoring ambiguous shortcut assignment: %s", details)

    def _resolve_shortcut_candidates(self, candidates: list[dict]) -> dict | None:
            candidates = self._dedupe_shortcut_candidates(candidates)
            if not candidates:
                return None
            identities = {self._shortcut_candidate_identity(candidate) for candidate in candidates}
            if len(identities) > 1:
                self._warn_ambiguous_shortcut(candidates)
                return None
            return candidates[0]

    def _matching_shortcut_candidates(self, key) -> list[dict]:
            candidates = []
            for action, binding in self._always_available_single_fire_bindings():
                if self._shortcut_matches(binding, key):
                    candidates.append(
                        {
                            "kind": "single",
                            "action": action,
                            "binding": binding,
                            "single_fire": True,
                        }
                    )
            popup_open = self._is_popup_open()
            if popup_open or not self._hotkeys_disabled():
                for action, binding in self._popup_add_anki_bindings():
                    # Popup add actions remain available while shortcut mode is
                    # disabled, but capture-only is a global action and must
                    # honor the global disable setting in every context.
                    if action == "capture_only" and self._hotkeys_disabled():
                        continue
                    if self._shortcut_matches(binding, key):
                        candidates.append(
                            {
                                "kind": "popup_add",
                                "action": action,
                                "binding": binding,
                                "single_fire": True,
                            }
                        )
            if popup_open:
                for action, binding, provider in self._popup_translation_bindings():
                    if binding and self._shortcut_matches(binding, key):
                        candidates.append(
                            {
                                "kind": "popup_translation",
                                "action": action,
                                "binding": binding,
                                "provider": provider,
                                "single_fire": True,
                            }
                        )
            if not self._hotkeys_disabled():
                for action, binding in self._single_fire_bindings():
                    if self._shortcut_matches(binding, key):
                        candidates.append(
                            {
                                "kind": "single",
                                "action": action,
                                "binding": binding,
                                "single_fire": True,
                            }
                        )
                for action, binding in self._repeat_action_bindings():
                    if self._shortcut_matches(binding, key):
                        candidates.append(
                            {
                                "kind": "repeat",
                                "action": action,
                                "binding": binding,
                                "single_fire": False,
                            }
                        )
            return candidates

    def _hotkeys_disabled(self) -> bool:
            return bool(self.config.get("SHORTCUTS_DISABLED") or False)

    def _reset_hotkey_state(self, reset_shift: bool = True) -> None:
            was_shift_pressed = bool(getattr(self, "shift_pressed", False))
            was_translation_pressed = bool(getattr(self, "translation_pressed", False))
            if reset_shift:
                self.shift_pressed = False
            self.controller._pressed_key_tokens = set()
            self.translation_pressed = False
            self.translation_provider = "deepl"
            self._active_translation_action = None
            self._active_translation_binding = None
            self.alt_pressed = False
            self.ctrl_pressed = False
            self._single_fire_actions.clear()
            repeat_actions = {action for action, _ in self._repeat_action_bindings()}
            self._release_repeat_actions(repeat_actions, settle_seek=False)
            if reset_shift and was_shift_pressed:
                self.controller._hover_refresh_requested = True
            if was_translation_pressed:
                self._request_popup_translation_display_refresh()

    def _toggle_m3_mode(self) -> None:
            enable_m3 = not self._hotkeys_disabled()
            try:
                self.settings.set_hotkeys_disabled(enable_m3)
            except Exception as e:
                logger.debug("Failed to toggle m3 mode: %s", e, exc_info=True)
                return
            # Clear hotkey state without disabling Shift-hover dictionary lookup.
            self._reset_hotkey_state(reset_shift=False)

    def _on_key_press(self, key):
            if getattr(self.controller, "_browser_text_input_focused", False):
                return
            if self._modal_input_blocked():
                return
            text_input_focused = self._is_text_input_focused()

            if text_input_focused:
                self._set_hover_hold_key_state(key, True)
                if self._copy_popup_selection_shortcut(key):
                    return
                popup_translation_candidate = None
                if self._is_popup_open():
                    popup_translation_candidates = []
                    for action, binding, provider in self._popup_translation_bindings():
                        if binding and self._shortcut_matches(binding, key):
                            popup_translation_candidates.append(
                                {
                                    "kind": "popup_translation",
                                    "action": action,
                                    "binding": binding,
                                    "provider": provider,
                                    "single_fire": True,
                                }
                            )

                    popup_translation_candidate = self._resolve_shortcut_candidates(popup_translation_candidates)

                if popup_translation_candidate is not None:
                    provider = popup_translation_candidate.get("provider") or "deepl"
                    binding = popup_translation_candidate.get("binding")
                    action = popup_translation_candidate.get("action")
                    if self.translation_pressed:
                        if self.translation_provider != provider:
                            self.translation_provider = provider
                            self._active_translation_action = action
                            self._active_translation_binding = binding
                            self._request_popup_translation_display_refresh(source="physical")
                        return
                    self.translation_pressed = True
                    self.translation_provider = provider
                    self._active_translation_action = action
                    self._active_translation_binding = binding
                    self._request_popup_translation_display_refresh(source="physical")
                    return

                popup_add_candidate = None
                if self._is_popup_open():
                    popup_add_candidates = []
                    for action, binding in self._popup_add_anki_bindings():
                        if self._shortcut_matches(binding, key):
                            popup_add_candidates.append(
                                {
                                    "kind": "popup_add",
                                    "action": action,
                                    "binding": binding,
                                    "single_fire": True,
                                }
                            )

                    popup_add_candidate = self._resolve_shortcut_candidates(popup_add_candidates)

                if popup_add_candidate is not None:
                    action = popup_add_candidate.get("action")
                    if action in self._single_fire_actions:
                        return
                    self._single_fire_actions.add(action)
                    self._enqueue_input_action(action, source="physical")
                    return

                return

            old_hover_mode = self._current_hover_mode_for_refresh()
            self._set_pressed_key_tokens(key, True)

            modifier_tokens = self._modifier_tokens_for_key(key)
            if modifier_tokens:
                if self._set_modifier_tokens(modifier_tokens, True):
                    self._refresh_hover_displays_if_mode_changed(old_hover_mode)
                return

            chord = getattr(self.controller, "_legacy_capture_chord", None)
            if (chord and time.monotonic() < getattr(self.controller, "_legacy_capture_until", 0)
                    and self._shortcut_matches(chord, key)):
                return

            self._refresh_hover_displays_if_mode_changed(old_hover_mode)

            if self._copy_popup_selection_shortcut(key):
                return

            candidate = self._resolve_shortcut_candidates(self._matching_shortcut_candidates(key))
            if candidate is None:
                return
            kind = candidate.get("kind")
            action = candidate.get("action")
            if kind == "popup_translation":
                provider = candidate.get("provider") or "deepl"
                binding = candidate.get("binding")
                if self.translation_pressed:
                    if self.translation_provider != provider:
                        self.translation_provider = provider
                        self._active_translation_action = action
                        self._active_translation_binding = binding
                        self._request_popup_translation_display_refresh(source="physical")
                    return
                self.translation_pressed = True
                self.translation_provider = provider
                self._active_translation_action = action
                self._active_translation_binding = binding
                self._request_popup_translation_display_refresh(source="physical")
                return
            if kind == "popup_add":
                if action in self._single_fire_actions:
                    return
                self._single_fire_actions.add(action)
                self._enqueue_input_action(action, source="physical")
                return
            if bool(candidate.get("single_fire")):
                if action in self._single_fire_actions:
                    return
                self._single_fire_actions.add(action)
                self._enqueue_input_action(action, source="physical")
                return
            if not self._hold_repeat_action(action):
                return
            if not self._is_seek_repeat_action(action):
                self._queue_repeat_step(action)
                return

    def _on_key_release(self, key):
            if getattr(self.controller, "_browser_text_input_focused", False):
                return
            if self._modal_input_blocked():
                return
            if self._is_text_input_focused():
                self._set_hover_hold_key_state(key, False)
                for action in self._popup_add_anki_release_actions(key):
                    self._single_fire_actions.discard(action)
                active_translation_action = getattr(self, "_active_translation_action", None)
                if active_translation_action and self._translation_release_matches(active_translation_action, key):
                    self.translation_pressed = False
                    self.translation_provider = "deepl"
                    self._active_translation_action = None
                    self._active_translation_binding = None
                    self._request_popup_translation_display_refresh(source="physical")
                return
            old_hover_mode = self._current_hover_mode_for_refresh()
            self._set_pressed_key_tokens(key, False)
            for action in self._popup_add_anki_release_actions(key):
                self._single_fire_actions.discard(action)

            active_translation_action = getattr(self, "_active_translation_action", None)
            if active_translation_action and self._translation_release_matches(active_translation_action, key):
                self.translation_pressed = False
                self.translation_provider = "deepl"
                self._active_translation_action = None
                self._active_translation_binding = None
                self._request_popup_translation_display_refresh(source="physical")
                return

            modifier_tokens = self._modifier_tokens_for_key(key)
            if "shift" in modifier_tokens:
                if self.shift_pressed:
                    self._set_modifier_tokens({"shift"}, False)
                    self._refresh_hover_displays_if_mode_changed(old_hover_mode)
                with self._repeat_lock:
                    repeat_actions_to_drop = set(self._held_repeat_next_fire.keys())
                if not repeat_actions_to_drop:
                    repeat_actions_to_drop = {action for action, _ in self._repeat_action_bindings()}
                self._release_repeat_actions(repeat_actions_to_drop, settle_seek=True)
                return

            if self._hotkeys_disabled():
                self._reset_hotkey_state(reset_shift=False)
                return

            if modifier_tokens:
                if self._set_modifier_tokens(modifier_tokens, False):
                    self._refresh_hover_displays_if_mode_changed(old_hover_mode)

            released_tokens = self._key_tokens(key)
            for action, binding in (
                self._always_available_single_fire_bindings() + self._single_fire_bindings()
            ):
                _, key_token = self._split_shortcut(binding)
                if key_token and key_token in released_tokens:
                    self._single_fire_actions.discard(action)

            repeat_actions_to_drop = set()
            for action, binding in self._repeat_action_bindings():
                _, key_token = self._split_shortcut(binding)
                if key_token and key_token in released_tokens:
                    repeat_actions_to_drop.add(action)
            if modifier_tokens:
                repeat_actions_to_drop.update(action for action, _ in self._repeat_action_bindings())
            self._release_repeat_actions(repeat_actions_to_drop, settle_seek=True)
            self._refresh_hover_displays_if_mode_changed(old_hover_mode)

    def on_alt_x(self, event=None):
            if not bool(getattr(self.controller, "control_show_on_subtitle_hover", True)):
                control = getattr(self.settings, "control_window", None)
                subtitle = getattr(getattr(self.controller, "overlay", None), "sub_window", None)
                control_shown = self._window_is_shown(control)
                subtitle_shown = (
                    not bool(getattr(self.controller, "subtitles_user_hidden", False))
                    and self._window_is_shown(subtitle)
                )

                if subtitle_shown and control_shown:
                    try:
                        control.withdraw()
                    except Exception:
                        pass
                    return

                if not subtitle_shown:
                    if bool(getattr(self.controller, "subtitles_user_hidden", False)):
                        self.subtitle_navigation.toggle_subtitle_visibility()
                    else:
                        self.subtitle_navigation._show_subtitle_overlay_window()
                        self.last_subtitle_text = ""
                        self.subtitle_navigation._update_subtitle_display(force=True)

                if not control_shown:
                    self._show_control_window_for_hotkey(control)
                self.popup.ensure_on_top()
                return

            self._show_control_window_for_hotkey(getattr(self.settings, "control_window", None))
            self.popup.ensure_on_top()

    def _toggle_display_windows_for_voice(self) -> None:
            try:
                control = getattr(self.settings, "control_window", None)
                subtitle = getattr(getattr(self.controller, "overlay", None), "sub_window", None)
                both_visible = self._window_is_shown(control) and self._window_is_shown(subtitle)
                self.subtitle_navigation.set_subtitle_visibility(not both_visible)
                if both_visible:
                    try:
                        control.withdraw()
                    except Exception:
                        pass
                else:
                    self._sync_control_window_for_subtitle_visibility()
                self.popup.ensure_on_top()
                self.controller._report_voice_status(
                    "listening",
                    "Subtitle and control windows hidden." if both_visible else "Subtitle and control windows shown.",
                )
            except Exception as exc:
                logger.exception("Failed to toggle subtitle/control windows from voice command")
                self.controller._report_voice_status("error", f"Could not toggle subtitle/control windows: {exc}")

    def _toggle_app_settings_for_voice(self) -> None:
            toggle = getattr(self.settings, "toggle_settings_window_from_control", None)
            if not callable(toggle):
                self.controller._report_voice_status("error", "The app settings windows are unavailable.")
                return
            try:
                shown = bool(toggle())
                self.controller._report_voice_status(
                    "listening",
                    "App settings windows shown." if shown else "App settings windows hidden.",
                )
            except Exception as exc:
                logger.exception("Failed to toggle app settings windows from voice command")
                self.controller._report_voice_status("error", f"Could not toggle app settings windows: {exc}")

    def _show_settings_window_for_voice(self) -> None:
            show = getattr(self.settings, "show_settings_window", None)
            if not callable(show):
                self.controller._report_voice_status("error", "The settings window is unavailable.")
                return
            try:
                show()
                on_open = getattr(self.settings, "_on_settings_open", None)
                if callable(on_open):
                    on_open()
                self.controller._report_voice_status("listening", "Settings window shown.")
            except Exception as exc:
                logger.exception("Failed to show settings window from voice command")
                self.controller._report_voice_status("error", f"Could not show the settings window: {exc}")

    def _open_advanced_settings_for_voice(self) -> None:
            try:
                self.settings.show_settings_window()
                win = getattr(self.settings, "advanced_window", None)
                if win is not None and self._window_exists(win):
                    self.settings.adv_settings._show_advanced_window(win)
                else:
                    self.settings._open_advanced_settings_window()
                self.controller._report_voice_status("listening", "Advanced Settings opened.")
            except Exception as exc:
                logger.exception("Failed to open Advanced Settings from voice command")
                self.controller._report_voice_status("error", f"Could not open Advanced Settings: {exc}")

    def _close_advanced_settings_for_voice(self) -> None:
            win = getattr(self.settings, "advanced_window", None)
            if win is None or not self._window_exists(win):
                self.controller._report_voice_status("listening", "Advanced Settings is already closed.")
                return
            try:
                win.destroy()
                self.controller._report_voice_status("listening", "Advanced Settings closed.")
            except Exception as exc:
                logger.exception("Failed to close Advanced Settings from voice command")
                self.controller._report_voice_status("error", f"Could not close Advanced Settings: {exc}")

    @staticmethod
    def _window_is_shown(window) -> bool:
            if window is None:
                return False
            try:
                if not window.winfo_exists():
                    return False
                if str(window.state() or "").lower() in {"withdrawn", "iconic"}:
                    return False
                return bool(window.winfo_ismapped())
            except Exception:
                return False

    @staticmethod
    def _show_control_window_for_hotkey(control) -> None:
            if control is None:
                return
            try:
                control.deiconify()
                control.lift()
                control.attributes("-topmost", True)
            except Exception:
                pass

    def _refresh_hover_displays(self) -> None:
            for owner_name in ("popup", "renderer"):
                try:
                    owner = getattr(self.controller, owner_name, None)
                    refresh = getattr(owner, "refresh_hover_display", None)
                    if callable(refresh):
                        refresh()
                except Exception:
                    pass

    def _refresh_popup_translation_display(self) -> None:
            try:
                refresh = getattr(getattr(self.controller, "popup", None), "refresh_hover_display", None)
                if callable(refresh):
                    refresh()
            except Exception:
                pass

    def _request_popup_translation_display_refresh(self, *, source: str = "internal") -> None:
            self._enqueue_input_action("_refresh_popup_translation", source=source)

    def _read_popup_open(self) -> bool:
            try:
                popup = getattr(self.controller, "popup", None)
                if getattr(popup, "_popup", None) is not None:
                    return True
                if hasattr(popup, "_popup"):
                    return False
                checker = getattr(popup, "is_open", None)
                return bool(checker()) if callable(checker) else False
            except Exception:
                return False

    def _is_popup_open(self) -> bool:
            return bool(getattr(self.controller, "_cached_popup_open", False))

    def _sync_control_window_for_subtitle_visibility(self) -> None:
            control = getattr(self.settings, "control_window", None)
            overlay = getattr(getattr(self.controller, "overlay", None), "sub_window", None)
            if control is None:
                return
            try:
                if not control.winfo_exists():
                    return
                if bool(getattr(self.controller, "subtitles_user_hidden", False)):
                    control.withdraw()
                    if overlay is not None and overlay.winfo_exists():
                        overlay.withdraw()
                else:
                    if overlay is not None and overlay.winfo_exists():
                        overlay.deiconify()
                        overlay.lift()
                        try:
                            overlay.attributes("-topmost", True)
                        except Exception:
                            pass
                    control.deiconify()
                    control.lift()
                    try:
                        control.attributes("-topmost", True)
                    except Exception:
                        pass
            except Exception:
                pass

    def _on_global_click(self, x, y, button, pressed):
            if not pressed:
                return
            if getattr(self.controller, "_browser_text_input_focused", False):
                return
            if self._modal_input_blocked():
                return
            self._reset_transient_hotkey_state_for_external_click(x, y, button)
            if self._is_text_input_focused():
                self._reset_hotkey_state(reset_shift=True)
                return
            if button == Button.x1:
                if not self._hotkeys_disabled() and not self._hotkey_action_disabled("popup_add_anki_capture"):
                    self._enqueue_input_action("subtitle_hover_add_anki_capture", source="mouse")
            elif button == Button.x2:
                if self._point_inside_subtitle_window(x, y):
                    if not self._hotkeys_disabled():
                        self._enqueue_input_action("subtitle_hover_add_anki", source="mouse")
                elif not self._hotkey_action_disabled("clear_subtitle"):
                    self._enqueue_input_action("clear_subtitle", source="mouse")
            elif button == Button.middle:
                self._enqueue_input_action("toggle_m3_mode", source="mouse")
