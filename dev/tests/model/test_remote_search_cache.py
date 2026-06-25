import json

from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.model.remote_search_cache import (
    REMOTE_SEARCH_CACHE_SCHEMA_VERSION,
    build_remote_search_cache_payload,
    load_remote_search_cache_payload,
    remote_search_cache_items,
    remote_search_cache_repo_matches,
)


def test_versioned_remote_cache_write_shape(tmp_path):
    payload = build_remote_search_cache_payload(
        anime_query="test anime",
        last_search="repo:o/r path:subtitles test",
        repo="o/r",
        items=[{"name": "S01E01.srt", "path": "subtitles/S01E01.srt"}],
        sxexx_to_gxx={"S01E01": 1},
        stop_reason="done",
        rate_info={"remaining": "10"},
    )

    assert payload["schema_version"] == REMOTE_SEARCH_CACHE_SCHEMA_VERSION
    assert payload["last_search"] == payload["last search"]
    assert payload["result_count"] == 1
    assert payload["sxexx_to_gxx"] == {"S01E01": 1}

    path = tmp_path / "cache.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_remote_search_cache_payload(str(path))

    assert remote_search_cache_repo_matches(loaded, "o/r") is True
    assert remote_search_cache_items(loaded)[0]["name"] == "S01E01.srt"


def test_legacy_remote_cache_payload_is_readable(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps({"repo": "o/r", "items": [{"name": "legacy.srt", "path": "legacy.srt"}]}),
        encoding="utf-8",
    )

    loaded = load_remote_search_cache_payload(str(path))

    assert loaded["schema_version"] == 1
    assert remote_search_cache_repo_matches(loaded, "o/r") is True
    assert remote_search_cache_repo_matches(loaded, "other/repo") is False
    assert remote_search_cache_items(loaded) == [{"name": "legacy.srt", "path": "legacy.srt"}]


def test_legacy_list_remote_cache_payload_is_readable(tmp_path):
    path = tmp_path / "legacy-list.json"
    path.write_text(json.dumps([{"name": "one.srt"}]), encoding="utf-8")

    loaded = load_remote_search_cache_payload(str(path))

    assert loaded["schema_version"] == 1
    assert remote_search_cache_items(loaded) == [{"name": "one.srt"}]


def test_saved_search_rename_keeps_old_name_as_filter_alias(tmp_path):
    manager = object.__new__(SubtitleManager)
    manager._cached_github_search_dir = lambda: str(tmp_path)

    payload = build_remote_search_cache_payload(
        anime_query="Old Name",
        last_search='repo:o/r path:subtitles "Old Name"',
        repo="o/r",
        items=[{"name": "S01E01.srt", "path": "subtitles/S01E01.srt"}],
    )
    path = tmp_path / "github_search_Old_Name.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    records = manager._list_cached_github_search_records()
    assert records[0]["display"] == "Old Name"
    assert records[0]["query"] == "Old Name"

    assert manager._rename_cached_github_search_record(records[0], "Better Name") is True

    renamed = manager._list_cached_github_search_records()[0]
    assert renamed["display"] == "Better Name"
    assert renamed["query"] == "Old Name"
    assert "better name" in renamed["filter_text"]
    assert "old name" in renamed["filter_text"]

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema_version"] == REMOTE_SEARCH_CACHE_SCHEMA_VERSION
    assert stored["display_name"] == "Better Name"
    assert "Old Name" in stored["aliases"]
