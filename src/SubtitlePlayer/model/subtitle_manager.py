import re
import os
import json
from typing import List, Optional

import srt
import chardet
import tkinter as tk
from tkinter import filedialog

from model.config_manager import ConfigManager

class SubtitleManager:

    CLEAN_PATTERN_1 = re.compile(r'\{\\an\d+\}')
    CLEAN_PATTERN_2 = re.compile(r'[（(].*?[）)]')
    SEASON_PATTERN = r'S(\d+)'
    EPISODE_PATTERN = r'E(\d+)'

    def __init__(self, config: ConfigManager) -> None:
        self.config = config

        # initialize srt_file
        if config.get("DEBUGGING"):
              self.srt_file = config.get("DEBUGGING_SRT_FILE")
        else: self.srt_file = config.get("LAST_SRT_FILE")
        if not self.srt_file:
            raise FileNotFoundError("No .srt file selected or provided")
        
        filename = os.path.basename(self.srt_file)
        self.current_season = self._extract_number(r'S(\d+)', filename)
        self.current_episode = self._extract_number(r'E(\d+)', filename)

        self.subtitles = self._load_subtitles(self.srt_file)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]

        self.srt_dir = os.path.dirname(self.srt_file)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]


    def _extract_number(self, pattern, filename, default=1):
        match = re.search(pattern, filename, re.IGNORECASE)
        return int(match.group(1)) if match else default

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN_1.sub('', text)
        cleaned = self.CLEAN_PATTERN_2.sub('', cleaned)
        cleaned = cleaned.replace('&lrm;', '').replace('\u200e', '').strip()
        return cleaned

    def _load_subtitles(self, srt_path: str) -> List[srt.Subtitle]:
        with open(srt_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        text = raw.decode(detected['encoding'] or 'utf-8', errors='replace')
        return list(srt.parse(text))
    
    def get_skip_value(self) -> float:
        return float(self.config.get('DEFAULT_SKIP'))

    def get_total_duration(self) -> float:
        return max(sub.end.total_seconds() for sub in self.subtitles)

    def set_episode(self, season: int, episode: int) -> None:
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
            print(f"No .srt found for S{season}E{episode}")
            return
        season_found = self._extract_number(self.SEASON_PATTERN, new_file, default=None)
        episode_found = self._extract_number(self.EPISODE_PATTERN, new_file, default=None)
        if season_found is None or episode_found is None:
            print("Selected file does not contain season or episode info. Skipping episode logic.")
            return
            
        self.current_season = season
        self.current_episode = episode

        new_path = os.path.join(self.srt_dir, new_file)
        self.srt_path = new_path
        self.config.set("LAST_SRT_FILE", new_path)
        self.subtitles = self._load_subtitles(new_path)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]


    def prompt_srt_file(self) -> Optional[str]:
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            parent=window,
            title="Select SRT File",
            initialdir=self.srt_dir,
            filetypes=[("SubRip files", "*.srt"), ("All Files", "*.*")]
        )
        window.destroy()
        if not path:
            return None
        if not path.lower().endswith('.srt'):
            raise ValueError("Selected file is not a .srt file")
        episode = self._extract_number(self.EPISODE_PATTERN, os.path.basename(path), default=None)
        season = self._extract_number(self.SEASON_PATTERN, os.path.basename(path), default=None)
        if episode is None or season is None:
            print("Selected file does not contain season or episode info. Skipping episode logic.")
            return None
        self.current_episode = episode
        self.current_season = season
        self.srt_file = path
        self.srt_dir = os.path.dirname(self.srt_file)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]
        self.config.set("LAST_SRT_FILE", path)
        return path
