import json
import os

class ConfigManager:
    def __init__(self, path="config.json"):
        self.path = path
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Config file not found: {self.path}")
        with open(self.path, "r", encoding="utf-8-sig") as f:
            self.config = json.load(f)

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self._save()

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.config, f, indent=4)

    # def load_subtitles(self, srt_path):
    #     with open(srt_path, 'rb') as f:
    #         raw_data = f.read()
    #     detected = chardet.detect(raw_data)
    #     encoding = detected['encoding']

    #     srt_content = raw_data.decode(encoding)
    #     return list(srt.parse(srt_content))