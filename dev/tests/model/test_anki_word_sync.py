import time

from SubtitlePlayer.model.anki_word_sync import AnkiSyncSettings, AnkiWordSync, classify_card


def test_classify_card_mature_threshold_is_inclusive():
    assert classify_card({"queue": 2, "type": 2, "interval": 21}, 21) == "anki_mature"
    assert classify_card({"queue": 2, "type": 2, "interval": 20}, 21) == "anki_young"


def test_classify_card_can_ignore_mature_threshold():
    assert classify_card({"queue": 2, "type": 2, "interval": 21}, 21, "Normal", False) == "anki_graduated"
    assert classify_card({"queue": 2, "type": 2, "interval": 1}, 21, "Normal", False) == "anki_graduated"


def test_classify_learning_and_unknown():
    assert classify_card({"queue": 1, "type": 1, "interval": 0}, 21) == "anki_learning"
    assert classify_card({"queue": 0, "type": 0, "interval": 0}, 21) == "anki_unknown"


def test_suspended_dropdown_override():
    assert classify_card({"queue": -1, "type": 2, "interval": 3}, 21, "Mature") == "anki_mature"
    assert classify_card({"queue": -1, "type": 2, "interval": 3}, 21, "Ignored") == "ignored"
    assert classify_card({"queue": -1, "type": 2, "interval": 30}, 21, "Normal") == "anki_mature"


def test_sync_extracts_selected_fields_and_classifies():
    calls = []

    def invoke(action, params=None):
        calls.append((action, params or {}))
        if action == "findNotes":
            return [1]
        if action == "notesInfo":
            return [
                {
                    "noteId": 1,
                    "fields": {
                        "Word": {"value": "人間"},
                        "Reading": {"value": "にんげん"},
                        "Meaning": {"value": "human"},
                    },
                }
            ]
        if action == "findCards":
            return [10]
        if action == "cardsInfo":
            return [{"cardId": 10, "note": 1, "queue": 2, "type": 2, "interval": 22}]
        return []

    sync = AnkiWordSync(invoke)
    result = sync.sync(
        AnkiSyncSettings(
            decks=["Japanese"],
            word_fields=["Word"],
            reading_fields=["Reading"],
            meaning_fields=["Meaning"],
            mature_interval_days=21,
        )
    )

    assert result.error == ""
    assert result.collected == 1
    assert result.entries[0].surface == "人間"
    assert result.entries[0].status == "anki_mature"
    assert result.entries[0].reading == "にんげん"
    assert result.entries[0].extra["note_id"] == 1
    assert result.entries[0].extra["field"] == "Word"
    assert result.entries[0].extra["reading_field"] == "Reading"
    assert result.entries[0].extra["meaning_field"] == "Meaning"


def test_entries_for_note_extracts_added_note_metadata():
    calls = []

    def invoke(action, params=None):
        calls.append((action, params or {}))
        query = (params or {}).get("query", "")
        if action == "notesInfo":
            assert params == {"notes": [42]}
            return [
                {
                    "noteId": 42,
                    "mod": "2026-07-02 10:11:12",
                    "fields": {
                        "Front": {"value": "猫[ねこ]"},
                        "Back": {"value": "cat"},
                        "SentenceJA": {"value": "猫がいる"},
                    },
                }
            ]
        if action == "findCards" and "is:due" in query:
            return [99]
        if action == "findCards":
            return [99]
        if action == "cardsInfo":
            return [{"cardId": 99, "note": 42, "queue": 2, "type": 2, "interval": 22, "reps": 5}]
        return []

    entries = AnkiWordSync(invoke).entries_for_note(
        42,
        AnkiSyncSettings(
            word_fields=["Front"],
            meaning_fields=["Back"],
            sentence_fields=["SentenceJA"],
        ),
    )

    assert len(entries) == 1
    assert entries[0].surface == "猫"
    assert entries[0].meaning == "cat"
    assert entries[0].status == "anki_mature"
    assert entries[0].extra["note_id"] == 42
    assert entries[0].extra["card_ids"] == "99"
    assert entries[0].extra["reviews"] == 5
    assert entries[0].extra["sentence"] == "猫がいる"
    assert entries[0].extra["note_modified"] == "10:11 02-07-2026"


