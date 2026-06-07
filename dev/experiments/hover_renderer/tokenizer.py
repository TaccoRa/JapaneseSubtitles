"""Simple Japanese-friendly tokenizer for hover hit-testing."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import List


_TOKEN_RE = re.compile(
    r"([0-9A-Za-z]+|[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf\u3005]+|[^\s])"
)


@dataclass(frozen=True)
class TokenSpan:
    text: str
    start: int
    end: int


def tokenize_for_hover(text: str) -> List[TokenSpan]:
    value = str(text or "")
    out: List[TokenSpan] = []
    for m in _TOKEN_RE.finditer(value):
        token = m.group(0)
        if not token.strip():
            continue
        out.append(TokenSpan(text=token, start=int(m.start()), end=int(m.end())))
    return out

