import re
import os

import json
import srt
import chardet
from config_manager import ConfigManager

class SubtitleManager:

    CLEAN_PATTERN_1 = re.compile(r'\{\\an\d+\}')
    CLEAN_PATTERN_2 = re.compile(r'[（(].*?[）)]')
    
    def __init__(self, srt_path: str, config: ConfigManager) -> None:
        self.srt_path = srt_path
        self.subtitles = self.load_subtitles(srt_path)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.start_times = [s.start.total_seconds() for s in self.subtitles]
        self.config = config
        self.default_skip = config.get('DEFAULT_SKIP')



    def load_subtitles(self, srt_path):
        with open(srt_path, 'rb') as f:
            raw_data = f.read()
        detected = chardet.detect(raw_data)
        encoding = detected['encoding']
        srt_content = raw_data.decode(encoding)
        return list(srt.parse(srt_content))
    
    def get_total_duration(self) -> float:
        return max(sub.end.total_seconds() for sub in self.subtitles)

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN_1.sub('', text)
        cleaned = self.CLEAN_PATTERN_2.sub('', cleaned)
        cleaned = cleaned.replace('&lrm;', '').replace('\u200e', '')
        return cleaned.strip()
    
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
    
    def parse_time_value(self, text: str) -> float:
        text = text.strip()
        if ":" in text:
            parts = text.split(":")
            if len(parts) == 2:
                try:
                    minutes = int(parts[0])
                    seconds = int(parts[1])
                    return minutes * 60 + seconds
                except ValueError:
                    return self.default_skip
            else:
                return self.default_skip
        else:
            if text.isdigit():
                return float(text) if len(text) <= 2 else int(text[:-2]) * 60 + int(text[-2:])
            try:
                return float(text)
            except ValueError:
                return self.default_skip
            
    def get_skip_value(self) -> float:
        return self.parse_time_value(self.skip_entry.get())
