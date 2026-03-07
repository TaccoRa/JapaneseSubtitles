"""
SubtitleManager (model) handles:
- Loading local or cached subtitle files (.srt / .ass / .ssa)
- Parsing and building display data used by the renderer/controller
- Building episode maps (local directory + remote GitHub search results)
- Downloading missing remote episodes into a runtime cache
"""

import os
import re
import shutil
import requests
import json
import time
import datetime
import atexit
import bisect
import queue
from urllib.parse import urlparse, unquote
from typing import List, Optional, Tuple, Dict
import threading
from collections import defaultdict, Counter
import statistics

import regex
import srt
import chardet
import tkinter as tk
from tkinter import font as tkFont
from tkinter import filedialog

from model.config_manager import ConfigManager
from utils import format_time
from view.overlays import LoadingOverlay, get_startup_overlay, hide_startup_overlay, show_startup_overlay

import logging
logger = logging.getLogger(__name__)
logging.Formatter.converter = time.gmtime

class SubtitleManager:

    CLEAN_PATTERN = re.compile(r'\{\\an\d+\}')
    TAG_PATTERN = re.compile(r'<[^>]*>')
    SEASON_PATTERN = re.compile(r'S(\d+)', re.IGNORECASE)
    EPISODE_PATTERN = re.compile(r'E(\d+)', re.IGNORECASE)
    RUBY_PATTERN = regex.compile(r'(\p{Han}+)\(([^)]+)\)')

    RESOLUTION_RE = re.compile(r'^\d{3,4}p$', re.IGNORECASE)
    RESOLUTION_X_RE = re.compile(r'^\d{3,4}x\d{3,4}$', re.IGNORECASE)
    VIDEO_CODEC_RE = re.compile(r'^(x265|h264|av1|hevc|x264)$', re.IGNORECASE)
    NOISE_TOKENS = {'bd','web','webrip','bluray','bdrip','dvd','x264','x265','av1','hevc',
                    'aac','flac','hdtv','bdrip','bs8','netflix','amazon', 'fansub','group','copy','complete','ja[cc]'}

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.github_token = os.environ.get("GITHUB_TOKEN")
        self.remote_flag = self.config.get("REMOTE_FLAG")

        if self.remote_flag:
            url = self.config.get("LAST_GITHUB_URL")
            # If we don't have a URL yet, fall back to the same "choose source" dialog used by set_new_file().
            # This gives the user local/remote-url/remote-search options at startup too.
            local_srt_path = self._initialize_remote_path(url) if url else None
            self._register_cache_cleanup()
        else:
            local_srt_path = self.config.get("LAST_LOCAL_SRT_FILE")
        self._load_local_and_process(local_srt_path)
        # self._trying_search_queries()#debugging
        # self._load_local_and_process(self.config.get("DEBUGGING_SRT_FILE"))
    
    def _register_cache_cleanup(self) -> None:
        def _cleanup():
            try:
                base = self._get_cache_base_dir()
                if os.path.exists(base):
                    shutil.rmtree(base)
                    logger.debug("Removed runtime cache: %s", base)
            except Exception:
                logger.exception("Failed to cleanup cache on exit")
        atexit.register(_cleanup)

    def save_state(self):
        #save all the variables to config on close:
        #LAST_LOCAL_SRT_FILE, LAST_ANIME_NAME, LAST_GITHUB_URL, 
        if getattr(self, "remote_flag", False):
            try:
                self._sync_remote_url_to_current_episode()
            except Exception:
                logger.exception("Failed to sync remote URL to current episode before save_state")

        if getattr(self, "srt_file", None) != self.config.get("LAST_LOCAL_SRT_FILE"):
            self.config.set("LAST_LOCAL_SRT_FILE", self.srt_file)
        if getattr(self, "anime_folder_name", None) != self.config.get("LAST_ANIME_NAME"):
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
        remote_url = getattr(self, "remote_url", None)
        if remote_url and remote_url != self.config.get("LAST_GITHUB_URL"):
            self.config.set("LAST_GITHUB_URL", remote_url)
            
#region --------------------------------local handling-----------------------------------
    def _load_local_and_process(self, local_srt_path: str) -> bool:
        if not (local_srt_path and os.path.isfile(local_srt_path)):
            logger.error("Local SRT path not found: \n%s\n -> Manual selection", local_srt_path)
            local_srt_path = self.choose_new_file()  # ask for local or remote
            if local_srt_path is None:
                return False
        # Keep the currently loaded file path in sync so helpers like get_current_global()
        # can always parse the active filename.
        self.srt_file = local_srt_path
        self._extract_and_set_local_episode_metadata(local_srt_path)
        self.set_subtitle_display_data(local_srt_path)
        if self.is_movie:
            logger.info(f"Loaded subtitle: Movie | {local_srt_path}")
        else:
            # Prefer global numbering if that's all we have (common for long-running shows).
            g = self.get_current_global()
            if self.current_season is not None and self.current_episode is not None:
                logger.info(f"Loaded subtitle: S{self.current_season}E{self.current_episode} | {local_srt_path}")
            elif g is not None:
                logger.info(f"Loaded subtitle: G{g} | {local_srt_path}")
            else:
                logger.info(f"Loaded subtitle: Episode | {local_srt_path}")

