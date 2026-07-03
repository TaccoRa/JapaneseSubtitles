"""Status names and style defaults for subtitle annotations."""

from __future__ import annotations

import copy
import json
from typing import Any


STATUS_ORDER = [
    "local_known",
    "anki_mature",
    "anki_young",
    "anki_graduated",
    "anki_learning",
    "anki_unknown",
    "wanikani_burned",
    "wanikani_enlightened",
    "wanikani_master",
    "wanikani_guru",
    "wanikani_apprentice",
    "wanikani_unlocked",
    "uncollected",
    "ignored",
]

STATUS_LABELS = {
    "local_known": "Local known",
    "anki_mature": "Mature",
    "anki_young": "Young",
    "anki_graduated": "Graduated",
    "anki_learning": "Learning",
    "anki_unknown": "Unknown",
    "wanikani_burned": "WaniKani Burned",
    "wanikani_enlightened": "WaniKani Enlightened",
    "wanikani_master": "WaniKani Master",
    "wanikani_guru": "WaniKani Guru",
    "wanikani_apprentice": "WaniKani Apprentice",
    "wanikani_unlocked": "WaniKani Unlocked",
    "uncollected": "Uncollected",
    "ignored": "Ignored",
}

MATURE_STATUSES = {
    "local_known",
    "anki_mature",
    "anki_graduated",
    "wanikani_burned",
    "wanikani_enlightened",
    "wanikani_master",
}

KNOWN_STATUSES = {
    "local_known",
    "anki_mature",
    "anki_young",
    "anki_graduated",
    "anki_learning",
    "wanikani_burned",
    "wanikani_enlightened",
    "wanikani_master",
    "wanikani_guru",
    "wanikani_apprentice",
    "wanikani_unlocked",
}

STATUS_PRIORITY = {
    "ignored": 0,
    "local_known": 10,
    "anki_mature": 20,
    "anki_graduated": 21,
    "anki_young": 22,
    "anki_learning": 23,
    "anki_unknown": 24,
    "wanikani_burned": 30,
    "wanikani_enlightened": 31,
    "wanikani_master": 32,
    "wanikani_guru": 33,
    "wanikani_apprentice": 34,
    "wanikani_unlocked": 35,
    "uncollected": 99,
}


def _style(
    *,
    enabled: bool,
    text_color: str = "",
    background_color: str = "",
    underline: bool = False,
    underline_color: str = "",
    overline: bool = False,
    overline_color: str = "",
    outline: bool = False,
    outline_color: str = "",
    show_ruby: bool | None = None,
) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "text_color": text_color,
        "background_color": background_color,
        "background_alpha": 0.35,
        "underline": bool(underline),
        "underline_color": underline_color or text_color or "",
        "underline_thickness": 2,
        "overline": bool(overline),
        "overline_color": overline_color or text_color or "",
        "overline_thickness": 2,
        "outline": bool(outline),
        "outline_color": outline_color,
        "outline_thickness": 1,
        "font_weight": "bold",
        "show_ruby": show_ruby,
    }


DEFAULT_STYLES: dict[str, dict[str, Any]] = {
    "local_known": _style(enabled=True, underline=True, underline_color="#54d66a"),
    "anki_mature": _style(enabled=True, text_color="#66e07f"),
    "anki_young": _style(enabled=True, text_color="#ffd166"),
    "anki_graduated": _style(enabled=True, text_color="#8fd3ff"),
    "anki_learning": _style(enabled=True, text_color="#ff9f43"),
    "anki_unknown": _style(enabled=True, text_color="#ff6b6b"),
    "wanikani_burned": _style(enabled=True, text_color="#b388ff"),
    "wanikani_enlightened": _style(enabled=True, text_color="#c77dff"),
    "wanikani_master": _style(enabled=True, text_color="#7b2cbf"),
    "wanikani_guru": _style(enabled=True, text_color="#00b4d8"),
    "wanikani_apprentice": _style(enabled=True, text_color="#ff4d8d"),
    "wanikani_unlocked": _style(enabled=True, underline=True, underline_color="#9aa0a6"),
    "uncollected": _style(enabled=False, text_color="#b0b0b0"),
    "ignored": _style(enabled=False, text_color="#808080"),
}


def load_style_config(raw: Any) -> dict[str, dict[str, Any]]:
    styles = copy.deepcopy(DEFAULT_STYLES)
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if isinstance(raw, dict):
        for status, overrides in raw.items():
            if status not in styles or not isinstance(overrides, dict):
                continue
            styles[status].update(overrides)
    return styles


def dump_style_config(styles: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = copy.deepcopy(DEFAULT_STYLES)
    if isinstance(styles, dict):
        for status, data in styles.items():
            if status in out and isinstance(data, dict):
                out[status].update(data)
    return out
