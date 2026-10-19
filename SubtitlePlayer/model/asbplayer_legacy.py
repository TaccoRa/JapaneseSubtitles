"""The original asbplayer recording shortcut and window target.

This adapter intentionally needs neither the companion's native recording grant
nor an asbplayer WebSocket/proxy. Normal browser playback keeps its own gateway.
"""
from utils import focus_window_by_title, send_global_hotkey
import time

LEGACY_SETTINGS = {
    "ASBPLAYER_CAPTURE_HOTKEY": ("POST_ADD_CAPTURE_EXTERNAL_HOTKEY", "ctrl+shift+y"),
    "ASBPLAYER_TARGET_TITLE": ("POST_ADD_CAPTURE_TARGET_TITLE", "ABSPlayer, Google Chrome, Chrome"),
    "ASBPLAYER_CAPTURE_DELAY_MS": ("POST_ADD_CAPTURE_DELAY_MS", 0),
    "ASBPLAYER_MEDIA_POLL_MS": ("POST_ADD_CAPTURE_MEDIA_COPY_DELAY_MS", 1500),
    "ASBPLAYER_RESUME_PLAYBACK": ("POST_ADD_CAPTURE_RESUME_PLAYBACK", False),
    "ASBPLAYER_CAPTURE_ONLY_RESUME": ("CAPTURE_ONLY_RESUME_PLAYBACK", False),
}


def legacy_value(config, key):
    old, default = LEGACY_SETTINGS[key]
    # New explicit overrides win; earlier settings remain usable from the archive.
    local = getattr(config, "local_config", {})
    if key in local:
        return local[key]
    archive = config.get("ARCHIVED_PLAYBACK_SETTINGS") or {}
    if old in archive:
        return archive[old]
    value = config.get(key)
    if value is None:
        value = config.get(old)
    return default if value is None else value


class LegacyAsbplayerCapture:
    def __init__(self, config):
        self.config = config

    def delay_ms(self):
        try:
            return min(60000, max(0, int(float(legacy_value(self.config, "ASBPLAYER_CAPTURE_DELAY_MS")))))
        except (TypeError, ValueError):
            return 0

    def poll_seconds(self):
        try:
            return min(10, max(.25, float(legacy_value(self.config, "ASBPLAYER_MEDIA_POLL_MS"))/1000))
        except (TypeError, ValueError):
            return 1.5

    def dispatch(self, hotkeys=None):
        hotkey = str(legacy_value(self.config, "ASBPLAYER_CAPTURE_HOTKEY") or "").strip()
        target = str(legacy_value(self.config, "ASBPLAYER_TARGET_TITLE") or "").strip()
        if not hotkey:
            raise ValueError("asbplayer capture shortcut is empty; set its original shortcut in Browser Recording")
        if target and not focus_window_by_title(target):
            raise ValueError("asbplayer capture window not found: " + target)
        # Suppress only the injected capture chord, only in this adapter.
        if hotkeys is not None:
            hotkeys._legacy_capture_chord = hotkey
            hotkeys._legacy_capture_until = time.monotonic() + .5
        if not send_global_hotkey(hotkey):
            raise ValueError("asbplayer capture shortcut could not be sent: " + hotkey)
        return hotkey
