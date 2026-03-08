#!/usr/bin/env python3
"""
Sync missing KanjiVG SVG media files for cards using live_stroke_auto_runtime.js.

The script:
1) scans note fields for HTML elements with class="kanji"
2) extracts unique kanji characters from those blocks
3) ensures matching media files exist in Anki media as stroke_<5hex>.svg
4) downloads and uploads only missing files
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_QUERY = ""
DEFAULT_SVG_BASE_URL = "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji"
DEFAULT_MEDIA_PREFIX = "stroke_"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_BATCH_SIZE = 500
FALLBACK_FIELD_HINTS = (
    "front",
    "back",
    "kanji",
    "word",
    "vocab",
    "expression",
    "term",
)

KANJI_BLOCK_RE = re.compile(
    r'(?is)<(?P<tag>[a-z0-9]+)\b[^>]*class\s*=\s*["\'][^"\']*\bkanji\b[^"\']*["\'][^>]*>(?P<inner>.*?)</(?P=tag)>'
)
SCRIPT_RE = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
STYLE_RE = re.compile(r"(?is)<style\b[^>]*>.*?</style>")
TAG_RE = re.compile(r"(?is)<[^>]+>")
INLINE_READING_RE = re.compile(r"\[[^\[\]]+\]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan Anki notes for .kanji blocks and sync missing stroke SVG media files."
        )
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"AnkiConnect host (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"AnkiConnect port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--query",
        default=DEFAULT_QUERY,
        help='Anki search query (default: all notes, i.e. "").',
    )
    parser.add_argument(
        "--sort",
        choices=("anki", "nid-asc", "nid-desc"),
        default="anki",
        help="Order before offset/limit.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip first N matched notes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max matched notes to scan (0 = all).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for notesInfo requests (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--svg-base-url",
        default=DEFAULT_SVG_BASE_URL,
        help="Base URL for raw KanjiVG SVG files.",
    )
    parser.add_argument(
        "--media-prefix",
        default=DEFAULT_MEDIA_PREFIX,
        help=f'Media filename prefix (default: "{DEFAULT_MEDIA_PREFIX}").',
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview missing files without downloading/uploading.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=25,
        help="How many missing entries to preview (default: 25).",
    )
    return parser.parse_args()


def ankiconnect_invoke(
    base_url: str, action: str, params: dict[str, Any] | None, timeout_seconds: float
) -> Any:
    payload = {"action": action, "version": 6, "params": params or {}}
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach AnkiConnect at {base_url}. Keep Anki open with AnkiConnect enabled."
        ) from exc

    parsed = json.loads(raw)
    if parsed.get("error"):
        raise RuntimeError(f'AnkiConnect error for "{action}": {parsed["error"]}')
    return parsed.get("result")


def is_kanji_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
    )


def strip_html_to_text(fragment: str) -> str:
    text = SCRIPT_RE.sub("", fragment or "")
    text = STYLE_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", "", text)
    return text


def extract_kanji_from_html(html_text: str) -> set[str]:
    out: set[str] = set()
    for match in KANJI_BLOCK_RE.finditer(html_text or ""):
        text = strip_html_to_text(match.group("inner"))
        text = INLINE_READING_RE.sub("", text)
        for ch in text:
            if is_kanji_char(ch):
                out.add(ch)
    return out


def extract_kanji_from_plain_text(raw_text: str) -> set[str]:
    out: set[str] = set()
    text = strip_html_to_text(raw_text or "")
    text = INLINE_READING_RE.sub("", text)
    for ch in text:
        if is_kanji_char(ch):
            out.add(ch)
    return out


def is_fallback_field_name(field_name: str) -> bool:
    name = (field_name or "").strip().lower()
    if not name:
        return False
    return any(hint in name for hint in FALLBACK_FIELD_HINTS)


def extract_note_kanji(note_fields: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    fallback_values: list[str] = []
    for field_name, field_data in (note_fields or {}).items():
        value = str(field_data.get("value", "") or "")
        if "kanji" not in value.lower():
            if is_fallback_field_name(str(field_name)):
                fallback_values.append(value)
            continue
        out.update(extract_kanji_from_html(value))
        if is_fallback_field_name(str(field_name)):
            fallback_values.append(value)

    # Fallback path: if there are no explicit .kanji blocks in note fields
    # (e.g. .kanji wrapper is only in card template), scan likely expression fields.
    if not out:
        for value in fallback_values:
            out.update(extract_kanji_from_plain_text(value))
    return out


def media_filename(prefix: str, kanji_char: str) -> str:
    return f"{prefix}{ord(kanji_char):05x}.svg"


def download_candidates(base: str, kanji_char: str) -> list[str]:
    code = ord(kanji_char)
    hex_candidates = [
        f"{code:05x}",
        f"{code:04x}",
        f"{code:x}",
        f"{code:05X}",
        f"{code:04X}",
        f"{code:X}",
    ]
    root = (base or "").strip().rstrip("/")
    return [f"{root}/{code_hex}.svg" for code_hex in hex_candidates]


def download_first(urls: list[str], timeout_seconds: float) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for url in urls:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read(), url
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
    raise RuntimeError(f"No candidate URL worked: {urls}") from last_error


def store_media_file(
    base_url: str, filename: str, raw: bytes, timeout_seconds: float
) -> None:
    encoded = base64.b64encode(raw).decode("ascii")
    ankiconnect_invoke(
        base_url,
        "storeMediaFile",
        {"filename": filename, "data": encoded},
        timeout_seconds,
    )


def media_exists(base_url: str, filename: str, timeout_seconds: float) -> bool:
    result = ankiconnect_invoke(
        base_url,
        "retrieveMediaFile",
        {"filename": filename},
        timeout_seconds,
    )
    return isinstance(result, str) and bool(result)


def safe_out(text: str) -> str:
    enc = sys.stdout.encoding or "utf-8"
    return text.encode(enc, errors="backslashreplace").decode(enc, errors="ignore")


def select_note_ids(base_url: str, args: argparse.Namespace) -> list[int]:
    note_ids = ankiconnect_invoke(
        base_url,
        "findNotes",
        {"query": args.query},
        args.timeout_seconds,
    )
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


def main() -> int:
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"
    query_text = args.query if args.query != "" else '(all notes, i.e. "")'
    mode_text = "DRY-RUN" if args.dry_run else "APPLY"

    print(f"Mode: {mode_text}")
    print(f"Query: {query_text}")
    print(f"SVG base URL: {args.svg_base_url}")
    print(f"Media prefix: {args.media_prefix}")
    print("")

    if "/" in args.media_prefix or "\\" in args.media_prefix:
        print(
            "Error: --media-prefix must not contain path separators ('/' or '\\').",
            file=sys.stderr,
        )
        return 1

    if args.batch_size <= 0:
        print("Error: --batch-size must be > 0.", file=sys.stderr)
        return 1

    try:
        ankiconnect_invoke(base_url, "deckNames", {}, args.timeout_seconds)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    note_ids = select_note_ids(base_url, args)
    if not note_ids:
        print("No notes matched query/filters.")
        return 0

    unique_kanji: set[str] = set()
    notes_with_kanji = 0
    scanned = 0

    for start in range(0, len(note_ids), args.batch_size):
        batch = note_ids[start : start + args.batch_size]
        rows = ankiconnect_invoke(
            base_url,
            "notesInfo",
            {"notes": batch},
            args.timeout_seconds,
        )
        for row in rows:
            fields = row.get("fields", {})
            chars = extract_note_kanji(fields)
            if chars:
                notes_with_kanji += 1
                unique_kanji.update(chars)
        scanned += len(batch)
        print(f"Scanned notes: {scanned}/{len(note_ids)}", end="\r", flush=True)
    print("")

    sorted_kanji = sorted(unique_kanji, key=lambda ch: ord(ch))
    missing_chars: list[str] = []

    for index, kanji_char in enumerate(sorted_kanji, start=1):
        filename = media_filename(args.media_prefix, kanji_char)
        if not media_exists(base_url, filename, args.timeout_seconds):
            missing_chars.append(kanji_char)
        if index % 200 == 0 or index == len(sorted_kanji):
            print(
                f"Checked media files: {index}/{len(sorted_kanji)}",
                end="\r",
                flush=True,
            )
    print("")

    print(f"Matched notes: {len(note_ids)}")
    print(f"Notes containing .kanji blocks: {notes_with_kanji}")
    print(f"Unique kanji required: {len(sorted_kanji)}")
    print(f"Missing SVG files: {len(missing_chars)}")
    print("")

    preview_count = max(0, args.top)
    for kanji_char in missing_chars[:preview_count]:
        print(
            safe_out(
                f"Missing {kanji_char} -> {media_filename(args.media_prefix, kanji_char)}"
            )
        )

    if args.dry_run:
        print("")
        print("Dry-run complete.")
        return 0

    downloaded = 0
    uploaded = 0
    failures: list[str] = []

    for kanji_char in missing_chars:
        filename = media_filename(args.media_prefix, kanji_char)
        urls = download_candidates(args.svg_base_url, kanji_char)
        try:
            raw, used_url = download_first(urls, args.timeout_seconds)
            downloaded += 1
            store_media_file(base_url, filename, raw, args.timeout_seconds)
            uploaded += 1
            print(safe_out(f"Uploaded {filename} from {used_url}"))
        except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            failures.append(f"{kanji_char} ({filename}) -> {exc}")

    print("")
    print(f"Downloaded SVG files: {downloaded}")
    print(f"Uploaded media files: {uploaded}")
    print(f"Failures: {len(failures)}")
    for line in failures[:10]:
        print(safe_out(f"  {line}"))
    print("Sync complete.")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
