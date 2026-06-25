"""Versioned cache helpers for remote GitHub subtitle search results."""

from __future__ import annotations

import datetime as _datetime
import json
from collections import OrderedDict
from typing import Any, Iterable

REMOTE_SEARCH_CACHE_SCHEMA_VERSION = 2


def normalize_remote_search_cache_payload(payload: Any) -> OrderedDict:
    """Return a dict-shaped cache payload while accepting legacy files."""
    if isinstance(payload, list):
        return OrderedDict(
            [
                ("schema_version", 1),
                ("items", payload),
                ("result_count", len(payload)),
            ]
        )
    if not isinstance(payload, dict):
        return OrderedDict(
            [
                ("schema_version", 1),
                ("items", []),
                ("result_count", 0),
            ]
        )
    normalized = OrderedDict(payload)
    normalized.setdefault("schema_version", 1)
    normalized.setdefault("items", [])
    return normalized


def load_remote_search_cache_payload(path: str) -> OrderedDict:
    with open(path, "r", encoding="utf-8") as fh:
        return normalize_remote_search_cache_payload(json.load(fh, object_pairs_hook=OrderedDict))


def remote_search_cache_items(payload: Any) -> list[dict]:
    normalized = normalize_remote_search_cache_payload(payload)
    items = normalized.get("items")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def remote_search_cache_repo_matches(payload: Any, expected_repo: str | None) -> bool:
    normalized = normalize_remote_search_cache_payload(payload)
    repo = normalized.get("repo")
    if not repo or not expected_repo:
        return True
    return str(repo) == str(expected_repo)


def build_remote_search_cache_payload(
    *,
    anime_query: str,
    last_search: str,
    repo: str,
    items: Iterable[dict],
    sxexx_to_gxx: dict | None = None,
    stop_reason: str | None = None,
    rate_info: dict | None = None,
) -> OrderedDict:
    items_list = [dict(item) for item in items]
    payload = OrderedDict()
    payload["schema_version"] = REMOTE_SEARCH_CACHE_SCHEMA_VERSION
    payload["anime_query"] = anime_query
    payload["last_search"] = last_search
    payload["last search"] = last_search
    payload["repo"] = repo
    payload["created_at"] = _datetime.datetime.now(_datetime.UTC).isoformat().replace("+00:00", "Z")
    if stop_reason is not None:
        payload["stop_reason"] = stop_reason
    if rate_info is not None:
        payload["rate_info"] = dict(rate_info)
    payload["result_count"] = len(items_list)
    payload["items"] = items_list
    payload["sxexx_to_gxx"] = dict(sxexx_to_gxx or {})
    return payload
