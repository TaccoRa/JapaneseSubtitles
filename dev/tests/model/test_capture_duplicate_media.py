import pytest

from SubtitlePlayer.model.anki_client import AnkiClient
from SubtitlePlayer.model.config_manager import ConfigManager


def test_new_recording_updates_earlier_add_only_note_with_exact_sentence(monkeypatch):
    from queue import Queue
    from types import SimpleNamespace
    from controller.browser_sync_controller import BrowserSyncController

    client = AnkiClient(ConfigManager("config.json"))
    def note(nid, sentence, word):
        return dict(noteId=nid, tags=[], fields={name: {"value": value} for name, value in {
            client.add_rubies_to_sentence_ja_field: sentence,
            client.sentence_ja_field: sentence, client.front_field: word,
            client.sound_field: "", client.image_field: ""}.items()})
    notes = {1: note(1, "同じ文。", "first"), 2: note(2, "同じ文。", "second"),
             3: note(3, "別の文。", "unrelated")}
    updates = []
    def invoke(action, params):
        if action == "notesInfo":
            return [notes[nid] for nid in params["notes"]]
        if action == "findNotes":
            return list(notes)
        if action == "updateNoteFields":
            update = params["note"]
            updates.append(update)
            notes[update["id"]]["fields"].update(
                {key: {"value": value} for key, value in update["fields"].items()})
            return
        pytest.fail(action)
    monkeypatch.setattr(client, "_invoke", invoke)
    sync = object.__new__(BrowserSyncController)
    sync.controller = SimpleNamespace(anki=client,
        anki_controller=SimpleNamespace(submit_anki_work=lambda work: work()))
    sync._pending_media, sync._results = {}, Queue()
    sync._set_status = lambda value: None
    sync._save_pending_media = lambda *args: None
    session = SimpleNamespace(id="capture", target_note_id=2, cancelled=False,
                              backend="native", replace_sentence_media=False)
    media = {client.sound_field: "[sound:fresh.wav]", client.image_field: '<img src="fresh.png">'}
    sync._attach_media(session, {"sharedFields": media})
    assert sync._results.get_nowait() == ("media", "capture", None)
    assert [update["id"] for update in updates] == [2, 1]
    for nid in (1, 2):
        assert all(notes[nid]["fields"][name]["value"] == value for name, value in media.items())
    assert notes[1]["fields"][client.front_field]["value"] == "first"
    assert notes[3]["fields"][client.sound_field]["value"] == ""






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
