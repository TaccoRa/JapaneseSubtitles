#!/usr/bin/env python3
"""
Clean and enrich the "Japanese N3" deck via AnkiConnect.

Default mode is a dry-run. Use --apply after reviewing the report.

Behavior:
- Cleans ruby markup from Front span markup.
- Fills AddRubiesToFront from Front plain text.
- If AddRubiesToSentenceJA is empty, looks up an example sentence on Tatoeba
  for AddRubiesToFront.
- If a Tatoeba example sentence is found, fills:
  - AddRubiesToSentenceJA (plain Japanese sentence)
  - SentenceJA (ruby-formatted sentence)
  - SentenceDE (DeepL translation)
  - Sound (Tatoeba audio, if available)
- Deduplicates comma-separated Back entries case-insensitively without
  reformatting comma spacing.
- Can audit existing rubies with --audit-all-rubies.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from SubtitlePlayer.model.anki_ruby import AddonRubyGenerator  # noqa: E402


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_DECK = "Japanese N3"
DEFAULT_BACKUP = "anki_modify/japanese_n3_cleanup_backup.json"

DEEPL_URL = "https://api-free.deepl.com/v2/translate"
TATOEBA_SEARCH_URL = "https://api.tatoeba.org/v1/sentences"
TATOEBA_AUDIO_MP3_URL = "https://audio.tatoeba.org/sentences/jpn/{audio_id}.mp3"

SCRIPT_RE = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
STYLE_RE = re.compile(r"(?is)<style\b[^>]*>.*?</style>")
TAG_RE = re.compile(r"(?is)<[^>]+>")
BR_RE = re.compile(r"(?is)<br\s*/?>")
INLINE_READING_RE = re.compile(r"\[[^\[\]]+\]")
KANJI_SPAN_RE = re.compile(
    r'(?is)<(?P<tag>[a-z0-9]+)\b(?P<attrs>[^>]*class\s*=\s*["\'][^"\']*\bkanji\b[^"\']*["\'][^>]*)>'
    r"(?P<inner>.*?)</(?P=tag)>"
)

AUDIO_MIME_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

TATOEBA_CACHE: dict[str, dict[str, Any] | None] = {}


@dataclass
class NotePlan:
    note_id: int
    fields: dict[str, str]
    reasons: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--deck", default=DEFAULT_DECK)
    parser.add_argument("--query", default="", help="Override Anki search query.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--sort", choices=("anki", "nid-asc", "nid-desc"), default="anki")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--overwrite-filled", action="store_true")
    parser.add_argument("--skip-enrichment", action="store_true")
    parser.add_argument("--audit-all-rubies", action="store_true")
    parser.add_argument("--backup-path", default=DEFAULT_BACKUP)
    parser.add_argument("--ruby-report-path", default="anki_modify/ruby_audit_report.txt")
    return parser.parse_args()


def invoke(base_url: str, action: str, params: dict[str, Any] | None, timeout: float) -> Any:
    payload = {"action": action, "version": 6, "params": params or {}}
    raw = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach AnkiConnect at {base_url}. Keep Anki open and enable AnkiConnect."
        ) from exc
    data = json.loads(body)
    if data.get("error"):
        raise RuntimeError(f'AnkiConnect error for "{action}": {data["error"]}')
    return data.get("result")


def strip_html(value: str) -> str:
    text = SCRIPT_RE.sub("", value or "")
    text = STYLE_RE.sub("", text)
    text = BR_RE.sub(" ", text)
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_inline_readings(value: str) -> str:
    return INLINE_READING_RE.sub("", value or "").strip()


def is_japanese_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    )


def has_japanese(text: str) -> bool:
    return any(is_japanese_char(ch) for ch in text or "")


def normalize_japanese_spacing(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    previous = None
    while previous != value:
        previous = value
        value = re.sub(
            r"([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])\s+([\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff])",
            r"\1\2",
            value,
        )
    return value


def is_vocab_like_term(term: str) -> bool:
    cleaned = normalize_japanese_spacing(term)
    if not has_japanese(cleaned):
        return False
    if re.search(r"[A-Za-z:()]", cleaned):
        return False
    return len(cleaned) <= 40


def clean_kanji_span_readings(front_html: str) -> tuple[str, int]:
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        inner = match.group("inner")
        cleaned_inner = strip_inline_readings(inner)
        if cleaned_inner != inner:
            count += 1
        return f"<{match.group('tag')}{match.group('attrs')}>{cleaned_inner}</{match.group('tag')}>"

    return KANJI_SPAN_RE.sub(repl, front_html or ""), count


def plain_from_front(front_html: str) -> str:
    return normalize_japanese_spacing(strip_inline_readings(strip_html(front_html)))


def select_note_ids(base_url: str, args: argparse.Namespace) -> list[int]:
    query = args.query.strip() or f'deck:"{args.deck}"'
    note_ids = invoke(base_url, "findNotes", {"query": query}, args.timeout_seconds) or []
    if args.sort == "nid-asc":
        note_ids = sorted(note_ids)
    elif args.sort == "nid-desc":
        note_ids = sorted(note_ids, reverse=True)
    if args.offset > 0:
        note_ids = note_ids[args.offset :]
    if args.limit > 0:
        note_ids = note_ids[: args.limit]
    return note_ids


def fetch_notes(base_url: str, note_ids: list[int], timeout: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for start in range(0, len(note_ids), 250):
        rows.extend(invoke(base_url, "notesInfo", {"notes": note_ids[start:start + 250]}, timeout))
    return rows


def http_json(url: str, params: dict[str, str] | None, timeout: float) -> Any:
    full_url = url
    if params:
        full_url += "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        full_url,
        method="GET",
        headers={"User-Agent": "Mozilla/5.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return {}
    except Exception:
        return {}


def deepl_translate(text: str, source: str, target: str, timeout: float) -> str:
    key = (
        os.environ.get("DEEPL_TOKEN")
        or os.environ.get("DEEPL_AUTH_KEY")
        or os.environ.get("DEEPL_API_KEY")
        or ""
    ).strip()
    if not text.strip() or not key:
        return ""
    data = urllib.parse.urlencode(
        {"text": text.strip(), "source_lang": source.upper(), "target_lang": target.upper()}
    ).encode("utf-8")
    request = urllib.request.Request(
        DEEPL_URL,
        data=data,
        method="POST",
        headers={"Authorization": f"DeepL-Auth-Key {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError:
        return ""
    translations = payload.get("translations") or []
    if not translations:
        return ""
    return str(translations[0].get("text") or "").strip()


def jisho_definitions(word: str, timeout: float) -> list[str]:
    try:
        payload = http_json("https://jisho.org/api/v1/search/words", {"keyword": word}, timeout)
    except Exception:
        return []
    entries = payload.get("data") if isinstance(payload, dict) else []
    if not entries:
        return []
    best = entries[0]
    best_score = -1
    for entry in entries:
        score = 0
        for jp in entry.get("japanese") or []:
            if jp.get("word") == word:
                score += 100
            if jp.get("reading") == word:
                score += 80
        if score > best_score:
            best = entry
            best_score = score
    out: list[str] = []
    for sense in best.get("senses") or []:
        for item in sense.get("english_definitions") or []:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
    return out


def normalize_tatoeba_query(text: str) -> str:
    text = strip_inline_readings(strip_html(text))
    text = html.unescape(text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[。、，「」『』（）」()［］【】!?！？…・,;:]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tatoeba_query_variants(term: str) -> list[str]:
    cleaned = normalize_tatoeba_query(term)
    if not cleaned:
        return []
    variants: list[str] = []
    for candidate in (cleaned, cleaned.replace(" ", ""), f'"{cleaned}"'):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return variants


def tatoeba_search_sentences(term: str, timeout: float) -> list[dict[str, Any]]:
    for q in tatoeba_query_variants(term):
        params: dict[str, str] = {
            "lang": "jpn",
            "q": q,
            "sort": "relevance",
            "include": "audios",
        }
        payload = http_json(TATOEBA_SEARCH_URL, params, timeout)
        results = payload.get("data") if isinstance(payload, dict) else payload.get("results") if isinstance(payload, dict) else []
        if isinstance(results, list) and results:
            return results
    return []


def first_audio_id(sentence_row: dict[str, Any]) -> int | None:
    audios = sentence_row.get("audios") or []
    for audio in audios:
        if not isinstance(audio, dict):
            continue
        for key in ("id", "audio_id"):
            try:
                value = int(audio.get(key) or 0)
            except Exception:
                value = 0
            if value > 0:
                return value
    return None


def pick_tatoeba_sentence(rows: list[dict[str, Any]], term: str) -> dict[str, Any] | None:
    if not rows:
        return None

    term_norm = re.sub(r"\s+", "", normalize_tatoeba_query(term))

    exact_audio: dict[str, Any] | None = None
    exact: dict[str, Any] | None = None
    contains_audio: dict[str, Any] | None = None
    contains: dict[str, Any] | None = None
    any_audio: dict[str, Any] | None = None

    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:
            continue

        text_norm = re.sub(r"\s+", "", normalize_tatoeba_query(text))
        audio_id = first_audio_id(row)

        if audio_id is not None and any_audio is None:
            any_audio = row

        if term_norm and text_norm == term_norm:
            if audio_id is not None and exact_audio is None:
                exact_audio = row
            if exact is None:
                exact = row
            continue

        if term_norm and term_norm in text_norm:
            if audio_id is not None and contains_audio is None:
                contains_audio = row
            if contains is None:
                contains = row

    return exact_audio or exact or contains_audio or contains or any_audio or rows[0]


def tatoeba_example_sentence(term: str, timeout: float) -> dict[str, Any] | None:
    cache_key = normalize_tatoeba_query(term)
    if cache_key in TATOEBA_CACHE:
        return TATOEBA_CACHE[cache_key]

    rows = tatoeba_search_sentences(term, timeout)
    candidate = pick_tatoeba_sentence(rows, term)
    if candidate is None:
        TATOEBA_CACHE[cache_key] = None
        return None

    jp = normalize_japanese_spacing(str(candidate.get("text") or "").strip())
    if not jp:
        TATOEBA_CACHE[cache_key] = None
        return None

    out: dict[str, Any] = {
        "jp": jp,
        "sentence_id": int(candidate.get("id") or 0) if str(candidate.get("id") or "").isdigit() else 0,
        "audio_id": first_audio_id(candidate),
    }
    TATOEBA_CACHE[cache_key] = out
    return out


def download_url_bytes(url: str, timeout: float) -> tuple[bytes, str]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type() or ""


def audio_filename_for(audio_id: int) -> str:
    return f"tatoeba_{audio_id}.mp3"


def ensure_audio_media(base_url: str, audio_id: int, timeout: float) -> str:
    audio_url = TATOEBA_AUDIO_MP3_URL.format(audio_id=audio_id)
    data, _mime_type = download_url_bytes(audio_url, timeout)
    filename = audio_filename_for(audio_id)

    existing = invoke(base_url, "retrieveMediaFile", {"filename": filename}, timeout)
    if not existing:
        invoke(
            base_url,
            "storeMediaFile",
            {
                "filename": filename,
                "data": base64.b64encode(data).decode("ascii"),
            },
            timeout,
        )

    return filename


def maybe_set(
    updates: dict[str, str],
    fields: dict[str, str],
    name: str,
    value: str,
    reasons: list[str],
    reason: str,
    overwrite: bool,
) -> None:
    if name not in fields or not value:
        return
    current = fields.get(name, "")
    if overwrite or not strip_html(current):
        if current != value:
            updates[name] = value
            reasons.append(reason)


def dedupe_back_field(text: str) -> tuple[str, bool]:
    if "," not in text:
        return text, False

    parts = text.split(",")

    seen: set[str] = set()
    out: list[str] = []
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


def make_plan(
    row: dict[str, Any],
    ruby: AddonRubyGenerator,
    args: argparse.Namespace,
) -> NotePlan | None:
    note_id = int(row["noteId"])
    fields = {k: str(v.get("value", "") or "") for k, v in row.get("fields", {}).items()}
    updates: dict[str, str] = {}
    reasons: list[str] = []

    front = fields.get("Front", "")
    plain = plain_from_front(front)
    cleaned_front, removed = clean_kanji_span_readings(front)

    if removed and cleaned_front != front:
        updates["Front"] = cleaned_front
        reasons.append(f"removed {removed} bracket ruby reading(s) from Front .kanji span")

    maybe_set(
        updates,
        fields,
        "AddRubiesToFront",
        plain,
        reasons,
        "filled AddRubiesToFront with Front text without rubies",
        True,
    )

    if not args.skip_enrichment and plain and is_vocab_like_term(plain):
        overwrite = bool(args.overwrite_filled)

        current_sentence_source = normalize_japanese_spacing(
            strip_inline_readings(strip_html(fields.get("AddRubiesToSentenceJA", "")))
        )

        lookup_term = current_sentence_source or plain
        sentence_row = tatoeba_example_sentence(lookup_term, args.timeout_seconds) if lookup_term else None

        sentence_source = current_sentence_source
        if not sentence_source and sentence_row:
            sentence_source = sentence_row["jp"]

        if sentence_source:
            if not current_sentence_source:
                maybe_set(
                    updates,
                    fields,
                    "AddRubiesToSentenceJA",
                    sentence_source,
                    reasons,
                    "filled AddRubiesToSentenceJA with example Japanese sentence",
                    overwrite,
                )

            sentence_ruby = ruby.reading(sentence_source)
            maybe_set(
                updates,
                fields,
                "SentenceJA",
                sentence_ruby,
                reasons,
                "filled SentenceJA from example sentence with rubies",
                overwrite,
            )

            sentence_de = deepl_translate(sentence_source, "JA", "DE", args.timeout_seconds)
            maybe_set(
                updates,
                fields,
                "SentenceDE",
                sentence_de,
                reasons,
                "filled SentenceDE with DeepL German translation of the example sentence",
                overwrite,
            )

            if sentence_row is None:
                sentence_row = tatoeba_example_sentence(sentence_source, args.timeout_seconds)

            if sentence_row is not None:
                audio_id = sentence_row.get("audio_id")
                if isinstance(audio_id, int) and audio_id > 0:
                    try:
                        filename = ensure_audio_media(base_url, audio_id, args.timeout_seconds)
                        maybe_set(
                            updates,
                            fields,
                            "Sound",
                            f"[sound:{filename}]",
                            reasons,
                            "filled Sound with Tatoeba audio for the example sentence",
                            overwrite,
                        )
                    except Exception:
                        pass

        definitions = jisho_definitions(plain, args.timeout_seconds)
        if definitions:
            back_source = "; ".join(definitions[:3])
            back_de = deepl_translate(back_source, "EN", "DE", args.timeout_seconds) or back_source
            definition_en = "; ".join(definitions)
        else:
            back_de = deepl_translate(plain, "JA", "DE", args.timeout_seconds)
            definition_en = deepl_translate(plain, "JA", "EN", args.timeout_seconds)

        maybe_set(
            updates,
            fields,
            "Definition",
            definition_en,
            reasons,
            "filled Definition with English translation",
            overwrite,
        )
        maybe_set(
            updates,
            fields,
            "Back",
            back_de,
            reasons,
            "filled Back with German meanings",
            overwrite,
        )

    current_back = fields.get("Back", "")
    if current_back:
        new_back, changed = dedupe_back_field(current_back)
        if changed:
            updates["Back"] = new_back
            reasons.append("Removed duplicate Back entries")

    if not updates:
        return None

    return NotePlan(note_id=note_id, fields=updates, reasons=reasons)


def audit_rubies(rows: list[dict[str, Any]], ruby: AddonRubyGenerator, top: int) -> list[str]:
    report: list[str] = []
    for row in rows:
        nid = row.get("noteId")
        fields = {k: str(v.get("value", "") or "") for k, v in row.get("fields", {}).items()}
        for name in ("Front", "SentenceJA"):
            value = fields.get(name, "")
            if not value or "[" not in value:
                continue
            plain = plain_from_front(value)
            generated = ruby.reading(plain) if plain else ""
            current = strip_html(value)
            if generated and strip_inline_readings(current) == plain and generated != current:
                report.append(f"nid:{nid} {name}: current={current!r} generated={generated!r}")
                if len(report) >= top:
                    return report
    return report


def safe_out(text: str) -> str:
    enc = sys.stdout.encoding or "utf-8"
    return text.encode(enc, errors="backslashreplace").decode(enc, errors="ignore")


def preview(value: str, limit: int = 140) -> str:
    one = re.sub(r"\s+", " ", strip_html(value)).strip()
    return one if len(one) <= limit else one[: limit - 3] + "..."


def backup_output_path(root: Path, raw_path: str, apply: bool) -> Path:
    path = root / raw_path
    if not apply:
        return path.with_name(f"{path.stem}.dry_run{path.suffix}")
    if not path.exists():
        return path
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}.{stamp}{path.suffix}")


def main() -> int:
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"
    mode = "APPLY" if args.apply else "DRY-RUN"
    query = args.query.strip() or ("" if args.audit_all_rubies else f'deck:"{args.deck}"')

    try:
        invoke(base_url, "deckNames", {}, args.timeout_seconds)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    note_ids = (
        select_note_ids(base_url, args)
        if not args.audit_all_rubies
        else invoke(base_url, "findNotes", {"query": query}, args.timeout_seconds)
    )
    if args.audit_all_rubies and args.limit > 0:
        note_ids = note_ids[: args.limit]

    rows = fetch_notes(base_url, note_ids, args.timeout_seconds) if note_ids else []
    ruby = AddonRubyGenerator()

    if args.audit_all_rubies:
        findings = audit_rubies(rows, ruby, 1_000_000)
        report_path = ROOT / args.ruby_report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(findings) + ("\n" if findings else ""), encoding="utf-8")
        print(f"Mode: {mode}")
        print("Operation: audit-all-rubies")
        print(f"Scanned notes: {len(rows)}")
        print(f"Ruby differences found: {len(findings)}")
        print(f"Report written: {report_path}")
        for line in findings[: max(args.top, 0)]:
            print(safe_out(line))
        return 0

    plans = [p for row in rows if (p := make_plan(row, ruby, args))]
    print(f"Mode: {mode}")
    print("Operation: cleanup-japanese-n3")
    print(f"Query: {query}")
    print(f"Scanned notes: {len(rows)}")
    print(f"Notes to update: {len(plans)}")
    print(
        f"DeepL configured: {bool(os.environ.get('DEEPL_TOKEN') or os.environ.get('DEEPL_AUTH_KEY') or os.environ.get('DEEPL_API_KEY'))}"
    )
    print("")

    for plan in plans[: max(args.top, 0)]:
        print(f"nid:{plan.note_id}")
        for reason in plan.reasons:
            print(f"  - {safe_out(reason)}")
        print(f"  fields: {', '.join(plan.fields)}")
        for field_name, value in plan.fields.items():
            print(f"    {field_name}: {safe_out(preview(value))}")

    backup_path = backup_output_path(ROOT, args.backup_path, args.apply)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup = [
        {
            "noteId": row.get("noteId"),
            "modelName": row.get("modelName"),
            "fields": {k: v.get("value", "") for k, v in row.get("fields", {}).items()},
        }
        for row in rows
        if any(plan.note_id == row.get("noteId") for plan in plans)
    ]
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    print("")
    print(f"Backup written: {backup_path}")

    if not args.apply:
        print("Dry-run complete. Re-run with --apply to update Anki.")
        return 0

    applied = 0
    failures: list[str] = []
    for plan in plans:
        try:
            invoke(
                base_url,
                "updateNoteFields",
                {"note": {"id": plan.note_id, "fields": plan.fields}},
                args.timeout_seconds,
            )
            applied += 1
        except RuntimeError as exc:
            failures.append(f"nid:{plan.note_id}: {exc}")

    print(f"Applied updates: {applied}/{len(plans)}")
    print(f"Failures: {len(failures)}")
    for line in failures[:10]:
        print(f"  {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())