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
from collections import defaultdict, Counter
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

        # self.total_duration = 0
        # self.display_data = []
        # self.raw_subtitles = None
        # self.max_width = None
        # self.max_height = None
        # self.is_movie = False
        # self.local_srt_dir = None
        # self.title = None
        # self.url = None
        # self.srt_file = None
        # self.cache_dir = None
        # self._remote_files_cache: Dict[int, List[str]] = {}
        # self.local_episode_paths = {}
        # self.remote_url = ""
        # self.cached_folders: List[str] = []

        # first check if last used remote or not then get path to local or download remote
        if self.remote_flag:
            url = self.config.get("LAST_GITHUB_URL")
            local_srt_path = self._initialize_remote_path(url)#should download the last save github url and in a window of 10 episodes the others. (create remote episode map -> downloaded episodes -> load local path)
            self._register_cache_cleanup()
        else:
            local_srt_path = self.config.get("LAST_LOCAL_SRT_FILE")
        self._load_local_and_process(local_srt_path)
        #have one remote episode map for downloads --> s,e,global --> github path
        #and one local episode map for episode switching local (if remote and episode not found use above and download and update local episode map)
    
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
            local_srt_path= self.set_new_file()# ask for local or remote
            if local_srt_path is None: return
        self._extract_and_set_local_episode_metadata(local_srt_path)
        self.set_subtitle_display_data(local_srt_path)
        if self.is_movie: logger.info(f"Loaded subtitle: Movie | {local_srt_path}")
        else:             logger.info(f"Loaded subtitle: S{self.current_season}E{self.current_episode} | {local_srt_path}")

