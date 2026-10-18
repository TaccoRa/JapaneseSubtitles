import json
from types import SimpleNamespace

from SubtitlePlayer.view.settings_advanced_ui import SettingsAdvancedUI


class _ProfileConfig:
    def __init__(self, local_path, active):
        self.local_path = str(local_path)
        self.local_config = {"ACTIVE_SETTINGS_PROFILE": active, "LOCAL_ONLY": "kept"}
        self.active = active

    def get(self, key):
        if key == "ACTIVE_SETTINGS_PROFILE":
            return self.active
        return self.local_config.get(key)


def _profile_ui(config):
    ui = object.__new__(SettingsAdvancedUI)
    object.__setattr__(ui, "settings_ui", SimpleNamespace(config=config))
    return ui


def test_runtime_episode_progress_is_written_only_to_active_profile(tmp_path):
    profiles = tmp_path / "settings_profiles"
    profiles.mkdir()
    profile_a = profiles / "A.json"
    profile_b = profiles / "B.json"
    profile_a.write_text(json.dumps({"ACTIVE_SETTINGS_PROFILE": "A", "A_ONLY": 1}), encoding="utf-8")
    profile_b.write_text(json.dumps({"ACTIVE_SETTINGS_PROFILE": "B", "B_ONLY": 2}), encoding="utf-8")
    config = _ProfileConfig(tmp_path / "config.local.json", "A")
    ui = _profile_ui(config)

    ui.persist_active_profile_fields(
        {"ANIME_EPISODE_PROGRESS": {"show": {"anime": "Show", "season": 1, "episode": 3, "global": 3}}}
    )
    config.active = "B"
    ui.persist_active_profile_fields(
        {"ANIME_EPISODE_PROGRESS": {"show": {"anime": "Show", "season": 1, "episode": 9, "global": 9}}}
    )

    stored_a = json.loads(profile_a.read_text(encoding="utf-8"))
    stored_b = json.loads(profile_b.read_text(encoding="utf-8"))
    assert stored_a["ANIME_EPISODE_PROGRESS"]["show"]["episode"] == 3
    assert stored_b["ANIME_EPISODE_PROGRESS"]["show"]["episode"] == 9
    assert stored_a["A_ONLY"] == 1
    assert stored_b["B_ONLY"] == 2
