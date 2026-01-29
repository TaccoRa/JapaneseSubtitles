# subtitle_manager.py
import os
import re
import shutil
import requests
import json
import time
import datetime
import atexit
from urllib.parse import urlparse, unquote
from typing import List, Optional, Tuple, Dict
import threading
from collections import defaultdict
import statistics

import regex
import srt
import chardet
import tkinter as tk
from tkinter import font as tkFont
from tkinter import filedialog, messagebox

from model.config_manager import ConfigManager
from utils import format_time


import logging
logger = logging.getLogger(__name__)

'''
First run: depending on remote flag use last used github url or use local path to download current season
during runtime: either episode switch or season switch
e switch: switch_episode(season, episode) look for s(season)e(episode+-1) (depending on switching) 
if this is not in current cache get the new season:
s switch: look for anime name in url, search in github for folders with this name. 
(problem if currently at season 2 and the naming is arbitrary the base name of season two must not be inside name of season three)
(solution: if season 1 save the name of the anime in config and only change if new anime/new season 1 and use this to search the other seasons)
(maybe make an additional check for the least common char in all of the folders found -> should be anime name)
if last episode (not in current cache) look for S(season+1)E1 inside the folders. -> download from the folder
where this srt file is in, the other srt files and safe in cache. (If multiple hits for folder use the first hit)
'''

class SubtitleManager:

    CLEAN_PATTERN = re.compile(r'\{\\an\d+\}')
    TAG_PATTERN = re.compile(r'<[^>]*>')
    SEASON_PATTERN = re.compile(r'S(\d+)', re.IGNORECASE)
    EPISODE_PATTERN = re.compile(r'E(\d+)', re.IGNORECASE)
    RUBY_PATTERN = regex.compile(r'(\p{Han}+)\(([^)]+)\)')

    RESOLUTION_RE = re.compile(r'^\d{3,4}p$', re.IGNORECASE)
    RESOLUTION_X_RE = re.compile(r'^\d{3,4}x\d{3,4}$', re.IGNORECASE)
    VIDEO_CODEC_RE = re.compile(r'^(x265|h264|av1|hevc|x264)$', re.IGNORECASE)
    YEAR_RE = re.compile(r'^(19|20)\d{2}$')

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.github_token = os.environ.get("GITHUB_TOKEN")
        self.i = 1
        self.total_duration = 0
        self.display_data = []
        self.raw_subtitles = None
        self.max_width = None
        self.max_height = None
        self.is_movie = False

        self.local_srt_dir = None
        self.title = None

        self.url = None
        self.srt_file = None
        self.cache_dir = None
        self._remote_files_cache: Dict[int, List[str]] = {}
        self.local_episode_paths = {}
        self.remote_url = ""
        
        # Comprehensive episode map: (season, episode) -> path
        # Built on initialization and used for all navigation
        self.episode_map: Dict[Tuple[int, int], str] = {}
        self.global_episode_map: Dict[int, Tuple[int, int]] = {}  # global_ep -> (season, episode)
        self.cached_folders: List[str] = []  # Cached folder list for the current anime

        # first check if last used remote or not then get path to local or download remote
        # self.remote_flag = self.config.get("REMOTE_FLAG")
        # if self.remote_flag:
        #     local_srt_path = self._initialize_remote_path()
        #     self._register_cache_cleanup()
        # else:
        #     local_srt_path = self.config.get("LAST_LOCAL_SRT_FILE")
        # self._load_local_and_process(local_srt_path)

        self._search_subtitle_folders()

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
        if self.srt_file != self.config.get("LAST_LOCAL_SRT_FILE"):
            self.config.set("LAST_LOCAL_SRT_FILE", self.srt_file)
        if self.anime_folder_name != self.config.get("LAST_ANIME_NAME"):
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
        if self.remote_url != self.config.get("LAST_GITHUB_URL"):
            self.config.set("LAST_GITHUB_URL", self.remote_url)
            
#region --------------------------------local handling-----------------------------------
    def _load_local_and_process(self, local_srt_path: str) -> bool:
        if not (local_srt_path and os.path.isfile(local_srt_path)):
            logger.error("Local SRT path not found: \n%s\n -> Manual selection", local_srt_path)
            local_srt_path = self.ask_local_srt_file()
        self._extract_and_set_local_episode_metadata(local_srt_path)
        self.set_subtitle_display_data(local_srt_path)
        logger.info(f"Loaded subtitle: S{self.current_season}E{self.current_episode} | {local_srt_path}")#what if movie?

