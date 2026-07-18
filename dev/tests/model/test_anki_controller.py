from types import SimpleNamespace

import SubtitlePlayer.controller.anki_controller as anki_controller_module
from SubtitlePlayer.controller.anki_controller import AnkiController


def test_anki_add_result_prints_plain_status_lines(capsys):
    controller = SimpleNamespace(
        anki=SimpleNamespace(
            sound_field="Sound",
            image_field="Image",
            word_translate_provider="jisho",
            sentence_translate_provider="deepl",
        )
    )
    anki_controller = AnkiController(controller)

    anki_controller._log_anki_add_result(
        result={
            "selection_lookup_text": "\u52c9\u5f37\u3059\u308b",
            "word_translation": "lernen",
            "translation_candidates": {
                "word": {"jisho": "lernen", "google": ""},
                "sentence": {"deepl": "Ich lerne.", "google": "Google Satz"},
            },
            "definition": "study, learn",
            "copied_media_fields": {"Sound": "[sound:test.mp3]"},
            "routed_cards": {"reading": [11], "reverse": [12], "unrouted": []},
            "translation_provider_used": {"word": "jisho", "sentence": "deepl"},
            "note_id": 10,
        },
        selected="\u52c9\u5f37",
        elapsed=1.234,
    )

    out = capsys.readouterr().out
    assert "Time: 1.23s" in out
    assert "Word: \u52c9\u5f37" in out
    assert "Lookup: \u52c9\u5f37\u3059\u308b" in out
    assert "Sentence Google: Google Satz" in out
    assert "Card IDs: 11, 12" in out
    assert "Anki " not in out


def test_post_add_external_capture_focuses_target_and_sends_hotkey(monkeypatch):
    calls = []
    controller = SimpleNamespace(
        config=SimpleNamespace(
            get=lambda key: {
                "POST_ADD_CAPTURE_TARGET_TITLE": "Chrome",
                "POST_ADD_CAPTURE_EXTERNAL_HOTKEY": "ctrl+shift+y",
            }.get(key)
        ),
        playback=SimpleNamespace(on_jump_sub_end=lambda: calls.append(("jump", "subtitle_end"))),
        _suppress_external_hotkey_events=lambda hotkey, count: calls.append(("suppress", hotkey, count)),
    )
    anki_controller = AnkiController(controller)

    monkeypatch.setattr(
        anki_controller_module,
        "focus_window_by_title",
        lambda title: calls.append(("focus", title)) or True,
    )
    monkeypatch.setattr(
        anki_controller_module,
        "send_global_hotkey",
        lambda hotkey: calls.append(("hotkey", hotkey)) or True,
    )

    assert anki_controller._run_post_add_external_capture(123) is True
    assert calls == [
        ("focus", "Chrome"),
        ("suppress", "ctrl+shift+y", 1),
        ("hotkey", "ctrl+shift+y"),
        ("jump", "subtitle_end"),
    ]
