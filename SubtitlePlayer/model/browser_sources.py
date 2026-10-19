"""Local, explicit associations between watched media and subtitle sources."""
from copy import deepcopy
import json
from pathlib import Path


class BrowserSources:
    FIELDS = ("srt_file", "remote_flag", "anime_folder_name", "display_anime_name",
              "current_season", "current_episode", "is_movie", "github_owner",
              "github_repo", "github_ref", "remote_path", "remote_url",
              "remote_search_query", "file_name", "all_results_items")

    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text("utf-8"))
            self.entries = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.entries = {}

    def remember(self, key, manager):
        state = {name: deepcopy(getattr(manager, name, None)) for name in self.FIELDS}
        if not state["srt_file"] or not Path(state["srt_file"]).is_file():
            return
        if self.entries.get(key) == state:
            return
        self.entries[key] = state
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.entries, ensure_ascii=False), "utf-8")
        temporary.replace(self.path)

    def restore(self, key, manager):
        state = self.entries.get(key)
        if not isinstance(state, dict) or not state.get("srt_file"):
            return False
        if not Path(state["srt_file"]).is_file():
            raise FileNotFoundError("Remembered subtitle file is missing. Select its replacement once.")
        if all(getattr(manager, name, None) == state.get(name) for name in
               ("srt_file", "remote_flag", "anime_folder_name", "current_episode", "current_season")):
            return False
        previous = {name: getattr(manager, name, None) for name in self.FIELDS}
        previous_display = {name: getattr(manager, name, None) for name in
                            ("subtitles", "display_data", "display_start_times", "display_end_times")}
        config_keys = ("REMOTE_FLAG", "LAST_LOCAL_SRT_FILE", "LAST_ANIME_NAME", "LAST_DISPLAY_ANIME_NAME", "LAST_GITHUB_URL")
        previous_config = {name: manager.config.get(name) for name in config_keys}
        try:
            for name in self.FIELDS:
                setattr(manager, name, deepcopy(state.get(name)))
            manager.all_results_items = manager.all_results_items or []
            if manager.remote_flag:
                manager.build_remote_episode_maps()
            manager._load_local_and_process(state["srt_file"])
            # Preserve mapped season/episode and canonical search names that
            # cannot always be recovered from a provider's cached filename.
            for name in ("anime_folder_name", "display_anime_name", "current_season", "current_episode", "is_movie"):
                setattr(manager, name, deepcopy(state.get(name)))
            for name, value in zip(config_keys, (bool(manager.remote_flag), manager.srt_file,
                    manager.anime_folder_name, manager.display_anime_name, manager.remote_url)):
                if value is not None:
                    manager.config.set(name, value)
        except Exception:
            for name, value in (previous | previous_display).items():
                setattr(manager, name, value)
            if manager.remote_flag:
                manager.build_remote_episode_maps()
            for name, value in previous_config.items():
                manager.config.set(name, value)
            raise
        return True
