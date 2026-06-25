"""Subtitle file decoding and parser dispatch helpers."""

from __future__ import annotations

import os
from typing import Callable, List

import chardet
import srt


def read_subtitle_text(local_path: str) -> tuple[str, str]:
    with open(local_path, "rb") as f:
        raw = f.read()
    detected = chardet.detect(raw)
    text = raw.decode(detected.get("encoding") or "utf-8", errors="replace")
    return os.path.splitext(local_path)[1].lower(), text


def parse_subtitle_file(local_path: str, ass_parser: Callable[[str], List[srt.Subtitle]]) -> List[srt.Subtitle]:
    ext, text = read_subtitle_text(local_path)
    if ext in (".ass", ".ssa"):
        return ass_parser(text)
    return list(srt.parse(text))
