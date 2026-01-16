# subtitle_manager.py
import os
import re
import shutil
import requests
import atexit
import shutil
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
First run: use last used github url to download current season
during runtime: either episode switch or season switch
e switch: switch_episode(season, episode) look for s(season)e(episode+-1) (depending on switching) 
if this is not in current cache get the new season:
s switch: look for anime name in url, search in github for folders with this name. 
(problem if currently at season 2 and the naming is arbitrary the base name of season two must not be inside name of season three)
(solution: if season 1 save the name of the anime in config and only change if new anime/new season 1 and use this to search the other seasons)
(maybe make an additional check for the least common char in all of the folders found -> should be anime name)
if last episode (not in current cache) look for S(season+1)E1 inside the folders. -> download from the folder
where this srt file is in, the other srt files and safe in cache. (If multiple hits for folder use the first hit)
(For later: If srt button is used ask for new url to get new anime)
'''

class SubtitleManager:

    CLEAN_PATTERN = re.compile(r'\{\\an\d+\}')
    SEASON_PATTERN = re.compile(r'S(\d+)', re.IGNORECASE)
    EPISODE_PATTERN = re.compile(r'E(\d+)', re.IGNORECASE)
    RUBY_PATTERN = regex.compile(r'(\p{Han}+)\(([^)]+)\)')

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        
        self.url = None
        local_srt_path = None
        self.srt_file = None
        self.srt_dir = None
        self.is_movie = False
        self.title = None
        self.total_duration = 0
        self.display_data = []
        self.raw_subtitles = None
        self.max_width = None
        self.max_height = None
        self.cache_dir = None
        self._remote_files_cache: Dict[int, List[str]] = {}

        # find last URL or ask user
        init_url = self._parse_github_url(self.config.get("LAST_GITHUB_URL"))
        if not init_url:
            init_url = self.ask_remote_srt_file()
        if not init_url:
            local_srt_path = self.ask_local_srt_file()
        if local_srt_path:
            self.load_local_srt(local_srt_path)

        self.github_token = os.environ.get("GITHUB_TOKEN")

        (owner, repo, ref, path,
            file_name, self.current_season, self.current_episode,
            anime_name, remote_folder) = self.extract_episode_metadata(init_url)
        # determine persistent anime folder name
        stored_name = self.config.get("LAST_ANIME_NAME")
        if stored_name and stored_name.strip():
            self.anime_folder_name = stored_name
        else:
            self.anime_folder_name = anime_name
            if self.anime_folder_name:
                self.config.set("LAST_ANIME_NAME", self.anime_folder_name)



        # create cache directory for the current season and download first file and process
        self.season_dir = self._season_cache_dir(self.anime_folder_name, self.current_season, create=True)
        self.update_current_github_reference(owner, repo, ref, path)
        self.srt_file = self.download_current_episode(path)
        self._load_and_process(self.srt_file)

        # register cleanup of temp cache on exit
        self._register_cache_cleanup()

################ TODO: figure out the anime name of first season #################
##### workaround user gives always s1 when pasting URL##########
    def _load_and_process(self, local_path: str) -> None:
        if not local_path or not os.path.isfile(local_path):
            raise FileNotFoundError(local_path)
        self.set_subtitle_display_data(local_path)
    

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
        cleaned = self.RUBY_PATTERN.sub(r'\1«\2»', cleaned)
        cleaned = regex.sub(r'[（(].*?[）)]', '', cleaned)
        cleaned = cleaned.replace('«', '(').replace('»', ')')
        return cleaned.replace('&lrm;', '').replace('\u200e', '').strip()

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


# ---------------------- get data -------------------------
    def get_title(self)-> Optional[str]:
        return self.anime_folder_name
    
    def get_subtitle_display_data(self):
        return self.display_data
    
    def get_subtitle_geometry(self):
        return self.calculate_geometry()
        
    def get_season_episode_movie_info(self) -> Tuple[Optional[int], Optional[int]]:
        return (self.current_season, self.current_episode)

    def get_total_duration(self) -> float:
        return self.subtitles[-1].end.total_seconds()
    
    def get_current_season(self) -> int:
        return self.current_season  
    def get_current_episode(self) -> int:
        return self.current_episode
# ---------------------- get data -------------------------


# ---------------------- helpers: cache dirs ----------------------
    def _season_cache_dir(self, anime_folder_name: str, season: int, create: bool = True) -> str:
        base = self._get_cache_base_dir()
        anime_dir = os.path.join(base, anime_folder_name)
        if create:
            os.makedirs(anime_dir, exist_ok=True)
        season_dir = os.path.join(anime_dir, f"Season{season}")
        if create:
            os.makedirs(season_dir, exist_ok=True)
        return season_dir
 
    def _get_cache_base_dir(self) -> str: #get current base directory
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
        base = os.path.join(project_root, "cache_github")
        os.makedirs(base, exist_ok=True)
        return base

    def _cached_episode_numbers(self, anime_folder_name: str, season: int) -> List[int]:
        season_dir = self._season_cache_dir(anime_folder_name, season, create=False)
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
# ---------------------- helpers: cache dirs ----------------------



# ---------------------- Helpers: parsing ----------------------
    def extract_episode_metadata(self, episode_url):
        owner = episode_url["owner"]
        repo  = episode_url["repo"]
        ref   = episode_url["ref"]
        path  = episode_url["path"]

        file_name = os.path.basename(path)
        season_num, episode_num = self.extract_season_episode(file_name)
        anime_name = self._extract_anime_name_from_url(path)
        remote_folder = os.path.dirname(path)

        return owner, repo, ref, path, file_name, season_num, episode_num, anime_name, remote_folder

    def _get_raw_url(self, owner: str, repo: str, ref: str, path: str) -> str:
        enc_path = "/".join(quote(p) for p in path.split("/"))
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{enc_path}"

    def extract_season_episode(self, name: str) -> Tuple[Optional[int], Optional[int]]:
        # 1) SxxExx
        m = re.search(r'(?i)s(\d{1,2})\D*e(\d{1,4})', name)
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
            if 1900 <= ep <= 2100:  # year
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
        
    def _extract_number(self, pattern: re.Pattern, filename: str):
        match = pattern.search(filename)
        if match: return int(match.group(1))

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
# ---------------------- Helpers: parsing ----------------------



# ---------------------- GitHub searching / downloading ----------------------
    def update_current_github_reference(self, owner, repo, ref, path):
        self.github_owner = owner
        self.github_repo  = repo
        self.github_ref   = ref
        self.github_path  = path

    def _get_remote_files_for_season(self, season: int) -> List[str]:
        """
        Return list of remote file paths for a season (cached in-memory per run).
        Caches results in self._remote_files_cache to avoid repeated GitHub API calls.
        """
        if season in self._remote_files_cache:
            return self._remote_files_cache[season]

        try:
            folders = self._search_subtitle_folders(
                self.github_owner, self.github_repo, self.github_ref, self.anime_folder_name
            )
            files = self._search_srt_files_in_folders(
                self.github_owner, self.github_repo, folders, season
            )
        except Exception:
            logger.exception("Remote lookup failed for season %s", season)
            files = []

        # cache result (even empty) for the session
        self._remote_files_cache[season] = files
        return files

    def _search_subtitle_folders(self, owner: str, repo: str, ref: str, query: str) -> List[str]:

        api_url = "https://api.github.com/search/code"
        headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {self.github_token}"}
        params = {"q": f'repo:{owner}/{repo} {query}'}

        resp = requests.get(api_url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub search failed: {resp.status_code}, {resp.text}")

        data = resp.json()
        results = []

        for item in data.get("items", []):
            path = item.get("path", "")
            if query.lower() in path.lower():
                folder = os.path.dirname(path)
                results.append(folder)
        return list(set(results))

    def _search_srt_files_in_folders(self, owner: str, repo: str, folders: List[str], season: Optional[int] = None) -> List[str]:
        #only needed to find episode 1 of the new season. Then save the path to this episode and the other episodes should be in the same folder and download every episode of this season.
        headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {self.github_token}"}

        results: List[str] = []

        for folder in folders:
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{folder}"
            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    logger.debug("Skipping folder %s (HTTP %s)", folder, resp.status_code)
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
                logger.exception("Failed to inspect folder: %s", folder)
        return results

    def download_current_episode(self, path):
        file_name = self.sanitize_filename(os.path.basename(path))

        # Always use season cache directory
        season_dir = self._season_cache_dir(self.anime_folder_name, self.current_season)
        local_path = os.path.join(season_dir, file_name)

        self._download_file(self.github_owner, self.github_repo, self.github_ref, path, local_path)
        return local_path

    def sanitize_filename(self,filename: str) -> str:
        # Replace invalid Windows characters with underscore
        return re.sub(r'[<>:"/\\|?*]', '_', filename)

    # def download_remaining_season_async(self, owner, repo, ref, season_files, current_file, season_dir):
    #     for remote_path in season_files:
    #         filename = self.sanitize_filename(os.path.basename(remote_path))
    #         local_path = os.path.join(season_dir, filename)

    #         if filename == current_file:
    #             continue
    #         local_path = os.path.join(season_dir, filename)
    #         threading.Thread(
    #             target=self._download_file,
    #             args=(owner, repo, ref, remote_path, local_path),
    #             daemon=True
    #         ).start()

    def download_remaining_season_async(self, owner, repo, ref, season_files: List[str], current_file: str, season_dir: str, window: int = 20):
        """
        Download a window of episodes around the currently selected episode.
        Deduplicate by episode number (only one file per episode).
        """
        # Map episode -> (episode, fname, remote_path) keeping first encountered entry for that episode.
        entries_map: Dict[int, Tuple[int, str, str]] = {}
        unknowns: List[Tuple[str, str]] = []  # (fname, remote_path) for files without episode number

        for remote_path in season_files:
            fname = self.sanitize_filename(os.path.basename(remote_path))
            s, e = self.extract_season_episode(os.path.basename(remote_path))
            if e is None:
                unknowns.append((fname, remote_path))
                continue
            if e in entries_map:
                # keep first seen for this episode (avoid downloading multiple variants for same epi)
                continue
            entries_map[e] = (e, fname, remote_path)

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
                args=(owner, repo, ref, remote_path, local_path),
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
                    args=(owner, repo, ref, remote_path, local_path),
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
                logger.debug("Evicted old episode file: %s", path)
            except Exception:
                logger.exception("Failed to remove cached file: %s", fn)




    def _download_file(self, owner, repo, ref, remote_path, local_path):
        # Ensure only the directory exists, not the file
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        url = self._get_raw_url(owner, repo, ref, remote_path)
        try:
            r = requests.get(url)
            r.raise_for_status()
            with open(local_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Download failed for {url}: {e}")
# ---------------------- GitHub searching / downloading ----------------------


# ---------------------- file selection ----------------------
    def ask_local_srt_file(self) -> Optional[str]:
        try:
            window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                parent=window,
                title="Select SRT File",
                initialdir=self.srt_dir,
                filetypes=[("SubRip files","*.srt"),("All Files","*.*")]
            )
            window.destroy()
            if not path: 
                return None
            return path
        except Exception:
            logger.exception("SRT file selection failed")
            return None
        
    def ask_remote_srt_file(self) -> str:
        window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
        url = simpledialog.askstring("Remote URL", "Enter GitHub subtitle URL:", parent=window)
        window.destroy()
        if not url or not url.strip():
            logger.exception("No url given")
            return None
        return url
    
    def ask_remote_srt_with_hint(self) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """
        Show a small dialog that asks for:
        - GitHub subtitle URL
        - Season/Episode hint in form sXXeYY (case-insensitive). User may write minimal digits (s2e1).
        Returns (url, season_int_or_None, episode_int_or_None) or (None, None, None) on cancel.
        """
        result = {"url": None, "season": None, "episode": None}
        dlg = tk.Toplevel()
        dlg.title("Remote subtitle (URL + sXeY)")
        dlg.attributes("-topmost", True)
        dlg.grab_set()
        dlg.resizable(False, False)

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
            se = se_entry.get().strip().lower()
            s = e = None
            if se:
                m = re.search(r'[sS](\d{1,2})\D*[eE](\d{1,4})', se)
                if m:
                    s = int(m.group(1))
                    e = int(m.group(2))
            result["url"], result["season"], result["episode"] = (u or None, s, e)
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=6)
        tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

        # center
        dlg.update_idletasks()
        sw, sh = dlg.winfo_screenwidth(), dlg.winfo_screenheight()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        dlg.geometry(f"+{x}+{y}")

        dlg.wait_window(dlg)
        return result["url"], result["season"], result["episode"]

# ---------------------- file selection ----------------------

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
        eps = self._cached_episode_numbers(self.anime_folder_name, season_to_check)
        return max(eps) if eps else 0


# ---------------------- episode / season switching ----------------------
    def change_episode(self, action: str, raw: Optional[int] = None) -> Tuple[int, int]:
        #action: "dec","inc","set"; raw: if user set episode(int); sets new episode and returns target
            
        season = self.current_season
        current = self.current_episode

        target_season = season
        target_episode = current
        remote_path = None
        season_files = None

        if action == 'dec':
            if season == 1 and current <= 1:  # cannot go below S1E1
                return season, current
            if current > 1:
                target_episode = current - 1
                target_season = season
            else:
                # go to previous season, prefer cached last-episode if that season is cached
                target_season = season - 1
                lc = self._last_cached(target_season)
                # lc > 0 only when that specific season is cached
                if lc:
                    target_episode = lc
                else:
                    # Not cached: find remote files for previous season (cached per-session)
                    files = self._get_remote_files_for_season(target_season)
                    if not files:
                        # season likely not released
                        return season, current
                    # pick file with highest episode number
                    max_e = 0
                    chosen_remote = None
                    for fpath in files:
                        s, e = self.extract_season_episode(os.path.basename(fpath))
                        if e and e > max_e:
                            max_e = e
                            chosen_remote = fpath
                    if not chosen_remote:
                        return season, current
                    target_episode = max_e
                    remote_path = chosen_remote
                    season_files = files


                
        elif action == 'inc':
            # check in-cache
            lc = self._last_cached(season)
            if current + 1 <= lc:
                target_episode = current + 1
                target_season = season
            else:
                # try next season (remote or cached)
                target_season = season + 1
                lc_next = self._last_cached(target_season)
                if lc_next:
                    # if cached and contains ep1, use that
                    if 1 in self._cached_episode_numbers(self.anime_folder_name, target_season):
                        target_episode = 1
                else:
                    # not cached, search remote for season+1
                    folders = self._search_subtitle_folders(self.github_owner, self.github_repo, self.github_ref, self.anime_folder_name)
                    files = self._search_srt_files_in_folders(self.github_owner, self.github_repo, folders, target_season)
                    if not files:
                        return season, current
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
                        return season, current
                    target_episode = min_e
                    remote_path = chosen_remote
                    season_files = files

        elif action == 'set': #manually written inside the settings episode entry raw only > 0
            lc = self._last_cached(season)
            if raw > lc:
                return season, current
            target_season = season
            target_episode = raw
        else:
            # unknown action
            return season, current

        # If the target is the same as current and it exists cached, do nothing
        if target_season == self.current_season and target_episode == self.current_episode:
            return self.current_season, self.current_episode
        

        # Try to load from cache if available and we don't already have a remote_path
        if remote_path is None:
            # If we're in local mode, look in the local folder; otherwise look in cache_github
            if getattr(self, "using_local_folder", False) and self.season_dir:
                season_dir = self.season_dir
            else:
                season_dir = self._season_cache_dir(self.anime_folder_name or "unknown", target_season, create=False)
           
           # find matching file in season_dir
            if season_dir and os.path.isdir(season_dir):
                for fn in os.listdir(season_dir):
                    if not fn.lower().endswith(".srt"):
                        continue
                    s, e = self.extract_season_episode(fn)
                    if e == target_episode:
                        chosen = os.path.join(season_dir, fn)
                        try:
                            self._load_and_process(chosen)
                            self.current_season = target_season
                            self.current_episode = target_episode
                            self.season_dir = season_dir
                            self.srt_file = chosen
                            return self.current_season, self.current_episode
                        except Exception:
                            logger.exception("Failed to load cached subtitle: %s", chosen)
                            break  # fall back to remote if available

        # If we reach here we need to fetch remote_path (either was found above or we need to locate it)
        if remote_path is None:
            # Find remote path for target season/episode
            folders = self._search_subtitle_folders(self.github_owner, self.github_repo, self.github_ref, self.anime_folder_name)
            files = self._search_srt_files_in_folders(self.github_owner, self.github_repo, folders, target_season)
            if not files:
                return season, current
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
                    return season, current
            remote_path = chosen_remote
            season_files = files

        # download the remote_path into season cache
        season_dir = self._season_cache_dir(self.anime_folder_name or "unknown", target_season, create=True)
        filename = self.sanitize_filename(os.path.basename(remote_path))
        local_path = os.path.join(season_dir, filename)

        # blocking download of the required episode (so UI can show it)
        try:
            self._download_file(self.github_owner, self.github_repo, self.github_ref, remote_path, local_path)
            # verify file exists
            if not os.path.isfile(local_path):
                logger.error("Downloaded file missing: %s", local_path)
                return season, current
            # load and update state
            self._load_and_process(local_path)
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
            logger.exception("Failed to download or load remote episode: %s", remote_path)
            return season, current

        # kick off background downloads for remaining season files (if we have file list)
        try:
            if season_files:
                # convert remote paths to filenames to tell the async downloader which is current
                current_file = filename
                self.download_remaining_season_async(self.github_owner, self.github_repo, self.github_ref, season_files, current_file, season_dir, window = 15)
        except Exception:
            logger.exception("Failed to start async season download")

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
            success = self.load_local_srt(path)
            if not success:
                messagebox.showerror("Load failed", "Failed to load selected local SRT file.")

        def choose_remote():
            popup.destroy()
            url, season_hint, episode_hint = self.ask_remote_srt_with_hint()
            if not url:
                return
            success = self.load_remote_srt_url(url, hint=(season_hint, episode_hint))
            if not success:
                messagebox.showerror("Load failed", "Failed to load subtitle from the provided URL.")

        tk.Button(button_frame, text="Local File", width=15, command=choose_local).grid(row=0, column=0, padx=12)
        tk.Button(button_frame, text="Remote URL", width=15, command=choose_remote).grid(row=0, column=1, padx=12)

        popup.wait_window(popup)




# ---------------------- episode / season switching ----------------------


    # ----- New utility: reset internal state before loading a new SRT source -----
    def reset_state(self) -> None:
        """
        Reset runtime state that is specific to the currently loaded subtitle file/season.
        This makes loading a new local file or new remote URL deterministic.
        """
        # local/remote path info
        self.srt_file = None
        self.srt_dir = None
        self.season_dir = None

        # metadata
        self.is_movie = False
        self.title = None
        self.current_season = None
        self.current_episode = None
        self.total_duration = 0

        # subtitle contents / display
        self.subtitles = []
        self.display_data = []
        self.raw_subtitles = None
        self.max_width = None
        self.max_height = None
        
        self.using_local_folder = False
        self.source = None
        # remote cache for per-session lookups
        try:
            self._remote_files_cache.clear()
        except Exception:
            self._remote_files_cache = {}

        # github related (keep token)
        self.github_owner = None
        self.github_repo = None
        self.github_ref = None
        self.github_path = None










    # ----- New: load a remote GitHub URL (acts like startup) -----
    def load_remote_srt_url(self, url: str, hint: Optional[Tuple[Optional[int], Optional[int]]] = None) -> bool:
        """
        url: GitHub (or raw) URL pointing to a subtitle file (or folder). 
        hint: (season, episode) provided by user dialog — can be (None, None) to fall back to parsed values.
        """
        if not url:
            return False

        self.reset_state()
        self.using_local_folder = False
        self.source = "remote"

        parsed = self._parse_github_url(url)
        if not parsed or not parsed.get("owner") or not parsed.get("repo") or not parsed.get("path"):
            logger.error("Invalid GitHub URL parsed: %s", url)
            return False

        owner, repo, ref, path, file_name, season_num, episode_num, anime_name, remote_folder = self.extract_episode_metadata(parsed)

        # Prefer user hint if provided
        hint_season, hint_episode = (None, None)
        if hint:
            hint_season, hint_episode = hint

        # If no user hint, fall back to parsed values
        season_to_use = hint_season or season_num or 1
        episode_to_use = hint_episode or episode_num or 1

        # Determine anime folder name (two folders under /subtitles/)
        detected_anime_name = anime_name or self._extract_anime_name_from_url(path) or os.path.basename(remote_folder) or owner
        self.anime_folder_name = detected_anime_name
        # persist definitive name and source URL
        if self.anime_folder_name:
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)

        # Save github reference
        self.update_current_github_reference(owner, repo, ref, path)

        # Find candidate subtitle folders for this anime
        try:
            candidate_folders = self._search_subtitle_folders(owner, repo, ref, self.anime_folder_name)
        except Exception:
            candidate_folders = []

        chosen_folder = None
        chosen_remote = None
        season_files = []

        # Search candidate folders for the requested season/episode (strong preference)
        for folder in candidate_folders:
            files = self._search_srt_files_in_folders(owner, repo, [folder], season=season_to_use)
            if not files:
                continue
            # find file matching requested episode
            for f in files:
                s, e = self.extract_season_episode(os.path.basename(f))
                if e == episode_to_use:
                    chosen_folder = folder
                    chosen_remote = f
                    season_files = files
                    break
            if chosen_folder:
                break

        # Fallback: inspect the folder from the provided URL directly
        if chosen_remote is None:
            try:
                direct_files = self._search_srt_files_in_folders(owner, repo, [remote_folder], season=season_to_use)
                if direct_files:
                    # prefer exact path if present in that list
                    if path in direct_files:
                        chosen_remote = path
                        season_files = direct_files
                        chosen_folder = remote_folder
                    else:
                        chosen_remote = direct_files[0]
                        season_files = direct_files
                        chosen_folder = remote_folder
            except Exception:
                pass

        # If still not found, try a looser search (all candidate folders for the season)
        if chosen_remote is None and not season_files:
            try:
                # gather season files across all candidate folders
                all_files = []
                for folder in candidate_folders:
                    all_files.extend(self._search_srt_files_in_folders(owner, repo, [folder], season=season_to_use))
                if all_files:
                    # choose the file with the closest episode (or first)
                    chosen_remote = None
                    min_diff = None
                    for f in all_files:
                        s,e = self.extract_season_episode(os.path.basename(f))
                        if e is None:
                            continue
                        diff = abs(e - episode_to_use)
                        if min_diff is None or diff < min_diff:
                            min_diff = diff
                            chosen_remote = f
                    season_files = all_files
            except Exception:
                pass

        if chosen_remote is None:
            logger.error("Could not locate remote file for season %s episode %s", season_to_use, episode_to_use)
            return False

        # Ensure cache folder uses the determined anime name and season
        self.current_season = season_to_use
        self.current_episode = episode_to_use
        self.season_dir = self._season_cache_dir(self.anime_folder_name, self.current_season, create=True)

        # blocking download of required episode (if not already present)
        filename = self.sanitize_filename(os.path.basename(chosen_remote))
        local_path = os.path.join(self.season_dir, filename)
        if not os.path.exists(local_path):
            try:
                self._download_file(owner, repo, ref, chosen_remote, local_path)
            except Exception:
                logger.exception("Failed to download requested episode: %s", chosen_remote)
                return False

        if not os.path.isfile(local_path):
            logger.error("Downloaded file missing after download: %s", local_path)
            return False

        # Update runtime state and persist last-used metadata
        self.srt_file = local_path
        self.config.set("LAST_SRT_FILE", self.srt_file)
        self.config.set("LAST_SEASON", self.current_season)
        self.config.set("LAST_EPISODE", self.current_episode)
        final_file_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{chosen_remote}"
        self.config.set("LAST_GITHUB_URL", final_file_url)
        if self.anime_folder_name:
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)

        try:
            self._load_and_process(self.srt_file)
        except Exception:
            logger.exception("Failed to load downloaded subtitle: %s", self.srt_file)
            return False

        # Start background download of the rest of the season from the chosen folder (if not already cached)
        try:
            if season_files:
                current_file = os.path.basename(self.srt_file)
                self.download_remaining_season_async(owner, repo, ref, season_files, self.sanitize_filename(current_file), self.season_dir, window=15)
        except Exception:
            logger.exception("Failed to schedule async season downloads")

        return True


    # ----- New: load a local SRT file (path)-----
    def load_local_srt(self, path: str) -> bool:
        """
        Load a local .srt file selected by the user.
        - Resets state
        - Determines season/episode from filename if possible
        - Copies available .srt files from the selected file's folder into cache (SeasonX folder)
        - Loads & processes the selected file into runtime state
        Returns True on success.
        """
        if not path or not os.path.isfile(path):
            logger.error("Local SRT path not found: %s", path)
            return False

        self.reset_state()

        srt_dir = os.path.dirname(path)
        files = [f for f in os.listdir(srt_dir) if f.lower().endswith('.srt')]
        if not files:
            logger.error("No .srt files found in folder: %s", srt_dir)
            return False
        self.using_local_folder = True
        self.source = "local"

        self.srt_dir = srt_dir
        self._srt_file_list = files
        self.srt_file = path
        self.season_dir = srt_dir

        filename = os.path.basename(path)
        s_num, e_num = self.extract_season_episode(filename)
        if s_num is None:
            self.current_season = 1 if e_num is not None else None
            self.is_movie = (e_num is None)
        else:
            self.current_season = s_num

        self.current_episode = e_num if e_num is not None else (1 if self.current_season is not None else None)

        self.config.set("LAST_SRT_FILE", self.srt_file)

        try:
            self._load_and_process(self.srt_file)
        except Exception:
            logger.exception("Failed to load local srt: %s", self.srt_file)
            return False
        
        return True