# -------------------------helpers-----------------------------
    def _extract_and_set_local_episode_metadata(self, local_path):
        if not self.remote_flag: #hardcoded certain local folder
            self.anime_folder_name = local_path.replace("\\", "/").split("/")[local_path.replace("\\", "/").split("/").index("subs")+1]
            #self.config.set("LAST_LOCAL_SRT_FILE",local_path)
            self.current_season, self.current_episode = self.extract_season_episode(local_path)
        self.is_movie = self.current_season is None and self.current_episode is None
        self.local_srt_dir = os.path.dirname(local_path)
        srt_paths = [os.path.join(self.local_srt_dir, f) for f in os.listdir(self.local_srt_dir) if f.lower().endswith('.srt')]
        if not srt_paths: logger.error("No .srt files found in folder: %s", self.local_srt_dir)
        for srt_path in srt_paths:
            s, e = self.extract_season_episode(srt_path)
            if s is None and e is None: self.local_episode_paths["movie"] = srt_path
            else: self.local_episode_paths[(s,e)] = srt_path

    def set_subtitle_display_data(self, local_path):
        with open(local_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        text = raw.decode(detected['encoding'] or 'utf-8', errors='replace')
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
    def get_episode_metadata(self): return (self.github_owner, self.github_repo, self.github_ref, self.remote_path,
                self.remote_folder,self.anime_folder_name, self.file_name,
                self.current_season, self.current_episode)
    def get_total_duration(self) -> float: return self.subtitles[-1].end.total_seconds()
    def get_current_season(self) -> int: 
        print(self.current_season)
        return self.current_season
    def get_current_episode(self) -> int: return self.current_episode
# ---------------------- get data -------------------------
#endregion ------------------------------local handling-----------------------------------

#region -------------------------episode / season switching-----------------------------
    def change_episode(self, action: str, raw: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
        #all episodes should be either locally saved or the paths to the remote saved in the episode map
        if self.remote_flag:
            current_season, current_episode = self.change_episode_remote(self, action, raw)
        else:
            currentse_season, current_episode = self.change_episode_local(self, action, raw)
        return currentse_season, current_episode


    def change_episode_local(self, action: str, raw: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
        #goal: change episode either with inc, dec, or set. Raw is the wished episode
        # Look for next episode in file_list if it is not there ask the user to save it and press select to select it. 
        # -> the file list is then updated and the new episode is loaded
        s,e = self.extract_season_episode(raw) #does this work if it is just 12 --> E12?
        # if action == "set":#manually written inside the settings episode entry raw only > 0
        #     if e in episode_map: #
        #         ...


        return self.current_season, self.current_episode

    def change_episode_remote(self, action: str, raw: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
        #action: "dec","inc","set"; raw: if user set episode(int); sets new episode and returns target
        cur_season = self.current_season
        cur_episode = self.current_episode
        
        # Initialize variables that may be set in branches
        remote_path = None
        season_files = []
        target_season = cur_season
        target_episode = cur_episode
        
        episodes = []
        for filename in self.local_file_list:
            s, e = self.extract_season_episode(filename)
            if e is not None: episodes.append(e)
        lowest_episode, highest_episode = min(episodes), max(episodes)

        if action == 'dec':#decrease episode if episdoe 1 search for new season (only remote for now)
            if cur_season <= 1:
                if cur_episode <= 1:  # cannot go below S1E1
                    return None, None
                target_episode = cur_episode - 1
            else:
                # go to previous season, prefer cached last-episode if that season is cached
                target_season = cur_season - 1
                lc = self._last_cached(target_season)
                # lc > 0 only when that specific season is cached
                if lc:
                    target_episode = lc
                else:
                    # Not cached: find remote files for previous season (cached per-session)
                    files = self._get_remote_files_for_season(target_season)
                    if not files:
                        # season likely not released, try episode map fallback
                        logger.info("No season %d files found, trying episode map fallback for 'dec'", target_season)
                        result = self._find_next_from_map(cur_season, cur_episode)
                        if result:
                            # For 'dec', we need to go backward, so find_next won't help
                            # Instead, get the map and find the previous episode
                            episode_map, available_episodes = self._build_episode_map_from_log()
                            cur_key = (cur_season, cur_episode)
                            if cur_key in episode_map:
                                try:
                                    idx = available_episodes.index(cur_key)
                                    if idx > 0:
                                        prev_s, prev_e = available_episodes[idx - 1]
                                        remote_path = episode_map[(prev_s, prev_e)]
                                        target_season = prev_s
                                        target_episode = prev_e
                                        folders = self._search_subtitle_folders()
                                        files = self._search_srt_files_in_folders(folders, target_season)
                                        season_files = files if files else []
                                        logger.info("Episode map fallback (dec) found: S%dE%d", target_season, target_episode)
                                    else:
                                        logger.warning("No previous episode available in map")
                                        return cur_season, cur_episode
                                except ValueError:
                                    logger.warning("Current episode not in map")
                                    return cur_season, cur_episode
                            else:
                                logger.warning("Current episode S%dE%d not in map", cur_season, cur_episode)
                                return cur_season, cur_episode
                        else:
                            # season likely not released
                            return cur_season, cur_episode
                    else:
                        # pick file with highest episode number
                        max_e = 0
                        chosen_remote = None
                        for fpath in files:
                            s, e = self.extract_season_episode(os.path.basename(fpath))
                            if e and e > max_e:
                                max_e = e
                                chosen_remote = fpath
                        if not chosen_remote:
                            return cur_season, cur_episode
                        target_episode = max_e
                        remote_path = chosen_remote
                        season_files = files

        elif action == 'inc': #increase episode if end of season search for new season (only remote for now)
            # check in-cache
            lc = self._last_cached(cur_season)
            if cur_episode + 1 <= lc:
                target_episode = cur_episode + 1
                target_season = cur_season
            else:
                # try next season (remote or cached)
                target_season = cur_season + 1
                lc_next = self._last_cached(target_season)
                if lc_next:
                    # if cached and contains ep1, use that
                    if 1 in self.local_file_list:
                        target_episode = 1
                    else:
                        # cached but doesn't have ep1, find the lowest episode
                        eps = self.local_file_list
                        target_episode = min(eps) if eps else 1
                else:
                    # not cached, search remote for season+1
                    folders = self._search_subtitle_folders()
                    files = self._search_srt_files_in_folders(folders, target_season)
                    if files:
                        # choose ep1 if present, else smallest episode
                        chosen_remote = None
                        min_e = None
                        for fpath in files:
                            s, e = self.extract_season_episode(os.path.basename(fpath))
                            if e is None:
                                continue
                            if min_e is None or e < min_e:
                                min_e = e
                                chosen_remote = fpath
                        if chosen_remote:
                            target_episode = min_e
                            remote_path = chosen_remote
                            season_files = files
                    else:
                        # search failed, try episode map fallback
                        logger.info("No season %d files found, trying episode map fallback", target_season)
                        result = self._find_next_from_map(cur_season, cur_episode)
                        if result:
                            target_season, target_episode, remote_path = result
                            folders = self._search_subtitle_folders()
                            files = self._search_srt_files_in_folders(folders, target_season)
                            season_files = files if files else []
                            logger.info("Episode map fallback found: S%dE%d", target_season, target_episode)
                        else:
                            # fallback also failed
                            logger.warning("No next episode available (season %d not found, map fallback failed)", target_season)
                            return None, None

        elif action == 'set': #manually written inside the settings episode entry raw only > 0
            lc = self._last_cached(cur_season) #change if specific episode is wished
            if raw > lc:
                logger.warning("Episode %d is beyond cached episodes for season %d (max: %d)", raw, cur_season, lc)
                return None, None
            target_season = cur_season
            target_episode = raw
        else:
            # unknown action
            return cur_season, cur_episode

        # If the target is the same as current and it exists cached, do nothing
        if target_season == self.current_season and target_episode == self.current_episode:
            return self.current_season, self.current_episode

        # Try to load from cache if available and we don't already have a remote_path
        if remote_path is None:
            # If we're in local mode, look in the local folder; otherwise look in cache_github
            if not self.remote_flag and self.local_srt_dir:
                season_dir = self.local_srt_dir
            else:
                season_dir = self._season_cache_dir(target_season)  #Should not be needed because after download the local dir should be updated
           
           # find matching file in season_dir
            if season_dir and os.path.isdir(season_dir):
                for fn in os.listdir(season_dir):
                    if not fn.lower().endswith(".srt"):
                        continue
                    s, e = self.extract_season_episode(fn)
                    if e == target_episode:
                        chosen = os.path.join(season_dir, fn)
                        try:
                            self._load_local_and_process(chosen)
                            # Extract actual season/episode from the cached file
                            actual_s, actual_e = self.extract_season_episode(fn)
                            if actual_s and actual_e:
                                self.current_season = actual_s
                                self.current_episode = actual_e
                                logger.info("Cache hit for S%dE%d (requested S%dE%d)", actual_s, actual_e, target_season, target_episode)
                            else:
                                # Fallback if parsing failed
                                self.current_season = target_season
                                self.current_episode = target_episode
                            self.season_dir = season_dir
                            self.srt_file = chosen
                            return self.current_season, self.current_episode
                        except Exception:
                            # logger.exception("Failed to load cached subtitle: %s", chosen)
                            break  # fall back to remote if available

        # If we reach here we need to fetch remote_path (either was found above or we need to locate it)
        if remote_path is None:
            # Find remote path for target season/episode
            logger.info(f"Searching for S{target_season}E{target_episode} (current: S{cur_season}E{cur_episode}, action: {action})")
            folders = self._search_subtitle_folders()
            logger.info(f"Found {len(folders)} folders to search")
            files = self._search_srt_files_in_folders(folders, target_season)
            logger.info(f"Found {len(files)} files for season {target_season}")
            if not files:
                logger.warning("No remote files found for season %s", target_season)
                # Try episode map fallback when no season files found
                if action == 'inc':
                    result = self._find_next_from_map(cur_season, cur_episode)
                    if result:
                        target_season, target_episode, remote_path = result
                        folders = self._search_subtitle_folders()
                        files = self._search_srt_files_in_folders(folders, target_season)
                        season_files = files if files else []
                        logger.info("Episode map fallback found: S%dE%d", target_season, target_episode)
                    else:
                        return None, None
                else:
                    return None, None
            
            # find file matching target_episode
            chosen_remote = None
            for fpath in files:
                s, e = self.extract_season_episode(os.path.basename(fpath))
                if e == target_episode:
                    chosen_remote = fpath
                    break
            
            if chosen_remote is None:
                # Exact episode not found
                if action == 'inc':
                    # For 'inc' action, don't pick max - use episode map fallback instead
                    logger.info("Episode S%dE%d not found in remote files, trying episode map fallback", target_season, target_episode)
                    result = self._find_next_from_map(cur_season, cur_episode)
                    if result:
                        target_season, target_episode, remote_path = result
                        folders = self._search_subtitle_folders()
                        files = self._search_srt_files_in_folders(folders, target_season)
                        season_files = files if files else []
                        logger.info("Episode map fallback found: S%dE%d", target_season, target_episode)
                        # Need to find this episode in the new files list
                        for fpath in files:
                            s, e = self.extract_season_episode(os.path.basename(fpath))
                            if e == target_episode:
                                chosen_remote = fpath
                                break
                    if not chosen_remote:
                        logger.warning("No next episode available via map fallback")
                        return None, None
                else:
                    # For 'set' or 'dec' action, pick closest available (legacy behavior)
                    max_e = 0
                    for fpath in files:
                        s, e = self.extract_season_episode(os.path.basename(fpath))
                        if e and e > max_e:
                            max_e = e
                            chosen_remote = fpath
                    if chosen_remote is None:
                        logger.warning("No matching remote file found for episode %s", target_episode)
                        return None, None
            remote_path = chosen_remote
            season_files = files

        # download the remote_path into season cache
        # Only create directory after confirming we have a valid remote_path with files
        season_dir = self._season_cache_dir(target_season, create=True)  # Explicit create=True here
        filename = self.sanitize_filename(os.path.basename(remote_path))
        local_path = os.path.join(season_dir, filename)

        # blocking download of the required episode (so UI can show it)
        try:
            raw_url = self._get_raw_url(remote_path)
            self._download_file(raw_url, local_path)
            # verify file exists
            # if not os.path.isfile(local_path):
            #     logger.error("Downloaded file missing: %s", local_path)
            #     return cur_season, cur_episode
            # load and update state
            self._load_local_and_process(local_path)
            # Extract actual season/episode from the file we loaded to avoid display mismatch
            actual_s, actual_e = self.extract_season_episode(os.path.basename(local_path))
            if actual_s and actual_e:
                self.current_season = actual_s
                self.current_episode = actual_e
                logger.info("Remote download loaded: S%dE%d (requested S%dE%d)", actual_s, actual_e, target_season, target_episode)
            else:
                # Fallback if parsing failed
                self.current_season = target_season
                self.current_episode = target_episode
                logger.warning("Could not extract actual episode from %s, using target S%dE%d", os.path.basename(local_path), target_season, target_episode)
            self.season_dir = season_dir
            self.srt_file = local_path
            s_num, e_num = self.extract_season_episode(os.path.basename(remote_path))
            if s_num == 1:
                # Extract anime folder name from the remote path (may include suffixes)
                extracted = self._extract_anime_name_from_url(remote_path)
                if extracted:
                    # strip trailing Roman numerals or "Season N" suffixes to get base name
                    cleaned = re.sub(r'\s+(?:Season\s*\d+|\bI{1,3}\b|I{1,3}V?I{0,3})\s*$', '', extracted, flags=re.IGNORECASE).strip()
                    if cleaned:
                        self.anime_folder_name = cleaned
                        self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
        except Exception:
            logger.exception("Failed to download or load remote episode: %s", remote_path)
            return None, None

        # kick off background downloads for remaining season files (if we have file list)
        try:
            if season_files:
                # convert remote paths to filenames to tell the async downloader which is current
                current_file = filename
                # Larger window for initial season download (50 episodes), smaller for mid-season (15)
                window_size = 50 if not self.local_file_list else 15
                self.download_remaining_season_async(season_files, current_file, season_dir, window=window_size)
        except Exception:
            logger.exception("Failed to start async season download")


        # update: new github url to current episode to config, update github metadata?, ...
        return self.current_season, self.current_episode
    
    def set_new_file(self):
        popup = tk.Toplevel()
        popup.title("Choose Source")
        popup.attributes("-topmost", True)
        popup.grab_set()
        w,h = 290,120
        popup.update_idletasks()
        sw, sh = popup.winfo_screenwidth(), popup.winfo_screenheight()
        x,y = (sw - w) // 2, (sh - h) // 2
        popup.geometry(f"{w}x{h}+{x}+{y}")

        tk.Label(popup, text="Select source for subtitle file:", font=("Arial", 12)).pack(pady=(12, 8))
        button_frame = tk.Frame(popup)
        button_frame.pack(pady=8)

        def choose_local():
            popup.destroy()
            path = self.ask_local_srt_file()
            if not path:
                return
            self._load_local_and_process(path)

        def choose_remote():
            popup.destroy()
            url, season_hint, episode_hint = self.ask_remote_srt_with_hint()
            if not url:
                return
            success = self.load_remote_srt_url(url, s_e=(season_hint, episode_hint))
            if not success:
                messagebox.showerror("Load failed", "Failed to load subtitle from the provided URL.")
        tk.Button(button_frame, text="Local File", width=15, command=choose_local).grid(row=0, column=0, padx=12)
        tk.Button(button_frame, text="Remote URL", width=15, command=choose_remote).grid(row=0, column=1, padx=12)
        popup.wait_window(popup)

    def ask_remote_srt_with_hint(self) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        result = {"url": None, "season": None, "episode": None}
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
            s,e = self.extract_season_episode(se_entry.get().strip().lower())
            result["url"], result["season"], result["episode"] = (u or None, s, e)
            dlg.destroy()
        def on_cancel():
            dlg.destroy()
        tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

        dlg.wait_window(dlg)
        return result["url"], result["season"], result["episode"]
    
    def ask_local_srt_file(self) -> Optional[str]:
        try:
            window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                parent=window,
                title="Select SRT File",
                initialdir=self.local_srt_dir,
                filetypes=[("SubRip files","*.srt"),("All Files","*.*")]
            )
            window.destroy()
            if not path: 
                return None
            self.config.set('LAST_LOCAL_SRT_FILE',path)
            return path
        except Exception:
            logger.exception("SRT file selection failed")
            return None

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
        total_width  = max_width + 2 * pad_x

        return (total_width, total_height)
#endregion -------------------------episode / season switching-----------------------------


################ TODO: figure out the anime name of first season #################
##### workaround user gives always s1 when pasting URL##########
#look for same string in folder name and file name? Could work but not everytime


#region -------------------------remote handling-----------------------------
    def _initialize_remote_path(self):
        #extract github metadata
        init_url = self.config.get("LAST_GITHUB_URL")
        if not init_url:#fallback
            init_url = self.ask_remote_srt_with_hint()
            #or ask the name of the anime and which season and/or episode
            if not init_url:
                local_srt_path = self.ask_local_srt_file()
                if local_srt_path:
                    return local_srt_path
        self._extract_and_set_remote_episode_metadata(init_url)
        
        #create episode map
        # self.create_episode_map()
        
        create_season_dir = self._season_cache_dir()
        self.file_name = os.path.basename(self.remote_path) #i dont think self.file_name is needed change later
        local_srt_path = os.path.join(create_season_dir, self.file_name)
        self._download_file(self._get_raw_url(self.remote_path), local_srt_path)
        #download other files later in app.py
        self._extract_and_set_local_episode_metadata(local_srt_path)
        return local_srt_path
    
    def _extract_and_set_remote_episode_metadata(self, remote_url):
        self.config.set("LAST_GITHUB_URL", remote_url)
        github_dict = self._parse_github_url(remote_url)
        self.github_owner = github_dict["owner"]
        self.github_repo  = github_dict["repo"]
        self.github_ref   = github_dict["ref"]
        self.remote_path = github_dict["path"]
        self.remote_folder = os.path.dirname(self.remote_path)
        self.current_season, self.current_episode = self.extract_season_episode(self.remote_path)
        self.anime_folder_name = self.config.get("LAST_ANIME_NAME")
        url_anime_name = self._extract_anime_name_from_url(self.remote_path)
        if self.current_season == 1: #what if no seasons? change later doesnt make too much sense dont know how to do it. save last used github url will this be always s1? ...
            self.anime_folder_name = url_anime_name
            self.config.set("LAST_ANIME_NAME", url_anime_name)

        # Build comprehensive episode map for this anime
        logger.info(f"Building episode map for {self.anime_folder_name}...")
        print(self.i)
        self.i += 1
        try:
            self._build_comprehensive_episode_map()
        except Exception as ex:
            logger.exception("Failed to build comprehensive episode map")


    def _create_episode_map(self, tries):
        logger.info(f"Building comprehensive episode map for {self.anime_folder_name}")
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
        season = 1
        while True:
            if tries == 1:
                search_query = f"{self.anime_folder_name} s{season:02d} Netflix"
            elif tries == 2:
                search_query = f"{self.anime_folder_name} s{season:02d} Amazon"
            elif tries == 3:
                search_query = f"{self.anime_folder_name} s{season:02d}"
            #if 0 hits try amazon instead of netflix then without both and so on can add more fallbacks later
            q = (
                f'repo:{self.github_owner}/{self.github_repo}'# in:path {self.anime_folder_name}' #important " " at the end if not in:path used
                f' path:subtitles/anime_tv extension:srt in:path {search_query}'
            )
            params = {"q": q, "per_page": per_page} 
            page = 1

            while True:
                params["page"] = page
                try:
                    resp = session.get(api_url, params=params, timeout=15)
                except requests.RequestException as e:# network error: stop and return what we have
                    stop_reason = f"network error: {e}"
                    print("Network error during GitHub request, returning partial results:", e)
                    break
                # capture and print rate-limit headers for diagnostics
                hdr = resp.headers
                last_rate_info = _print_rate_info(hdr)
                rem = last_rate_info.get("remaining")
                try:
                    if rem is not None and int(rem) <= 0:
                        _wait_until_reset(last_rate_info)# after waiting, retry same page
                        continue
                except ValueError:# ignore parsing error and proceed
                    pass
                # Successful response
                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, list):
                        continue
                    items = data.get("items", [])
                    if not items and page == 1: #try again with different search query
                        tries += 1
                        self._create_episode_map(tries = tries)
                        print(f"GitHub search returned 0 items for query: {q}")
                    for it in items:
                        if it.get("type") != "file": #really needed?
                            continue
                        name = it.get("name", "")
                        if not name.lower().endswith('.srt'): #really needed?
                            continue
                        s, e, global_e = self.extract_season_episode(name)

                        all_results_items.append({
                            "name": it.get("name"),
                            "path": it.get("path"),
                            "html_url": it.get("html_url"),
                        })
                    # stop when fewer than per_page items returned (no more pages)
                    if len(items) < per_page:
                        break
                    page += 1
                    # polite short sleep to avoid bursting
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
                    break
                raise RuntimeError(f"GitHub search failed: {resp.status_code}, {resp.text}")

            season += 1
            break

        return

 
    def _search_subtitle_folders(self, timeout: int = 15, stop_when_remaining: int = 0,) -> List[str]:

        init_url = self.config.get("LAST_GITHUB_URL")
        github_dict = self._parse_github_url(init_url)
        self.github_owner = github_dict["owner"]
        self.github_repo  = github_dict["repo"]
        # self.anime_folder_name = self.config.get("LAST_ANIME_NAME")
        self.anime_folder_name = "one piece e7 netflix" #for debugging
        per_page = 100

        api_url = "https://api.github.com/search/code"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.github_token}",
            "User-Agent": "subtitle-searcher",
        }

        q = (
            f'repo:{self.github_owner}/{self.github_repo}'# in:path {self.anime_folder_name}' #important " " at the end if not in:path used
            f' path:subtitles/anime_tv extension:srt in:path {self.anime_folder_name}'
        )
        params = {"q": q, "per_page": per_page}
        results_items: List[Dict] = []

        session = requests.Session()
        session.headers.update(headers)

        page = 1
        stop_reason = None
        last_rate_info = {}

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

        while True:
            params["page"] = page
            try:
                resp = session.get(api_url, params=params, timeout=timeout)
            except requests.RequestException as e:# network error: stop and return what we have
                stop_reason = f"network error: {e}"
                print("Network error during GitHub request, returning partial results:", e)
                break
            # capture and print rate-limit headers for diagnostics
            hdr = resp.headers
            last_rate_info = _print_rate_info(hdr)

            # If remaining header present and <= stop_when_remaining, wait then retry same page
            rem = last_rate_info.get("remaining")
            try:
                if rem is not None and int(rem) <= stop_when_remaining:
                    _wait_until_reset(last_rate_info)# after waiting, retry same page
                    continue
            except ValueError:# ignore parsing error and proceed
                pass
            # Successful response
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if not items and page == 1:
                    print(f"GitHub search returned 0 items for query: {q}")
                for it in items:
                    results_items.append({
                        "name": it.get("name"),
                        "path": it.get("path"),
                        "html_url": it.get("html_url"),
                    })
                # stop when fewer than per_page items returned (no more pages)
                if len(items) < per_page:
                    break
                page += 1
                # polite short sleep to avoid bursting
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
                break
            raise RuntimeError(f"GitHub search failed: {resp.status_code}, {resp.text}")

        # Extract parent folder paths and dedupe while preserving order
        seen = set()
        unique_folders: List[str] = []
        for it in results_items:
            path = it.get("path", "")
            if not path:
                continue
            folder = os.path.dirname(path).strip("/")
            if folder and folder.lower() not in seen:
                seen.add(folder.lower())
                unique_folders.append(folder)

        # Print results
        if unique_folders:
            print(f"Found {len(unique_folders)} unique folder(s):")
            for f in unique_folders:
                print(" -", f)
        else:
            print("No matching .srt files found with that query.")

        # diagnostics JSON
        if True and unique_folders:
            safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (self.anime_folder_name or ""))[:200] or "result"
            json_path = os.path.join(os.getcwd(), f"github_search_{safe_name}.json")
            payload = {
                "query": q,
                "repo": f"{self.github_owner}/{self.github_repo}",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "stop_reason": stop_reason,
                "rate_info": last_rate_info,
                "result_count": len(results_items),
                "items": results_items,
            }
            try:
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                print(f"Wrote diagnostics to {json_path}")
            except Exception as e:
                print("Failed to write diagnostics JSON:", e)

        return unique_folders




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

    def load_remote_srt_url(self, url: str, s_e: Optional[Tuple[Optional[int], Optional[int]]] = None) -> bool:
        """
        url: GitHub (or raw) URL pointing to a subtitle file. 
        wished episode: (season, episode) provided by user dialog — can be (None, None) to fall back to parsed values.
        """
        if not url:
            return False
        self.remote_flag = True
        self._extract_and_set_remote_episode_metadata(url)


        #########     workaround      #########
        # Prefer user hint if provided
        wished_season, wished_episode = (None, None)
        if s_e:
            wished_season, wished_episode = s_e
        else:
            wished_season, wished_episode = self.extract_season_episode(url)
        
        # Find candidate subtitle folders for this anime
        candidate_folders = self._search_subtitle_folders()

        chosen_folder = None
        chosen_remote = None
        season_files = []

        # Search candidate folders for the requested season/episode (strong preference)
        files = self._search_srt_files_in_folders(candidate_folders, season=wished_season)
        # find file matching requested episode
        wished_episode_file = None
        for f in files:#find wished episode
            s, e = self.extract_season_episode(os.path.basename(f))
            if e == wished_episode:
                wished_episode_file = f
                break

        if wished_episode_file is None:
            logger.error("Could not locate remote file for season %s episode %s", wished_season, wished_episode)
            return False

        self.current_season, self.current_episode = self.extract_season_episode(wished_episode_file)
        self.srt_file = self.download_current_episode(wished_episode_file)
        try:
            self._load_local_and_process(self.srt_file)
        except Exception:
            # logger.exception("Failed to load downloaded subtitle: %s", self.srt_file)
            return False

        # Start background download of the rest of the season from the chosen folder (if not already cached)
        try:
            if season_files:
                current_file = os.path.basename(self.srt_file)
                self.download_remaining_season_async(season_files, self.sanitize_filename(current_file), self.season_dir, window=15)
        except Exception:
            logger.exception("Failed to schedule async season downloads")
        return True
    

