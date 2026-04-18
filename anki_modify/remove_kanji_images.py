#!/usr/bin/env python3
"""
Remove image tags from Front field notes that contain .kanji spans.

Default is dry-run. Use --apply to write changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_NOTE_TYPE = 'Standard (und umgekehrte Karte) Japanese'
DEFAULT_QUERY = f'note:"{DEFAULT_NOTE_TYPE}"'

KANJI_SPAN_RE = re.compile(r'(?is)<span\b[^>]*class\s*=\s*["\'][^"\']*\bkanji\b[^"\']*["\']')
IMG_TAG_RE = re.compile(r"(?is)(?:\s*<br\s*/?>\s*)?<img\b[^>]*>")
DOUBLE_BR_RE = re.compile(r"(?is)(<br\s*/?>\s*){3,}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove image tags from Front when .kanji exists.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--limit", type=int, default=0, help="Max notes (0=all).")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sort", choices=("anki", "nid-asc", "nid-desc"), default="anki")
    parser.add_argument("--timeout-seconds", type=float, default=12.0)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def invoke(base_url: str, action: str, params: dict[str, Any] | None, timeout_seconds: float) -> Any:
    payload = {"action": action, "version": 6, "params": params or {}}
    raw = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url, data=raw, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach AnkiConnect at {base_url}. Keep Anki open with AnkiConnect enabled."
        ) from exc
    data = json.loads(body)
    if data.get("error"):
        raise RuntimeError(f'AnkiConnect error for "{action}": {data["error"]}')
    return data.get("result")


def select_note_ids(base_url: str, args: argparse.Namespace) -> list[int]:
    note_ids = invoke(base_url, "findNotes", {"query": args.query}, args.timeout_seconds)
    if not note_ids:
        return []
    if args.sort == "nid-asc":
        note_ids = sorted(note_ids)
    elif args.sort == "nid-desc":
        note_ids = sorted(note_ids, reverse=True)
    if args.offset > 0:
        note_ids = note_ids[args.offset :]
    if args.limit > 0:
        note_ids = note_ids[: args.limit]
    return note_ids


def fetch_notes_info(base_url: str, note_ids: list[int], timeout_seconds: float, batch_size: int = 500) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, len(note_ids), batch_size):
        chunk = note_ids[start : start + batch_size]
        rows.extend(invoke(base_url, "notesInfo", {"notes": chunk}, timeout_seconds))
    return rows


def clean_front(front_html: str) -> tuple[str, int]:
    if not front_html:
        return front_html, 0
    if not KANJI_SPAN_RE.search(front_html):
        return front_html, 0
    matches = IMG_TAG_RE.findall(front_html)
    if not matches:
        return front_html, 0
    cleaned = IMG_TAG_RE.sub("", front_html)
    cleaned = DOUBLE_BR_RE.sub("<br><br>", cleaned)
    return cleaned, len(matches)


def preview(s: str, limit: int = 180) -> str:
    one = re.sub(r"\s+", " ", s or "").strip()
    return one if len(one) <= limit else one[: limit - 3] + "..."


def safe_out(text: str) -> str:
    enc = sys.stdout.encoding or "utf-8"
    return text.encode(enc, errors="backslashreplace").decode(enc, errors="ignore")


def main() -> int:
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"
    mode = "APPLY" if args.apply else "DRY-RUN"

    try:
        invoke(base_url, "deckNames", {}, args.timeout_seconds)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    note_ids = select_note_ids(base_url, args)
    if not note_ids:
        print("No notes matched query/filters.")
        return 0

    info = fetch_notes_info(base_url, note_ids, args.timeout_seconds)
    plans: list[tuple[int, str, str, int]] = []
    for row in info:
        nid = row.get("noteId")
        front = row.get("fields", {}).get("Front", {}).get("value", "")
        new_front, removed = clean_front(front)
        if removed > 0 and new_front != front:
            plans.append((nid, front, new_front, removed))

    print(f"Mode: {mode}")
    print("Operation: remove-kanji-images")
    print(f"Query: {args.query}")
    print(f"Scanned notes: {len(info)}")
    print(f"Notes to update: {len(plans)}")
    print(f"Image tags to remove: {sum(p[3] for p in plans)}")
    print("")

    for nid, old, new, removed in plans[: max(0, args.top)]:
        print(f"nid:{nid} | remove {removed} image tags")
        print(f"  OLD: {safe_out(preview(old))}")
        print(f"  NEW: {safe_out(preview(new))}")

    if not args.apply:
        print("")
        print("Dry-run complete. Re-run with --apply to update Front fields.")
        return 0

    applied = 0
    failures: list[str] = []
    for nid, _old, new, _removed in plans:
        try:
            invoke(
                base_url,
                "updateNoteFields",
                {"note": {"id": nid, "fields": {"Front": new}}},
                args.timeout_seconds,
            )
            applied += 1
        except RuntimeError as exc:
            failures.append(f"nid:{nid} | {exc}")

    print("")
    print(f"Applied updates: {applied}/{len(plans)}")
    print(f"Failures: {len(failures)}")
    for line in failures[:10]:
        print(f"  {line}")
    print("Apply complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
