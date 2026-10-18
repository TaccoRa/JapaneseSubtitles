"""Immutable analysis shared by every consumer of one displayed subtitle cue."""

from __future__ import annotations

import re
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence


FrozenMeta = tuple[tuple[str, Any], ...]
SourceSpan = tuple[int, int, int]


def _segment_base(segment) -> str:
    if isinstance(segment, Mapping):
        return str(segment.get("base") or "")
    try:
        return str(segment[0] or "")
    except Exception:
        return ""


def _segment_ruby(segment) -> str | None:
    if isinstance(segment, Mapping):
        value = segment.get("ruby")
    else:
        try:
            value = segment[1]
        except Exception:
            value = None
    return str(value) if value else None


def _segment_meta(segment) -> dict[str, Any]:
    if isinstance(segment, Mapping):
        value = segment.get("meta") or {}
    else:
        try:
            value = segment[2] if len(segment) > 2 else {}
        except Exception:
            value = {}
    return dict(value) if isinstance(value, Mapping) else {}


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_value(item) for item in value), key=repr))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _thaw_value(value: Any) -> Any:
    if isinstance(value, tuple):
        if all(
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], str)
            for item in value
        ):
            return {key: _thaw_value(item) for key, item in value}
        return tuple(_thaw_value(item) for item in value)
    return value


def _freeze_meta(meta: Mapping[str, Any] | None) -> FrozenMeta:
    if not meta:
        return ()
    return tuple(sorted((str(key), _freeze_value(value)) for key, value in meta.items()))


def _normalized_selection(value: str, *, compact: bool = False) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    if compact:
        return re.sub(r"\s+", "", text).casefold()
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass(frozen=True, slots=True)
class CueAnnotationMatch:
    status: str
    source: str
    surface: str
    normalized: str
    reading: str = ""
    meaning: str = ""
    notes: str = ""
    match_reason: str = ""
    matched_key: str = ""

    @classmethod
    def from_match(cls, match: Any) -> "CueAnnotationMatch | None":
        if match is None:
            return None
        return cls(
            status=str(getattr(match, "status", "") or ""),
            source=str(getattr(match, "source", "") or ""),
            surface=str(getattr(match, "surface", "") or ""),
            normalized=str(getattr(match, "normalized", "") or ""),
            reading=str(getattr(match, "reading", "") or ""),
            meaning=str(getattr(match, "meaning", "") or ""),
            notes=str(getattr(match, "notes", "") or ""),
            match_reason=str(getattr(match, "match_reason", "") or ""),
            matched_key=str(getattr(match, "matched_key", "") or ""),
        )


@dataclass(frozen=True, slots=True)
class CueRubySpan:
    start: int
    end: int
    base: str
    ruby: str


@dataclass(frozen=True, slots=True)
class CueToken:
    line_index: int
    start: int
    end: int
    cue_start: int
    cue_end: int
    surface: str
    lemma: str
    pos1: str
    pos2: str
    reading: str
    ruby: str
    dictionary_lookup_key: str
    conjugation_type: str = ""
    conjugation_form: str = ""
    orthographic_base: str = ""
    compound: bool = False
    honorific_name: bool = False
    annotation: CueAnnotationMatch | None = None
    ruby_spans: tuple[CueRubySpan, ...] = ()

    @property
    def key(self) -> tuple[int, int, int, str]:
        return self.line_index, self.start, self.end, self.dictionary_lookup_key

    def as_mapping(self) -> dict[str, Any]:
        value = {
            "surface": self.surface,
            "lookup": self.dictionary_lookup_key,
            "lemma": self.lemma,
            "reading": self.reading,
            "ruby": self.ruby,
            "start": self.start,
            "end": self.end,
            "cue_start": self.cue_start,
            "cue_end": self.cue_end,
            "line_index": self.line_index,
            "pos1": self.pos1,
            "pos2": self.pos2,
            "c_type": self.conjugation_type,
            "c_form": self.conjugation_form,
            "orth_base": self.orthographic_base,
            "compound": self.compound,
            "honorific_name": self.honorific_name,
            "cue_token": self,
        }
        if self.annotation is not None:
            value.update(
                {
                    "annotation": True,
                    "status": self.annotation.status,
                    "source": self.annotation.source,
                    "annotation_surface": self.annotation.surface,
                    "annotation_meaning": self.annotation.meaning,
                    "annotation_notes": self.annotation.notes,
                    "annotation_match_reason": self.annotation.match_reason,
                    "annotation_matched_key": self.annotation.matched_key,
                }
            )
        return value


