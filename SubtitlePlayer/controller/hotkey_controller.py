"""Keyboard shortcut, repeat-action, and global hotkey helper."""

import time
import queue
from pynput.mouse import Button
from typing import Any
from pynput.keyboard import Key

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

    def _enqueue_input_action(self, action: str) -> None:
            if self._shutting_down:
                return
            self._input_actions.put_nowait(action)

    def _drop_pending_input_actions(self, actions_to_remove: set[str]) -> None:
            if not actions_to_remove:
                return
            kept = []
            while True:
                try:
                    action = self._input_actions.get_nowait()
                except queue.Empty:#
                    break
                except Exception:#
                    break
                if action not in actions_to_remove:
                    kept.append(action)
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

    def _accumulate_pending_seek(self, action: str) -> None:
            delta = self._seek_delta_for_action(action)
            if abs(delta) < 0.000001:
                return
            try:
                self._pending_seek_delta = float(self._pending_seek_delta) + delta
            except Exception:#
                print("I dont know man :)")
                self._pending_seek_delta = delta
            self.update_time_and_subtitle_displays()

    def _clear_pending_seek_preview(self) -> None:
            if abs(float(getattr(self, "_pending_seek_delta", 0.0) or 0.0)) < 0.000001:
                return
            self._pending_seek_delta = 0.0
            self.update_time_and_subtitle_displays()

    def _apply_pending_seek(self) -> None:
            delta = float(self._pending_seek_delta or 0.0)
            if abs(delta) < 0.000001:
                return
            self._pending_seek_delta = 0.0
            self.playback.set_current_time(self.current_time + delta)
            self._schedule_hide_controls()

    def _hold_repeat_action(self, action: str) -> bool:
            now = time.perf_counter()
            with self._repeat_lock:
                if action in self._held_repeat_next_fire:
                    return False
                self._held_repeat_next_fire[action] = now + self._repeat_initial_delay_sec
                self._held_repeat_fired.discard(action)
            return True

    def _release_repeat_actions(self, actions: set[str], settle_seek: bool = True) -> None:
            if not actions:
                return
            released_seek_actions = set()
            released_seek_fired = {}
            with self._repeat_lock:
                for action in actions:
                    if self._is_seek_repeat_action(action):
                        released_seek_actions.add(action)
                        released_seek_fired[action] = (action in self._held_repeat_fired)
                    self._held_repeat_next_fire.pop(action, None)
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
                                self._enqueue_input_action("go_forward")
                            elif "go_back" in unresolved and "go_forward" not in unresolved:
                                self._enqueue_input_action("go_back")
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
                print(e)
                self._repeat_job = None

    def _process_input_queue(self):
            if self._shutting_down:
                return
            try:
                for _ in range(50):
                    try:
                        action = self._input_actions.get_nowait()
                    except queue.Empty:#
                        break
                    self._dispatch_input_action(action)
            finally:
                try:
                    self._input_pump_job = self.settings.root.after(15, self._process_input_queue)
                except Exception as e:
                    print(e)
                    self._input_pump_job = None

    def _dispatch_input_action(self, action: str) -> None:
            if action == "toggle_play":
                self.playback.toggle_play()
            elif action == "go_back":
                self.playback.go_back()
            elif action == "go_forward":
                self.playback.go_forward()
            elif action == "subtitle_back":
                self.playback.jump_subtitle_segment("prev")
            elif action == "subtitle_forward":
                self.playback.jump_subtitle_segment("next")
            elif action == "jump_sub_end":
                self.playback.on_jump_sub_end()
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
            elif action == "toggle_m3_mode":
                self._toggle_m3_mode()
            elif action == "_seek_step_back":
                self._accumulate_pending_seek("go_back")
            elif action == "_seek_step_forward":
                self._accumulate_pending_seek("go_forward")
            elif action == "apply_pending_seek":
                self._apply_pending_seek()
            elif action == "clear_pending_seek":
                self._clear_pending_seek_preview()

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
                print(e)
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
                if bool(self.config.get("DISABLE_SPACE_HOTKEY") or False):
                    if self._normalize_shortcut_token(value) == "space":
                        return ""
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
    
    @staticmethod
    def _is_text_input_widget(widget) -> bool:
            if widget is None:
                return False
            cls = str(widget.winfo_class() or "").lower()
            return cls in {
                "entry",
                "tentry",
                "ttk::entry",
                "text",
                "combobox",
                "tcombobox",
                "ttk::combobox",
                "spinbox",
                "ttk::spinbox",
            }

    def _is_text_input_focused(self) -> bool:
            windows = [
                getattr(self.settings, "root", None),
                getattr(self.settings, "control_window", None),
                getattr(self.settings, "advanced_window", None),
                getattr(self.popup, "_popup", None),
            ]
            for owner in windows:
                if owner is None:
                    continue
                try:
                    widget = owner.focus_get()
                except Exception as e:
                    print(e)
                    widget = None
                # Treat control time entry as text-focused only while actively editing.
                if widget is getattr(self.settings, "time_entry", None) and not bool(self.entry_editing):
                    continue
                if self._is_text_input_widget(widget):
                    return True
            return False

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

    def _repeat_action_bindings(self):
            try:
                mode2_numpad = int(getattr(self.settings, "input_mode", 1)) == 2
            except Exception as e:
                print(e)
                mode2_numpad = bool(getattr(self.settings, "numpad_mode_enabled", False))
            if mode2_numpad:
                bindings = [
                    ("subtitle_back", self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_BACK")),
                    ("subtitle_forward", self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_FORWARD")),
                    ("go_back", self._get_shortcut_value("SHORTCUT_MODE2_GO_BACK")),
                    ("go_forward", self._get_shortcut_value("SHORTCUT_MODE2_GO_FORWARD")),
                ]
            else:
                bindings = [
                    ("subtitle_back", self._get_shortcut_value("SHORTCUT_SUBTITLE_BACK")),
                    ("subtitle_forward", self._get_shortcut_value("SHORTCUT_SUBTITLE_FORWARD")),
                    ("go_back", self._get_shortcut_value("SHORTCUT_GO_BACK")),
                    ("go_forward", self._get_shortcut_value("SHORTCUT_GO_FORWARD")),
                ]
            return [(action, binding) for action, binding in bindings if not self._hotkey_action_disabled(action)]

    def _single_fire_bindings(self):
            bindings = [
                ("toggle_play", self._get_shortcut_value("SHORTCUT_TOGGLE_PLAY")),
                ("toggle_play", self._get_shortcut_value("SHORTCUT_MODE2_TOGGLE_PLAY")),
                ("alt_x", self._get_shortcut_value("SHORTCUT_BRING_TO_FRONT")),
                ("episode_inc", self._get_shortcut_value("SHORTCUT_EPISODE_INC")),
                ("episode_dec", self._get_shortcut_value("SHORTCUT_EPISODE_DEC")),
                ("jump_sub_end", self._get_shortcut_value("SHORTCUT_JUMP_SUB_END")),
            ]
            return [(action, binding) for action, binding in bindings if not self._hotkey_action_disabled(action)]

    def _hotkeys_disabled(self) -> bool:
            if bool(getattr(self.sub_manager, "is_search_dialog_active", lambda: False)()):
                return True
            if int(getattr(self.settings, "input_mode", 1)) == 3:
                return True
            return bool(self.config.get("SHORTCUTS_DISABLED") or False)

    def _reset_hotkey_state(self) -> None:
            self.shift_pressed = False
            self.alt_pressed = False
            self.ctrl_pressed = False
            self._single_fire_actions.clear()
            repeat_actions = {action for action, _ in self._repeat_action_bindings()}
            self._release_repeat_actions(repeat_actions, settle_seek=False)

    def _toggle_m3_mode(self) -> None:
            enable_m3 = not self._hotkeys_disabled()
            try:
                self.settings.set_hotkeys_disabled(enable_m3)
            except Exception as e:
                print(e)
                return
            # Clear any stuck modifier state when toggling hotkeys on/off.
            self._reset_hotkey_state()

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

            if self._is_text_input_focused():
                self._reset_hotkey_state()
                return

            mapping = []
            mapping.extend((binding, action, True) for action, binding in self._single_fire_bindings())
            mapping.extend((binding, action, False) for action, binding in self._repeat_action_bindings())

            for binding, action, single_fire in mapping:
                if not self._shortcut_matches(binding, key):
                    continue
                if single_fire and action in self._single_fire_actions:
                    return
                if single_fire:
                    self._single_fire_actions.add(action)
                    self._enqueue_input_action(action)
                    return
                if not self._hold_repeat_action(action):
                    return
                if not self._is_seek_repeat_action(action):
                    self._queue_repeat_step(action)
                return

    def _on_key_release(self, key):
            if self._hotkeys_disabled():
                self._reset_hotkey_state()
                return

            if self._is_text_input_focused():
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

            repeat_actions_to_drop = set()
            for action, binding in self._repeat_action_bindings():
                _, key_token = self._split_shortcut(binding)
                if key_token and key_token in released_tokens:
                    repeat_actions_to_drop.add(action)
            if key in (Key.shift_l, Key.shift_r, Key.alt_l, Key.alt_r, Key.ctrl_l, Key.ctrl_r):
                repeat_actions_to_drop.update(action for action, _ in self._repeat_action_bindings())
            self._release_repeat_actions(repeat_actions_to_drop, settle_seek=True)

    def on_alt_x(self, event=None):
            self.settings.control_window.attributes("-topmost", True)
            self.popup.ensure_on_top()

    def _on_global_click(self, x, y, button, pressed):
            if button == Button.x2 and pressed:
                self._enqueue_input_action("clear_subtitle")
            if button == Button.x1 and pressed:
                self._enqueue_input_action("toggle_m3_mode")