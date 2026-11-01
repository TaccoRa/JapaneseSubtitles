"""Per-media timing preferences; legacy trims are retained only as an archive."""
import json
import math
from pathlib import Path
from model.timing_map import digest

MODES = {"audio": "Dialog/Audio", "captions": "Website-Untertitel"}


class AlignmentPreferences:
    def __init__(self, path, legacy=None):
        self.path = Path(path)
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {"version": 1, "media": {}, "trims": {}, "legacyTrims": dict(legacy or {})}
        self.data.setdefault("media", {})
        self.data.setdefault("trims", {})
        self.data.setdefault("manualOffsets", {})

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(self.path)

    def mode(self, media):
        mode = self.data["media"].get(media, "captions")
        return mode if mode in MODES else "captions"

    def inherit_media(self, media, candidates, source):
        """Keep unambiguous offline timing preferences after a confirmed rename."""
        changed = False
        modes = {self.data["media"][old] for old in candidates if self.data["media"].get(old) in MODES}
        if media not in self.data["media"] and len(modes) == 1:
            self.data["media"][media] = modes.pop()
            changed = True
        for mode in MODES:
            key = self.trim_key(media, source, mode)
            for field in ("trims", "manualOffsets"):
                values = self.data[field]
                old = {values[self.trim_key(candidate, source, mode)] for candidate in candidates
                       if isinstance(values.get(self.trim_key(candidate, source, mode)), (int, float))
                       and math.isfinite(values[self.trim_key(candidate, source, mode)])}
                if key not in values and len(old) == 1:
                    values[key] = old.pop()
                    changed = True
        if changed:
            self.save()

    def set_mode(self, media, mode):
        if mode not in MODES:
            raise ValueError("Unknown alignment mode")
        self.data["media"][media] = mode
        self.save()

    def trim_key(self, media, source, mode):
        return digest(json.dumps([media, source, mode]).encode())

    def trim(self, media, source, mode):
        return float(self.data["trims"].get(self.trim_key(media, source, mode), 0))

    def set_trim(self, media, source, mode, value):
        if not math.isfinite(value):
            raise ValueError("Offset must be finite")
        self.data["trims"][self.trim_key(media, source, mode)] = value
        self.save()

    def manual_offset(self, media, source, mode):
        value = self.data["manualOffsets"].get(self.trim_key(media, source, mode))
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None

    def set_manual_offset(self, media, source, mode, value):
        if not math.isfinite(value):
            raise ValueError("Offset must be finite")
        self.data["manualOffsets"][self.trim_key(media, source, mode)] = value
        self.save()

    def adopt_automatic(self, media, source, mode, trim):
        """Convert a manually confirmed total offset to an automatic-map trim."""
        if not math.isfinite(trim):
            raise ValueError("Offset must be finite")
        key = self.trim_key(media, source, mode)
        previous_trim = self.data["trims"].get(key)
        previous_manual = self.data["manualOffsets"].get(key)
        self.data["trims"][key] = trim
        self.data["manualOffsets"].pop(key, None)
        try:
            self.save()
        except OSError:
            for values, previous in ((self.data["trims"], previous_trim),
                                     (self.data["manualOffsets"], previous_manual)):
                if previous is None:
                    values.pop(key, None)
                else:
                    values[key] = previous
            raise

    @staticmethod
    def scope(source, media, audio_track, mode, reference_kind):
        revision = "alignment-v8"
        return digest(json.dumps([revision, source, media, audio_track, mode, reference_kind]).encode())
