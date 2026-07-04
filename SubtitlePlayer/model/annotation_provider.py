"""In-memory annotation index and segment preparation for subtitle rendering."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Optional

from model.annotation_styles import (
    DEFAULT_STYLES,
    KNOWN_STATUSES,
    MATURE_STATUSES,
    STATUS_PRIORITY,
    load_style_config,
)
from model.word_database import WordDatabase, WordEntry, normalize_word, search_query_variants

logger = logging.getLogger(__name__)


PARTICLES = {
    "は",
    "が",
    "を",
    "に",
    "へ",
    "で",
    "と",
    "も",
    "の",
    "や",
    "か",
    "から",
    "まで",
    "より",
    "ね",
    "よ",
    "ぞ",
    "な",
}

SURU_BASES = {"\u3059\u308b", "\u70ba\u308b", "\u30b9\u30eb"}
SURU_SURFACES = {
    "\u3059\u308b",
    "\u3057",
    "\u3057\u3066",
    "\u3057\u305f",
    "\u3057\u307e\u3059",
    "\u3057\u307e\u3057\u305f",
    "\u3057\u306a\u3044",
    "\u305b",
    "\u3055",
    "\u3059\u308c",
}
SURU_INFLECTION_SUFFIXES = (
    "\u3057\u307e\u3057\u305f",
    "\u3057\u307e\u3059",
    "\u3057\u3066",
    "\u3057\u305f",
    "\u3057\u306a\u3044",
    "\u3057",
)
VERB_LOOKUP_SUFFIXES = (
    ("\u3055\u305b\u3089\u308c\u308b", "\u3059"),
    ("\u3055\u305b\u3089\u308c\u305f", "\u3059"),
    ("\u3055\u305b\u308b", "\u3059"),
    ("\u3055\u308c\u308b", "\u3059"),
    ("\u3055\u308c\u305f", "\u3059"),
    ("\u3055\u308c\u3066", "\u3059"),
    ("\u3055\u308c\u306a\u3044", "\u3059"),
    ("\u3055\u308c\u307e\u3059", "\u3059"),
)


@dataclass(frozen=True)
class AnnotationMatch:
    status: str
    source: str
    surface: str
    normalized: str
    reading: str = ""
    meaning: str = ""
    notes: str = ""


def _config_bool(config, key: str, default: bool = False) -> bool:
    try:
        value = config.get(key)
    except Exception:
        return bool(default)
    if value is None:
        return bool(default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _config_int(config, key: str, default: int, minimum: int | None = None) -> int:
    try:
        value = int(float(config.get(key)))
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    return value


def _segment_base(segment) -> str:
    if isinstance(segment, dict):
        return str(segment.get("base") or "")
    try:
        return str(segment[0] or "")
    except Exception:
        return ""


def _segment_ruby(segment) -> str | None:
    if isinstance(segment, dict):
        value = segment.get("ruby")
    else:
        try:
            value = segment[1]
        except Exception:
            value = None
    return str(value) if value else None


def _make_segment(base: str, ruby: str | None, meta: dict | None = None):
    if meta:
        return (base, ruby, meta)
    return (base, ruby)


class AnnotationProvider:
    """Fast, read-only annotation lookup used by renderer/popup after sync/import."""

    def __init__(self, config, database: WordDatabase) -> None:
        self.config = config
        self.database = database
        self.version = 0
        self._index: dict[str, list[AnnotationMatch]] = {}
        self._styles = load_style_config(self.config.get("ANNOTATION_STYLES"))
        self.last_build_ms = 0.0
        self.refresh()

    def refresh(self) -> None:
        if not self.enabled():
            self._index = {}
            self._styles = load_style_config(self.config.get("ANNOTATION_STYLES"))
            self.version += 1
            self.last_build_ms = 0.0
            return
        started = time.perf_counter()
        index: dict[str, list[AnnotationMatch]] = {}
        entries = self.database.list_entries()
        for entry in entries:
            for key in self._entry_keys(entry):
                index.setdefault(key, []).append(self._entry_to_match(entry))
        for key, matches in index.items():
            matches.sort(key=lambda match: STATUS_PRIORITY.get(match.status, 90))
        self._index = index
        self._styles = load_style_config(self.config.get("ANNOTATION_STYLES"))
        self.version += 1
        self.last_build_ms = (time.perf_counter() - started) * 1000.0
        logger.debug(
            "Built annotation index: entries=%d keys=%d in %.2f ms",
            len(entries),
            len(index),
            self.last_build_ms,
        )

    def _entry_keys(self, entry: WordEntry) -> Iterable[str]:
        seen = set()
        for value in (entry.normalized, entry.surface, entry.base):
            key = normalize_word(value)
            if key and key not in seen:
                seen.add(key)
                yield key
        if _config_bool(self.config, "ANNOTATION_MATCH_READING", False):
            key = normalize_word(entry.reading)
            if key and key not in seen:
                yield key

    @staticmethod
    def _entry_to_match(entry: WordEntry) -> AnnotationMatch:
        return AnnotationMatch(
            status=entry.status,
            source=entry.source,
            surface=entry.surface,
            normalized=entry.normalized,
            reading=entry.reading,
            meaning=entry.meaning,
            notes=entry.notes,
        )

    def enabled(self) -> bool:
        return _config_bool(self.config, "ANNOTATION_ENABLED", False)

    def source_enabled(self, source: str) -> bool:
        if source == "local":
            return _config_bool(self.config, "ANNOTATION_MARK_LOCAL", False)
        if source == "anki":
            return _config_bool(self.config, "ANNOTATION_COLORIZE_ANKI", False)
        if source == "wanikani":
            return _config_bool(self.config, "ANNOTATION_COLORIZE_WANIKANI", False)
        return False

    def _style_for_status(self, status: str) -> dict[str, Any]:
        style = dict(DEFAULT_STYLES.get(status, {}))
        style.update(self._styles.get(status, {}) if isinstance(self._styles, dict) else {})
        return style

    def match_token(self, token: dict[str, Any]) -> AnnotationMatch | None:
        if not self.enabled():
            return None
        surface = str(token.get("surface") or "").strip()
        lookup = str(token.get("lookup") or surface).strip()
        reading = str(token.get("reading") or "").strip()
        if not self._token_allowed(token, surface, lookup):
            return None

        keys = self._candidate_keys_for_token(surface, lookup, reading)

        candidates: list[AnnotationMatch] = []
        for key in keys:
            candidates.extend(self._index.get(key, []))

        for match in sorted(candidates, key=lambda item: STATUS_PRIORITY.get(item.status, 90)):
            if match.status == "ignored":
                return match
            if self.source_enabled(match.source):
                return match

        uncollected_style = self._style_for_status("uncollected")
        if bool(uncollected_style.get("enabled")):
            return AnnotationMatch(
                status="uncollected",
                source="annotation",
                surface=surface,
                normalized=normalize_word(lookup or surface),
                reading=reading,
            )
        return None

    def _candidate_keys_for_token(self, surface: str, lookup: str, reading: str) -> list[str]:
        keys = []
        for value in (
            surface,
            lookup,
            *self._suru_lookup_candidates(surface, lookup),
            *self._verb_lookup_candidates(surface, lookup),
        ):
            key = normalize_word(value)
            if key and key not in keys:
                keys.append(key)
        if _config_bool(self.config, "ANNOTATION_MATCH_READING", False):
            key = normalize_word(reading)
            if key and key not in keys:
                keys.append(key)
        return keys

    @staticmethod
    def _suru_lookup_candidates(surface: str, lookup: str) -> list[str]:
        candidates = []
        for value in (lookup, surface):
            text = str(value or "").strip()
            if not text:
                continue
            if text.endswith("\u3059\u308b"):
                candidates.append(text)
                continue
            for suffix in SURU_INFLECTION_SUFFIXES:
                if text.endswith(suffix) and len(text) > len(suffix):
                    candidates.append(text[: -len(suffix)] + "\u3059\u308b")
                    break
        return candidates

    @staticmethod
    def _verb_lookup_candidates(surface: str, lookup: str) -> list[str]:
        candidates = []
        for value in (lookup, surface):
            text = str(value or "").strip()
            if not text:
                continue
            candidates.extend(search_query_variants(text))
            for suffix, replacement in VERB_LOOKUP_SUFFIXES:
                if text.endswith(suffix) and len(text) > len(suffix):
                    candidates.append(text[: -len(suffix)] + replacement)
                    break
        return candidates

    def _token_allowed(self, token: dict[str, Any], surface: str, lookup: str) -> bool:
        text = unicodedata.normalize("NFKC", surface or lookup or "").strip()
        if not text:
            return False
        min_len = _config_int(self.config, "ANNOTATION_MIN_TOKEN_LENGTH", 1, minimum=1)
        if len(normalize_word(text)) < min_len:
            return False
        include_particles = _config_bool(self.config, "ANNOTATION_INCLUDE_PARTICLES", False)
        if not include_particles:
            pos1 = str(token.get("pos1") or "")
            if pos1 == "助詞" or text in PARTICLES:
                return False
        return True

    def annotate_segments(
        self,
        segments,
        tokenizer: Callable[[str], List[dict[str, Any]]] | None = None,
        *,
        hover_only: bool | None = None,
    ):
        if not segments or not self.enabled():
            return segments

        started = time.perf_counter()
        line_text = "".join(_segment_base(segment) for segment in segments)
        if not line_text:
            return segments
        tokens = self._tokens_for_line(line_text, tokenizer)
        tokens = self._merge_annotation_tokens(tokens, self._ruby_segment_tokens(segments))
        intervals = self._matched_intervals(tokens)
        if not intervals:
            return segments

        output = []
        cursor = 0
        for segment in segments:
            base = _segment_base(segment)
            ruby = _segment_ruby(segment)
            seg_start = cursor
            seg_end = seg_start + len(base)
            cursor = seg_end
            if not base:
                continue

            boundaries = {seg_start, seg_end}
            for start, end, _match in intervals:
                if end <= seg_start or start >= seg_end:
                    continue
                boundaries.add(max(seg_start, start))
                boundaries.add(min(seg_end, end))
            ordered = sorted(boundaries)
            split_ruby_group = bool(ruby and len(ordered) > 2)
            ruby_group_id = f"{seg_start}:{seg_end}:{ruby}" if split_ruby_group else ""
            for left, right in zip(ordered, ordered[1:]):
                if right <= left:
                    continue
                piece = base[left - seg_start:right - seg_start]
                match = self._match_for_span(left, right, intervals)
                meta = self._meta_for_match(match, piece, ruby, hover_only=hover_only) if match else None
                piece_ruby = ruby if piece == base else (ruby if ruby and len(piece) == len(base) else None)
                if split_ruby_group:
                    meta = dict(meta or {})
                    meta.update(
                        {
                            "ruby_group_id": ruby_group_id,
                            "ruby_group_base": base,
                            "ruby_group_ruby": ruby,
                        }
                    )
                    # A substring annotation must not hide the ruby for the containing compound.
                    meta["hide_ruby"] = False
                    meta["hidden_ruby"] = ""
                    piece_ruby = None
                output.append(_make_segment(piece, piece_ruby, meta))
        logger.debug(
            "Annotated subtitle line in %.2f ms: chars=%d intervals=%d",
            (time.perf_counter() - started) * 1000.0,
            len(line_text),
            len(intervals),
        )
        return output

    def _tokens_for_line(self, line_text: str, tokenizer) -> list[dict[str, Any]]:
        if callable(tokenizer):
            try:
                raw = tokenizer(line_text)
            except Exception:
                raw = []
            tokens = []
            for item in raw or []:
                try:
                    start = int(item.get("start"))
                    end = int(item.get("end"))
                except Exception:
                    continue
                if end <= start:
                    continue
                token = dict(item)
                token.setdefault("surface", line_text[start:end])
                token["start"] = start
                token["end"] = end
                tokens.append(token)
            if tokens:
                return self._expand_annotation_tokens(line_text, tokens)
        tokens = [
            {
                "surface": m.group(0),
                "lookup": m.group(0),
                "reading": "",
                "start": m.start(),
                "end": m.end(),
            }
            for m in re.finditer(r"\S+", line_text)
        ]
        return self._expand_annotation_tokens(line_text, tokens)

    def _ruby_segment_tokens(self, segments) -> list[dict[str, Any]]:
        tokens: list[dict[str, Any]] = []
        cursor = 0
        for segment in segments or []:
            base = _segment_base(segment)
            ruby = _segment_ruby(segment)
            start = cursor
            end = start + len(base or "")
            cursor = end
            if not base or not ruby:
                continue
            lookup = str(base or "").strip()
            if not normalize_word(lookup):
                continue
            tokens.append(
                {
                    "surface": base,
                    "lookup": lookup,
                    "reading": str(ruby or "").strip(),
                    "start": start,
                    "end": end,
                    "ruby_segment": True,
                }
            )
        return tokens

    @staticmethod
    def _merge_annotation_tokens(
        tokens: list[dict[str, Any]],
        extra_tokens: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not extra_tokens:
            return tokens
        merged = list(tokens or [])
        seen = {
            (
                int(token.get("start") or 0),
                int(token.get("end") or 0),
                normalize_word(token.get("lookup") or token.get("surface") or ""),
            )
            for token in merged
        }
        for token in extra_tokens:
            key = (
                int(token.get("start") or 0),
                int(token.get("end") or 0),
                normalize_word(token.get("lookup") or token.get("surface") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(token)
        merged.sort(
            key=lambda token: (
                int(token.get("start") or 0),
                -(int(token.get("end") or 0) - int(token.get("start") or 0)),
            )
        )
        return merged

    def _expand_annotation_tokens(self, line_text: str, tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        tokens = self._expand_suru_annotation_tokens(line_text, tokens)
        return self._expand_verb_annotation_tokens(line_text, tokens)

    def _expand_suru_annotation_tokens(self, line_text: str, tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not tokens:
            return tokens
        expanded = list(tokens)
        existing = {
            (
                int(token.get("start") or 0),
                int(token.get("end") or 0),
                normalize_word(token.get("lookup") or token.get("surface") or ""),
            )
            for token in tokens
        }
        for idx in range(1, len(tokens)):
            token = tokens[idx]
            if not self._is_suru_token(token):
                continue
            stem_token = self._previous_suru_stem_token(tokens, idx)
            if stem_token is None:
                continue
            stem = self._suru_stem_text(stem_token)
            if not stem:
                continue
            start = int(stem_token.get("start") or 0)
            end = int(token.get("end") or start)
            tail_idx = idx + 1
            while tail_idx < len(tokens) and self._is_suru_tail_token(tokens[tail_idx]):
                end = int(tokens[tail_idx].get("end") or end)
                tail_idx += 1
            lookup = stem + "\u3059\u308b"
            key = (start, end, normalize_word(lookup))
            if key in existing or end <= start:
                continue
            existing.add(key)
            expanded.append(
                {
                    "surface": line_text[start:end],
                    "lookup": lookup,
                    "reading": "",
                    "start": start,
                    "end": end,
                    "compound": True,
                }
            )
        expanded.sort(
            key=lambda token: (
                int(token.get("start") or 0),
                -(int(token.get("end") or 0) - int(token.get("start") or 0)),
            )
        )
        return expanded

    def _expand_verb_annotation_tokens(self, line_text: str, tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not tokens:
            return tokens
        expanded = list(tokens)
        existing = {
            (
                int(token.get("start") or 0),
                int(token.get("end") or 0),
                normalize_word(token.get("lookup") or token.get("surface") or ""),
            )
            for token in tokens
        }
        for idx, token in enumerate(tokens):
            if not self._is_independent_verb_token(token):
                continue
            lookup = str(token.get("lookup") or "").strip()
            if not lookup or not normalize_word(lookup):
                continue
            start = int(token.get("start") or 0)
            end = int(token.get("end") or start)
            tail_idx = idx + 1
            while tail_idx < len(tokens) and self._is_verb_tail_token(tokens[tail_idx]):
                end = int(tokens[tail_idx].get("end") or end)
                tail_idx += 1
            if end <= int(token.get("end") or start):
                continue
            key = (start, end, normalize_word(lookup))
            if key in existing:
                continue
            existing.add(key)
            expanded.append(
                {
                    "surface": line_text[start:end],
                    "lookup": lookup,
                    "reading": str(token.get("reading") or ""),
                    "start": start,
                    "end": end,
                    "compound": True,
                }
            )
        expanded.sort(
            key=lambda token: (
                int(token.get("start") or 0),
                -(int(token.get("end") or 0) - int(token.get("start") or 0)),
            )
        )
        return expanded

    @staticmethod
    def _is_independent_verb_token(token: dict[str, Any]) -> bool:
        pos1 = str(token.get("pos1") or "")
        pos2 = str(token.get("pos2") or "")
        if pos1 != "\u52d5\u8a5e":
            return False
        return pos2 != "\u975e\u81ea\u7acb\u53ef\u80fd"

    @staticmethod
    def _is_verb_tail_token(token: dict[str, Any]) -> bool:
        surface = str(token.get("surface") or "").strip()
        pos1 = str(token.get("pos1") or "")
        pos2 = str(token.get("pos2") or "")
        if not surface:
            return True
        if pos1 in {"\u52a9\u52d5\u8a5e", "\u52a9\u8a5e", "\u88dc\u52a9\u8a18\u53f7", "\u7a7a\u767d"}:
            return True
        if pos1 == "\u52d5\u8a5e" and pos2 == "\u975e\u81ea\u7acb\u53ef\u80fd":
            return True
        return False

    def _previous_suru_stem_token(self, tokens: list[dict[str, Any]], idx: int) -> dict[str, Any] | None:
        prev_idx = idx - 1
        if prev_idx >= 0 and str(tokens[prev_idx].get("surface") or "") == "\u3092":
            prev_idx -= 1
        if prev_idx < 0:
            return None
        candidate = tokens[prev_idx]
        return candidate if self._suru_stem_text(candidate) else None

    @staticmethod
    def _is_suru_token(token: dict[str, Any]) -> bool:
        values = {
            str(token.get("surface") or "").strip(),
            str(token.get("lookup") or "").strip(),
        }
        if any(value in SURU_BASES or value in SURU_SURFACES for value in values):
            return True
        return any(value.endswith("\u3059\u308b") for value in values)

    @staticmethod
    def _is_suru_tail_token(token: dict[str, Any]) -> bool:
        surface = str(token.get("surface") or "").strip()
        pos1 = str(token.get("pos1") or "")
        pos2 = str(token.get("pos2") or "")
        if not surface:
            return True
        if pos1 in {"\u52a9\u52d5\u8a5e", "\u52a9\u8a5e", "\u88dc\u52a9\u8a18\u53f7", "\u7a7a\u767d"}:
            return True
        if pos1 == "\u52d5\u8a5e" and pos2 == "\u975e\u81ea\u7acb\u53ef\u80fd":
            return True
        return False

    @staticmethod
    def _suru_stem_text(token: dict[str, Any]) -> str:
        for key in ("lookup", "surface"):
            text = str(token.get(key) or "").strip()
            if not text:
                continue
            if text in PARTICLES or text in SURU_BASES or text in SURU_SURFACES:
                continue
            if text.endswith("\u3059\u308b"):
                text = text[:-2]
            if normalize_word(text):
                return text
        return ""

    def _matched_intervals(self, tokens: list[dict[str, Any]]) -> list[tuple[int, int, AnnotationMatch]]:
        candidates: list[tuple[int, int, AnnotationMatch]] = []
        for token in tokens:
            match = self.match_token(token)
            if match is None:
                continue
            start = int(token.get("start") or 0)
            end = int(token.get("end") or 0)
            if end > start:
                candidates.append((start, end, match))

        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), STATUS_PRIORITY.get(item[2].status, 90)))
        accepted: list[tuple[int, int, AnnotationMatch]] = []
        occupied: list[tuple[int, int]] = []
        for start, end, match in candidates:
            if any(not (end <= left or start >= right) for left, right in occupied):
                continue
            accepted.append((start, end, match))
            occupied.append((start, end))
        return sorted(accepted, key=lambda item: item[0])

    @staticmethod
    def _match_for_span(left: int, right: int, intervals: list[tuple[int, int, AnnotationMatch]]) -> AnnotationMatch | None:
        for start, end, match in intervals:
            if left >= start and right <= end:
                return match
        return None

    def _meta_for_match(
        self,
        match: AnnotationMatch,
        piece: str,
        ruby: str | None,
        *,
        hover_only: bool | None,
    ) -> dict[str, Any] | None:
        style = self._style_for_status(match.status)
        if match.status == "ignored":
            style = dict(style)
            style["enabled"] = False
        if not bool(style.get("enabled")) and match.status != "ignored":
            return None

        only_hover = _config_bool(self.config, "ANNOTATION_ONLY_ON_HOVER", False) if hover_only is None else bool(hover_only)
        hide_ruby = self._should_hide_ruby(match.status, style)
        label_parts = []
        if _config_bool(self.config, "ANNOTATION_SHOW_CARD_STATUS_ON_HOVER", False):
            label_parts.append(match.status.replace("_", " "))
        if match.meaning and _config_bool(self.config, "ANNOTATION_SHOW_MEANING_ON_HOVER", False):
            label_parts.append(match.meaning)
        return {
            "annotation": True,
            "status": match.status,
            "source": match.source,
            "style": style,
            "normal_style_visible": not only_hover,
            "hover_highlight": _config_bool(self.config, "ANNOTATION_HIGHLIGHT_ON_HOVER", True),
            "annotation_text": ": ".join(label_parts),
            "hide_ruby": bool(hide_ruby),
            "hidden_ruby": ruby if hide_ruby and ruby else "",
            "show_ruby_on_hover": _config_bool(self.config, "ANNOTATION_SHOW_RUBY_ON_HOVER", True),
            "lookup": match.surface or piece,
        }

    def _should_hide_ruby(self, status: str, style: dict[str, Any]) -> bool:
        if style.get("show_ruby") is False:
            return True
        if style.get("show_ruby") is True:
            return False
        mode = str(self.config.get("ANNOTATION_RUBY_MODE") or "always").strip().lower()
        if mode in {"always", "always_show", "show"}:
            return False
        if mode in {"hover", "hover_only", "show ruby only on hover"}:
            return True
        if mode in {"hide_mature", "mature"}:
            return status in MATURE_STATUSES
        if mode in {"hide_known", "known"}:
            return status in KNOWN_STATUSES
        if mode in {"unknown_only", "show_unknown", "show ruby only for unknown/uncollected words"}:
            return status in KNOWN_STATUSES or status == "ignored"
        return False
