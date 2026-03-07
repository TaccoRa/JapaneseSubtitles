"""
AnkiConnect client used to create notes from popup selections.
"""

from __future__ import annotations

import os
import re
from html import escape
from typing import Dict, List, Optional

import requests

try:
    from fugashi import Tagger
except Exception:
    Tagger = None


class AnkiClient:
    DEFAULT_JISHO_URL = "https://jisho.org/api/v1/search/words"
    DEFAULT_GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
    DEFAULT_DEEPL_URL = "https://api-free.deepl.com/v2/translate"

    def __init__(self, config) -> None:
        self.config = config
        self.url = (self.config.get("ANKI_CONNECT_URL") or "http://127.0.0.1:8765").strip()
        self.http_timeout = float(self.config.get("ANKI_HTTP_TIMEOUT_SEC") or 4.0)

        self._model_fields_cache: Optional[set[str]] = None
        self._word_translation_cache: Dict[str, str] = {}
        self._word_definition_cache: Dict[str, str] = {}
        self._sentence_translation_cache: Dict[str, str] = {}
        self._jisho_entries_cache: Dict[str, List[Dict]] = {}

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

        # Keep these stable in code so config stays minimal.
        self.add_rubies_to_front_field = "AddRubiesToFront"
        self.front_field = "Front"
        self.back_field = "Back"
        self.sentence_ja_field = "SentenceJA"
        self.sentence_de_field = "SentenceDE"
        self.sound_field = "Sound"
        self.image_field = "Image"
        self.add_rubies_to_sentence_ja_field = "AddRubiesToSentenceJA"
        self.definition_field = "Definition"

        self.sentence_target_lang = (self.config.get("ANKI_SENTENCE_TARGET_LANG") or "de").strip()
        self.word_target_lang = (self.config.get("ANKI_WORD_TARGET_LANG") or "de").strip()

        # Prefer Jisho for single-word meanings; keep DeepL for sentence translation.
        self.word_translate_provider = "jisho"
        self.sentence_translate_provider = "deepl"

        self.jisho_url = self.DEFAULT_JISHO_URL
        self.google_translate_url = self.DEFAULT_GOOGLE_TRANSLATE_URL
        self.deepl_translate_url = self.DEFAULT_DEEPL_URL

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
        self.tags = list(tags) if isinstance(tags, list) else ["subtitleplayer"]

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

        return {
            "note_id": note_id,
            "routed_cards": routed,
            "word_translation": word_translation,
            "sentence_translation": sentence_translation,
            "translation_candidates": translation_candidates,
            "translation_provider_used": {
                "word": word_provider_used,
                "sentence": sentence_provider_used,
            },
        }

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

        back_value = word_translation
        sentence_de = (sentence_translation).strip()

        fields[self.add_rubies_to_front_field] = selected_raw
        fields[self.front_field] = selected_with_rubies or selected_raw
        fields[self.back_field] = back_value
        fields[self.sentence_ja_field] = sentence_with_rubies
        fields[self.sentence_de_field] = sentence_de
        fields[self.add_rubies_to_sentence_ja_field] = subtitle_raw
        if self.definition_field in model_fields:
            fields[self.definition_field] = self._last_jisho_full_definition

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
            prev_had_ruby = i > 0 and segments[i - 1][1] is not None
            if (
                sentence_spacing
                and ruby
                and self._starts_with_kanji(base)
                and out
                and not out[-1].endswith((" ", "\n", "\t"))
                and not out[-1].endswith(("[", "(", "{", "<", "\u300c", "\u300e"))
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
        # Heuristic split: distribute token reading across kanji chars.
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
        small = set(
            "\u3083\u3085\u3087\u3041\u3043\u3045\u3047\u3049\u308e\u308e\u3095\u3096\u3063\u309d\u309e\u30fc"
        )
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

    def _starts_with_kanji(self, text: str) -> bool:
        if not text:
            return False
        return self._contains_kanji(text[0])

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
        self._last_jisho_full_definition = ", ".join(definitions)
        summary_en = ", ".join(definitions[:3])
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
        return translated or summary_en

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
        for sense in best.get("senses") or []:
            for item in sense.get("english_definitions") or []:
                gloss = str(item).strip()
                if gloss and gloss not in definitions:
                    definitions.append(gloss)
            if len(definitions) >= 6:
                break
        return definitions

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
            "jisho": self._translate_word_with_jisho(selected_text) if selected_text else "",
            "deepl": self._translate_deepl(
                selected_text,
                source_lang="ja",
                target_lang=self.word_target_lang,
            ) if selected_text else "",
            "google": self._translate_google(
                selected_text,
                source_lang="ja",
                target_lang=self.word_target_lang,
            ) if selected_text else "",
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
