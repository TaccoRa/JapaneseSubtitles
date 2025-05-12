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

        match_season = re.search(r'S(\d+)', os.path.basename(self.srt_file), re.IGNORECASE)
        self.current_season = int(match_season.group(1)) if match_season else 1

        self.srt_dir = os.path.dirname(self.srt_file)
        self._srt_file_list = [
            f for f in os.listdir(self.srt_dir)
            if f.lower().endswith('.srt')
        ]

        self.subtitles = self.load_subtitles(self.srt_file)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]
    
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
        return path

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








    def get_skip_value(self) -> float:
        return float(self.config.get('DEFAULT_SKIP'))

    def get_total_duration(self) -> float:
        return max(sub.end.total_seconds() for sub in self.subtitles)


    def set_episode(self, season: int, episode: int) -> None:
        pat1 = re.compile(rf'S0*{season}E0*{episode}\b', re.IGNORECASE)
        for file in self._srt_file_list:
            if pat1.search(file):
                new_file = file
        pat2 = re.compile(rf'E0*{episode}\b', re.IGNORECASE)
        for file in self._srt_file_list:
            if pat2.search(file):
                new_file = file
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
    

    def update_srt_file_in_config(self, new_season: int, new_episode: int) -> None:
        cfg_path = self.config.path
        with open(cfg_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        old = data.get("LAST_SRT_FILE", "")
        def repl(m):
            s_str = m.group(1).zfill(len(m.group(1)))
            e_str = str(new_episode).zfill(len(m.group(2)))
            return f"S{s_str}E{e_str}"

        data["LAST_SRT_FILE"] = re.sub(r'S(\d+)E(\d+)', repl, old, count=1)
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)