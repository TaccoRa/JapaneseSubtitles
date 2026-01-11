# subtitle_manager.py
import os
import re
import shutil
import threading
import hashlib
import requests
import atexit
import shutil
from urllib.parse import quote
from urllib.parse import urlparse, unquote
from typing import List, Optional, Tuple, Dict

import regex
import srt
import chardet
import tkinter as tk
from tkinter import font as tkFont
from tkinter import filedialog, messagebox, simpledialog

from model.config_manager import ConfigManager

import logging
logger = logging.getLogger(__name__)


class SubtitleManager:

    CLEAN_PATTERN = re.compile(r'\{\\an\d+\}')
    SEASON_PATTERN = re.compile(r'S(\d+)', re.IGNORECASE)
    EPISODE_PATTERN = re.compile(r'E(\d+)', re.IGNORECASE)
    RUBY_PATTERN = regex.compile(r'(\p{Han}+)\(([^)]+)\)')
    SXXEXX_PATTERN = re.compile(r'[Ss](\d{1,2})[^\d]*[Ee](\d{1,4})')

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.url = None
        self.srt_file = None
        self.srt_dir = None
        self.is_movie = None
        self.title = None
        self.season = None
        self.episode = None
        self.total_duration = 0
        self.display_data = []
        self.raw_subtitles = None
        self.max_width = None
        self.max_height = None
        self.cache_dir = None

        self.local_srt_dir = self._get_cache_base_dir()
        url = self.config.get("LAST_GITHUB_URL").strip()
        self.init_srt_file_path = self.get_srt_files(url)
        # self.load_srt(self.init_srt_file_path)

        # atexit.register(self._cleanup_created_caches)



        #for startup testing for now
        self.srt_file = self.config.get("LAST_SRT_FILE")
        self._load_and_process(self.srt_file)


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

    app.py will call SubtitleManager() and initilize it and give it to the other modules. 
    During init: See above first run. 
    so controller will call: sub_manager.change_episode(action)

    conceptual: always remote, failsafe ask for local file or new url.
    Meaning: safe last github url, use this to get the season folder and cache it
    After app close delete cache.
    '''

    # def load_srt(self, path) -> None:
    #     self.get_srt_file()



    def get_srt_files(self, url) -> None: 
        self.srt_file = self._get_remote_srt(url.strip())
        if not self.srt_file:# ask for remote url
            remote_url = self.ask_remote_srt_file()
            self.srt_file = self._get_remote_srt(remote_url)
        if not self.srt_file: #last fail safe
            self.srt_file = self.ask_local_srt_file()
        return self.srt_file ##not sure if i should do it like this???


    def _get_remote_srt(self, url:str):
        '''
        parse through url to get owner, repo, ... and extracts folder name from the path to 
        search for current anime in repo and then searches in this folders for specific srt file
        with anime_name sxxexx and netflix or amazon etc...
        and if hit saves the other srt files of this season into the cache folder.
        '''
        parsed = self._parse_github_url(url)
        token = os.environ.get("GITHUB_TOKEN")
        print(token)
        owner = parsed.get('owner')
        repo = parsed.get('repo')
        ref = parsed.get('ref') or 'HEAD'
        remote_path = parsed.get('path')

        if not (owner and repo):
            logger.debug("Unsupported GitHub URL format. Cannot determine owner/repo.")
            self.srt_file = self.ask_remote_srt_file()


        folder_title = "shuumatsu no v" #for debugging
        # folder_title = self._extract_folder_name_from_url(remote_path)
        print("Folder_title: ",folder_title)
        if folder_title:
            folders = self._search_subtitle_folders(owner, repo, ref, token, folder_title)
            print("Found folders:", folders)

            files = self._search_srt_files_in_folders(owner, repo, token, folders)

            print("Found matching srt files:", files)

        #save all files from this folder if hit, in the local cache:
        ...

        # if files:
        #     return(files[0])


    def _parse_github_url(self, url: str) -> Dict[str, Optional[str]]:  
        """
        Parse common GitHub URL formats
        Returns dict with owner, repo, ref, path, is_file, filename
        """
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
    
    def _search_subtitle_folders(self, owner: str, repo: str, ref: str, token: Optional[str], query: str) -> List[str]:

        api_url = "https://api.github.com/search/code"

        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {token}"
        }
        params = {
            "q": f'repo:{owner}/{repo} {query}'
        }


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

        return (set(results))


    def _search_srt_files_in_folders(self, owner: str, repo: str, token: Optional[str],folders: List[str]) -> List[str]:

        headers = {
            "Accept": "application/vnd.github.v3+json",
        }
        if token:
            headers["Authorization"] = f"token {token}"

        # hardcoded episode for now
        season = 2
        episode = 1
        sxxexx_pattern = re.compile(
            rf"(?i)s0*{season}[^0-9]*e0*{episode}(?!\d)"
        )

        results: List[str] = []

        for folder in folders:
            url = f"https://api.github.com/repos/{owner}/{repo}/contents/{folder}"

            try:
                resp = requests.get(url, headers=headers, timeout=15)
                if resp.status_code != 200:
                    logger.debug(
                        "Skipping folder %s (HTTP %s)",
                        folder,
                        resp.status_code,
                    )
                    continue

                items = resp.json()
                if not isinstance(items, list):
                    continue

                for it in items:
                    if it.get("type") != "file":
                        continue

                    name = it.get("name", "")
                    lname = name.lower()
                    # print(name)
                    if not lname.endswith(".srt"):
                        continue
                    if not any(x in lname for x in ['amazon','netflix',"bandai", "Webrip"]):
                        continue
                    if not sxxexx_pattern.search(name):
                        continue

                    results.append(it.get("path"))

            except Exception:
                logger.exception("Failed to inspect folder: %s", folder)

        return results



    def _get_cache_base_dir(self) -> str:
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
        base = os.path.join(project_root, "cache_github")
        os.makedirs(base, exist_ok=True)
        return base

    def _extract_folder_name_from_url(self, remote_path: str) -> Optional[str]:
        """
        extract 'Name' (two levels under `subtitles`).
        """
        parts = remote_path.split("/")
        try:
            idx = parts.index("subtitles")
            return parts[idx + 2]  # folder 2 under /subtitles/
        except ValueError:
            return None
    
    def ask_local_srt_file(self) -> bool:
        # Need to add logger if the fieldialog is closed withput choosing file
        try:
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
        except Exception:
            logger.exception("SRT file selection failed")
            return None
        
    def ask_remote_srt_file(self) -> bool:
        try:
            window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
            url = simpledialog.askstring("Remote URL", "Enter GitHub subtitle URL:", parent=window)
            window.destroy()
            if not url or not url.strip():
                raise ValueError("No URL provided.")
            return self.load_srt(url)
        except Exception:
            logger.exception("SRT file download failed")
            return None

    def change_episode(self, action: str, raw): 
        #action: "dec","inc","set"; raw: if user set episode(int); sets new episode and returns target
        season = self.current_season
        current = self.current_episode
        last_episode = self.last_episode
        if action == 'dec':
            if season == 1 and current < 2: #if season1 and trying decrease at episode 1 do nothing
                return season,None
            if current == 1:
                target_season = target_season - 1
                target_episode = None #set after knowing what last episode is
            else:
                target_episode = current - 1
                
        elif action == 'inc':
            #check if end of season = no more episodes > last episode
            if current + 1 > last_episode:
                target_season = season + 1
                target_episode = 1
            else:#if not end of season:
                target_episode = current + 1
        elif action == 'set': #manually written inside the settings episode entry raw only > 0
            if raw > last_episode:
                return season, None
            target = int(raw) #posibility to switch to certain episode in current season

        #function to get the new_path with new season/episode if new season download new season
        new_path = ... 
        #function to set all the data from the new path
        ...    
        #or combine? set new path and all data in one function? but usable with startup and here?

        return (target_season,target_episode) #return current episode for settings display
        

    def get_total_duration(self) -> float:
        return self.subtitles[-1].end.total_seconds()
    
    def get_title(self):
        return self.title

    def get_episode_info(self):
        return (self.season, self.episode)


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











    def _parse_sxxexx_from_filename(self, name: str) -> Optional[Tuple[int,int]]:
        """
        Returns (season, episode) if pattern found in filename, else None.
        Accepts many formats: S01E02, s1e2, S01.E02, S01E002 etc.
        """
        m = self.SXXEXX_PATTERN.search(name)
        if not m:
            return None
        try:
            s = int(m.group(1))
            e = int(m.group(2))
            return (s, e)
        except Exception:
            return None
        
    def _extract_number(self, pattern: re.Pattern, filename: str):
        match = pattern.search(filename)
        if match: return int(match.group(1))

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN.sub('', text)
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


    # def ask_srt_file(self, path: Optional[str] = None) -> bool:
    #     # Prompt for file if not given
    #     if path is None:
    #         window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
    #         path = filedialog.askopenfilename(
    #             parent=window,
    #             title="Select SRT File",
    #             initialdir=self.srt_dir,
    #             filetypes=[("SubRip files","*.srt"),("All Files","*.*")]
    #         )
    #         window.destroy()
    #         if not path: return False   
    #     return path

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

    # def get_total_duration(self) -> float:
    #     return self.subtitles[-1].end.total_seconds()

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