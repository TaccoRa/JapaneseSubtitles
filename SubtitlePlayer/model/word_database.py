"""Persistent local word database for subtitle annotations."""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_word(text: str | None) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    value = re.sub(r"\s+", "", value)
    return value.casefold()


_GODAN_RENYOU_ENDINGS = {
    "い": "う",
    "き": "く",
    "ぎ": "ぐ",
    "し": "す",
    "ち": "つ",
    "に": "ぬ",
    "び": "ぶ",
    "み": "む",
    "り": "る",
}


def search_query_variants(text: str | None) -> list[str]:
    value = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not value:
        return []
    variants = [value]
    compact = re.sub(r"\s+", "", value)
    if compact and compact not in variants:
        variants.append(compact)
    last = compact[-1:] if compact else ""
    replacement = _GODAN_RENYOU_ENDINGS.get(last)
    if replacement and len(compact) > 1:
        variants.append(compact[:-1] + replacement)
    out = []
    seen = set()
    for variant in variants:
        normalized = normalize_word(variant)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def clean_import_text(text: str | None) -> str:
    value = str(text or "")
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\[[^\]]*\]", "", value)
    return unicodedata.normalize("NFKC", value).strip()


@dataclass
class WordEntry:
    surface: str
    normalized: str = ""
    base: str = ""
    reading: str = ""
    meaning: str = ""
    source: str = "local"
    status: str = "local_known"
    created_at: str = ""
    updated_at: str = ""
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.surface = clean_import_text(self.surface)
        self.base = clean_import_text(self.base)
        self.reading = clean_import_text(self.reading)
        self.meaning = str(self.meaning or "").strip()
        self.source = str(self.source or "local").strip() or "local"
        self.status = str(self.status or "local_known").strip() or "local_known"
        self.normalized = normalize_word(self.normalized or self.base or self.surface)
        now = utc_now_iso()
        self.created_at = str(self.created_at or now)
        self.updated_at = str(self.updated_at or now)
        self.notes = str(self.notes or "")
        if not isinstance(self.extra, dict):
            self.extra = {}

    @property
    def key(self) -> str:
        return make_entry_key(self.source, self.normalized)

    @classmethod
    def from_dict(cls, data: dict) -> "WordEntry":
        payload = dict(data or {})
        return cls(
            surface=payload.get("surface") or payload.get("word") or "",
            normalized=payload.get("normalized") or "",
            base=payload.get("base") or payload.get("lemma") or "",
            reading=payload.get("reading") or "",
            meaning=payload.get("meaning") or payload.get("translation") or "",
            source=payload.get("source") or "local",
            status=payload.get("status") or "local_known",
            created_at=payload.get("created_at") or "",
            updated_at=payload.get("updated_at") or "",
            notes=payload.get("notes") or "",
            extra=payload.get("extra") if isinstance(payload.get("extra"), dict) else {},
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["normalized"] = self.normalized or normalize_word(self.base or self.surface)
        return data


def make_entry_key(source: str, normalized: str) -> str:
    return f"{str(source or 'local').strip() or 'local'}:{normalize_word(normalized)}"


class WordDatabase:
    """Small JSON-backed word cache shared by local, Anki, and WaniKani sources."""

    def __init__(self, path: str) -> None:
        self.path = os.path.abspath(path)
        self._entries: dict[str, WordEntry] = {}
        self.last_error = ""
        self.load()

    def load(self) -> None:
        started = time.perf_counter()
        self._entries = {}
        self.last_error = ""
        if not os.path.exists(self.path):
            logger.debug("Annotation word database not found; starting empty: %s", self.path)
            return
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            raw_entries = data.get("words", data if isinstance(data, list) else [])
            if not isinstance(raw_entries, list):
                raise ValueError("word database JSON must contain a words list")
            for item in raw_entries:
                try:
                    entry = WordEntry.from_dict(item)
                except Exception:
                    logger.debug("Skipping invalid word database row: %r", item, exc_info=True)
                    continue
                if entry.normalized:
                    self._entries[entry.key] = entry
            logger.debug(
                "Loaded annotation word database: %d entries in %.2f ms",
                len(self._entries),
                (time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Failed to load annotation word database: %s", self.path)
            self._entries = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": utc_now_iso(),
            "words": [entry.to_dict() for entry in self.list_entries()],
        }
        fd, tmp_path = tempfile.mkstemp(prefix=".annotation_words_", suffix=".json", dir=os.path.dirname(self.path) or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def list_entries(self, *, source: str | None = None) -> List[WordEntry]:
        entries = list(self._entries.values())
        if source:
            entries = [entry for entry in entries if entry.source == source]
        return sorted(entries, key=lambda entry: (entry.source, entry.normalized, entry.surface))

    def search(self, query: str = "") -> List[WordEntry]:
        needles = search_query_variants(query)
        if not needles:
            return self.list_entries()
        results = []
        for entry in self.list_entries():
            haystack = (
                normalize_word(entry.surface),
                normalize_word(entry.base),
                normalize_word(entry.reading),
                normalize_word(entry.meaning),
                normalize_word(entry.status),
                normalize_word(entry.source),
            )
            if any(needle in value for needle in needles for value in haystack):
                results.append(entry)
        return results

    def get(self, source: str, text: str) -> Optional[WordEntry]:
        return self._entries.get(make_entry_key(source, normalize_word(text)))

    def get_key(self, key: str) -> Optional[WordEntry]:
        return self._entries.get(str(key or ""))

    def upsert(self, entry: WordEntry, *, preserve_existing: bool = True, save: bool = True) -> WordEntry:
        incoming = WordEntry.from_dict(entry.to_dict())
        key = incoming.key
        existing = self._entries.get(key)
        if existing and preserve_existing:
            incoming.created_at = existing.created_at
            incoming.updated_at = utc_now_iso()
            for attr in ("surface", "base", "reading", "meaning", "notes"):
                new_value = getattr(incoming, attr)
                old_value = getattr(existing, attr)
                if not new_value and old_value:
                    setattr(incoming, attr, old_value)
            merged_extra = dict(existing.extra or {})
            merged_extra.update(incoming.extra or {})
            incoming.extra = merged_extra
        else:
            incoming.updated_at = utc_now_iso()
        self._entries[key] = incoming
        if save:
            self.save()
        return incoming

    def upsert_many(self, entries: Iterable[WordEntry], *, preserve_existing: bool = True, replace_source: str | None = None) -> int:
        if replace_source:
            self.delete_source(replace_source, save=False)
        count = 0
        for entry in entries:
            if not normalize_word(entry.surface or entry.base):
                continue
            self.upsert(entry, preserve_existing=preserve_existing, save=False)
            count += 1
        self.save()
        return count

    def delete(self, source: str, text_or_normalized: str, *, save: bool = True) -> bool:
        key = make_entry_key(source, text_or_normalized)
        existed = key in self._entries
        self._entries.pop(key, None)
        if existed and save:
            self.save()
        return existed

    def delete_key(self, key: str, *, save: bool = True) -> bool:
        existed = key in self._entries
        self._entries.pop(key, None)
        if existed and save:
            self.save()
        return existed

    def delete_source(self, source: str, *, save: bool = True) -> int:
        source = str(source or "").strip()
        keys = [key for key, entry in self._entries.items() if entry.source == source]
        for key in keys:
            self._entries.pop(key, None)
        if keys and save:
            self.save()
        return len(keys)

    def import_file(self, path: str, *, source: str = "local", status: str = "local_known") -> dict:
        ext = os.path.splitext(str(path or ""))[1].lower()
        if ext == ".json":
            entries = self._read_json_import(path, source=source, status=status)
        elif ext == ".csv":
            entries = self._read_csv_import(path, source=source, status=status)
        else:
            entries = self._read_text_import(path, source=source, status=status)
        before = len(self._entries)
        imported = self.upsert_many(entries, preserve_existing=True)
        return {
            "imported": imported,
            "total": len(self._entries),
            "new": max(0, len(self._entries) - before),
        }

    def _read_text_import(self, path: str, *, source: str, status: str) -> List[WordEntry]:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        return [
            WordEntry(surface=line.strip(), source=source, status=status)
            for line in lines
            if line.strip()
        ]

    def _read_json_import(self, path: str, *, source: str, status: str) -> List[WordEntry]:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        rows = data.get("words", data if isinstance(data, list) else [])
        if not isinstance(rows, list):
            raise ValueError("JSON import must be a list or contain a words list")
        entries = []
        for row in rows:
            if isinstance(row, str):
                entries.append(WordEntry(surface=row, source=source, status=status))
            elif isinstance(row, dict):
                payload = dict(row)
                payload.setdefault("source", source)
                payload.setdefault("status", status)
                entries.append(WordEntry.from_dict(payload))
        return entries

    def _read_csv_import(self, path: str, *, source: str, status: str) -> List[WordEntry]:
        entries = []
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(2048)
            f.seek(0)
            has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
            if has_header:
                reader = csv.DictReader(f)
                for row in reader:
                    payload = dict(row)
                    payload.setdefault("source", source)
                    payload.setdefault("status", status)
                    entries.append(WordEntry.from_dict(payload))
            else:
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    entries.append(
                        WordEntry(
                            surface=row[0],
                            reading=row[1] if len(row) > 1 else "",
                            meaning=row[2] if len(row) > 2 else "",
                            source=source,
                            status=status,
                        )
                    )
        return entries

    def export_file(self, path: str, *, source: str | None = None) -> dict:
        ext = os.path.splitext(str(path or ""))[1].lower()
        entries = self.list_entries(source=source)
        if ext == ".json":
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "words": [entry.to_dict() for entry in entries]}, f, ensure_ascii=False, indent=2)
        elif ext == ".csv":
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "surface",
                        "base",
                        "reading",
                        "meaning",
                        "source",
                        "status",
                        "updated_at",
                        "notes",
                    ],
                )
                writer.writeheader()
                for entry in entries:
                    writer.writerow(
                        {
                            "surface": entry.surface,
                            "base": entry.base,
                            "reading": entry.reading,
                            "meaning": entry.meaning,
                            "source": entry.source,
                            "status": entry.status,
                            "updated_at": entry.updated_at,
                            "notes": entry.notes,
                        }
                    )
        else:
            raise ValueError("Export path must end in .json or .csv")
        return {"exported": len(entries), "path": path}
