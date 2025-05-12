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
    
    def __init__(self, config: ConfigManager) -> None:
        self.config = config

        if config.get("DEBUGGING"):
              self.srt_file = config.get("DEBUGGING_SRT_FILE")
        else: self.srt_file = config.get("LAST_SRT_FILE")
        if not self.srt_file:
            raise FileNotFoundError("No .srt file selected or provided")
        
        filename = os.path.basename(self.srt_file)
        self.current_season = self._extract_number(r'S(\d+)', filename)
        self.current_episode = self._extract_number(r'E(\d+)', filename)

        self.subtitles = self.load_subtitles(self.srt_file)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]

        self.srt_dir = os.path.dirname(self.srt_file)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]

#—————— Only used in subtitle_manager.py ————————————————————————————
    def _extract_number(self, pattern, filename, default=1):
        match = re.search(pattern, filename, re.IGNORECASE)
        return int(match.group(1)) if match else default

    def load_subtitles(self, srt_path: str) -> List[srt.Subtitle]:
        with open(srt_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        text = raw.decode(detected['encoding'] or 'utf-8', errors='replace')
        return list(srt.parse(text))

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN_1.sub('', text)
        cleaned = self.CLEAN_PATTERN_2.sub('', cleaned)
        cleaned = cleaned.replace('&lrm;', '').replace('\u200e', '').strip()
        return cleaned

#—————— Used in other ————————————————————————————
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
            raise FileNotFoundError(f"No .srt found for S{season}E{episode}")
        
        self.current_season = season
        self.current_episode = episode

        new_path = os.path.join(self.srt_dir, new_file)
        self.srt_path = new_path

        self.subtitles = self.load_subtitles(new_path)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]
        self.config.set("LAST_SRT_FILE", new_path)

    def prompt_srt_file(self) -> Optional[str]:
        window = tk.Tk()
        window.withdraw()
        window.attributes("-topmost", True)
        path = filedialog.askopenfilename(
            parent=window,
            title="Select SRT File",
            filetypes=[("SubRip files", "*.srt"), ("All Files", "*.*")]
        )
        window.destroy()
        self.current_episode = self._extract_number(r'E(\d+)', os.path.basename(path))
        self.current_season = self._extract_number(r'S(\d+)', os.path.basename(path))
        self.srt_file = path
        self.srt_dir = os.path.dirname(self.srt_file)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]
        self.config.set("LAST_SRT_FILE", path)
        return path