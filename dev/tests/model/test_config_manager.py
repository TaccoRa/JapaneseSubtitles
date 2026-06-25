import json

from SubtitlePlayer.model.config_manager import ConfigManager


def test_local_config_overrides_defaults_and_writes_local_only(tmp_path):
    defaults = tmp_path / "config.json"
    local = tmp_path / "config.local.json"
    defaults.write_text('{"A": 1, "B": 2}', encoding="utf-8")
    local.write_text('{"B": 20, "C": 30}', encoding="utf-8")

    config = ConfigManager(str(defaults), local_path=str(local))

    assert config.get("A") == 1
    assert config.get("B") == 20
    assert config.get("C") == 30

    config.set("A", 10)

    assert json.loads(defaults.read_text(encoding="utf-8")) == {"A": 1, "B": 2}
    assert json.loads(local.read_text(encoding="utf-8")) == {"B": 20, "C": 30, "A": 10}


def test_missing_local_config_is_allowed_and_created_on_write(tmp_path):
    defaults = tmp_path / "config.json"
    local = tmp_path / "config.local.json"
    defaults.write_text('{"A": 1}', encoding="utf-8")

    config = ConfigManager(str(defaults), local_path=str(local))
    assert config.get("A") == 1
    assert not local.exists()

    config.set("B", 2)

    assert local.exists()
    assert json.loads(local.read_text(encoding="utf-8")) == {"B": 2}


def test_set_many_preserves_existing_local_key_order(tmp_path):
    defaults = tmp_path / "config.json"
    local = tmp_path / "config.local.json"
    defaults.write_text('{"A": 1}', encoding="utf-8")
    local.write_text('{"C": 3}', encoding="utf-8")

    config = ConfigManager(str(defaults), local_path=str(local))
    config.set_many({"B": 2, "C": 30, "D": 4})

    data = json.loads(local.read_text(encoding="utf-8"))
    assert list(data.keys()) == ["C", "B", "D"]
    assert data == {"C": 30, "B": 2, "D": 4}
