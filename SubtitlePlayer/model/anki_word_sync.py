"""AnkiConnect word sync helpers for subtitle annotations."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html import unescape
from typing import Any, Callable, Iterable, List

from model.word_database import WordEntry, clean_import_text, normalize_word

logger = logging.getLogger(__name__)


ANKI_STATUS_PRIORITY = {
    "anki_mature": 0,
    "anki_graduated": 1,
    "anki_young": 2,
    "anki_learning": 3,
    "anki_unknown": 4,
    "ignored": 5,
}

ANKI_ANIME_TAG_PREFIX = "Anime::"


def format_anime_tag(anime_name: str) -> str:
    """Return an Anki-safe tag containing only the anime name."""
    name = unicodedata.normalize("NFKC", str(anime_name or "")).strip()
    if not name:
        return ""
    name = re.sub(r"\s+", "_", name)
    name = name.replace("::", "_").replace(":", "_")
    name = re.sub(r"[\x00-\x1f,;]+", "_", name)
    return re.sub(r"_+", "_", name).strip("_")


@dataclass
class AnkiSyncSettings:
    decks: list[str] = field(default_factory=list)
    word_fields: list[str] = field(default_factory=list)
    sentence_fields: list[str] = field(default_factory=list)
    sentence_translated_fields: list[str] = field(default_factory=list)
    reading_fields: list[str] = field(default_factory=list)
    meaning_fields: list[str] = field(default_factory=list)
    include_sentence_words: bool = False
    use_mature_threshold: bool = True
    mature_interval_days: int = 21
    suspended_as: str = "normal"
    tokenizer: Callable[[str], list[dict[str, Any]]] | None = None
    anime_names: list[str] = field(default_factory=list)


@dataclass
class AnkiSyncResult:
    entries: list[WordEntry] = field(default_factory=list)
    collected: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    failed: int = 0
    error: str = ""
    elapsed_ms: float = 0.0


def split_csv_values(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[,;]", str(value)) if part.strip()]


def strip_html(value: str | None) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return clean_import_text(unescape(text))


class AnkiWordSync:
    def __init__(self, invoker: Callable[[str, dict | None], Any]) -> None:
        self._invoke = invoker

    @classmethod
    def from_client(cls, client) -> "AnkiWordSync":
        return cls(lambda action, params=None: client._invoke(action, params or {}))

    def deck_names(self) -> list[str]:
        result = self._invoke("deckNames", {}) or []
        return sorted(str(name) for name in result)

    def model_names(self) -> list[str]:
        result = self._invoke("modelNames", {}) or []
        return sorted(str(name) for name in result)

    def model_field_names(self, model_name: str) -> list[str]:
        result = self._invoke("modelFieldNames", {"modelName": str(model_name or "")}) or []
        return [str(name) for name in result]

    def update_note_fields(self, note_id: int, fields: dict[str, str]) -> None:
        clean_fields = {str(key): str(value or "") for key, value in (fields or {}).items() if str(key or "").strip()}
        if not clean_fields:
            return
        self._invoke("updateNoteFields", {"note": {"id": int(note_id), "fields": clean_fields}})

    def entries_for_note(
        self,
        note_id: int,
        settings: AnkiSyncSettings,
        *,
        anime_name: str = "",
    ) -> list[WordEntry]:
        try:
            note_id = int(note_id)
        except Exception:
            note_id = 0
        if note_id <= 0:
            return []

        notes = self._invoke("notesInfo", {"notes": [note_id]}) or []
        note = next(
            (
                item
                for item in notes
                if int(item.get("noteId") or item.get("id") or 0) == note_id
            ),
            notes[0] if notes else None,
        )
        if not note:
            return []

        fields = note.get("fields") or {}
        summary = self._card_summary_by_note({note_id}, settings).get(note_id, {})
        resolved_anime_name = str(anime_name or "").strip() or self._anime_from_tags(
            note.get("tags"),
            settings.anime_names,
        )
        return self._entries_from_note(
            fields,
            str(summary.get("status") or "anki_unknown"),
            settings,
            note_id=note_id,
            anime_name=resolved_anime_name,
            due_date=str(summary.get("due_date") or ""),
            reviews=int(summary.get("reviews") or 0),
            card_ids=str(summary.get("card_ids") or ""),
            suspended=bool(summary.get("suspended")),
            note_modified=self._note_modified_text(note),
        )

    def sync(self, settings: AnkiSyncSettings) -> AnkiSyncResult:
        started = time.perf_counter()
        result = AnkiSyncResult()
        try:
            note_ids = self._find_note_ids(settings.decks)
            if not note_ids:
                result.elapsed_ms = (time.perf_counter() - started) * 1000.0
                return result
            notes = []
            sorted_note_ids = sorted(note_ids)
            for i in range(0, len(sorted_note_ids), 500):
                notes.extend(self._invoke("notesInfo", {"notes": sorted_note_ids[i:i + 500]}) or [])
            card_summary_by_note = self._card_summary_by_note(note_ids, settings)
            entries = []
            seen = set()
            for note in notes:
                try:
                    fields = note.get("fields") or {}
                    note_id = int(note.get("noteId") or note.get("id") or 0)
                    summary = card_summary_by_note.get(note_id, {})
                    status = str(summary.get("status") or "anki_unknown")
                    due_date = str(summary.get("due_date") or "")
                    reviews = int(summary.get("reviews") or 0)
                    note_modified = self._note_modified_text(note)
                    extracted = self._entries_from_note(
                        fields,
                        status,
                        settings,
                        note_id=note_id,
                        anime_name=self._anime_from_tags(note.get("tags"), settings.anime_names),
                        due_date=due_date,
                        reviews=reviews,
                        card_ids=str(summary.get("card_ids") or ""),
                        suspended=bool(summary.get("suspended")),
                        note_modified=note_modified,
                    )
                    if not extracted:
                        result.skipped += 1
                        reason = self._skip_reason_for_note(fields, settings)
                        result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1
                        continue
                    for entry in extracted:
                        key = normalize_word(entry.surface or entry.base)
                        if not key or key in seen:
                            continue
                        seen.add(key)
                        entries.append(entry)
                except Exception:
                    logger.debug("Failed to extract Anki note for annotation sync: %r", note, exc_info=True)
                    result.failed += 1
            result.entries = entries
            result.collected = len(entries)
        except Exception as exc:
            result.error = str(exc)
            logger.warning("Anki annotation sync failed: %s", exc, exc_info=True)
        finally:
            result.elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "Anki annotation sync finished: collected=%d skipped=%d failed=%d in %.2f ms",
                result.collected,
                result.skipped,
                result.failed,
                result.elapsed_ms,
            )
        return result

    def _skip_reason_for_note(self, fields: dict, settings: AnkiSyncSettings) -> str:
        word_fields = settings.word_fields or ["Front"]
        existing = []
        missing = []
        for field_name in word_fields:
            if field_name in fields:
                existing.append(field_name)
            else:
                missing.append(field_name)
        if not existing:
            return "word fields missing"
        if not any(self._field_value(fields, field_name) for field_name in existing):
            return "word fields empty"
        return "no usable words"

    def _find_note_ids(self, decks: Iterable[str]) -> set[int]:
        note_ids: set[int] = set()
        for deck in decks:
            deck = str(deck or "").strip()
            if not deck:
                continue
            query = f'deck:"{deck}"'
            found = self._invoke("findNotes", {"query": query}) or []
            for note_id in found:
                try:
                    note_ids.add(int(note_id))
                except Exception:
                    continue
        return note_ids

    def _card_summary_by_note(self, note_ids: set[int], settings: AnkiSyncSettings) -> dict[int, dict[str, int | str | bool]]:
        if not note_ids:
            return {}
        cards = []
        due_cards = set()
        sorted_ids = sorted(note_ids)
        chunk_size = 100
        for i in range(0, len(sorted_ids), chunk_size):
            query = " OR ".join(f"nid:{nid}" for nid in sorted_ids[i:i + chunk_size])
            if not query:
                continue
            cards.extend(self._invoke("findCards", {"query": query}) or [])
            for card_id in self._invoke("findCards", {"query": f"({query}) is:due"}) or []:
                try:
                    due_cards.add(int(card_id))
                except Exception:
                    continue
        scheduler_today = self._scheduler_today()
        if not cards:
            return {
                int(note_id): {
                    "status": "anki_unknown",
                    "due_date": "",
                    "reviews": 0,
                    "card_ids": "",
                    "suspended": False,
                }
                for note_id in note_ids
            }
        infos = []
        for i in range(0, len(cards), 500):
            infos.extend(self._invoke("cardsInfo", {"cards": cards[i:i + 500]}) or [])
        by_note: dict[int, list[str]] = {}
        due_by_note: dict[int, list[str]] = {}
        reviews_by_note: dict[int, int] = {}
        card_ids_by_note: dict[int, list[int]] = {}
        suspended_by_note: dict[int, bool] = {}
        for info in infos:
            try:
                note_id = int(info.get("note") or info.get("noteId") or 0)
            except Exception:
                continue
            status = classify_card(
                info,
                settings.mature_interval_days,
                settings.suspended_as,
                settings.use_mature_threshold,
            )
            by_note.setdefault(note_id, []).append(status)
            try:
                card_id = int(info.get("cardId") or info.get("card_id") or info.get("id") or 0)
            except Exception:
                card_id = 0
            if card_id:
                card_ids_by_note.setdefault(note_id, []).append(card_id)
            due_text = self._card_due_text(info, bool(card_id and card_id in due_cards), scheduler_today=scheduler_today)
            if due_text:
                due_by_note.setdefault(note_id, []).append(due_text)
            reviews_by_note[note_id] = reviews_by_note.get(note_id, 0) + self._card_review_count(info)
            try:
                if int(info.get("queue")) == -1:
                    suspended_by_note[note_id] = True
            except Exception:
                pass
        return {
            note_id: {
                "status": self._best_status(statuses),
                "due_date": self._best_due_text(due_by_note.get(note_id, [])),
                "reviews": int(reviews_by_note.get(note_id, 0)),
                "card_ids": ",".join(str(card_id) for card_id in sorted(set(card_ids_by_note.get(note_id, [])))),
                "suspended": bool(suspended_by_note.get(note_id, False)),
            }
            for note_id, statuses in by_note.items()
        }

    def _scheduler_today(self) -> int | None:
        for query in ("prop:due=0 is:review", "prop:due=0"):
            try:
                cards = self._invoke("findCards", {"query": query}) or []
            except Exception:
                continue
            if not cards:
                continue
            try:
                infos = self._invoke("cardsInfo", {"cards": cards[:20]}) or []
            except Exception:
                continue
            for info in infos:
                try:
                    queue_i = int(info.get("queue") or 0)
                    type_i = int(info.get("type") or 0)
                    due_i = int(float(info.get("due")))
                except Exception:
                    continue
                if due_i > 0 and due_i < 1000000000 and (queue_i == 2 or type_i == 2):
                    return due_i
        return None

    @staticmethod
    def _card_review_count(info: dict) -> int:
        for key in ("reps", "reviews", "review_count"):
            try:
                return max(0, int(info.get(key) or 0))
            except Exception:
                continue
        return 0

    @staticmethod
    def _card_due_text(info: dict, due_now: bool, scheduler_today: int | None = None) -> str:
        try:
            queue_i = int(info.get("queue") or 0)
        except Exception:
            queue_i = 0
        try:
            type_i = int(info.get("type") or 0)
        except Exception:
            type_i = 0
        due_raw = info.get("due")
        try:
            due_i = int(float(due_raw))
        except Exception:
            due_i = 0

        if queue_i < 0:
            return ""
        if queue_i in {1, 3}:
            if due_i > 1000000000:
                return AnkiWordSync._format_due_date(time.localtime(due_i))
            if due_now:
                return AnkiWordSync._format_due_date(time.localtime())
            return ""
        if queue_i == 0 or type_i == 0:
            return ""
        if type_i == 2 or queue_i == 2:
            if due_i > 1000000000:
                return AnkiWordSync._format_due_date(time.localtime(due_i))
            if scheduler_today is not None and due_i > 0:
                target = datetime.now().date() + timedelta(days=int(due_i) - int(scheduler_today))
                return target.strftime("%d.%m.%Y")
            if due_now:
                return AnkiWordSync._format_due_date(time.localtime())
            return ""
        if due_now:
            return AnkiWordSync._format_due_date(time.localtime())
        return ""

    @staticmethod
    def _best_due_text(values: list[str]) -> str:
        cleaned = [
            AnkiWordSync._normalize_due_text(str(value or "").strip())
            for value in values
            if str(value or "").strip()
        ]
        cleaned = [value for value in cleaned if value]
        if not cleaned:
            return ""
        today = AnkiWordSync._format_due_date(time.localtime())
        if today in cleaned:
            return today
        dated = [value for value in cleaned if re.match(r"^\d{2}\.\d{2}\.\d{4}$", value)]
        if dated:
            return min(dated, key=lambda value: datetime.strptime(value, "%d.%m.%Y"))
        return cleaned[0]

    @staticmethod
    def _format_due_date(date_tuple) -> str:
        return time.strftime("%d.%m.%Y", date_tuple)

    @staticmethod
    def _normalize_due_text(value: str) -> str:
        text = re.sub(r"^Due\s+", "", str(value or "").strip(), flags=re.IGNORECASE)
        if not text:
            return ""
        for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
            except Exception:
                continue
        return text

    @staticmethod
    def _best_status(statuses: list[str]) -> str:
        if not statuses:
            return "anki_unknown"
        return sorted(statuses, key=lambda status: ANKI_STATUS_PRIORITY.get(status, 90))[0]

    def _entries_from_note(
        self,
        fields: dict,
        status: str,
        settings: AnkiSyncSettings,
        note_id: int = 0,
        anime_name: str = "",
        due_date: str = "",
        reviews: int = 0,
        card_ids: str = "",
        suspended: bool = False,
        note_modified: str = "",
    ) -> list[WordEntry]:
        word_fields = settings.word_fields or ["Front"]
        reading = self._first_field_value(fields, settings.reading_fields)
        meaning = self._first_field_value(fields, settings.meaning_fields)
        sentence = self._first_field_value(fields, settings.sentence_fields)
        sentence_translated = self._first_field_value(fields, settings.sentence_translated_fields)
        common_extra = {
            "note_id": int(note_id),
            "card_ids": str(card_ids or ""),
            "due_date": str(due_date or ""),
            "reviews": int(reviews or 0),
            "suspended": bool(suspended),
            "note_modified": str(note_modified or ""),
            "sentence": sentence,
            "sentence_translated": sentence_translated,
            "reading_field": self._first_present_field_name(fields, settings.reading_fields),
            "meaning_field": self._first_present_field_name(fields, settings.meaning_fields),
            "sentence_field": self._first_present_field_name(fields, settings.sentence_fields),
            "sentence_translated_field": self._first_present_field_name(fields, settings.sentence_translated_fields),
        }
        entries: list[WordEntry] = []
        for field_name in word_fields:
            text = self._field_value(fields, field_name)
            if not text:
                continue
            for word in self._split_field_words(text):
                entries.append(
                    WordEntry(
                        surface=word,
                        reading=reading,
                        meaning=meaning,
                        anime=anime_name,
                        source="anki",
                        status=status,
                        extra={**common_extra, "field": field_name, "field_value": text},
                    )
                )

        if settings.include_sentence_words and settings.tokenizer:
            for field_name in settings.sentence_fields:
                sentence = self._field_value(fields, field_name)
                if not sentence:
                    continue
                for token in settings.tokenizer(sentence):
                    surface = str(token.get("lookup") or token.get("surface") or "").strip()
                    if not surface:
                        continue
                    entries.append(
                        WordEntry(
                            surface=surface,
                            reading=str(token.get("reading") or ""),
                            meaning=meaning,
                            anime=anime_name,
                            source="anki",
                            status=status,
                            extra={**common_extra, "field": field_name, "sentence": sentence, "from_sentence": True},
                        )
                    )
        return entries

    @staticmethod
    def _anime_from_tags(tags, known_anime_names: Iterable[str] | None = None) -> str:
        if isinstance(tags, str):
            values = tags.split()
        elif isinstance(tags, (list, tuple, set)):
            values = [str(tag or "").strip() for tag in tags]
        else:
            values = []
        prefix_folded = ANKI_ANIME_TAG_PREFIX.casefold()
        for tag in values:
            value = str(tag or "").strip()
            if not value.casefold().startswith(prefix_folded):
                continue
            anime = value[len(ANKI_ANIME_TAG_PREFIX):].replace("_", " ").strip()
            if anime:
                return anime
        folded_tags = {str(tag or "").strip().casefold() for tag in values if str(tag or "").strip()}
        for anime_name in known_anime_names or ():
            display_name = unicodedata.normalize("NFKC", str(anime_name or "")).strip()
            plain_tag = format_anime_tag(display_name)
            if plain_tag and plain_tag.casefold() in folded_tags:
                return display_name
        return ""

    @staticmethod
    def _note_modified_text(note: dict) -> str:
        for key in ("mod", "modified", "modificationTime", "modification_time", "updated"):
            value = note.get(key)
            if value in (None, ""):
                continue
            text = str(value).strip()
            try:
                timestamp = int(float(text))
                if timestamp > 100000000000:
                    timestamp //= 1000
                if timestamp > 0:
                    return time.strftime("%H:%M %d-%m-%Y", time.localtime(timestamp))
            except Exception:
                parsed = AnkiWordSync._parse_modified_text(text)
                return parsed or text
            parsed = AnkiWordSync._parse_modified_text(text)
            return parsed or text
        return ""

    @staticmethod
    def _parse_modified_text(text: str) -> str:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
            try:
                return datetime.strptime(str(text or "").strip(), fmt).strftime("%H:%M %d-%m-%Y")
            except Exception:
                continue
        return ""

    @staticmethod
    def _field_value(fields: dict, field_name: str) -> str:
        field = fields.get(field_name)
        if isinstance(field, dict):
            field = field.get("value")
        return strip_html(field)

    def _first_field_value(self, fields: dict, names: list[str]) -> str:
        for name in names or []:
            value = self._field_value(fields, name)
            if value:
                return value
        return ""

    def _first_present_field_name(self, fields: dict, names: list[str]) -> str:
        for name in names or []:
            if name in fields:
                return str(name)
        return ""

    @staticmethod
    def _split_field_words(text: str) -> list[str]:
        cleaned = strip_html(text)
        cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
        parts = re.split(r"[,;/\n\r\t、，]+", cleaned)
        words = []
        for part in parts:
            value = clean_import_text(part)
            if value:
                words.append(value)
        return words


def classify_card(
    card: dict[str, Any],
    mature_interval_days: int = 21,
    suspended_as: str = "normal",
    use_mature_threshold: bool = True,
) -> str:
    suspended_as = str(suspended_as or "normal").strip().lower()
    forced = {
        "mature": "anki_mature",
        "young": "anki_young",
        "graduated": "anki_graduated",
        "learning": "anki_learning",
        "unknown": "anki_unknown",
        "ignored": "ignored",
    }.get(suspended_as)

    queue = card.get("queue")
    card_type = card.get("type")
    try:
        queue_i = int(queue)
    except Exception:
        queue_i = 0
    try:
        type_i = int(card_type)
    except Exception:
        type_i = 0
    try:
        interval = int(card.get("interval", card.get("ivl", 0)) or 0)
    except Exception:
        interval = 0
    threshold = max(1, int(mature_interval_days or 21))

    if queue_i < 0 and forced:
        return forced

    if queue_i in {1, 3} or type_i in {1, 3}:
        return "anki_learning"
    if queue_i == 0 or type_i == 0:
        return "anki_unknown"
    if type_i == 2 or queue_i == 2:
        if not use_mature_threshold:
            return "anki_graduated"
        if interval >= threshold:
            return "anki_mature"
        return "anki_young"
    if not use_mature_threshold and interval > 0:
        return "anki_graduated"
    if interval >= threshold:
        return "anki_mature"
    if interval > 0:
        return "anki_young"
    return "anki_unknown"