@dataclass(frozen=True, slots=True)
class CueSegment:
    line_index: int
    start: int
    end: int
    base: str
    ruby: str | None = None
    meta: FrozenMeta = ()

    def meta_dict(self) -> dict[str, Any]:
        return {key: _thaw_value(value) for key, value in self.meta}

    def as_legacy(self):
        meta = self.meta_dict()
        if meta:
            return self.base, self.ruby, meta
        return self.base, self.ruby


@dataclass(frozen=True, slots=True)
class CueLine:
    index: int
    position: str
    cue_start: int
    text: str
    segments: tuple[CueSegment, ...]
    annotated_segments: tuple[CueSegment, ...]
    tokens: tuple[CueToken, ...]

    def legacy_segments(self, *, annotated: bool = False) -> tuple:
        source = self.annotated_segments if annotated else self.segments
        return tuple(segment.as_legacy() for segment in source)


@dataclass(frozen=True, slots=True)
class CueSelection:
    text: str
    spans: tuple[SourceSpan, ...]
    tokens: tuple[CueToken, ...]

    @property
    def preferred_lookup(self) -> str:
        if not self.tokens:
            return self.text.strip()
        normalized = _normalized_selection(self.text, compact=True)
        exact = [
            token
            for token in self.tokens
            if _normalized_selection(token.surface, compact=True) == normalized
        ]
        if exact:
            exact.sort(key=lambda token: (not token.compound, -(token.end - token.start)))
            return exact[0].dictionary_lookup_key or exact[0].lemma or self.text.strip()
        if len(self.tokens) == 1:
            token = self.tokens[0]
            if not self.spans or any(
                token.line_index == line and token.start == start and token.end == end
                for line, start, end in self.spans
            ):
                return token.dictionary_lookup_key or token.lemma or self.text.strip()
        base_tokens = sorted(
            (token for token in self.tokens if not token.compound),
            key=lambda token: (token.line_index, token.start, token.end),
        )
        if (
            base_tokens
            and _normalized_selection("".join(token.surface for token in base_tokens), compact=True)
            == normalized
            and base_tokens[0].pos1 in {"動詞", "形容詞"}
            and all(token.pos1 in {"助動詞", "助詞", "接尾辞"} for token in base_tokens[1:])
        ):
            first = base_tokens[0]
            return first.dictionary_lookup_key or first.lemma or self.text.strip()
        return self.text.strip()


