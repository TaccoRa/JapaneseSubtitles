from SubtitlePlayer.model.config_manager import ConfigManager
from SubtitlePlayer.model.anki_client import AnkiClient


def test_extract_jisho_translation_list():
    # basic sanity check that we return a list of definitions
    client = AnkiClient(ConfigManager("config.json"))
    fake_entries = [
        {
            "japanese": [{"word": "試験", "reading": "しけん"}],
            "senses": [{"english_definitions": ["test", "exam", "try"]}],
        }
    ]
    defs = client._extract_jisho_translation("試験", fake_entries)
    assert isinstance(defs, list)
    assert defs == ["test", "exam", "try"]


def test_build_fields_with_definition(monkeypatch):
    config = ConfigManager("config.json")
    client = AnkiClient(config)

    # pretend a jisho lookup already produced a full definition string
    client._last_jisho_full_definition = "one, two, three, four"

    # ensure the model appears to have a Definition field
    monkeypatch.setattr(client, "_get_model_field_names", lambda: {
        client.add_rubies_to_front_field,
        client.front_field,
        client.back_field,
        client.sentence_ja_field,
        client.sentence_de_field,
        client.add_rubies_to_sentence_ja_field,
        client.definition_field,
    })

    fields = client._build_note_fields(
        selected="選択",
        subtitle="",
        word_translation="one, two, three",
        sentence_translation="",
    )
    assert fields[client.back_field] == "one, two, three"
    assert fields[client.definition_field] == "one, two, three, four"
