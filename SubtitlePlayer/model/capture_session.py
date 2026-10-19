"""Immutable Anki action targets and mutable capture-operation bookkeeping.

The UI is intentionally allowed to keep changing while an Anki request is in
flight.  These small value objects make the request's target independent from
that live UI state, and give the capture worker one stable identity to use for
media reconciliation and feedback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import time
import uuid
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class FrozenActionTarget:
    """The text/sentence selected at the instant an Anki action is started."""

    selected_text: str
    subtitle_text: str
    sentence_key: str
    initiator: str = "unknown"
    cue_snapshot: Any = None
    selection_spans: tuple = ()
    created_monotonic: float = field(default_factory=time.monotonic)

    @classmethod
    def create(
        cls,
        *,
        selected_text: str,
        subtitle_text: str,
        sentence_key: str,
        initiator: str = "unknown",
        cue_snapshot: Any = None,
        selection_spans=None,
    ) -> "FrozenActionTarget":
        return cls(
            selected_text=str(selected_text or "").strip(),
            subtitle_text=str(subtitle_text or ""),
            sentence_key=str(sentence_key or "").strip(),
            initiator=str(initiator or "unknown"),
            cue_snapshot=cue_snapshot,
            selection_spans=tuple(selection_spans or ()),
        )


@dataclass(slots=True)
class CaptureSession:
    """One native recording and its independently completing Anki operation."""

    target: FrozenActionTarget
    capture_only: bool = False
    early_capture: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    target_note_id: int | None = None
    phase: str = "created"
    cancelled: bool = False
    pause_lease: Any = None
    dispatched_monotonic: float | None = None
    latest_interactive: bool = True
    interaction_owner: str = "ui"
    action_feedback_key: int | None = None
    cancellation_reason: str = ""
    backend: str = "native"
    timing_version: str = ""
    media_identity: Any = None
    media_fingerprint: str = ""
    mapped_interval: tuple[float, float] | None = None
    post_capture_policy: str = "play"
    native_result: Mapping[str, Any] | None = None
    media_completion_notified: bool = False

    def set_target_note(self, note_id: int | None) -> None:
        try:
            parsed = int(note_id or 0)
        except (TypeError, ValueError):
            parsed = 0
        self.target_note_id = parsed if parsed > 0 else None

    def cancel(self, reason: str = "aborted") -> None:
        self.cancelled = True
        self.cancellation_reason = str(reason or "aborted")
        self.phase = "cancelled"


@dataclass(slots=True)
class PlaybackPauseLease:
    """Operation ownership without focus changes or synthetic playback keys."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    was_playing: bool = False
    acquired_monotonic: float = field(default_factory=time.monotonic)
    released: bool = False
