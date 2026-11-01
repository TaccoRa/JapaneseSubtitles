"""Local, explicit associations between watched media and subtitle sources."""
from copy import deepcopy
import json
from pathlib import Path


class BrowserSources:
    FIELDS = ("srt_file", "remote_flag", "anime_folder_name", "display_anime_name",
              "current_season", "current_episode", "is_movie", "github_owner",
              "github_repo", "github_ref", "remote_path", "remote_url",
              "remote_search_query", "file_name", "all_results_items", "source_kind", "source_descriptor")

    def __init__(self, path):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text("utf-8"))
            self.entries = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self.entries = {}

    def remember(self, key, manager, *, explicit=False):
        state = {name: deepcopy(getattr(manager, name, None)) for name in self.FIELDS}
        previous = self.entries.get(key, {})
        state["manual_provider"] = bool(explicit or (previous.get("manual_provider")
            and previous.get("source_kind") == state.get("source_kind")))
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
        return self.restore_state(state, manager)

    def restore_state(self, state, manager):
        """Apply a prepared source on the UI thread, with the same rollback rules."""
        if not isinstance(state, dict) or not state.get("srt_file"):
            return False
        if not Path(state["srt_file"]).is_file():
            raise FileNotFoundError("Remembered subtitle file is missing. Select its replacement once.")
        if all(getattr(manager, name, None) == state.get(name) for name in
               ("srt_file", "remote_flag", "anime_folder_name", "display_anime_name", "current_episode", "current_season", "source_kind", "source_descriptor") if name in state):
            return False
        previous = {name: getattr(manager, name, None) for name in self.FIELDS}
        previous_display = {name: getattr(manager, name, None) for name in
                            ("subtitles", "display_data", "display_start_times", "display_end_times",
                             "display_revision", "display_file_identity", "display_source_hash",
                             "startup_needs_source", "_auto_ruby_ready_indices", "local_srt_files", "local_srt_dir")}
        config_keys = ("REMOTE_FLAG", "LAST_LOCAL_SRT_FILE", "LAST_ANIME_NAME", "LAST_DISPLAY_ANIME_NAME", "LAST_GITHUB_URL",
                       "LAST_SUBTITLE_SOURCE_KIND", "LAST_SUBTITLE_SOURCE_DESCRIPTOR")
        previous_config = {name: manager.config.get(name) for name in config_keys}
        try:
            for name in self.FIELDS:
                if name in state:
                    setattr(manager, name, deepcopy(state[name]))
            manager.all_results_items = manager.all_results_items or []
            if manager.remote_flag:
                manager.build_remote_episode_maps()
            payload = state.get("_prepared_payload")
            if payload is not None:
                manager._store_prepared_episode_payload(state["srt_file"], payload, state["_prepared_key"])
            manager._load_local_and_process(state["srt_file"])
            # Preserve mapped season/episode and canonical search names that
            # cannot always be recovered from a provider's cached filename.
            for name in ("anime_folder_name", "display_anime_name", "current_season", "current_episode", "is_movie", "source_kind", "source_descriptor"):
                if name in state:
                    setattr(manager, name, deepcopy(state[name]))
            values = dict(zip(config_keys, (bool(manager.remote_flag), manager.srt_file,
                manager.anime_folder_name, manager.display_anime_name, manager.remote_url,
                manager.source_kind, manager.source_descriptor)))
            manager.config.set_many({name: value for name, value in values.items() if value is not None})
        except Exception:
            for name, value in (previous | previous_display).items():
                setattr(manager, name, value)
            if manager.remote_flag:
                manager.build_remote_episode_maps()
            for name, value in previous_config.items():
                manager.config.set(name, value)
            raise
        return True
