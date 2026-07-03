"""Small WaniKani API v2 client for annotation word sync."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

import requests

from model.word_database import WordEntry

logger = logging.getLogger(__name__)


WANIKANI_API_BASE = "https://api.wanikani.com/v2"


@dataclass
class WaniKaniSyncResult:
    entries: list[WordEntry] = field(default_factory=list)
    vocabulary_count: int = 0
    kanji_count: int = 0
    failed: int = 0
    error: str = ""
    elapsed_ms: float = 0.0


def map_srs_stage(stage: int | None) -> str:
    try:
        value = int(stage or 0)
    except Exception:
        value = 0
    if value >= 9:
        return "wanikani_burned"
    if value == 8:
        return "wanikani_enlightened"
    if value == 7:
        return "wanikani_master"
    if 5 <= value <= 6:
        return "wanikani_guru"
    if 1 <= value <= 4:
        return "wanikani_apprentice"
    return "wanikani_unlocked"


class WaniKaniClient:
    def __init__(self, token: str, *, session=None, timeout: float = 12.0, api_base: str = WANIKANI_API_BASE) -> None:
        self.token = str(token or "").strip()
        self.session = session or requests.Session()
        self.timeout = float(timeout or 12.0)
        self.api_base = api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Wanikani-Revision": "20170710",
        }

    def test_token(self) -> tuple[bool, str]:
        if not self.token:
            return False, "No WaniKani API token configured."
        try:
            data = self._get_json(f"{self.api_base}/user")
            username = ((data.get("data") or {}).get("username") or "").strip()
            return True, f"Connected{f' as {username}' if username else ''}."
        except Exception as exc:
            logger.warning("WaniKani token test failed: %s", exc, exc_info=True)
            return False, str(exc)

    def sync(self) -> WaniKaniSyncResult:
        started = time.perf_counter()
        result = WaniKaniSyncResult()
        if not self.token:
            result.error = "No WaniKani API token configured."
            return result
        try:
            assignments = self._fetch_paginated(
                f"{self.api_base}/assignments",
                params={"subject_types": "vocabulary,kanji", "unlocked": "true"},
            )
            subject_ids = sorted(
                {
                    int((item.get("data") or {}).get("subject_id"))
                    for item in assignments
                    if (item.get("data") or {}).get("subject_id") is not None
                }
            )
            subjects = self._fetch_subjects(subject_ids)
            subject_by_id = {int(item.get("id")): item for item in subjects if item.get("id") is not None}
            entries = []
            for assignment in assignments:
                try:
                    data = assignment.get("data") or {}
                    subject_id = int(data.get("subject_id"))
                    subject = subject_by_id.get(subject_id)
                    if not subject:
                        continue
                    subject_data = subject.get("data") or {}
                    subject_type = str(subject.get("object") or data.get("subject_type") or "")
                    status = map_srs_stage(data.get("srs_stage"))
                    characters = str(subject_data.get("characters") or "").strip()
                    if not characters:
                        continue
                    reading = self._primary_reading(subject_data.get("readings") or [])
                    meaning = self._primary_meaning(subject_data.get("meanings") or [])
                    entries.append(
                        WordEntry(
                            surface=characters,
                            reading=reading,
                            meaning=meaning,
                            source="wanikani",
                            status=status,
                            extra={"subject_id": subject_id, "subject_type": subject_type},
                        )
                    )
                    if subject_type == "vocabulary":
                        result.vocabulary_count += 1
                    elif subject_type == "kanji":
                        result.kanji_count += 1
                except Exception:
                    logger.debug("Failed to convert WaniKani assignment: %r", assignment, exc_info=True)
                    result.failed += 1
            result.entries = entries
        except Exception as exc:
            result.error = str(exc)
            logger.warning("WaniKani sync failed: %s", exc, exc_info=True)
        finally:
            result.elapsed_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "WaniKani sync finished: vocabulary=%d kanji=%d failed=%d in %.2f ms",
                result.vocabulary_count,
                result.kanji_count,
                result.failed,
                result.elapsed_ms,
            )
        return result

    def _get_json(self, url: str, *, params: dict | None = None) -> dict[str, Any]:
        response = self.session.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        if response.status_code == 401:
            raise RuntimeError("WaniKani API token is invalid.")
        if response.status_code == 429:
            raise RuntimeError("WaniKani rate limit reached. Try again later.")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected WaniKani API response.")
        return data

    def _fetch_paginated(self, url: str, *, params: dict | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url = url
        next_params = dict(params or {})
        while next_url:
            data = self._get_json(next_url, params=next_params)
            page_items = data.get("data") or []
            if not isinstance(page_items, list):
                raise RuntimeError("Unexpected WaniKani page data.")
            items.extend(page_items)
            pages = data.get("pages") or {}
            next_url = pages.get("next_url") or ""
            next_params = None
        return items

    def _fetch_subjects(self, subject_ids: Iterable[int]) -> list[dict[str, Any]]:
        ids = [int(value) for value in subject_ids]
        subjects: list[dict[str, Any]] = []
        chunk_size = 500
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            if not chunk:
                continue
            subjects.extend(
                self._fetch_paginated(
                    f"{self.api_base}/subjects",
                    params={"ids": ",".join(str(value) for value in chunk)},
                )
            )
        return subjects

    @staticmethod
    def _primary_reading(readings: list[dict[str, Any]]) -> str:
        for item in readings:
            if item.get("primary"):
                return str(item.get("reading") or "").strip()
        if readings:
            return str(readings[0].get("reading") or "").strip()
        return ""

    @staticmethod
    def _primary_meaning(meanings: list[dict[str, Any]]) -> str:
        for item in meanings:
            if item.get("primary"):
                return str(item.get("meaning") or "").strip()
        if meanings:
            return str(meanings[0].get("meaning") or "").strip()
        return ""
