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

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(self.path)

    def mode(self, media):
        mode = self.data["media"].get(media, "audio")
        return mode if mode in MODES else "audio"

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

    @staticmethod
    def scope(source, media, audio_track, mode, reference_kind):
        return digest(json.dumps(["alignment-v3", source, media, audio_track, mode, reference_kind]).encode())
