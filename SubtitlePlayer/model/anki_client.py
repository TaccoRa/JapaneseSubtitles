"""
AnkiConnect client used to create notes from popup selections.
"""

from __future__ import annotations
import base64
import functools
import os
import re
import sys
from html import escape
from typing import Dict, List, Optional

import requests

try:
    from SubtitlePlayer.furigana_splitter import split_furigana, split_moras
except ImportError:
    try:
        from furigana_splitter import split_furigana, split_moras
    except ImportError:
        _package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _package_dir not in sys.path:
            sys.path.insert(0, _package_dir)
        from furigana_splitter import split_furigana, split_moras

try:
    from fugashi import Tagger
except Exception:
    Tagger = None


class AnkiClient:
    DEFAULT_JISHO_URL = "https://jisho.org/api/v1/search/words"
    DEFAULT_GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
    DEFAULT_DEEPL_URL = "https://api-free.deepl.com/v2/translate"
    DEFAULT_STROKE_SVG_BASE_URL = "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji"
    DEFAULT_STROKE_MEDIA_PREFIX = "stroke_"

    def __init__(self, config) -> None:
        self.config = config
        self.url = (self.config.get("ANKI_CONNECT_URL") or "http://127.0.0.1:8765").strip()
        self.http_timeout = float(self.config.get("ANKI_HTTP_TIMEOUT_SEC") or 4.0)

        self._model_fields_cache: Optional[set[str]] = None
        self._word_translation_cache: Dict[str, str] = {}
        self._word_definition_cache: Dict[str, str] = {}
        self._sentence_translation_cache: Dict[str, str] = {}
        self._jisho_entries_cache: Dict[str, List[Dict]] = {}
        self._dictionary_hover_cache: Dict[tuple[str, bool], str] = {}
        self._stroke_media_exists_cache: Dict[str, bool] = {}
        self._stroke_sync_failed_cache: set[str] = set()

        # when using Jisho we keep the full english definition list here so the
        # note builder can put the full string into the "Definition" field
        self._last_jisho_full_definition: str = ""

        self._tagger = None
        if Tagger is not None:
            try:
                self._tagger = Tagger()
            except Exception:
                self._tagger = None

        # Deck/model defaults are hardcoded; config can still override if needed.
        self.deck_name = (self.config.get("ANKI_DECK") or "Japanese").strip()
        self.reading_deck = (self.config.get("ANKI_READING_DECK") or f"{self.deck_name}::Reading").strip()
        self.reverse_deck = (self.config.get("ANKI_REVERSE_DECK") or f"{self.deck_name}::DE -> JA").strip()
        self.model_name = (
            self.config.get("ANKI_MODEL") or "Standard (und umgekehrte Karte) Japanese"
        ).strip()

        self.add_rubies_to_front_field = (
            self.config.get("ANKI_FIELD_ADD_RUBIES_FRONT") or "AddRubiesToFront"
        ).strip()
        self.front_field = (self.config.get("ANKI_FIELD_FRONT") or "Front").strip()
        self.back_field = (self.config.get("ANKI_FIELD_BACK") or "Back").strip()
        self.sentence_ja_field = (
            self.config.get("ANKI_FIELD_SENTENCE_JA") or "SentenceJA"
        ).strip()
        self.sentence_de_field = (
            self.config.get("ANKI_FIELD_SENTENCE_DE") or "SentenceDE"
        ).strip()
        self.sound_field = (self.config.get("ANKI_FIELD_SOUND") or "Sound").strip()
        self.image_field = (self.config.get("ANKI_FIELD_IMAGE") or "Image").strip()
        self.add_rubies_to_sentence_ja_field = (
            self.config.get("ANKI_FIELD_ADD_RUBIES_SENTENCE_JA") or "AddRubiesToSentenceJA"
        ).strip()
        self.definition_field = (
            self.config.get("ANKI_FIELD_DEFINITION") or "Definition"
        ).strip()

        self.sentence_target_lang = (self.config.get("ANKI_SENTENCE_TARGET_LANG") or "de").strip()
        self.word_target_lang = (self.config.get("ANKI_WORD_TARGET_LANG") or "de").strip()

        # Prefer Jisho for single-word meanings; keep DeepL for sentence translation.
        self.word_translate_provider = "jisho"
        self.sentence_translate_provider = "deepl"

        self.jisho_url = self.DEFAULT_JISHO_URL
        self.google_translate_url = self.DEFAULT_GOOGLE_TRANSLATE_URL
        self.deepl_translate_url = self.DEFAULT_DEEPL_URL
        self.stroke_svg_base_url = (
            self.config.get("ANKI_STROKE_SVG_BASE_URL") or self.DEFAULT_STROKE_SVG_BASE_URL
        ).strip()
        self.stroke_media_prefix = (
            self.config.get("ANKI_STROKE_MEDIA_PREFIX") or self.DEFAULT_STROKE_MEDIA_PREFIX
        ).strip() or self.DEFAULT_STROKE_MEDIA_PREFIX
        stroke_auto_sync = self.config.get("ANKI_STROKE_AUTO_SYNC")
        self.stroke_auto_sync = True if stroke_auto_sync is None else bool(stroke_auto_sync)
        try:
            self.stroke_download_timeout = float(
                self.config.get("ANKI_STROKE_DOWNLOAD_TIMEOUT_SEC") or max(self.http_timeout, 20.0)
            )
        except Exception:
            self.stroke_download_timeout = max(self.http_timeout, 20.0)

        # Keep API key out of config when sharing repo.
        self.deepl_api_key = (
            os.environ.get("DEEPL_TOKEN")
            or os.environ.get("DEEPL_AUTH_KEY")
            or os.environ.get("DEEPL_API_KEY")
            or ""
        ).strip()
        enabled = self.config.get("ANKI_ENABLED")
        self.enabled = True if enabled is None else bool(enabled)
        tags = self.config.get("ANKI_TAGS")
        if tags is None:
            self.tags = ["subtitleplayer"]
        elif isinstance(tags, list):
            self.tags = [str(t).strip() for t in tags if str(t).strip()]
        elif isinstance(tags, str):
            self.tags = [t.strip() for t in tags.replace(";", ",").split(",") if t.strip()]
        else:
            self.tags = []

    def is_enabled(self) -> bool:
        return self.enabled

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
        full_definition = self._last_jisho_full_definition
        translation_candidates = self._collect_translation_candidates(selected, subtitle)
        self._last_jisho_full_definition = full_definition
        word_provider_used = self._detect_translation_provider(
            chosen=word_translation,
            candidates=translation_candidates.get("word", {}),
            configured=self.word_translate_provider,
        )
        sentence_provider_used = self._detect_translation_provider(
            chosen=sentence_translation,
            candidates=translation_candidates.get("sentence", {}),
            configured=self.sentence_translate_provider,
        )
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
        # stroke_sync = self._sync_missing_stroke_svgs_for_new_note(
        #     selected_text=selected,
        #     fields=fields,
        # )

        return {
            "note_id": note_id,
            "routed_cards": routed,
            "word_translation": word_translation,
            "sentence_translation": sentence_translation,
            # "stroke_svg_sync": stroke_sync,
            "translation_candidates": translation_candidates,
            "translation_provider_used": {
                "word": word_provider_used,
                "sentence": sentence_provider_used,
            },
            "stroke_svg_sync_fields": fields,
        }


    def sync_missing_stroke_svgs_async(self, selected_text: str, fields: Dict[str, str]):
        import threading

        thread = threading.Thread(
            target=self._sync_missing_stroke_svgs_for_new_note,
            args=(selected_text, fields),
            daemon=True,
        )
        thread.start()

    def _build_note_fields(
        self,
        selected: str,
        subtitle: str,
        word_translation: str,
        sentence_translation: str,
    ) -> Dict[str, str]:
        model_fields = self._get_model_field_names()
        fields: Dict[str, str] = {}

        missing_required = [
            name
            for name in (
                self.add_rubies_to_front_field,
                self.front_field,
                self.back_field,
                self.sentence_ja_field,
                self.sentence_de_field,
                self.add_rubies_to_sentence_ja_field,
            )
            if name not in model_fields
        ]
        if missing_required:
            raise RuntimeError(
                f'Anki model "{self.model_name}" is missing fields: {", ".join(missing_required)}.'
            )

        selected_raw = (selected or "").strip()
        subtitle_raw = (subtitle or "").strip()
        selected_with_rubies = self._to_furigana_brackets(
            selected_raw,
            collapse_inline_reading=False,
            sentence_spacing=False,
        )
        sentence_with_rubies = self._to_furigana_brackets(
            subtitle_raw,
            collapse_inline_reading=True,
            sentence_spacing=True,
        )

        back_value = self._dedupe_translation_entries(word_translation)
        sentence_de = (sentence_translation).strip()

        fields[self.add_rubies_to_front_field] = selected_raw
        fields[self.front_field] = selected_with_rubies or selected_raw
        fields[self.back_field] = back_value
        fields[self.sentence_ja_field] = sentence_with_rubies
        fields[self.sentence_de_field] = sentence_de
        fields[self.add_rubies_to_sentence_ja_field] = subtitle_raw
        if self.definition_field in model_fields:
            fields[self.definition_field] = self._dedupe_translation_entries(self._last_jisho_full_definition)

        # Optional media fields stay empty by design; they are filled by capture pipeline later.
        if self.sound_field in model_fields:
            fields[self.sound_field] = ""
        if self.image_field in model_fields:
            fields[self.image_field] = ""

        return fields

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
            if (
                ruby
                and self._starts_with_kanji(base)
                and out
                and not out[-1].endswith((" ", "\n", "\t"))
                and not out[-1].endswith(("[", "(", "{", "<", "\u300c", "\u300e"))
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
        if not self._split_kanji_moras_enabled():
            return [(surface, reading)]
        return split_furigana(surface, reading, self._single_kanji_reading_for_split)

    def _split_kanji_moras_enabled(self) -> bool:
        try:
            return bool(self.config.get("ANKI_SPLIT_KANJI_MORAS") or False)
        except Exception:
            return False

    def _single_kanji_reading_for_split(self, ch: str) -> str:
        if self._tagger is None or not self._is_kanji_char(ch):
            return ""
        try:
            tokens = list(self._tagger(ch))
        except Exception:
            return ""
        if len(tokens) != 1:
            return ""
        token = tokens[0]
        if str(getattr(token, "surface", "") or "") != ch:
            return ""
        return self._katakana_to_hiragana(self._token_reading(token))

    def _split_all_kanji_chars(
        self,
        surface: str,
        reading: str,
    ) -> List[tuple[str, Optional[str]]] | None:
        if not surface or not reading:
            return None
        if not self._is_all_kanji(surface):
            return None

        reading = self._katakana_to_hiragana(reading)
        moras = self._split_moras(reading)
        n_chars = len(surface)
        n_moras = len(moras)

        if n_moras < n_chars:
            return None

        def score_take(length: int, is_last: bool) -> float:
            if length == 1:
                score = 2.0
            elif length == 2:
                score = 5.0
            elif length == 3:
                score = 4.0
            elif length == 4:
                score = 2.0
            else:
                score = 1.0 - 0.5 * (length - 4)

            if is_last and length == 1:
                score -= 4.0
            return score

        def materialize(lengths: tuple[int, ...]) -> List[tuple[str, Optional[str]]]:
            out: List[tuple[str, Optional[str]]] = []
            idx = 0
            for ch, take in zip(surface, lengths):
                chunk = "".join(moras[idx:idx + take])
                if not chunk:
                    return []
                out.append((ch, chunk))
                idx += take
            return out

        # Special handling for 々
        if n_chars == 2 and surface[1] == "々":
            first_len = max(1, n_moras // 2)
            first = "".join(moras[:first_len])
            if not first:
                return None
            return [(surface[0], first), ("々", first)]

        # Strong, explicit rules for 2-kanji compounds
        if n_chars == 2:
            if n_moras == 2:
                return materialize((1, 1))
            if n_moras == 3:
                return materialize((1, 2))
            if n_moras == 4:
                return materialize((2, 2))
            if n_moras == 5:
                return materialize((2, 3))

        @functools.lru_cache(maxsize=None)
        def best(start: int, parts_left: int) -> tuple[float, tuple[int, ...]] | None:
            if parts_left == 1:
                take = n_moras - start
                if take < 1:
                    return None
                return score_take(take, True), (take,)

            best_score: float | None = None
            best_lengths: tuple[int, ...] | None = None

            max_take = n_moras - start - (parts_left - 1)
            if max_take < 1:
                return None

            for take in range(1, max_take + 1):
                rest = best(start + take, parts_left - 1)
                if rest is None:
                    continue

                rest_score, rest_lengths = rest
                score = score_take(take, False) + rest_score

                if rest_lengths:
                    if take <= rest_lengths[0]:
                        score += 0.8
                    else:
                        score -= 0.8

                if best_score is None or score > best_score:
                    best_score = score
                    best_lengths = (take,) + rest_lengths

            if best_score is None or best_lengths is None:
                return None
            return best_score, best_lengths

        result = best(0, n_chars)
        if result is None:
            return None

        _, lengths = result
        if len(lengths) != n_chars:
            return None

        return materialize(lengths)

    def _split_moras(self, reading: str) -> List[str]:
        return split_moras(reading)

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
        return any(self._is_kanji_char(ch) for ch in text or "")

    def _starts_with_kanji(self, text: str) -> bool:
        return bool(text) and self._is_kanji_char(text[0])

    def _is_all_kanji(self, text: str) -> bool:
        return bool(text) and all(self._is_kanji_char(ch) for ch in text)

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
        self._last_jisho_full_definition = ""
        text = (text or "").strip()
        if not text:
            return ""
        cached = self._word_translation_cache.get(text)
        if cached is not None:
            self._last_jisho_full_definition = self._word_definition_cache.get(text, "")
            return cached
        
        translation = self._translate_word_with_jisho(text)
        if not translation:
            translation = self._translate_google(text, source_lang="ja", target_lang=self.word_target_lang)
        translation = self._dedupe_translation_entries(translation)
        self._word_translation_cache[text] = translation
        self._word_definition_cache[text] = self._last_jisho_full_definition
        return translation

    def _translate_word_with_jisho(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        entries = self._fetch_jisho_entries(text)
        definitions = self._extract_jisho_translation(text, entries)
        # definitions is now a list of strings (may be empty)
        if not definitions:
            return ""
        self._last_jisho_full_definition = self._dedupe_translation_entries(", ".join(definitions))
        summary_en = self._dedupe_translation_entries(", ".join(definitions[:3]))
        if self.word_target_lang.lower() == "en":
            return summary_en

        translated = self._translate_deepl(
            summary_en,
            source_lang="en",
            target_lang=self.word_target_lang,
        )
        if not translated:
            translated = self._translate_google(
                summary_en,
                source_lang="en",
                target_lang=self.word_target_lang,
            )
        return self._dedupe_translation_entries(translated or summary_en)

    def _fetch_jisho_entries(self, text: str) -> List[Dict]:
        text = (text or "").strip()
        if not text:
            return []
        cached = self._jisho_entries_cache.get(text)
        if cached is not None:
            return cached

        entries: List[Dict] = []
        try:
            r = requests.get(
                self.jisho_url,
                params={"keyword": text},
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                entries = data
        except Exception:
            entries = []

        self._jisho_entries_cache[text] = entries
        return entries

    def _extract_jisho_translation(self, query: str, entries: List[Dict]) -> List[str]:
        if not entries:
            return []

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
        seen_definition_keys: set[str] = set()
        for sense in best.get("senses") or []:
            for item in sense.get("english_definitions") or []:
                gloss = str(item).strip()
                key = self._translation_dedupe_key(gloss)
                if gloss and key and key not in seen_definition_keys:
                    seen_definition_keys.add(key)
                    definitions.append(gloss)
            if len(definitions) >= 6:
                break
        return definitions

    def lookup_dictionary_entry(self, text: str, allow_translation_fallback: bool = False) -> str:
        """
        Return a compact, readable Jisho-style dictionary entry for hover tooltips.
        Reuses the same Jisho lookup and definition extraction path used by card creation.
        """
        query = (text or "").strip()
        if not query:
            return ""
        cache_key = (query, bool(allow_translation_fallback))
        cached = self._dictionary_hover_cache.get(cache_key)
        if cached is not None:
            return cached

        for lookup_query in self._dictionary_lookup_queries(query):
            entries = self._fetch_jisho_entries(lookup_query)
            definitions = self._extract_jisho_translation(lookup_query, entries)
            if definitions:
                result = self._format_jisho_hover_entry(lookup_query, entries, definitions)
                self._cache_dictionary_hover_result(cache_key, result)
                return result

        if allow_translation_fallback:
            translated = self._translate_sentence(query)
            if translated and translated.strip() and translated.strip() != query:
                result = f"{query} — {translated.strip()}"
                self._cache_dictionary_hover_result(cache_key, result)
                return result

        self._cache_dictionary_hover_result(cache_key, "")
        return ""

    def _cache_dictionary_hover_result(self, key: tuple[str, bool], value: str) -> None:
        self._dictionary_hover_cache[key] = value
        if len(self._dictionary_hover_cache) > 512:
            try:
                oldest = next(iter(self._dictionary_hover_cache))
                self._dictionary_hover_cache.pop(oldest, None)
            except Exception:
                pass

    def translate_hover_selection(self, text: str, provider: str = "deepl") -> str:
        query = self._normalize_translation_input(text)
        if not query:
            return ""
        provider = str(provider or "deepl").strip().lower()
        if provider == "google":
            translated = self._translate_google(
                query,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
        else:
            translated = self._translate_deepl(
                query,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
        if translated and translated.strip() and translated.strip() != query:
            return f"{query} — {translated.strip()}"
        return ""

    def _dictionary_lookup_queries(self, query: str) -> List[str]:
        query = (query or "").strip()
        if not query:
            return []
        queries = [query]
        compact = re.sub(r"\s+", "", query)
        if compact and compact != query and self._contains_japanese(compact):
            queries.append(compact)
        return queries

    def _format_jisho_hover_entry(self, query: str, entries: List[Dict], definitions: List[str]) -> str:
        head = query
        try:
            best = entries[0]
            for entry in entries:
                if self._extract_jisho_translation(query, [entry]) == definitions:
                    best = entry
                    break
            jap = (best.get("japanese") or [{}])[0] if isinstance(best.get("japanese"), list) else {}
            word = str(jap.get("word") or "").strip()
            reading = str(jap.get("reading") or "").strip()
            if word and reading and word != reading:
                head = f"{word} [{reading}]"
            elif word:
                head = word
            elif reading:
                head = reading
        except Exception:
            pass
        return f"{head} — {', '.join(definitions[:6])}"

    def word_spans(self, text: str) -> List[Dict[str, object]]:
        """
        Return word-like spans for hover lookup.
        Spans are character offsets into the original text and preserve the surface form.
        """
        source = str(text or "")
        if not source:
            return []

        if self._tagger is not None:
            try:
                tokens = list(self._tagger(source))
            except Exception:
                tokens = []
            spans: List[Dict[str, object]] = []
            cursor = 0
            for token in tokens:
                surface = str(getattr(token, "surface", "") or "")
                if not surface:
                    continue
                start = source.find(surface, cursor)
                if start < 0:
                    start = source.find(surface)
                if start < 0:
                    continue
                end = start + len(surface)
                cursor = end
                if not self._is_lookup_candidate(surface):
                    continue
                reading = self._katakana_to_hiragana(self._token_reading(token))
                lookup = self._token_lookup_text(token, surface)
                feature = getattr(token, "feature", None)
                spans.append(
                    {
                        "surface": surface,
                        "lookup": lookup or surface,
                        "reading": reading,
                        "start": start,
                        "end": end,
                        "pos1": str(getattr(feature, "pos1", "") or ""),
                        "pos2": str(getattr(feature, "pos2", "") or ""),
                    }
                )
            if spans:
                return self._expand_compound_word_spans(source, spans)

        return [
            {
                "surface": match.group(0),
                "lookup": match.group(0),
                "reading": "",
                "start": match.start(),
                "end": match.end(),
            }
            for match in re.finditer(r"\S+", source)
            if self._is_lookup_candidate(match.group(0))
        ]

    def _expand_compound_word_spans(
        self,
        source: str,
        spans: List[Dict[str, object]],
    ) -> List[Dict[str, object]]:
        compounds: List[Dict[str, object]] = []
        existing = {
            (int(span.get("start") or 0), int(span.get("end") or 0), str(span.get("lookup") or ""))
            for span in spans
        }

        max_parts = 4
        for i in range(len(spans)):
            if not self._compound_part_allowed(spans[i]):
                continue
            start = int(spans[i].get("start") or 0)
            end = int(spans[i].get("end") or 0)
            readings = [str(spans[i].get("reading") or "")]
            for j in range(i + 1, min(len(spans), i + max_parts)):
                next_span = spans[j]
                next_start = int(next_span.get("start") or 0)
                next_end = int(next_span.get("end") or 0)
                if source[end:next_start] != "":
                    break
                if not self._compound_part_allowed(next_span):
                    break
                end = next_end
                readings.append(str(next_span.get("reading") or ""))
                surface = source[start:end]
                lookup = re.sub(r"\s+", "", surface).strip()
                if len(lookup) < 2 or not self._contains_kanji(lookup):
                    continue
                key = (start, end, lookup)
                if key in existing:
                    continue
                existing.add(key)
                compounds.append(
                    {
                        "surface": surface,
                        "lookup": lookup,
                        "reading": "".join(readings).strip(),
                        "start": start,
                        "end": end,
                        "compound": True,
                    }
                )

        compounds.sort(
            key=lambda span: (
                int(span.get("start") or 0),
                -(int(span.get("end") or 0) - int(span.get("start") or 0)),
            )
        )
        base_spans = sorted(
            spans,
            key=lambda span: (
                int(span.get("start") or 0),
                int(span.get("end") or 0) - int(span.get("start") or 0),
            ),
        )
        return compounds + base_spans

    def _compound_part_allowed(self, span: Dict[str, object]) -> bool:
        surface = str(span.get("surface") or "").strip()
        if not surface or not self._is_lookup_candidate(surface):
            return False
        if surface in {
            "は", "が", "を", "に", "へ", "で", "と", "も", "の", "や", "か",
            "から", "まで", "より", "ね", "よ", "ぞ", "な",
        }:
            return False

        pos1 = str(span.get("pos1") or "")
        if pos1:
            return pos1 in {"名詞", "接頭辞", "接尾辞"}

        return self._contains_kanji(surface)

    def _token_lookup_text(self, token, surface: str) -> str:
        feature = getattr(token, "feature", None)
        if feature is not None:
            for attr in ("lemma", "orthBase", "formBase"):
                value = getattr(feature, attr, None)
                if value and value != "*":
                    text = str(value).strip()
                    if text:
                        return text
        return surface

    def _is_lookup_candidate(self, text: str) -> bool:
        for ch in text or "":
            if self._is_kanji_char(ch):
                return True
            code = ord(ch)
            if 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF:
                return True
            if ch.isalnum():
                return True
        return False

    def _contains_japanese(self, text: str) -> bool:
        for ch in text or "":
            if self._is_kanji_char(ch):
                return True
            code = ord(ch)
            if 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF:
                return True
        return False

    def _translation_dedupe_key(self, text: str) -> str:
        value = re.sub(r"\s+", " ", str(text or "").strip())
        value = value.strip(" .。、,;:/|")
        return value.casefold()

    def _dedupe_translation_entries(self, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        parts = [
            part.strip()
            for part in re.split(r"\s*(?:[/;,|]|\n+)\s*", value)
            if part.strip()
        ]
        if len(parts) <= 1:
            return value
        seen: set[str] = set()
        out: List[str] = []
        for part in parts:
            key = self._translation_dedupe_key(part)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(part)
        return ", ".join(out) if out else value

    def _translate_sentence(self, text: str) -> str:
        text = self._normalize_translation_input(text)
        if not text:
            return ""

        cached = self._sentence_translation_cache.get(text)
        if cached is not None:
            return cached

        provider = self.sentence_translate_provider
        translated = ""
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

    def _normalize_translation_input(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""
        value = value.replace("\u3000", " ")
        # Translate subtitle lines as one sentence block.
        value = re.sub(r"\s*\n+\s*", " ", value)
        value = re.sub(r"[ \t]+", " ", value)
        return value.strip()

    def _collect_translation_candidates(self, selected: str, subtitle: str) -> Dict[str, Dict[str, str]]:
        selected_text = (selected or "").strip()
        subtitle_text = self._normalize_translation_input(subtitle)

        word_candidates = {
            "jisho": self._dedupe_translation_entries(
                self._translate_word_with_jisho(selected_text)
            ) if selected_text else "",
            "google": self._dedupe_translation_entries(self._translate_google(
                selected_text,
                source_lang="ja",
                target_lang=self.word_target_lang,
            )) if selected_text else "",
        }

        sentence_candidates = {
            "deepl": (
                self._sentence_translation_cache.get(subtitle_text)
                or self._translate_deepl(
                    subtitle_text, source_lang="ja", target_lang=self.sentence_target_lang
                )
                if subtitle_text
                else ""
            ),
            "google": self._translate_google(
                subtitle_text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            ) if subtitle_text else "",
        }

        return {
            "word": word_candidates,
            "sentence": sentence_candidates,
        }

    def _detect_translation_provider(
        self,
        chosen: str,
        candidates: Dict[str, str],
        configured: str,
    ) -> str:
        selected = (chosen or "").strip()
        if not selected:
            return "none"
        configured_key = (configured or "").strip().lower()
        if configured_key and selected == (candidates.get(configured_key) or "").strip():
            return configured_key
        for name, value in candidates.items():
            if selected == (value or "").strip():
                return name
        return "fallback/unknown"

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
            return translated.strip()
        except Exception:
            return ""

    def _translate_deepl(self, text: str, source_lang: str, target_lang: str) -> str:
        text = (text or "").strip()
        if not text or not self.deepl_api_key:
            return ""

        source = self._normalize_deepl_source_lang(source_lang)
        target = self._normalize_deepl_target_lang(target_lang)
        if not target:
            return ""

        payload = {
            "text": text,
            "target_lang": target,
        }
        if source:
            payload["source_lang"] = source

        try:
            r = requests.post(
                self.deepl_translate_url,
                headers={"Authorization": f"DeepL-Auth-Key {self.deepl_api_key}"},
                data=payload,
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            data = r.json()
            translations = data.get("translations") or []
            if not translations:
                return ""
            return str((translations[0] or {}).get("text") or "").strip()
        except Exception:
            return ""

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

    def _sync_missing_stroke_svgs_for_new_note(self, selected_text: str, fields: Dict[str, str]) -> Dict[str, int]:
        result = {
            "enabled": int(bool(self.stroke_auto_sync)),
            "required": 0,
            "missing": 0,
            "uploaded": 0,
            "failed": 0,
        }
        if not self.stroke_auto_sync:
            return result

        texts = [
            selected_text,
            fields.get(self.add_rubies_to_front_field, ""),
            fields.get(self.front_field, ""),
        ]
        chars = self._extract_kanji_chars_for_strokes(texts)
        result["required"] = len(chars)
        if not chars:
            return result

        for kanji_char in chars:
            filename = self._stroke_media_filename(kanji_char)
            if self._stroke_media_exists(filename):
                continue
            result["missing"] += 1

            if kanji_char in self._stroke_sync_failed_cache:
                result["failed"] += 1
                continue

            try:
                raw_svg = self._download_stroke_svg(kanji_char)
                self._store_media_file(filename, raw_svg)
                self._stroke_media_exists_cache[filename] = True
                result["uploaded"] += 1
            except Exception:
                self._stroke_sync_failed_cache.add(kanji_char)
                result["failed"] += 1
        return result

    def _extract_kanji_chars_for_strokes(self, texts: List[str]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for raw in texts or []:
            value = str(raw or "")
            value = re.sub(r"<[^>]+>", "", value)
            value = re.sub(r"\[[^\[\]]+\]", "", value)
            for ch in value:
                if not self._is_kanji_char(ch):
                    continue
                if ch in seen:
                    continue
                seen.add(ch)
                out.append(ch)
        return out

    def _is_kanji_char(self, ch: str) -> bool:
        if not ch:
            return False
        code = ord(ch)
        return (
            code == 0x3005
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0x20000 <= code <= 0x2A6DF
            or 0x2A700 <= code <= 0x2B73F
            or 0x2B740 <= code <= 0x2B81F
            or 0x2B820 <= code <= 0x2CEAF
        )

    def _stroke_media_filename(self, kanji_char: str) -> str:
        prefix = re.sub(r"[^a-zA-Z0-9_.-]", "_", self.stroke_media_prefix or self.DEFAULT_STROKE_MEDIA_PREFIX)
        return f"{prefix}{ord(kanji_char):05x}.svg"

    def _stroke_media_exists(self, filename: str) -> bool:
        cached = self._stroke_media_exists_cache.get(filename)
        if cached is not None:
            return cached
        try:
            result = self._invoke("retrieveMediaFile", {"filename": filename})
            exists = isinstance(result, str) and bool(result)
        except Exception:
            exists = False
        self._stroke_media_exists_cache[filename] = exists
        return exists

    def _stroke_download_candidates(self, kanji_char: str) -> List[str]:
        base = (self.stroke_svg_base_url or self.DEFAULT_STROKE_SVG_BASE_URL).strip().rstrip("/")
        code = ord(kanji_char)
        hex_candidates = [
            f"{code:05x}",
            f"{code:04x}",
            f"{code:x}",
            f"{code:05X}",
            f"{code:04X}",
            f"{code:X}",
        ]
        return [f"{base}/{code_hex}.svg" for code_hex in hex_candidates]

    def _download_stroke_svg(self, kanji_char: str) -> bytes:
        last_error = None
        for url in self._stroke_download_candidates(kanji_char):
            try:
                response = requests.get(url, timeout=self.stroke_download_timeout)
                response.raise_for_status()
                if response.content:
                    return response.content
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"No stroke SVG URL worked for {kanji_char}") from last_error

    def _store_media_file(self, filename: str, raw: bytes) -> None:
        encoded = base64.b64encode(raw).decode("ascii")
        self._invoke("storeMediaFile", {"filename": filename, "data": encoded})
