import re
import os
import bisect
from typing import List, Optional

import srt
import chardet
from PyQt5.QtWidgets import QFileDialog, QWidget

from model.config_manager import ConfigManager

class SubtitleManager:

    CLEAN_PATTERN_1 = re.compile(r'\{\\an\d+\}')
    CLEAN_PATTERN_2 = re.compile(r'[（(].*?[）)]')
    SEASON_PATTERN = r'S(\d+)'
    EPISODE_PATTERN = r'E(\d+)'

    def __init__(self, config: ConfigManager, parent_widget: Optional[QWidget] = None) -> None:
        self.config = config
        self.parent = parent_widget

        if config.get("DEBUGGING"):
              self.srt_file = config.get("DEBUGGING_SRT_FILE")
        else: self.srt_file = config.get("LAST_SRT_FILE")
        if not self.srt_file:
            selected = self.prompt_srt_file()
            if not selected:
                raise FileNotFoundError("No subtitle file selected.")

            
        filename = os.path.basename(self.srt_file)
        self.srt_dir = os.path.dirname(self.srt_file)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]

        self.current_season = self._extract_number(self.SEASON_PATTERN, filename, default=None)
        self.current_episode = self._extract_number(self.EPISODE_PATTERN, filename, default=None)

        self._load_and_process(self.srt_file)

    def prompt_srt_file(self) -> Optional[str]:
        dialog = QFileDialog(self.parent)
        dialog.setWindowTitle("Select SRT File")
        dialog.setNameFilters(["SubRip files (*.srt)", "All Files (*)"])
        if self.srt_dir:
            dialog.setDirectory(self.srt_dir)
        if dialog.exec_():
            path = dialog.selectedFiles()[0]
        else:
            return None

        if not path.lower().endswith('.srt'):
            raise ValueError("Selected file is not a .srt file")

        self.srt_file = path
        self.srt_dir = os.path.dirname(path)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]
        self.config.set("LAST_SRT_FILE", path)

        season = self._extract_number(self.SEASON_PATTERN, os.path.basename(path))
        episode = self._extract_number(self.EPISODE_PATTERN, os.path.basename(path))
        if season is None or episode is None:
            self.current_season = None
            self.current_episode = None
        else:
            self.current_season = season
            self.current_episode = episode

        self._load_and_process(path)
        return path

    def load_srt_file(self, path: str) -> None:
        self.srt_file = path
        filename = os.path.basename(path)
        self.current_season = self._extract_number(self.SEASON_PATTERN, filename, default=None)
        self.current_episode = self._extract_number(self.EPISODE_PATTERN, filename, default=None)
        self._load_and_process(path)
        self.srt_dir = os.path.dirname(path)
        self._srt_file_list = [f for f in os.listdir(self.srt_dir) if f.lower().endswith('.srt')]

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

    def get_subtitle_at(self, time: float, offset: float = 0.0) -> str:
        eff = time - offset
        idx = bisect.bisect_right(self.start_times, eff) - 1
        return self.cleaned_subtitles[idx] if idx >= 0 else ""  

    def _extract_number(self, pattern, filename, default=None):
        match = re.search(pattern, filename, re.IGNORECASE)
        return int(match.group(1)) if match else default
    
    def _load_subtitles(self, srt_path: str) -> List[srt.Subtitle]:
        with open(srt_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        text = raw.decode(detected['encoding'] or 'utf-8', errors='replace')
        return list(srt.parse(text))
    
    def _load_and_process(self, path: str) -> None:
        self.subtitles = self._load_subtitles(path)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN_1.sub('', text)
        cleaned = self.CLEAN_PATTERN_2.sub('', cleaned)
        cleaned = cleaned.replace('&lrm;', '').replace('\u200e', '').strip()
        return cleaned
