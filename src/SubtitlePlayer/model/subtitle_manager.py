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
        self.local_srt_path = None


        self.srt_file = None
        self.srt_dir = None
        self.is_movie = False
        self.title = None
        self.season = None
        self.episode = None
        self.total_duration = 0
        self.display_data = []
        self.raw_subtitles = None
        self.max_width = None
        self.max_height = None
        self.cache_dir = None



        #url points to last used episode
        init_url = self._parse_github_url(self.config.get("LAST_GITHUB_URL"))
        if not init_url:
            init_url = self.ask_remote_srt_file()
        if not init_url:
            self.local_srt_path = self.ask_local_srt_file()
        if self.local_srt_path:
            self._load_local_srt(self.local_srt_path)

        self.github_token = os.environ.get("GITHUB_TOKEN")
        (owner, repo, ref, path,
         file_name, season_num, episode_num,
         anime_name, remote_folder) = self.extract_episode_metadata(init_url)
        self.update_current_github_reference(owner, repo, ref, path)

        #last anime name should always be the name of the folder where s1e1 is
        self.anime_folder_name =  self.config.get("LAST_ANIME_NAME")
        if self.anime_folder_name not in anime_name:
            logger.critical("Current anime doesnt match last saved anime")
            self.anime_folder_name = anime_name
            #maybe later add ask user for anime name
            #init_season1_name = self._ask_anime_name()
            #function to check for season 1?

        season_dir = self._season_cache_dir(anime_name, season_num)
        self.srt_file = self.download_current_episode(owner, repo, ref, path, season_dir)
        self.srt_dir = season_dir
        # self._load_srt_file(self.srt_file)

        self.srt_file = self.config.get("LAST_SRT_FILE")
        self._load_and_process(self.srt_file)



        season_files = self._search_srt_files_in_folders(owner, repo, [remote_folder], season_num)
        self.download_remaining_season_async(owner, repo, ref, season_files, file_name, season_dir)




        #download season in cache
        #figure out anime name/folder name of first season
        #next steps....




        # self._register_cache_cleanup()
        # self.local_srt_dir = self._get_cache_base_dir()


        # self.init_srt_file_path = self.get_initial_srt_or_prompt(init_url) #try downloading srt season from url if fail ask for url if fail ask for lokal file
        #cache this season
        # self.load_srt(self.init_srt_file_path)

        # atexit.register(self._cleanup_created_caches)


        #for startup testing for now


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

    def download_current_episode(self, owner, repo, ref, path, season_dir):
        file_name = self.sanitize_filename(os.path.basename(path))
        local_path = os.path.join(season_dir, file_name)
        self._download_file(owner, repo, ref, path, local_path)

        # local_path = os.path.join(season_dir, os.path.basename(path))
        # self._download_file(owner, repo, ref, path, local_path)
        return local_path
    
    def sanitize_filename(self,filename: str) -> str:
        # Replace invalid Windows characters with underscore
        return re.sub(r'[<>:"/\\|?*]', '_', filename)

    def download_remaining_season_async(self, owner, repo, ref, season_files, current_file, season_dir):
        for remote_path in season_files:
            filename = self.sanitize_filename(os.path.basename(remote_path))
            local_path = os.path.join(season_dir, filename)

            if filename == current_file:
                continue
            local_path = os.path.join(season_dir, filename)
            threading.Thread(
                target=self._download_file,
                args=(owner, repo, ref, remote_path, local_path),
                daemon=True
            ).start()

    def update_current_github_reference(self, owner, repo, ref, path):
        self.github_owner = owner
        self.github_repo  = repo
        self.github_ref   = ref
        self.github_path  = path

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








    def _load_local_srt(self, path):
        self.config.set("LAST_SRT_FILE", path)
        ...

    def download_initial_file(self, owner, repo, ref, path, season_dir):
        local_path = os.path.join(season_dir, os.path.basename(path))
        self._download_file(owner, repo, ref, path, local_path)
        return local_path


    def download_season_from_url(self, episode_url):
        owner = episode_url["owner"]
        repo = episode_url["repo"]
        ref = episode_url["ref"]
        path = episode_url["path"]

        # Extract file name and season/episode
        file_name = path.split("/")[-1]
        season_num, episode_num = self.extract_season_episode(file_name)
        if season_num is None and episode_num is None:
            self.is_movie = True
        anime_name = self._extract_anime_name_from_url(path)

        season_dir = self._season_cache_dir(anime_name,season_num) #local directory
        remote_folder =  ["/".join(path.split("/")[:-1])] #remote directory

        season_files = self._search_srt_files_in_folders(owner, repo, remote_folder, season_num) #remote files in remote directory
        
        threads = []
        for entry in season_files:
            entry_filename = os.path.basename(entry)

            if entry_filename == file_name:
                continue
            remote_path = entry
            local_path = os.path.join(season_dir, entry_filename)

            t = threading.Thread(
                target=self._download_file,
                args=(owner, repo, ref, remote_path, local_path),
                daemon=True
            )
            t.start()
            threads.append(t)

        # return season_dir #do i need to return something?



    # #get file from remote url?
    # def get_initial_srt_or_prompt(self, url) -> None: 
    #     local = self._download_season_from_github(url)
    #     if local:
    #       return local
    #     remote_url = self.ask_remote_srt_file()
    #     if remote_url:
    #         return remote_url
    #     return self.ask_local_srt_file()


        # if parsed.get('is_file') and parsed.get('filename', '').lower().endswith('.srt'):
        #     folder = os.path.dirname(remote_path)
        #     downloaded = self._download_folder_srts(owner, repo, ref, folder)
        #     return downloaded[0] if downloaded else None
        
        # folders = self._search_subtitle_folders(owner, repo, ref, self.github_token, self.anime_folder_name)
        # if not folders:
        #     logger.debug("No subtitle folders discovered for query: %s", self.anime_folder_name)
        #     return None
        
        # for folder in folders:
        #     downloaded = self._download_folder_srts(owner, repo, ref, folder)
        #     if downloaded:
        #     # pick first downloaded srt as representative
        #         return downloaded[0]
        # return None



