"""Keyboard shortcut, repeat-action, and global hotkey helper."""

import ctypes
import logging
import re
import time
import queue
from pynput.mouse import Button
from typing import Any
from pynput.keyboard import Key
from model.voice_commands import decode_voice_sequence
from utils import (
    get_window_root_hwnd,
    get_window_screen_rect,
    is_any_window_foreground,
    move_pointer_to_monitor_bottom,
)

logger = logging.getLogger(__name__)

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
        controller._voice_sequence_steps = []
        controller._voice_sequence_running = False

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

    def _enqueue_input_action(self, action: str, event_time: float | None = None) -> None:
            if self._shutting_down:
                return
            timestamp = time.perf_counter() if event_time is None else float(event_time)
            self._input_actions.put_nowait((action, timestamp))

    def enqueue_action(
        self,
        action: str,
        event_time: float | None = None,
        *,
        source: str = "external",
        repeat_count: int = 1,
    ) -> None:
            """Thread-safe public entry point for semantic input actions."""
            del source
            try:
                count = max(1, min(20, int(repeat_count)))
            except Exception:
                count = 1
            if count == 1:
                self._enqueue_input_action(str(action), event_time=event_time)
                return
            if self._shutting_down:
                return
            timestamp = time.perf_counter() if event_time is None else float(event_time)
            self._input_actions.put_nowait((str(action), timestamp, count))

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
                self._enqueue_input_action(step_name)
            else:
                self._enqueue_input_action(action)

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
                    if self._is_seek_repeat_action(action):
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
                            self._enqueue_input_action("apply_pending_seek")
                        else:
                            unresolved = [a for a in released_seek_actions if not released_seek_fired.get(a, False)]
                            if "go_forward" in unresolved and "go_back" not in unresolved:
                                self._enqueue_input_action(
                                    "go_forward",
                                    event_time=released_seek_press_time.get("go_forward"),
                                )
                            elif "go_back" in unresolved and "go_forward" not in unresolved:
                                self._enqueue_input_action(
                                    "go_back",
                                    event_time=released_seek_press_time.get("go_back"),
                                )
                    else:
                        if abs(pending) >= 0.000001:
                            self._enqueue_input_action("clear_pending_seek")

    def _process_repeat_actions(self):
            if self._shutting_down:
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
                    if isinstance(item, tuple) and len(item) >= 3:
                        action, event_time, repeat_count = item[:3]
                    elif isinstance(item, tuple):
                        action, event_time = item
                    else:
                        action, event_time = item, None
                    self._dispatch_input_action(action, event_time=event_time, repeat_count=repeat_count)
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
    ) -> None:
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
            elif action == "voice_cycle_mode":
                self.controller.handle_voice_input_mode(None)
            elif action == "voice_mode_1":
                self.controller.handle_voice_input_mode(1)
            elif action == "voice_mode_2":
                self.controller.handle_voice_input_mode(2)
            elif action == "voice_mode_3":
                self.controller.handle_voice_input_mode(3)
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
                    if accepted and bool(self.config.get("POST_ADD_CAPTURE_MOVE_MOUSE_TO_BOTTOM") or False):
                        move_pointer_to_monitor_bottom(getattr(self.settings, "root", None))
                except Exception as e:
                    logger.exception("Failed to add current selection or hovered subtitle word with capture: %s", e)
            elif action == "voice_add_anki":
                if self._voice_anki_action_in_flight():
                    self.controller._report_voice_status(
                        "listening",
                        "Ignored the voice command because an Anki add is already running.",
                    )
                    return
                self.controller._voice_anki_action_queued = True
                try:
                    accepted = self._add_current_anki_hotkey_target(post_add_capture=False)
                    if not accepted:
                        self.controller._report_voice_status(
                            "listening",
                            "No popup selection or hovered subtitle word is available to add.",
                        )
                except Exception as exc:
                    logger.exception("Voice Anki add failed")
                    self.controller._report_voice_status("error", f"Voice Anki add failed: {exc}")
                finally:
                    if not bool(getattr(self.controller, "_anki_add_busy", False)):
                        self.controller._voice_anki_action_queued = False
            elif action == "voice_add_anki_capture":
                if self._voice_anki_action_in_flight():
                    self.controller._report_voice_status(
                        "listening",
                        "Ignored the voice command because an Anki add is already running.",
                    )
                    return
                self.controller._voice_anki_action_queued = True
                try:
                    accepted = self._add_current_anki_hotkey_target(post_add_capture=True)
                    if accepted and bool(self.config.get("POST_ADD_CAPTURE_MOVE_MOUSE_TO_BOTTOM") or False):
                        move_pointer_to_monitor_bottom(getattr(self.settings, "root", None))
                    if not accepted:
                        self.controller._report_voice_status(
                            "listening",
                            "No popup selection or hovered subtitle word is available to add with capture.",
                        )
                except Exception as exc:
                    logger.exception("Voice Anki add with capture failed")
                    self.controller._report_voice_status("error", f"Voice Anki add with capture failed: {exc}")
                finally:
                    if not bool(getattr(self.controller, "_anki_add_busy", False)):
                        self.controller._voice_anki_action_queued = False
            elif str(action).startswith("voice_add_anki_capture_index:"):
                try:
                    position = int(str(action).split(":", 1)[1])
                except Exception:
                    return
                self._add_indexed_subtitle_word_with_capture(position)
            elif action == "voice_capture_only":
                capture = getattr(self.controller.anki_controller, "trigger_external_capture_only", None)
                captured = bool(capture()) if callable(capture) else False
                if captured:
                    self.controller._report_voice_status(
                        "listening",
                        "Capture hotkey sent without creating an Anki card.",
                    )
                else:
                    self.controller._report_voice_status(
                        "error",
                        "Capture-only failed because the configured capture window or hotkey was unavailable.",
                    )
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
                    if accepted and bool(self.config.get("POST_ADD_CAPTURE_MOVE_MOUSE_TO_BOTTOM") or False):
                        move_pointer_to_monitor_bottom(getattr(self.settings, "root", None))
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
            elif action == "voice_copy_selection":
                renderer = getattr(self, "renderer", None)
                copy_hover = getattr(renderer, "copy_hover_selection_to_clipboard_if_pointer_inside", None)
                copy_selection = getattr(self.popup, "copy_selection_to_clipboard_if_pointer_inside", None)
                copied = False
                try:
                    if callable(copy_hover):
                        copied = bool(copy_hover())
                    if not copied and callable(copy_selection):
                        copied = bool(copy_selection())
                except Exception:
                    logger.debug("Failed to copy from voice command", exc_info=True)
                if not copied:
                    self.controller._report_voice_status("listening", "No selectable popup or hover text is available to copy.")
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

    def _voice_anki_action_in_flight(self) -> bool:
            if bool(getattr(self.controller, "_anki_add_busy", False)) or bool(
                getattr(self.controller, "_voice_anki_action_queued", False)
            ):
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
            self._dispatch_input_action(action, repeat_count=repeat_count)
            _complete(True)

    def _add_indexed_subtitle_word_with_capture(self, position: int) -> bool:
            if self._voice_anki_action_in_flight():
                self.controller._report_voice_status(
                    "listening",
                    "Ignored indexed capture because an Anki add is already running.",
                )
                return False
            getter = getattr(self.renderer, "subtitle_words_for_anki", None)
            words = list(getter() or []) if callable(getter) else []
            word_count = len(words)
            index = int(position) - 1 if int(position) > 0 else word_count + int(position)
            if word_count <= 0:
                self.controller._report_voice_status(
                    "listening",
                    "Indexed capture failed because the current subtitle has no selectable words.",
                )
                return False
            if index < 0 or index >= word_count:
                requested = (
                    str(position)
                    if position > 0
                    else ("last" if position == -1 else f"last {abs(position)}")
                )
                self.controller._report_voice_status(
                    "listening",
                    f"Capture word {requested} is unavailable; the current subtitle has {word_count} selectable words.",
                )
                return False

            selected = str(words[index] or "").strip()
            subtitle_getter = getattr(self.subtitle_navigation, "copy_text_for_current_subtitle", None)
            subtitle_text = str(subtitle_getter() or "") if callable(subtitle_getter) else ""
            if not selected or not subtitle_text:
                self.controller._report_voice_status(
                    "listening",
                    "Indexed capture failed because the current subtitle is no longer available.",
                )
                return False

            self.controller._voice_anki_action_queued = True
            try:
                self.controller._add_selection_to_anki(
                    selected_text=selected,
                    subtitle_text=subtitle_text,
                    post_add_capture=True,
                )
                if bool(self.config.get("POST_ADD_CAPTURE_MOVE_MOUSE_TO_BOTTOM") or False):
                    move_pointer_to_monitor_bottom(getattr(self.settings, "root", None))
                self.controller._report_voice_status(
                    "working",
                    f"Capturing subtitle word {index + 1} of {word_count}: {selected}",
                )
                return True
            except Exception as exc:
                logger.exception("Indexed voice capture failed")
                self.controller._report_voice_status("error", f"Indexed capture failed: {exc}")
                return False
            finally:
                if not bool(getattr(self.controller, "_anki_add_busy", False)):
                    self.controller._voice_anki_action_queued = False

    def _add_current_anki_hotkey_target(
        self,
        *,
        post_add_capture: bool,
        subtitle_only: bool = False,
    ) -> bool:
            renderer = getattr(self, "renderer", None)
            hovered_word_getter = getattr(renderer, "hovered_subtitle_word_for_anki", None)
            hovered_word = ""
            if callable(hovered_word_getter):
                hovered_word = str(hovered_word_getter() or "").strip()
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
                )
                return True

            if subtitle_only:
                return False
            add_selected = getattr(self.popup, "add_selected_to_anki_if_pointer_inside", None)
            if not callable(add_selected):
                return False
            if post_add_capture:
                return bool(add_selected(post_add_capture=True))
            return bool(add_selected())

    def _skip_buttons_use_subtitle_segments(self) -> bool:
            return bool(self.config.get("SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS") or False)

    def _hotkey_action_disabled(self, action: str) -> bool:
            key = self.HOTKEY_DISABLE_KEYS.get(str(action or "").strip())
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
            default = self.SHORTCUT_DEFAULTS.get(config_key, "")
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

    def _is_suppressed_synthetic_key(self, key) -> bool:
            now = time.perf_counter()
            key_tokens = self._key_tokens(key)
            deadlines = getattr(self.controller, "_suppress_synthetic_tokens_until", None)
            if isinstance(deadlines, dict):
                for token, deadline in list(deadlines.items()):
                    try:
                        deadline = float(deadline or 0.0)
                    except Exception:
                        deadline = 0.0
                    if deadline < now:
                        deadlines.pop(token, None)
                        continue
                    if self._normalize_shortcut_token(token) in key_tokens:
                        return True
            try:
                until = float(getattr(self.controller, "_suppress_synthetic_space_until", 0.0) or 0.0)
            except Exception:
                until = 0.0
            if until <= 0.0 or now > until:
                return False
            return "space" in key_tokens

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
            self._enqueue_input_action("toggle_subtitles")

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
            self._enqueue_input_action("popup_copy_selection")
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
            if self._is_suppressed_synthetic_key(key):
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
                            self._request_popup_translation_display_refresh()
                        return
                    self.translation_pressed = True
                    self.translation_provider = provider
                    self._active_translation_action = action
                    self._active_translation_binding = binding
                    self._request_popup_translation_display_refresh()
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
                    self._enqueue_input_action(action)
                    return

                return

            old_hover_mode = self._current_hover_mode_for_refresh()
            self._set_pressed_key_tokens(key, True)

            modifier_tokens = self._modifier_tokens_for_key(key)
            if modifier_tokens:
                if self._set_modifier_tokens(modifier_tokens, True):
                    self._refresh_hover_displays_if_mode_changed(old_hover_mode)
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
                        self._request_popup_translation_display_refresh()
                    return
                self.translation_pressed = True
                self.translation_provider = provider
                self._active_translation_action = action
                self._active_translation_binding = binding
                self._request_popup_translation_display_refresh()
                return
            if kind == "popup_add":
                if action in self._single_fire_actions:
                    return
                self._single_fire_actions.add(action)
                self._enqueue_input_action(action)
                return
            if bool(candidate.get("single_fire")):
                if action in self._single_fire_actions:
                    return
                self._single_fire_actions.add(action)
                self._enqueue_input_action(action)
                return
            if not self._hold_repeat_action(action):
                return
            if not self._is_seek_repeat_action(action):
                self._queue_repeat_step(action)
                return

    def _on_key_release(self, key):
            if self._is_suppressed_synthetic_key(key):
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
                    self._request_popup_translation_display_refresh()
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
                self._request_popup_translation_display_refresh()
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

    def _request_popup_translation_display_refresh(self) -> None:
            self._enqueue_input_action("_refresh_popup_translation")

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
            self._reset_transient_hotkey_state_for_external_click(x, y, button)
            if self._is_text_input_focused():
                self._reset_hotkey_state(reset_shift=True)
                return
            if button == Button.x1:
                if not self._hotkeys_disabled() and not self._hotkey_action_disabled("popup_add_anki_capture"):
                    self._enqueue_input_action("subtitle_hover_add_anki_capture")
            elif button == Button.x2:
                if self._point_inside_subtitle_window(x, y):
                    if not self._hotkeys_disabled():
                        self._enqueue_input_action("subtitle_hover_add_anki")
                elif not self._hotkey_action_disabled("clear_subtitle"):
                    self._enqueue_input_action("clear_subtitle")
            elif button == Button.middle:
                self._enqueue_input_action("toggle_m3_mode")