@dataclass(frozen=True, slots=True)
class CueSnapshot:
    cue_index: int
    start_time: float
    end_time: float
    clean_text: str
    lines: tuple[CueLine, ...]
    annotation_version: int = 0

    @property
    def plain_text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def tokens(self) -> tuple[CueToken, ...]:
        return tuple(token for line in self.lines for token in line.tokens)

    def line(self, index: int) -> CueLine | None:
        try:
            target = int(index)
        except Exception:
            return None
        for line in self.lines:
            if line.index == target:
                return line
        return None

    def line_for_position(self, position: str) -> CueLine | None:
        target = str(position or "")
        for line in self.lines:
            if line.position == target:
                return line
        return None

    def legacy_line_segments(self, *, annotated: bool = False) -> list[list[tuple]]:
        return [list(line.legacy_segments(annotated=annotated)) for line in self.lines]

    def renderer_segments(self, position: str, *, annotated: bool = True) -> tuple:
        line = self.line_for_position(position)
        return line.legacy_segments(annotated=annotated) if line is not None else ()

    @staticmethod
    def _starts_with_kanji(text: str) -> bool:
        if not text:
            return False
        code = ord(text[0])
        return (
            code == 0x3005
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
        )

    @property
    def inline_ruby_text(self) -> str:
        lines: list[str] = []
        for line in self.lines:
            output: list[str] = []
            for segment in line.segments:
                if segment.ruby:
                    if (
                        self._starts_with_kanji(segment.base)
                        and output
                        and not output[-1].endswith((" ", "\n", "\t"))
                        and not output[-1].endswith(("[", "(", "（", "{", "｛", "<", "＜", "「", "『", "【"))
                    ):
                        output.append(" ")
                    output.append(f"{segment.base}[{segment.ruby}]")
                else:
                    output.append(segment.base)
            rendered = "".join(output).strip()
            if rendered:
                lines.append(rendered)
        return "\n".join(lines)

    def resolve_selection(
        self,
        text: str,
        spans: Iterable[SourceSpan] | None = None,
    ) -> CueSelection:
        selected = str(text or "").strip()
        normalized_spans = tuple(
            (int(line), int(start), int(end))
            for line, start, end in (spans or ())
            if int(end) > int(start)
        )
        matched: list[CueToken] = []
        if normalized_spans:
            for token in self.tokens:
                if any(
                    token.line_index == line
                    and token.end > start
                    and token.start < end
                    for line, start, end in normalized_spans
                ):
                    matched.append(token)
        else:
            normal = _normalized_selection(selected)
            compact = _normalized_selection(selected, compact=True)
            exact = [
                token
                for token in self.tokens
                if _normalized_selection(token.surface) == normal
                or _normalized_selection(token.surface, compact=True) == compact
            ]
            if exact:
                matched = exact
            elif compact:
                for line in self.lines:
                    start = line.text.find(selected)
                    if start < 0:
                        compact_line = re.sub(r"\s+", "", line.text)
                        compact_start = compact_line.find(re.sub(r"\s+", "", selected))
                        if compact_start < 0:
                            continue
                        cursor = 0
                        offsets = []
                        for index, char in enumerate(line.text):
                            if char.isspace():
                                continue
                            if cursor >= compact_start and cursor < compact_start + len(compact):
                                offsets.append(index)
                            cursor += 1
                        if not offsets:
                            continue
                        start, end = offsets[0], offsets[-1] + 1
                    else:
                        end = start + len(selected)
                    matched.extend(
                        token
                        for token in line.tokens
                        if token.end > start and token.start < end
                    )
                    if matched:
                        normalized_spans = ((line.index, start, end),)
                        break

        unique: list[CueToken] = []
        seen = set()
        for token in matched:
            if token.key in seen:
                continue
            seen.add(token.key)
            unique.append(token)
        unique.sort(key=lambda token: (token.line_index, token.start, -(token.end - token.start)))
        return CueSelection(selected, normalized_spans, tuple(unique))


