"""Font helpers for Japanese-capable Tk/Pillow rendering."""

from __future__ import annotations

import os
from typing import Iterable, Optional


def _iter_font_candidates(preferred_family: Optional[str] = None) -> Iterable[str]:
    win_dir = os.environ.get("WINDIR", r"C:\Windows")
    fonts_dir = os.path.join(win_dir, "Fonts")

    preferred = str(preferred_family or "").strip().lower()
    family_map = {
        "meiryo": ["meiryo.ttc", "meiryob.ttc"],
        "yugothic": ["YuGothM.ttc", "YuGothB.ttc"],
        "ms gothic": ["msgothic.ttc", "msyh.ttc"],
    }
    if preferred in family_map:
        for name in family_map[preferred]:
            yield os.path.join(fonts_dir, name)

    for name in (
        "meiryo.ttc",
        "meiryob.ttc",
        "YuGothM.ttc",
        "YuGothB.ttc",
        "msgothic.ttc",
        "msmincho.ttc",
        "NotoSansCJK-Regular.ttc",
        "NotoSerifCJK-Regular.ttc",
    ):
        yield os.path.join(fonts_dir, name)


def find_japanese_font_path(preferred_family: Optional[str] = None) -> Optional[str]:
    for path in _iter_font_candidates(preferred_family=preferred_family):
        if os.path.isfile(path):
            return path
    return None

