"""
AnkiConnect client used to create notes from popup selections.
"""

from __future__ import annotations

from html import escape
import lzma
import os
import re
import tarfile
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET

import requests

try:
    from fugashi import Tagger
except Exception:
    Tagger = None


class AnkiClient:
    def __init__(self, config) -> None:
        self.config = config
        self.url = (self.config.get("ANKI_CONNECT_URL") or "http://127.0.0.1:8765").strip()
        self.http_timeout = float(self.config.get("ANKI_HTTP_TIMEOUT_SEC") or 4.0)

        self._model_fields_cache: Optional[set[str]] = None
        self._word_translation_cache: Dict[str, str] = {}
        self._sentence_translation_cache: Dict[str, str] = {}
        self._wadoku_index: Optional[Dict[str, List[str]]] = None

        self._tagger = None
        if Tagger is not None:
            try:
                self._tagger = Tagger()
            except Exception:
                self._tagger = None

        # Root deck + per-card target decks.
        self.deck_name = (self.config.get("ANKI_DECK") or "Japanese").strip()
        self.reading_deck = (self.config.get("ANKI_READING_DECK") or f"{self.deck_name}::Reading").strip()
        self.reverse_deck = (self.config.get("ANKI_REVERSE_DECK") or f"{self.deck_name}::DE -> JA").strip()

        # Note type + field names.
        self.model_name = (
            self.config.get("ANKI_MODEL") or "Standard (und umgekehrte Karte) Japanese"
        ).strip()
        self.front_field = (self.config.get("ANKI_FIELD_FRONT") or "Front").strip()
        self.back_field = (self.config.get("ANKI_FIELD_BACK") or "Back").strip()

        self.default_back = self.config.get("ANKI_DEFAULT_BACK") or ""
        self.translate_word_enabled = bool(
            self.config.get("ANKI_AUTO_TRANSLATE_WORD")
            if self.config.get("ANKI_AUTO_TRANSLATE_WORD") is not None
            else True
        )
        self.translate_sentence_enabled = bool(
            self.config.get("ANKI_AUTO_TRANSLATE_SENTENCE")
            if self.config.get("ANKI_AUTO_TRANSLATE_SENTENCE") is not None
            else True
        )
        self.wadoku_enabled = bool(self.config.get("ANKI_WADOKU_ENABLED"))
        self.wadoku_path = (self.config.get("ANKI_WADOKU_PATH") or "").strip()
        self.word_target_lang = (self.config.get("ANKI_WORD_TARGET_LANG") or "de").strip()
        self.sentence_target_lang = (self.config.get("ANKI_SENTENCE_TARGET_LANG") or "de").strip()
        self.word_translate_provider = self._normalize_translation_provider(
            self.config.get("ANKI_TRANSLATE_PROVIDER_WORD"),
            default="jisho",
        )
        self.sentence_translate_provider = self._normalize_translation_provider(
            self.config.get("ANKI_TRANSLATE_PROVIDER_SENTENCE"),
            default="google",
        )
        self.jisho_url = (self.config.get("ANKI_JISHO_URL") or "https://jisho.org/api/v1/search/words").strip()
        self.google_translate_url = (
            self.config.get("ANKI_GOOGLE_TRANSLATE_URL")
            or "https://translate.googleapis.com/translate_a/single"
        ).strip()
        self.deepl_translate_url = (
            self.config.get("ANKI_DEEPL_API_URL")
            or "https://api-free.deepl.com/v2/translate"
        ).strip()
        self.deepl_api_key = (self.config.get("ANKI_DEEPL_API_KEY") or "").strip()
        tags = self.config.get("ANKI_TAGS")
        self.tags = list(tags) if isinstance(tags, list) else ["subtitleplayer"]

    def is_enabled(self) -> bool:
        return bool(self.config.get("ANKI_ENABLED"))

    def ping(self) -> bool:
        try:
            result = self._invoke("version")
            return isinstance(result, int)
        except Exception:
            return False

    def add_from_selection(
        self,
        selection_text: str,
        subtitle_text: str = "",
    ) -> Dict:
        selected = (selection_text or "").strip()
        if not selected:
            raise ValueError("No selected text.")

        subtitle = (subtitle_text or "").strip()
        word_translation = self._translate_word(selected)
        sentence_translation = self._translate_sentence(subtitle)
        fields = self._build_note_fields(
            selected=selected,
            subtitle=subtitle,
            word_translation=word_translation,
            sentence_translation=sentence_translation,
        )

        self._ensure_deck(self.deck_name)
        self._ensure_deck(self.reading_deck)
        self._ensure_deck(self.reverse_deck)

        note = {
            "deckName": self.deck_name,
            "modelName": self.model_name,
            "fields": fields,
            "tags": self.tags,
            "options": {"allowDuplicate": True},
        }
        note_id = self._invoke("addNote", {"note": note})
        routed = self._route_new_cards(note_id)

        return {
            "note_id": note_id,
            "routed_cards": routed,
            "word_translation": word_translation,
            "sentence_translation": sentence_translation,
        }

    def _build_front_html(self, selected: str, subtitle: str) -> str:
        selected_furigana = self._to_furigana_brackets(
            selected,
            collapse_inline_reading=False,
            sentence_spacing=True,
        )
        sentence_furigana = self._to_furigana_brackets(
            subtitle,
            collapse_inline_reading=True,
            sentence_spacing=True,
        )

        parts = [f'<span class="kanji">{self._escape_multiline(selected_furigana)}</span>']
        if subtitle:
            parts.append(f'<span class="sentence">{self._escape_multiline(sentence_furigana)}</span>')
        return "<br>".join(parts)

    def _build_note_fields(
        self,
        selected: str,
        subtitle: str,
        word_translation: str,
        sentence_translation: str,
    ) -> Dict[str, str]:
        fields = {
            self.front_field: self._build_front_html(selected, subtitle),
            self.back_field: self._build_back_html(
                word_translation=word_translation,
                sentence_translation=sentence_translation,
            ),
        }

        model_fields = self._get_model_field_names()
        if self.front_field not in model_fields:
            raise RuntimeError(
                f'Anki model "{self.model_name}" has no field "{self.front_field}". '
                "Check ANKI_FIELD_FRONT in config.json."
            )
        if self.back_field not in model_fields:
            raise RuntimeError(
                f'Anki model "{self.model_name}" has no field "{self.back_field}". '
                "Check ANKI_FIELD_BACK in config.json."
            )
        return fields

    def _build_back_html(self, word_translation: str, sentence_translation: str) -> str:
        parts = []

        word_text = (word_translation or "").strip()
        sentence_text = (sentence_translation or "").strip()
        if word_text:
            parts.append(f"<span class='transl'>{self._escape_multiline(word_text)}</span>")
        if sentence_text:
            highlighted = self._sentence_with_highlight(sentence_text, word_text)
            parts.append(f"<span class='sentence2'>{highlighted}</span>")
        if not parts and self.default_back:
            parts.append(f'<span class="default-back">{self._escape_multiline(self.default_back)}</span>')
        return "<br>".join(parts).strip()

    def _escape_multiline(self, text: str) -> str:
        return escape(text).replace("\n", "<br>")

    def _sentence_with_highlight(self, sentence_text: str, word_translation: str) -> str:
        safe_sentence = self._escape_multiline(sentence_text)
        candidates = self._translation_candidates(word_translation)
        for candidate in candidates:
            safe_candidate = escape(candidate)
            if not safe_candidate:
                continue
            pattern = re.compile(re.escape(safe_candidate), re.IGNORECASE)
            if not pattern.search(safe_sentence):
                continue
            return pattern.sub(
                lambda m: f'<span class="highlight">{m.group(0)}</span>',
                safe_sentence,
            )

        # Fallback: if exact match fails, highlight a sentence word sharing a long prefix.
        sentence_words = re.findall(r"[^\W_]+", sentence_text, flags=re.UNICODE)
        best_word = ""
        best_score = 0
        for candidate in candidates:
            cand = candidate.casefold()
            for word in sentence_words:
                w = word.casefold()
                common = 0
                max_len = min(len(cand), len(w))
                while common < max_len and cand[common] == w[common]:
                    common += 1
                if common > best_score:
                    best_score = common
                    best_word = word
        if best_word and best_score >= 6:
            pattern = re.compile(re.escape(escape(best_word)), re.IGNORECASE)
            return pattern.sub(
                lambda m: f'<span class="highlight">{m.group(0)}</span>',
                safe_sentence,
                count=1,
            )
        return safe_sentence

    def _translation_candidates(self, word_translation: str) -> List[str]:
        raw = (word_translation or "").strip()
        if not raw:
            return []
        parts = [p.strip() for p in re.split(r"[;,/|]", raw) if p.strip()]
        candidates = []
        for part in parts:
            candidates.append(part)
            simplified = re.sub(r"\s*\(.*?\)\s*", " ", part).strip()
            if simplified and simplified != part:
                candidates.append(simplified)
        if not candidates:
            candidates = [raw]

        dedup = []
        seen = set()
        for item in sorted(candidates, key=len, reverse=True):
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        return dedup

    def _to_furigana_brackets(
        self,
        text: str,
        collapse_inline_reading: bool,
        sentence_spacing: bool,
    ) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        segments = self._tokenize_with_reading(
            text=text,
            collapse_inline_reading=collapse_inline_reading,
        )
        return self._segments_to_bracket_text(segments, sentence_spacing=sentence_spacing)

    def _segments_to_bracket_text(
        self,
        segments: List[tuple[str, Optional[str]]],
        sentence_spacing: bool,
    ) -> str:
        out: List[str] = []
        for i, (base, ruby) in enumerate(segments):
            if not base:
                continue
            prev_had_ruby = i > 0 and segments[i - 1][1] is not None
            if (
                sentence_spacing
                and ruby
                and len(base) == 1
                and self._contains_kanji(base)
                and out
                and not out[-1].endswith((" ", "\n", "\t"))
                and not out[-1].endswith(("[", "(", "{", "<"))
                and (not prev_had_ruby or not self._has_okurigana_continuation(segments, i))
            ):
                out.append(" ")
            if ruby:
                out.append(f"{escape(base)}[{escape(ruby)}]")
            else:
                out.append(escape(base))
        return "".join(out)

    def _has_okurigana_continuation(
        self,
        segments: List[tuple[str, Optional[str]]],
        idx: int,
    ) -> bool:
        if idx + 1 >= len(segments):
            return False
        next_base, next_ruby = segments[idx + 1]
        if next_ruby is not None:
            return False
        return bool(next_base) and self._starts_with_kana(next_base)

    def _tokenize_with_reading(
        self,
        text: str,
        collapse_inline_reading: bool,
    ) -> List[tuple[str, Optional[str]]]:
        if self._tagger is None:
            return [(text, None)]
        try:
            tokens = list(self._tagger(text))
        except Exception:
            return [(text, None)]
        if not tokens:
            return [(text, None)]

        surfaces_and_readings: List[tuple[str, str]] = []
        for token in tokens:
            surface = str(token.surface or "")
            if not surface:
                continue
            reading_hira = self._katakana_to_hiragana(self._token_reading(token))
            surfaces_and_readings.append((surface, reading_hira))

        segments: List[tuple[str, Optional[str]]] = []
        i = 0
        n = len(surfaces_and_readings)
        while i < n:
            surface, reading_hira = surfaces_and_readings[i]
            consumed_until = i

            # Convert inline subtitle style like "kanji+reading" into bracket furigana.
            if (
                collapse_inline_reading
                and reading_hira
                and self._contains_kanji(surface)
                and not self._looks_numeric(surface)
            ):
                merged = ""
                j = i + 1
                while j < n:
                    nxt_surface, _ = surfaces_and_readings[j]
                    if self._contains_kanji(nxt_surface):
                        break
                    if not nxt_surface:
                        break
                    merged += nxt_surface
                    if merged == reading_hira:
                        consumed_until = j
                        break
                    if not reading_hira.startswith(merged):
                        break
                    j += 1

            segments.extend(self._split_surface_and_reading(surface, reading_hira))
            i = consumed_until + 1

        return segments or [(text, None)]

    def _token_reading(self, token) -> str:
        feature = getattr(token, "feature", None)
        if feature is None:
            return ""
        for attr in ("kana", "pron", "pronBase", "form"):
            value = getattr(feature, attr, None)
            if value and value != "*":
                return str(value)
        return ""

    def _split_surface_and_reading(self, surface: str, reading_kata: str) -> List[tuple[str, Optional[str]]]:
        if not reading_kata:
            return [(surface, None)]

        reading = self._katakana_to_hiragana(reading_kata)
        if not reading:
            return [(surface, None)]
        if surface == reading:
            return [(surface, None)]
        if self._katakana_to_hiragana(surface) == reading:
            return [(surface, None)]
        if not self._contains_kanji(surface):
            return [(surface, None)]
        if self._looks_numeric(surface):
            return [(surface, None)]
        if self._is_all_kanji(surface) and len(surface) > 1:
            char_split = self._split_all_kanji_chars(surface, reading)
            if char_split:
                return char_split

        place_r = 0
        max_suffix = min(len(surface), len(reading)) - 1
        for i in range(1, max_suffix + 1):
            if surface[-i] != reading[-i]:
                break
            place_r = i

        place_l = 0
        max_prefix = min(len(surface) - 1, len(reading))
        for i in range(0, max_prefix):
            if surface[i] != reading[i]:
                break
            place_l = i + 1

        if place_l == 0 and place_r == 0:
            return [(surface, reading)]
        if place_l == 0:
            base = surface[:-place_r] if place_r else surface
            ruby = reading[:-place_r] if place_r else reading
            suffix = reading[-place_r:] if place_r else ""
            if not base or not ruby:
                return [(surface, reading)]
            out: List[tuple[str, Optional[str]]] = [(base, ruby)]
            if suffix:
                out.append((suffix, None))
            return out

        if place_r == 0:
            prefix = reading[:place_l]
            base = surface[place_l:]
            ruby = reading[place_l:]
            if not base or not ruby:
                return [(surface, reading)]
            out: List[tuple[str, Optional[str]]] = []
            if prefix:
                out.append((prefix, None))
            out.append((base, ruby))
            return out

        prefix = reading[:place_l]
        base = surface[place_l:-place_r]
        ruby = reading[place_l:-place_r]
        suffix = reading[-place_r:]
        if not base or not ruby:
            return [(surface, reading)]
        out: List[tuple[str, Optional[str]]] = []
        if prefix:
            out.append((prefix, None))
        out.append((base, ruby))
        if suffix:
            out.append((suffix, None))
        return out

    def _split_all_kanji_chars(
        self,
        surface: str,
        reading: str,
    ) -> List[tuple[str, Optional[str]]] | None:
        # Heuristic split: distribute the token reading across kanji characters.
        # This avoids standalone-kanji readings like 指[ゆび] for compounds such as 指示[しじ].
        if not surface or not reading:
            return None
        if not self._is_all_kanji(surface):
            return None

        moras = self._split_moras(reading)
        n = len(surface)
        if len(moras) < n:
            return None

        result: List[tuple[str, Optional[str]]] = []
        idx = 0
        for i, ch in enumerate(surface):
            remaining_moras = len(moras) - idx
            remaining_chars = n - i
            if remaining_chars <= 1:
                take = remaining_moras
            else:
                take = max(1, remaining_moras // remaining_chars)
            chunk = "".join(moras[idx: idx + take])
            idx += take
            if not chunk:
                return None
            result.append((ch, chunk))
        return result

    def _split_moras(self, reading: str) -> List[str]:
        small = set("ゃゅょぁぃぅぇぉゎゕゖっゝゞー")
        moras: List[str] = []
        for ch in reading:
            if ch in small and moras:
                moras[-1] += ch
            else:
                moras.append(ch)
        return moras

    def _katakana_to_hiragana(self, text: str) -> str:
        out = []
        for ch in text:
            code = ord(ch)
            if 0x30A1 <= code <= 0x30F6:
                out.append(chr(code - 0x60))
            else:
                out.append(ch)
        return "".join(out)

    def _contains_kanji(self, text: str) -> bool:
        for ch in text:
            code = ord(ch)
            if (0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF):
                return True
        return False

    def _is_all_kanji(self, text: str) -> bool:
        return bool(text) and all(self._contains_kanji(ch) for ch in text)

    def _starts_with_kana(self, text: str) -> bool:
        if not text:
            return False
        code = ord(text[0])
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)

    def _looks_numeric(self, text: str) -> bool:
        kansuji = "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u4e07\u5104\u5146\u3007\u96f6"
        full_width_digits = "".join(chr(cp) for cp in range(0xFF10, 0xFF1A))
        number_chars = set("0123456789" + full_width_digits + kansuji)
        cleaned = "".join(ch for ch in text if ch.strip())
        return bool(cleaned) and all(ch in number_chars for ch in cleaned)

    def _translate_word(self, text: str) -> str:
        text = (text or "").strip()
        if not text or not self.translate_word_enabled:
            return ""
        cached = self._word_translation_cache.get(text)
        if cached is not None:
            return cached

        provider = self.word_translate_provider
        translation = ""

        if provider in {"jisho", "auto"} and self.wadoku_enabled:
            translation = self._lookup_wadoku_local(text)

        if not translation:
            if provider == "jisho":
                translation = self._translate_word_with_jisho(text)
            elif provider == "deepl":
                translation = self._translate_deepl(
                    text,
                    source_lang="ja",
                    target_lang=self.word_target_lang,
                )
            elif provider == "google":
                translation = self._translate_google(
                    text,
                    source_lang="ja",
                    target_lang=self.word_target_lang,
                )
            else:
                translation = self._translate_word_with_jisho(text)
                if not translation:
                    translation = self._translate_deepl(
                        text,
                        source_lang="ja",
                        target_lang=self.word_target_lang,
                    )
                if not translation:
                    translation = self._translate_google(
                        text,
                        source_lang="ja",
                        target_lang=self.word_target_lang,
                    )

        # Extra fallback if selected provider cannot translate.
        if not translation and provider != "google":
            translation = self._translate_google(
                text,
                source_lang="ja",
                target_lang=self.word_target_lang,
            )

        self._word_translation_cache[text] = translation
        return translation

    def _translate_word_with_jisho(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        try:
            r = requests.get(
                self.jisho_url,
                params={"keyword": text},
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            payload = r.json()
            entries = payload.get("data") or []
            translation_en = self._extract_jisho_translation(text, entries)
        except Exception:
            translation_en = ""

        if not translation_en:
            return ""
        if self.word_target_lang.lower() == "en":
            return translation_en

        # Jisho is EN-focused, so bridge EN -> target via machine translation.
        translation = self._translate_deepl(
            translation_en,
            source_lang="en",
            target_lang=self.word_target_lang,
        )
        if not translation:
            translation = self._translate_google(
                translation_en,
                source_lang="en",
                target_lang=self.word_target_lang,
            )
        return translation or translation_en

    def _lookup_wadoku_local(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        index = self._get_wadoku_index()
        if not index:
            return ""

        candidates = [
            text,
            self._normalize_lookup_key(text),
            re.sub(r"\[[^\]]+\]", "", text).strip(),
        ]
        for candidate in candidates:
            key = self._normalize_lookup_key(candidate)
            if not key:
                continue
            values = index.get(key)
            if values:
                return "; ".join(values[:3])
        return ""

    def _get_wadoku_index(self) -> Dict[str, List[str]]:
        if self._wadoku_index is not None:
            return self._wadoku_index

        path = self.wadoku_path
        if not path or not os.path.exists(path):
            self._wadoku_index = {}
            return self._wadoku_index

        index: Dict[str, List[str]] = {}
        try:
            for line in self._iter_wadoku_lines(path):
                parsed = self._parse_wadoku_xml_line(line)
                if not parsed:
                    parsed = self._parse_wadoku_edict_line(line)
                if not parsed:
                    continue
                keys, gloss = parsed
                for key in keys:
                    self._add_wadoku_entry(index, key, gloss)
        except Exception:
            # Keep lookup non-fatal and fall back to online services.
            index = {}

        self._wadoku_index = index
        return self._wadoku_index

    def _iter_wadoku_lines(self, path: str):
        lower = path.lower()
        if tarfile.is_tarfile(path):
            with tarfile.open(path, "r:*") as tf:
                for member in tf.getmembers():
                    if not member.isfile():
                        continue
                    name = member.name.lower()
                    if "edict" not in name and not name.endswith((".txt", ".u8", ".utf8", ".xml")):
                        continue
                    extracted = tf.extractfile(member)
                    if extracted is None:
                        continue
                    for raw in extracted:
                        yield raw.decode("utf-8", errors="ignore")
                    return
        elif lower.endswith(".xz"):
            with lzma.open(path, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    yield line
        else:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    yield line

    def _parse_wadoku_edict_line(self, line: str):
        row = (line or "").strip()
        if not row or row.startswith("#"):
            return None
        if "/" not in row:
            return None

        head, tail = row.split("/", 1)
        head = head.strip()
        tail = tail.strip()
        if not head:
            return None

        head = re.sub(r"^\d+\s+", "", head)
        head = re.sub(r"\s+\([^)]+\)\s*$", "", head)

        readings = [r.strip() for r in re.findall(r"\[([^\]]+)\]", head) if r.strip()]
        orth_part = re.sub(r"\[[^\]]+\]", "", head).strip()
        orths = [o.strip() for o in re.split(r"[;,]", orth_part) if o.strip()]
        keys = orths + readings
        if not keys:
            return None

        glosses = []
        for chunk in tail.split("/"):
            c = chunk.strip()
            if not c:
                continue
            c = re.sub(r"^EntL\d+\s*", "", c)
            c = re.sub(r"^\([^)]+\)\s*", "", c)
            c = re.sub(r"\s*\([^)]+\)\s*$", "", c)
            c = c.strip()
            if not c:
                continue
            glosses.append(c)
            if len(glosses) >= 6:
                break
        if not glosses:
            return None

        return keys, "; ".join(glosses)

    def _parse_wadoku_xml_line(self, line: str):
        row = (line or "").strip()
        if not row.startswith("<entry"):
            return None
        try:
            root = ET.fromstring(row)
        except Exception:
            return None

        ns = {"w": "http://www.wadoku.de/xml/entry"}
        orths = []
        for node in root.findall(".//w:form/w:orth", ns):
            txt = "".join(node.itertext()).strip()
            txt = re.sub(r"\(([^)]*)\)", r"\1", txt)
            txt = txt.replace("△", "").replace("×", "")
            txt = txt.strip()
            if txt:
                orths.append(txt)

        readings = []
        for node in root.findall(".//w:form/w:reading/w:hira", ns):
            txt = "".join(node.itertext()).strip()
            if txt:
                readings.append(txt)

        keys = orths + readings
        if not keys:
            return None

        glosses = []
        for node in root.findall(".//w:sense/w:trans/w:tr", ns):
            txt = "".join(node.itertext()).strip()
            txt = re.sub(r"\s+", " ", txt)
            txt = txt.strip()
            if txt and txt not in glosses:
                glosses.append(txt)
            if len(glosses) >= 6:
                break
        if not glosses:
            return None

        return keys, "; ".join(glosses)

    def _add_wadoku_entry(self, index: Dict[str, List[str]], key: str, gloss: str) -> None:
        normalized = self._normalize_lookup_key(key)
        if not normalized:
            return
        entries = index.setdefault(normalized, [])
        if gloss not in entries:
            entries.append(gloss)

    def _normalize_lookup_key(self, text: str) -> str:
        t = (text or "").strip()
        t = t.replace("\u3000", " ")
        t = re.sub(r"\s+", "", t)
        return t

    def _extract_jisho_translation(self, query: str, entries: List[Dict]) -> str:
        if not entries:
            return ""
        best = entries[0]
        best_score = -1
        for entry in entries:
            score = 0
            for jp in entry.get("japanese") or []:
                word = str(jp.get("word") or "")
                reading = str(jp.get("reading") or "")
                if word == query:
                    score += 100
                if reading == query:
                    score += 80
                if query and query in word:
                    score += 30
                if query and query in reading:
                    score += 20
            if score > best_score:
                best = entry
                best_score = score

        definitions: List[str] = []
        for sense in best.get("senses") or []:
            for item in sense.get("english_definitions") or []:
                gloss = str(item).strip()
                if gloss and gloss not in definitions:
                    definitions.append(gloss)
            if len(definitions) >= 6:
                break
        if not definitions:
            return ""
        return "; ".join(definitions[:6])

    def _translate_sentence(self, text: str) -> str:
        text = (text or "").strip()
        if not text or not self.translate_sentence_enabled:
            return ""
        cached = self._sentence_translation_cache.get(text)
        if cached is not None:
            return cached
        provider = self.sentence_translate_provider
        if provider == "deepl":
            translated = self._translate_deepl(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
            if not translated:
                translated = self._translate_google(
                    text,
                    source_lang="ja",
                    target_lang=self.sentence_target_lang,
                )
        elif provider == "google":
            translated = self._translate_google(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
        else:
            translated = self._translate_deepl(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
            if not translated:
                translated = self._translate_google(
                    text,
                    source_lang="ja",
                    target_lang=self.sentence_target_lang,
                )
        self._sentence_translation_cache[text] = translated
        return translated

    def _translate_google(self, text: str, source_lang: str, target_lang: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        try:
            r = requests.get(
                self.google_translate_url,
                params={
                    "client": "gtx",
                    "sl": source_lang,
                    "tl": target_lang,
                    "dt": "t",
                    "q": text,
                },
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            payload = r.json()
            translated = ""
            chunks = payload[0] if isinstance(payload, list) and payload else []
            for chunk in chunks:
                if isinstance(chunk, list) and chunk:
                    translated += str(chunk[0] or "")
            translated = translated.strip()
        except Exception:
            translated = ""
        return translated

    def _translate_deepl(self, text: str, source_lang: str, target_lang: str) -> str:
        text = (text or "").strip()
        if not text or not self.deepl_api_key:
            return ""
        source = self._normalize_deepl_source_lang(source_lang)
        target = self._normalize_deepl_target_lang(target_lang)
        if not target:
            return ""
        payload = {
            "auth_key": self.deepl_api_key,
            "text": text,
            "target_lang": target,
        }
        if source:
            payload["source_lang"] = source

        try:
            r = requests.post(
                self.deepl_translate_url,
                data=payload,
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            data = r.json()
            translations = data.get("translations") or []
            if not translations:
                return ""
            translated = str((translations[0] or {}).get("text") or "").strip()
            return translated
        except Exception:
            return ""

    def _normalize_translation_provider(self, value: Optional[str], default: str) -> str:
        provider = str(value or default).strip().lower()
        if provider not in {"jisho", "google", "deepl", "auto"}:
            return default
        return provider

    def _normalize_deepl_source_lang(self, lang: str) -> str:
        code = str(lang or "").strip().replace("_", "-").upper()
        if not code:
            return ""
        if "-" in code:
            code = code.split("-", 1)[0]
        if len(code) > 2:
            code = code[:2]
        return code

    def _normalize_deepl_target_lang(self, lang: str) -> str:
        code = str(lang or "").strip().replace("_", "-").upper()
        if not code:
            return ""
        allowed = {
            "AR", "BG", "CS", "DA", "DE", "EL", "EN", "EN-GB", "EN-US", "ES",
            "ET", "FI", "FR", "HU", "ID", "IT", "JA", "KO", "LT", "LV", "NB",
            "NL", "PL", "PT", "PT-BR", "PT-PT", "RO", "RU", "SK", "SL", "SV",
            "TR", "UK", "ZH", "ZH-HANS", "ZH-HANT",
        }
        if code in allowed:
            return code
        if "-" in code:
            base = code.split("-", 1)[0]
            if base in allowed:
                return base
        if len(code) >= 2:
            short = code[:2]
            if short in allowed:
                return short
        return ""

    def _route_new_cards(self, note_id: int) -> Dict[str, List[int]]:
        routed = {"reading": [], "reverse": [], "unrouted": []}
        if not note_id:
            return routed

        card_ids = self._invoke("findCards", {"query": f"nid:{note_id}"}) or []
        if not card_ids:
            return routed

        info = self._invoke("cardsInfo", {"cards": card_ids}) or []
        to_reading = []
        to_reverse = []
        for card in info:
            card_id = card.get("cardId")
            ord_index = card.get("ord")
            if card_id is None:
                continue
            if ord_index == 0:
                to_reading.append(card_id)
            elif ord_index == 1:
                to_reverse.append(card_id)
            else:
                routed["unrouted"].append(card_id)

        if to_reading:
            self._invoke("changeDeck", {"cards": to_reading, "deck": self.reading_deck})
            routed["reading"].extend(to_reading)
        if to_reverse:
            self._invoke("changeDeck", {"cards": to_reverse, "deck": self.reverse_deck})
            routed["reverse"].extend(to_reverse)
        return routed

    def _ensure_deck(self, deck_name: str) -> None:
        if not deck_name:
            return
        self._invoke("createDeck", {"deck": deck_name})

    def _get_model_field_names(self) -> set[str]:
        if self._model_fields_cache is None:
            names = self._invoke("modelFieldNames", {"modelName": self.model_name}) or []
            self._model_fields_cache = set(str(name) for name in names)
        return self._model_fields_cache

    def _invoke(self, action: str, params: Optional[Dict] = None):
        payload = {
            "action": action,
            "version": 6,
            "params": params or {},
        }
        response = requests.post(self.url, json=payload, timeout=self.http_timeout)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data.get("result")