# -------------------------helpers-----------------------------
    def _extract_and_set_local_episode_metadata(self, local_path):
        if not self.remote_flag: #hardcoded certain local folder when not using cached files
            self.anime_folder_name = local_path.replace("\\", "/").split("/")[local_path.replace("\\", "/").split("/").index("subs")+1]
        self.config.set("LAST_LOCAL_SRT_FILE", local_path)

        # Always parse from the filename (works for both fixed subs folder and runtime cache).
        s, e, g = self.extract_season_episode_global(os.path.basename(local_path))
        # If the file only has global numbering, treat that as the current "episode"
        # so the UI/controller doesn't label it as "Movie".
        if s is None and e is None and g is not None:
            self.current_season, self.current_episode = None, int(g)
        else:
            self.current_season, self.current_episode = s, e

        # Only treat as "movie" if we couldn't parse *any* episodic identifier.
        # (Global-only numbering like "- 123" is still episodic.)
        self.is_movie = (s is None and e is None and g is None)
        self._build_local_episode_map(local_path)

    def _build_local_episode_map(self, local_path=None):
        if local_path is None:
            # Allow callers to refresh the map without explicitly passing a path.
            local_path = getattr(self, "srt_file", None)
            if not local_path:
                return

        self.local_srt_dir = os.path.dirname(local_path)
        self.local_srt_files = []
        for fn in os.listdir(self.local_srt_dir):
            if not fn.lower().endswith(('.srt', '.ass', '.ssa')):
                continue
            path = os.path.join(self.local_srt_dir, fn)
            s, e, g = self.extract_season_episode_global(fn)
            if s is None and e is None and g is None:
                self._log_unparsed_filename(name=fn, path=path, reason="local_dir_scan")
            # Normalize global-only files so they behave like episodes in the UI.
            if s is None and e is None and g is not None:
                e = int(g)
            rec = {"name": fn, "path": path, "season": s, "episode": e, "global": g}
            self.local_srt_files.append(rec)
        def _sort_key(rec):
            g = rec.get("global")
            if g is not None:
                return (0, int(g), (rec.get("name") or ""))
            return (1, int(rec.get("season") or 0), int(rec.get("episode") or 0), rec.get("name") or "")
        self.local_srt_files.sort(key=_sort_key)

    def set_subtitle_display_data(self, local_path):
        with open(local_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        text = raw.decode(detected['encoding'] or 'utf-8', errors='replace')

        ext = os.path.splitext(local_path)[1].lower()
        if ext in (".ass", ".ssa"):
            self.subtitles = self._parse_ass_subtitles(text)
        else:
            self.subtitles = list(srt.parse(text))

        #seperate into clean, start times, top and bottom segments
        self.display_data = []
        for sub in self.subtitles:
            clean = self._clean_text(sub.content)
            start_times = sub.start.total_seconds()
            lines = [l for l in clean.splitlines() if l.strip()]
            if not lines:
                top, bottom = [], []
            elif len(lines) == 1:
                top, bottom = [], self._parse_ruby_segments(lines[0])
            else:
                top = self._parse_ruby_segments(lines[0])
                bottom = self._parse_ruby_segments(lines[1])
            self.display_data.append((clean, start_times, top, bottom))

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN.sub('', text)
        cleaned = self.TAG_PATTERN.sub('', cleaned)
        cleaned = self.RUBY_PATTERN.sub(r'\1«\2»', cleaned)
        cleaned = regex.sub(r'[（(].*?[）)]', '', cleaned)
        cleaned = cleaned.replace('«', '(').replace('»', ')')
        return cleaned.replace('&lrm;', '').replace('\u200e', '').strip()

    # ---------------------- ASS parsing (minimal) ----------------------
    ASS_OVERRIDE_TAG_RE = re.compile(r'\{[^}]*\}')

    def _ass_time_to_timedelta(self, ts: str) -> datetime.timedelta:
        """
        ASS timestamps are usually H:MM:SS.CS (centiseconds), but some files use 3-digit fractions.
        """
        ts = (ts or "").strip()
        # e.g. 0:01:23.45
        hms, dot, frac = ts.partition(".")
        parts = hms.split(":")
        if len(parts) != 3:
            raise ValueError(f"Invalid ASS timestamp: {ts!r}")
        h = int(parts[0]); m = int(parts[1]); s = int(parts[2])
        frac_val = 0.0
        if dot:
            frac = (frac or "").strip()
            if frac.isdigit():
                if len(frac) <= 2:
                    frac_val = int(frac) / 100.0
                else:
                    frac_val = int(frac) / 1000.0
        return datetime.timedelta(hours=h, minutes=m, seconds=s + frac_val)

    def _clean_ass_dialogue_text(self, text: str) -> str:
        """
        Strip ASS override tags and convert common escape sequences to plain text.
        """
        t = text or ""
        # Remove override tags like "{\\i1}" / "{\\pos(...)}"
        t = self.ASS_OVERRIDE_TAG_RE.sub("", t)
        # Line breaks and non-breaking spaces
        t = t.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        return t.strip()

    def _parse_ass_subtitles(self, text: str) -> List[srt.Subtitle]:
        """
        Parse an .ass/.ssa file into a list of srt.Subtitle objects.
        We only extract Start/End/Text from [Events] -> Dialogue lines.
        """
        if not text:
            return []

        in_events = False
        fmt_cols: List[str] = []
        idx_map: Dict[str, int] = {}
        subs: List[srt.Subtitle] = []
        out_idx = 1

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            low = line.lower()
            if low.startswith("[events]"):
                in_events = True
                fmt_cols = []
                idx_map = {}
                continue
            if not in_events:
                continue

            if low.startswith("format:"):
                fmt = line.split(":", 1)[1]
                fmt_cols = [c.strip().lower() for c in fmt.split(",") if c.strip()]
                idx_map = {name: i for i, name in enumerate(fmt_cols)}
                continue

            if low.startswith("dialogue:") or low.startswith("comment:"):
                # Ignore Comment lines (same shape as Dialogue in many files).
                if low.startswith("comment:"):
                    continue

                payload = line.split(":", 1)[1].lstrip()

                # If the file didn't provide a Format line, fall back to the common V4+ format.
                if not idx_map:
                    fmt_cols = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
                    idx_map = {name: i for i, name in enumerate(fmt_cols)}

                maxsplit = max(len(fmt_cols) - 1, 1)
                parts = payload.split(",", maxsplit=maxsplit)
                if len(parts) < len(fmt_cols):
                    # Malformed line; skip
                    continue

                try:
                    start_s = parts[idx_map["start"]].strip()
                    end_s = parts[idx_map["end"]].strip()
                    txt = parts[idx_map["text"]]
                except Exception:
                    continue

                try:
                    start = self._ass_time_to_timedelta(start_s)
                    end = self._ass_time_to_timedelta(end_s)
                except Exception:
                    continue

                content = self._clean_ass_dialogue_text(txt)
                if not content:
                    continue

                # Ensure ordering for duration computations; skip inverted cues.
                if end <= start:
                    continue

                subs.append(srt.Subtitle(index=out_idx, start=start, end=end, content=content))
                out_idx += 1

        subs.sort(key=lambda s: (s.start, s.end, s.index))
        return subs
     
    def _parse_ruby_segments(self, text: str) -> List[tuple[str, Optional[str]]]:
        segments: List[tuple[str, Optional[str]]] = []
        last = 0
        for m in self.RUBY_PATTERN.finditer(text):
            plain = text[last:m.start()].strip()
            if plain:
                segments.append((plain, None))
            segments.append((m.group(1), m.group(2)))
            last = m.end()
        tail = text[last:].strip()
        if tail:
            segments.append((tail, None))
        return segments
# -------------------------helpers-----------------------------

# ---------------------- get data -------------------------
    def get_anime_name(self)-> Optional[str]: return self.anime_folder_name
    def get_subtitle_display_data(self): return self.display_data
    def get_subtitle_geometry(self): return self.calculate_geometry()
    def get_episode_metadata(self): return (self.github_owner, self.github_repo, self.remote_path,
                                            self.anime_folder_name, self.file_name, self.current_season, self.current_episode)
    def get_total_duration(self) -> float: return self.subtitles[-1].end.total_seconds()
    def get_current_season(self) -> int: return self.current_season
    def get_current_episode(self) -> int: return self.current_episode
# ---------------------- get data -------------------------
#endregion ------------------------------local handling-----------------------------------

#region -------------------------episode / season switching-----------------------------
    def _load_local_record(self, rec: dict) -> bool:
        """
        Given a rec from self.local_srt_files, load it and update current state.
        Returns True on success.
        """
        if not rec or not rec.get("path") or not os.path.isfile(rec["path"]):
            return False
        try:
            self._load_local_and_process(rec["path"])
            # _load_local_and_process already parses the filename and sets current_season/current_episode.
            # Only override if the record provides concrete metadata (and normalize global-only files).
            if rec.get("season") is not None:
                self.current_season = rec.get("season")
            if rec.get("episode") is not None:
                self.current_episode = rec.get("episode")
            elif rec.get("global") is not None and self.current_episode is None:
                self.current_episode = int(rec.get("global"))
            self.srt_file = rec["path"]
            if getattr(self, "remote_flag", False):
                try:
                    self._sync_remote_url_to_current_episode(rec)
                except Exception:
                    logger.exception("Failed to sync remote URL after loading local record")
            return True
        except Exception:
            logger.exception("Failed to load local subtitle: %s", rec.get("path"))
            return False

    def _sync_remote_url_to_current_episode(self, rec: Optional[Dict] = None) -> None:
        """
        Keep remote_url/remote_path aligned with the currently loaded episode.
        This allows startup to resume the same episode via LAST_GITHUB_URL even when cache is cleared on exit.
        """
        if not getattr(self, "remote_flag", False):
            return

        owner = getattr(self, "github_owner", None)
        repo = getattr(self, "github_repo", None)
        ref = getattr(self, "github_ref", None)
        if not (owner and repo and ref):
            return

        item = None
        remote_map = getattr(self, "remote_episode_map_global", None) or {}

        g = None
        if isinstance(rec, dict):
            try:
                if rec.get("global") is not None:
                    g = int(rec.get("global"))
            except Exception:
                g = None
        if g is None:
            try:
                g = self.get_current_global()
            except Exception:
                g = None
        if g is not None:
            item = remote_map.get(int(g))

        if item is None:
            s = None
            e = None
            if isinstance(rec, dict):
                s = rec.get("season")
                e = rec.get("episode")
            if s is None:
                s = getattr(self, "current_season", None)
            if e is None:
                e = getattr(self, "current_episode", None)
            season_map = getattr(self, "remote_episode_map_season", None) or {}
            if s is not None and e is not None:
                for it in season_map.get(int(s), []):
                    if it.get("episode") == int(e):
                        item = it
                        break

        remote_path = None
        if item and item.get("path"):
            remote_path = item.get("path")
        elif getattr(self, "remote_path", None):
            remote_path = self.remote_path
        if not remote_path:
            return

        self.remote_path = remote_path
        self.remote_url = f"https://github.com/{owner}/{repo}/blob/{ref}/{remote_path}"

    def change_episode(
        self,
        action: str,
        raw: Optional[int] = None,
        target_season: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        if self.remote_flag:
            return self.change_episode_remote(action, raw, target_season)
        return self.change_episode_local(action, raw, target_season)

    def change_episode_local(
        self,
        action: str,
        raw: Optional[int] = None,
        target_season: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        if not getattr(self, "local_srt_files", None):
            self._build_local_episode_map()

        cur_s = getattr(self, "current_season", None)
        cur_e = getattr(self, "current_episode", None)
        cur_g = None
        try:
            cur_g = self.get_current_global()
        except Exception:
            # fallback parsing from filename
            if getattr(self, "srt_file", None):
                _, _, cur_g = self.extract_season_episode_global(os.path.basename(self.srt_file))

        # helper to find by global or by (s,e)
        def find_by_global(g):
            if g is None:
                return None
            for rec in self.local_srt_files:
                if rec.get("global") == g:
                    return rec
            return None

        def find_by_local(s, e):
            for rec in self.local_srt_files:
                if rec.get("season") == s and rec.get("episode") == e:
                    return rec
            return None

        target_rec = None
        if action == "inc":
            # prefer global step if available
            if cur_g is not None:
                globals_sorted = sorted({int(r.get("global")) for r in self.local_srt_files if r.get("global") is not None})
                _, next_g = self._prev_next_in_sorted(globals_sorted, int(cur_g))
                if next_g is not None:
                    target_rec = find_by_global(next_g)
            if target_rec is None and cur_s is not None and cur_e is not None:
                season_eps = sorted({int(r.get("episode")) for r in self.local_srt_files if r.get("season") == cur_s and r.get("episode") is not None})
                _, next_e = self._prev_next_in_sorted(season_eps, int(cur_e))
                if next_e is not None:
                    target_rec = find_by_local(cur_s, next_e)

        elif action == "dec":
            if cur_g is not None:
                globals_sorted = sorted({int(r.get("global")) for r in self.local_srt_files if r.get("global") is not None})
                prev_g, _ = self._prev_next_in_sorted(globals_sorted, int(cur_g))
                if prev_g is not None:
                    target_rec = find_by_global(prev_g)
            if target_rec is None and cur_s is not None and cur_e is not None and cur_e > 1:
                season_eps = sorted({int(r.get("episode")) for r in self.local_srt_files if r.get("season") == cur_s and r.get("episode") is not None})
                prev_e, _ = self._prev_next_in_sorted(season_eps, int(cur_e))
                if prev_e is not None:
                    target_rec = find_by_local(cur_s, prev_e)

        elif action == "set":
            if not (isinstance(raw, int) and raw > 0):
                return self.current_season, self.current_episode
            if target_season is not None:
                target_rec = find_by_local(int(target_season), raw)
            # interpret as local episode in current season first
            if target_rec is None:
                if cur_s is not None:
                    target_rec = find_by_local(cur_s, raw)
            # if that failed, also check whether raw matches a global index
            if target_rec is None:
                target_rec = find_by_global(raw)

        else:
            return self.current_season, self.current_episode

        if target_rec:
            if self._load_local_record(target_rec):
                return self.current_season, self.current_episode
            # if load fails, fall through to warn

        # not found locally -> warn user (no downloads in local mode)
        message = f"Episode not found in local folder: action={action}, value={raw}"
        logger.info(message)

        return self.current_season, self.current_episode

    def _prev_next_in_sorted(self, sorted_values: List[int], current: int) -> Tuple[Optional[int], Optional[int]]:
        """
        Given a sorted list of ints and a current value, return (prev, next) values.
        If current is not in the list, we return neighbors around the insertion point.
        """
        if not sorted_values:
            return None, None
        cur = int(current)
        idx = bisect.bisect_left(sorted_values, cur)
        prev_val = sorted_values[idx - 1] if idx > 0 else None

        # If current exists in the list, "next" should be the element after it.
        if idx < len(sorted_values) and sorted_values[idx] == cur:
            idx += 1
        next_val = sorted_values[idx] if idx < len(sorted_values) else None
        return prev_val, next_val

    def get_episode_nav_state(self) -> Tuple[bool, bool, bool]:
        """
        Returns (can_dec, can_inc, is_movie) for UI.
        We only disable buttons when we *know* there is no prev/next episode from the current maps/index.
        """
        if getattr(self, "is_movie", False):
            return False, False, True

        # Remote mode: prefer the remote global map if available.
        if getattr(self, "remote_flag", False):
            m = getattr(self, "remote_episode_map_global", None)
            if isinstance(m, dict) and m:
                cur_g = None
                try:
                    cur_g = self.get_current_global()
                except Exception:
                    cur_g = None
                if cur_g is not None:
                    keys = sorted(int(k) for k in m.keys())
                    prev_g, next_g = self._prev_next_in_sorted(keys, int(cur_g))
                    return (prev_g is not None), (next_g is not None), False
            # Unknown -> don't block UI.
            return True, True, False

        # Local mode: base decision on the local index if available.
        if not getattr(self, "local_srt_files", None):
            try:
                self._build_local_episode_map()
            except Exception:
                return True, True, False

        cur_g = None
        try:
            cur_g = self.get_current_global()
        except Exception:
            cur_g = None

        if cur_g is not None:
            globals_sorted = sorted({int(r.get("global")) for r in self.local_srt_files if r.get("global") is not None})
            prev_g, next_g = self._prev_next_in_sorted(globals_sorted, int(cur_g))
            return (prev_g is not None), (next_g is not None), False

        cur_s = getattr(self, "current_season", None)
        cur_e = getattr(self, "current_episode", None)
        if cur_s is not None and cur_e is not None:
            season_eps = sorted({int(r.get("episode")) for r in self.local_srt_files if r.get("season") == cur_s and r.get("episode") is not None})
            prev_e, next_e = self._prev_next_in_sorted(season_eps, int(cur_e))
            return (prev_e is not None), (next_e is not None), False

        return True, True, False

    def get_episode_dropdown_values(self) -> List[int]:
        """
        Values for the episode dropdown (combobox).

        Remote mode: show *all* global episodes from the remote episode map (even if not downloaded yet).
        Local mode: show global episodes if available, otherwise local episodes (prefer current season).
        """
        # Remote: prefer complete global map.
        if getattr(self, "remote_flag", False):
            m = getattr(self, "remote_episode_map_global", None)
            if not isinstance(m, dict) or not m:
                try:
                    self.build_remote_episode_maps()
                except Exception:
                    pass
                m = getattr(self, "remote_episode_map_global", None)
            if isinstance(m, dict) and m:
                try:
                    return sorted(int(k) for k in m.keys())
                except Exception:
                    pass

        # Local fallback: index available local/cache files.
        if not getattr(self, "local_srt_files", None):
            try:
                if getattr(self, "remote_flag", False):
                    self.update_local_srt_files()
                else:
                    self._build_local_episode_map()
            except Exception:
                pass

        lst = getattr(self, "local_srt_files", None) or []
        try:
            globals_sorted = sorted({int(r.get("global")) for r in lst if r.get("global") is not None})
        except Exception:
            globals_sorted = []
        if globals_sorted:
            return globals_sorted

        cur_s = getattr(self, "current_season", None)
        if cur_s is not None:
            try:
                eps = sorted({int(r.get("episode")) for r in lst if r.get("season") == cur_s and r.get("episode") is not None})
            except Exception:
                eps = []
            if eps:
                return eps

        try:
            return sorted({int(r.get("episode")) for r in lst if r.get("episode") is not None})
        except Exception:
            return []

    def change_episode_remote(
        self,
        action: str,
        raw: Optional[int] = None,
        target_season: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Remote switching: first try to find the file in the local index. If missing, trigger a
        focused windowed download around the target/global (synchronously), refresh local index,
        then load if available. If still missing, show a warning.
        """
        # local_srt_files is optional in remote mode; avoid expensive full-cache scans here.
        if not isinstance(getattr(self, "local_srt_files", None), list):
            self.local_srt_files = []

        # ensure remote maps exist (for global<->local mapping and download lists)
        if not getattr(self, "remote_episode_map_global", None):
            overlay = None
            try:
                root = getattr(tk, "_default_root", None)
                if root is not None:
                    overlay = LoadingOverlay(
                        root,
                        text="Searching GitHub...",
                        anchor_window=root,
                        y_offset=0,
                    )
                if not getattr(self, "all_results_items", None):
                    self._create_remote_episode_map_per_season()
                self.build_remote_episode_maps()
            except Exception:
                logger.exception("Failed to build remote maps in change_episode_remote")
            finally:
                if overlay:
                    overlay.close()

        cur_s = getattr(self, "current_season", None)
        cur_e = getattr(self, "current_episode", None)
        cur_g = self.get_current_global()

        # helpers to locate rec
        def find_by_global(g):
            for rec in self.local_srt_files:
                if rec.get("global") == g:
                    return rec
            return None

        def find_by_local(s, e):
            for rec in self.local_srt_files:
                if rec.get("season") == s and rec.get("episode") == e:
                    return rec
            return None

        # resolve intended target global/season/episode similar to local function
        target_global = None
        target_s = None
        target_e = None

        if action == "inc":
            if cur_g is not None:
                # Prefer stepping to the next *available* global episode in the remote map.
                target_global = None
                try:
                    keys = sorted(int(k) for k in getattr(self, "remote_episode_map_global", {}).keys())
                    _, next_g = self._prev_next_in_sorted(keys, int(cur_g))
                    target_global = next_g
                except Exception:
                    target_global = int(cur_g) + 1

                if target_global is None:
                    logger.info("No next episode available (global %s).", cur_g)
                    return self.current_season, self.current_episode

                target_rec = find_by_global(target_global)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                # not found locally: compute target season/episode from remote map
                ts, te = self.global_to_local(target_global)
                target_s, target_e = ts, te
            else:
                if cur_s is None or cur_e is None:
                    return None, None
                # attempt local step
                target_rec = find_by_local(cur_s, cur_e + 1)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                # map local->global if possible and fall through to download
                target_global = self.local_to_global(cur_s, cur_e + 1)
                target_s, target_e = cur_s, cur_e + 1

        elif action == "dec":
            if cur_g is not None:
                # Prefer stepping to the previous *available* global episode in the remote map.
                target_global = None
                try:
                    keys = sorted(int(k) for k in getattr(self, "remote_episode_map_global", {}).keys())
                    prev_g, _ = self._prev_next_in_sorted(keys, int(cur_g))
                    target_global = prev_g
                except Exception:
                    target_global = int(cur_g) - 1

                if target_global is None:
                    logger.info("No previous episode available (global %s).", cur_g)
                    return self.current_season, self.current_episode

                target_rec = find_by_global(target_global)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                ts, te = self.global_to_local(target_global)
                target_s, target_e = ts, te
            else:
                if cur_s is None or cur_e is None:
                    return None, None
                target_rec = find_by_local(cur_s, cur_e - 1) if cur_e > 1 else None
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                target_global = self.local_to_global(cur_s, cur_e - 1) if cur_e > 1 else None
                target_s, target_e = cur_s, cur_e - 1

        elif action == "set":
            if not (isinstance(raw, int) and raw > 0):
                return self.current_season, self.current_episode
            if target_season is not None:
                ts = int(target_season)
                target_rec = find_by_local(ts, raw)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode

                g = self.local_to_global(ts, raw)
                if g is not None:
                    target_global = int(g)
                    target_s, target_e = ts, raw
                else:
                    try:
                        if not getattr(self, "remote_episode_map_global", None):
                            if not getattr(self, "all_results_items", None):
                                self._create_remote_episode_map_per_season()
                            self.build_remote_episode_maps()
                    except Exception:
                        logger.exception("Failed to build remote maps for season-episode set")

                    season_items = getattr(self, "remote_episode_map_season", {}).get(ts, [])
                    chosen = None
                    for item in season_items:
                        if item.get("episode") == int(raw):
                            chosen = item
                            break
                    if chosen and chosen.get("global") is not None:
                        target_global = int(chosen.get("global"))
                        target_s, target_e = ts, raw
                    else:
                        logger.info("Episode not found for season set: S%sE%s", ts, raw)
                        return self.current_season, self.current_episode

            # prefer local interpretation: current season + episode raw
            elif cur_s is not None:
                target_rec = find_by_local(cur_s, raw)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                # try to map to global if possible
                g = self.local_to_global(cur_s, raw)
                if g:
                    target_global = g
                    target_s, target_e = cur_s, raw
                else:
                    # treat raw as global if present in remote map
                    if raw in getattr(self, "remote_episode_map_global", {}):
                        target_global = raw
                        target_s, target_e = self.global_to_local(raw)
                    else:
                        # build remote maps and retry
                        try:
                            if not getattr(self, "all_results_items", None):
                                self._create_remote_episode_map_per_season()
                            self.build_remote_episode_maps()
                        except Exception:
                            logger.exception("Failed to build remote maps for 'set'")
                        if raw in getattr(self, "remote_episode_map_global", {}):
                            target_global = raw
                            target_s, target_e = self.global_to_local(raw)
                        else:
                            # fall back to trying a local file with that episode number
                            for rec in self.local_srt_files:
                                if rec.get("season") == cur_s and rec.get("episode") == raw:
                                    if self._load_local_record(rec):
                                        return self.current_season, self.current_episode
                            logger.info("Episode not found locally or remotely: %s", raw)
                            return self.current_season, self.current_episode
            else:
                # no cur season known -> try treat raw as global
                if raw in getattr(self, "remote_episode_map_global", {}):
                    target_global = raw
                    target_s, target_e = self.global_to_local(raw)
                else:
                    logger.info("Episode not found: %s", raw)
                    return self.current_season, self.current_episode
        else:
            return self.current_season, self.current_episode

        # At this point we have target_global (maybe None) and/or target_s/target_e
        # If the file is still not local, request windowed download around target_global (or current global)
        if target_global is None and target_s is not None and target_e is not None:
            target_global = self.local_to_global(target_s, target_e)

        if target_global is None:
            # If we still cannot derive a global index, warn user
            logger.info("Could not determine global index for requested episode.")
            return self.current_season, self.current_episode

        # Resolve the expected local cache path for this global episode.
        item = getattr(self, "remote_episode_map_global", {}).get(int(target_global))
        if not item or not item.get("path"):
            logger.info("Requested episode not available: no remote path for global %s.", target_global)
            return self.current_season, self.current_episode

        season = item.get("season") or self.current_season
        season_dir = self._season_cache_dir(season)
        filename = self.sanitize_filename(os.path.basename(item["path"]))
        local_path = os.path.join(season_dir, filename)

        # Always download the *requested* episode first (sync) if missing.
        if not os.path.exists(local_path):
            overlay = None
            try:
                root = getattr(tk, "_default_root", None)
                if root is not None:
                    overlay = LoadingOverlay(
                        root,
                        text=f"Downloading episode {target_global}...",
                        anchor_window=root,
                    )
                raw_url = self._get_raw_url(item["path"])
                self._download_file(raw_url, local_path)
            except Exception:
                logger.exception("Failed to download requested episode (global %s)", target_global)
            finally:
                if overlay:
                    overlay.close()

        # After download attempt, load directly from the expected cache path (no full cache scan).
        if os.path.exists(local_path):
            rec = {
                "season": item.get("season"),
                "episode": item.get("episode"),
                "global": int(target_global),
                "path": local_path,
                "name": os.path.basename(local_path),
            }
            # Normalize global-only episodes so they don't behave like "movies".
            if rec.get("season") is None and rec.get("episode") is None:
                rec["episode"] = int(target_global)

            if self._load_local_record(rec):
                # Now that the requested episode is loaded, download the rest of the window asynchronously.
                try:
                    self._schedule_prefetch_window(target_global)
                except Exception:
                    logger.exception("Failed to start background window download around global %s", target_global)
                return self.current_season, self.current_episode

        logger.info("Requested episode not available after download attempt (global %s).", target_global)
        return self.current_season, self.current_episode

    def set_new_file(self):
        """
        User action: prompt for a new subtitle source and immediately load it.

        Returns the selected local file path (including downloaded cache files) or None.
        """
        path = self.choose_new_file()
        if not path:
            return None
        try:
            self._load_local_and_process(path)
        except Exception:
            logger.exception("Failed to load selected subtitle: %s", path)
            return None
        return path

    def choose_new_file(self) -> Optional[str]:
        """
        Prompt the user to select a new subtitle source.

        Returns a local path:
        - Local mode: chosen file path
        - Remote mode: downloaded cache path for the chosen episode
        """
        result = {"path": None}

        # If a startup splash is visible, hide it while the user interacts with dialogs.
        hide_startup_overlay()
        try:
            popup = tk.Toplevel()
            popup.title("Choose Source")
            popup.attributes("-topmost", True)
            popup.grab_set()
            w, h = 420, 100
            popup.update_idletasks()
            sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()
            x, y = (sw - w) // 2, (sh - h) // 2
            popup.geometry(f"{w}x{h}+{x}+{y}")

            tk.Label(popup, text="Select source for subtitle file:", font=("Arial", 12)).pack(pady=(12, 8))
            button_frame = tk.Frame(popup)
            button_frame.pack(pady=8)

            def _done(path: Optional[str]):
                result["path"] = path
                try:
                    popup.destroy()
                except Exception:
                    pass

            def choose_local():
                path = self.ask_local_srt_file()
                if not path:
                    return
                # Bring the startup splash back while we parse/process the chosen file.
                show_startup_overlay()
                _done(path)

            def choose_remote_url():
                url, s, e = self.ask_remote_srt_with_hint()
                if not url:
                    return
                hint = (s, e) if (s is not None or e is not None) else None

                # Close chooser. Show startup splash again while we do network work.
                try:
                    popup.destroy()
                except Exception:
                    pass
                show_startup_overlay()

                overlay = None
                try:
                    # If we already have the startup splash, don't stack another overlay.
                    root = getattr(tk, "_default_root", None)
                    if root is not None and get_startup_overlay() is None:
                        overlay = LoadingOverlay(
                            root,
                            text="Searching and downloading...",
                            anchor_window=root,
                            y_offset=0,
                        )
                    result["path"] = self._initialize_remote_path(url, hint=hint)
                finally:
                    if overlay:
                        overlay.close()

            def choose_remote_search():
                anime_query, s, e = self.ask_remote_search_query()
                if not anime_query:
                    return
                hint = (s, e) if (s is not None or e is not None) else None

                try:
                    popup.destroy()
                except Exception:
                    pass
                show_startup_overlay()

                overlay = None
                try:
                    root = getattr(tk, "_default_root", None)
                    if root is not None and get_startup_overlay() is None:
                        overlay = LoadingOverlay(
                            root,
                            text="Searching and downloading...",
                            anchor_window=root,
                            y_offset=0,
                        )
                    result["path"] = self._initialize_remote_from_search_query(anime_query, hint=hint)
                finally:
                    if overlay:
                        overlay.close()

            tk.Button(button_frame, text="Local File", width=15, command=choose_local).grid(row=0, column=0, padx=10)
            tk.Button(button_frame, text="Remote URL", width=15, command=choose_remote_url).grid(row=0, column=1, padx=10)
            tk.Button(button_frame, text="Remote Search", width=15, command=choose_remote_search).grid(row=0, column=2, padx=10)

            popup.wait_window(popup)
            return result["path"]
        finally:
            # Ensure the splash returns even if the user cancels/closes the dialog.
            show_startup_overlay()

    def ask_remote_srt_with_hint(self) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        result = {"url": None, "season": None, "episode": None}
        hide_startup_overlay()
        try:
            dlg = tk.Toplevel()
            dlg.title("Remote subtitle (URL + sXeY)")
            dlg.attributes("-topmost", True)
            dlg.grab_set()
            dlg.resizable(False, False)
            dlg.update_idletasks()
            sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
            w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
            dlg.geometry(f"+{x}+{y}")

            tk.Label(dlg, text="GitHub subtitle URL:", anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
            url_entry = tk.Entry(dlg, width=60)
            url_entry.grid(row=1, column=0, padx=8)

            tk.Label(dlg, text="Season/Episode (e.g. s2e1 or s02e01):", anchor="w").grid(row=2, column=0, sticky="w", padx=8, pady=(8,2))
            se_entry = tk.Entry(dlg, width=30)
            se_entry.grid(row=3, column=0, padx=8)

            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=4, column=0, pady=10)

            def on_ok():
                u = url_entry.get().strip()
                s, e, global_e = self.extract_season_episode_global(se_entry.get().strip().lower())
                result["url"], result["season"], result["episode"] = (u or None, s, e)
                dlg.destroy()
            def on_cancel():
                dlg.destroy()
            tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=6)
            tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

            dlg.wait_window(dlg)
            return result["url"], result["season"], result["episode"]
        finally:
            show_startup_overlay()

    def _cached_github_search_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "github_search")

    def _list_cached_github_search_queries(self) -> List[str]:
        folder_dir = self._cached_github_search_dir()
        if not os.path.isdir(folder_dir):
            return []

        prefix = "github_search_"
        suffix = ".json"
        queries: List[str] = []
        for fn in os.listdir(folder_dir):
            if not (fn.startswith(prefix) and fn.endswith(suffix)):
                continue
            safe_name = fn[len(prefix):-len(suffix)]
            if not safe_name:
                continue
            queries.append(safe_name.replace("_", " "))
        return sorted(set(queries), key=str.casefold)

    def _ask_cached_github_search_query(self, parent) -> Optional[str]:
        queries = self._list_cached_github_search_queries()
        if not queries:
            try:
                parent.bell()
            except Exception:
                pass
            return None

        chosen = {"query": None}
        chooser = tk.Toplevel(parent)
        chooser.title("Choose cached search")
        chooser.attributes("-topmost", True)
        chooser.transient(parent)
        chooser.grab_set()
        chooser.resizable(False, False)

        tk.Label(chooser, text="Select a cached anime query:", anchor="w").pack(padx=8, pady=(8, 4), fill="x")

        list_frame = tk.Frame(chooser)
        list_frame.pack(padx=8, pady=(0, 8), fill="both", expand=True)
        scrollbar = tk.Scrollbar(list_frame, orient="vertical")
        listbox = tk.Listbox(
            list_frame,
            width=56,
            height=min(12, len(queries)),
            yscrollcommand=scrollbar.set,
            exportselection=False,
        )
        scrollbar.config(command=listbox.yview)
        listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for q in queries:
            listbox.insert(tk.END, q)
        if queries:
            listbox.selection_set(0)
            listbox.activate(0)
            listbox.focus_set()

        btn_frame = tk.Frame(chooser)
        btn_frame.pack(pady=(0, 8))

        def on_ok(event=None):
            selection = listbox.curselection()
            if not selection:
                try:
                    chooser.bell()
                except Exception:
                    pass
                return "break"
            chosen["query"] = (listbox.get(selection[0]) or "").strip() or None
            chooser.destroy()
            return "break"

        def on_cancel(event=None):
            chooser.destroy()
            return "break"

        tk.Button(btn_frame, text="Use Selected", width=12, command=on_ok).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

        listbox.bind("<Double-Button-1>", on_ok)
        listbox.bind("<Return>", on_ok)
        listbox.bind("<KP_Enter>", on_ok)
        chooser.bind("<Escape>", on_cancel)

        try:
            chooser.update_idletasks()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            w, h = chooser.winfo_reqwidth(), chooser.winfo_reqheight()
            x = px + max((pw - w) // 2, 0)
            y = py + max((ph - h) // 2, 0)
            chooser.geometry(f"+{x}+{y}")
        except Exception:
            pass

        chooser.wait_window(chooser)
        return chosen["query"]

    def ask_remote_search_query(self) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """
        Ask the user for a GitHub search query (anime name) and an optional episode hint.

        Hint parsing uses extract_season_episode_global() so the user can enter:
        - "s2e1" (season+episode)
        - "130" (global episode)
        - "e254" (global episode)
        """
        result = {"query": None, "season": None, "episode": None}

        hide_startup_overlay()
        try:
            dlg = tk.Toplevel()
            dlg.title("Remote Subtitle Search")
            dlg.attributes("-topmost", True)
            dlg.grab_set()
            dlg.resizable(False, False)
            dlg.update_idletasks()
            sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
            w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
            x = (sw - w) // 2
            y = (sh - h) // 2
            dlg.geometry(f"+{x}+{y}")
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(dlg, text="Anime search query (folder name / season 1 base):", anchor="w").grid(
                row=0, column=0, sticky="w", padx=8, pady=(8, 2)
            )
            query_row = tk.Frame(dlg)
            query_row.grid(row=1, column=0, sticky="ew", padx=8)
            query_row.grid_columnconfigure(0, weight=1)
            query_entry = tk.Entry(query_row, width=25)
            query_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

            def choose_cached():
                cached_query = self._ask_cached_github_search_query(dlg)
                if not cached_query:
                    return
                query_entry.delete(0, tk.END)
                query_entry.insert(0, cached_query)
                query_entry.icursor(tk.END)

            tk.Button(query_row, text="Use Saved...", width=12, command=choose_cached).grid(row=0, column=1)
            try:
                dlg.after(0, lambda: query_entry.focus_set())
            except Exception:
                try:
                    query_entry.focus_set()
                except Exception:
                    pass

            tk.Label(dlg, text="Episode hint (optional: s2e1 or 130):", anchor="w").grid(
                row=2, column=0, sticky="w", padx=8, pady=(8, 2)
            )
            hint_entry = tk.Entry(dlg, width=30)
            hint_entry.grid(row=3, column=0, sticky="w", padx=8)

            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=4, column=0, pady=10)

            def on_ok():
                q = (query_entry.get() or "").strip()
                hint_raw = (hint_entry.get() or "").strip().lower()
                s = e = g = None
                if hint_raw:
                    s, e, g = self.extract_season_episode_global(hint_raw)
                # We only store (season, episode) here; when only a global is present we store it in "episode"
                # so remote init can resolve it via remote_episode_map_global.
                if s is None and e is None and g is not None:
                    e = int(g)
                result["query"], result["season"], result["episode"] = (q or None, s, e)
                dlg.destroy()

            def on_cancel():
                dlg.destroy()

            tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=6)
            tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

            def on_enter(event=None):
                on_ok()
                return "break"

            query_entry.bind("<Return>", on_enter)
            query_entry.bind("<KP_Enter>", on_enter)
            hint_entry.bind("<Return>", on_enter)
            hint_entry.bind("<KP_Enter>", on_enter)
            dlg.bind("<Return>", on_enter)
            dlg.bind("<KP_Enter>", on_enter)

            dlg.wait_window(dlg)
            return result["query"], result["season"], result["episode"]
        finally:
            show_startup_overlay()
    
    def ask_local_srt_file(self) -> Optional[str]:
        hide_startup_overlay()
        try:
            window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                parent=window,
                title="Select Subtitle File",
                initialdir=self.local_srt_dir,
                filetypes=[
                    ("Subtitle files", "*.srt *.ass *.ssa"),
                    ("SubRip files", "*.srt"),
                    ("ASS/SSA files", "*.ass *.ssa"),
                    ("All Files", "*.*"),
                ]
            )
            window.destroy()
            if not path:
                return None
            return path
        except Exception:
            logger.exception("Subtitle file selection failed")
            return None
        finally:
            show_startup_overlay()

    def calculate_geometry(self):
        font = tkFont.Font(family=self.config.get("SUBTITLE_FONT"),size=self.config.get("SUBTITLE_FONT_SIZE"),weight="bold")
        max_width = 0
        for clean, time, *_rest in self.display_data:
            base_text = regex.sub(r'\p{Han}+\([^)]+\)', lambda m: regex.match(r'(\p{Han}+)', m.group()).group(), clean)
            for line in base_text.splitlines():
                width = font.measure(line)
                if max_width < width:
                    max_width = width
                    biggest_line = line
                    start_time = time
        # print(format_time(start_time),": ",biggest_line)
        # print(self.display_data[1:4])
        line_height = font.metrics("linespace")
        ruby_height = int(line_height * 0.6)
        pad_x = 5
        total_height = ruby_height * 2 + line_height * 2

        # If we wrap long lines at a fixed pixel limit, the overlay doesn't need to grow beyond that limit.
        # Keep this in sync with SubtitleRenderer._wrap_segments()' default padding (40px each side),
        # otherwise we might wrap sooner than intended.
        try:
            wrap_limit_px = int(self.config.get("SUBTITLE_WRAP_LIMIT_PX") or 0)
        except Exception:
            wrap_limit_px = 0

        if wrap_limit_px > 0:
            renderer_padding = 40
            max_width = min(max_width, wrap_limit_px)
            total_width = max_width + 2 * renderer_padding
        else:
            total_width  = max_width + 2 * pad_x

        return (total_width, total_height)
#endregion -------------------------episode / season switching-----------------------------


################ TODO: figure out the anime name of first season #################
##### workaround user gives always s1 when pasting URL##########
#look for same string in folder name and file name? Could work but not everytime


#region -------------------------remote handling-----------------------------
    def _schedule_prefetch_window(self, center_global: Optional[int]) -> None:
        """
        Schedule a background prefetch of the configured window around center_global.

        This never expands beyond one window. It can be delayed via DOWNLOAD_PREFETCH_DELAY_MS.
        """
        if center_global is None:
            return
        try:
            window = self.config.get("DOWNLOAD_WINDOW")
        except Exception:
            window = 0
        if window <= 0:
            return

        # Cancel any pending prefetch (e.g. user quickly changed episodes).
        t = getattr(self, "_prefetch_timer", None)
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass
            self._prefetch_timer = None

        delay_ms = self.config.get("DOWNLOAD_PREFETCH_DELAY_MS")
        if delay_ms <= 0:
            self.download_window_around_global(int(center_global), window=int(window), async_download=True)
            return

        def _run():
            try:
                self.download_window_around_global(int(center_global), window=int(window), async_download=True)
            except Exception:
                logger.exception("Prefetch window download failed (global %s)", center_global)

        timer = threading.Timer(delay_ms / 1000.0, _run)
        timer.daemon = True
        self._prefetch_timer = timer
        timer.start()

    def _ensure_download_workers(self) -> None:
        """
        Start a small fixed set of background downloader threads.

        This avoids spawning one thread per file (which can cause short CPU spikes).
        """
        if getattr(self, "_dl_workers_started", False):
            return
        self._dl_workers_started = True

        self._dl_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._dl_seen_lock = threading.Lock()
        self._dl_seen: set[str] = set()  # local_path values currently queued/in-progress

        max_workers = self.config.get("DOWNLOAD_MAX_WORKERS")
        self._dl_workers: list[threading.Thread] = []
        for i in range(int(max_workers)):
            t = threading.Thread(target=self._download_worker_loop, daemon=True, name=f"subtitle-dl-{i+1}")
            t.start()
            self._dl_workers.append(t)

    def _download_worker_loop(self) -> None:
        # Keep a session per worker to reuse TLS connections (less CPU than requests.get per file).
        try:
            session = requests.Session()
        except Exception:
            session = None
        while True:
            job = self._dl_q.get()
            try:
                raw_url, local_path = job
                try:
                    if not os.path.exists(local_path):
                        self._download_file(raw_url, local_path, session=session)
                except Exception:
                    logger.exception("Background download failed for %s", raw_url)
                finally:
                    try:
                        with self._dl_seen_lock:
                            self._dl_seen.discard(local_path)
                    except Exception:
                        pass

                try:
                    throttle_ms = self.config.get("DOWNLOAD_THROTTLE_MS")
                    if throttle_ms:
                        time.sleep(throttle_ms / 1000.0)
                except Exception:
                    pass
            finally:
                try:
                    self._dl_q.task_done()
                except Exception:
                    pass

    def _enqueue_download(self, raw_url: str, local_path: str) -> bool:
        if not raw_url or not local_path:
            return False
        if os.path.exists(local_path):
            return False

        self._ensure_download_workers()
        try:
            with self._dl_seen_lock:
                if local_path in self._dl_seen:
                    return False
                self._dl_seen.add(local_path)
        except Exception:
            # If we can't de-dupe, still enqueue (worst case: duplicates).
            pass
        try:
            self._dl_q.put((raw_url, local_path))
            return True
        except Exception:
            try:
                with self._dl_seen_lock:
                    self._dl_seen.discard(local_path)
            except Exception:
                pass
            return False

    def _initialize_remote_path(self, init_url: Optional[str] = None, hint: Optional[Tuple[Optional[int], Optional[int]]] = None) -> Optional[str]:
        url = init_url
        prompt_hint = None
        if not url:
            url, h_s, h_e = self.ask_remote_srt_with_hint()
            prompt_hint = (h_s, h_e) if (h_s is not None or h_e is not None) else None
            if not url:
                return None

        # download/gather metadata for the chosen URL, then build maps once
        self._extract_and_set_remote_episode_metadata(url)
        self._create_remote_episode_map_per_season()
        self.build_remote_episode_maps()

        # allow caller-provided hint (highest priority), otherwise prefer prompt hint
        chosen_item = None
        use_hint = hint if hint is not None else prompt_hint
        if use_hint:
            hint_season, hint_episode = use_hint
            if hint_season is not None and hint_episode is not None:
                lst = getattr(self, "remote_episode_map_season", {}).get(int(hint_season), [])
                for it in lst:
                    if it.get("episode") == int(hint_episode):
                        chosen_item = it
                        break
            if chosen_item is None and hint_episode is not None:
                chosen_item = getattr(self, "remote_episode_map_global", {}).get(int(hint_episode))
            if chosen_item:
                logger.info("Using explicit hint: season=%s episode=%s -> global=%s", hint_season, hint_episode, chosen_item.get("global"))

        # only try URL/filename matching if hint did not resolve
        if not chosen_item:
            basename = os.path.basename(self.remote_path or "")
            if getattr(self, "all_results_items", None):
                for it in self.all_results_items:
                    if it.get("path") == self.remote_path or it.get("name") == basename:
                        chosen_item = it
                        break

        # if still not chosen, try to infer from the parsed filename (global or season/episode)
        if not chosen_item:
            basename = os.path.basename(self.remote_path or "")
            s_parsed, e_parsed, g_parsed = self.extract_season_episode_global(basename)
            if g_parsed is not None:
                chosen_item = getattr(self, "remote_episode_map_global", {}).get(int(g_parsed))
            elif s_parsed is not None and e_parsed is not None:
                lst = getattr(self, "remote_episode_map_season", {}).get(int(s_parsed), [])
                for it in lst:
                    if it.get("episode") == int(e_parsed):
                        chosen_item = it
                        break

        # fallback: prefer first episode of current season
        if not chosen_item and getattr(self, "remote_episode_map_season", None) and getattr(self, "current_season", None) is not None:
            lst = self.remote_episode_map_season.get(self.current_season, [])
            if lst:
                chosen_item = lst[0]

        # final fallback: lowest global available
        if not chosen_item and getattr(self, "remote_episode_map_global", None):
            keys = sorted(self.remote_episode_map_global.keys())
            if keys:
                chosen_item = self.remote_episode_map_global[keys[0]]

        # If nothing has episode numbering (movies / standalone files), pick the best candidate by score.
        if not chosen_item and getattr(self, "all_results_items", None):
            try:
                chosen_item = max(
                    self.all_results_items,
                    key=lambda it: self._subtitle_candidate_score(it.get("name"), it.get("path")),
                )
                logger.info("No episode numbering found; treating as movie/standalone. Selected: %s", chosen_item.get("path") or chosen_item.get("name"))
            except Exception:
                chosen_item = None

        if not chosen_item:
            logger.error("No remote subtitle candidates found for URL: %s", url)
            return None

        # extract target properties and attempt to resolve season/episode if missing
        target_remote_path = chosen_item.get("path")
        target_season = chosen_item.get("season")
        target_episode = chosen_item.get("episode")
        target_global = chosen_item.get("global")
        if (target_season is None or target_episode is None) and target_global is not None:
            mapped = self.global_to_local(int(target_global))
            if mapped != (None, None):
                target_season, target_episode = mapped

        if not target_remote_path:
            logger.error("Chosen item has no path: %s", chosen_item)
            return None

        # synchronous download of chosen item so UI can load it immediately
        try:
            season_dir = self._season_cache_dir(target_season) if target_season is not None else self._season_cache_dir()
            filename = self.sanitize_filename(os.path.basename(target_remote_path))
            local_path = os.path.join(season_dir, filename)
            raw_url = self._get_raw_url(target_remote_path)
            self._download_file(raw_url, local_path)
        except Exception:
            logger.exception("Failed to download chosen remote episode: %s", target_remote_path)
            return None

        # Persist remote selection (do not load here; __init__ loads exactly once)
        try:
            self.remote_url = url
            try:
                self.config.set("LAST_GITHUB_URL", url)
                if getattr(self, "anime_folder_name", None):
                    self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
            except Exception:
                logger.debug("Failed to persist LAST_GITHUB_URL/LAST_ANIME_NAME")
        except Exception:
            logger.exception("Failed to persist remote selection metadata for: %s", local_path)
            return None

        # kick off async windowed download around the chosen global (±20)
        try:
            center_global = target_global
            if center_global is None and target_season is not None and target_episode is not None:
                center_global = self.local_to_global(target_season, target_episode)
            if center_global is not None:
                self._schedule_prefetch_window(center_global)
        except Exception:
            logger.exception("Failed to start windowed background downloads")

        return local_path


     
    def _extract_and_set_remote_episode_metadata(self, remote_url):
        self.config.set("LAST_GITHUB_URL", remote_url)
        github_dict = self._parse_github_url(remote_url)
        self.github_owner = github_dict["owner"]
        self.github_repo  = github_dict["repo"]
        self.github_ref   = github_dict["ref"]
        self.remote_path = github_dict["path"]

        s, e, global_e = self.extract_season_episode_global(os.path.basename(self.remote_path))
        if s is None and e is None and global_e is not None:
            self.current_season, self.current_episode = None, int(global_e)
        else:
            self.current_season, self.current_episode = s, e
        self.anime_folder_name = self.config.get("LAST_ANIME_NAME")
        url_anime_name = self._extract_anime_name_from_url(self.remote_path)

        remote_norm = (self.remote_path or "").replace("\\", "/")
        is_movie_url = "/anime_movie/" in remote_norm or remote_norm.startswith("subtitles/anime_movie/")

        # TV shows: keep the name of season 1 (user preference), since later seasons can have
        # slightly different folder names. Movies: always use the movie folder name.
        if url_anime_name and (is_movie_url or self.current_season == 1 or not self.anime_folder_name):
            self.anime_folder_name = url_anime_name
            self.config.set("LAST_ANIME_NAME", url_anime_name)
        return

    def _initialize_remote_from_search_query(
        self,
        anime_query: str,
        hint: Optional[Tuple[Optional[int], Optional[int]]] = None,
    ) -> Optional[str]:
        """
        Remote "init" without a URL: use a user-provided anime query to build the episode map,
        pick an initial episode (hint > first global), download it to cache, then start window downloads.

        This stores a synthesized LAST_GITHUB_URL pointing to the chosen file so the next startup
        can resume in URL-based remote init.
        """
        anime_query = (anime_query or "").strip()
        if not anime_query:
            return None

        # Default repo/ref for kitsunekko mirror if nothing else is known yet.
        self.github_owner = getattr(self, "github_owner", None) or self.config.get("GITHUB_OWNER") or "Ajatt-Tools"
        self.github_repo = getattr(self, "github_repo", None) or self.config.get("GITHUB_REPO") or "kitsunekko-mirror"
        self.github_ref = getattr(self, "github_ref", None) or self.config.get("GITHUB_REF") or "main"

        self.anime_folder_name = anime_query
        try:
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
        except Exception:
            pass

        # Build / load the remote episode listing and maps.
        self._create_remote_episode_map_per_season()
        self.build_remote_episode_maps()

        chosen_item = None
        if hint:
            hint_season, hint_episode = hint
            if hint_season is not None and hint_episode is not None:
                lst = getattr(self, "remote_episode_map_season", {}).get(int(hint_season), [])
                for it in lst:
                    if it.get("episode") == int(hint_episode):
                        chosen_item = it
                        break
            if chosen_item is None and hint_episode is not None:
                chosen_item = getattr(self, "remote_episode_map_global", {}).get(int(hint_episode))

        if not chosen_item and getattr(self, "remote_episode_map_global", None):
            keys = sorted(self.remote_episode_map_global.keys())
            if keys:
                chosen_item = self.remote_episode_map_global[keys[0]]

        if not chosen_item:
            logger.info("No remote subtitle candidates found for search query: %s", anime_query)
            return None

        target_remote_path = chosen_item.get("path")
        target_season = chosen_item.get("season")
        target_episode = chosen_item.get("episode")
        target_global = chosen_item.get("global")

        # If the chosen item only has global numbering, keep current_episode set so UI doesn't treat it as a movie.
        if target_season is None and target_episode is None and target_global is not None:
            self.current_season, self.current_episode = None, int(target_global)
        else:
            self.current_season, self.current_episode = target_season, target_episode

        if not target_remote_path:
            return None

        # Download synchronously so we can load immediately.
        try:
            season_dir = self._season_cache_dir(target_season) if target_season is not None else self._season_cache_dir()
            filename = self.sanitize_filename(os.path.basename(target_remote_path))
            local_path = os.path.join(season_dir, filename)
            raw_url = self._get_raw_url(target_remote_path)
            self._download_file(raw_url, local_path)
        except Exception:
            logger.exception("Failed to download chosen remote episode for search query: %s", target_remote_path)
            return None

        # Persist a concrete URL for restart-resume behavior.
        try:
            blob_url = f"https://github.com/{self.github_owner}/{self.github_repo}/blob/{self.github_ref}/{target_remote_path}"
            self.remote_url = blob_url
            self.config.set("LAST_GITHUB_URL", blob_url)
        except Exception:
            pass

        # Start background download window around the chosen global if available.
        try:
            center_global = target_global
            if center_global is None and target_season is not None and target_episode is not None:
                center_global = self.local_to_global(target_season, target_episode)
            if center_global is not None:
                self._schedule_prefetch_window(center_global)
        except Exception:
            logger.exception("Failed to start windowed background downloads for search-query init")

        return local_path
    
    def _search_remote_candidates_in_path(self, anime_query: str, repo_sub_path: str) -> List[Dict]:
        """
        Search GitHub code API for subtitle files under a specific path.
        Returns normalized items with parsed season/episode/global metadata.
        """
        anime_query = (anime_query or "").strip()
        repo_sub_path = (repo_sub_path or "").strip().strip("/")
        if not anime_query or not repo_sub_path:
            return []

        owner = getattr(self, "github_owner", None) or self.config.get("GITHUB_OWNER") or "Ajatt-Tools"
        repo = getattr(self, "github_repo", None) or self.config.get("GITHUB_REPO") or "kitsunekko-mirror"
        if not owner or not repo:
            return []

        api_url = "https://api.github.com/search/code"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "subtitle-searcher",
        }
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        per_page = 100
        out: List[Dict] = []
        seen_paths: set[str] = set()

        session = requests.Session()
        session.headers.update(headers)

        for ext in ("srt", "ass"):
            q = (f'repo:{owner}/{repo} '
                 f'path:{repo_sub_path} extension:{ext} in:path "{anime_query}"')
            page = 1
            while True:
                params = {"q": q, "per_page": per_page, "page": page}
                try:
                    resp = session.get(api_url, params=params, timeout=15)
                except requests.RequestException:
                    logger.exception("GitHub path search failed: query=%s", q)
                    break

                if resp.status_code != 200:
                    logger.error("GitHub path search failed (%s): %s", resp.status_code, resp.text)
                    break

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break

                for it in items:
                    path = it.get("path")
                    if not path or path in seen_paths:
                        continue
                    seen_paths.add(path)
                    name = os.path.basename(path or it.get("name") or "")
                    s, e, g = self.extract_season_episode_global(name)
                    if s is None and e is None and g is not None:
                        e = int(g)
                    out.append({
                        "name": name or it.get("name"),
                        "path": path,
                        "season": s,
                        "episode": e,
                        "global": g,
                    })

                if len(items) < per_page:
                    break
                page += 1
                time.sleep(0.1)

        try:
            out.sort(key=self.sort_key_per_season)
        except Exception:
            pass
        return out

    def search_remote_movie_candidates(self, anime_query: str) -> List[Dict]:
        """
        Search order for movie subtitles:
        1) subtitles/anime_movie
        2) subtitles/drama_movie (fallback only if #1 has zero results)
        """
        anime_query = (anime_query or "").strip()
        if not anime_query:
            return []

        results = self._search_remote_candidates_in_path(anime_query, "subtitles/anime_movie")
        if results:
            return results
        logger.info("No results in subtitles/anime_movie for '%s'; falling back to subtitles/drama_movie", anime_query)
        return self._search_remote_candidates_in_path(anime_query, "subtitles/drama_movie")

    def search_remote_drama_tv_candidates(self, anime_query: str) -> List[Dict]:
        """
        Search drama TV subtitles under subtitles/drama_tv.
        """
        anime_query = (anime_query or "").strip()
        if not anime_query:
            return []
        return self._search_remote_candidates_in_path(anime_query, "subtitles/drama_tv")
    

    def _trying_search_queries(self):
        #season and episode must be in the format of the github s02e0001 or e01 and so on
        url = self.config.get("LAST_GITHUB_URL")
        github_dict = self._parse_github_url(url)
        self.github_owner = github_dict["owner"]
        self.github_repo  = github_dict["repo"]
        self.anime_folder_name = self.config.get("LAST_ANIME_NAME")
        api_url = "https://api.github.com/search/code"
        headers = {"Accept": "application/vnd.github.v3+json",
                   "Authorization": f"token {self.github_token}",
                   "User-Agent": "subtitle-searcher"}
        per_page = 100
        search_query = f"{self.anime_folder_name}"
        q = (f'repo:{self.github_owner}/{self.github_repo}'
             f' path:subtitles/anime_tv extension:ass extension:srt in:path "{search_query}"')
        params = {"q": q, "per_page": per_page}
        all_results_items: List[Dict] = []
        page = 1
        logger.info(f"Building comprehensive episode map for {q}...")
        while True:
            params["page"] = page
            resp = requests.get(api_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if not items and page == 1:
                    print(f"GitHub search returned 0 items for query: {q}")
                for it in items:
                    name = os.path.basename(it.get("path") or it.get("name") or "")
                    s, e, global_e = self.extract_season_episode_global(name)
                    if s is None and e is None and global_e is None:
                        self._log_unparsed_filename(name=name, path=it.get("path"), reason="github_search_debug")
                    all_results_items.append({
                        "name": name,
                        "path": it.get("path"),
                        "season": s,
                        "episode": e,
                        "global": global_e,
                    })
                if len(items) < per_page:
                    break
                page += 1
                time.sleep(0.1)
                continue
            else:
                logger.error("GitHub search failed: %s", resp.text)
                break
        season_offset, local_numbering, season_len_est, season_len_density = self.compute_season_offsets_per_season(all_results_items)
        self.assign_globals_per_season(all_results_items, season_offset, local_numbering, season_len_est, season_len_density)
        all_results_items.sort(key=self.sort_key_per_season)
        sxexx_to_gxx = {}
        for it in all_results_items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            if s is not None and e is not None and g is not None:
                key = f"S{int(s):02d}E{int(e):02d}"
                # prefer lowest conflict-free mapping (but if duplicates exist we keep the first seen)
                if key not in sxexx_to_gxx:
                    sxexx_to_gxx[key] = int(g)

        if all_results_items:
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (self.anime_folder_name or ""))[:200] or "result"
            folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"github_search")
            os.makedirs(folder_dir, exist_ok=True)
            json_path = os.path.join(folder_dir, f"github_search_{safe_name}.json")
            payload = {
                "last search": q,
                "repo": f"{self.github_owner}/{self.github_repo}",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "result_count": len(all_results_items),
                "items": all_results_items,
                "sxexx_to_gxx": sxexx_to_gxx
            }
            try:
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                print(f"Wrote diagnostics to {json_path}")
            except Exception as e:
                print("Failed to write diagnostics JSON:", e)
        self.all_results_items = all_results_items
        return

    def _specific_episode_search(self,s,e):
        #season and episode must be in the format of the github s02e0001 or e01 and so on
        api_url = "https://api.github.com/search/code"
        headers = {"Accept": "application/vnd.github.v3+json",
                   "Authorization": f"token {self.github_token}",
                   "User-Agent": "subtitle-searcher"}
        per_page = 100
        if s is not None and e is not None:
            logger.info(f"Searching [S{s}E{e}] for {self.anime_folder_name}...")
            search_query = f"{self.anime_folder_name} s{s}e{e}"
        elif e is not None:
            search_query = f"{self.anime_folder_name} e{e}"
        else: search_query = f"{self.anime_folder_name}"
        q = (f'repo:{self.github_owner}/{self.github_repo}'
             f' path:subtitles/anime_tv extension:srt in:path "{search_query}"')
        params = {"q": q, "per_page": per_page}
        results: List[Dict] = []
        page = 1
        while True:
            params["page"] = page
            resp = requests.get(api_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if not items and page == 1:
                    print(f"GitHub search returned 0 items for query: {q}")
                for it in items:
                    results.append({
                        "name": it.get("name"),
                        "path": it.get("path")
                    })
                if len(items) < per_page:
                    break
                page += 1
                time.sleep(0.1)
                continue
            else:
                logger.error("GitHub search failed: %s", resp.text)
                break

    def _create_remote_episode_map_per_season(self):
        #add end_season and anime name searches in the gui
        end_season = 50
        # self.anime_folder_name = "Daini no Shokugyo"
        logger.info(f"Building comprehensive episode map for {self.anime_folder_name}...")

        # If we already have a cached episode map JSON for this anime, prefer it over
        # hitting the GitHub Search API again (avoids rate limits / repeat work).
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (self.anime_folder_name or ""))[:200] or "result"
        folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "github_search")
        cache_path = os.path.join(folder_dir, f"github_search_{safe_name}.json")
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                repo = payload.get("repo")
                expected_repo = f"{getattr(self, 'github_owner', None)}/{getattr(self, 'github_repo', None)}"
                if repo and expected_repo and repo != expected_repo:
                    logger.info("Cached search exists but repo mismatch (%s != %s); ignoring: %s", repo, expected_repo, cache_path)
                else:
                    cached_items = payload.get("items")
                    if isinstance(cached_items, list) and cached_items:
                        logger.info(
                            "Using cached GitHub search results: %s (created_at=%s, upgraded_at=%s)",
                            cache_path,
                            payload.get("created_at"),
                            payload.get("upgraded_at"),
                        )

                        # Trust the cache as-is (no reparsing, no "upgrade" rewrite).
                        # Rewriting a large JSON and re-running regex parsing can be noticeably CPU-heavy.
                        out_items: List[Dict] = []
                        for it in cached_items:
                            if not isinstance(it, dict):
                                continue
                            if not it.get("name") and it.get("path"):
                                it = dict(it)
                                it["name"] = os.path.basename(it["path"])
                            out_items.append(it)
                        self.all_results_items = out_items
                        return
            except Exception:
                logger.exception("Failed to load cached GitHub search results from: %s", cache_path)

        all_results_items: List[Dict] = []
        stop_reason = None
        last_rate_info = {}
        api_url = "https://api.github.com/search/code"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.github_token}",
            "User-Agent": "subtitle-searcher",
        }
        per_page = 100  
        
        def _print_rate_info(hdr):
            limit = hdr.get("X-RateLimit-Limit")
            remaining = hdr.get("X-RateLimit-Remaining")
            reset = hdr.get("X-RateLimit-Reset")
            retry_after = hdr.get("Retry-After")
            reset_time = None
            if reset:
                try:
                    reset_time = datetime.datetime.utcfromtimestamp(int(reset)).isoformat() + "Z"
                except Exception:
                    reset_time = reset
            print(f"Rate: limit={limit} remaining={remaining} reset={reset_time}")
            return {"limit": limit, "remaining": remaining, "reset": reset, "retry_after": retry_after}

        def _wait_until_reset(hdr_info):
            # honor Retry-After first
            ra = hdr_info.get("retry_after")
            if ra:
                try:
                    wait = int(ra) + 1
                except Exception:
                    wait = 60
                print(f"Server requested Retry-After {ra}s; sleeping {wait}s...")
                time.sleep(wait)
                return
            # otherwise use X-RateLimit-Reset
            reset = hdr_info.get("reset")
            if reset:
                try:
                    reset_ts = int(reset)
                    now_ts = int(time.time())
                    wait = max(reset_ts - now_ts + 3, 3)
                    reset_time = datetime.datetime.utcfromtimestamp(reset_ts).isoformat() + "Z"
                    print(f"Sleeping {wait}s until rate reset at {reset_time}...")
                    time.sleep(wait)
                    return
                except Exception:
                    pass
            # fallback
            print("No reset info available; sleeping 60s as fallback...")
            time.sleep(60)
            return
        
        session = requests.Session()
        session.headers.update(headers)

        provider_early_stop_enabled = bool(self.config.get("SEASON_PROVIDER_EARLY_STOP_ENABLED") or False)
        try:
            raw_min_found = self.config.get("SEASON_PROVIDER_EARLY_STOP_MIN_FOUND_SEASONS")
            provider_early_stop_min_found_seasons = int(raw_min_found) if raw_min_found is not None else 1
        except Exception:
            provider_early_stop_min_found_seasons = 1
        provider_early_stop_min_found_seasons = max(1, provider_early_stop_min_found_seasons)

        # Provider "try" that succeeded for season 1 (1=Netflix, 2=Amazon).
        primary_provider_try = None
        primary_provider_found_seasons = 0

        season = 1
        while True: #search season until none found
            logger.info(f"\nSearching season {season:02d}")
            found_any_for_season = False
            old_length = len(all_results_items)
            season_searching = True
            stop_season_loop = False

            strict_provider_only = (
                provider_early_stop_enabled
                and primary_provider_try in (1, 2)
                and season > 1
                and primary_provider_found_seasons >= provider_early_stop_min_found_seasons
            )

            # Tries: 1=Netflix, 2=Amazon, 5=unseasoned fallback (only for season 1).
            if strict_provider_only:
                tries_to_run = [primary_provider_try]
            else:
                if primary_provider_try == 2:
                    tries_to_run = [2, 1]
                else:
                    tries_to_run = [1, 2]
                if season == 1:
                    tries_to_run.append(5)

            for tries in tries_to_run:  # if 0 hits try amazon instead of netflix then without both and so on can add more fallbacks later
                if tries == 1:
                    search_query = f"{self.anime_folder_name} s{season:02d} Netflix"
                elif tries == 2:
                    search_query = f"{self.anime_folder_name} s{season:02d} Amazon"
                elif tries == 3:
                    continue #skip for now
                    search_query = f"{self.anime_folder_name} s{season:02d} Hulu"
                elif tries == 4:
                    continue #skip for now
                    search_query = f"{self.anime_folder_name} s{season:02d}"
                elif tries == 5:
                    # continue #skip for now
                    search_query = f"{self.anime_folder_name}"
                    season_searching = False
                provider_found = False

                # Prefer .srt, but if nothing is found (ass-only anime) try .ass as a fallback.
                for ext in ("srt", "ass"):
                    q = (f'repo:{self.github_owner}/{self.github_repo}'
                         f' path:subtitles extension:{ext} in:path {search_query}')
                    params = {"q": q, "per_page": per_page}
                    page = 1
                    while True:
                        params["page"] = page
                        try: resp = session.get(api_url, params=params, timeout=15)
                        except requests.RequestException as e:# network error: stop and return what we have
                            logger.error(f"network error: {e}")
                            return
                        hdr = resp.headers
                        last_rate_info = _print_rate_info(hdr)
                        if resp.status_code == 200:
                            data = resp.json()
                            items = data.get("items", [])
                            if not items:
                                if page == 1:
                                    print(f"GitHub search returned 0 items for query: {q}")
                                break

                            provider_found = True
                            found_any_for_season = True
                            for it in items:
                                name = os.path.basename(it.get("path") or it.get("name") or "")
                                s, e, global_e = self.extract_season_episode_global(name)
                                if s is None and e is None and global_e is None:
                                    self._log_unparsed_filename(name=name, path=it.get("path"), reason="github_search")
                                all_results_items.append({
                                    "name": name,
                                    "path": it.get("path"),
                                    "season": s,
                                    "episode": e,
                                    "global": global_e,
                                })

                            # stop when fewer than per_page items returned (no more pages)
                            if len(items) < per_page:
                                break

                            page += 1

                            # Only wait when we actually need another request.
                            rem = last_rate_info.get("remaining")
                            try:
                                rem_i = int(rem) if rem is not None else None
                            except Exception:
                                rem_i = None
                            if rem_i is not None and rem_i <= 0:
                                _wait_until_reset(last_rate_info)

                            time.sleep(0.1)
                            continue
                        # Rate-limited or retryable responses: 403 / 429
                        if resp.status_code == 403 or resp.status_code == 429:
                            # try to parse message
                            try:
                                msg = resp.json().get("message", "")
                            except Exception:
                                msg = resp.text or ""
                            # honor Retry-After header if provided
                            if hdr.get("Retry-After"):
                                print("Retry-After header present; waiting as requested...")
                                _wait_until_reset(last_rate_info)
                                continue
                            # if remaining==0 or message mentions rate limit -> wait until reset
                            rem = last_rate_info.get("remaining")
                            if rem == "0" or (rem is not None and int(rem) == 0) or "rate limit" in msg.lower():
                                print("Rate limit reached; will wait until reset and then continue...")
                                _wait_until_reset(last_rate_info)
                                continue
                            # abuse detection -> wait a longer time then retry
                            if "abuse" in msg.lower():
                                print(f"Abuse detection triggered: {msg}. Sleeping 120s then retrying...")
                                time.sleep(120)
                                continue
                            raise RuntimeError(f"GitHub search failed: {resp.status_code}, {resp.text}")
                        # Search API 1000-results cap
                        if resp.status_code == 422:
                            print("Search API 422 (cannot access beyond the first 1000 results). Stopping and returning partial results.")
                            stop_reason = "search_api_1000_cap"
                            if not season_searching:
                                stop_season_loop = True
                            break
                        raise RuntimeError(f"GitHub search failed: {resp.status_code}, {resp.text}")

                    if provider_found or stop_season_loop:
                        break
                if provider_found:
                    # Remember which provider succeeded for season 1, and count how many seasons
                    # have results on that same provider (used for optional early stopping).
                    if season == 1 and tries in (1, 2) and primary_provider_try is None:
                        primary_provider_try = tries
                        primary_provider_found_seasons = 1
                        if provider_early_stop_enabled:
                            logger.info(
                                "Provider early stop enabled: season 01 succeeded with %s",
                                "Netflix" if tries == 1 else "Amazon",
                            )
                    elif tries == primary_provider_try:
                        primary_provider_found_seasons += 1
                    break
                if stop_season_loop:
                    break
            if stop_season_loop:
                break

            if strict_provider_only and not found_any_for_season and primary_provider_try in (1, 2):
                provider_label = "Netflix" if primary_provider_try == 1 else "Amazon"
                stop_reason = f"provider_early_stop:{provider_label}:S{season:02d}"
                logger.info(
                    "Provider early stop: no results for %s season %02d (after %d seasons found on that provider). Stopping season search.",
                    provider_label,
                    season,
                    primary_provider_found_seasons,
                )
                break
            if not found_any_for_season:
                logger.info(f"No providers found results for season {season:02d}, stopping.")
                break  # stop season loop entirely
            print("For season:",season, " we found ",len(all_results_items)-old_length,"files")
            if not season_searching:
                if stop_reason is None:
                    stop_reason = "fallback_unseasoned_search"
                break
            season += 1
            if season == end_season:
                break
            if self.anime_folder_name == "HUNTER×HUNTER" and season == 7:
                break
            if self.anime_folder_name == "Shingeki no Kyojin" and season == 8:
                break
            if self.anime_folder_name == "One Piece" and season == 40:
                break
        season_offset, local_numbering, season_len_est, season_len_density = self.compute_season_offsets_per_season(all_results_items)
        self.assign_globals_per_season(all_results_items, season_offset, local_numbering, season_len_est, season_len_density)
        all_results_items.sort(key=self.sort_key_per_season)
        sxexx_to_gxx = {}
        for it in all_results_items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            if s is not None and e is not None and g is not None:
                key = f"S{int(s):02d}E{int(e):02d}"
                # prefer lowest conflict-free mapping (but if duplicates exist we keep the first seen)
                if key not in sxexx_to_gxx:
                    sxexx_to_gxx[key] = int(g)

        if all_results_items:
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (self.anime_folder_name or ""))[:200] or "result"
            folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"github_search")
            os.makedirs(folder_dir, exist_ok=True)
            json_path = os.path.join(folder_dir, f"github_search_{safe_name}.json")
            payload = {
                "last search": q,
                "repo": f"{self.github_owner}/{self.github_repo}",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "stop_reason": stop_reason,
                "rate_info": last_rate_info,
                "result_count": len(all_results_items),
                "items": all_results_items,
                "sxexx_to_gxx": sxexx_to_gxx
            }
            try:
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                print(f"Wrote diagnostics to {json_path}")
            except Exception as e:
                print("Failed to write diagnostics JSON:", e)
        self.all_results_items = all_results_items
        return
    
    def sort_key_per_season(self, it):
        # 1) items with global first
        if it.get("global") is not None:
            return (0, it["global"])
        # 2) then season+episode
        if it.get("season") is not None and it.get("episode") is not None:
            return (1, it["season"], it["episode"])
        # 3) unknowns last
        return (2, it.get("name", ""))

    def compute_season_offsets_per_season(self, items):
        """
        Returns (season_offset, local_numbering, season_len_est, season_len_density)
        - season_offset: dict season -> offset (used when season uses local numbering)
        - local_numbering: dict season -> bool (True if season's episode numbers are local i.e. include E1)
        - season_len_est: dict season -> estimated local season length (robust against outliers like S02E074)
        - season_len_density: dict season -> density of observed episodes in [1..season_len_est]
        """
        season_eps = defaultdict(set)     # season -> set(episode numbers)
        season_eps_by_provider = defaultdict(lambda: defaultdict(set))  # season -> provider -> set(episode numbers)
        pairs = defaultdict(lambda: defaultdict(set))  # season -> episode -> set(globals)

        def _provider_token(s: str) -> str:
            s = (s or "").lower()
            if "netflix" in s:
                return "netflix"
            if "amazon" in s:
                return "amazon"
            if "hulu" in s:
                return "hulu"
            if "bandai" in s:
                return "bandai"
            if "TV" in s:
                return "TV"
            return "other"

        for it in items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            if s is None or e is None:
                continue
            season_eps[s].add(int(e))
            prov = _provider_token(it.get("name") or it.get("path") or "")
            season_eps_by_provider[s][prov].add(int(e))
            if g is not None:
                pairs[s][int(e)].add(int(g))

        # Decide whether seasons use local numbering (contain an episode 1) or not.
        local_numbering = {s: (1 in eps) for s, eps in season_eps.items()}

        # Estimate local season length (used for robust running offsets and per-item heuristics).
        # Some sources mix global numbering into "seasoned" files (e.g. S02E074 meaning global 74),
        # so using max(eps) overestimates the true local season length and breaks later season offsets.
        season_len_est = {}
        season_len_density = {}

        def _estimate_local_len(eps: set[int]) -> tuple[int, float]:
            eps = {int(x) for x in eps if x is not None and int(x) > 0}
            if not eps:
                return 0, 0.0
            mx = max(eps)
            # Not enough samples -> don't guess; treat as full span.
            if len(eps) < 10 or 1 not in eps:
                return mx, (len(eps) / mx) if mx else 0.0

            # Choose the largest k where observed density in [1..k] stays high.
            threshold = 0.80
            seen = 0
            best_k = 1
            best_density = 1.0
            for k in sorted(eps):
                seen += 1
                density = seen / k
                if density >= threshold:
                    best_k = k
                    best_density = density
            return best_k, float(best_density)

        preferred_provider_order = ("netflix", "amazon", "hulu")
        for s, eps in season_eps.items():
            if not local_numbering.get(s, False):
                continue

            eps_for_len = eps
            for prov in preferred_provider_order:
                cand = season_eps_by_provider.get(s, {}).get(prov)
                if cand and 1 in cand:
                    eps_for_len = cand
                    break

            k, dens = _estimate_local_len(eps_for_len)
            season_len_est[int(s)] = int(k)
            season_len_density[int(s)] = float(dens)

        # Compute offsets from explicit (season,episode)->global pairs (one vote per episode)
        offsets_votes = defaultdict(list)
        for s, ep_map in pairs.items():
            # For each episode in this season that has explicit global(s), create votes
            for e, gs in ep_map.items():
                # If multiple different global values exist for same (s,e), take them all once
                for gg in gs:
                    offsets_votes[s].append(int(gg) - int(e))

        season_offset = {}
        # Accept majority offsets for seasons that use local numbering
        for s, votes in offsets_votes.items():
            if not votes:
                continue
            cnt = Counter(votes)
            most_common, count = cnt.most_common(1)[0]
            # require at least one clear vote and either >=2 votes or >50% agreement
            if count >= max(1, len(votes) * 0.5):
                season_offset[s] = most_common

        # Fallback: compute cumulative running global using max episode (deduped) for previous seasons
        ordered = sorted(season_eps.keys())
        running = 0
        for s in ordered:
            if local_numbering.get(s, True):
                # If no computed offset, use running as fallback
                if s not in season_offset:
                    season_offset[s] = running
                # Use robust local season length estimate rather than max(eps) to avoid outliers
                # inflating later season offsets.
                est_len = season_len_est.get(int(s))
                if est_len is None:
                    est_len = max(season_eps[s]) if season_eps[s] else 0
                running = max(running, season_offset[s] + int(est_len))
            else:
                # season uses global numbering: update running to be at least the max global seen
                running = max(running, max(season_eps[s]) if season_eps[s] else running)

        return season_offset, local_numbering, season_len_est, season_len_density

    def assign_globals_per_season(self, items, season_offset, local_numbering, season_len_est, season_len_density):
        used_globals = set(it['global'] for it in items if it.get('global') is not None)
        season_item_counts = Counter()
        for it in items:
            s = it.get('season')
            e = it.get('episode')
            if s is None or e is None:
                continue
            season_item_counts[int(s)] += 1

        for it in items:
            if it.get('global') is not None:
                continue
            s = it.get('season')
            e = it.get('episode')
            if s is None or e is None:
                continue
            # If season uses global numbering already -> use episode value as global
            if not local_numbering.get(s, True):
                it['global'] = int(e)
            else:
                # Default mapping assumes local numbering within a season.
                # If we have strong evidence that this season has a dense low-episode range
                # (e.g. 1..50) and this particular item has an episode number far above that,
                # it's likely using global numbering despite having an Sxx prefix.
                local_len = season_len_est.get(int(s))
                dens = season_len_density.get(int(s), 0.0)
                if (local_len is not None
                        and season_item_counts.get(int(s), 0) >= 10
                        and int(local_len) >= 10
                        and float(dens) >= 0.85
                        and int(e) > int(local_len) + 2):
                    it['global'] = int(e)
                    it.setdefault('conflicts', []).append('season_mixed_numbering')
                else:
                    it['global'] = int(season_offset.get(s, 0)) + int(e)

            if it['global'] in used_globals:
                it.setdefault('conflicts', []).append('global_collision')
            used_globals.add(it['global'])

    def build_remote_episode_maps(self):
        """
        Build convenient lookup maps from self.all_results_items:
        - self.remote_episode_map_global: global_index -> item
        - self.remote_episode_map_season: season -> list[item]
        Each item contains: season, episode, global, path, name.
        """
        self.remote_episode_map_global = {}
        self.remote_episode_map_season = defaultdict(list)
        if not getattr(self, "all_results_items", None):
            return
        for it in self.all_results_items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            path = it.get("path") or it.get("name")
            name = it.get("name")
            entry = {"season": s, "episode": e, "global": g, "path": path, "name": name}
            entry["_score"] = self._subtitle_candidate_score(name, path)
            if g is not None:
                try:
                    gi = int(g)
                    prev = self.remote_episode_map_global.get(gi)
                    # Prefer higher-scored variants (JP, SxxExx) instead of "last one wins".
                    if prev is None or int(entry["_score"]) > int(prev.get("_score") or 0):
                        self.remote_episode_map_global[gi] = entry
                except Exception:
                    pass
            if s is not None:
                try:
                    self.remote_episode_map_season[int(s)].append(entry)
                except Exception:
                    pass
        # sort season lists by episode, but keep the "best" variant first for each episode
        for s, lst in self.remote_episode_map_season.items():
            lst.sort(key=lambda x: (x.get("episode") or 0, -(x.get("_score") or 0), x.get("global") or 0, x.get("name") or ""))

    def global_to_local(self, global_idx: int) -> Tuple[Optional[int], Optional[int]]:
        """Return (season, episode) for a given global index, or (None, None)."""
        if global_idx is None:
            return None, None
        if not hasattr(self, "remote_episode_map_global"):
            self.build_remote_episode_maps()
        it = self.remote_episode_map_global.get(int(global_idx))
        if not it:
            return None, None
        return (int(it["season"]) if it.get("season") is not None else None,
                int(it["episode"]) if it.get("episode") is not None else None)

    def local_to_global(self, season: Optional[int], episode: Optional[int]) -> Optional[int]:
        """Return global index for given local season/episode if known, else None."""
        if season is None or episode is None:
            return None
        if not hasattr(self, "remote_episode_map_season"):
            self.build_remote_episode_maps()
        for it in self.remote_episode_map_season.get(int(season), []):
            if it.get("episode") == int(episode) and it.get("global") is not None:
                return int(it["global"])
        return None

    def get_current_global(self) -> Optional[int]:
        """Try to determine a global index for the currently loaded subtitle."""
        # 1) try to parse from the current filename
        if getattr(self, "srt_file", None):
            _, _, g = self.extract_season_episode_global(os.path.basename(self.srt_file))
            if g:
                return int(g)
        # 2) try mapping from current season/episode
        if getattr(self, "current_season", None) and getattr(self, "current_episode", None):
            g = self.local_to_global(self.current_season, self.current_episode)
            if g:
                return g
        # 3) last-resort: if remote path corresponds to an item with global
        if getattr(self, "remote_path", None) and getattr(self, "all_results_items", None):
            basename = os.path.basename(self.remote_path)
            for it in self.all_results_items:
                if it.get("name") == basename or it.get("path") == self.remote_path:
                    if it.get("global") is not None:
                        return int(it["global"])
        return None

    def download_window_around_global(self, center_global: int, window: int, async_download: bool = True):
        """
        Download files with global indices in [center_global - window, center_global + window].
        Creates season cache dirs and downloads missing subtitle files.

        If async_download is True, downloads are queued onto a small fixed set of daemon
        worker threads (smears CPU usage and avoids spawning one thread per file).
        """
        if center_global is None:
            return []
        if not hasattr(self, "remote_episode_map_global"):
            self.build_remote_episode_maps()

        got: List[str] = []
        lo = max(1, int(center_global) - int(window))
        hi = int(center_global) + int(window)

        # Prefer downloading "closest to the center" first (helps next/prev navigation).
        ordered_globals: List[int] = [int(center_global)]
        for d in range(1, int(window) + 1):
            ordered_globals.append(int(center_global) - d)
            ordered_globals.append(int(center_global) + d)
        # Clamp + de-dup while keeping order.
        seen_g: set[int] = set()
        ordered_globals = [g for g in ordered_globals if lo <= g <= hi and (g not in seen_g and not seen_g.add(g))]

        for g in ordered_globals:
            item = self.remote_episode_map_global.get(int(g))
            if not item or not item.get("path"):
                continue
            season = item.get("season") or self.current_season
            season_dir = self._season_cache_dir(season)
            filename = self.sanitize_filename(os.path.basename(item["path"]))
            local_path = os.path.join(season_dir, filename)
            if os.path.exists(local_path):
                got.append(local_path)
                continue
            raw_url = self._get_raw_url(item["path"])
            if not async_download:
                try:
                    self._download_file(raw_url, local_path)
                    got.append(local_path)
                except Exception:
                    pass
            else:
                self._enqueue_download(raw_url, local_path)
                got.append(local_path)

        # Only refresh local index for synchronous downloads where we know files exist now.
        if not async_download:
            try:
                self.update_local_srt_files()
            except Exception:
                logger.exception("Failed to refresh local_srt_files after synchronous window download")
        return got

    def update_local_srt_files(self):
        """
        Scan cache and build self.local_srt_files: list of dicts with keys:
        'season','episode','global','path','name'
        """
        self.local_srt_files = []
        base = self._get_cache_base_dir()
        anime_dir = os.path.join(base, self.anime_folder_name) if self.anime_folder_name else base
        if not os.path.isdir(anime_dir):
            return self.local_srt_files
        for root, _dirs, files in os.walk(anime_dir):
            for fn in files:
                if not fn.lower().endswith((".srt", ".ass", ".ssa")):
                    continue
                full = os.path.join(root, fn)
                s, e, g = self.extract_season_episode_global(fn)
                if s is None and e is None and g is None:
                    self._log_unparsed_filename(name=fn, path=full, reason="local_cache_scan")
                # Normalize global-only files so they behave like episodes in the UI/navigation.
                if s is None and e is None and g is not None:
                    e = int(g)
                # if file name had no explicit global, try to map via remote maps
                if g is None and s is not None and e is not None:
                    g = self.local_to_global(s, e)
                rec = {"season": s, "episode": e, "global": g, "path": full, "name": fn}
                # Used only for deterministic selection when multiple variants exist for one episode.
                rec["_score"] = self._subtitle_candidate_score(fn, full)
                self.local_srt_files.append(rec)
        # Keep selection deterministic: prefer higher-scored (JP, SxxExx) variants when duplicates exist.
        def _sort_key(x):
            g = x.get("global")
            s = x.get("season") or 0
            e = x.get("episode") or 0
            sc = x.get("_score") or 0
            if g is not None:
                return (0, int(g), -int(sc), x.get("name") or "")
            return (1, int(s), int(e), -int(sc), x.get("name") or "")
        self.local_srt_files.sort(key=_sort_key)
        return self.local_srt_files



    def _parse_github_url(self, url: str) -> Dict[str, Optional[str]]:  
        p = urlparse(url)
        path = unquote(p.path)
        parts = path.strip('/').split('/')
        out = {'owner': None, 'repo': None, 'ref': None, 'path': None, 'is_file': False, 'filename': None}
        if p.netloc.endswith('github.com'):
            if len(parts) >= 2:
                out['owner'] = parts[0]
                out['repo'] = parts[1]
            if len(parts) >= 3 and parts[2] in ('blob', 'tree'):
                mode = parts[2]
                if len(parts) >= 4:
                    out['ref'] = parts[3]
                    internal = parts[4:]
                    out['path'] = '/'.join(internal) if internal else ''
                    if mode == 'blob' and out['path']:
                        out['is_file'] = True
                        out['filename'] = os.path.basename(out['path'])
            else:
                out['path'] = '/'.join(parts[2:]) if len(parts) > 2 else ''
        elif p.netloc == 'raw.githubusercontent.com':
            if len(parts) >= 4:
                out['owner'], out['repo'], out['ref'] = parts[0], parts[1], parts[2]
                out['path'] = '/'.join(parts[3:])
                out['is_file'] = True
                out['filename'] = os.path.basename(out['path'])
        return out

    # ---------------------- Diagnostics: unparsed filenames ----------------------
    def _get_unparsed_log_path(self) -> str:
        folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "github_search")
        os.makedirs(folder_dir, exist_ok=True)
        return os.path.join(folder_dir, "unparsed_episode_names.tsv")

    def _ensure_unparsed_seen_loaded(self) -> None:
        if getattr(self, "_unparsed_seen_loaded", False):
            return
        self._unparsed_seen_loaded = True
        self._unparsed_seen = set()
        log_path = self._get_unparsed_log_path()
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    parts = line.split("\t")
                    # expected: ts, anime, name, path, reason
                    key = None
                    if len(parts) >= 4:
                        key = parts[3] or parts[2]
                    elif len(parts) >= 3:
                        key = parts[2]
                    if key:
                        self._unparsed_seen.add(key)
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("Failed to read unparsed log file: %s", log_path)

    def _log_unparsed_filename(self, name: str, path: Optional[str] = None, reason: str = "") -> None:
        if not name:
            return
        lock = getattr(self, "_unparsed_log_lock", None)
        if lock is None:
            self._unparsed_log_lock = threading.Lock()
            lock = self._unparsed_log_lock

        with lock:
            self._ensure_unparsed_seen_loaded()
            log_path = self._get_unparsed_log_path()

            key = path or name
            if key in self._unparsed_seen:
                return
            self._unparsed_seen.add(key)

            def _clean(s: Optional[str]) -> str:
                s = s or ""
                return s.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()

            ts = datetime.datetime.utcnow().isoformat() + "Z"
            anime = getattr(self, "anime_folder_name", None) or ""
            line = f"{_clean(ts)}\t{_clean(anime)}\t{_clean(name)}\t{_clean(path)}\t{_clean(reason)}\n"
            try:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except Exception:
                logger.exception("Failed to append to unparsed log file: %s", log_path)

# ---------------------- helpers: cache dirs ----------------------base
    def _season_cache_dir(self, season: Optional[int] = None) -> str:
        base = self._get_cache_base_dir()
        season_num = season if season is not None else self.current_season
        # Avoid creating "SeasonNone" folders; use Season0 for global-only numbering.
        if season_num is None:
            season_num = 0
        season_dir = os.path.join(base, self.anime_folder_name,f"Season{season_num}")
        if not os.path.exists(season_dir):
            os.makedirs(season_dir, exist_ok=True)
        return season_dir
    
    def _get_cache_base_dir(self) -> str: #get current base directory
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
        base = os.path.join(project_root, "cache_github")
        os.makedirs(base, exist_ok=True)
        return base
# ---------------------- helpers: cache dirs ----------------------

# ---------------------- Helpers: parsing ----------------------
    def normalize_name(self, name: str) -> str:
        s = name.replace('\\','/').split('/')[-1]
        s = re.sub(r'\[.*?\]', '', s)
        s = re.sub(r'\{.*?\}', '', s)
        s = re.sub(r'\.(mkv|mp4|srt|ass|avi)$', '', s, flags=re.IGNORECASE)
        s = s.replace('–','-').replace('—','-')
        s = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '', s)
        tokens = re.split(r'([.\s_\-()\[\]]+)', s)
        filtered = []
        for t in tokens:
            if re.match(r'[.\s_\-()\[\]]+', t):
                filtered.append(t)
            else:
                if not self.is_noise_token(t):
                    filtered.append(t)
        s2 = ''.join(filtered).strip()
        s2 = re.sub(r'[._]+', ' ', s2)
        s2 = re.sub(r'\s+', ' ', s2).strip()
        return s2

    def _subtitle_candidate_score(self, name: Optional[str], path: Optional[str] = None) -> int:
        """
        Heuristic score to prefer "better" subtitle variants when multiple files map to the same episode/global.

        Goals (user preference):
        - Prefer Japanese subs (ja/jpn/ja[cc]) over English.
        - Prefer filenames containing SxxExx when available.
        - Keep behavior deterministic (avoid "last one wins" overwrites).
        """
        text = f"{name or ''} {path or ''}"
        t = text.lower()
        score = 0

        # Language preference (strong)
        if re.search(r'(?i)(?:^|[^a-z0-9])(jpn|ja)(?:$|[^a-z0-9])', text) or "ja[cc]" in t:
            score += 200
        if re.search(r'(?i)(?:^|[^a-z0-9])(eng|english)(?:$|[^a-z0-9])', text):
            score -= 300
        # Common compact token " en" / ".en." / "_en_" etc.
        if re.search(r'(?i)(?:^|[^a-z0-9])en(?:$|[^a-z0-9])', text):
            score -= 150

        # Prefer explicit season/episode tags in filename.
        if re.search(r'(?i)\bS\d{1,2}[ ._\-]*E\d{1,4}\b', text):
            score += 120
        # Weak positive: other episode markers (still better than nothing)
        if re.search(r'(?i)\b(?:ep|episode)\s*\.?\s*\d{1,4}\b', text):
            score += 25
        if re.search(r'(?i)\bE\d{1,4}\b', text):
            score += 15
        if re.search(r'第\s*\d{1,4}\s*話', text):
            score += 20
        if re.search(r'(?:シーズン|ｼｰｽﾞﾝ)\s*\d{1,2}\s*[-‐‑–—ー]\s*\d{1,4}', text):
            score += 30

        # Small provider preference (tie-breaker)
        if "netflix" in t:
            score += 10
        if "amazon" in t:
            score += 5

        return int(score)
    
    def is_noise_token(self, token: str) -> bool:
        t = token.lower().strip(" ._-()[]{}")
        if not t:
            return True
        if re.match(r'^\d{3,4}p$', t): return True
        if re.match(r'^\d{3,4}x\d{3,4}$', t): return True
        if t in self.NOISE_TOKENS: return True
        if re.match(r'^(x26[45]|av1|hevc|h264|aac|ac3|flac)$', t):
            return True
        if re.match(r'^(19|20)\d{2}$', t):
            return True
        return False

    def extract_season_episode_global(self, filename: str, folder_path: Optional[str] = None) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        name = filename or ""
        s = e = g = None
        n = name.replace('\u2013', '-').replace('\u2014', '-')
        def is_probable_episode_number(num: int) -> bool:
            if 1 <= num <= 1600:
                return True
        # Regexes
        # Episode numbers can be up to 4 digits for long-running shows (e.g. One Piece E1135).
        #
        # IMPORTANT: We can't rely on \b boundaries for SxxEyy because many Japanese filenames are like:
        # "名探偵コナンS10 E1 - 第384話..." (no separator before "S").
        # Use an ASCII-only "not preceded by [A-Za-z0-9]" guard instead.
        re_s_e_paren = re.compile(
            r'(?xi)(?<![A-Za-z0-9])S(?P<s>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})(?!\d)'
            r'[^()\[\]]*[\(\[]\s*(?P<g>\d{1,4})\s*[\)\]]'
        )
        re_s_e = re.compile(r'(?xi)(?<![A-Za-z0-9])S(?P<s>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})(?!\d)')
        re_s_paren = re.compile(r'(?xi)(?<![A-Za-z0-9])S(?P<s>\d{1,2})(?!\d)[ ._\-]*[\(\[]\s*(?P<g>\d{1,4})\s*[\)\]]')
        re_episode_number = re.compile(r'(?xi)\b(?:ep|episode|ep\.)[ ._\-#]*(?P<num>\d{1,4})\b')
        # Similar to SxxEyy: don't rely on \b because filenames can contain "_E60_" etc.
        re_e_token = re.compile(r'(?xi)(?<![A-Za-z0-9])E(?P<num>\d{1,4})(?!\d)')
        # Japanese "第384話" style global episode markers.
        # Use explicit unicode escapes to avoid source-encoding / mojibake issues.
        re_jp_episode = re.compile(r'(?x)\u7b2c\s*(?P<num>\d{1,4})\s*\u8a71')
        re_jp_season_dash = re.compile(r'(?x)(?:シーズン|ｼｰｽﾞﾝ)\s*(?P<s>\d{1,2})\s*[-‐‑–—ー]\s*(?P<e>\d{1,4})')
        re_bracket_number = re.compile(r'[\(\[]\s*(\d{1,4})\s*[\)\]]')
        re_trailing_number = re.compile(r'(?xi)(?:[_\-. ]|^)(?P<num>\d{1,4})(?:\.[a-z0-9]{1,6})?$')
        # Common fansub pattern: "Show Name - 123 [720p].srt" (no season info -> treat as global)
        re_dash_number = re.compile(r'(?x)\s-\s(?P<num>\d{1,4})\b')
        # Compact variant: "Kyojin-19[...].ass" / "Show-76(...).srt"
        # Avoid matching resolutions like "-1080p" by rejecting letters right after the digits.
        re_dash_number_compact = re.compile(r'(?x)-(?P<num>\d{1,4})(?![A-Za-z])')

        # 1) SxxEyy (GGG) -> explicit local + bracketed global
        m = re_s_e_paren.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e')); gnum = int(m.group('g'))
                if is_probable_episode_number(gnum):
                    g = gnum
                else:
                    g = None
            except Exception:
                pass
            if s == 1 and e is not None:
                g = e
            return s, e, g

        # 2) SxxEyy -> local episode (conservative: do not treat as global unless we have
        # an explicit global marker like "第384話" in the same filename).
        m = re_s_e.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
            except Exception:
                return None, None, None
            # If the filename contains an explicit global episode marker, prefer it.
            # Example: "名探偵コナンS10 E1 - 第384話...srt" -> s=10,e=1,g=384.
            m_g = re_jp_episode.search(n)
            if m_g:
                try:
                    gnum = int(m_g.group("num"))
                    if is_probable_episode_number(gnum):
                        return s, e, gnum
                except Exception:
                    pass
            if s == 1:
                return s, e, e
            return s, e, None

        # 3) Sxx (GGG) -> season present, bracket likely a global index (no E present)
        m = re_s_paren.search(n)
        if m:
            try:
                s = int(m.group('s')); gnum = int(m.group('g'))
                if is_probable_episode_number(gnum):
                    g = gnum
            except Exception:
                pass
            if s == 1 and g is not None:
                return s, g, g
            return s, None, g

        # 4) textual "Season X Episode Y"
        re_season_episode_words = re.compile(r'(?xi)\bseason[ ._\-]*(?P<s>\d{1,2})[^\d]{0,12}episode[ ._\-]*(?P<e>\d{1,4})\b')
        m = re_season_episode_words.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
            except Exception:
                return None, None, None
            if s == 1:
                return s, e, e
            return s, e, None

        # 4b) Japanese "シーズンX-Y" (common on some subtitle sources)
        m = re_jp_season_dash.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
            except Exception:
                return None, None, None
            if s == 1:
                return s, e, e
            return s, e, None

        # 5) "Ep 38" or "Episode 38" - if a season exists elsewhere treat as local episode; else treat as global
        m = re_episode_number.search(n)
        if m:
            try:
                num = int(m.group('num'))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None

            return None, None, num

        # 5b) standalone "E254" token - if a season exists elsewhere treat as local; else treat as global
        m = re_e_token.search(n)
        if m:
            try:
                num = int(m.group('num'))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None
            return None, None, num

        # 5c) Japanese "第255話" -> global episode number
        m = re_jp_episode.search(n)
        if m:
            try:
                num = int(m.group('num'))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None
            return None, None, num

        # 6) bracketed numbers (general). If season present and no E present: bracket likely global.
        #    If season+E present we would have returned earlier; if both S and E exist earlier we prefer E as local and bracket as global.
        for br in re_bracket_number.findall(n):
            try:
                num = int(br)
                if not is_probable_episode_number(num):
                    continue
            except Exception:
                continue
            s_m = re.search(r'(?xi)\bS(?P<s>\d{1,2})\b', n)
            e_m = re.search(r'(?xi)\bS(?P<s2>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})\b', n)
            if s_m and e_m:
                # filename contains SxxEyy and also bracket: treat bracket as global and return both
                s = int(s_m.group('s')); e = int(e_m.group('e')); g = num
                if s == 1:
                    return s, e, e
                return s, e, num
            if s_m:
                # season present but no explicit E -> bracket is probably the global index
                s = int(s_m.group('s'))
                if s == 1:
                    return s, num, num
                return s, None, num
            return None, None, num

        # 7) dash-number patterns (e.g. " - 123 " or "Kyojin-19[...]") as global index
        #    (or local if season is present)
        m = re_dash_number.search(n) or re_dash_number_compact.search(n)
        if m:
            try:
                num = int(m.group("num"))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None

            return None, None, num

        # 8) trailing number heuristics:
        m = re_trailing_number.search(n)
        if m:
            try:
                num = int(m.group("num"))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None

            return None, None, num

        # nothing confident
        return None, None, None

    def _extract_anime_name_from_url(self, remote_path: str) -> Optional[str]:
        parts = remote_path.split("/")
        try:
            idx = parts.index("subtitles")
            return parts[idx + 2]  # folder 2 under /subtitles/
        except ValueError:
            return None

    def _get_raw_url(self, filename: str) -> str:
        return f"https://raw.githubusercontent.com/{self.github_owner}/{self.github_repo}/{self.github_ref}/{filename}"
# ---------------------- Helpers: parsing ----------------------

# ---------------------- GitHub searching / downloading ----------------------
    def download_current_episode(self, remote_path):
        file_name = self.sanitize_filename(os.path.basename(remote_path))
        season_dir = self._season_cache_dir()  # Create directory when actually downloading
        local_path = os.path.join(season_dir, file_name)
        raw_url = self._get_raw_url(remote_path)
        self._download_file(raw_url, local_path)
        return local_path

    def sanitize_filename(self,filename: str) -> str:
        # Replace invalid Windows characters with underscore
        return re.sub(r'[<>:"/\\|?*]', '_', filename)

    def download_remaining_season_async(self, season_files: List[str], current_file: str, season_dir: str, window: int = 20):
        entries_map: Dict[int, Tuple[int, str, str]] = {}
        unknowns: List[Tuple[str, str]] = []  # (fname, remote_path) for files without episode number

        for file in season_files:
            fname = self.sanitize_filename(os.path.basename(file))
            s, e, global_e = self.extract_season_episode_global(os.path.basename(file))
            if e is None:
                unknowns.append((fname, file))
                continue
            if e in entries_map:
                # keep first seen for this episode (avoid downloading multiple variants for same epi)
                continue
            entries_map[e] = (e, fname, file)

        # create a sorted list of episodes
        entries = sorted(entries_map.values(), key=lambda x: x[0])

        # locate current index by matching filename
        idx = next((i for i, (_e, fname, _rp) in enumerate(entries) if fname == current_file), None)
        if idx is None:
            # fallback: find index by episode number of current_file
            cur_e = None
            for e, fname, rp in entries:
                if fname == current_file:
                    cur_e = e
                    idx = entries.index((e, fname, rp))
                    break

        if idx is None:
            start, end = 0, min(len(entries)-1, window-1)
        else:
            start = max(0, idx - window)
            end = min(len(entries)-1, idx + window)

        to_download = entries[start:end+1]
        keep_filenames = {fname for _e, fname, _rp in to_download}

        # For safety, also include the current_file in keep_filenames
        keep_filenames.add(current_file)
        # spawn download threads for the window
        for e, fname, remote_path in to_download:
            local_path = os.path.join(season_dir, fname)
            if os.path.exists(local_path):
                continue
            threading.Thread(
                target=self._download_file,
                args=(self._get_raw_url(remote_path), local_path),
                daemon=True
            ).start()

        # optionally consider unknowns (files without parsed episode) only if season_dir is empty
        if not entries and unknowns:
            for fname, remote_path in unknowns[:window]:
                local_path = os.path.join(season_dir, fname)
                if os.path.exists(local_path):
                    continue
                threading.Thread(
                    target=self._download_file,
                    args=(self._get_raw_url(remote_path), local_path),
                    daemon=True
                ).start()

        # optional: evict files outside keep_filenames to limit disk usage
        try:
            self._evict_outside_window(season_dir, keep_filenames)
        except Exception:
            logger.exception("Failed to evict old episode files")

    def _evict_outside_window(self, season_dir: str, keep_filenames: set):
        """
        Remove files in season_dir that are not in keep_filenames.
        Safe-guards: only remove .srt and only when season_dir exists.
        """
        if not season_dir or not os.path.isdir(season_dir):
            return
        for fn in os.listdir(season_dir):
            if not fn.lower().endswith(".srt"):
                continue
            if fn in keep_filenames:
                continue
            try:
                path = os.path.join(season_dir, fn)
                os.remove(path)
                # logger.debug("Evicted old episode file: %s", path)
            except Exception:
                print("fail")
                # logger.exception("Failed to remove cached file: %s", fn)

    def _download_file(self,remote_path, local_path, session=None):
        if not os.path.exists(local_path):
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
        try:
            getter = session.get if session is not None else requests.get
            r = getter(remote_path, timeout=15)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            if "text/html" in content_type.lower():
                raise RuntimeError(f"Downloaded HTML instead of SRT from {remote_path}")
            with open(local_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Download failed for {remote_path}: {e}")
# ---------------------- GitHub searching / downloading ----------------------
#endregion -------------------------remote handling-----------------------------
