# ---------------------- helpers: cache dirs ----------------------
    def _season_cache_dir(self, anime_folder_name: str, season: int) -> str: #create cache for current season
        base = self._get_cache_base_dir()
        anime_dir = os.path.join(base, anime_folder_name)
        os.makedirs(anime_dir, exist_ok=True)
        season_dir = os.path.join(anime_dir, f"Season{season}")
        os.makedirs(season_dir, exist_ok=True)
        return season_dir
    
    def _get_cache_base_dir(self) -> str: #get current base directory
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
        base = os.path.join(project_root, "cache_github")
        os.makedirs(base, exist_ok=True)
        return base

    def _cached_episode_numbers(self, anime_folder_name: str, season: int) -> List[int]: #how many episodes in season folder local
        season_dir = os.path.join(self._get_cache_base_dir(), anime_folder_name, f"season{season}")
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

    def _download_folder_srts(self, owner: str, repo: str, ref: str, folder_path: str) -> List[str]:
        # Downloads all .srt files from a repo folder into a season cache dir determined by filenames
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{folder_path}"
        headers = {"Accept": "application/vnd.github.v3+json", "Authorization": f"token {self.github_token}"}
        try:
            resp = requests.get(url, headers=headers, params={'ref': ref} if ref else None, timeout=15)
            if resp.status_code != 200:
                logger.debug("Failed to list folder %s (%s)", folder_path, resp.status_code)
                return []
            items = resp.json()
            downloaded = []
            for it in items:
                if it.get('type') != 'file':
                    continue
                name = it.get('name', '')
                if not name.lower().endswith('.srt'):
                    continue
                s, e = self.extract_season_episode(name)
                season_num = s or 1
                dest_dir = self._season_cache_dir(self.anime_folder_name or 'unknown', season_num)
                local = self._download_file(self.github_owner, self.github_repo, self.github_ref, it.get('path'), dest_dir)
                if local:
                    downloaded.append(local)
            return downloaded
        except Exception:
            logger.exception("Failed to download folder: %s", folder_path)
        return []

    
# ---------------------- GitHub searching / downloading ----------------------




# ---------------------- Manual file selection ----------------------
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
# ---------------------- Manual file selection ----------------------




