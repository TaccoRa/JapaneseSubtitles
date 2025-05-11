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
    
    def __init__(self, srt_path: Optional[str], config: ConfigManager) -> None:
        self.config = config
        self.srt_path = srt_path or self.prompt_srt_file()
        if not self.srt_path:
            raise FileNotFoundError("No .srt file selected or provided")

        m = re.search(r'S(\d+)', os.path.basename(self.srt_path), re.IGNORECASE)
        self.current_season = int(m.group(1)) if m else 1

        self.srt_dir = os.path.dirname(self.srt_path)
        self._srt_file_list = [
            f for f in os.listdir(self.srt_dir)
            if f.lower().endswith('.srt')
        ]

        self.subtitles = self.load_subtitles(srt_path)
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


    def switch_to(self, season: int, episode: int) -> None:
        new_fn = self._find_srt_for(season, episode)
        if not new_fn:
            raise FileNotFoundError(f"No .srt found for S{season}E{episode}")
        
        self.current_season = season
        new_path = os.path.join(self.srt_dir, new_fn)
        self.srt_path = new_path
        self.subtitles = self.load_subtitles(new_path)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]
    
    def _find_srt_for(self, season: int, episode: int):
        pat1 = re.compile(rf'S0*{season}E0*{episode}\b', re.IGNORECASE)
        for fn in self._srt_file_list:
            if pat1.search(fn):
                return fn
        pat2 = re.compile(rf'E0*{episode}\b', re.IGNORECASE)
        for fn in self._srt_file_list:
            if pat2.search(fn):
                return fn
        return None
    

    def update_debug_srt_file_in_config(self, new_season: int, new_episode: int) -> None:
        cfg_path = self.config.path
        with open(cfg_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        old = data.get("DEBUG_SRT_FILE", "")
        def repl(m):
            s_str = m.group(1).zfill(len(m.group(1)))
            e_str = str(new_episode).zfill(len(m.group(2)))
            return f"S{s_str}E{e_str}"

        data["DEBUG_SRT_FILE"] = re.sub(r'S(\d+)E(\d+)', repl, old, count=1)
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)