class CueSnapshotBuilder:
    """Build and cache one immutable analysis for a displayed cue."""

    def __init__(self, tokenizer: Callable[[str], Sequence[Mapping[str, Any]]], cache_limit: int = 64) -> None:
        self._tokenizer = tokenizer
        self._cache: OrderedDict[tuple, CueSnapshot] = OrderedDict()
        self._cache_limit = max(4, int(cache_limit))

    @staticmethod
    def _segments_key(segments) -> tuple:
        return tuple((_segment_base(segment), _segment_ruby(segment)) for segment in (segments or ()))

    @staticmethod
    def _normalize_source_tokens(text: str, values) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for value in values or ():
            if not isinstance(value, Mapping):
                continue
            try:
                start = int(value.get("start"))
                end = int(value.get("end"))
            except Exception:
                continue
            if start < 0 or end <= start or end > len(text):
                continue
            surface = str(value.get("surface") or text[start:end]).strip()
            if not surface:
                continue
            token = dict(value)
            token.update(
                {
                    "surface": surface,
                    "lookup": str(value.get("lookup") or surface).strip(),
                    "reading": str(value.get("reading") or "").strip(),
                    "start": start,
                    "end": end,
                }
            )
            output.append(token)
        return output

    @staticmethod
    def _freeze_segments(segments, line_index: int) -> tuple[CueSegment, ...]:
        output: list[CueSegment] = []
        cursor = 0
        for segment in segments or ():
            base = _segment_base(segment)
            if not base:
                continue
            start = cursor
            end = start + len(base)
            cursor = end
            meta = _segment_meta(segment)
            meta.update(
                {
                    "_cue_line_index": int(line_index),
                    "_cue_start": int(start),
                    "_cue_end": int(end),
                }
            )
            output.append(
                CueSegment(
                    line_index=int(line_index),
                    start=start,
                    end=end,
                    base=base,
                    ruby=_segment_ruby(segment),
                    meta=_freeze_meta(meta),
                )
            )
        return tuple(output)

    @staticmethod
    def _ruby_spans_for_token(token: Mapping[str, Any], segments: tuple[CueSegment, ...]) -> tuple[CueRubySpan, ...]:
        start = int(token.get("start") or 0)
        end = int(token.get("end") or start)
        spans = []
        for segment in segments:
            if not segment.ruby or segment.end <= start or segment.start >= end:
                continue
            spans.append(CueRubySpan(segment.start, segment.end, segment.base, segment.ruby))
        return tuple(spans)

    @staticmethod
    def _match_for_token(token: Mapping[str, Any], intervals) -> CueAnnotationMatch | None:
        start = int(token.get("start") or 0)
        end = int(token.get("end") or start)
        candidates = [
            (right - left, match)
            for left, right, match in intervals or ()
            if start >= int(left) and end <= int(right)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return CueAnnotationMatch.from_match(candidates[0][1])

    def build(
        self,
        *,
        cue_index: int,
        start_time: float,
        end_time: float,
        clean_text: str,
        top_segments,
        bottom_segments,
        annotation_provider=None,
    ) -> CueSnapshot:
        try:
            annotation_version = int(getattr(annotation_provider, "version", 0) or 0)
            annotation_enabled = bool(annotation_provider is not None and annotation_provider.enabled())
        except Exception:
            annotation_version = 0
            annotation_enabled = False

        cache_key = (
            int(cue_index),
            float(start_time),
            float(end_time),
            str(clean_text or ""),
            self._segments_key(top_segments),
            self._segments_key(bottom_segments),
            bool(annotation_enabled),
            int(annotation_version),
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached

        inputs = []
        if top_segments:
            inputs.append(("first", top_segments))
        if bottom_segments:
            inputs.append(("second", bottom_segments))
        if not inputs:
            clean_lines = [line for line in str(clean_text or "").splitlines() if line.strip()]
            for index, text in enumerate(clean_lines[:2]):
                position = "second" if len(clean_lines) == 1 or index == 1 else "first"
                inputs.append((position, [(text, None)]))

        lines: list[CueLine] = []
        cue_cursor = 0
        for line_index, (position, raw_segments) in enumerate(inputs):
            source_segments = self._freeze_segments(raw_segments, line_index)
            line_text = "".join(segment.base for segment in source_segments)
            try:
                raw_tokens = self._normalize_source_tokens(line_text, self._tokenizer(line_text))
            except Exception:
                raw_tokens = []

            annotated_legacy = [segment.as_legacy() for segment in source_segments]
            intervals = []
            if annotation_enabled:
                try:
                    annotated_legacy, _analysis_tokens, intervals = annotation_provider.analyze_segments(
                        annotated_legacy,
                        source_tokens=raw_tokens,
                    )
                except Exception:
                    annotated_legacy = [segment.as_legacy() for segment in source_segments]
                    intervals = []
            annotated_segments = self._freeze_segments(annotated_legacy, line_index)

            tokens: list[CueToken] = []
            for value in raw_tokens:
                start = int(value.get("start") or 0)
                end = int(value.get("end") or start)
                ruby_spans = self._ruby_spans_for_token(value, source_segments)
                ruby = "".join(span.ruby for span in ruby_spans)
                surface = str(value.get("surface") or line_text[start:end]).strip()
                lookup = str(value.get("lookup") or surface).strip()
                tokens.append(
                    CueToken(
                        line_index=line_index,
                        start=start,
                        end=end,
                        cue_start=cue_cursor + start,
                        cue_end=cue_cursor + end,
                        surface=surface,
                        lemma=str(value.get("lemma") or lookup).strip(),
                        pos1=str(value.get("pos1") or ""),
                        pos2=str(value.get("pos2") or ""),
                        reading=str(value.get("reading") or "").strip(),
                        ruby=ruby,
                        dictionary_lookup_key=lookup,
                        conjugation_type=str(value.get("c_type") or ""),
                        conjugation_form=str(value.get("c_form") or ""),
                        orthographic_base=str(value.get("orth_base") or "").strip(),
                        compound=bool(value.get("compound")),
                        honorific_name=bool(value.get("honorific_name")),
                        annotation=self._match_for_token(value, intervals),
                        ruby_spans=ruby_spans,
                    )
                )

            lines.append(
                CueLine(
                    index=line_index,
                    position=position,
                    cue_start=cue_cursor,
                    text=line_text,
                    segments=source_segments,
                    annotated_segments=annotated_segments,
                    tokens=tuple(tokens),
                )
            )
            cue_cursor += len(line_text) + 1

        snapshot = CueSnapshot(
            cue_index=int(cue_index),
            start_time=float(start_time),
            end_time=float(end_time),
            clean_text=str(clean_text or ""),
            lines=tuple(lines),
            annotation_version=int(annotation_version),
        )
        self._cache[cache_key] = snapshot
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)
        return snapshot

    def clear(self) -> None:
        self._cache.clear()
