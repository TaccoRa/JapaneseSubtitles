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
from model.word_database import (
    WordDatabase,
    WordEntry,
    godan_base_from_potential_form,
    normalize_word,
    search_query_variants,
)

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
HONORIFIC_NAME_SUFFIXES = {
    "\u3055\u3093",
    "\u304f\u3093",
    "\u541b",
    "\u3061\u3083\u3093",
    "\u3055\u307e",
    "\u69d8",
    "\u6c0f",
    "\u3069\u306e",
    "\u6bbf",
    "\u5148\u751f",
    "\u305b\u3093\u305b\u3044",
    "\u5148\u8f29",
    "\u305b\u3093\u3071\u3044",
    "\u535a\u58eb",
}


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
        self._phrase_entries: list[tuple[str, AnnotationMatch]] = []
        self._styles = load_style_config(self.config.get("ANNOTATION_STYLES"))
        self.last_build_ms = 0.0
        self.refresh()

    def refresh(self) -> None:
        if not self.enabled():
            self._index = {}
            self._phrase_entries = []
            self._styles = load_style_config(self.config.get("ANNOTATION_STYLES"))
            self.version += 1
            self.last_build_ms = 0.0
            return
        started = time.perf_counter()
        index: dict[str, list[AnnotationMatch]] = {}
        phrase_entries: list[tuple[str, AnnotationMatch]] = []
        entries = self.database.list_entries()
        for entry in entries:
            match = self._entry_to_match(entry)
            for key in self._entry_keys(entry):
                index.setdefault(key, []).append(match)
            for phrase in self._entry_phrases(entry):
                phrase_entries.append((phrase, match))
        for key, matches in index.items():
            matches.sort(key=lambda match: STATUS_PRIORITY.get(match.status, 90))
        self._index = index
        self._phrase_entries = phrase_entries
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
    def _entry_phrases(entry: WordEntry) -> Iterable[str]:
        seen = set()
        for value in (entry.surface, entry.base):
            text = unicodedata.normalize("NFKC", str(value or "")).strip()
            key = normalize_word(text)
            if not key or len(key) < 2 or key in seen:
                continue
            seen.add(key)
            yield text

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

        keys: list[str] = []
        self._add_candidate_key(keys, surface)
        if _config_bool(self.config, "ANNOTATION_MATCH_READING", False):
            self._add_candidate_key(keys, reading)
        match = self._first_enabled_match(keys, reading)
        if match is not None:
            return match

        keys = []
        if bool(token.get("honorific_name")):
            for value in token.get("honorific_base_candidates") or [lookup]:
                self._add_candidate_key(keys, str(value or ""))
        elif (
            self._allow_exact_lookup_candidate(token, surface, lookup)
            and normalize_word(surface) != normalize_word(lookup)
        ):
            self._add_candidate_key(keys, lookup)
            for value in self._orthographic_lookup_candidates(token, lookup):
                self._add_candidate_key(keys, value)
        match = self._first_enabled_match(keys, "")
        if match is not None:
            return match

        keys = self._fallback_candidate_keys_for_token(token, surface, lookup, reading)
        match = self._first_enabled_match(keys, "")
        if match is not None:
            return match

        if (
            bool(token.get("honorific_name"))
            and _config_bool(self.config, "ANNOTATION_RECOGNIZE_HONORIFIC_NAMES", True)
        ):
            return AnnotationMatch(
                status="name",
                source="annotation",
                surface=surface,
                normalized=normalize_word(lookup or surface),
                reading=reading,
            )

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

    def _first_enabled_match(self, keys: list[str], reading: str) -> AnnotationMatch | None:
        candidates: list[AnnotationMatch] = []
        for key in keys:
            candidates.extend(self._index.get(key, []))

        for match in sorted(candidates, key=lambda item: STATUS_PRIORITY.get(item.status, 90)):
            if not self._reading_compatible(reading, match.reading):
                continue
            if match.status == "ignored":
                return match
            if self.source_enabled(match.source):
                return match
        return None

    @staticmethod
    def _reading_key(value: str) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip()
        if AnnotationProvider._contains_cjk(text):
            return ""
        text = "".join(
            chr(ord(ch) - 0x60) if "\u30a1" <= ch <= "\u30f6" else ch
            for ch in text
        )
        return normalize_word(text)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any(
            "\u3400" <= ch <= "\u4dbf"
            or "\u4e00" <= ch <= "\u9fff"
            or "\uf900" <= ch <= "\ufaff"
            for ch in str(text or "")
        )

    @staticmethod
    def _is_kana_only(text: str) -> bool:
        value = unicodedata.normalize("NFKC", str(text or "")).strip()
        if not value:
            return False
        has_kana = False
        for ch in value:
            if ch.isspace():
                continue
            if "\u3041" <= ch <= "\u309f" or "\u30a1" <= ch <= "\u30ff" or ch == "\u30fc":
                has_kana = True
                continue
            return False
        return has_kana

    @classmethod
    def _reading_compatible(cls, token_reading: str, entry_reading: str) -> bool:
        token_key = cls._reading_key(token_reading)
        entry_key = cls._reading_key(entry_reading)
        return not token_key or not entry_key or token_key == entry_key

    @staticmethod
    def _add_candidate_key(keys: list[str], value: str) -> None:
        key = normalize_word(value)
        if key and key not in keys:
            keys.append(key)

    def _exact_candidate_keys_for_token(
        self,
        token: dict[str, Any] | None,
        surface: str,
        lookup: str,
        reading: str,
    ) -> list[str]:
        keys: list[str] = []
        self._add_candidate_key(keys, surface)
        if self._allow_exact_lookup_candidate(token, surface, lookup):
            self._add_candidate_key(keys, lookup)
        if _config_bool(self.config, "ANNOTATION_MATCH_READING", False):
            self._add_candidate_key(keys, reading)
        return keys

    @staticmethod
    def _allow_exact_lookup_candidate(token: dict[str, Any] | None, surface: str, lookup: str) -> bool:
        if normalize_word(surface) == normalize_word(lookup):
            return True
        if isinstance(token, dict) and bool(token.get("honorific_name")):
            return True
        if AnnotationProvider._is_kana_only(surface):
            return False
        if isinstance(token, dict) and str(token.get("pos1") or "") == "\u540d\u8a5e":
            return False
        return True

    @staticmethod
    def _orthographic_lookup_candidates(token: dict[str, Any], lookup: str) -> list[str]:
        orth_base = str(token.get("orth_base") or "").strip()
        if not orth_base or orth_base == "*":
            return []

        candidates = [orth_base]
        pos1 = str(token.get("pos1") or "")
        c_type = str(token.get("c_type") or "")
        if (
            pos1 == "動詞"
            and normalize_word(orth_base) != normalize_word(lookup)
            and (not c_type or "下一段" in c_type)
        ):
            potential_base = godan_base_from_potential_form(orth_base)
            if potential_base:
                candidates.append(potential_base)
        return candidates

    def _fallback_candidate_keys_for_token(
        self,
        token: dict[str, Any],
        surface: str,
        lookup: str,
        reading: str,
    ) -> list[str]:
        keys: list[str] = []
        for value in self._suru_lookup_candidates(surface, lookup):
            self._add_candidate_key(keys, value)
        if self._allow_verb_lookup_fallback(token, surface, lookup):
            for value in self._verb_lookup_candidates(surface, lookup):
                self._add_candidate_key(keys, value)
        elif self._allow_verb_suffix_lookup_fallback(token, surface, lookup):
            for value in self._verb_suffix_lookup_candidates(surface, lookup):
                self._add_candidate_key(keys, value)
        if _config_bool(self.config, "ANNOTATION_MATCH_READING", False):
            self._add_candidate_key(keys, reading)
        return keys

    def _candidate_keys_for_token(self, surface: str, lookup: str, reading: str) -> list[str]:
        keys = self._exact_candidate_keys_for_token(None, surface, lookup, reading)
        for value in self._suru_lookup_candidates(surface, lookup):
            self._add_candidate_key(keys, value)
        for value in self._verb_lookup_candidates(surface, lookup):
            self._add_candidate_key(keys, value)
        return keys

    def _allow_verb_lookup_fallback(self, token: dict[str, Any], surface: str, lookup: str) -> bool:
        pos1 = str(token.get("pos1") or "")
        if bool(token.get("compound")):
            return True
        if self._is_kana_only(surface) and normalize_word(surface) != normalize_word(lookup):
            return False
        if pos1 in {"\u52d5\u8a5e", "\u5f62\u5bb9\u8a5e"}:
            return True
        if pos1 == "\u540d\u8a5e":
            return _config_bool(self.config, "ANNOTATION_MATCH_DERIVED_VERB_NOUNS", False)
        return normalize_word(surface) != normalize_word(lookup)

    @staticmethod
    def _allow_verb_suffix_lookup_fallback(token: dict[str, Any], surface: str, lookup: str) -> bool:
        pos1 = str(token.get("pos1") or "")
        if pos1 == "\u540d\u8a5e":
            return False
        return normalize_word(surface) == normalize_word(lookup)

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
            candidates.extend(AnnotationProvider._verb_suffix_candidates_for_text(text))
        return candidates

    @staticmethod
    def _verb_suffix_lookup_candidates(surface: str, lookup: str) -> list[str]:
        candidates = []
        for value in (lookup, surface):
            text = str(value or "").strip()
            if not text:
                continue
            candidates.extend(AnnotationProvider._verb_suffix_candidates_for_text(text))
        return candidates

    @staticmethod
    def _verb_suffix_candidates_for_text(text: str) -> list[str]:
        candidates = []
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
        intervals = self._matched_intervals(tokens, line_text=line_text)
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
        tokens = self._expand_verb_annotation_tokens(line_text, tokens)
        tokens = self._expand_purpose_ni_annotation_tokens(line_text, tokens)
        return self._expand_honorific_name_tokens(line_text, tokens)

    def _expand_honorific_name_tokens(
        self,
        line_text: str,
        tokens: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not tokens or not _config_bool(self.config, "ANNOTATION_RECOGNIZE_HONORIFIC_NAMES", True):
            return tokens

        proper_names = [token for token in tokens if self._is_proper_name_token(token)]
        suffixes = [
            token
            for token in tokens
            if str(token.get("surface") or "").strip() in HONORIFIC_NAME_SUFFIXES
        ]
        if not proper_names or not suffixes:
            return tokens

        expanded = list(tokens)
        existing = {
            (
                int(token.get("start") or 0),
                int(token.get("end") or 0),
                bool(token.get("honorific_name")),
            )
            for token in tokens
        }
        for suffix in suffixes:
            suffix_start = int(suffix.get("start") or 0)
            suffix_end = int(suffix.get("end") or suffix_start)
            if suffix_end <= suffix_start:
                continue
            preceding = [
                token
                for token in proper_names
                if int(token.get("end") or 0) <= suffix_start
                and not line_text[int(token.get("end") or 0):suffix_start].strip()
            ]
            if not preceding:
                continue
            nearest = max(preceding, key=lambda token: int(token.get("end") or 0))
            name_parts = [nearest]
            name_start = int(nearest.get("start") or 0)

            while True:
                previous = [
                    token
                    for token in proper_names
                    if token not in name_parts
                    and int(token.get("end") or 0) <= name_start
                    and not line_text[int(token.get("end") or 0):name_start].strip()
                ]
                if not previous:
                    break
                candidate = max(previous, key=lambda token: int(token.get("end") or 0))
                name_parts.insert(0, candidate)
                name_start = int(candidate.get("start") or name_start)

            key = (name_start, suffix_end, True)
            if key in existing:
                continue
            existing.add(key)
            full_base = "".join(str(token.get("surface") or "").strip() for token in name_parts)
            base_candidates = []
            for value in (
                full_base,
                *(str(token.get("surface") or "").strip() for token in reversed(name_parts)),
                *(str(token.get("lookup") or "").strip() for token in reversed(name_parts)),
            ):
                if normalize_word(value) and value not in base_candidates:
                    base_candidates.append(value)
            expanded.append(
                {
                    "surface": line_text[name_start:suffix_end],
                    "lookup": full_base,
                    "reading": self._combined_token_reading(
                        sorted(name_parts + [suffix], key=lambda token: int(token.get("start") or 0)),
                        0,
                        len(name_parts) + 1,
                    ),
                    "start": name_start,
                    "end": suffix_end,
                    "pos1": "\u540d\u8a5e",
                    "pos2": "\u56fa\u6709\u540d\u8a5e",
                    "honorific_name": True,
                    "honorific_base_candidates": base_candidates,
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
    def _is_proper_name_token(token: dict[str, Any]) -> bool:
        return "\u56fa\u6709\u540d\u8a5e" in str(token.get("pos2") or "")

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
            while tail_idx < len(tokens):
                tail_token = tokens[tail_idx]
                tail_start = int(tail_token.get("start") or end)
                if self._source_gap_between(line_text, end, tail_start) or self._is_source_separator_token(tail_token):
                    break
                if not self._is_verb_tail_token(tail_token):
                    break
                end = int(tail_token.get("end") or end)
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
                    "orth_base": str(token.get("orth_base") or ""),
                    "reading": self._combined_token_reading(tokens, idx, tail_idx) or str(token.get("reading") or ""),
                    "start": start,
                    "end": end,
                    "pos1": str(token.get("pos1") or ""),
                    "pos2": str(token.get("pos2") or ""),
                    "c_type": str(token.get("c_type") or ""),
                    "c_form": str(token.get("c_form") or ""),
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

    def _expand_purpose_ni_annotation_tokens(self, line_text: str, tokens: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
            if bool(token.get("compound")) or str(token.get("pos1") or "") != "\u540d\u8a5e":
                continue
            stem = str(token.get("surface") or "").strip()
            if not stem or not self._contains_cjk(stem):
                continue
            if not self._renyou_has_dictionary_variant(stem):
                continue
            prev_idx = self._previous_content_token_index(tokens, idx)
            next_idx = self._next_content_token_index(tokens, idx)
            if prev_idx is None or next_idx is None:
                continue
            prev_token = tokens[prev_idx]
            next_token = tokens[next_idx]
            if str(prev_token.get("surface") or "").strip() != "\u3092":
                continue
            if str(next_token.get("surface") or "").strip() != "\u306b":
                continue
            if self._has_source_separator_between(line_text, idx, next_idx, tokens):
                continue
            start = int(token.get("start") or 0)
            end = int(next_token.get("end") or start)
            key = (start, end, normalize_word(stem))
            if key in existing or end <= start:
                continue
            existing.add(key)
            expanded.append(
                {
                    "surface": line_text[start:end],
                    "lookup": stem,
                    "reading": self._combined_token_reading(tokens, idx, next_idx + 1),
                    "start": start,
                    "end": end,
                    "compound": True,
                    "purpose_ni": True,
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
    def _source_gap_between(line_text: str, left_end: int, right_start: int) -> bool:
        if right_start <= left_end:
            return False
        return bool(line_text[left_end:right_start])

    @staticmethod
    def _is_source_separator_token(token: dict[str, Any]) -> bool:
        surface = str(token.get("surface") or "")
        pos1 = str(token.get("pos1") or "")
        return not surface.strip() or pos1 in {"\u7a7a\u767d"}

    @staticmethod
    def _renyou_has_dictionary_variant(text: str) -> bool:
        normalized = normalize_word(text)
        return any(candidate != normalized for candidate in search_query_variants(text))

    @classmethod
    def _previous_content_token_index(cls, tokens: list[dict[str, Any]], idx: int) -> int | None:
        for probe in range(idx - 1, -1, -1):
            if not cls._is_source_separator_token(tokens[probe]):
                return probe
        return None

    @classmethod
    def _next_content_token_index(cls, tokens: list[dict[str, Any]], idx: int) -> int | None:
        for probe in range(idx + 1, len(tokens)):
            if not cls._is_source_separator_token(tokens[probe]):
                return probe
        return None

    @classmethod
    def _has_source_separator_between(
        cls,
        line_text: str,
        left_idx: int,
        right_idx: int,
        tokens: list[dict[str, Any]],
    ) -> bool:
        left_token = tokens[left_idx]
        right_token = tokens[right_idx]
        left_end = int(left_token.get("end") or 0)
        right_start = int(right_token.get("start") or left_end)
        if cls._source_gap_between(line_text, left_end, right_start):
            return True
        return any(cls._is_source_separator_token(tokens[probe]) for probe in range(left_idx + 1, right_idx))

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
        if pos1 == "\u52a9\u8a5e":
            return surface in {"\u3066", "\u3067"}
        if pos1 in {"\u52a9\u52d5\u8a5e", "\u88dc\u52a9\u8a18\u53f7", "\u7a7a\u767d"}:
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

    @staticmethod
    def _combined_token_reading(tokens: list[dict[str, Any]], start_idx: int, end_idx: int) -> str:
        parts = []
        for token in tokens[start_idx:end_idx]:
            reading = str(token.get("reading") or "").strip()
            if reading:
                parts.append(reading)
        return "".join(parts)

    @staticmethod
    def _compact_with_offsets(text: str) -> tuple[str, list[int]]:
        chars: list[str] = []
        offsets: list[int] = []
        for idx, char in enumerate(unicodedata.normalize("NFKC", str(text or ""))):
            if char.isspace():
                continue
            chars.append(char.casefold())
            offsets.append(idx)
        return "".join(chars), offsets

    def _phrase_intervals_for_line(
        self,
        line_text: str,
        tokens: list[dict[str, Any]],
    ) -> list[tuple[int, int, AnnotationMatch]]:
        if not line_text or not self._phrase_entries:
            return []

        min_len = _config_int(self.config, "ANNOTATION_MIN_TOKEN_LENGTH", 1, minimum=1)
        token_spans = {
            (int(token.get("start") or 0), int(token.get("end") or 0))
            for token in tokens or []
            if int(token.get("end") or 0) > int(token.get("start") or 0)
        }
        token_boundaries = {boundary for span in token_spans for boundary in span}
        intervals: list[tuple[int, int, AnnotationMatch]] = []
        seen: set[tuple[int, int, str, str]] = set()
        compact_line, compact_offsets = self._compact_with_offsets(line_text)

        def add_interval(start: int, end: int, match: AnnotationMatch) -> None:
            if start not in token_boundaries or end not in token_boundaries:
                return
            key = (start, end, match.source, match.normalized)
            if key in seen:
                return
            seen.add(key)
            intervals.append((start, end, match))

        for phrase, match in self._phrase_entries:
            normalized_phrase = normalize_word(phrase)
            if len(normalized_phrase) < min_len:
                continue
            if match.status != "ignored" and not self.source_enabled(match.source):
                continue
            start = line_text.find(phrase)
            while start >= 0:
                end = start + len(phrase)
                add_interval(start, end, match)
                start = line_text.find(phrase, start + 1)
            if not compact_line or not compact_offsets:
                continue
            compact_phrase = normalized_phrase
            start_compact = compact_line.find(compact_phrase)
            while start_compact >= 0:
                end_compact = start_compact + len(compact_phrase) - 1
                if 0 <= end_compact < len(compact_offsets):
                    start = compact_offsets[start_compact]
                    end = compact_offsets[end_compact] + 1
                    add_interval(start, end, match)
                start_compact = compact_line.find(compact_phrase, start_compact + 1)
        return intervals

    def _matched_intervals(
        self,
        tokens: list[dict[str, Any]],
        *,
        line_text: str = "",
    ) -> list[tuple[int, int, AnnotationMatch]]:
        candidates: list[tuple[int, int, AnnotationMatch, int]] = []
        if line_text:
            candidates.extend((start, end, match, 0) for start, end, match in self._phrase_intervals_for_line(line_text, tokens))
        suppressed_token_spans = self._spaced_verb_component_spans(line_text, tokens) if line_text else set()
        for token in tokens:
            start = int(token.get("start") or 0)
            end = int(token.get("end") or 0)
            if (start, end) in suppressed_token_spans:
                continue
            match = self.match_token(token)
            if match is None:
                continue
            if end > start:
                candidates.append((start, end, match, 1))

        candidates.sort(
            key=lambda item: (
                item[0],
                -(item[1] - item[0]),
                item[3],
                STATUS_PRIORITY.get(item[2].status, 90),
            )
        )
        accepted: list[tuple[int, int, AnnotationMatch]] = []
        occupied: list[tuple[int, int]] = []
        for start, end, match, _priority in candidates:
            if any(not (end <= left or start >= right) for left, right in occupied):
                continue
            accepted.append((start, end, match))
            occupied.append((start, end))
        return sorted(accepted, key=lambda item: item[0])

    def _spaced_verb_component_spans(self, line_text: str, tokens: list[dict[str, Any]]) -> set[tuple[int, int]]:
        spans: set[tuple[int, int]] = set()
        ordered = sorted(
            [token for token in tokens or [] if int(token.get("end") or 0) > int(token.get("start") or 0)],
            key=lambda token: (int(token.get("start") or 0), int(token.get("end") or 0)),
        )
        for idx, token in enumerate(ordered):
            if bool(token.get("compound")) or not self._is_independent_verb_token(token):
                continue
            next_idx = self._next_content_token_index(ordered, idx)
            if next_idx is None:
                continue
            next_token = ordered[next_idx]
            if bool(next_token.get("compound")) or str(next_token.get("pos1") or "") != "\u52d5\u8a5e":
                continue
            if str(next_token.get("pos2") or "") != "\u975e\u81ea\u7acb\u53ef\u80fd":
                continue
            if not self._has_source_separator_between(line_text, idx, next_idx, ordered):
                continue
            start = int(token.get("start") or 0)
            end = int(token.get("end") or 0)
            next_start = int(next_token.get("start") or 0)
            next_end = int(next_token.get("end") or 0)
            if end > start:
                spans.add((start, end))
            if next_end > next_start:
                spans.add((next_start, next_end))
        return spans

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
