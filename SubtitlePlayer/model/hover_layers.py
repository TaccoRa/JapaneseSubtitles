from __future__ import annotations

import re

HOVER_LAYER_ORDER = ("ruby", "dictionary", "translation", "status")


def _hover_region_span(region: dict | None) -> tuple[int, int] | None:
    if not isinstance(region, dict):
        return None
    try:
        start = int(region.get("char_start"))
        end = int(region.get("char_end"))
    except (TypeError, ValueError):
        return None
    return (start, end) if end > start else None


def choose_hover_region(candidates: list[dict], anchor: dict | None = None) -> dict | None:
    """Choose the most specific word span corresponding to a parsed ruby span."""
    if not candidates:
        return None
    anchor_span = _hover_region_span(anchor)

    def rank(item: tuple[int, dict]) -> tuple[int, int, int, int]:
        order, region = item
        span = _hover_region_span(region)
        if span is None:
            try:
                x1, _y1, x2, _y2 = region.get("bbox") or ()
                width = max(1, int(float(x2) - float(x1)))
            except Exception:
                width = 1_000_000
            return (3 if anchor_span else 0, width, bool(region.get("compound")), order)

        start, end = span
        category = 0
        if anchor_span is not None:
            anchor_start, anchor_end = anchor_span
            if start == anchor_start and end == anchor_end:
                category = 0
            elif start <= anchor_start and end >= anchor_end:
                category = 1
            elif start < anchor_end and end > anchor_start:
                category = 2
            else:
                category = 3
        return (category, end - start, bool(region.get("compound")), order)

    return min(enumerate(candidates), key=rank)[1]


def clean_hover_translation(query: str, value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    remainder = text
    query_text = str(query or "").strip()
    if query_text and remainder.startswith(query_text):
        remainder = remainder[len(query_text):]
    remainder = remainder.replace("\u2014", "").replace("\u2013", "")
    remainder = remainder.replace("\u00e2\u20ac\u201d", "")
    remainder = remainder.strip(" \t\r\n:;-_")
    remainder = re.sub(r"^[^\w\u3040-\u30ff\u3400-\u9fff]+", "", remainder).strip()
    if not remainder or not any(ch.isalnum() for ch in remainder):
        return ""
    return remainder


def normal_hover_layers(config, *, popup: bool = False) -> tuple[str, ...]:
    """Return the configured normal-hover stack, nearest layer first."""
    prefix = "HOVER_POPUP_DEFAULT" if popup else "HOVER_DEFAULT"
    keys = {layer: f"{prefix}_{layer.upper()}" for layer in HOVER_LAYER_ORDER}
    selected = []
    for layer in HOVER_LAYER_ORDER:
        try:
            enabled = bool(config.get(keys[layer]))
        except Exception:
            enabled = layer == "ruby"
        if enabled:
            selected.append(layer)
    return tuple(selected)


def hover_layers(config, mode: str, *, popup: bool = False) -> tuple[str, ...]:
    normalized = str(mode or "ruby").strip().lower()
    selected = list(normal_hover_layers(config, popup=popup))
    if normalized in {"dictionary", "status", "translation"}:
        if normalized not in selected:
            selected.append(normalized)
        selected.sort(key=HOVER_LAYER_ORDER.index)
    return tuple(selected)


def combine_hover_text(parts: list[tuple[str, str]], *, ruby_last: bool = False) -> str:
    cleaned = [(name, str(text or "").strip()) for name, text in parts if str(text or "").strip()]
    if ruby_last:
        cleaned.sort(key=lambda item: item[0] == "ruby")
    if not cleaned:
        return ""
    if len(cleaned) == 1 and cleaned[0][0] == "ruby":
        return cleaned[0][1]
    labels = {"dictionary": "Dictionary", "status": "Status", "translation": "Translation"}
    return "\n".join(
        text if name == "ruby" else f"{labels.get(name, name.title())}: {text}"
        for name, text in cleaned
    )
