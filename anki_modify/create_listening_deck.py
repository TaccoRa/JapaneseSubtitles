"""Create independent, one-direction listening cards from existing sentence audio.

Preview: python anki_modify/create_listening_deck.py
Create:  python anki_modify/create_listening_deck.py --apply
Re-running skips sentences already imported. Existing notes are never edited.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from html import unescape
import hashlib
import json
from pathlib import Path
import re

from fix_sentence_ruby_spacing import invoke, repair


DECK = "Japanese::Listening"
MODEL = "Japanese Listening"
FIELDS = ["SentenceKey", "Sound", "SentenceJA", "SentenceDE", "Image", "SourceNoteId"]
FRONT = """<main class="listening-card">
<div class="audio">{{Sound}}</div>
<details class="hint"><summary>Japanischen Satz anzeigen</summary>
<div class="japanese" lang="ja">{{furigana:SentenceJA}}</div></details>
</main>"""
BACK = """<main class="listening-card">
<div class="audio">{{Sound}}</div>
<div id="answer" class="translation" lang="de">{{SentenceDE}}</div>
{{#Image}}<div class="picture">{{Image}}</div>{{/Image}}
<details class="hint"><summary>Japanischen Satz anzeigen</summary>
<div class="japanese" lang="ja">{{furigana:SentenceJA}}</div></details>
</main>"""
CSS = """.card { font-family: Arial, sans-serif; font-size: 24px; text-align: center;
color: #202124; background: #faf9f6; line-height: 1.6; margin: 0; padding: 24px 16px; }
.listening-card { max-width: 850px; margin: 0 auto; }
.audio { font-size: 40px; margin: 12px 0 30px; }
.translation { font-size: 1.1em; margin: 18px 0; }
.hint { margin: 22px 0; padding: 14px 18px; border: 1px solid #aaa6; border-radius: 12px; }
.hint summary { cursor: pointer; font-size: .75em; opacity: .8; }
.japanese { margin-top: 16px; font-size: 1.15em; line-height: 2.1; }
ruby rt { font-size: .5em; }
.picture img { display: block; max-width: 100%; max-height: 55vh; object-fit: contain; margin: 24px auto; }
.nightMode, .night_mode { color: #eee; background: #202124; }
"""


def plain(value):
    value = re.sub(r"<br\b[^>]*>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]*>", "", value)
    value = re.sub(r"\[[^\[\]]*\]", "", unescape(value))
    return re.sub(r"\s+", "", value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    call = lambda action, params=None: invoke(args.url, action, params)
    ids = call("findNotes", {"query": '-note:"Japanese Listening"'})
    candidates = {}
    stats = {"scanned": len(ids), "with_audio": 0, "missing_sentence_or_translation": 0, "duplicate_sentences": 0}
    for start in range(0, len(ids), 250):
        for note in call("notesInfo", {"notes": ids[start:start + 250]}):
            get = lambda name: note["fields"].get(name, {}).get("value", "").strip()
            sound = get("Sound")
            if not re.search(r"\[sound:[^\]]+\]", sound):
                continue
            stats["with_audio"] += 1
            ja, de = get("SentenceJA"), get("SentenceDE")
            if not plain(ja) or not plain(de):
                stats["missing_sentence_or_translation"] += 1
                continue
            key = hashlib.sha256(plain(ja).encode("utf-8")).hexdigest()
            fields = dict(zip(FIELDS, [key, sound, repair(ja), de, get("Image"), str(note["noteId"])]))
            rank = (bool(fields["Image"]), note["noteId"])
            if key in candidates:
                stats["duplicate_sentences"] += 1
            if key not in candidates or rank > candidates[key]["rank"]:
                candidates[key] = {"fields": fields, "rank": rank}
    existing = set()
    models = call("modelNames")
    if MODEL in models:
        existing_ids = call("findNotes", {"query": 'note:"Japanese Listening"'})
        for start in range(0, len(existing_ids), 250):
            for note in call("notesInfo", {"notes": existing_ids[start:start + 250]}):
                existing.add(note["fields"]["SentenceKey"]["value"])
    plan = [entry["fields"] for key, entry in candidates.items() if key not in existing]
    stats.update(unique_sentences=len(candidates), already_present=len(existing), to_create=len(plan))
    print(json.dumps(stats, indent=2))
    folder = Path(__file__).resolve().parent / "listening_deck"
    folder.mkdir(exist_ok=True)
    for name, value in [("front.html", FRONT), ("back.html", BACK), ("styling.css", CSS)]:
        (folder / name).write_text(value, encoding="utf-8")
    report = {"stats": stats, "created_note_ids": [], "notes": plan}
    path = folder / ("import_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {path}")
    if not args.apply:
        return
    if MODEL in models:
        if call("modelFieldNames", {"modelName": MODEL}) != FIELDS:
            raise RuntimeError("Existing listening model has unexpected fields")
        templates = call("modelTemplates", {"modelName": MODEL})
        if len(templates) != 1:
            raise RuntimeError("Existing listening model must have exactly one card template")
    else:
        call("createModel", {"modelName": MODEL, "inOrderFields": FIELDS, "css": CSS,
                             "isCloze": False, "cardTemplates": [{"Name": "Listening", "Front": FRONT, "Back": BACK}]})
    call("createDeck", {"deck": DECK})
    for start in range(0, len(plan), 100):
        batch = plan[start:start + 100]
        results = call("addNotes", {"notes": [
            {"deckName": DECK, "modelName": MODEL, "fields": fields,
             "options": {"allowDuplicate": False}, "tags": ["listening", "subtitleplayer_listening"]}
            for fields in batch]})
        report["created_note_ids"].extend(nid for nid in results if nid is not None)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if any(nid is None for nid in results):
            raise RuntimeError("Some notes could not be added; see manifest and rerun to retry missing sentences")
        print(f"Created {len(report['created_note_ids'])}/{len(plan)}", flush=True)
    cards = call("findCards", {"query": 'deck:"Japanese::Listening"'})
    print(f"Verified: {len(cards)} listening cards.")


if __name__ == "__main__":
    main()
