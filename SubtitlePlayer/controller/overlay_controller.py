"""Overlay/window visibility helper for control-window and subtitle-handle behavior."""

import logging
import time
from typing import Any

from pynput.keyboard import Controller as KeyboardController, Key

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

class OverlayController(_ControllerProxy):
    """Overlay/window visibility helper for control-window and subtitle-handle behavior."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def _schedule_hide_controls(self):
            if self.settings.default_phone_mode:
                self._hide_controls_after(self.phone_windows_hide_control_ms)

    def _hide_controls_after(self, ms: int):
        if self._shutting_down:
            return
        if not self._windows_alive():
            return
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job)
        self._con_hide_job = self.settings.control_window.after(ms, self._hide_controls_if_pointer_outside)

    def _hide_controls_if_pointer_outside(self):
        self._con_hide_job = None
        if not self._windows_alive():
            return
        if self._pointer_inside_window(self.settings.control_window) or self._pointer_inside_window(self.overlay.sub_window):
            return
        if bool(getattr(self, "control_show_on_subtitle_hover", True)):
            self.settings.control_window.lower()
        else:
            self.settings.control_window.withdraw()

    @staticmethod
    def _pointer_inside_window(win) -> bool:
        try:
            x = int(win.winfo_pointerx())
            y = int(win.winfo_pointery())
            left = int(win.winfo_rootx())
            top = int(win.winfo_rooty())
            right = left + int(win.winfo_width())
            bottom = top + int(win.winfo_height())
            return left <= x < right and top <= y < bottom
        except Exception:
            return False

    def _windows_alive(self) -> bool:
        if self._shutting_down:
            return False
        cw = getattr(self.settings, "control_window", None)
        sw = getattr(self.overlay, "sub_window", None)
        return (
            cw is not None
            and sw is not None
            and cw.winfo_exists()
            and sw.winfo_exists()
        )

    def sub_window_enter(self, event):
        if not self._windows_alive():
            return
        if getattr(self, "subtitles_user_hidden", False):
            return
        self._trigger_background_video_space(paused=True)
        self.overlay.sub_window.attributes("-transparentcolor", "")
        if bool(getattr(self, "control_show_on_subtitle_hover", True)):
            self.settings.control_window.deiconify()
            self.settings.control_window.lift()
            self.settings.control_window.attributes("-topmost", True)
            if getattr(self, "_con_hide_job", None) is not None:
                self.settings.control_window.after_cancel(self._con_hide_job)
                self._con_hide_job = None
        self.overlay.sub_window.attributes("-topmost", True)
        self.popup.ensure_on_top()
        ensure_hover_on_top = getattr(self.renderer, "ensure_hover_text_on_top", None)
        if callable(ensure_hover_on_top):
            ensure_hover_on_top()

    def sub_window_leave(self, event):
        if not self._windows_alive():
            return
        if getattr(self, "subtitles_user_hidden", False):
            return
        self._trigger_background_video_space(paused=False)
        self.overlay.sub_window.attributes("-transparentcolor", "grey")
        if not self.settings.default_phone_mode:
            self._hide_controls_after(self.windows_hide_control_ms)

    def _trigger_background_video_space(self, *, paused: bool) -> None:
        if not bool(getattr(self, "subtitle_hover_pause_video", False)):
            if not paused:
                self._hover_video_pause_active = False
                self._hover_timer_pause_active = False
            return
        now = time.perf_counter()
        if paused:
            if bool(getattr(self, "_hover_video_pause_active", False)):
                return
            self._hover_video_pause_active = True
        else:
            if not bool(getattr(self, "_hover_video_pause_active", False)):
                return
            self._hover_video_pause_active = False
        self._sync_timer_for_hover_pause(paused=paused, event_time=now)
        try:
            self._suppress_synthetic_space_until = now + 0.45
            keyboard = getattr(self, "_hover_pause_keyboard", None)
            if keyboard is None:
                keyboard = KeyboardController()
                self._hover_pause_keyboard = keyboard
            keyboard.press(Key.space)
            keyboard.release(Key.space)
        except Exception:
            logger.warning("Failed to send subtitle-hover spacebar toggle", exc_info=True)

    def _sync_timer_for_hover_pause(self, *, paused: bool, event_time: float | None = None) -> None:
        try:
            if paused:
                if bool(getattr(self, "playing", False)):
                    self.playback.toggle_play(event_time=event_time)
                    self._hover_timer_pause_active = True
                else:
                    self._hover_timer_pause_active = False
                return

            if bool(getattr(self, "_hover_timer_pause_active", False)):
                if not bool(getattr(self, "playing", False)):
                    self.playback.toggle_play(event_time=event_time)
                self._hover_timer_pause_active = False
        except Exception:
            logger.warning("Failed to sync app timer with subtitle-hover pause", exc_info=True)

    def sub_handle_enter(self, event):
        if not self._windows_alive():
            return
        if getattr(self, "subtitles_user_hidden", False):
            return
        if not bool(getattr(self, "control_show_on_subtitle_hover", True)):
            return
        self.settings.control_window.deiconify()
        self.settings.control_window.lift()
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        self.popup.ensure_on_top()
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job)
            self._con_hide_job = None

    def control_window_enter(self, event):
        if not self._windows_alive():
            return
        if getattr(self, "subtitles_user_hidden", False):
            return
        self.settings.control_window.deiconify()
        self.settings.control_window.lift()
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        self.popup.ensure_on_top()
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job)
            self._con_hide_job = None

    def control_window_leave(self, event):
        if self._shutting_down:
            return
        delay = (
            self.phone_windows_hide_control_ms
            if self.settings.default_phone_mode
            else self.windows_hide_control_ms
        )
        self._hide_controls_after(delay)

    def show_subtitle_handle(self, is_phone):
        setter = getattr(self.overlay, "set_handle_enabled", None)
        enabled = bool(is_phone) and bool(getattr(self, "phone_subtitle_handle_enabled", True))
        if enabled:
            try:
                if self.controller._pointer_inside_settings_windows():
                    self.overlay.hide_handle()
                    return
            except Exception:
                pass
            if callable(setter):
                self.overlay.set_handle_enabled(True)
            else:
                self.overlay.show_handle()
        else:
            if callable(setter):
                self.overlay.set_handle_enabled(False)
            else:
                self.overlay.hide_handle()

    def _hide_subtitle_handle_for_settings(self):
        if self.settings.default_phone_mode:
            self.overlay.hide_handle()

    def _restore_subtitle_handle_after_settings(self):
        if self.settings.default_phone_mode and bool(getattr(self, "phone_subtitle_handle_enabled", True)):
            try:
                if self.controller._pointer_inside_settings_windows():
                    self.overlay.hide_handle()
                    return
            except Exception:
                pass
            setter = getattr(self.overlay, "set_handle_enabled", None)
            if callable(setter):
                self.overlay.set_handle_enabled(True)
            else:
                self.overlay.show_handle()