# ---------------------- episode / season switching ----------------------
    def _download_season_by_number(self, season: int) -> Optional[str]:
        """Try to find S{season}E1 and download its folder. Return path to S{season}E1 local file if found."""
        if not (self.github_owner and self.github_repo and self.anime_folder_name):
            logger.debug("GitHub repo not initialized; cannot download season %s", season)
            return None
        folders = self._search_subtitle_folders(self.github_owner, self.github_repo, self.github_ref, self.github_token, self.anime_folder_name)
        if not folders:
            return None
        matches = self._search_srt_files_in_folders(self.github_owner, self.github_repo, self.github_token, folders, season=season)
        if not matches:
            return None
        # choose the first match's folder
        folder = os.path.dirname(matches[0])
        downloaded = self._download_folder_srts(self.github_owner, self.github_repo, self.github_ref, folder)
        # find s1e1 file in downloaded
        for p in downloaded:
            s, e = self.extract_season_episode(os.path.basename(p))
            if s == season and e == 1:
                return p
        return downloaded[0] if downloaded else None

    def change_episode(self, action: str, raw): 
        #action: "dec","inc","set"; raw: if user set episode(int); sets new episode and returns target
        last_episode = 0
        if self.anime_folder_name:
            last_episode = max(self._cached_episode_numbers(self.anime_folder_name, season))
            
        season = self.current_season
        current = self.current_episode

        if action == 'dec':
            if season == 1 and current <= 1: #cannot fo below S1E1
                return season,current
            if current > 1:
                target_episode = current - 1
            else: #switch to season before and download this season to cache
                target_season = season - 1
                target_episode = self._cached_episode_numbers(self.anime_folder_name, target_season)
                if not target_episode:
                    p = self._download_season_by_number(target_episode)
                    if p:
                        self._load_and_process(p)
                        prev_eps = self._cached_episode_numbers(self.anime_folder_name, target_episode)
                    if prev_eps:
                        target_season = target_episode
                        target_episode = max(prev_eps)
                    else:
                        return season, current
                
        elif action == 'inc':
            #check if end of season = no more episodes > last episode
            if last_episode and current + 1 > last_episode:
                target_season = season + 1
                p = self._download_season_by_number(target_season)
                if p:
                    # set to next season episode 1
                    self._load_and_process(p)
                    self.current_season = target_season
                    self.current_episode = 1
                    return self.current_season, self.current_episode
                else:
                    return season, current
            else:#if not end of season:
                target_episode = current + 1

        elif action == 'set': #manually written inside the settings episode entry raw only > 0
            if raw is None or raw <= 0:
                return season, current
            if last_episode and raw > last_episode:
                return season, current
            target_episode = int(raw) #posibility to switch to certain episode in current season

        if target_season != season:
            # load the season folder where this episode lives
            # attempt to download season if missing
            p = self._download_season_by_number(target_season)
            if p:
                # load the specific episode file if present in cache
                # try to find file matching episode
                eps = self._cached_episode_numbers(self.anime_folder_name, target_season)
                if eps:
                    if target_episode is None:
                        target_episode = max(eps)
                    # find filename
                    season_dir = self._season_cache_dir(self.anime_folder_name, target_season)
                    candidates = [os.path.join(season_dir, f) for f in os.listdir(season_dir) if f.lower().endswith('.srt')]
                    # pick file matching episode number
                    chosen = None
                    for c in candidates:
                        s, e = self.extract_season_episode(os.path.basename(c))
                        if e == target_episode:
                            chosen = c
                            break
                    if chosen:
                        self._load_and_process(chosen)
                        return self.current_season, self.current_episode
        else:
            # same season: find the file in cache
            season_dir = self._season_cache_dir(self.anime_folder_name or 'unknown', season)
            candidates = [os.path.join(season_dir, f) for f in os.listdir(season_dir) if f.lower().endswith('.srt')]
            chosen = None
            for c in candidates:
                s, e = self.extract_season_episode(os.path.basename(c))
                if e == target_episode:
                    chosen = c
                    break
            if chosen:
                self._load_and_process(chosen)
                return self.current_season, self.current_episode


        return (target_season,target_episode) #return current episode for settings display
