# subtitle_manager.py
import os
import re
import shutil
import requests
import atexit
from urllib.parse import quote, urlparse, unquote
from typing import List, Optional, Tuple, Dict
import threading

import regex
import srt
import chardet
import tkinter as tk
from tkinter import font as tkFont
from tkinter import filedialog, messagebox, simpledialog

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

    def __init__(self, config: ConfigManager) -> None:
        self.config = config

        self.total_duration = 0
        self.display_data = []
        self.raw_subtitles = None
        self.max_width = None
        self.max_height = None
        
        self.local_srt_dir = None
        self.is_movie = False
        self.title = None

        self.url = None
        self.srt_file = None
        self.cache_dir = None
        self._remote_files_cache: Dict[int, List[str]] = {}
        self.github_token = os.environ.get("GITHUB_TOKEN")

        # first check if last used remote or not then get path to local or download remote
        self.remote_flag = self.config.get("REMOTE_FLAG")
        if self.remote_flag:
            local_srt_path = self._initialize_remote_path()
            # register cleanup of temp cache on exit
            self._register_cache_cleanup()
        else:
            local_srt_path = self.config.get("LAST_LOCAL_SRT_FILE")
        self._load_local_and_process(local_srt_path)

#--------------------------------local handling-----------------------------------
    def _load_local_and_process(self, local_srt_path: str) -> bool:
        if not (local_srt_path and os.path.isfile(local_srt_path)):
            # logger.error("Local SRT path not found: \n%s\n -> Manual selection", local_srt_path)
            local_srt_path = self.ask_local_srt_file()
        self._extract_and_set_local_episode_metadata(local_srt_path)
        self.set_subtitle_display_data(local_srt_path)

    def _extract_and_set_local_episode_metadata(self, local_path):
        if not self.remote_flag:
            self.anime_folder_name = local_path.replace("\\", "/").split("/")[local_path.replace("\\", "/").split("/").index("subs")+1]
            self.config.set("LAST_LOCAL_SRT_FILE",local_path)
        self.current_season, self.current_episode = self.extract_season_episode(local_path)
        if self.current_season is None and self.current_episode is None:
            self.is_movie = True
        self.local_srt_dir = os.path.dirname(local_path)
        self.local_file_list = [f for f in os.listdir(self.local_srt_dir) if f.lower().endswith('.srt')]
        # if not self.local_file_list:
        #     logger.error("No .srt files found in folder: %s", self.local_srt_dir)

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
#--------------------------------local handling-----------------------------------


#--------------------------------file selection-----------------------------------
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
#--------------------------------file selection-----------------------------------


# -------------------------episode / season switching-----------------------------
    def change_episode(self, action: str, raw: Optional[int] = None) -> Tuple[int, int]:
        #action: "dec","inc","set"; raw: if user set episode(int); sets new episode and returns target
        cur_season = self.current_season
        cur_episode = self.current_episode
        
        #change local episode
        if not self.remote_flag:
            if action == 'dec':
                #search for self.current_episode - 1 if in lower season or same season. (inside the current anime folder)
                for filename in self.local_file_list:
                    s, e = self.extract_season_episode(filename)
                    #can be S(current-1)E(cuurent)-1 or S(current)E(cuurent)-1
                    if e == (self.current_episode - 1): return filename 





        episodes = []
        for filename in self.local_file_list:
            s, e = self.extract_season_episode(filename)
            if e is not None: episodes.append(e)
        lowest_episode, highest_episode = min(episodes), max(episodes)

        if action == 'dec':#decrease episode if episdoe 1 search for new season (only remote for now)
            if cur_season <= 1:
                if cur_episode <= 1:  # cannot go below S1E1
                    return cur_season, cur_episode
                target_episode = cur_episode - 1 
            elif cur_season > 1:
                if cur_episode > lowest_episode:
                #figure out if season switch is needed.
                    self._find_next_episode(action)

                target_episode = cur_episode - 1
                target_season = cur_season
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
                        # season likely not released
                        return cur_season, cur_episode
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
                    if 1 in self._cached_episode_numbers(target_season):
                        target_episode = 1
                else:
                    # not cached, search remote for season+1
                    folders = self._search_subtitle_folders()
                    files = self._search_srt_files_in_folders(folders, target_season)
                    if not files:
                        return cur_season, cur_episode
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
                    if not chosen_remote:
                        return cur_season, cur_episode
                    target_episode = min_e
                    remote_path = chosen_remote
                    season_files = files

        elif action == 'set': #manually written inside the settings episode entry raw only > 0
            lc = self._last_cached(cur_season) #change if specific episode is wished
            if raw > lc:
                return cur_season, cur_episode
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
            if getattr(self, "using_local_folder", False) and self.season_dir:
                season_dir = self.season_dir
            else:
                season_dir = self._season_cache_dir()
           
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
            folders = self._search_subtitle_folders()
            files = self._search_srt_files_in_folders(folders, target_season)
            if not files:
                return cur_season, cur_episode
            # find file matching target_episode
            chosen_remote = None
            for fpath in files:
                s, e = self.extract_season_episode(os.path.basename(fpath))
                if e == target_episode:
                    chosen_remote = fpath
                    break
            if chosen_remote is None:
                # fallback: choose closest available (e.g., if asking for last ep and it's the max in files)
                max_e = 0
                for fpath in files:
                    s, e = self.extract_season_episode(os.path.basename(fpath))
                    if e and e > max_e:
                        max_e = e
                        chosen_remote = fpath
                if chosen_remote is None:
                    return cur_season, cur_episode
            remote_path = chosen_remote
            season_files = files

        # download the remote_path into season cache
        season_dir = self._season_cache_dir()
        filename = self.sanitize_filename(os.path.basename(remote_path))
        local_path = os.path.join(season_dir, filename)

        # blocking download of the required episode (so UI can show it)
        try:
            self._download_file(remote_path, local_path)
            # verify file exists
            # if not os.path.isfile(local_path):
            #     logger.error("Downloaded file missing: %s", local_path)
            #     return cur_season, cur_episode
            # load and update state
            self._load_local_and_process(local_path)
            self.current_season = target_season
            self.current_episode = target_episode
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
            # logger.exception("Failed to download or load remote episode: %s", remote_path)
            return cur_season, cur_episode

        # kick off background downloads for remaining season files (if we have file list)
        try:
            if season_files:
                # convert remote paths to filenames to tell the async downloader which is current
                current_file = filename
                self.download_remaining_season_async(season_files, current_file, season_dir, window = 15)
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
# -------------------------episode / season switching-----------------------------


