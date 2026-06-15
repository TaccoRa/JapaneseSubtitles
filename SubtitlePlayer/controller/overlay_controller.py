"""Overlay/window visibility helper for control-window and subtitle-handle behavior."""

from typing import Any

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
        if getattr(self, "_con_hide_job", None): self.settings.control_window.after_cancel(self._con_hide_job)
        self._con_hide_job = self.settings.control_window.after(ms,self.settings.control_window.lower)

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
        self.overlay.sub_window.attributes("-transparentcolor", "")
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        self.popup.ensure_on_top()
        if getattr(self, "_con_hide_job", None) is not None:
            self.settings.control_window.after_cancel(self._con_hide_job)
            self._con_hide_job = None
        self.subtitle_deleted = False

    def sub_window_leave(self, event):
        if not self._windows_alive():
            return
        self.overlay.sub_window.attributes("-transparentcolor", "grey")
        if not self.settings.default_phone_mode:
            self._hide_controls_after(self.windows_hide_control_ms)

    def sub_handel_enter(self, event):
        if not self._windows_alive():
            return
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        self.popup.ensure_on_top()
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job)
            self._con_hide_job = None
        self.subtitle_deleted = False

    def control_window_enter(self, event):
        if not self._windows_alive():
            return
        self.settings.control_window.attributes("-topmost", True)
        self.overlay.sub_window.attributes("-topmost", True)
        self.popup.ensure_on_top()
        if getattr(self, "_con_hide_job", None):
            self.settings.control_window.after_cancel(self._con_hide_job)
            self._con_hide_job = None
        self.subtitle_deleted = False

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
        if is_phone:
            self.overlay.show_handle()
        else:
            self.overlay.hide_handle()

    def _hide_subtitle_handle_for_settings(self):
        if self.settings.default_phone_mode:
            self.overlay.hide_handle()

    def _restore_subtitle_handle_after_settings(self):
        if self.settings.default_phone_mode:
            self.overlay.show_handle()