# -------------------------Episode navigation map (comprehensive)-------------------------
    def _build_comprehensive_episode_map(self) -> None:
        """
        Build a complete episode map from ALL files across all seasons.
        This includes:
        - episode_map: (season, episode) -> remote_path (for SxxExx format)
        - global_episode_map: global_episode -> (season, episode) (for global numbering)
        - Printed to comprehensive_episodes.txt for reference
        
        This should be called once during initialization to build a stable map for navigation.
        """
        logger.info("Building comprehensive episode map from all files...")
        
        # Search ALL folders and ALL files WITHOUT season filter
        folders = self._search_subtitle_folders()
        # logger.info(f"Found {len(folders)} subtitle folders for {self.anime_folder_name}:")
        # for folder in folders:
        #     logger.info(f"  - {folder}")
        self.cached_folders = folders
        
        headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {self.github_token}"}
        all_files_data: List[Dict] = []  # (season, episode, global_ep, path)
        
        # Search all files in all folders (no season filter)
        for folder in folders:
            url = f"https://api.github.com/repos/{self.github_owner}/{self.github_repo}/contents/{folder}"
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    continue
                items = resp.json()
                if not isinstance(items, list):
                    continue
                for it in items:
                    if it.get("type") != "file":
                        continue
                    name = it.get("name", "")
                    if not name.lower().endswith('.srt'):
                        continue
                    
                    path = it.get("path")
                    s, e = self.extract_season_episode(name)
                    
                    # Extract global episode if present (in parentheses)
                    global_ep = None
                    m = re.search(r'\((\d+)\)', name)
                    if m:
                        global_ep = int(m.group(1))
                    
                    all_files_data.append({
                        'name': name,
                        'path': path,
                        'season': s,
                        'episode': e,
                        'global_episode': global_ep
                    })
                    
                    # Build maps from this file
                    if s and e:
                        self.episode_map[(s, e)] = path
                    if global_ep and s and e:
                        self.global_episode_map[global_ep] = (s, e)
            except Exception as ex:
                logger.exception(f"Failed to search folder {folder}")
        
        logger.info(f"Built episode map with {len(self.episode_map)} SxxExx entries and {len(self.global_episode_map)} global episode mappings")
        
        # Log comprehensive map for debugging
        self._log_comprehensive_episodes(all_files_data)
    
    def _log_comprehensive_episodes(self, files_data: List[Dict]) -> None:
        """Log all discovered episodes to comprehensive_episodes.txt for reference."""
        try:
            log_file = os.path.join(self._get_cache_base_dir(), "comprehensive_episodes.txt")
            timestamp = __import__('datetime').datetime.now().isoformat()
            
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write("=" * 100 + "\n")
                f.write("COMPREHENSIVE EPISODE MAP\n")
                f.write("=" * 100 + "\n")
                f.write(f"Time: {timestamp}\n")
                f.write(f"Anime: {self.anime_folder_name}\n")
                f.write(f"Total files found: {len(files_data)}\n")
                f.write("=" * 100 + "\n\n")
                
                # Group by season
                by_season = {}
                for file_info in files_data:
                    s = file_info['season']
                    if s not in by_season:
                        by_season[s] = []
                    by_season[s].append(file_info)
                
                # Print by season (None first, then sorted integers)
                sorted_seasons = sorted([k for k in by_season.keys() if k is not None])
                if None in by_season:
                    sorted_seasons = [None] + sorted_seasons
                
                for season in sorted_seasons:
                    if season is None:
                        continue  # Handle separately below
                    files_in_season = by_season[season]
                    f.write(f"\n{'─' * 100}\n")
                    f.write(f"SEASON {season} ({len(files_in_season)} files)\n")
                    f.write(f"{'─' * 100}\n")
                    for file_info in sorted(files_in_season, key=lambda x: x['episode'] if x['episode'] else 9999):
                        s, e = file_info['season'], file_info['episode']
                        g = file_info['global_episode']
                        path = file_info['path']
                        name = file_info['name']
                        global_str = f" [Global: {g}]" if g else ""
                        f.write(f"S{s:02d}E{e:02d}{global_str:20s} | {name}\n")
                        f.write(f"                        | {path}\n\n")
                
                # Print files without season number
                no_season = [f for f in files_data if f['season'] is None]
                if no_season:
                    f.write(f"\n{'─' * 100}\n")
                    f.write(f"FILES WITHOUT SEASON NUMBER ({len(no_season)} files)\n")
                    f.write(f"{'─' * 100}\n")
                    for file_info in no_season:
                        e = file_info['episode']
                        g = file_info['global_episode']
                        path = file_info['path']
                        name = file_info['name']
                        ep_str = f"E{e}" if e else "???"
                        global_str = f" [Global: {g}]" if g else ""
                        f.write(f"{ep_str:8s}{global_str:20s} | {name}\n")
                        f.write(f"                        | {path}\n\n")
                
                # Print global episode map
                if self.global_episode_map:
                    f.write(f"\n{'─' * 100}\n")
                    f.write(f"GLOBAL EPISODE MAPPING\n")
                    f.write(f"{'─' * 100}\n")
                    for global_ep in sorted(self.global_episode_map.keys()):
                        s, e = self.global_episode_map[global_ep]
                        f.write(f"Global E{global_ep} → S{s}E{e}\n")
        except Exception as e:
            logger.exception("Failed to log comprehensive episodes")

