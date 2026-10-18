"""Repair ruby boundaries after line breaks; preview by default, --apply to write.

Scans SentenceJA in every note type. Only inserts ASCII spaces after raw
newlines or HTML <br> tags immediately followed by a bracket-ruby base.
Readings, line breaks, other fields, templates and styling are preserved.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import urllib.request


RUBY_AFTER_BREAK = re.compile(
    r"(?P<break><br\b[^>]*>|\r\n|\r|\n)"
    r"(?=[^\s<>\[\]]+\[[^\[\]\r\n]+\])", re.IGNORECASE
)


def repair(value: str) -> str:
    return RUBY_AFTER_BREAK.sub(lambda match: match.group(0) + " ", value)


def invoke(url: str, action: str, params: dict | None = None):
    request = urllib.request.Request(
        url, data=json.dumps({"action": action, "version": 6, "params": params or {}}).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    if result.get("error"):
        raise RuntimeError(result["error"])
    return result["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--query", default="", help="Default: all notes")
    parser.add_argument("--field", action="append", help="Repeat to check additional sentence fields; default SentenceJA")
    args = parser.parse_args()
    fields = args.field or ["SentenceJA"]
    ids = invoke(args.url, "findNotes", {"query": args.query})
    changes = []
    for start in range(0, len(ids), 250):
        for note in invoke(args.url, "notesInfo", {"notes": ids[start:start + 250]}):
            before, after = {}, {}
            for field in fields:
                if field not in note["fields"]:
                    continue
                original = note["fields"][field]["value"]
                corrected = repair(original)
                if corrected != original:
                    before[field], after[field] = original, corrected
            if after:
                changes.append({"id": note["noteId"], "model": note["modelName"], "before": before, "after": after})
    print(f"Scanned {len(ids)} notes; {len(changes)} notes need correction.")
    report = Path(__file__).resolve().parent / ("sentence_ruby_spacing_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
    report.write_text(json.dumps({"applied": args.apply, "changes": changes}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Preview / original-field backup: {report}")
    if not args.apply:
        print("No notes changed. Run with --apply to repair.")
        return
    applied = 0
    for change in changes:
        current = invoke(args.url, "notesInfo", {"notes": [change["id"]]})
        if not current or any(current[0]["fields"][field]["value"] != value for field, value in change["before"].items()):
            raise RuntimeError(f"Note {change['id']} changed since scan; stopped after {applied} updates.")
        invoke(args.url, "updateNoteFields", {"note": {"id": change["id"], "fields": change["after"]}})
        applied += 1
    print(f"Corrected {applied} notes.")


if __name__ == "__main__":
    main()
