"""Prepare a browser's saved-search selection without changing the live manager."""
from copy import copy, deepcopy
from pathlib import Path
import re
import unicodedata
import requests

from model.browser_sources import BrowserSources
from model.remote_search_cache import load_remote_search_cache_payload, remote_search_cache_items


def title_key(value):
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def matching_search(records, title):
    """Prefer the longest whole title/alias; never resolve a tie arbitrarily."""
    title = title_key(title)
    matches = []
    for record in records:
        aliases = [record.get("query"), record.get("display"), *record.get("aliases", [])]
        score = max((len(key) for alias in aliases
                     if len(key := title_key(alias)) >= 3 and key in title), default=0)
        if score:
            matches.append((score, record))
    if not matches:
        return None
    best = max(score for score, _ in matches)
    winners = [record for score, record in matches if score == best]
    if len(winners) != 1:
        raise ValueError("Browser title matches several saved searches; select the source once")
    return winners[0]


class _DraftConfig:
    def __init__(self, original):
        self.original, self.values = original, {}

    def get(self, key, *args):
        return self.values[key] if key in self.values else self.original.get(key, *args)

    def set(self, key, value):
        self.values[key] = value


def prepare_selection(manager, record, episode, season, scope, *, refresh=False, allow_refresh=True):
    draft = copy(manager)
    # Copy source data, not live executors, locks or GUI-owned caches.
    for name in BrowserSources.FIELDS:
        setattr(draft, name, deepcopy(getattr(manager, name, None)))
    draft.config = _DraftConfig(manager.config)
    draft._schedule_prefetch_window = lambda *_args, **_kwargs: None
    aliases = [record.get("query"), record.get("display"), *record.get("aliases", [])]
    same_series = title_key(manager.get_anime_name()) in {title_key(alias) for alias in aliases}
    if (not refresh and same_series and manager.srt_file and Path(manager.srt_file).is_file()
            and (season is None or season == manager.get_current_season())
            and episode == (manager.get_current_global() if scope == "global" else manager.current_episode)):
        state = {name: deepcopy(getattr(manager, name, None)) for name in BrowserSources.FIELDS}
        state["display_anime_name"] = record.get("display") or record["query"]
        return state
    if season is None and scope == "season":
        if same_series:
            season = manager.get_current_season()

    excluded = set()
    download_error = None
    original_download = draft._download_file
    def download(*args, **kwargs):
        nonlocal download_error
        try:
            original_download(*args, **kwargs, raise_errors=True)
        except Exception as exc:
            download_error = exc
            raise
    draft._download_file = download

    def exact_item(*, hint=None, movie_mode=False):
        if excluded:
            draft.all_results_items = [row for row in draft.all_results_items if row.get("path") not in excluded]
            draft.build_remote_episode_maps()
        # Explicit season numbers must not fall back to an unrelated global episode.
        item = draft._remote_item_for_episode(
            season=season, episode=episode,
            global_episode=episode if season is None and scope != "season" else None)
        if item is None and season is None and scope == "season":
            candidates = [items for items in draft.remote_episode_map_season.values()
                          if any(int(row.get("episode") or 0) == episode for row in items)]
            if len(candidates) == 1:
                item = next(row for row in candidates[0] if int(row.get("episode") or 0) == episode)
        if item is None:
            raise ValueError(f"Episode {episode} is not uniquely available in this saved search")
        draft.remote_path = item.get("path")
        draft.file_name = item.get("name") or Path(item["path"]).name
        return item

    draft._choose_initial_remote_item = exact_item
    if refresh:
        result = draft._refresh_cached_github_search_record(record)
        if not result.get("ok"):
            raise ValueError("Map refresh failed: " + str(result.get("reason")))
    path = draft._initialize_remote_from_saved_search(record, hint=(season, episode))
    if (allow_refresh and isinstance(download_error, requests.HTTPError)
            and download_error.response is not None and download_error.response.status_code == 404):
        # One refresh only. A merged old path must not win provider scoring again.
        excluded.add(draft.remote_path)
        if not refresh:
            result = draft._refresh_cached_github_search_record(record)
            if not result.get("ok"):
                raise ValueError("Subtitle link expired; map refresh failed: " + str(result.get("reason")))
        payload = load_remote_search_cache_payload(record["path"])
        retained = [row for row in remote_search_cache_items(payload) if row.get("path") not in excluded]
        if retained:
            payload["items"] = retained
            payload["result_count"] = len(retained)
            draft._write_cached_github_search_payload(record["path"], payload)
        path = draft._initialize_remote_from_saved_search(record, hint=(season, episode))
    if not path or not Path(path).is_file():
        raise ValueError("Could not load the matching saved subtitle")
    draft.srt_file = path
    draft.is_movie = False
    # Never inherit the previous anime's label when a provider path has no title.
    draft.display_anime_name = record.get("display") or record["query"]
    return {name: deepcopy(getattr(draft, name, None)) for name in BrowserSources.FIELDS}
