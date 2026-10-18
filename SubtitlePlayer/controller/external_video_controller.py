"""One best-effort delivery path for external video hotkeys.

SubtitlePlayer cannot read a browser/player's playback state.  This service
therefore reports whether a hotkey was delivered to the selected window, never
whether the player consumed it.  A single lock keeps pause/capture/seek
hotkeys in their requested order and prevents two callers from fighting over
foreground focus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
import time
import uuid
from typing import Any, Callable

from utils import dispatch_to_tk, send_hotkey_to_window


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExternalVideoDelivery:
    command: str
    ok: bool
    reason: str
    target: dict | None = None


@dataclass(slots=True)
class PlaybackPauseLease:
    """Ownership record for a best-effort pause around an Anki action."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    was_playing: bool = False
    target: dict | None = None
    external_pause_attempted: bool = False
    acquired_monotonic: float = field(default_factory=time.monotonic)
    released: bool = False


class ExternalVideoCommandService:
    """Serialize semantic commands sent to the shared external-video target."""

    def __init__(self, controller: Any) -> None:
        self.controller = controller
        self._delivery_lock = threading.RLock()

    def _resolve_target(self, requested_target=None) -> dict | None:
        if isinstance(requested_target, dict) and requested_target:
            try:
                if int(requested_target.get("hwnd") or 0) > 0:
                    return dict(requested_target)
            except Exception:
                pass
            if requested_target.get("title_filter") or requested_target.get("process"):
                return dict(requested_target)
        resolver = getattr(self.controller, "_resolve_external_video_target", None)
        if not callable(resolver):
            resolver = getattr(self.controller, "_resolve_voice_video_target", None)
        try:
            result = resolver() if callable(resolver) else None
        except Exception:
            logger.debug("Failed to resolve external video target", exc_info=True)
            result = None
        return dict(result) if isinstance(result, dict) else None

    def _hotkey_for(self, command: str) -> str:
        key_map = {
            "pause": ("VOICE_PLAYBACK_HOTKEY", "space"),
            "resume": ("VOICE_PLAYBACK_HOTKEY", "space"),
            "seek_back": ("VOICE_PLAYBACK_BACK_HOTKEY", "left"),
            "seek_forward": ("VOICE_PLAYBACK_FORWARD_HOTKEY", "right"),
            "capture": ("POST_ADD_CAPTURE_EXTERNAL_HOTKEY", "ctrl+shift+y"),
        }
        key, default = key_map.get(str(command or ""), ("", ""))
        return str(self.controller.config.get(key) or default).strip() if key else ""

    def deliver(
        self,
        command: str,
        *,
        target=None,
        repeat_count: int = 1,
        repeat_interval_ms: int = 120,
    ) -> ExternalVideoDelivery:
        command = str(command or "").strip().lower()
        browser = getattr(self.controller, "browser_sync", None)
        if browser is not None and browser.following and command != "capture":
            try:
                if command in ("pause", "resume"):
                    browser.command("pause" if command == "pause" else "play")
                elif command in ("seek_back", "seek_forward"):
                    delta = float(self.controller.settings._last_skip_value) * max(1, repeat_count)
                    browser.command("seek", time=max(0.0, self.controller.current_time + (delta if command == "seek_forward" else -delta)))
                else:
                    return ExternalVideoDelivery(command, False, "unsupported_browser_command")
                return ExternalVideoDelivery(command, True, "browser_command_pending", browser.identity.wire())
            except (ConnectionError, ValueError) as exc:
                return ExternalVideoDelivery(command, False, str(exc))
        hotkey = self._hotkey_for(command)
        if not hotkey:
            return ExternalVideoDelivery(command, False, "hotkey_empty")
        resolved = self._resolve_target(target)
        if not resolved:
            return ExternalVideoDelivery(command, False, "target_not_found")
        try:
            repeat_count = max(1, min(20, int(repeat_count)))
        except Exception:
            repeat_count = 1
        try:
            repeat_interval_ms = max(0, min(2000, int(repeat_interval_ms)))
        except Exception:
            repeat_interval_ms = 120
        suppress = getattr(self.controller, "_suppress_external_hotkey_events", None)
        try:
            with self._delivery_lock:
                if callable(suppress):
                    suppress(hotkey, repeat_count, repeat_interval_ms)
                result = send_hotkey_to_window(
                    resolved,
                    hotkey,
                    exclude_hwnds=set(getattr(self.controller, "_app_window_hwnds", set()) or set()),
                    restore_foreground=True,
                    repeat_count=repeat_count,
                    repeat_interval_ms=repeat_interval_ms,
                )
        except Exception:
            logger.exception("External video %s delivery failed", command)
            return ExternalVideoDelivery(command, False, "send_failed", resolved)
        return ExternalVideoDelivery(
            command,
            bool(result.get("ok")),
            str(result.get("reason") or "send_failed"),
            dict(result.get("window") or resolved),
        )

    def deliver_async(
        self,
        command: str,
        *,
        target=None,
        repeat_count: int = 1,
        repeat_interval_ms: int = 120,
        on_complete: Callable[[ExternalVideoDelivery], None] | None = None,
    ) -> None:
        def worker() -> None:
            result = self.deliver(
                command,
                target=target,
                repeat_count=repeat_count,
                repeat_interval_ms=repeat_interval_ms,
            )
            if not callable(on_complete):
                return
            root = getattr(getattr(self.controller, "settings", None), "root", None)
            try:
                dispatch_to_tk(root, on_complete, result)
            except Exception:
                on_complete(result)

        threading.Thread(target=worker, daemon=True, name=f"video-{command}").start()
