"""
JSON-backed configuration wrapper.

`config.json` is the tracked defaults file. Runtime/user changes are written to
`config.local.json` next to it, then merged over the defaults at load time.
"""

import json
import os
from collections import OrderedDict


class ConfigManager:
    def __init__(self, path="config.json", local_path=None):
        self.path = path
        self.local_path = local_path or self._default_local_path(path)
        self.defaults = {}
        self.local_config = {}
        self.config = {}
        self._default_key_order = []
        self._local_key_order = []
        self._key_order = []
        self._load()

    @staticmethod
    def _default_local_path(path: str) -> str:
        base_dir = os.path.dirname(os.path.abspath(path)) or os.getcwd()
        return os.path.join(base_dir, "config.local.json")

    @staticmethod
    def _read_json(path: str, *, required: bool) -> OrderedDict:
        if not os.path.exists(path):
            if required:
                raise FileNotFoundError(f"Config file not found: {path}")
            return OrderedDict()
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f, object_pairs_hook=OrderedDict)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse config file {path}: {e}")
        if not isinstance(data, dict):
            raise ValueError(f"Config file must contain a JSON object: {path}")
        return data

    def _load(self):
        self.defaults = self._read_json(self.path, required=True)
        self.local_config = self._read_json(self.local_path, required=False)
        self._default_key_order = list(self.defaults.keys())
        self._local_key_order = list(self.local_config.keys())
        self._rebuild_merged_config()

    def _rebuild_merged_config(self):
        merged = OrderedDict()
        for key in self._default_key_order:
            if key in self.defaults:
                merged[key] = self.defaults[key]
        for key, value in self.local_config.items():
            merged[key] = value
        self.config = merged
        self._key_order = list(merged.keys())

    def get(self, key):
        return self.config.get(key)

    def set(self, key, value):
        if key not in self.local_config:
            self._local_key_order.append(key)
        self.local_config[key] = value
        self._rebuild_merged_config()
        self._save()

    def set_many(self, updates):
        if not isinstance(updates, dict) or not updates:
            return
        changed = False
        for key, value in updates.items():
            if key not in self.local_config:
                self._local_key_order.append(key)
            if self.local_config.get(key) != value:
                self.local_config[key] = value
                changed = True
        if changed:
            self._rebuild_merged_config()
            self._save()

    def replace_local_config(self, data):
        if not isinstance(data, dict):
            raise ValueError("Local config replacement must be a JSON object.")
        self.local_config = OrderedDict((str(key), value) for key, value in data.items())
        self._local_key_order = list(self.local_config.keys())
        self._rebuild_merged_config()
        self._save()

    def reload(self):
        self._load()

    def _save(self):
        ordered = OrderedDict()
        for key in list(getattr(self, "_local_key_order", [])):
            if key in self.local_config:
                ordered[key] = self.local_config[key]
        for key, value in self.local_config.items():
            if key not in ordered:
                ordered[key] = value
                self._local_key_order.append(key)
        self.local_config = ordered

        base_dir = os.path.dirname(os.path.abspath(self.local_path))
        if base_dir:
            os.makedirs(base_dir, exist_ok=True)
        tmp_path = f"{self.local_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.local_config, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, self.local_path)
