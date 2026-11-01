"""A screenshot position relative to a mapped cue, independent of padding."""
from dataclasses import dataclass
from fractions import Fraction
import re


@dataclass(frozen=True, slots=True)
class ScreenshotPosition:
    kind: str
    value: Fraction

    @classmethod
    def parse(cls, text):
        raw = str(text).strip().lower().replace(",", ".")
        if re.fullmatch(r"\d+\s*/\s*\d+", raw):
            numerator, denominator = map(int, raw.split("/"))
            if denominator <= 0 or numerator > denominator:
                raise ValueError("Fraction must be between 0 and 1, with a positive denominator")
            return cls("fraction", Fraction(numerator, denominator))
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:ms)?", raw)
        if match is None:
            raise ValueError("Use milliseconds, e.g. 500 ms, or a fraction, e.g. 1/2")
        value = Fraction(match[1])
        if value > 120000:
            raise ValueError("Screenshot delay must be between 0 and 120000 ms")
        return cls("milliseconds", value)

    def canonical(self):
        if self.kind == "fraction":
            return f"{self.value.numerator}/{self.value.denominator}"
        return f"{float(self.value):g} ms"

    def video_time(self, cue_start, cue_end, clip_start, clip_end):
        delta = float(self.value) * (cue_end - cue_start) if self.kind == "fraction" else float(self.value) / 1000
        return max(clip_start, min(clip_end, cue_start + delta))
