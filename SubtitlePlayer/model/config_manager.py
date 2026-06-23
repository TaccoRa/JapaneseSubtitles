"""
Simple JSON-backed configuration wrapper.

Reads/writes `config.json` and exposes get/set helpers.
"""

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
            self._key_order = list(self.config.keys())
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse config file: {e}")

    def get(self, key):
        return self.config.get(key)

    def set(self, key, value):
        if key not in self.config:
            self._key_order.append(key)
        self.config[key] = value
        self._save()

    def set_many(self, updates):
        if not isinstance(updates, dict) or not updates:
            return
        changed = False
        for key, value in updates.items():
            if key not in self.config:
                self._key_order.append(key)
            if self.config.get(key) != value:
                self.config[key] = value
                changed = True
        if changed:
            self._save()
         
    def _save(self):
        ordered = {}
        for key in list(getattr(self, "_key_order", [])):
            if key in self.config:
                ordered[key] = self.config[key]
        for key, value in self.config.items():
            if key not in ordered:
                ordered[key] = value
                self._key_order.append(key)
        self.config = ordered
        tmp_path = f"{self.path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, self.path)
