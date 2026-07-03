"""Small geometry helpers used by SubtitleManager."""

from __future__ import annotations


def freeze_segments_for_cache(segments) -> tuple:
    return tuple((str(base or ""), str(ruby) if ruby else None) for base, ruby in (segments or ()))


def subtitle_geometry_from_measured_width(font, max_width: int, wrap_limit_px: int = 0) -> tuple[int, int]:
    line_height = font.metrics("linespace")
    ruby_height = int(line_height * 0.6)
    pad_x = 2
    total_height = ruby_height * 2 + line_height * 2

    if int(wrap_limit_px or 0) > 0:
        max_width = min(int(max_width), int(wrap_limit_px))
        total_width = max_width + 2 * pad_x
    else:
        total_width = int(max_width) + 2 * pad_x

    return (total_width, total_height)
