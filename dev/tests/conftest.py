"""All model tests run with disposable config, subtitle and cache directories."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "SubtitlePlayer"))


@pytest.fixture(autouse=True)
def isolated_user_state(tmp_path, monkeypatch):
    tmp_path = tmp_path / "user-state"
    tmp_path.mkdir()
    defaults = json.loads((ROOT / "config.json").read_text("utf-8-sig"))
    subtitle = tmp_path / "test.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:03,000\n日本語\n\n2\n00:00:05,000 --> 00:00:07,000\n字幕\n", encoding="utf-8")
    defaults.update(REMOTE_FLAG=False, CLEANUP_CACHED_SUBTITLES_ON_EXIT=False,
                    LAST_LOCAL_SRT_FILE=str(subtitle), ANKI_ADD_ANIME_TAG=False,
                    ANNOTATION_LOCAL_DB_PATH=str(tmp_path / "words.json"))
    (tmp_path / "config.json").write_text(json.dumps(defaults), encoding="utf-8")
    (tmp_path / "config.local.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    # Cache paths historically live beside the module, independently of cwd.
    import importlib
    for name in ("model.subtitle_manager", "SubtitlePlayer.model.subtitle_manager"):
        module = importlib.import_module(name)
        cache = tmp_path / "subtitle-cache"
        cache.mkdir(exist_ok=True)
        monkeypatch.setattr(module.SubtitleManager, "_get_cache_base_dir", lambda self, p=str(cache): p)
        monkeypatch.setattr(module.SubtitleManager, "_get_unparsed_log_path", lambda self, p=str(tmp_path / "unparsed.tsv"): p)
