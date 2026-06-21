#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------
# Project setup
# ---------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from SubtitlePlayer.model.anki_ruby import AddonRubyGenerator

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------

ANKI_HOST = "127.0.0.1"
ANKI_PORT = 8765

RUBY_RE = re.compile(r"\[[^\[\]]+\]")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def invoke(action: str, params: dict | None = None) -> Any:
    payload = {
        "action": action,
        "version": 6,
        "params": params or {},
    }

    req = urllib.request.Request(
        f"http://{ANKI_HOST}:{ANKI_PORT}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Cannot connect to AnkiConnect. Is Anki running?"
        ) from exc

    if data.get("error"):
        raise RuntimeError(data["error"])

    return data["result"]


def strip_ruby(text: str) -> str:
    return RUBY_RE.sub("", text or "").strip()


def normalize_compare(text: str) -> str:
    text = strip_ruby(text)
    text = re.sub(r"\s+", "", text)
    return text.strip()


def dedupe_back_field(text: str) -> tuple[str, bool]:

    if "," not in text:
        return text, False

    parts = text.split(",")

    seen = set()
    out = []
    changed = False

    for part in parts:

        key = part.strip().lower()

        if key and key in seen:
            changed = True
            continue

        if key:
            seen.add(key)

        out.append(part)

    return ",".join(out), changed

