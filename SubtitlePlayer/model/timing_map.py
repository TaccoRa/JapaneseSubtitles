"""Immutable source cue intervals and independently cached video intervals.

No code in this module writes a subtitle file. A map is scoped to the exact
source bytes and media/reference fingerprints, never just to an anime title.
"""
from bisect import bisect_right
from dataclasses import dataclass, asdict, replace
from functools import cached_property
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
    # Corroborated regional model, but no direct cue coverage: usable for
    # subtitle preview/navigation, never sufficient for automatic capture.
    preview: tuple[bool, ...] = ()

    def __post_init__(self):
        if not self.source or not len(self.source) == len(self.video) == len(self.confidence):
            raise ValueError("Timing map must contain one video interval per source cue")
        if self.preview and len(self.preview) != len(self.source):
            raise ValueError("Incomplete preview coverage")
        # Whisper/NumPy can supply scalar floats and booleans. Freeze plain
        # Python values so hashing, JSON caching and bridge messages all agree.
        for name in ("source", "video"):
            object.__setattr__(self, name, tuple((float(a), float(b)) for a, b in getattr(self, name)))
        object.__setattr__(self, "confidence", tuple(bool(x) for x in self.confidence))
        object.__setattr__(self, "preview", tuple(bool(x) for x in self.preview))
        for intervals in (self.source, self.video):
            if any(not (math.isfinite(a) and math.isfinite(b) and a < b) for a, b in intervals):
                raise ValueError("Invalid timing interval")
            if any(a[0] > b[0] for a, b in zip(intervals, intervals[1:])):
                raise ValueError("Nonmonotonic cue order")

    @cached_property
    def version(self):
        return digest(json.dumps(asdict(self), sort_keys=True).encode())[:24]

    @cached_property
    def source_starts(self):
        return tuple(a for a, _ in self.source)

    @cached_property
    def video_starts(self):
        return tuple(a for a, _ in self.video)

    @cached_property
    def evidence_summary(self):
        offsets = [video[0]-source[0] for source, video, confident in
                   zip(self.source, self.video, self.confidence) if confident]
        return (len(offsets), min(offsets) if offsets else None, max(offsets) if offsets else None)

    def cue_at(self, video_time):
        idx = bisect_right(self.video_starts, video_time) - 1
        return idx if idx >= 0 and video_time < self.video[idx][1] else None

    def to_video(self, source_time):
        idx = max(0, bisect_right(self.source_starts, source_time) - 1)
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

    def capture_sentence_interval(self, first, last, before=0.0, after=0.0):
        if not 0 <= first <= last < len(self.video):
            raise ValueError("Sentence belongs to a different subtitle timing map")
        if not all(self.confidence[first:last+1]):
            raise ValueError("Alignment uncertain for part of this sentence; recording refused")
        intervals = self.video[first:last+1]
        if any(right[0] < left[0] for left, right in zip(intervals, intervals[1:])):
            raise ValueError("Sentence crosses a non-continuous timing boundary; recording refused")
        return max(0.0, intervals[0][0]-before), max(end for _, end in intervals)+after

    def display_supported(self, cue_index):
        return self.confidence[cue_index] or bool(self.preview and self.preview[cue_index])

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

    def save_reference(self, media_fingerprint, intervals, language=""):
        """Local diagnostics contain timing only, never caption text or URLs."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / ("reference-" + digest(media_fingerprint.encode()) + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(fingerprint=media_fingerprint, language=language,
                                             intervals=intervals)), encoding="utf-8")
        temporary.replace(path)

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
            value["preview"] = tuple(value.get("preview", ()))
            result = TimingMap(**value)
            if (result.source_hash, result.media_fingerprint, result.reference_hash) != (source_hash, media_fingerprint, reference_hash):
                return None
            return result
        except (OSError, ValueError, TypeError, KeyError):
            return None