# -------------------------Episode navigation map (from found_srt_files.txt)-------------------------
    def _build_episode_map_from_log(self) -> Tuple[Dict[Tuple[int, int], str], List[Tuple[int, int]]]:
        """
        Parse found_srt_files.txt and build:
        1. episode_map: (season, episode) -> remote_path for MATCHED files only
        2. available_episodes: sorted list of (season, episode) tuples available
        Returns: (episode_map, available_episodes)
        """
        episode_map = {}
        available_episodes = []
        log_file = os.path.join(self._get_cache_base_dir(), "found_srt_files.txt")
        
        if not os.path.exists(log_file):
            return episode_map, available_episodes
        
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            i = 0
            while i < len(lines):
                line = lines[i]
                # Look for lines with MATCHED files (format: "✓ MATCHED | SxEy | filename")
                if '✓ MATCHED' in line and '|' in line:
                    # Extract SxEy from the line
                    m = re.search(r'S(\d+)E(\d+)', line)
                    if m:
                        season, episode = int(m.group(1)), int(m.group(2))
                        # Look at next line for the path
                        if i + 1 < len(lines):
                            path_line = lines[i + 1]
                            if 'Path:' in path_line:
                                path = path_line.split('Path:')[1].strip()
                                episode_map[(season, episode)] = path
                                available_episodes.append((season, episode))
                i += 1
        except Exception as e:
            logger.debug("Failed to parse episode map from log: %s", e)
        
        # Sort by season then episode
        available_episodes.sort()
        return episode_map, available_episodes
    
    def _find_next_from_map(self, current_season: int, current_episode: int) -> Optional[Tuple[int, int, str]]:
        """
        Find next available episode from the comprehensive episode map.
        Returns: (season, episode, remote_path) or None
        """
        if not self.episode_map:
            logger.warning("Episode map not built, cannot find next episode")
            return None
        
        # Get all episodes sorted
        available_eps = sorted(self.episode_map.keys())
        if not available_eps:
            return None
        
        # Find current episode in the list
        current_key = (current_season, current_episode)
        current_idx = None
        try:
            current_idx = available_eps.index(current_key)
        except ValueError:
            # Current episode not in map, try to find next one anyway
            for i, (s, e) in enumerate(available_eps):
                if s > current_season or (s == current_season and e > current_episode):
                    current_idx = i - 1  # Start from previous so we get the next one
                    break
        
        # Return next episode if available
        if current_idx is not None and current_idx + 1 < len(available_eps):
            next_s, next_e = available_eps[current_idx + 1]
            next_path = self.episode_map[(next_s, next_e)]
            logger.info(f"Found next episode from map: S{next_s}E{next_e}")
            return next_s, next_e, next_path
        
        return None
        
        return None

