"""
AnkiConnect client used to create notes from popup selections.
"""

from __future__ import annotations
import base64
import functools
import logging
import os
import re
import sys
import threading
import time
from html import escape
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class AnkiConnectRequestError(RuntimeError):
    """Raised when AnkiConnect drops or rejects a request."""

try:
    from SubtitlePlayer.furigana_splitter import iter_number_counter_matches, split_furigana, split_moras
except ImportError:
    try:
        from furigana_splitter import iter_number_counter_matches, split_furigana, split_moras
    except ImportError:
        _package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _package_dir not in sys.path:
            sys.path.insert(0, _package_dir)
        from furigana_splitter import iter_number_counter_matches, split_furigana, split_moras

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
    ANKI_SURU_MARKER_TAG = "する-Verb"
    ANKI_I_ADJECTIVE_MARKER_TAG = "い-Adj"
    ANKI_NA_ADJECTIVE_MARKER_TAG = "な-Adj"
    RUBY_READING_OVERRIDES = {
        "十分": "じゅうぶん",
    }
    TEXT_CACHE_LIMIT = 512

    def __init__(self, config) -> None:
        self.config = config
        self.url = (self.config.get("ANKI_CONNECT_URL") or "http://127.0.0.1:8765").strip()
        self.http_timeout = float(self.config.get("ANKI_HTTP_TIMEOUT_SEC") or 4.0)

        self._http = requests.Session()
        self._closed = False
        self._model_fields_cache: Optional[set[str]] = None
        self._ensured_decks: set[str] = set()
        self._furigana_cache: Dict[tuple[str, bool, bool, bool], str] = {}
        self._word_spans_cache: Dict[str, List[Dict[str, object]]] = {}
        self._single_kanji_reading_cache: Dict[str, str] = {}
        self._word_translation_cache: Dict[str, str] = {}
        self._word_definition_cache: Dict[str, str] = {}
        self._word_provider_cache: Dict[str, str] = {}
        self._jisho_word_translation_cache: Dict[str, str] = {}
        self._jisho_word_definition_cache: Dict[str, str] = {}
        self._sentence_translation_cache: Dict[str, str] = {}
        self._sentence_provider_cache: Dict[str, str] = {}
        self._google_translation_cache: Dict[tuple[str, str, str], str] = {}
        self._deepl_translation_cache: Dict[tuple[str, str, str], str] = {}
        self._jisho_entries_cache: Dict[str, List[Dict]] = {}
        self._dictionary_hover_cache: Dict[tuple[str, bool], str] = {}
        self._stroke_media_exists_cache: Dict[str, bool] = {}
        self._stroke_sync_failed_cache: set[str] = set()
        self._stroke_sync_threads: set[threading.Thread] = set()
        self._stroke_sync_lock = threading.Lock()

        # when using Jisho we keep the full english definition list here so the
        # note builder can put the full string into the "Definition" field
        self._last_jisho_full_definition: str = ""

        self._tagger = None
        self._tagger_load_attempted = False

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
            self.tags = []
        elif isinstance(tags, list):
            self.tags = [str(t).strip() for t in tags if str(t).strip()]
        elif isinstance(tags, str):
            self.tags = [t.strip() for t in tags.replace(";", ",").split(",") if t.strip()]
        else:
            self.tags = []
        self.tag_suru_verbs = self._config_bool("ANKI_TAG_SURU_VERBS", True)
        self.tag_i_adjectives = self._config_bool("ANKI_TAG_I_ADJECTIVES", True)
        self.tag_na_adjectives = self._config_bool("ANKI_TAG_NA_ADJECTIVES", True)

    def is_enabled(self) -> bool:
        return self.enabled

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._http.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _config_bool(self, key: str, default: bool = False) -> bool:
        try:
            value = self.config.get(key)
        except Exception:
            value = None
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"1", "true", "yes", "on"}:
                return True
            if text in {"0", "false", "no", "off"}:
                return False
        return bool(value)

    def _cache_put(self, cache: Dict, key, value, limit: int | None = None) -> None:
        cache[key] = value
        max_items = int(limit or self.TEXT_CACHE_LIMIT)
        while len(cache) > max_items:
            try:
                oldest = next(iter(cache))
            except StopIteration:
                return
            cache.pop(oldest, None)

    def _translation_cache_key(self, text: str, source_lang: str, target_lang: str) -> tuple[str, str, str]:
        return (
            str(text or "").strip(),
            str(source_lang or "").strip().lower(),
            str(target_lang or "").strip().lower(),
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        timeout: float | None = None,
        retries: int = 0,
        backoff_sec: float = 0.15,
        **kwargs,
    ) -> requests.Response:
        if self._closed:
            raise RuntimeError("Anki client is closed.")
        attempts = max(1, int(retries or 0) + 1)
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                response = self._http.request(
                    method,
                    url,
                    timeout=self.http_timeout if timeout is None else timeout,
                    **kwargs,
                )
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                if attempt >= attempts - 1:
                    break
                logger.warning(
                    "HTTP request to %s failed (%s); retrying in %.2fs",
                    url,
                    exc,
                    backoff_sec,
                )
                time.sleep(max(0.0, float(backoff_sec)) * (attempt + 1))
        raise AnkiConnectRequestError(f"HTTP request to {url} failed: {last_exc}") from last_exc

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        timeout: float | None = None,
        retries: int = 0,
        **kwargs,
    ):
        return self._request(method, url, timeout=timeout, retries=retries, **kwargs).json()

    def _get_tagger(self):
        if self._tagger is not None:
            return self._tagger
        if self._tagger_load_attempted or Tagger is None:
            return None
        self._tagger_load_attempted = True
        try:
            self._tagger = Tagger()
        except Exception:
            self._tagger = None
        return self._tagger

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
        prepared = self.prepare_note_from_selection(selection_text, subtitle_text)
        return self.commit_prepared_note(prepared)

    def prepare_note_from_selection(
        self,
        selection_text: str,
        subtitle_text: str = "",
    ) -> Dict:
        selected = (selection_text or "").strip()
        if not selected:
            raise ValueError("No selected text.")

        subtitle = (subtitle_text or "").strip()
        card_word = self._card_headword_for_anki(selected, subtitle)
        marker_tags = self._anki_marker_tags_for_selection(selected, card_word, subtitle)
        word_translation = self._translate_word(card_word)
        sentence_translation = self._translate_sentence(subtitle)
        full_definition = self._last_jisho_full_definition
        translation_candidates = self._collect_translation_candidates(
            card_word,
            subtitle,
            word_translation=word_translation,
            sentence_translation=sentence_translation,
            fetch_missing_google_sentence=True,
        )
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
            selected=card_word,
            subtitle=subtitle,
            word_translation=word_translation,
            sentence_translation=sentence_translation,
        )
        copied_media_fields = self._copy_existing_sentence_media_fields(fields)

        note = {
            "deckName": self.deck_name,
            "modelName": self.model_name,
            "fields": fields,
            "tags": self._merge_anki_tags(self.tags, marker_tags),
            "options": {"allowDuplicate": True},
        }

        return {
            "note": note,
            "fields": fields,
            "routing_error": "",
            "word_translation": word_translation,
            "sentence_translation": sentence_translation,
            "definition": full_definition,
            "selection_surface_text": selected,
            "selection_lookup_text": card_word,
            "anki_marker_tags": marker_tags,
            "copied_media_fields": copied_media_fields,
            "translation_candidates": translation_candidates,
            "translation_provider_used": {
                "word": word_provider_used,
                "sentence": sentence_provider_used,
            },
            "stroke_svg_sync_fields": fields,
        }

    def commit_prepared_note(self, prepared: Dict) -> Dict:
        if not isinstance(prepared, dict):
            raise ValueError("Prepared Anki note is invalid.")
        note = prepared.get("note")
        if not isinstance(note, dict):
            raise ValueError("Prepared Anki note is missing note payload.")
        fields = note.get("fields")
        if not isinstance(fields, dict):
            fields = prepared.get("fields")
        if not isinstance(fields, dict):
            raise ValueError("Prepared Anki note is missing fields.")
        note["fields"] = fields
        tags = note.get("tags")
        if isinstance(tags, str):
            note["tags"] = self._merge_anki_tags(tags.replace(";", ",").split(","))
        elif not isinstance(tags, list):
            note["tags"] = []

        self._ensure_decks((note.get("deckName") or self.deck_name, self.reading_deck, self.reverse_deck))
        note_id = self._add_note_without_duplicate_retry(note, fields)
        routing_error = ""
        try:
            routed = self._route_new_cards(note_id)
        except Exception as exc:
            routed = {"reading": [], "reverse": [], "unrouted": []}
            routing_error = str(exc)
            logger.warning(
                "Anki note %s was created, but routing new cards failed: %s",
                note_id,
                exc,
                exc_info=True,
            )
        # stroke_sync = self._sync_missing_stroke_svgs_for_new_note(
        #     selected_text=selected,
        #     fields=fields,
        # )

        result = dict(prepared)
        result.update({
            "note_id": note_id,
            "routed_cards": routed,
            "routing_error": routing_error,
            # "stroke_svg_sync": stroke_sync,
            "stroke_svg_sync_fields": fields,
        })
        return result


    def _add_note_without_duplicate_retry(self, note: Dict, fields: Dict[str, str]) -> int:
        try:
            return self._invoke("addNote", {"note": note})
        except AnkiConnectRequestError as exc:
            logger.warning(
                "AnkiConnect dropped during addNote; verifying whether the note already exists before retrying.",
                exc_info=True,
            )

            existing = self._find_existing_note_id_for_fields(fields)
            if existing:
                logger.warning("Using existing note %s after dropped addNote response", existing)
                return existing

            try:
                logger.warning("No matching note found after dropped addNote response; retrying addNote once.")
                return self._invoke("addNote", {"note": note})
            except AnkiConnectRequestError as retry_exc:
                raise RuntimeError(
                    "AnkiConnect closed or restarted while creating the note. "
                    "The app could not verify creation without risking a duplicate."
                ) from retry_exc
            except Exception:
                raise

    def _find_existing_note_id_for_fields(self, fields: Dict[str, str]) -> Optional[int]:
        queries: List[str] = []
        for name in (
            self.add_rubies_to_front_field,
            self.front_field,
            self.add_rubies_to_sentence_ja_field,
            self.sentence_ja_field,
        ):
            value = str(fields.get(name) or "").strip()
            if not value:
                continue
            query = self._quote_anki_search_text(value)
            if query not in queries:
                queries.append(query)

        note_ids: set[int] = set()
        for query in queries:
            found = self._invoke("findNotes", {"query": query}) or []
            for note_id in found:
                try:
                    note_ids.add(int(note_id))
                except Exception:
                    continue
        if not note_ids:
            return None

        notes = self._invoke("notesInfo", {"notes": sorted(note_ids, reverse=True)[:50]}) or []
        for note in sorted(notes, key=lambda item: int(item.get("noteId") or 0), reverse=True):
            if self._note_core_fields_match(note, fields):
                try:
                    return int(note.get("noteId"))
                except Exception:
                    return None
        return None

    def _note_core_fields_match(self, note: Dict, fields: Dict[str, str]) -> bool:
        if str(note.get("modelName") or "").strip() != self.model_name:
            return False
        names = [
            self.add_rubies_to_front_field,
            self.front_field,
            self.back_field,
            self.add_rubies_to_sentence_ja_field,
            self.sentence_ja_field,
        ]
        compared = 0
        for name in names:
            expected = self._normalize_media_sentence_value(str(fields.get(name) or ""))
            if not expected:
                continue
            actual = self._normalize_media_sentence_value(self._note_field_value(note, name))
            if actual != expected:
                return False
            compared += 1
        return compared > 0


    def sync_missing_stroke_svgs_async(self, selected_text: str, fields: Dict[str, str]):
        if self._closed:
            return

        def worker():
            try:
                self._sync_missing_stroke_svgs_for_new_note(selected_text, fields)
            finally:
                with self._stroke_sync_lock:
                    self._stroke_sync_threads.discard(thread)

        thread = threading.Thread(
            target=worker,
            name="AnkiStrokeSvgSync",
            daemon=True,
        )
        with self._stroke_sync_lock:
            self._stroke_sync_threads.add(thread)
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

    def _copy_existing_sentence_media_fields(self, fields: Dict[str, str]) -> Dict[str, str]:
        media_fields = [
            name
            for name in (self.sound_field, self.image_field)
            if name in fields and not str(fields.get(name) or "").strip()
        ]
        if not media_fields:
            return {}

        source = self._find_existing_sentence_media(fields, media_fields)
        copied: Dict[str, str] = {}
        for name in media_fields:
            value = str(source.get(name) or "").strip()
            if value and not str(fields.get(name) or "").strip():
                fields[name] = value
                copied[name] = value
        return copied

    def _find_existing_sentence_media(self, fields: Dict[str, str], media_fields: List[str]) -> Dict[str, str]:
        raw_sentence = str(fields.get(self.add_rubies_to_sentence_ja_field, "") or "")
        rendered_sentence = str(fields.get(self.sentence_ja_field, "") or "")
        if not raw_sentence and not rendered_sentence:
            return {}

        queries = self._media_copy_search_queries(raw_sentence, rendered_sentence)
        if not queries:
            return {}

        note_ids: set[int] = set()
        for query in queries:
            try:
                found = self._invoke("findNotes", {"query": query}) or []
            except Exception:
                logger.exception("Anki media-copy findNotes failed for query: %s", query)
                continue

            for note_id in found:
                try:
                    note_ids.add(int(note_id))
                except Exception:
                    continue

        if not note_ids:
            logger.info(
                "No existing Anki notes found for media copy. raw=%r rendered=%r queries=%r",
                raw_sentence,
                rendered_sentence,
                queries,
            )
            return {}

        try:
            notes = self._invoke("notesInfo", {"notes": sorted(note_ids, reverse=True)[:100]}) or []
        except Exception:
            logger.exception("Anki media-copy notesInfo failed")
            return {}

        target_raw = self._normalize_media_sentence_value(raw_sentence)
        target_rendered = self._normalize_media_sentence_value(rendered_sentence)
        best: Dict[str, str] = {}

        for note in sorted(notes, key=lambda item: int(item.get("noteId") or 0), reverse=True):
            if not self._note_matches_media_sentence(note, target_raw, target_rendered, exact_add_rubies_only=True):
                continue

            for name in media_fields:
                if best.get(name):
                    continue
                value = self._note_field_value(note, name).strip()
                if value:
                    best[name] = value

            if all(best.get(name) for name in media_fields):
                logger.info("Copied existing Anki media fields: %s", sorted(best.keys()))
                return best

        logger.info(
            "No previous exact AddRubiesToSentenceJA note had copyable media; checking rendered-sentence fallback."
        )

        for note in sorted(notes, key=lambda item: int(item.get("noteId") or 0), reverse=True):
            if not self._note_matches_media_sentence(note, target_raw, target_rendered):
                continue

            for name in media_fields:
                if best.get(name):
                    continue
                value = self._note_field_value(note, name).strip()
                if value:
                    best[name] = value

            if all(best.get(name) for name in media_fields):
                logger.info("Copied existing Anki media fields from fallback match: %s", sorted(best.keys()))
                return best

        logger.info(
            "Existing notes matched sentence but had no copyable media. note_count=%s media_fields=%s",
            len(notes),
            media_fields,
        )
        return best


    def _media_copy_search_queries(self, raw_sentence: str, rendered_sentence: str) -> List[str]:
        queries: List[str] = []

        def add_query(query: str) -> None:
            query = str(query or "").strip()
            if query and query not in queries:
                queries.append(query)

        def quote(value: str) -> str:
            value = self._normalize_media_sentence_value(value)
            value = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'"{value}"'

        raw_normalized = self._normalize_media_sentence_value(raw_sentence)
        rendered_normalized = self._normalize_media_sentence_value(rendered_sentence)

        if raw_normalized:
            raw_quoted = quote(raw_normalized)
            add_query(raw_quoted)
            add_query(f'{self.add_rubies_to_sentence_ja_field}:{raw_quoted}')

        if rendered_normalized:
            rendered_quoted = quote(rendered_normalized)
            add_query(rendered_quoted)
            add_query(f'{self.sentence_ja_field}:{rendered_quoted}')

        return queries

    def _quote_anki_search_text(self, text: str) -> str:
        value = self._normalize_media_sentence_value(text)
        value = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{value}"'

    def _note_matches_media_sentence(
        self,
        note: Dict,
        target_raw: str,
        target_rendered: str,
        *,
        exact_add_rubies_only: bool = False,
    ) -> bool:
        model_name = str(note.get("modelName") or "").strip()
        if model_name and model_name != self.model_name:
            return False

        if target_raw:
            note_raw = self._normalize_media_sentence_value(
                self._note_field_value(note, self.add_rubies_to_sentence_ja_field)
            )
            if note_raw == target_raw:
                return True

        if exact_add_rubies_only:
            return False

        if target_rendered:
            note_rendered = self._normalize_media_sentence_value(
                self._note_field_value(note, self.sentence_ja_field)
            )
            if note_rendered == target_rendered:
                return True

        return False

    def _note_field_value(self, note: Dict, field_name: str) -> str:
        fields = note.get("fields") if isinstance(note, dict) else None
        if not isinstance(fields, dict):
            return ""
        value = fields.get(field_name)
        if isinstance(value, dict):
            return str(value.get("value") or "")
        return str(value or "")

    def _normalize_media_sentence_value(self, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        value = value.replace("\u3000", " ")
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _to_furigana_brackets(
        self,
        text: str,
        collapse_inline_reading: bool,
        sentence_spacing: bool,
    ) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        cache_key = (
            text,
            bool(collapse_inline_reading),
            bool(sentence_spacing),
            self._split_kanji_moras_enabled(),
        )
        cached = self._furigana_cache.get(cache_key)
        if cached is not None:
            return cached

        segments = self._tokenize_with_reading(
            text=text,
            collapse_inline_reading=collapse_inline_reading,
        )
        result = self._segments_to_bracket_text(segments, sentence_spacing=sentence_spacing)
        self._cache_put(self._furigana_cache, cache_key, result)
        return result

    def _segments_to_bracket_text(
        self,
        segments: List[tuple[str, Optional[str]]],
        sentence_spacing: bool,
    ) -> str:
        out: List[str] = []
        for i, (base, ruby) in enumerate(segments):
            if not base:
                continue
            if base in self.RUBY_READING_OVERRIDES:
                ruby = self.RUBY_READING_OVERRIDES[base]
            if (
                ruby
                and self._starts_with_kanji(base)
                and out
                and not out[-1].endswith((" ", "\n", "\t"))
                and not out[-1].endswith(("[", "(", "\uff08", "{", "\uff5b", "<", "\uff1c", "\u300c", "\u300e", "\u3010"))
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
        counter_segments = self._tokenize_number_counter_segments(text, collapse_inline_reading)
        if counter_segments is not None:
            return counter_segments
        return self._tokenize_with_reading_no_counters(
            text=text,
            collapse_inline_reading=collapse_inline_reading,
        )

    def _tokenize_number_counter_segments(
        self,
        text: str,
        collapse_inline_reading: bool,
    ) -> List[tuple[str, Optional[str]]] | None:
        matches = list(iter_number_counter_matches(text or ""))
        if not matches:
            return None

        segments: List[tuple[str, Optional[str]]] = []
        cursor = 0
        for match, reading in matches:
            if match.start() > cursor:
                segments.extend(
                    self._tokenize_with_reading_no_counters_preserve_edges(
                        text=text[cursor:match.start()],
                        collapse_inline_reading=collapse_inline_reading,
                    )
                )
            surface = match.group(0)
            segments.append((surface, self.RUBY_READING_OVERRIDES.get(surface, reading)))
            cursor = match.end()
        if cursor < len(text):
            segments.extend(
                self._tokenize_with_reading_no_counters_preserve_edges(
                    text=text[cursor:],
                    collapse_inline_reading=collapse_inline_reading,
                )
            )
        return segments

    def _tokenize_with_reading_no_counters_preserve_edges(
        self,
        text: str,
        collapse_inline_reading: bool,
    ) -> List[tuple[str, Optional[str]]]:
        if not text:
            return []
        leading_len = len(text) - len(text.lstrip())
        trailing_len = len(text) - len(text.rstrip())
        leading = text[:leading_len]
        trailing = text[len(text) - trailing_len :] if trailing_len else ""
        middle_end = len(text) - trailing_len if trailing_len else len(text)
        middle = text[leading_len:middle_end]
        segments: List[tuple[str, Optional[str]]] = []
        if leading:
            segments.append((leading, None))
        if middle:
            segments.extend(
                self._tokenize_with_reading_no_counters(
                    text=middle,
                    collapse_inline_reading=collapse_inline_reading,
                )
            )
        if trailing:
            segments.append((trailing, None))
        return segments

    def _tokenize_with_reading_no_counters(
        self,
        text: str,
        collapse_inline_reading: bool,
    ) -> List[tuple[str, Optional[str]]]:
        if re.search(r"\s", text or ""):
            segments: List[tuple[str, Optional[str]]] = []
            for part in re.split(r"(\s+)", text or ""):
                if not part:
                    continue
                if part.isspace():
                    segments.append((part, None))
                else:
                    segments.extend(
                        self._tokenize_with_reading_no_counters(
                            text=part,
                            collapse_inline_reading=collapse_inline_reading,
                        )
                    )
            return segments or [(text, None)]

        tagger = self._get_tagger()
        if tagger is None:
            return [(text, None)]
        try:
            tokens = list(tagger(text))
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

        segments = self._merge_katakana_ruby_segments(segments)
        return segments or [(text, None)]

    def _merge_katakana_ruby_segments(
        self,
        segments: List[tuple[str, Optional[str]]],
    ) -> List[tuple[str, Optional[str]]]:
        if not segments:
            return segments
        out: List[tuple[str, Optional[str]]] = []
        i = 0
        while i < len(segments):
            base, ruby = segments[i]
            if ruby and self._is_katakana_ruby_base(base):
                merged_base = base
                j = i + 1
                while j < len(segments):
                    next_base, next_ruby = segments[j]
                    if next_ruby is not None or not self._is_katakana_fragment(next_base):
                        break
                    merged_base += next_base
                    j += 1
                if j > i + 1:
                    out.append((merged_base, self._katakana_to_hiragana(merged_base)))
                    i = j
                    continue
            out.append((base, ruby))
            i += 1
        return out

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
        if surface in self.RUBY_READING_OVERRIDES:
            return [(surface, self.RUBY_READING_OVERRIDES[surface])]
        if not reading_kata:
            if self._is_katakana_ruby_base(surface):
                hira = self._katakana_to_hiragana(surface)
                if hira and hira != surface:
                    return [(surface, hira)]
            return [(surface, None)]

        reading = self._katakana_to_hiragana(reading_kata)
        if not reading:
            return [(surface, None)]
        if self._is_katakana_ruby_base(surface):
            if reading != surface:
                return [(surface, reading)]
            return [(surface, None)]
        if surface == reading:
            return [(surface, None)]
        if self._katakana_to_hiragana(surface) == reading:
            return [(surface, None)]
        if not self._contains_kanji(surface):
            return [(surface, None)]
        if self._looks_numeric(surface):
            return [(surface, None)]
        return split_furigana(
            surface,
            reading,
            self._single_kanji_reading_for_split,
            split_kanji_compounds=self._split_kanji_moras_enabled(),
        )

    def _card_headword_for_selection(self, text: str) -> str:
        """
        Return the word form that should be used for Anki card lookup/front fields.

        For a selected Japanese verb inflection, use the dictionary-form lemma so
        lookups hit entries like 貫く instead of noun-biased matches for 貫い.
        サ変 selections such as 勉強した are normalized to 勉強する.
        Non-verbs keep the original selected text.
        """
        selected = (text or "").strip()
        if not selected:
            return ""

        tagger = self._get_tagger()
        if tagger is None:
            return selected

        try:
            tokens = list(tagger(selected))
        except Exception:
            return selected
        if not tokens:
            return selected

        meaningful_tokens = self._meaningful_morph_tokens(tokens)
        suru_headword = self._suru_compound_headword(meaningful_tokens)
        if suru_headword:
            return suru_headword

        for token in meaningful_tokens:
            surface = str(getattr(token, "surface", "") or "").strip()
            if not surface:
                continue

            feature = getattr(token, "feature", None)
            pos1 = str(getattr(feature, "pos1", "") or "")
            if pos1 != "動詞":
                return selected

            if self._is_suru_verb_token(token):
                lemma = self._suru_token_base_text(token, surface)
                if lemma and self._contains_japanese(lemma):
                    return lemma
                return selected

            lemma = self._token_lookup_text(token, surface)
            if lemma and lemma != surface and self._contains_japanese(lemma):
                return lemma
            return selected

        return selected

    def _card_headword_for_anki(self, selected: str, subtitle: str = "") -> str:
        selected = (selected or "").strip()
        if not selected:
            return ""

        context_headword = self._card_headword_from_sentence_context(selected, subtitle)
        if context_headword:
            return context_headword

        return self._card_headword_for_selection(selected)

    def _card_headword_from_sentence_context(self, selected: str, subtitle: str = "") -> str:
        for window in self._selection_context_token_windows(selected, subtitle):
            suru_headword = self._suru_compound_headword(window)
            if suru_headword:
                return suru_headword
        return ""

    def _anki_marker_tags_for_selection(
        self,
        selected: str,
        lookup_text: str = "",
        subtitle: str = "",
    ) -> List[str]:
        markers: List[str] = []
        selected = (selected or "").strip()
        lookup_text = (lookup_text or "").strip()
        if not selected:
            return markers

        context_windows = self._selection_context_token_windows(selected, subtitle)
        if context_windows:
            for window in context_windows:
                has_suru = bool(
                    (lookup_text.endswith("する") and self._selection_has_suru_verb(window))
                    or self._suru_compound_headword(window)
                )
                if has_suru and self.tag_suru_verbs:
                    markers.append(self.ANKI_SURU_MARKER_TAG)
                if self.tag_i_adjectives and self._selection_has_i_adjective(window):
                    markers.append(self.ANKI_I_ADJECTIVE_MARKER_TAG)
                if self.tag_na_adjectives and not has_suru and self._selection_has_na_adjective(window):
                    markers.append(self.ANKI_NA_ADJECTIVE_MARKER_TAG)
            return self._merge_anki_tags(markers)

        tagger = self._get_tagger()
        if tagger is None:
            if self.tag_suru_verbs and (lookup_text.endswith("する") or selected.endswith("する")):
                markers.append(self.ANKI_SURU_MARKER_TAG)
            return markers

        try:
            tokens = list(tagger(selected))
        except Exception:
            if self.tag_suru_verbs and (lookup_text.endswith("する") or selected.endswith("する")):
                markers.append(self.ANKI_SURU_MARKER_TAG)
            return markers

        meaningful_tokens = self._meaningful_morph_tokens(tokens)
        if not meaningful_tokens:
            return markers

        has_suru = bool(
            (lookup_text.endswith("する") and self._selection_has_suru_verb(meaningful_tokens))
            or self._suru_compound_headword(meaningful_tokens)
        )
        if has_suru and self.tag_suru_verbs:
            markers.append(self.ANKI_SURU_MARKER_TAG)

        if self.tag_i_adjectives and self._selection_has_i_adjective(meaningful_tokens):
            markers.append(self.ANKI_I_ADJECTIVE_MARKER_TAG)
        if self.tag_na_adjectives and not has_suru and self._selection_has_na_adjective(meaningful_tokens):
            markers.append(self.ANKI_NA_ADJECTIVE_MARKER_TAG)

        return self._merge_anki_tags(markers)

    def _selection_context_token_windows(self, selected: str, subtitle: str = "") -> List[List[object]]:
        selected = (selected or "").strip()
        subtitle = (subtitle or "").strip()
        if not selected or not subtitle or selected == subtitle:
            return []

        positioned = self._tokenize_with_positions(subtitle)
        if not positioned:
            return []

        windows: List[List[object]] = []
        for start, end in self._selected_text_occurrences(selected, subtitle):
            match = self._matching_context_token_range(positioned, selected, start, end)
            if match is None:
                continue
            token_start, token_end = match
            window = self._context_window_after_selection(positioned, token_start, token_end)
            if window:
                windows.append(window)
        return windows

    def _tokenize_with_positions(self, text: str) -> List[tuple[object, int, int]]:
        tagger = self._get_tagger()
        if tagger is None:
            return []
        try:
            tokens = list(tagger(text))
        except Exception:
            return []

        positioned: List[tuple[object, int, int]] = []
        cursor = 0
        for token in tokens:
            surface = str(getattr(token, "surface", "") or "")
            if not surface:
                continue
            start = text.find(surface, cursor)
            if start < 0:
                start = text.find(surface)
            if start < 0:
                continue
            end = start + len(surface)
            cursor = end
            positioned.append((token, start, end))
        return positioned

    def _selected_text_occurrences(self, selected: str, text: str) -> List[tuple[int, int]]:
        occurrences: List[tuple[int, int]] = []
        start = 0
        while True:
            idx = text.find(selected, start)
            if idx < 0:
                break
            occurrences.append((idx, idx + len(selected)))
            start = idx + max(1, len(selected))
        return occurrences

    def _matching_context_token_range(
        self,
        positioned: List[tuple[object, int, int]],
        selected: str,
        start: int,
        end: int,
    ) -> tuple[int, int] | None:
        for idx, (_token, token_start, token_end) in enumerate(positioned):
            if token_end <= start:
                continue
            if token_start >= end:
                break
            if token_start != start:
                continue

            surfaces: List[str] = []
            j = idx
            while j < len(positioned) and positioned[j][1] < end:
                _next_token, next_start, next_end = positioned[j]
                if next_start < start or next_end > end:
                    return None
                surfaces.append(str(getattr(_next_token, "surface", "") or ""))
                j += 1

            if "".join(surfaces) == selected and j > idx:
                return idx, j

        return None

    def _context_window_after_selection(
        self,
        positioned: List[tuple[object, int, int]],
        token_start: int,
        token_end: int,
    ) -> List[object]:
        window = [item[0] for item in positioned[token_start:token_end]]
        saw_suru = any(self._is_suru_verb_token(token) for token in window)
        idx = token_end

        while idx < len(positioned):
            token = positioned[idx][0]
            if not saw_suru:
                if self._is_suru_verb_token(token):
                    window.append(token)
                    saw_suru = True
                    idx += 1
                    continue
                if self._is_suru_linking_token(token, window):
                    window.append(token)
                    idx += 1
                    continue
                window.append(token)
                break

            if self._is_suru_inflection_tail_token(token):
                window.append(token)
                idx += 1
                continue
            break

        return window

    def _is_suru_linking_token(self, token, current_window: List[object]) -> bool:
        if not current_window:
            return False
        surface = str(getattr(token, "surface", "") or "").strip()
        feature = getattr(token, "feature", None)
        pos1 = str(getattr(feature, "pos1", "") or "")
        ctype = str(getattr(feature, "cType", "") or "")

        previous = current_window[-1]
        if surface == "に" and (
            pos1 == "助動詞" or ctype == "助動詞-ダ"
        ):
            return self._token_has_na_adjective_potential(previous)
        if surface == "を" and pos1 == "助詞":
            return self._token_has_suru_potential(previous)
        return False

    def _is_suru_inflection_tail_token(self, token) -> bool:
        surface = str(getattr(token, "surface", "") or "").strip()
        if not surface:
            return True
        feature = getattr(token, "feature", None)
        pos1 = str(getattr(feature, "pos1", "") or "")
        pos2 = str(getattr(feature, "pos2", "") or "")
        if pos1 in {"補助記号", "空白", "助動詞"}:
            return True
        if pos1 == "助詞":
            return True
        if pos1 == "動詞" and pos2 == "非自立可能":
            return True
        return False

    def _merge_anki_tags(self, *tag_groups) -> List[str]:
        merged: List[str] = []
        seen: set[str] = set()
        for group in tag_groups:
            if group is None:
                continue
            if isinstance(group, str):
                values = [group]
            else:
                try:
                    values = list(group)
                except TypeError:
                    values = [group]
            for value in values:
                tag = str(value or "").strip()
                if not tag or tag in seen:
                    continue
                seen.add(tag)
                merged.append(tag)
        return merged

    def _meaningful_morph_tokens(self, tokens) -> List[object]:
        meaningful: List[object] = []
        for token in tokens or []:
            surface = str(getattr(token, "surface", "") or "").strip()
            if not surface:
                continue
            feature = getattr(token, "feature", None)
            pos1 = str(getattr(feature, "pos1", "") or "")
            if pos1 in {"補助記号", "空白"}:
                continue
            meaningful.append(token)
        return meaningful

    def _suru_compound_headword(self, tokens: List[object]) -> str:
        for idx, token in enumerate(tokens or []):
            if idx <= 0 or not self._is_suru_verb_token(token):
                continue
            if not self._tokens_after_suru_are_inflection(tokens[idx + 1:]):
                continue
            stem = self._suru_stem_text(tokens[:idx])
            if stem:
                return f"{stem}する"
        return ""

    def _suru_stem_text(self, tokens: List[object]) -> str:
        if not tokens:
            return ""

        parts: List[str] = []
        saw_stem = False
        for idx, token in enumerate(tokens):
            surface = str(getattr(token, "surface", "") or "").strip()
            if not surface:
                continue
            feature = getattr(token, "feature", None)
            pos1 = str(getattr(feature, "pos1", "") or "")
            pos3 = str(getattr(feature, "pos3", "") or "")

            if pos1 in {"名詞", "形状詞", "接頭辞", "接尾辞"}:
                parts.append(surface)
                saw_stem = True
                continue

            if (
                surface == "に"
                and pos1 == "助動詞"
                and saw_stem
                and idx == len(tokens) - 1
                and self._token_has_na_adjective_potential(tokens[idx - 1])
            ):
                parts.append(surface)
                continue

            if (
                surface == "を"
                and pos1 == "助詞"
                and saw_stem
                and idx == len(tokens) - 1
                and self._token_has_suru_potential(tokens[idx - 1])
            ):
                continue

            if "サ変" in pos3 or "形状詞" in pos3:
                parts.append(surface)
                saw_stem = True
                continue

            return ""

        stem = "".join(parts).strip()
        if not stem or not self._contains_japanese(stem):
            return ""
        return stem

    def _tokens_after_suru_are_inflection(self, tokens: List[object]) -> bool:
        for token in tokens or []:
            surface = str(getattr(token, "surface", "") or "").strip()
            if not surface:
                continue
            feature = getattr(token, "feature", None)
            pos1 = str(getattr(feature, "pos1", "") or "")
            pos2 = str(getattr(feature, "pos2", "") or "")
            if pos1 in {"補助記号", "空白", "助動詞"}:
                continue
            if pos1 == "助詞":
                continue
            if pos1 == "動詞" and pos2 == "非自立可能":
                continue
            return False
        return True

    def _selection_has_suru_verb(self, tokens: List[object]) -> bool:
        return any(self._is_suru_verb_token(token) for token in tokens or [])

    def _is_suru_verb_token(self, token) -> bool:
        feature = getattr(token, "feature", None)
        pos1 = str(getattr(feature, "pos1", "") or "")
        if pos1 != "動詞":
            return False

        ctype = str(getattr(feature, "cType", "") or "")
        values = [
            str(getattr(token, "surface", "") or ""),
            str(getattr(feature, "lemma", "") or ""),
            str(getattr(feature, "orthBase", "") or ""),
            str(getattr(feature, "formBase", "") or ""),
            str(getattr(feature, "kanaBase", "") or ""),
        ]
        if "サ行変格" in ctype and any(value in {"する", "為る", "スル"} for value in values):
            return True
        return any(value.endswith("する") or value.endswith("スル") for value in values)

    def _suru_token_base_text(self, token, surface: str) -> str:
        feature = getattr(token, "feature", None)
        if feature is not None:
            for attr in ("lemma", "orthBase", "formBase"):
                value = getattr(feature, attr, None)
                if value and value != "*":
                    text = str(value).strip()
                    if text.endswith("する"):
                        return text
            for attr in ("orthBase", "formBase"):
                value = getattr(feature, attr, None)
                if value and value != "*":
                    text = str(value).strip()
                    if text == "する":
                        return "する"
            lemma = str(getattr(feature, "lemma", "") or "").strip()
            if lemma == "為る":
                return "する"
        if surface in {"し", "する", "すれ", "せ", "さ"}:
            return "する"
        return self._token_lookup_text(token, surface)

    def _selection_has_i_adjective(self, tokens: List[object]) -> bool:
        for token in tokens or []:
            feature = getattr(token, "feature", None)
            if str(getattr(feature, "pos1", "") or "") == "形容詞":
                return True
        return False

    def _selection_has_na_adjective(self, tokens: List[object]) -> bool:
        for idx, token in enumerate(tokens or []):
            feature = getattr(token, "feature", None)
            pos1 = str(getattr(feature, "pos1", "") or "")
            if pos1 == "形状詞":
                return True
            if not self._token_has_na_adjective_potential(token):
                continue
            if idx + 1 >= len(tokens):
                return True
            next_token = tokens[idx + 1]
            next_surface = str(getattr(next_token, "surface", "") or "").strip()
            next_feature = getattr(next_token, "feature", None)
            next_pos1 = str(getattr(next_feature, "pos1", "") or "")
            next_ctype = str(getattr(next_feature, "cType", "") or "")
            if next_surface in {"な", "だ", "に"} and (
                next_pos1 == "助動詞" or next_ctype == "助動詞-ダ"
            ):
                return True
        return False

    def _token_has_na_adjective_potential(self, token) -> bool:
        feature = getattr(token, "feature", None)
        pos1 = str(getattr(feature, "pos1", "") or "")
        pos3 = str(getattr(feature, "pos3", "") or "")
        return pos1 == "形状詞" or "形状詞" in pos3

    def _token_has_suru_potential(self, token) -> bool:
        feature = getattr(token, "feature", None)
        pos1 = str(getattr(feature, "pos1", "") or "")
        pos3 = str(getattr(feature, "pos3", "") or "")
        return pos1 in {"名詞", "形状詞"} and "サ変" in pos3

    def _split_kanji_moras_enabled(self) -> bool:
        try:
            return bool(self.config.get("ANKI_SPLIT_KANJI_MORAS") or False)
        except Exception:
            return False

    def _single_kanji_reading_for_split(self, ch: str) -> str:
        if not self._is_kanji_char(ch):
            return ""
        cached = self._single_kanji_reading_cache.get(ch)
        if cached is not None:
            return cached
        tagger = self._get_tagger()
        if tagger is None:
            return ""
        try:
            tokens = list(tagger(ch))
        except Exception:
            return ""
        if len(tokens) != 1:
            self._cache_put(self._single_kanji_reading_cache, ch, "")
            return ""
        token = tokens[0]
        if str(getattr(token, "surface", "") or "") != ch:
            self._cache_put(self._single_kanji_reading_cache, ch, "")
            return ""
        reading = self._katakana_to_hiragana(self._token_reading(token))
        self._cache_put(self._single_kanji_reading_cache, ch, reading)
        return reading

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
            return [(surface, reading)]

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

    def _is_katakana_ruby_base(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        has_katakana = False
        for ch in value:
            code = ord(ch)
            if 0x30A1 <= code <= 0x30FA or 0x30FD <= code <= 0x30FF:
                has_katakana = True
                continue
            if ch in {"ー", "・", "･"}:
                continue
            return False
        return has_katakana

    def _is_katakana_fragment(self, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        for ch in value:
            code = ord(ch)
            if 0x30A0 <= code <= 0x30FF or ch in {"ー", "・", "･"}:
                continue
            return False
        return True

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

    def _ends_with_kana(self, text: str) -> bool:
        if not text:
            return False
        code = ord(text[-1])
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)

    def _looks_numeric(self, text: str) -> bool:
        full_width_digits = "".join(chr(cp) for cp in range(0xFF10, 0xFF1A))
        number_chars = set("0123456789" + full_width_digits)
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
        provider_used = "jisho" if translation else "none"
        if not translation:
            translation = self._translate_google(text, source_lang="ja", target_lang=self.word_target_lang)
            provider_used = "google" if translation else "none"
        translation = self._dedupe_translation_entries(translation)
        self._cache_put(self._word_translation_cache, text, translation)
        self._cache_put(self._word_definition_cache, text, self._last_jisho_full_definition)
        self._cache_put(self._word_provider_cache, text, provider_used)
        return translation

    def _translate_word_with_jisho(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        cached = self._jisho_word_translation_cache.get(text)
        if cached is not None:
            self._last_jisho_full_definition = self._jisho_word_definition_cache.get(text, "")
            return cached

        entries = self._fetch_jisho_entries(text)
        definitions = self._extract_jisho_translation(text, entries)
        if not definitions:
            self._last_jisho_full_definition = ""
            self._cache_put(self._jisho_word_translation_cache, text, "")
            self._cache_put(self._jisho_word_definition_cache, text, "")
            return ""
        self._last_jisho_full_definition = self._dedupe_translation_entries(", ".join(definitions))
        summary_en = self._dedupe_translation_entries(", ".join(definitions[:3]))
        if self.word_target_lang.lower() == "en":
            self._cache_put(self._jisho_word_translation_cache, text, summary_en)
            self._cache_put(self._jisho_word_definition_cache, text, self._last_jisho_full_definition)
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
        result = self._dedupe_translation_entries(translated or summary_en)
        self._cache_put(self._jisho_word_translation_cache, text, result)
        self._cache_put(self._jisho_word_definition_cache, text, self._last_jisho_full_definition)
        return result

    def _fetch_jisho_entries(self, text: str) -> List[Dict]:
        text = (text or "").strip()
        if not text:
            return []
        cached = self._jisho_entries_cache.get(text)
        if cached is not None:
            return cached

        entries: List[Dict] = []
        try:
            payload = self._request_json(
                "GET",
                self.jisho_url,
                params={"keyword": text},
                timeout=self.http_timeout,
            )
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                entries = data
        except Exception:
            entries = []

        self._cache_put(self._jisho_entries_cache, text, entries)
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

        cached = self._word_spans_cache.get(source)
        if cached is not None:
            return [dict(span) for span in cached]

        tagger = self._get_tagger()
        if tagger is not None:
            try:
                tokens = list(tagger(source))
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
                result = self._expand_compound_word_spans(source, spans)
                self._cache_put(self._word_spans_cache, source, [dict(span) for span in result])
                return result

        result = [
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
        self._cache_put(self._word_spans_cache, source, [dict(span) for span in result])
        return result

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

        provider = str(self.sentence_translate_provider or "").strip().lower()
        translated = ""
        provider_used = "none"
        if provider == "deepl":
            translated = self._translate_deepl(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
            if translated:
                provider_used = "deepl"
            if not translated:
                translated = self._translate_google(
                    text,
                    source_lang="ja",
                    target_lang=self.sentence_target_lang,
                )
                if translated:
                    provider_used = "google"
        elif provider == "google":
            translated = self._translate_google(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
            if translated:
                provider_used = "google"
        else:
            translated = self._translate_deepl(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
            if translated:
                provider_used = "deepl"
            if not translated:
                translated = self._translate_google(
                    text,
                    source_lang="ja",
                    target_lang=self.sentence_target_lang,
                )
                if translated:
                    provider_used = "google"

        self._cache_put(self._sentence_translation_cache, text, translated)
        self._cache_put(self._sentence_provider_cache, text, provider_used)
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

    def _collect_translation_candidates(
        self,
        selected: str,
        subtitle: str,
        word_translation: str = "",
        sentence_translation: str = "",
        fetch_missing_google_sentence: bool = False,
    ) -> Dict[str, Dict[str, str]]:
        selected_text = (selected or "").strip()
        subtitle_text = self._normalize_translation_input(subtitle)

        word_candidates = {
            "jisho": "",
            "google": "",
        }
        if selected_text:
            word_candidates["jisho"] = self._jisho_word_translation_cache.get(selected_text, "")
            word_candidates["google"] = self._google_translation_cache.get(
                self._translation_cache_key(selected_text, "ja", self.word_target_lang),
                "",
            )
            word_provider = self._word_provider_cache.get(selected_text, "")
            if word_provider in word_candidates and word_translation:
                word_candidates[word_provider] = self._dedupe_translation_entries(word_translation)

        sentence_candidates = {
            "deepl": "",
            "google": "",
        }
        if subtitle_text:
            deepl_source = self._normalize_deepl_source_lang("ja")
            deepl_target = self._normalize_deepl_target_lang(self.sentence_target_lang)
            sentence_candidates["deepl"] = self._deepl_translation_cache.get(
                self._translation_cache_key(subtitle_text, deepl_source, deepl_target),
                "",
            )
            sentence_candidates["google"] = self._google_translation_cache.get(
                self._translation_cache_key(subtitle_text, "ja", self.sentence_target_lang),
                "",
            )
            if fetch_missing_google_sentence and not sentence_candidates["google"]:
                sentence_candidates["google"] = self._translate_google(
                    subtitle_text,
                    source_lang="ja",
                    target_lang=self.sentence_target_lang,
                )
            sentence_provider = self._sentence_provider_cache.get(subtitle_text, "")
            if sentence_provider in sentence_candidates and sentence_translation:
                sentence_candidates[sentence_provider] = sentence_translation

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
        cache_key = self._translation_cache_key(text, source_lang, target_lang)
        cached = self._google_translation_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            payload = self._request_json(
                "GET",
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
            translated = ""
            chunks = payload[0] if isinstance(payload, list) and payload else []
            for chunk in chunks:
                if isinstance(chunk, list) and chunk:
                    translated += str(chunk[0] or "")
            result = translated.strip()
            self._cache_put(self._google_translation_cache, cache_key, result)
            return result
        except Exception:
            self._cache_put(self._google_translation_cache, cache_key, "")
            return ""

    def _translate_deepl(self, text: str, source_lang: str, target_lang: str) -> str:
        text = (text or "").strip()
        if not text or not self.deepl_api_key:
            return ""

        source = self._normalize_deepl_source_lang(source_lang)
        target = self._normalize_deepl_target_lang(target_lang)
        if not target:
            return ""
        cache_key = self._translation_cache_key(text, source, target)
        cached = self._deepl_translation_cache.get(cache_key)
        if cached is not None:
            return cached

        payload = {
            "text": text,
            "target_lang": target,
        }
        if source:
            payload["source_lang"] = source

        try:
            data = self._request_json(
                "POST",
                self.deepl_translate_url,
                headers={"Authorization": f"DeepL-Auth-Key {self.deepl_api_key}"},
                data=payload,
                timeout=self.http_timeout,
            )
            translations = data.get("translations") or []
            if not translations:
                self._cache_put(self._deepl_translation_cache, cache_key, "")
                return ""
            result = str((translations[0] or {}).get("text") or "").strip()
            self._cache_put(self._deepl_translation_cache, cache_key, result)
            return result
        except Exception:
            self._cache_put(self._deepl_translation_cache, cache_key, "")
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
            routed["reading"].extend(to_reading)
        if to_reverse:
            routed["reverse"].extend(to_reverse)
        actions = []
        if to_reading:
            actions.append({
                "action": "changeDeck",
                "params": {"cards": to_reading, "deck": self.reading_deck},
            })
        if to_reverse:
            actions.append({
                "action": "changeDeck",
                "params": {"cards": to_reverse, "deck": self.reverse_deck},
            })
        if len(actions) == 1:
            action = actions[0]
            self._invoke(action["action"], action["params"])
        elif actions:
            self._invoke_multi(actions)
        return routed

    def _ensure_deck(self, deck_name: str) -> None:
        self._ensure_decks((deck_name,))

    def _ensure_decks(self, deck_names) -> None:
        pending: List[str] = []
        seen: set[str] = set()
        for raw_name in deck_names or ():
            name = str(raw_name or "").strip()
            if not name or name in self._ensured_decks or name in seen:
                continue
            seen.add(name)
            pending.append(name)
        if not pending:
            return

        actions = [
            {"action": "createDeck", "params": {"deck": name}}
            for name in pending
        ]
        if len(actions) == 1:
            self._invoke("createDeck", actions[0]["params"])
        else:
            self._invoke_multi(actions)
        self._ensured_decks.update(pending)

    def _get_model_field_names(self) -> set[str]:
        if self._model_fields_cache is None:
            names = self._invoke("modelFieldNames", {"modelName": self.model_name}) or []
            self._model_fields_cache = set(str(name) for name in names)
        return self._model_fields_cache

    def _invoke(self, action: str, params: Optional[Dict] = None, *, retries: int | None = None):
        payload = {
            "action": action,
            "version": 6,
            "params": params or {},
        }
        if retries is None:
            retries = 0 if action == "addNote" else 2
        try:
            data = self._request_json(
                "POST",
                self.url,
                json=payload,
                timeout=self.http_timeout,
                retries=max(0, int(retries)),
            )
        except AnkiConnectRequestError:
            logger.warning("AnkiConnect request failed for action %s", action, exc_info=True)
            raise
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data.get("result")

    def _invoke_multi(self, actions: List[Dict]) -> List:
        if not actions:
            return []
        result = self._invoke("multi", {"actions": actions}) or []
        if not isinstance(result, list):
            return []
        errors: List[str] = []
        for action, item in zip(actions, result):
            if isinstance(item, dict) and item.get("error"):
                errors.append(f"{action.get('action', 'action')}: {item.get('error')}")
        if errors:
            raise RuntimeError("; ".join(errors))
        return result

    def _sync_missing_stroke_svgs_for_new_note(self, selected_text: str, fields: Dict[str, str]) -> Dict[str, int]:
        result = {
            "enabled": int(bool(self.stroke_auto_sync)),
            "required": 0,
            "missing": 0,
            "uploaded": 0,
            "failed": 0,
        }
        if not self.stroke_auto_sync or self._closed:
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
            if self._closed:
                break
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
                response = self._request("GET", url, timeout=self.stroke_download_timeout)
                if response.content:
                    return response.content
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"No stroke SVG URL worked for {kanji_char}") from last_error

    def _store_media_file(self, filename: str, raw: bytes) -> None:
        encoded = base64.b64encode(raw).decode("ascii")
        self._invoke("storeMediaFile", {"filename": filename, "data": encoded})