# ---------------------- episode / season switching ----------------------

    # def get_total_duration(self) -> float:
    #     return self.total_duration
    
    def get_title(self)-> Optional[str]:
        return self.anime_folder_name
    
    def get_episode_info(self) -> Tuple[Optional[int], Optional[int]]:
        return (self.season, self.episode)


    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN.sub('', text)
        cleaned = self.RUBY_PATTERN.sub(r'\1«\2»', cleaned)
        cleaned = regex.sub(r'[（(].*?[）)]', '', cleaned)
        cleaned = cleaned.replace('«', '(').replace('»', ')')
        return cleaned.replace('&lrm;', '').replace('\u200e', '').strip()


    def calculate_geometry(self) -> dict:
        font = tkFont.Font(family=self.config.get("SUBTITLE_FONT"),size=self.config.get("SUBTITLE_FONT_SIZE"),weight="bold")
        max_width = 0
        for clean, time, *_rest in self.display_data:
            base_text = regex.sub(r'\p{Han}+\([^)]+\)', lambda m: regex.match(r'(\p{Han}+)', m.group()).group(), clean)
            for line in base_text.splitlines():
                width = font.measure(line)
                if max_width < width:
                    max_width = width
        #             biggest_line = line
        #             start_time = time
        # print(format_time(start_time),": ",biggest_line)
        line_height = font.metrics("linespace")
        ruby_height = int(line_height * 0.6)
        pad_x = 5
        total_height = ruby_height * 2 + line_height * 2
        total_width  = max_width + 2 * pad_x

        return {"max_height": total_height, "max_width":   total_width}



    # def _cleanup_created_caches(self) -> None:
    #     # Remove any cache dirs this manager created at runtime.
    #     for d in list(self._created_cache_dirs):
    #         try:
    #             if os.path.exists(d):
    #                 shutil.rmtree(d)
    #                 logger.debug("Removed runtime cache dir: %s", d)
    #         except Exception:
    #             logger.exception("Failed to remove cache dir at exit: %s", d)
    #     self._created_cache_dirs.clear()



    # def _load_and_process(self, path: str) -> None:
    #     if not path or not os.path.isfile(path):
    #         raise FileNotFoundError(path)
    #     # detect encoding
    #     with open(path, 'rb') as fh:
    #         raw = fh.read()
    #     enc = chardet.detect(raw).get('encoding') or 'utf-8'
    #     try:
    #         s = raw.decode(enc, errors='replace')
    #     except Exception:
    #         s = raw.decode('utf-8', errors='replace')
    #     subs = list(srt.parse(s))
    #     if not subs:
    #         raise ValueError('No subtitles parsed')
    #     self.raw_subtitles = subs
    #     self.srt_file = path
    #     self.srt_dir = os.path.dirname(path)


    #     # extract season/episode/title from filename
    #     fname = os.path.basename(path)
    #     s_num, e_num = self.extract_season_episode(fname)
    #     self.season = s_num or self.season or 1
    #     self.episode = e_num or self.episode or 1


    #     # build display_data: (clean_text, start, end)
    #     display = []
    #     for sub in subs:
    #         text = self._clean_text(sub.content)
    #         display.append((text, sub.start.total_seconds(), sub.end.total_seconds()))
    #     self.display_data = display
    #     self.total_duration = subs[-1].end.total_seconds()


    #     # persist last used srt in config
    #     try:
    #         self.config.set("LAST_SRT_FILE", self.srt_file)
    #     except Exception:
    #         logger.debug("Unable to persist LAST_SRT_FILE in config")


    #     # set convenience attributes
    #     self.current_season = self.season
    #     self.current_episode = self.episode
















































    # def























        # if config.get("DEBUGGING"):
        #       self.srt_file = config.get("DEBUGGING_SRT_FILE")
        # else: self.srt_file = config.get("LAST_SRT_FILE")
        # self.srt_dir = os.path.dirname(self.srt_file) if self.srt_file else os.getcwd()
        # if not self.srt_file or not os.path.exists(self.srt_file):
        #     selected = self.ask_srt_file()
        #     if not selected:
        #         raise FileNotFoundError("No subtitle file selected.")
        #     self.srt_file = selected
        # self._load_and_process(self.srt_file)


    def ask_srt_file(self, path: Optional[str] = None) -> bool:
        # Prompt for file if not given
        if path is None:
            window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                parent=window,
                title="Select SRT File",
                initialdir=self.srt_dir,
                filetypes=[("SubRip files","*.srt"),("All Files","*.*")]
            )
            window.destroy()
            if not path: return False   
        return path

    def _load_and_process(self, path: str) -> None:
        # save to last file, create srt list, set season/episode, creates subtitle data
        self.srt_file = path
        self.config.set("LAST_SRT_FILE", path)
        self.srt_dir = os.path.dirname(path)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]
        filename = os.path.basename(self.srt_file)
        self.current_season = self._extract_number(self.SEASON_PATTERN, filename)
        self.current_episode = self._extract_number(self.EPISODE_PATTERN, filename)

        # get subtitles and start time
        with open(path, 'rb') as f:
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

    def get_total_duration(self) -> float:
        return self.subtitles[-1].end.total_seconds()

    # def calculate_geometry(self) -> dict:
    #     font = tkFont.Font(family=self.config.get("SUBTITLE_FONT"),size=self.config.get("SUBTITLE_FONT_SIZE"),weight="bold")
    #     max_width = 0
    #     for clean, time, *_rest in self.display_data:
    #         base_text = regex.sub(r'\p{Han}+\([^)]+\)', lambda m: regex.match(r'(\p{Han}+)', m.group()).group(), clean)
    #         for line in base_text.splitlines():
    #             width = font.measure(line)
    #             if max_width < width:
    #                 max_width = width
    #     #             biggest_line = line
    #     #             start_time = time
    #     # print(format_time(start_time),": ",biggest_line)
    #     line_height = font.metrics("linespace")
    #     ruby_height = int(line_height * 0.6)
    #     pad_x = 5
    #     total_height = ruby_height * 2 + line_height * 2
    #     total_width  = max_width + 2 * pad_x

    #     return {"max_height": total_height, "max_width":   total_width}

    def set_episode(self, season: int, episode: int) -> bool: #true if movie, false if nothing found, If found set season and episode and path
        if season is None and episode is None:
            self.current_season = None
            self.current_episode = None
            return True
        
        target_file = None
        full_pattern = re.compile(rf'S0*{season}E0*{episode}(?!\d)', re.IGNORECASE)
        for file in self._srt_file_list:
            if full_pattern.search(file):
                target_file = file
                break

        if not target_file:
            episode_only = re.compile(rf'E0*{episode}(?!\d)', re.IGNORECASE)
            for file in self._srt_file_list:
                if episode_only.search(file):
                    target_file = file
                    break
        if not target_file:
            return False
        
        season_found = self._extract_number(self.SEASON_PATTERN, target_file)
        episode_found = self._extract_number(self.EPISODE_PATTERN, target_file)
        if season_found is None or episode_found is None:
            return False

        self.current_season = season
        self.current_episode = episode

        full_path = os.path.join(self.srt_dir, target_file)
        self.srt_file = full_path
        self.config.set("LAST_SRT_FILE", self.srt_file)
        self._load_and_process(self.srt_file)
        return True
    
    # def _extract_number(self, pattern: re.Pattern, filename: str):
    #     match = pattern.search(filename)
    #     if match: return int(match.group(1))

    # def _clean_text(self, text: str) -> str:
    #     cleaned = self.CLEAN_PATTERN.sub('', text)
    #     cleaned = self.RUBY_PATTERN.sub(r'\1«\2»', cleaned)
    #     cleaned = regex.sub(r'[（(].*?[）)]', '', cleaned)
    #     cleaned = cleaned.replace('«', '(').replace('»', ')')
    #     return cleaned.replace('&lrm;', '').replace('\u200e', '').strip()

    # def _parse_ruby_segments(self, text: str) -> List[tuple[str, Optional[str]]]:
    #     segments: List[tuple[str, Optional[str]]] = []
    #     last = 0
    #     for m in self.RUBY_PATTERN.finditer(text):
    #         plain = text[last:m.start()].strip()
    #         if plain:
    #             segments.append((plain, None))
    #         segments.append((m.group(1), m.group(2)))
    #         last = m.end()
    #     tail = text[last:].strip()
    #     if tail:
    #         segments.append((tail, None))
    #     return segments