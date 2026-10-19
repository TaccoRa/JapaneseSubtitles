import pytest

from SubtitlePlayer.model.anki_client import AnkiClient
from SubtitlePlayer.model.config_manager import ConfigManager






def test_sentence_break_has_literal_ruby_boundary():
    assert AnkiClient._to_anki_html_line_breaks("ても\n結果[けっか]") == "ても<br> 結果[けっか]"
    assert AnkiClient._to_anki_html_line_breaks("ても<br> 結果[けっか]") == "ても<br> 結果[けっか]"


@pytest.mark.parametrize("existing_audio,existing_image", [("", ""), ("keep.wav", ""), ("", "keep.png")])
def test_add_only_reuses_exact_sentence_media_without_overwriting_existing_fields(monkeypatch, existing_audio, existing_image):
    client = AnkiClient(ConfigManager("config.json"))
    fields = {client.sound_field: existing_audio, client.image_field: existing_image, client.front_field: "keep word"}
    monkeypatch.setattr(client, "_find_existing_sentence_media", lambda *args: {
        client.sound_field: "[sound:old.wav]", client.image_field: '<img src="old.png">'})
    copied = client._copy_existing_sentence_media_fields(fields)
    assert fields[client.sound_field] == (existing_audio or "[sound:old.wav]")
    assert fields[client.image_field] == (existing_image or '<img src="old.png">')
    assert fields[client.front_field] == "keep word"
    assert (client.sound_field in copied) == (not existing_audio)
    assert (client.image_field in copied) == (not existing_image)