# -------------------------helpers-----------------------------
    def _extract_and_set_local_episode_metadata(self, local_path):
        if not self.remote_flag: #hardcoded certain local folder when not using cached files
            self.anime_folder_name = local_path.replace("\\", "/").split("/")[local_path.replace("\\", "/").split("/").index("subs")+1]
            self.config.set("LAST_LOCAL_SRT_FILE",local_path)
            self.current_season, self.current_episode , _ = self.extract_season_episode_global(local_path)
        self.is_movie = self.current_season is None and self.current_episode is None
        self._build_local_episode_map(local_path)

    def _build_local_episode_map(self, local_path):
        self.local_srt_dir = os.path.dirname(local_path)
        self.local_srt_files = []
        for fn in os.listdir(self.local_srt_dir):
            if not fn.lower().endswith('.srt'):
                continue
            path = os.path.join(self.local_srt_dir, fn)
            s, e, g = self.extract_season_episode_global(fn)
            rec = {"name": fn, "path": path, "season": s, "episode": e, "global": g}
            self.local_srt_files.append(rec)
        self.local_srt_files.sort(key=lambda x: (x.get("global") if x.get("global") is not None else (x.get("season") or 0, x.get("episode") or 0)))

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
            self.current_season = rec.get("season")
            self.current_episode = rec.get("episode")
            self.srt_file = rec["path"]
            return True
        except Exception:
            logger.exception("Failed to load local subtitle: %s", rec.get("path"))
            return False

    def change_episode(self, action: str, raw: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
        if self.remote_flag:
            return self.change_episode_remote(action, raw)
        return self.change_episode_local(action, raw)

    def change_episode_local(self, action: str, raw: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
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
                target_rec = find_by_global(cur_g + 1)
            if target_rec is None and cur_s is not None and cur_e is not None:
                target_rec = find_by_local(cur_s, cur_e + 1)

        elif action == "dec":
            if cur_g is not None and cur_g > 1:
                target_rec = find_by_global(cur_g - 1)
            if target_rec is None and cur_s is not None and cur_e is not None and cur_e > 1:
                target_rec = find_by_local(cur_s, cur_e - 1)

        elif action == "set":
            if not (isinstance(raw, int) and raw > 0):
                return self.current_season, self.current_episode
            # interpret as local episode in current season first
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
        try:
            message = f"Episode not found in local folder: action={action}, value={raw}"
            logger.warning(message)
            # show user-visible warning
            messagebox.showwarning("Episode not found", message)
        except Exception:
            logger.warning("Could not show messagebox (episode not found).")

        return self.current_season, self.current_episode

    def change_episode_remote(self, action: str, raw: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
        """
        Remote switching: first try to find the file in the local index. If missing, trigger a
        focused windowed download around the target/global (synchronously), refresh local index,
        then load if available. If still missing, show a warning.
        """
        # ensure local index exists (might be empty on startup)
        if not getattr(self, "local_srt_files", None):
            # if we have a local folder (e.g., from a previous run) build it
            self._build_local_episode_map()

        # ensure remote maps exist (for global<->local mapping and download lists)
        if not getattr(self, "remote_episode_map_global", None):
            try:
                if not getattr(self, "all_results_items", None):
                    self._create_remote_episode_map_per_season()
                self.build_remote_episode_maps()
            except Exception:
                logger.exception("Failed to build remote maps in change_episode_remote")

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
                target_global = cur_g + 1
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
            if cur_g is not None and cur_g > 1:
                target_global = cur_g - 1
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
            # prefer local interpretation: current season + episode raw
            if cur_s is not None:
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
                            messagebox.showwarning("Episode not found", f"Episode {raw} not found locally or remotely.")
                            return self.current_season, self.current_episode
            else:
                # no cur season known -> try treat raw as global
                if raw in getattr(self, "remote_episode_map_global", {}):
                    target_global = raw
                    target_s, target_e = self.global_to_local(raw)
                else:
                    messagebox.showwarning("Episode not found", f"Episode {raw} not found.")
                    return self.current_season, self.current_episode
        else:
            return self.current_season, self.current_episode

        # At this point we have target_global (maybe None) and/or target_s/target_e
        # If the file is still not local, request windowed download around target_global (or current global)
        if target_global is None and target_s is not None and target_e is not None:
            target_global = self.local_to_global(target_s, target_e)

        if target_global is None:
            # If we still cannot derive a global index, warn user
            messagebox.showwarning("Episode not found", f"Could not determine global index for requested episode.")
            return self.current_season, self.current_episode

        # Request a synchronous windowed download centered on target_global, so the immediate next episodes are available
        try:
            # synchronous download so that we can load right afterwards
            self.download_window_around_global(target_global, window=20, async_download=False)
            # refresh local index
            self._build_local_episode_map()
        except Exception:
            logger.exception("Failed to download window around global %s", target_global)

        # After download attempt, try to find and load the file
        rec_after = find_by_global(target_global)
        if rec_after:
            if self._load_local_record(rec_after):
                return self.current_season, self.current_episode

        # still not found -> warn
        messagebox.showwarning("Episode not found", f"Requested episode not available after download attempt (global {target_global}).")
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

        def choose_local():popup.destroy(); return self.ask_local_srt_file()
        def choose_remote():popup.destroy();return self.ask_remote_srt_with_hint()
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
            s, e, global_e = self.extract_season_episode_global(se_entry.get().strip().lower())
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
    def _initialize_remote_path(self, init_url: Optional[str] = None, hint: Optional[Tuple[Optional[int], Optional[int]]] = None) -> Optional[str]:
        #download given url
        self._extract_and_set_remote_episode_metadata(url)
        self._create_remote_episode_map_per_season()
        self.build_remote_episode_maps()

        url = init_url
        prompt_hint = None
        if not url:
            url, h_s, h_e = self.ask_remote_srt_with_hint()
            prompt_hint = (h_s, h_e) if (h_s is not None or h_e is not None) else None
            if not url:
                return None

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
                logger.info("Using explicit hint: season=%s episode=%s → global=%s", hint_season, hint_episode, chosen_item.get("global"))

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

        if not chosen_item:
            logger.error("No remote subtitle candidates found for URL: %s", url)
            messagebox.showwarning("Episode not found", "Could not find any subtitle files in the repository for the provided URL.")
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
            messagebox.showerror("Load failed", "Resolved episode item has no download path.")
            return None

        # synchronous download of chosen item so UI can display it immediately
        try:
            season_dir = self._season_cache_dir(target_season) if target_season is not None else self._season_cache_dir()
            filename = self.sanitize_filename(os.path.basename(target_remote_path))
            local_path = os.path.join(season_dir, filename)
            raw_url = self._get_raw_url(target_remote_path)
            self._download_file(raw_url, local_path)
        except Exception:
            logger.exception("Failed to download chosen remote episode: %s", target_remote_path)
            messagebox.showerror("Download failed", "Failed to download the requested subtitle file.")
            return None

        # load the downloaded file and update state
        try:
            self._load_local_and_process(local_path)
            s_loaded, e_loaded, g_loaded = self.extract_season_episode_global(os.path.basename(local_path))
            if s_loaded is not None and e_loaded is not None:
                self.current_season = s_loaded
                self.current_episode = e_loaded
            else:
                if target_season is not None:
                    self.current_season = target_season
                if target_episode is not None:
                    self.current_episode = target_episode
            self.srt_file = local_path
            self.remote_url = url
            try:
                self.config.set("LAST_GITHUB_URL", url)
                if getattr(self, "anime_folder_name", None):
                    self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
            except Exception:
                logger.debug("Failed to persist LAST_GITHUB_URL/LAST_ANIME_NAME")
        except Exception:
            logger.exception("Failed to load downloaded subtitle: %s", local_path)
            messagebox.showerror("Load failed", "Downloaded subtitle could not be loaded.")
            return None

        # refresh local index and remote lookup maps
        try:
            self.update_local_srt_files()
        except Exception:
            logger.exception("Failed to refresh local_srt_files after download")
        try:
            self.build_remote_episode_maps()
        except Exception:
            logger.exception("Failed to rebuild remote lookup maps after download")

        # kick off async windowed download around the chosen global (±20)
        try:
            center_global = target_global
            if center_global is None and target_season is not None and target_episode is not None:
                center_global = self.local_to_global(target_season, target_episode)
            if center_global is not None:
                self.download_window_around_global(center_global, window=20, async_download=True)
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

        self.current_season, self.current_episode, global_e = self.extract_season_episode_global(self.remote_path)
        self.anime_folder_name = self.config.get("LAST_ANIME_NAME")
        url_anime_name = self._extract_anime_name_from_url(self.remote_path)
        if self.current_season == 1: #what if no seasons? change later doesnt make too much sense dont know how to do it. save last used github url will this be always s1? ...
            self.anime_folder_name = url_anime_name
            self.config.set("LAST_ANIME_NAME", url_anime_name)
        self._create_remote_episode_map_per_season()
    
    def _specific_episode_search(self,s,e):
        #season and episode must be in the format of the github s02e0001 or e01 and so on
        api_url = "https://api.github.com/search/code"
        headers = {"Accept": "application/vnd.github.v3+json",
                   "Authorization": f"token {self.github_token}",
                   "User-Agent": "subtitle-searcher"}
        per_page = 100
        if s is not None:
            logger.info(f"Searching [S{s}E{e}] for {self.anime_folder_name}...")
            search_query = f"{self.anime_folder_name} s{s} e{e}"
        elif e is not None:
            search_query = f"{self.anime_folder_name} e{e}"
        else: search_query = f"{self.anime_folder_name}"
        q = (f'repo:{self.github_owner}/{self.github_repo}'
             f' path:subtitles/anime_tv extension:srt in:path {search_query}')
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
        # self.anime_folder_name = "Shingeki no Kyojin"
        logger.info(f"Building comprehensive episode map for {self.anime_folder_name}...")
        all_results_items: List[Dict] = []
        stop_reason = None
        first_query_success_count = 0
        consecutive_first_failures = 0
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
        while True: #search season until none found
            logger.info(f"\nSearching season {season:02d}")
            found_any_for_season = False
            old_length = len(all_results_items)
            for tries in (1, 2, 3, 4):#if 0 hits try amazon instead of netflix then without both and so on can add more fallbacks later
                if tries == 1:
                    search_query = f"{self.anime_folder_name} s{season:02d} Netflix"
                elif tries == 2:
                    search_query = f"{self.anime_folder_name} s{season:02d} Amazon"
                elif tries == 3:
                    search_query = f"{self.anime_folder_name} s{season:02d} Hulu"
                elif tries == 4:
                    search_query = f"{self.anime_folder_name} s{season:02d}"
                elif tries == 5:
                    search_query = f"{self.anime_folder_name}"
                q = (f'repo:{self.github_owner}/{self.github_repo}'
                     f' path:subtitles/anime_tv extension:srt in:path {search_query}')
                params = {"q": q, "per_page": per_page} 
                page = 1
                provider_found = False
                while True:
                    params["page"] = page
                    try: resp = session.get(api_url, params=params, timeout=15)
                    except requests.RequestException as e:# network error: stop and return what we have
                        logger.error(f"network error: {e}")
                        return
                    hdr = resp.headers
                    last_rate_info = _print_rate_info(hdr)
                    rem = last_rate_info.get("remaining")
                    if rem is not None and int(rem) <= 0:
                        _wait_until_reset(last_rate_info)# after waiting, retry same page
                        continue
                    if resp.status_code == 200:
                        data = resp.json()
                        items = data.get("items", [])
                        if not items and page == 1:
                            print(f"GitHub search returned 0 items for query: {q}")
                            break
                        provider_found = True
                        found_any_for_season = True
                        for it in items:
                            name = os.path.basename(it.get("path") or it.get("name") or "")
                            s, e, global_e = self.extract_season_episode_global(name)
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
                if provider_found:
                    break
            if not found_any_for_season:
                logger.info(f"No providers found results for season {season:02d}, stopping.")
                break  # stop season loop entirely
            print("For season:",season, " we found ",len(all_results_items)-old_length,"files")
            season += 1
            if season == end_season:
                break
            if self.anime_folder_name == "Shingeki no Kyojin" and season == 8:
                break
            if self.anime_folder_name == "One Piece" and season == 40:
                break
            
        season_offset, local_numbering = self.compute_season_offsets_per_season(all_results_items)
        self.assign_globals_per_season(all_results_items, season_offset, local_numbering)
        
        all_results_items.sort(key=self.sort_key_per_season)
        # Build SxxEyy -> Gzz map for diagnostics (first seen mapping per pair)
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
        Returns (season_offset, local_numbering)
        - season_offset: dict season -> offset (used when season uses local numbering)
        - local_numbering: dict season -> bool (True if season's episode numbers are local i.e. include E1)
        """
        season_eps = defaultdict(set)     # season -> set(episode numbers)
        pairs = defaultdict(lambda: defaultdict(set))  # season -> episode -> set(globals)
        for it in items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            if s is None or e is None:
                continue
            season_eps[s].add(int(e))
            if g is not None:
                pairs[s][int(e)].add(int(g))

        # Decide whether seasons use local numbering (contain an episode 1) or not.
        local_numbering = {s: (1 in eps) for s, eps in season_eps.items()}

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
                running = max(running, season_offset[s] + (max(season_eps[s]) if season_eps[s] else 0))
            else:
                # season uses global numbering: update running to be at least the max global seen
                running = max(running, max(season_eps[s]) if season_eps[s] else running)

        return season_offset, local_numbering

    def assign_globals_per_season(self, items, season_offset, local_numbering):
        used_globals = set(it['global'] for it in items if it.get('global') is not None)

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
            if g is not None:
                try:
                    self.remote_episode_map_global[int(g)] = entry
                except Exception:
                    pass
            if s is not None:
                try:
                    self.remote_episode_map_season[int(s)].append(entry)
                except Exception:
                    pass
        # sort season lists by episode (fallback to global)
        for s, lst in self.remote_episode_map_season.items():
            lst.sort(key=lambda x: (x.get("episode") or 0, x.get("global") or 0))

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

    def download_window_around_global(self, center_global: int, window: int = 20, async_download: bool = True):
        """
        Download files with global indices in [center_global - window, center_global + window].
        Creates season cache dirs and downloads missing .srt files. If async_download is True,
        other files are downloaded in daemon threads (current file can be downloaded synchronously).
        """
        if center_global is None:
            return []
        if not hasattr(self, "remote_episode_map_global"):
            self.build_remote_episode_maps()
        got = []
        lo = max(1, int(center_global) - int(window))
        hi = int(center_global) + int(window)
        for g in range(lo, hi + 1):
            item = self.remote_episode_map_global.get(g)
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
                # spawn thread
                def _dl(url, lp):
                    try:
                        self._download_file(url, lp)
                    except Exception:
                        logger.exception("Background download failed for %s", url)
                t = threading.Thread(target=_dl, args=(raw_url, local_path), daemon=True)
                t.start()
                got.append(local_path)
        # update local_srt_files map after scheduling downloads (some may still be in progress)
        try:
            self.update_local_srt_files()
        except Exception:
            logger.exception("Failed to refresh local_srt_files after scheduling downloads")
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
                if not fn.lower().endswith(".srt"):
                    continue
                full = os.path.join(root, fn)
                s, e, g = self.extract_season_episode_global(fn)
                # if file name had no explicit global, try to map via remote maps
                if g is None and s is not None and e is not None:
                    g = self.local_to_global(s, e)
                rec = {"season": s, "episode": e, "global": g, "path": full, "name": fn}
                self.local_srt_files.append(rec)
        # optionally sort by global or season/episode
        self.local_srt_files.sort(key=lambda x: (x.get("global") if x.get("global") is not None else (x.get("season") or 0, x.get("episode") or 0)))
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

        # Normalize
        n = name.replace('\u2013', '-').replace('\u2014', '-')

        # Helper: plausible episode/global number
        def is_probable_episode_number(num: int) -> bool:
            if num <= 0:
                return False
            if 1900 <= num <= 2099:  # years
                return False
            if num > 10000:
                return False
            return True

        # Regexes
        re_s_e_paren = re.compile(r'(?xi)\bS(?P<s>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})\b[^()\[\]]*[\(\[]\s*(?P<g>\d{1,4})\s*[\)\]]')
        re_s_e = re.compile(r'(?xi)\bS(?P<s>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})\b')
        re_s_dash_e = re.compile(r'(?xi)\bS(?P<s>\d{1,2})\s*[-._ ]+\s*(?P<e>\d{1,4})\b')
        re_s_paren = re.compile(r'(?xi)\bS(?P<s>\d{1,2})[ ._\-]*[\(\[]\s*(?P<g>\d{1,4})\s*[\)\]]')
        re_episode_number = re.compile(r'(?xi)\b(?:ep|episode|ep\.)[ ._\-#]*(?P<num>\d{1,4})\b')
        re_e_only = re.compile(r'(?xi)(?:^|[\s._-])E(?P<e>\d{1,4})\b')
        re_dash_number = re.compile(r'(?xi)\s-\s*(?P<num>\d{1,4})\b')
        re_bracket_number = re.compile(r'[\(\[]\s*(\d{1,4})\s*[\)\]]')
        re_trailing_number = re.compile(r'(?xi)(?:[_\-. ]|^)(?P<num>\d{1,4})(?:\.[a-z0-9]{1,6})?$')

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

        # 2) SxxEyy -> local episode (conservative: do not treat as global)
        m = re_s_e.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
            except Exception:
                return None, None, None
            if s == 1:
                return s, e, e
            return s, e, None

        # 3) Sxx - yy -> common fansub format
        m = re_s_dash_e.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
                if not is_probable_episode_number(e):
                    raise ValueError
            except Exception:
                return None, None, None
            if s == 1:
                return s, e, e
            return s, e, None

        # 4) Sxx (GGG) -> season present, bracket likely a global index (no E present)
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

        # 5) textual "Season X Episode Y"
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

        # 6) Standalone "E38" - if a season exists elsewhere treat as local episode; else treat as global
        m = re_e_only.search(n)
        if m:
            try:
                num = int(m.group('e'))
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

        # 7) "Ep 38" or "Episode 38" - if a season exists elsewhere treat as local episode; else treat as global
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

        # 8) " - 07 " style fansub numbering (no S/E). If season exists elsewhere treat as local episode; else global.
        m = re_dash_number.search(n)
        if m:
            try:
                num = int(m.group('num'))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r'(?xi)\bS(?P<s>\d{1,2})\b', n)
            if not s_m:
                s_m = re.search(r'(?xi)\bseason[ ._\-]*(?P<s>\d{1,2})\b', n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None
            return None, None, num

        # 9) bracketed numbers (general). If season present and no E present: bracket likely global.
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

        # 10) trailing number heuristics:
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
#endregion -------------------------remote handling-----------------------------
