# ---------------------- get data -------------------------
    def get_anime_name(self)-> Optional[str]:
        return self.anime_folder_name
    
    def get_subtitle_display_data(self):
        return self.display_data
    
    def get_subtitle_geometry(self):
        return self.calculate_geometry()
        
    def get_episode_metadata(self):
        return (self.github_owner, self.github_repo, self.github_ref, self.remote_path,
                self.remote_folder,self.anime_folder_name, self.file_name,
                self.current_season, self.current_episode)
    
    def get_total_duration(self) -> float:
        return self.subtitles[-1].end.total_seconds()
    
    def get_current_season(self) -> int:
        return self.current_season
      
    def get_current_episode(self) -> int:
        return self.current_episode
# ---------------------- get data -------------------------

################ TODO: figure out the anime name of first season #################
##### workaround user gives always s1 when pasting URL##########
#look for same string in folder name and file name?





# -------------------------remote handling-----------------------------
    def _initialize_remote_path(self):
        #extract github metadata
        init_url = self.config.get("LAST_GITHUB_URL")
        if not init_url:#fallback
            init_url = self.ask_remote_srt_with_hint()
            if not init_url:
                local_srt_path = self.ask_local_srt_file()
                if local_srt_path:
                    return local_srt_path
        self._extract_and_set_remote_episode_metadata(init_url)
        local_srt_path = self.download_current_episode(self.remote_path) #other episodes downloaded in app.py
        return local_srt_path
    

    def _extract_and_set_remote_episode_metadata(self, remote_url):
        github_dict = self._parse_github_url(remote_url)
        self.github_owner = github_dict["owner"]
        self.github_repo  = github_dict["repo"]
        self.github_ref   = github_dict["ref"]
        self.remote_path = github_dict["path"]
        self.remote_folder = os.path.dirname(self.remote_path)
        self.anime_folder_name = self._extract_anime_name_from_url(self.remote_path)
        self.current_season, self.current_episode = self.extract_season_episode(remote_url)
        season_dir = self._season_cache_dir()
        file_name = os.path.basename(self.remote_path)
        local_path = os.path.join(season_dir, file_name)
        self._extract_and_set_local_episode_metadata(local_path)
        self.file_name = file_name
        self.config.set("LAST_GITHUB_URL", remote_url)
        if self.current_season == 1:
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
        else:
            self.anime_folder_name = self.config.get("LAST_ANIME_NAME")

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
        for f in files:#find wished episode
            s, e = self.extract_season_episode(os.path.basename(f))
            if e == wished_episode:
                wished_episode_file = f
                break

        if wished_episode is None:
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
    