def test_sync_counts_due_reviews_per_note():
    def invoke(action, params=None):
        query = (params or {}).get("query", "")
        if action == "findNotes":
            return [1]
        if action == "notesInfo":
            return [
                {
                    "noteId": 1,
                    "mod": "2026-07-02 10:11:12",
                    "fields": {
                        "Word": {"value": "cat"},
                        "Meaning": {"value": "cat"},
                        "Sentence": {"value": "This is a cat."},
                        "SentenceTranslated": {"value": "Das ist eine Katze."},
                    },
                }
            ]
        if action == "findCards" and "is:due" in query:
            return [10]
        if action == "findCards":
            return [10, 11]
        if action == "cardsInfo":
            return [
                {"cardId": 10, "note": 1, "queue": 2, "type": 2, "interval": 22, "reps": 3},
                {"cardId": 11, "note": 1, "queue": 2, "type": 2, "interval": 22, "reps": 4},
            ]
        return []

    result = AnkiWordSync(invoke).sync(
        AnkiSyncSettings(
            decks=["Japanese"],
            word_fields=["Word"],
            meaning_fields=["Meaning"],
            sentence_fields=["Sentence"],
            sentence_translated_fields=["SentenceTranslated"],
        )
    )

    assert result.collected == 1
    assert result.entries[0].extra["due_date"] == "Due " + time.strftime("%d-%m-%Y", time.localtime())
    assert result.entries[0].extra["reviews"] == 7
    assert result.entries[0].extra["note_modified"] == "10:11 02-07-2026"
    assert result.entries[0].extra["sentence"] == "This is a cat."
    assert result.entries[0].extra["sentence_translated"] == "Das ist eine Katze."


def test_sync_formats_future_review_due_date_when_scheduler_today_is_known():
    def invoke(action, params=None):
        query = (params or {}).get("query", "")
        cards = (params or {}).get("cards") or []
        if action == "findNotes":
            return [1]
        if action == "notesInfo":
            return [{"noteId": 1, "fields": {"Word": {"value": "cat"}, "Meaning": {"value": "cat"}}}]
        if action == "findCards" and "prop:due=0" in query:
            return [99]
        if action == "findCards" and "is:due" in query:
            return []
        if action == "findCards":
            return [10]
        if action == "cardsInfo":
            if cards == [99]:
                return [{"cardId": 99, "note": 9, "queue": 2, "type": 2, "due": 100}]
            return [{"cardId": 10, "note": 1, "queue": 2, "type": 2, "due": 105}]
        return []

    result = AnkiWordSync(invoke).sync(
        AnkiSyncSettings(decks=["Japanese"], word_fields=["Word"], meaning_fields=["Meaning"])
    )
    expected = time.strftime("%d-%m-%Y", time.localtime(time.time() + (5 * 86400)))

    assert result.entries[0].extra["due_date"] == f"Due {expected}"


def test_sync_connection_failure_returns_error():
    def invoke(_action, _params=None):
        raise RuntimeError("AnkiConnect unavailable")

    result = AnkiWordSync(invoke).sync(AnkiSyncSettings(decks=["Japanese"], word_fields=["Word"]))

    assert "AnkiConnect unavailable" in result.error


def test_sync_reports_skip_reasons_for_unusable_notes():
    def invoke(action, params=None):
        if action == "findNotes":
            return [1, 2, 3]
        if action == "notesInfo":
            return [
                {"noteId": 1, "fields": {"Front": {"value": ""}}},
                {"noteId": 2, "fields": {"Other": {"value": "word"}}},
                {"noteId": 3, "fields": {"Front": {"value": "<br>"}}},
            ]
        if action == "findCards":
            return []
        return []

    result = AnkiWordSync(invoke).sync(AnkiSyncSettings(decks=["Japanese"], word_fields=["Front"]))

    assert result.collected == 0
    assert result.skipped == 3
    assert result.skip_reasons == {"word fields empty": 2, "word fields missing": 1}


def test_update_note_fields_invokes_anki_connect():
    calls = []

    def invoke(action, params=None):
        calls.append((action, params or {}))
        return None

    AnkiWordSync(invoke).update_note_fields(123, {"Back": "meaning"})

    assert calls == [("updateNoteFields", {"note": {"id": 123, "fields": {"Back": "meaning"}}})]
