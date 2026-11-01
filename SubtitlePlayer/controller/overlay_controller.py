"""Overlay/window visibility helper for control-window and subtitle-handle behavior."""

import logging
import time
from typing import Any


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
            if win is None or not win.winfo_exists() or not win.winfo_viewable():
                return False
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
        self._enter_hover_domain()
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

    def _pointer_inside_hover_domain(self):
        return bool(getattr(self.popup, "_menu_open", False)) or any(self._pointer_inside_window(win) for win in (
            self.overlay.sub_window,
            getattr(self.popup, "_popup", None),
            getattr(self.renderer, "_hover_text_window", None),
            getattr(self.popup, "_hover_ruby_window", None),
            getattr(self.popup, "_hover_layer_window", None),
        ))

    def hover_popup_presence(self, inside):
        if not self._windows_alive():
            return
        if inside and getattr(self, "subtitles_user_hidden", False):
            return
        if inside:
            self._enter_hover_domain()
        else:
            self._schedule_hover_playback()

    def _enter_hover_domain(self):
        was_inside = bool(getattr(self, "_hover_pointer_inside", False))
        self._hover_pointer_inside = True
        job = getattr(self, "_hover_playback_job", None)
        if job is not None:
            self.settings.root.after_cancel(job)
            self._hover_playback_job = None
        if not was_inside:
            self._trigger_background_video_space(paused=True)
        self._watch_hover_domain()

    def _watch_hover_domain(self):
        if not getattr(self, "subtitle_hover_pause_video", False) and not getattr(self, "_hover_pause_lease", None):
            return
        if getattr(self, "_hover_domain_job", None) is not None:
            return
        def check():
            self._hover_domain_job = None
            if not self._windows_alive():
                return
            if self._pointer_inside_hover_domain():
                self._watch_hover_domain()
            else:
                # Separate tooltip windows can consume the subtitle's Leave.
                self._schedule_hover_playback()
        self._hover_domain_job = self.settings.root.after(75, check)

    def sub_window_leave(self, event):
        if not self._windows_alive():
            return
        if getattr(self, "subtitles_user_hidden", False):
            return
        self._schedule_hover_playback()
        self.overlay.sub_window.attributes("-transparentcolor", "grey")
        if not self.settings.default_phone_mode:
            self._hide_controls_after(self.windows_hide_control_ms)

    def _schedule_hover_playback(self):
        job = getattr(self, "_hover_playback_job", None)
        if job is not None:
            self.settings.root.after_cancel(job)
        def apply():
            self._hover_playback_job = None
            # Child Enter/Leave events do not mean the pointer left the overlay.
            self._hover_pointer_inside = self._pointer_inside_hover_domain()
            self._trigger_background_video_space(paused=self._hover_pointer_inside)
            if self._hover_pointer_inside:
                self._watch_hover_domain()
        self._hover_playback_job = self.settings.root.after(40, apply)

    def cancel_hover_resume(self):
        self._hover_pause_epoch = getattr(self, "_hover_pause_epoch", 0) + 1
        self._hover_pause_lease = None
        self._hover_video_pause_active = False

    def _trigger_background_video_space(self, *, paused):
        """One playback owner for both recorders, with an acknowledged pause lease."""
        browser = getattr(self.controller, "browser_sync", None)
        connected = bool(browser and browser.following and getattr(browser, "_connected", False) and browser.identity)
        lease = getattr(self, "_hover_pause_lease", None)
        if not bool(getattr(self, "subtitle_hover_pause_video", False)):
            self.cancel_hover_resume()
            return
        if lease and lease["backend"] == "browser" and (not connected or browser.identity != lease["identity"] or
                not browser.snapshot or browser.snapshot.fingerprint != lease["fingerprint"]):
            self.cancel_hover_resume()
            lease = None
        if lease and lease["backend"] == "legacy" and connected:
            self.cancel_hover_resume()
            lease = None
        if browser and getattr(browser, "_capture", None):
            self.cancel_hover_resume()
            return
        if paused:
            if lease is not None:
                lease["inside"] = True
                lease.pop("resume_deadline", None)
                return
            if connected:
                if not browser.fresh or not browser.snapshot:
                    epoch = getattr(self, "_hover_pause_epoch", 0)
                    if getattr(self, "_hover_probe_job", None) is None or getattr(self, "_hover_probe_epoch", None) != epoch:
                        self._hover_probe_epoch = epoch
                        browser.bridge.send(dict(type="PlaybackProbe", identity=browser.identity.wire()))
                        def retry():
                            if getattr(self, "_hover_probe_epoch", None) != epoch:
                                return
                            self._hover_probe_job = None
                            if (getattr(self, "_hover_pointer_inside", False) and self._pointer_inside_hover_domain()
                                    and epoch == getattr(self, "_hover_pause_epoch", 0)):
                                self._trigger_background_video_space(paused=True)
                        self._hover_probe_job = self.settings.root.after(150, retry)
                    return
                if browser.snapshot.paused:
                    return
                lease = dict(backend="browser", identity=browser.identity, fingerprint=browser.snapshot.fingerprint,
                             inside=True, pending=True, generation=None)
                self._hover_pause_lease = lease
                self._hover_video_pause_active = True
                def acknowledged(ok):
                    if getattr(self, "_hover_pause_lease", None) is not lease:
                        return
                    lease["pending"] = False
                    if not ok:
                        self.cancel_hover_resume()
                    elif not lease["inside"]:
                        self._resume_hover_playback(lease)
                try:
                    browser.command("pause", on_complete=acknowledged)
                    lease["generation"] = browser._control_generation
                except (ConnectionError, ValueError):
                    self.cancel_hover_resume()
                return
            if self.config.get("CAPTURE_BACKEND") == "asbplayer":
                from model.asbplayer_legacy import LegacyAsbplayerCapture
                try:
                    window = LegacyAsbplayerCapture(self.config).toggle_hover_playback(getattr(self.controller, "hotkey_controller", None))
                    self._hover_pause_lease = dict(backend="legacy", window=window, inside=True, pending=False)
                    self._hover_video_pause_active = True
                except ValueError as exc:
                    logger.warning("%s", exc)
        elif lease is not None:
            lease["inside"] = False
            if not lease["pending"]:
                self._resume_hover_playback(lease)

    def _resume_hover_playback(self, lease):
        if getattr(self, "_hover_pause_lease", None) is not lease:
            return
        browser = getattr(self.controller, "browser_sync", None)
        if lease["backend"] == "browser":
            if (browser and browser.identity == lease["identity"] and browser.snapshot
                    and browser.snapshot.fingerprint == lease["fingerprint"]
                    and browser._control_generation == lease["generation"] and not browser._capture
                    and getattr(browser, "_connected", False) and not browser.fresh):
                deadline = lease.setdefault("resume_deadline", time.monotonic() + 2)
                if time.monotonic() < deadline:
                    if not lease.get("resume_pending"):
                        lease["resume_pending"] = True
                        try:
                            browser.bridge.send(dict(type="PlaybackProbe", identity=browser.identity.wire()))
                        except ConnectionError:
                            self.cancel_hover_resume()
                            return
                        def retry():
                            lease["resume_pending"] = False
                            if (self._windows_alive() and getattr(self, "_hover_pause_lease", None) is lease
                                    and not lease["inside"] and not self._pointer_inside_hover_domain()):
                                self._resume_hover_playback(lease)
                        self.settings.root.after(150, retry)
                    return
            self.cancel_hover_resume()
            if (browser and browser.fresh and browser.identity == lease["identity"] and browser.snapshot
                    and browser.snapshot.fingerprint == lease["fingerprint"]
                    and browser._control_generation == lease["generation"] and not browser._capture and browser.snapshot.paused):
                try:
                    browser.command("play")
                except ConnectionError:
                    pass
        elif self.config.get("CAPTURE_BACKEND") == "asbplayer" and not (browser and getattr(browser, "_connected", False)):
            self.cancel_hover_resume()
            from model.asbplayer_legacy import LegacyAsbplayerCapture
            try:
                LegacyAsbplayerCapture(self.config).toggle_hover_playback(getattr(self.controller, "hotkey_controller", None), lease["window"])
            except ValueError as exc:
                logger.warning("%s", exc)
        else:
            self.cancel_hover_resume()

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