# ---------------------- helpers: cache dirs ----------------------base
    def _season_cache_dir(self) -> str:
        base = self._get_cache_base_dir()
        season_dir = os.path.join(base, self.anime_folder_name,f"Season{self.current_season}")
        if not os.path.exists(season_dir):
            os.makedirs(season_dir, exist_ok=True)
        return season_dir
    
    def _get_cache_base_dir(self) -> str: #get current base directory
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
        base = os.path.join(project_root, "cache_github")
        os.makedirs(base, exist_ok=True)
        return base
 
    def _cached_episode_numbers(self, season: int) -> List[int]:# count current season episodes maybe needs adjustment if github switches from sXeX to Ex or wrong season count maybe need to prioritize episode
        season_dir = self._season_cache_dir()
        if not os.path.isdir(season_dir):
            return []
        eps = []
        for fn in os.listdir(season_dir):
            if not fn.lower().endswith(".srt"):
                continue
            s, e = self.extract_season_episode(fn)
            if e:
                eps.append(e)
        return sorted(set(eps))

# ---------------------- helpers: cache dirs ----------------------



# ---------------------- Helpers: parsing ----------------------
    def _get_raw_url(self, filename: str) -> str:
        return f"https://raw.githubusercontent.com/{self.github_owner}/{self.github_repo}/{self.github_ref}/{filename}"

    def extract_season_episode(self, name: str) -> Tuple[Optional[int], Optional[int]]:
        # 1) SxxExx
        m = re.search(r'(?i)[sS](\d{1,2})\D*[eE](\d{1,4})', name)
        if m:
            return int(m.group(1)), int(m.group(2))

        # 2) Exx
        m = re.search(r'(?i)\b[eE](\d{1,4})\b', name)
        if m:
            return None, int(m.group(1))

        # 3) After dash
        m = re.search(r'-(?:\s*)(\d{2,4})(?=\s|\[|\.|$)', name)
        if m:
            return None, int(m.group(1))

        # 4) LAST number before extension/tags
        nums = re.findall(r'(?<!\d)(\d{2,4})(?!\d)', name)
        if nums:
            ep = int(nums[-1])
            # filter obvious junk
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

    def _search_subtitle_folders(self) -> List[str]:

        api_url = "https://api.github.com/search/code"
        headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {self.github_token}"}
        params = {"q": f'repo:{self.github_owner}/{self.github_repo} {self.anime_folder_name}'}

        resp = requests.get(api_url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            print("fail")
            # raise RuntimeError(f"GitHub search failed: {resp.status_code}, {resp.text}")

        data = resp.json()
        results = []

        for item in data.get("items", []):
            path = item.get("path", "")
            if self.anime_folder_name.lower() in path.lower():
                folder = os.path.dirname(path)
                results.append(folder)
        return list(set(results))

    def _search_srt_files_in_folders(self, folders: List[str], season: Optional[int] = None) -> List[str]:
        #only needed to find episode 1 of the new season. Then save the path to this episode and the other episodes should be in the same folder and download every episode of this season.
        headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {self.github_token}"}

        results: List[str] = []

        for folder in folders:
            url = f"https://api.github.com/repos/{self.github_owner}/{self.github_repo}/contents/{folder}"
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    print("fail")
                    # logger.debug("Skipping folder %s (HTTP %s)", folder, resp.status_code)
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
                    # if not any(x in lname for x in ['amazon','netflix',"bandai", "Webrip"]):
                    #     continue
                    s, e = self.extract_season_episode(name)
                    if season is not None:
                        if s is None:
                            continue
                        if s != season:
                            continue
                    results.append(it.get("path"))
            except Exception:
                print("fail")
                # logger.exception("Failed to inspect folder: %s", folder)
        return results

    def download_current_episode(self, remote_path):
        file_name = self.sanitize_filename(os.path.basename(remote_path))
        season_dir = self._season_cache_dir()
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
        # try:
            r = requests.get(remote_path)
            r.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(r.content)
        # except Exception as e:
        #     logger.error(f"Download failed for {remote_path}: {e}")
# ---------------------- GitHub searching / downloading ----------------------


    def _last_cached(self, season_to_check: int) -> int:
        """
        Unified 'last cached episode' lookup.
        If using a local folder, inspect that folder. Otherwise use cache under cache_github.
        """
        # local folder mode
        if getattr(self, "using_local_folder", False):
            sd = self.season_dir
            if sd and os.path.isdir(sd):
                eps = []
                for fn in os.listdir(sd):
                    if not fn.lower().endswith(".srt"):
                        continue
                    s, e = self.extract_season_episode(fn)
                    if e:
                        eps.append(e)
                return max(eps) if eps else 0
            return 0

        # remote/cache mode (existing behavior)
        eps = self._cached_episode_numbers(season_to_check)
        return max(eps) if eps else 0
# -------------------------remote handling-----------------------------








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










