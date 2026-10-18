"""Versioned values shared with the companion extension (seconds throughout)."""
from dataclasses import dataclass
import math
import time

PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class MediaIdentity:
    tab_id: int
    document_id: str
    frame_id: int
    video_id: str

    @classmethod
    def read(cls, value):
        return cls(int(value["tabId"]), str(value["documentId"]),
                   int(value["frameId"]), str(value["videoId"]))

    def wire(self):
        return dict(tabId=self.tab_id, documentId=self.document_id,
                    frameId=self.frame_id, videoId=self.video_id)


@dataclass(frozen=True)
class PlaybackSnapshot:
    identity: MediaIdentity
    sequence: int
    current_time: float
    duration: float
    paused: bool
    buffering: bool
    seeking: bool
    rate: float
    fingerprint: str
    received: float

    @classmethod
    def read(cls, value, now=None):
        numbers = [float(value[k]) for k in ("currentTime", "duration", "rate")]
        if not all(math.isfinite(n) for n in numbers) or numbers[0] < 0 or numbers[2] <= 0:
            raise ValueError("Invalid playback clock")
        return cls(MediaIdentity.read(value["identity"]), int(value["sequence"]),
                   *numbers[:2], bool(value["paused"]), bool(value["buffering"]),
                   bool(value.get("seeking")), numbers[2], str(value["fingerprint"]),
                   time.monotonic() if now is None else now)

    def time_at(self, now=None, stale_after=0.4):
        age = max(0.0, (time.monotonic() if now is None else now) - self.received)
        # Freeze at the final observed position when disconnected; never free-run.
        if age > stale_after or self.paused or self.buffering or self.seeking:
            return self.current_time
        return min(self.duration, self.current_time + age * self.rate)
