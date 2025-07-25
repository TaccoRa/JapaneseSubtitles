import json
import os

class ConfigManager:
    def __init__(self, path="config.json"):
        self.path = path
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Config file not found: {self.path}")
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                self.config = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse config file: {e}")

    def get(self, key):
        return self.config.get(key)

    def set(self, key, value):
        self.config[key] = value
        self._save()
        
    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