# ---------------------- helpers: cache dirs ----------------------base
    def _season_cache_dir(self, season: Optional[int] = None) -> str:
        base = self._get_cache_base_dir()
        season_num = season if season is not None else self.current_season
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
    def normalize_name(name: str) -> str:
        # remove group-tags in [] and {} but keep parentheses (they often contain a useful number)
        s = re.sub(r'\[.*?\]', ' ', name)
        s = re.sub(r'\{.*?\}', ' ', s)
        s = s.replace('\\', '/').split('/')[-1]
        return s.strip()
    
    def infer_season_offsets(parsed_files: List[Dict]) -> Dict[int, int]:
        # parsed_files entries have keys: season, episode, global
        per_season_offsets = defaultdict(list)
        for p in parsed_files:
            s = p.get("season"); e = p.get("episode"); g = p.get("global")
            if s is not None and e is not None and g is not None:
                per_season_offsets[s].append(g - e)
        season_offsets = {}
        for s, offs in per_season_offsets.items():
            if not offs:
                continue
            # require some agreement: use median and ensure spread small
            med = int(statistics.median(offs))
            if max(offs) - min(offs) <= 2:  # allow small noise
                season_offsets[s] = med
            else:
                # ambiguous: choose median but mark ambiguous (you can log)
                season_offsets[s] = med
        return season_offsets

    def apply_offsets(parsed_files, season_offsets):
        # Build maps
        episode_map = {}        # (s,e) -> path
        global_episode_map = {} # g -> (s,e)
        for p in parsed_files:
            s, e, g, path = p.get("season"), p.get("episode"), p.get("global"), p.get("path")
            if s is not None and e is not None:
                episode_map[(s, e)] = path
                if g is not None:
                    global_episode_map[g] = (s, e)
            elif g is not None:
                # try infer season from offsets
                for s_idx, offset in season_offsets.items():
                    candidate_e = g - offset
                    if candidate_e > 0:
                        # optionally validate candidate exists or is reasonable
                        global_episode_map[g] = (s_idx, candidate_e)
                        break
        return episode_map, global_episode_map

    def extract_season_episode_global(name: str) -> Tuple[Optional[int], Optional[int], Optional[int], float, Dict]:
        """
        Return (season, episode, global_episode, confidence_score 0..1, info_dict)
        """
        raw = name
        sname = normalize_name(raw)
        info = {"normalized": sname}
        
        # parenthesized numbers and positions
        par_iter = list(re.finditer(r'\(\s*(\d{1,4})\s*\)', sname))
        par_nums = [int(m.group(1)) for m in par_iter]
        par_positions = [m.start() for m in par_iter]
        info["par_nums"] = par_nums
        
        # explicit "Global: N" marker
        gm = re.search(r'Global[:\s]*#?\s*(\d{1,4})', sname, re.IGNORECASE)
        if gm:
            info["global_from_marker"] = int(gm.group(1))
        
        # 1) SxxExx (highest priority)
        m_s_ex = re.search(r'(?i)\bS(\d{1,2})\D*[eE](\d{1,4})\b', sname)
        if m_s_ex:
            s = int(m_s_ex.group(1)); e = int(m_s_ex.group(2))
            info["found_s_e"] = (s, e)
            # parenthesized number after SxxExx is likely global
            global_candidate = None
            if par_iter:
                for m in par_iter:
                    if m.start() >= m_s_ex.end():
                        global_candidate = int(m.group(1)); break
            jp = re.search(r'第\s*(\d{1,4})\s*話', sname)
            if jp:
                return s, e, int(jp.group(1)), 0.995, info
            if "global_from_marker" in info:
                return s, e, info["global_from_marker"], 0.995, info
            if global_candidate:
                return s, e, global_candidate, 0.98, info
            return s, e, None, 0.9, info
        
        # 2) Sx - yy pattern
        m_s_dash = re.search(r'(?i)\bS(\d{1,2})\s*[-:]\s*(\d{1,4})\b', sname)
        if m_s_dash:
            s = int(m_s_dash.group(1)); e = int(m_s_dash.group(2))
            info["found_s_e_dash"] = (s, e)
            global_candidate = None
            for m in par_iter:
                if m.start() >= m_s_dash.end():
                    global_candidate = int(m.group(1)); break
            if global_candidate:
                return s, e, global_candidate, 0.95, info
            if "global_from_marker" in info:
                return s, e, info["global_from_marker"], 0.95, info
            return s, e, None, 0.85, info
        
        # 3) Japanese global marker (第NNN話)
        jp = re.search(r'第\s*(\d{1,4})\s*話', sname)
        if jp:
            return None, None, int(jp.group(1)), 1.0, info
        
        # 4) hyphen-number then "(" or hyphen-number hyphen -> global candidate
        m_hy_par = re.search(r'[-_]\s*(\d{1,4})\s*(?:\(|[-_])', sname)
        if m_hy_par:
            num = int(m_hy_par.group(1))
            if not YEAR_RE.match(str(num)):
                return None, None, num, 0.9, info
        
        # 5) E### token without Sxx -> likely global
        m_e = re.search(r'(?i)(?:\b|^)[eE](\d{1,4})(?:\b|$)', sname)
        if m_e:
            num = int(m_e.group(1))
            if num < 5000:
                if "global_from_marker" in info:
                    return None, None, info["global_from_marker"], 0.95, info
                return None, None, num, 0.7, info
        
        # 6) duplicate marker like "095(1)" -> return main number as global
        m_dup = re.search(r'(\d{1,4})\(\s*1\s*\)', sname)
        if m_dup:
            num = int(m_dup.group(1))
            return None, None, num, 0.8, info
        
        # 7) fallback: last numeric token that is not noise
        tokens = re.split(r'[.\s_\-()\[\]]+', sname)
        tokens = [t for t in tokens if t]
        nums = []
        for t in tokens:
            if t.isdigit() and not is_noise_token(t):
                nums.append(int(t))
        if nums:
            if par_nums:
                # prefer a paren number if it isn't clearly a duplicate marker
                for i, n in enumerate(par_nums):
                    par_pos = par_positions[i]
                    before = sname[:par_pos].rstrip()
                    if before and before[-1].isdigit() and n <= 3:
                        continue
                    return None, None, n, 0.85, info
            return None, None, nums[-1], 0.5, info
        
        return None, None, None, 0.0, info

    def extract_season_episode(self, name: str) -> Tuple[Optional[int], Optional[int]]:
        # 1) Japanese format: シーズン1-10- (highest priority)
        m = re.search(r'シーズン\s*(\d{1,2})\s*[-_]\s*(\d{1,4})\s*[-_]', name)
        if m:
            return int(m.group(1)), int(m.group(2))
        
        # 2) English "Season X - Y" pattern (high priority to avoid picking up numbers)
        m = re.search(r'(?i)season\s+(\d{1,2})\s*[-:]\s*(\d{1,4})', name)
        if m:
            return int(m.group(1)), int(m.group(2))
        
        # 3) "SX - Y" or "SXY" with optional E: [Judas] Shingeki no Kyojin S3 - 19.srt
        # Also matches with underscore: Shingeki_no_Kyojin_S3 (38).srt
        m = re.search(r'[_\s]S(\d{1,2})\s*[-:\s]*E?(\d{1,4})', name, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2))
        
        # 4) "SX (Y)" pattern with optional global: Shingeki_no_Kyojin_S3 (38)(1).srt or S3E01 (38)
        # Match S# followed by numbers in parentheses (can be multiple)
        m = re.search(r'\bS(\d{1,2})(?:\D*E)?\s*(\d{1,4})\s*(?:\(\d+\))*', name, re.IGNORECASE)
        if m:
            return int(m.group(1)), int(m.group(2))

        # 5) Standard SxxExx format
        m = re.search(r'(?i)[sS](\d{1,2})\D*[eE](\d{1,4})', name)
        if m:
            return int(m.group(1)), int(m.group(2))

        # 6) Japanese style "進撃の巨人 - 01 -" (Judas format, just episode)
        m = re.search(r'\s*-\s*(\d{1,4})\s*-', name)
        if m:
            return None, int(m.group(1))

        # 7) Exx (episode only, no season)
        m = re.search(r'(?i)\b[eE](\d{1,4})\b', name)
        if m:
            return None, int(m.group(1))

        # 8) After dash with season context
        m = re.search(r'シーズン|S\d+.*?-\s*(\d{1,4})\s*-', name, re.IGNORECASE)
        if m:
            return None, int(m.group(1))

        # 9) LAST number (but NOT if followed by 'p' like "720p", "1080p")
        # Avoid matching resolution numbers
        nums = re.findall(r'(?<!\d)(\d{2,4})(?![p\d])', name)
        if nums:
            ep = int(nums[-1])
            if 1500 <= ep <= 2100:  # year
                return None, None
            return None, ep
        return None, None

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
    def _get_remote_files_for_season(self, season: int) -> List[str]:
        """
        Return list of remote file paths for a season (cached in-memory per run).
        Caches results in self._remote_files_cache to avoid repeated GitHub API calls.
        """
        if season in self._remote_files_cache:
            return self._remote_files_cache[season]

        try:
            folders = self._search_subtitle_folders()
            files = self._search_srt_files_in_folders(folders, season)
        except Exception:
            logger.exception("Remote lookup failed for season %s", season)
            files = []

        # cache result (even empty) for the session
        self._remote_files_cache[season] = files
        return files

    def _search_srt_files_in_folders(self, folders: List[str], season: Optional[int] = None) -> List[str]:
        #only needed to find episode 1 of the new season. Then save the path to this episode and the other episodes should be in the same folder and download every episode of this season.
        headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {self.github_token}"}

        results: List[str] = []
        all_files_found: List[Dict] = []  # Track ALL .srt files for logging

        for folder in folders:
            url = f"https://api.github.com/repos/{self.github_owner}/{self.github_repo}/contents/{folder}"
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    # logger.debug("Skipping folder %s (HTTP %s)", folder, resp.status_code)
                    print("test")
                    continue
                items = resp.json()
                if not isinstance(items, list):
                    continue
                for it in items:
                    if it.get("type") != "file":
                        continue
                    name = it.get("name", "")
                    if not name.lower().endswith('.srt'):
                        continue
                    
                    path = it.get("path")
                    s, e = self.extract_season_episode(name)
                    
                    # Log ALL .srt files with their parsed info
                    all_files_found.append({
                        'name': name,
                        'path': path,
                        'parsed_season': s,
                        'parsed_episode': e,
                        'matches_filter': False
                    })
                    
                    # Apply season filter (strict: only include files that explicitly match requested season)
                    if season is not None:
                        if s is None:
                            # File has no parsed season - skip it for season filtering
                            continue
                        if s != season:
                            # Season was parsed but doesn't match filter
                            continue
                        all_files_found[-1]['matches_filter'] = True
                    else:
                        all_files_found[-1]['matches_filter'] = True
                    
                    results.append(path)
                    
                    # Cap results at reasonable limit (max 100 files per season to avoid huge downloads)
                    if len(results) >= 100:
                        break
            except Exception:
                print("fail")
                # logger.exception("Failed to inspect folder: %s", folder)
        
        # Sort results by episode number and cap at max 50 to prevent massive downloads
        if results and season is not None:
            # Try to sort by episode number
            results_with_ep = [(r, self.extract_season_episode(os.path.basename(r))[1]) for r in results]
            results_with_ep = [(r, e if e is not None else 9999) for r, e in results_with_ep]
            results_with_ep.sort(key=lambda x: x[1])
            results = [r for r, _ in results_with_ep[:50]]  # Cap at 50 files
        
        # Log found files to a text file with detailed info
        if all_files_found:
            self._log_found_srt_files(results, season, all_files_found)
        
        return results
    
    def _log_found_srt_files(self, filtered_files: List[str], season: Optional[int] = None, all_files: Optional[List[Dict]] = None) -> None:
        """Log ALL found SRT file paths with detailed parsing info for debugging."""
        try:
            log_file = os.path.join(self._get_cache_base_dir(), "found_srt_files.txt")
            season_str = f"Season {season}" if season is not None else "All"
            timestamp = __import__('datetime').datetime.now().isoformat()
            
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*130}\n")
                f.write(f"SEARCH LOG\n")
                f.write(f"{'='*130}\n")
                f.write(f"Time: {timestamp}\n")
                f.write(f"Anime: {self.anime_folder_name}\n")
                f.write(f"Current Episode: S{self.current_season}E{self.current_episode}\n")
                f.write(f"Search Filter: {season_str}\n")
                f.write(f"Total .srt files found: {len(all_files) if all_files else 0}\n")
                f.write(f"Files matching season filter: {len(filtered_files)}\n")
                f.write(f"{'='*130}\n\n")
                
                if all_files:
                    f.write("ALL .SRT FILES FOUND (with parsing details):\n")
                    f.write(f"{'-'*130}\n")
                    for file_info in sorted(all_files, key=lambda x: (x['name'])):
                        status = "✓ MATCHED" if file_info['matches_filter'] else "✗ EXCLUDED"
                        s_str = f"S{file_info['parsed_season']}" if file_info['parsed_season'] is not None else "S?"
                        e_str = f"E{file_info['parsed_episode']}" if file_info['parsed_episode'] is not None else "E?"
                        parsed_info = f"{s_str}{e_str}" if (file_info['parsed_season'] is not None or file_info['parsed_episode'] is not None) else "UNPARSEABLE"
                        f.write(f"{status:10} | {parsed_info:12} | {file_info['name']}\n")
                        f.write(f"{'':10}   Path: {file_info['path']}\n")
                    f.write(f"{'-'*130}\n\n")
                
                if filtered_files:
                    f.write(f"FILTERED RESULTS (matching Season {season}):\n")
                    f.write(f"{'-'*130}\n")
                    for path in sorted(filtered_files):
                        f.write(f"{path}\n")
                    f.write(f"{'-'*130}\n")
                
                f.write("\n")
        except Exception:
            logger.exception("Failed to log found SRT files")

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
        """
        Download a window of episodes around the currently selected episode.
        Deduplicate by episode number (only one file per episode).
        """
        # Map episode -> (episode, fname, remote_path) keeping first encountered entry for that episode.
        entries_map: Dict[int, Tuple[int, str, str]] = {}
        unknowns: List[Tuple[str, str]] = []  # (fname, remote_path) for files without episode number

        for file in season_files:
            fname = self.sanitize_filename(os.path.basename(file))
            s, e = self.extract_season_episode(os.path.basename(file))
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

    def _download_file(self,remote_path, local_path):
        if not os.path.exists(local_path):
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
        try:
            r = requests.get(remote_path, timeout=15)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            if "text/html" in content_type.lower():
                raise RuntimeError(f"Downloaded HTML instead of SRT from {remote_path}")
            with open(local_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Download failed for {remote_path}: {e}")
# ---------------------- GitHub searching / downloading ----------------------


    def _last_cached(self, season_to_check: int) -> int:
        """
        Unified 'last cached episode' lookup.
        If using a local folder, inspect that folder. Otherwise use cache under cache_github.
        """
        # local folder mode
        if not self.remote_flag:
            if self.local_srt_dir and os.path.isdir(self.local_srt_dir):
                eps = []
                for fn in os.listdir(self.local_srt_dir):
                    if not fn.lower().endswith(".srt"):
                        continue
                    s, e = self.extract_season_episode(fn)
                    if e:
                        eps.append(e)
                print(eps)
                return max(eps) if eps else 0
            return 0
        # remote/cache mode (existing behavior)
        eps = len(self.local_file_list)
        return max(eps) if eps else 0
#endregion -------------------------remote handling-----------------------------

















