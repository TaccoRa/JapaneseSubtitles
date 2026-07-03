import json

from SubtitlePlayer.model.word_database import WordDatabase, WordEntry, normalize_word


def test_import_plain_text_deduplicates_by_normalized_form(tmp_path):
    path = tmp_path / "words.json"
    import_path = tmp_path / "words.txt"
    import_path.write_text("人間\n人 間\nﾊﾟﾗｻｲﾄ\n", encoding="utf-8")

    db = WordDatabase(str(path))
    result = db.import_file(str(import_path))

    assert result["imported"] == 3
    entries = db.list_entries()
    assert len(entries) == 2
    assert {entry.normalized for entry in entries} == {normalize_word("人間"), normalize_word("パラサイト")}


def test_import_csv_preserves_existing_reading_and_notes_when_blank_reimport(tmp_path):
    path = tmp_path / "words.json"
    csv_path = tmp_path / "words.csv"
    csv_path.write_text("surface,reading,meaning,notes\n勉強,べんきょう,study,keep\n", encoding="utf-8")

    db = WordDatabase(str(path))
    db.import_file(str(csv_path))
    csv_path.write_text("surface,reading,meaning,notes\n勉強,,,\n", encoding="utf-8")
    db.import_file(str(csv_path))

    entry = db.get("local", "勉強")
    assert entry.reading == "べんきょう"
    assert entry.meaning == "study"
    assert entry.notes == "keep"


def test_export_csv_and_json(tmp_path):
    path = tmp_path / "words.json"
    db = WordDatabase(str(path))
    db.upsert(WordEntry(surface="新一", reading="しんいち", meaning="Shinichi"))

    csv_path = tmp_path / "out.csv"
    json_path = tmp_path / "out.json"
    assert db.export_file(str(csv_path))["exported"] == 1
    assert db.export_file(str(json_path))["exported"] == 1

    assert "新一" in csv_path.read_text(encoding="utf-8-sig")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["words"][0]["surface"] == "新一"


def test_edit_and_delete_entry(tmp_path):
    db = WordDatabase(str(tmp_path / "words.json"))
    entry = db.upsert(WordEntry(surface="猫", reading="ねこ"))
    db.upsert(WordEntry(surface="猫", meaning="cat"))

    edited = db.get("local", entry.normalized)
    assert edited.reading == "ねこ"
    assert edited.meaning == "cat"

    assert db.delete("local", "猫") is True
    assert db.get("local", "猫") is None


def test_search_renyou_form_finds_dictionary_form(tmp_path):
    db = WordDatabase(str(tmp_path / "words.json"))
    db.upsert(WordEntry(surface="\u5207\u308b", meaning="cut"))

    results = db.search("\u5207\u308a")

    assert [entry.surface for entry in results] == ["\u5207\u308b"]
