"""Immutable source cue intervals and independently cached video intervals.

No code in this module writes a subtitle file. A map is scoped to the exact
source bytes and media/reference fingerprints, never just to an anime title.
"""
from bisect import bisect_right
from dataclasses import dataclass, asdict, replace
import hashlib
import json
import math
from pathlib import Path


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class TimingMap:
    source_hash: str
    media_fingerprint: str
    reference_hash: str
    source: tuple[tuple[float, float], ...]
    video: tuple[tuple[float, float], ...]
    confidence: tuple[bool, ...]
    method: str = "captions"

    def __post_init__(self):
        if not self.source or not len(self.source) == len(self.video) == len(self.confidence):
            raise ValueError("Timing map must contain one video interval per source cue")
        for intervals in (self.source, self.video):
            if any(not (math.isfinite(a) and math.isfinite(b) and a < b) for a, b in intervals):
                raise ValueError("Invalid timing interval")
            if any(a[0] > b[0] for a, b in zip(intervals, intervals[1:])):
                raise ValueError("Nonmonotonic cue order")

    @property
    def version(self):
        return digest(json.dumps(asdict(self), sort_keys=True).encode())[:24]

    def cue_at(self, video_time):
        idx = bisect_right([x[0] for x in self.video], video_time) - 1
        return idx if idx >= 0 and video_time < self.video[idx][1] else None

    def to_video(self, source_time):
        idx = max(0, bisect_right([x[0] for x in self.source], source_time) - 1)
        a, b = self.source[idx]
        c, d = self.video[idx]
        return c + (source_time - a) * (d - c) / (b - a)

    def to_source(self, video_time):
        idx = self.cue_at(video_time)
        if idx is None:
            # A gap must stay a gap even when a timing step skips source time.
            return -1.0
        a, b = self.source[idx]
        c, d = self.video[idx]
        return a + (video_time - c) * (b - a) / (d - c)

    def capture_interval(self, cue_index, before=0.0, after=0.0):
        if not self.confidence[cue_index]:
            raise ValueError("Alignment uncertain for this cue; recording refused")
        a, b = self.video[cue_index]
        return max(0.0, a - before), b + after

    def shifted(self, seconds):
        if not math.isfinite(seconds):
            raise ValueError("Invalid subtitle trim")
        return replace(self, video=tuple((a + seconds, b + seconds) for a, b in self.video))


class TimingMapCache:
    def __init__(self, directory):
        self.directory = Path(directory)

    def _path(self, source_hash, media_fingerprint, reference_hash):
        key = digest(json.dumps([source_hash, media_fingerprint, reference_hash]).encode())
        return self.directory / (key + ".json")

    def save(self, mapping):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(mapping.source_hash, mapping.media_fingerprint, mapping.reference_hash)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(mapping)), encoding="utf-8")
        temporary.replace(path)

    def load(self, source_hash, media_fingerprint, reference_hash):
        try:
            value = json.loads(self._path(source_hash, media_fingerprint, reference_hash).read_text("utf-8"))
            for key in ("source", "video"):
                value[key] = tuple(tuple(x) for x in value[key])
            value["confidence"] = tuple(value["confidence"])
            result = TimingMap(**value)
            if (result.source_hash, result.media_fingerprint, result.reference_hash) != (source_hash, media_fingerprint, reference_hash):
                return None
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return None
