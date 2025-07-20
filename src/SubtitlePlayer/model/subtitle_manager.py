import re
import os
import bisect
from typing import List, Optional
import regex

import srt
import chardet
import tkinter as tk
from tkinter import filedialog

from model.config_manager import ConfigManager

class SubtitleManager:

    CLEAN_PATTERN_1 = re.compile(r'\{\\an\d+\}')
    SEASON_PATTERN = re.compile(r'S(\d+)', re.IGNORECASE)
    EPISODE_PATTERN = re.compile(r'E(\d+)', re.IGNORECASE)


    def __init__(self, config: ConfigManager) -> None:
        self.config = config

        if config.get("DEBUGGING"):
              self.srt_file = config.get("DEBUGGING_SRT_FILE")
        else: self.srt_file = config.get("LAST_SRT_FILE")
        self.srt_dir = os.path.dirname(self.srt_file) if self.srt_file else os.getcwd()
        if not self.srt_file or not os.path.exists(self.srt_file):
            selected = self.load_srt_file()
            if not selected:
                raise FileNotFoundError("No subtitle file selected.")
            self.srt_file = selected

        filename = os.path.basename(self.srt_file)
        self.srt_dir = os.path.dirname(self.srt_file)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]

        self.current_season = self._extract_number(self.SEASON_PATTERN, filename, default=None)
        self.current_episode = self._extract_number(self.EPISODE_PATTERN, filename, default=None)

        self._load_and_process(self.srt_file)

    def load_srt_file(self, path: Optional[str] = None) -> bool:
        # Prompt for file if not given
        if path is None:
            if not os.path.isdir(self.srt_dir):
                self.srt_dir = os.getcwd()
            window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                parent=window,
                title="Select SRT File",
                initialdir=self.srt_dir,
                filetypes=[("SubRip files","*.srt"),("All Files","*.*")]
            )
            window.destroy()
            if not path: return False

        # Stash path + update config + directory list
        self.srt_file = path
        self.config.set("LAST_SRT_FILE", path)
        self.srt_dir = os.path.dirname(path)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]

        # Extract season/episode from filename
        filename = os.path.basename(path)
        self.current_season  = self._extract_number(self.SEASON_PATTERN, filename)
        self.current_episode = self._extract_number(self.EPISODE_PATTERN, filename)
        self._load_and_process(path)
        
        return path

    def set_episode(self, season: int, episode: int) -> bool:
        # If both are None, treat as movie
        if season is None and episode is None:
            self.current_season = None
            self.current_episode = None
            return True

        new_file = None
        pat1 = re.compile(rf'S0*{season}E0*{episode}(?!\d)', re.IGNORECASE)
        for file in self._srt_file_list:
            if pat1.search(file):
                new_file = file
                break
        if not new_file:
            pat2 = re.compile(rf'E0*{episode}(?!\d)', re.IGNORECASE)
            for file in self._srt_file_list:
                if pat2.search(file):
                    new_file = file
                    break
        if not new_file:
            return False
        season_found = self._extract_number(self.SEASON_PATTERN, new_file, default=None)
        episode_found = self._extract_number(self.EPISODE_PATTERN, new_file, default=None)
        if season_found is None or episode_found is None:
            return False

        self.current_season = season
        self.current_episode = episode

        new_path = os.path.join(self.srt_dir, new_file)
        self.config.set("LAST_SRT_FILE", new_path)
        self._load_and_process(new_path)
        return True

    def get_total_duration(self) -> float:
        return max(sub.end.total_seconds() for sub in self.subtitles)
    
    def _extract_number(self, pattern: re.Pattern, filename: str, default=None):
        match = pattern.search(filename)
        return int(match.group(1)) if match else default


    def _load_and_process(self, path: str) -> None:
        with open(path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        text = raw.decode(detected['encoding'] or 'utf-8', errors='replace')
        self.subtitles = list(srt.parse(text))

        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        
        self.start_times = [s.start.total_seconds() for s in self.subtitles]

        self.display_data = []

        for clean in self.cleaned_subtitles:# split into up to two nonempty lines
            lines = [l for l in clean.splitlines() if l.strip()]
            if not lines:
                top, bottom = [], []
            elif len(lines) == 1:
                top, bottom = [], self._parse_ruby_segments(lines[0])
            else:
                top = self._parse_ruby_segments(lines[0])
                bottom = self._parse_ruby_segments(lines[1])

            self.display_data.append((clean, top, bottom))

    def _parse_ruby_segments(self, text: str) -> List[tuple[str, Optional[str]]]:
        segments: List[tuple[str, Optional[str]]] = []
        pat = regex.compile(r'(\p{Han}+)\(([^)]+)\)')
        last = 0
        for m in pat.finditer(text):
            plain = text[last:m.start()].strip()
            if plain:
                segments.append((plain, None))
            segments.append((m.group(1), m.group(2)))
            last = m.end()
        tail = text[last:].strip()
        if tail:
            segments.append((tail, None))
        return segments
    
    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN_1.sub('', text)
        cleaned = regex.sub(r'(\p{Han}+)\(([^)]+)\)', r'\1«\2»', cleaned)
        cleaned = regex.sub(r'[（(].*?[）)]', '', cleaned)
        cleaned = cleaned.replace('«', '(').replace('»', ')')
        return cleaned.replace('&lrm;','').replace('\u200e','').strip()
