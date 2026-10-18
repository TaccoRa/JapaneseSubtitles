import pytest

from SubtitlePlayer.model.anki_client import AnkiClient
from SubtitlePlayer.model.config_manager import ConfigManager


@pytest.mark.parametrize("same_word", [True, False])
def test_capture_duplicate_waits_for_fresh_media(monkeypatch, same_word):
    client = AnkiClient(ConfigManager("config.json"))

    def note(nid, word, sound="", image=""):
        return {"noteId": nid, "fields": {
            client.add_rubies_to_front_field: {"value": word},
            client.add_rubies_to_sentence_ja_field: {"value": "same sentence"},
            client.sound_field: {"value": sound},
            client.image_field: {"value": image},
        }}

    old = note(1, "word" if same_word else "other", "old.mp3", "old.png")
    target = note(2, "word")
    updates = []
    monkeypatch.setattr(client, "_find_same_sentence_notes_for_media_copy", lambda **kw: [old])
    monkeypatch.setattr(client, "_invoke", lambda action, params: [target] if action == "notesInfo" else updates.append(params))
    baseline = client.snapshot_exact_sentence_media("same sentence")
    result = client.reconcile_exact_sentence_media(2, baseline=baseline)
    if same_word:
        assert result["available_media_fields"] == []
        assert updates == []
        old["fields"][client.sound_field]["value"] = "fresh.mp3"
        result = client.reconcile_exact_sentence_media(2, baseline=baseline)
        assert result["missing_media_fields"] == [client.image_field]
        old["fields"][client.image_field]["value"] = "fresh.png"
        result = client.reconcile_exact_sentence_media(2, baseline=baseline)
        assert result["missing_media_fields"] == []
    else:
        assert result["missing_media_fields"] == []
        assert updates


def test_sentence_break_has_literal_ruby_boundary():
    assert AnkiClient._to_anki_html_line_breaks("ても\n結果[けっか]") == "ても<br> 結果[けっか]"
    assert AnkiClient._to_anki_html_line_breaks("ても<br> 結果[けっか]") == "ても<br> 結果[けっか]"