def backup_path(deck_name: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = deck_name.replace(" ", "_")
    return ROOT / f"ruby_fix_backup_{safe}_{stamp}.json"


# ---------------------------------------------------------------------
# Deck selection
# ---------------------------------------------------------------------

def choose_deck() -> str:
    print()
    print("Select deck")
    print("1) Japanese N3")
    print("2) Japanese")
    print()

    while True:
        choice = input("> ").strip()

        if choice == "1":
            return "Japanese N3"

        if choice == "2":
            return "Japanese"

        print("Invalid choice")


# ---------------------------------------------------------------------
# Anki helpers
# ---------------------------------------------------------------------

def fetch_notes(deck_name: str):
    note_ids = invoke(
        "findNotes",
        {
            "query": f'deck:"{deck_name}"'
        }
    )

    rows = []

    for start in range(0, len(note_ids), 250):
        batch = note_ids[start:start + 250]

        rows.extend(
            invoke(
                "notesInfo",
                {
                    "notes": batch
                }
            )
        )

    return rows


# ---------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------

def build_changes(notes, ruby):
    changes = []

    stats = {
        "front_fixed": 0,
        "sentence_fixed": 0,
        "fake_sentences_removed": 0,
        "back_deduped": 0,
    }

    for note in notes:

        nid = note["noteId"]

        fields = {
            k: str(v.get("value", "") or "")
            for k, v in note["fields"].items()
        }

        updates = {}
        reasons = []

        front_source = fields.get("AddRubiesToFront", "")
        sentence_source = fields.get("AddRubiesToSentenceJA", "")

        clean_front_source = strip_ruby(front_source)
        clean_sentence_source = strip_ruby(sentence_source)

        # -------------------------------------------------------------
        # FRONT
        # -------------------------------------------------------------

        if clean_front_source and has_japanese(clean_front_source):

            try:
                new_front = ruby.reading(clean_front_source)
            except Exception:
                new_front = ""

            current_front = fields.get("Front", "")

            if (
                new_front
                and normalize_compare(new_front)
                != normalize_compare(current_front)
            ):
                updates["Front"] = new_front
                reasons.append("Front ruby regenerated")
                stats["front_fixed"] += 1

        # -------------------------------------------------------------
        # SENTENCE
        # -------------------------------------------------------------

        if clean_sentence_source:

            fake_sentence = (
                normalize_compare(clean_sentence_source)
                == normalize_compare(clean_front_source)
            )

            if fake_sentence:

                if fields.get("SentenceJA", ""):
                    updates["SentenceJA"] = ""

                if fields.get("AddRubiesToSentenceJA", ""):
                    updates["AddRubiesToSentenceJA"] = ""

                reasons.append("Removed fake sentence")
                stats["fake_sentences_removed"] += 1

            elif has_japanese(clean_sentence_source):

                if sentence_source != clean_sentence_source:
                    updates["AddRubiesToSentenceJA"] = clean_sentence_source
                    reasons.append(
                        "Removed ruby markup from AddRubiesToSentenceJA"
                    )

                try:
                    new_sentence = ruby.reading(clean_sentence_source)
                except Exception:
                    new_sentence = ""

                current_sentence = fields.get("SentenceJA", "")

                if (
                    new_sentence
                    and normalize_compare(new_sentence)
                    != normalize_compare(current_sentence)
                ):
                    updates["SentenceJA"] = new_sentence
                    reasons.append("SentenceJA regenerated")
                    stats["sentence_fixed"] += 1

        # -------------------------------------------------------------
        # BACK
        # -------------------------------------------------------------

        current_back = fields.get("Back", "")

        if current_back:

            new_back, changed = dedupe_back_field(current_back)

            if changed:
                updates["Back"] = new_back
                reasons.append("Removed duplicate Back entries")
                stats["back_deduped"] += 1

        # -------------------------------------------------------------

        if updates:
            changes.append(
                {
                    "nid": nid,
                    "updates": updates,
                    "fields": fields,
                    "reasons": reasons,
                }
            )

    return changes, stats


# ---------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------

def preview_changes(changes, start=0, count=10):

    end = min(start + count, len(changes))

    for idx in range(start, end):

        item = changes[idx]

        print("=" * 100)
        print(f"{idx + 1}/{len(changes)}")
        print(f"nid: {item['nid']}")

        for reason in item["reasons"]:
            print(f"  - {reason}")

        print()

        for field_name, new_value in item["updates"].items():

            old_value = item["fields"].get(field_name, "")

            print(f"FIELD: {field_name}")
            print()

            print("CURRENT:")
            print(old_value)

            print()
            print("PROPOSED:")
            print(new_value)

            print()
            print("-" * 80)

        print()

    return end


# ---------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------

def create_backup(deck_name, notes, changes):

    changed_ids = {
        row["nid"]
        for row in changes
    }

    backup = []

    for note in notes:

        if note["noteId"] not in changed_ids:
            continue

        backup.append(
            {
                "noteId": note["noteId"],
                "modelName": note["modelName"],
                "fields": {
                    k: v.get("value", "")
                    for k, v in note["fields"].items()
                },
            }
        )

    path = backup_path(deck_name)

    path.write_text(
        json.dumps(
            backup,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return path


# ---------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------

def apply_changes(changes):

    applied = 0

    for item in changes:

        invoke(
            "updateNoteFields",
            {
                "note": {
                    "id": item["nid"],
                    "fields": item["updates"],
                }
            }
        )

        applied += 1

    return applied


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():

    try:
        invoke("deckNames")
    except Exception as exc:
        print(exc)
        return 1

    deck_name = choose_deck()

    print()
    print(f"Loading deck: {deck_name}")
    print()

    notes = fetch_notes(deck_name)

    print(f"Notes found: {len(notes)}")
    print("Initializing ruby generator...")
    print()

    ruby = AddonRubyGenerator()

    changes, stats = build_changes(notes, ruby)

    print("Analysis complete")
    print()

    print(f"Notes needing changes: {len(changes)}")
    print()
    print("Statistics")
    print(f"  Front regenerated:         {stats['front_fixed']}")
    print(f"  Sentence regenerated:      {stats['sentence_fixed']}")
    print(f"  Fake sentences removed:    {stats['fake_sentences_removed']}")
    print(f"  Back deduplicated:         {stats['back_deduped']}")
    print()

    if not changes:
        print("Nothing to change.")
        return 0

    position = 0

    while True:

        position = preview_changes(
            changes,
            start=position,
            count=10,
        )

        print()
        print("[m] show next 10")
        print("[y] apply ALL changes")
        print("[n] quit")
        print()

        cmd = input("> ").strip().lower()

        if cmd == "m":

            if position >= len(changes):
                print()
                print("End of preview.")
                print()
                position = 0

            continue

        elif cmd == "n":

            print("Cancelled.")
            return 0

        elif cmd == "y":

            backup = create_backup(
                deck_name,
                notes,
                changes,
            )

            print()
            print(f"Backup written:")
            print(backup)
            print()

            confirm = input(
                "Apply changes? Type YES: "
            ).strip()

            if confirm != "YES":
                print("Cancelled.")
                return 0

            applied = apply_changes(changes)

            print()
            print(f"Applied updates: {applied}")
            print("Done.")

            return 0

        else:

            print("Unknown command")

def has_japanese(text: str) -> bool:
    for ch in text or "":
        code = ord(ch)

        if (
            0x3040 <= code <= 0x309F or
            0x30A0 <= code <= 0x30FF or
            0x3400 <= code <= 0x4DBF or
            0x4E00 <= code <= 0x9FFF or
            0xF900 <= code <= 0xFAFF
        ):
            return True

    return False

if __name__ == "__main__":
    raise SystemExit(main())