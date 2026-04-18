#!/usr/bin/env python3
"""
Anki note maintenance via AnkiConnect.

Supported operations:
  - normalize-front
  - cleanup-back-kanji
  - report-duplicates
  - inject-strokes

The script keeps the note type/templates unchanged and only modifies note data.
Default mode is dry-run with a preview.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import functools
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_NOTE_TYPE = 'Standard (und umgekehrte Karte) Japanese'
DEFAULT_REVIEW_TAG = "Review"
STROKE_RUNTIME_JS = "stroke_runtime.js"

IMG_RE = re.compile(r"(?is)<img\b[^>]*>")
KANJI_CLASS_RE = re.compile(
    r'(?is)<[a-z0-9]+\b[^>]*class\s*=\s*["\'][^"\']*\bkanji\b[^"\']*["\']'
)
KANJI_BLOCK_RE = re.compile(
    r'(?is)<(?P<tag>[a-z0-9]+)\b[^>]*class\s*=\s*["\'][^"\']*\bkanji\b[^"\']*["\'][^>]*>(?P<inner>.*?)</(?P=tag)>'
)
KANJI_READING_INLINE_RE = re.compile(
    r"^\s*(?P<expr>[^\[\]]+)\[(?P<reading>[^\[\]]+)\]\s*$"
)
SENTENCE_BLOCK_RE = re.compile(
    r'(?is)<(?P<tag>[a-z0-9]+)\b[^>]*class\s*=\s*["\'][^"\']*\bsentence(?:\d+)?\b[^"\']*["\'][^>]*>(?P<inner>.*?)</(?P=tag)>'
)
BR_RE = re.compile(r"(?is)<br\s*/?>")
SCRIPT_RE = re.compile(r"(?is)<script\b[^>]*>.*?</script>")
STYLE_RE = re.compile(r"(?is)<style\b[^>]*>.*?</style>")
TAG_RE = re.compile(r"(?is)<[^>]+>")
BACK_KANJI_TAG_RE = re.compile(r"(?i)\(\s*kanji\s*\)")
INLINE_READING_RE = re.compile(r"\[[^\[\]]+\]")

BLOCK_CLOSE_RE = re.compile(
    r"(?is)</(div|p|li|ul|ol|section|article|table|tr|td|th|blockquote|h[1-6])\s*>"
)


@dataclass
class PlanItem:
    note_id: int
    old_front: str
    new_front: str
    missing_sentence: bool
    front_changed: bool
    tag_will_be_added: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Anki note maintenance via AnkiConnect")
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"AnkiConnect host (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"AnkiConnect port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--note-type",
        default=DEFAULT_NOTE_TYPE,
        help=f'Note type to target (default: "{DEFAULT_NOTE_TYPE}")',
    )
    parser.add_argument(
        "--query",
        default="",
        help='Optional full Anki search query. Overrides --note-type if provided.',
    )
    parser.add_argument(
        "--operation",
        choices=(
            "normalize-front",
            "cleanup-back-kanji",
            "report-duplicates",
            "inject-strokes",
            "create-stroke-tests",
        ),
        default="normalize-front",
        help="What operation to run (default: normalize-front).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Maximum notes to process (default: 0 = all notes).",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many selected notes before processing (default: 0).",
    )
    parser.add_argument(
        "--sort",
        choices=("anki", "nid-asc", "nid-desc"),
        default="anki",
        help=(
            "Selection order before --offset/--limit. "
            "'anki' keeps Anki findNotes order; 'nid-asc'/'nid-desc' sort by note id."
        ),
    )
    parser.add_argument(
        "--review-tag",
        default=DEFAULT_REVIEW_TAG,
        help=f'Tag added when no sentence is found (default: "{DEFAULT_REVIEW_TAG}").',
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes to Anki. Without this flag, a dry run is performed.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="HTTP timeout for AnkiConnect calls (default: 10).",
    )
    parser.add_argument(
        "--report-path",
        default="duplicates_report.txt",
        help="Output path for report files in report operations.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=100,
        help="Maximum groups/samples printed to report output (default: 100).",
    )
    parser.add_argument(
        "--stroke-source-dir",
        default="",
        help="Folder containing local stroke media files (optional for inject-strokes).",
    )
    parser.add_argument(
        "--stroke-format",
        choices=("svg", "gif"),
        default="svg",
        help="Stroke media file format for inject-strokes (default: svg).",
    )
    parser.add_argument(
        "--stroke-svg-embed",
        choices=("object", "img"),
        default="object",
        help="How SVG is embedded in Front for stroke operations (default: object).",
    )
    parser.add_argument(
        "--stroke-media-prefix",
        default="stroke_",
        help="Prefix for media files stored in Anki media collection (default: stroke_).",
    )
    parser.add_argument(
        "--stroke-speed",
        type=float,
        default=1.0,
        help="Stored as data attribute on inserted stroke tag; useful for CSS/JS animation.",
    )
    parser.add_argument(
        "--stroke-gap",
        type=float,
        default=0.2,
        help="Stored as data attribute on inserted stroke tag; useful for CSS/JS animation.",
    )
    parser.add_argument(
        "--stroke-brush",
        type=int,
        choices=(0, 1),
        default=1,
        help="SVG animation flag (1/0): add brush class while drawing (default: 1).",
    )
    parser.add_argument(
        "--stroke-backward",
        type=int,
        choices=(0, 1),
        default=0,
        help="SVG animation flag (1/0): play stroke animation backward (default: 0).",
    )
    parser.add_argument(
        "--stroke-autoplay",
        type=int,
        choices=(0, 1),
        default=1,
        help="SVG animation flag (1/0): autoplay stroke sequence on card load (default: 1).",
    )
    parser.add_argument(
        "--stroke-auto-download",
        action="store_true",
        help="Auto-download missing stroke media from public GitHub raw URLs during inject-strokes.",
    )
    parser.add_argument(
        "--stroke-download-timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds for stroke auto-download requests (default: 20).",
    )
    parser.add_argument(
        "--stroke-svg-base-url",
        default="https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji",
        help="Base raw URL for SVG stroke media downloads.",
    )
    parser.add_argument(
        "--stroke-gif-base-url",
        default="",
        help=(
            "Base raw URL for GIF stroke media downloads (optional). "
            "If empty, GIF auto-download is disabled."
        ),
    )
    parser.add_argument(
        "--stroke-max-kanji",
        type=int,
        default=0,
        help="Maximum number of kanji per note to include as stroke media (0 = all).",
    )
    parser.add_argument(
        "--test-nid",
        type=int,
        default=0,
        help="Source note id used by create-stroke-tests operation.",
    )
    parser.add_argument(
        "--test-tag",
        default="stroke_test",
        help='Tag added to notes created by create-stroke-tests (default: "stroke_test").',
    )
    parser.add_argument(
        "--test-variants",
        default="gif,svg",
        help=(
            "Comma-separated variants for create-stroke-tests. "
            "Allowed: gif, svg (default: gif,svg)."
        ),
    )
    return parser.parse_args()


def ankiconnect_invoke(
    url: str, action: str, params: dict[str, Any] | None, timeout_seconds: float
) -> Any:
    payload = {
        "action": action,
        "version": 6,
        "params": params or {},
    }
    raw = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach AnkiConnect at {url}. Is Anki running with the add-on enabled?"
        ) from exc

    data = json.loads(body)
    if data.get("error"):
        raise RuntimeError(f'AnkiConnect error for "{action}": {data["error"]}')
    return data.get("result")


def strip_html_to_text(fragment: str) -> str:
    cleaned = SCRIPT_RE.sub("", fragment)
    cleaned = STYLE_RE.sub("", cleaned)
    cleaned = TAG_RE.sub("", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def is_kana_char(char: str) -> bool:
    code = ord(char)
    return (
        0x3040 <= code <= 0x309F  # Hiragana
        or 0x30A0 <= code <= 0x30FF  # Katakana
        or 0x31F0 <= code <= 0x31FF  # Katakana Extensions
        or 0xFF66 <= code <= 0xFF9D  # Half-width Katakana
        or code in {0x30FC, 0x30FB, 0xFF65}  # Long vowel mark, middle dots
    )


def is_probably_reading(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if "[" in stripped or "]" in stripped:
        return False

    ignored_codepoints = {
        0x0020,  # space
        0x3000,  # ideographic space
        0x3001,  # ideographic comma
        0x3002,  # ideographic period
        0xFF01,  # full-width exclamation mark
        0xFF1F,  # full-width question mark
        0x30FB,  # katakana middle dot
        0xFF65,  # half-width katakana middle dot
        0x002D,  # hyphen-minus
        0x0028,  # (
        0x0029,  # )
        0xFF08,  # full-width (
        0xFF09,  # full-width )
    }
    chars = [c for c in stripped if ord(c) not in ignored_codepoints]
    if not chars:
        return False

    kana_count = sum(1 for c in chars if is_kana_char(c))
    ratio = kana_count / len(chars)
    return kana_count > 0 and ratio >= 0.9


def contains_kanji(text: str) -> bool:
    for char in text:
        code = ord(char)
        if (
            0x3400 <= code <= 0x4DBF  # CJK Extension A
            or 0x4E00 <= code <= 0x9FFF  # Common CJK Unified Ideographs
            or 0xF900 <= code <= 0xFAFF  # CJK Compatibility Ideographs
        ):
            return True
    return False


def to_hiragana_char(char: str) -> str:
    code = ord(char)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    if code in {0x30FD, 0x30FE}:
        return chr(code - 0x60)
    return char


def normalize_kana(text: str) -> str:
    return "".join(to_hiragana_char(ch) for ch in text)


def build_inline_furigana(expression: str, reading: str) -> str:
    expr = re.sub(r"\s+", "", expression or "")
    yomi = re.sub(r"\s+", "", reading or "")
    if not expr:
        return ""
    if not yomi:
        return expr
    if not contains_kanji(expr):
        return expr

    expr_chars = list(expr)
    reading_norm = normalize_kana(yomi)
    n = len(expr_chars)
    m = len(reading_norm)

    suffix_kanji = [0] * (n + 1)
    suffix_kana = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_kanji[i] = suffix_kanji[i + 1]
        suffix_kana[i] = suffix_kana[i + 1]
        ch = expr_chars[i]
        if contains_kanji(ch):
            suffix_kanji[i] += 1
        elif is_kana_char(ch):
            suffix_kana[i] += 1

    @functools.lru_cache(maxsize=None)
    def solve(i: int, pos: int) -> tuple[int, tuple[str | None, ...]] | None:
        if i == n:
            if pos == m:
                return 0, ()
            return None

        ch = expr_chars[i]

        if contains_kanji(ch):
            min_rest = suffix_kanji[i + 1] + suffix_kana[i + 1]
            max_end = m - min_rest
            if max_end < pos + 1:
                return None

            remaining_kanji = suffix_kanji[i]
            available_for_kanji = (m - pos) - suffix_kana[i]

            best_key: tuple[int, int] | None = None
            best_payload: tuple[int, tuple[str | None, ...]] | None = None
            for end in range(pos + 1, max_end + 1):
                child = solve(i + 1, end)
                if child is None:
                    continue

                child_cost, child_segments = child
                seg_len = end - pos
                local_cost = abs(seg_len * remaining_kanji - available_for_kanji)
                total_cost = local_cost + child_cost
                key = (total_cost, seg_len)
                payload = (total_cost, (yomi[pos:end],) + child_segments)

                if best_key is None or key < best_key:
                    best_key = key
                    best_payload = payload

            return best_payload

        if is_kana_char(ch):
            if pos >= m:
                return None
            if normalize_kana(ch) != reading_norm[pos]:
                return None
            child = solve(i + 1, pos + 1)
            if child is None:
                return None
            child_cost, child_segments = child
            return child_cost, (None,) + child_segments

        child = solve(i + 1, pos)
        if child is None:
            return None
        child_cost, child_segments = child
        return child_cost, (None,) + child_segments

    solved = solve(0, 0)
    if solved is None:
        return f"{expr}[{yomi}]"

    _, segments = solved
    out: list[str] = []
    for ch, seg in zip(expr_chars, segments):
        if seg is None:
            out.append(ch)
        else:
            out.append(f"{ch}[{seg}]")
    return "".join(out)


def upgrade_existing_kanji_block(front_html: str) -> str:
    match = KANJI_BLOCK_RE.search(front_html)
    if match is None:
        return front_html

    inner_text = strip_html_to_text(match.group("inner"))
    inline_match = KANJI_READING_INLINE_RE.match(inner_text)
    if inline_match is None:
        return front_html

    expr = inline_match.group("expr").strip()
    reading = inline_match.group("reading").strip()
    if not contains_kanji(expr) or not is_probably_reading(reading):
        return front_html

    upgraded = build_inline_furigana(expr, reading)
    if upgraded == inner_text:
        return front_html

    start, end = match.span("inner")
    return front_html[:start] + html.escape(upgraded, quote=False) + front_html[end:]


def extract_text_lines(front_html: str) -> list[str]:
    no_script = SCRIPT_RE.sub("", front_html)
    no_script = STYLE_RE.sub("", no_script)
    textish = BR_RE.sub("\n", no_script)
    textish = BLOCK_CLOSE_RE.sub("\n", textish)
    textish = TAG_RE.sub("", textish)
    textish = html.unescape(textish)

    lines: list[str] = []
    for raw in textish.splitlines():
        normalized = re.sub(r"\s+", " ", raw).strip()
        if normalized:
            lines.append(normalized)
    return lines


def has_sentence_content(front_html: str) -> bool:
    for match in SENTENCE_BLOCK_RE.finditer(front_html):
        inner = match.group("inner")
        if strip_html_to_text(inner):
            return True
    return False


def normalize_front(front_html: str) -> tuple[str, bool]:
    original = front_html or ""
    original = original.strip()

    if not original:
        return "", True

    # If already wrapped with kanji class, keep text intact.
    if KANJI_CLASS_RE.search(original):
        upgraded = upgrade_existing_kanji_block(original)
        return upgraded, not has_sentence_content(upgraded)

    images = IMG_RE.findall(original)
    without_images = IMG_RE.sub("", original)
    lines = extract_text_lines(without_images)

    if not lines:
        # No text to infer structure from.
        return original, True

    kanji = lines[0]
    reading = ""
    sentence_lines: list[str]

    # Case: line1 is reading and line2 is expression with kanji.
    if (
        len(lines) >= 2
        and is_probably_reading(lines[0])
        and contains_kanji(lines[1])
        and not contains_kanji(lines[0])
    ):
        reading = lines[0]
        kanji = lines[1]
        sentence_lines = lines[2:]
    elif len(lines) >= 2 and is_probably_reading(lines[1]) and "[" not in kanji and "]" not in kanji:
        reading = lines[1]
        sentence_lines = lines[2:]
    else:
        sentence_lines = lines[1:]

    kanji = kanji.strip()
    if reading:
        kanji = build_inline_furigana(kanji, reading.strip())

    parts = [f'<span class="kanji">{html.escape(kanji, quote=False)}</span>']
    parts.extend(img.strip() for img in images if img.strip())

    if sentence_lines:
        sentence_html = "<br>".join(html.escape(s, quote=False) for s in sentence_lines)
        parts.append(f'<span class="sentence">{sentence_html}</span>')

    new_front = "<br>".join(parts)
    return new_front, not bool(sentence_lines)


def preview(text: str, max_len: int = 180) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 3] + "..."


def resolve_query(args: argparse.Namespace) -> str:
    return args.query.strip() or f'note:"{args.note_type}"'


def select_note_ids(base_url: str, query: str, args: argparse.Namespace) -> list[int]:
    if args.offset < 0:
        raise RuntimeError("--offset must be >= 0")

    note_ids = ankiconnect_invoke(
        base_url, "findNotes", {"query": query}, args.timeout_seconds
    )
    if not note_ids:
        print(f'No notes matched query: {query}')
        return []

    if args.sort == "nid-asc":
        note_ids = sorted(note_ids)
    elif args.sort == "nid-desc":
        note_ids = sorted(note_ids, reverse=True)

    if args.offset > 0:
        note_ids = note_ids[args.offset :]

    if args.limit > 0:
        note_ids = note_ids[: args.limit]
    if not note_ids:
        return []
    return note_ids


def fetch_notes_info(
    base_url: str, note_ids: list[int], timeout_seconds: float, batch_size: int = 500
) -> list[dict[str, Any]]:
    info_list: list[dict[str, Any]] = []
    for start in range(0, len(note_ids), batch_size):
        chunk = note_ids[start : start + batch_size]
        chunk_info = ankiconnect_invoke(
            base_url, "notesInfo", {"notes": chunk}, timeout_seconds
        )
        info_list.extend(chunk_info)
    return info_list


def ensure_collection_open(base_url: str, timeout_seconds: float) -> None:
    try:
        ankiconnect_invoke(base_url, "deckNames", {}, timeout_seconds)
    except RuntimeError as exc:
        raise RuntimeError(
            "Operation requires Anki to stay open with a profile/collection loaded."
        ) from exc


def extract_main_expression(front_html: str) -> str:
    original = (front_html or "").strip()
    if not original:
        return ""

    kanji_match = KANJI_BLOCK_RE.search(original)
    if kanji_match is not None:
        value = strip_html_to_text(kanji_match.group("inner"))
    else:
        without_images = IMG_RE.sub("", original)
        lines = extract_text_lines(without_images)
        if not lines:
            return ""
        value = lines[0]

    value = INLINE_READING_RE.sub("", value)
    value = re.sub(r"\s+", "", value)
    return value.strip()


def unique_kanji_chars(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for char in text:
        if not contains_kanji(char):
            continue
        if char in seen:
            continue
        seen.add(char)
        out.append(char)
    return out


def remove_back_kanji_tag(back_html: str) -> str:
    original = back_html or ""
    return BACK_KANJI_TAG_RE.sub("", original)


def build_stroke_media_tag(
    media_name: str, args: argparse.Namespace, dom_id: str = ""
) -> str:
    if args.stroke_format == "svg" and getattr(args, "stroke_svg_embed", "object") == "object":
        id_attr = f' id="{html.escape(dom_id, quote=True)}"' if dom_id else ""
        return (
            '<object{dom_id} data="{name}" type="image/svg+xml" class="stroke-order-media stroke-svg-object" '
            'data-stroke-speed="{speed}" data-stroke-gap="{gap}" data-stroke-brush="{brush}" '
            'data-stroke-backward="{backward}" data-stroke-autoplay="{autoplay}" '
            'style="visibility:hidden"></object>'
        ).format(
            dom_id=id_attr,
            name=html.escape(media_name, quote=True),
            speed=args.stroke_speed,
            gap=args.stroke_gap,
            brush=args.stroke_brush,
            backward=args.stroke_backward,
            autoplay=args.stroke_autoplay,
        )
    return (
        '<img src="{name}" class="stroke-order-media" data-stroke-speed="{speed}" '
        'data-stroke-gap="{gap}">'
    ).format(name=html.escape(media_name, quote=True), speed=args.stroke_speed, gap=args.stroke_gap)


def build_svg_animation_runtime_js() -> str:
    return (
        "(function(){\n"
        "if(window.__kanjiStrokeInit){return;}\n"
        "function num(v,d){const n=parseFloat(v);return Number.isFinite(n)?n:d;}\n"
        "function boolish(v,d){if(v===undefined||v===null||String(v).trim()===''){return d;}const s=String(v).trim().toLowerCase();if(s==='1'||s==='true'||s==='yes'||s==='on'){return true;}if(s==='0'||s==='false'||s==='no'||s==='off'){return false;}const n=parseFloat(s);if(Number.isFinite(n)){return n!==0;}return d;}\n"
        "function readSettings(el){const css=getComputedStyle(el);const speed=Math.max(0.01,num(css.getPropertyValue('--stroke-speed'),num(el.dataset.strokeSpeed,1)));const gap=Math.max(0,num(css.getPropertyValue('--stroke-gap'),num(el.dataset.strokeGap,0.25)));const brush=boolish(css.getPropertyValue('--stroke-brush'),boolish(el.dataset.strokeBrush,true));const backward=boolish(css.getPropertyValue('--stroke-backward'),boolish(el.dataset.strokeBackward,false));const autoplay=boolish(css.getPropertyValue('--stroke-autoplay'),boolish(el.dataset.strokeAutoplay,true));return {speed:speed,gap:gap,brush:brush,backward:backward,autoplay:autoplay};}\n"
        "function sleep(ms){return new Promise(function(resolve){setTimeout(resolve,ms);});}\n"
        "function orderedStrokes(doc){const items=[];doc.querySelectorAll('path[id*=\"-s\"]').forEach(function(path){const id=path.id||'';const m=id.match(/-s(\\d+)$/);if(!m){return;}items.push({index:parseInt(m[1],10),path:path});});items.sort(function(a,b){return a.index-b.index;});return items;}\n"
        "function prepareDoc(doc){doc.querySelectorAll('g[id*=\"StrokeNumbers\"]').forEach(function(g){g.style.display='none';});}\n"
        "function ensureBrush(doc){const ns='http://www.w3.org/2000/svg';const root=doc.querySelector('svg');if(!root){return null;}let brush=doc.getElementById('anki-stroke-brush');if(brush){return brush;}brush=doc.createElementNS(ns,'circle');brush.setAttribute('id','anki-stroke-brush');brush.setAttribute('r','3.2');brush.setAttribute('fill','#e53935');brush.setAttribute('stroke','#555');brush.setAttribute('stroke-width','1');brush.style.display='none';root.appendChild(brush);return brush;}\n"
        "function pathLength(path){let length=100;try{length=path.getTotalLength();}catch(_err){length=100;}if(!Number.isFinite(length)||length<=0){length=100;}return length;}\n"
        "function primeStrokes(strokes,backward){for(let i=0;i<strokes.length;i++){const path=strokes[i].path;const length=pathLength(path);path.dataset.strokeLength=String(length);path.style.fill='none';path.style.strokeLinecap='round';path.style.strokeLinejoin='round';path.style.strokeDasharray=String(length);path.style.transition='none';path.style.strokeDashoffset=backward?'0':String(length);} }\n"
        "function animateBrush(path,brush,durationSec,backward){if(!brush){return Promise.resolve();}return new Promise(function(resolve){const total=pathLength(path);brush.style.display='';const start=performance.now();const durationMs=Math.max(1,durationSec*1000);function tick(now){const t=Math.min(1,(now-start)/durationMs);const ratio=backward?(1-t):t;const point=path.getPointAtLength(total*ratio);brush.setAttribute('cx',String(point.x));brush.setAttribute('cy',String(point.y));if(t<1){requestAnimationFrame(tick);}else{brush.style.display='none';resolve();}}requestAnimationFrame(tick);});}\n"
        "async function runObject(obj){if(obj.dataset.strokeAnimated==='1'){return;}const doc=obj.contentDocument;if(!doc){obj.style.visibility='visible';return;}const settings=readSettings(obj);prepareDoc(doc);const strokes=orderedStrokes(doc);if(!strokes.length){obj.style.visibility='visible';return;}primeStrokes(strokes,settings.backward);obj.style.visibility='visible';if(!settings.autoplay){return;}obj.dataset.strokeAnimated='1';const brush=settings.brush?ensureBrush(doc):null;for(let i=0;i<strokes.length;i++){const path=strokes[i].path;const length=num(path.dataset.strokeLength,100);const duration=Math.max(0.15,Math.sqrt(length)/10)/settings.speed;path.style.transition='none';path.style.strokeDashoffset=settings.backward?'0':String(length);path.getBoundingClientRect();path.style.transition='stroke-dashoffset '+duration+'s ease-in-out';path.style.strokeDashoffset=settings.backward?String(length):'0';const brushRun=animateBrush(path,brush,duration,settings.backward);await sleep((duration+settings.gap)*1000);await brushRun;}}\n"
        "function bindObject(obj){if(obj.dataset.strokeBound==='1'){return;}obj.dataset.strokeBound='1';obj.style.visibility='hidden';const start=function(){runObject(obj).catch(function(){obj.style.visibility='visible';});};if(obj.contentDocument){start();}obj.addEventListener('load',start,{once:true});}\n"
        "window.__kanjiStrokeInit=function(){document.querySelectorAll('object.stroke-order-media.stroke-svg-object').forEach(bindObject);};\n"
        "})();"
    )


def build_svg_animation_script() -> str:
    runtime_src = html.escape(STROKE_RUNTIME_JS, quote=True)
    return (
        f'<script src="{runtime_src}"></script>'
        "<script>(function(){if(window.__kanjiStrokeInit){window.__kanjiStrokeInit();}})();</script>"
    )


def build_stroke_block(
    media_names: list[str], args: argparse.Namespace, note_id: int
) -> str:
    if not media_names:
        return ""

    if args.stroke_format == "svg" and args.stroke_svg_embed == "object":
        object_tags = "".join(
            build_stroke_media_tag(
                media_name, args, dom_id=f"stroke-svg-{note_id}-{idx}"
            )
            for idx, media_name in enumerate(media_names, start=1)
        )
        return object_tags + build_svg_animation_script()

    return "".join(build_stroke_media_tag(name, args) for name in media_names)


def inject_stroke_tag_into_front(front_html: str, stroke_tag: str) -> str:
    original = (front_html or "").strip()
    if not original:
        return stroke_tag

    if IMG_RE.search(original):
        return original

    kanji_match = KANJI_BLOCK_RE.search(original)
    if kanji_match is not None:
        insert_at = kanji_match.end()
        return original[:insert_at] + "<br>" + stroke_tag + original[insert_at:]

    return original + "<br>" + stroke_tag


def stroke_source_path(source_dir: str, kanji_char: str, ext: str) -> str:
    code = ord(kanji_char)
    candidates = [
        f"{code:05x}.{ext}",
        f"{code:04x}.{ext}",
        f"{code:x}.{ext}",
        f"{code:05X}.{ext}",
        f"{code:04X}.{ext}",
        f"{code:X}.{ext}",
    ]
    for filename in candidates:
        path = os.path.join(source_dir, filename)
        if os.path.isfile(path):
            return path
    return ""


def media_filename(prefix: str, kanji_char: str, ext: str) -> str:
    clean_prefix = re.sub(r"[^a-zA-Z0-9_.-]", "_", prefix or "stroke_")
    return f"{clean_prefix}{ord(kanji_char):05x}.{ext}"


def media_exists_in_anki(base_url: str, filename: str, timeout_seconds: float) -> bool:
    data = ankiconnect_invoke(
        base_url, "retrieveMediaFile", {"filename": filename}, timeout_seconds
    )
    return isinstance(data, str) and bool(data)


def upload_stroke_runtime_if_needed(
    base_url: str, timeout_seconds: float, uploaded_media: set[str] | None = None
) -> None:
    if uploaded_media is not None and STROKE_RUNTIME_JS in uploaded_media:
        return
    raw = build_svg_animation_runtime_js().encode("utf-8")
    ankiconnect_invoke(
        base_url,
        "storeMediaFile",
        {"filename": STROKE_RUNTIME_JS, "data": base64.b64encode(raw).decode("ascii")},
        timeout_seconds,
    )
    if uploaded_media is not None:
        uploaded_media.add(STROKE_RUNTIME_JS)


def stroke_download_urls(kanji_char: str, args: argparse.Namespace) -> list[str]:
    code = ord(kanji_char)
    code_candidates = [
        f"{code:05x}",
        f"{code:04x}",
        f"{code:x}",
        f"{code:05X}",
        f"{code:04X}",
        f"{code:X}",
    ]

    if args.stroke_format == "svg":
        base = (args.stroke_svg_base_url or "").rstrip("/")
        return [f"{base}/{c}.svg" for c in code_candidates]

    base = (args.stroke_gif_base_url or "").strip().rstrip("/")
    if not base:
        return []
    return [f"{base}/{c}.gif" for c in code_candidates]


def download_first_available_url(
    urls: list[str], timeout_seconds: float
) -> tuple[bytes, str]:
    last_error: Exception | None = None
    for url in urls:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read(), url
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            continue
    raise RuntimeError(f"No stroke media could be downloaded from candidates: {urls}") from last_error


def process_notes(args: argparse.Namespace) -> int:
    base_url = f"http://{args.host}:{args.port}"
    query = resolve_query(args)
    note_ids = select_note_ids(base_url, query, args)
    if not note_ids:
        print("No notes left after applying sort/offset/limit.")
        return 0

    info_list = fetch_notes_info(base_url, note_ids, args.timeout_seconds)

    plan: list[PlanItem] = []
    for info in info_list:
        note_id = info["noteId"]
        fields = info.get("fields", {})
        front = fields.get("Front", {}).get("value", "")
        tags = set(info.get("tags", []))

        new_front, missing_sentence = normalize_front(front)
        front_changed = new_front != front
        tag_will_be_added = missing_sentence and args.review_tag not in tags

        plan.append(
            PlanItem(
                note_id=note_id,
                old_front=front,
                new_front=new_front,
                missing_sentence=missing_sentence,
                front_changed=front_changed,
                tag_will_be_added=tag_will_be_added,
            )
        )

    to_update_front = [item for item in plan if item.front_changed]
    to_tag_review = [item for item in plan if item.tag_will_be_added]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Query: {query}")
    print(f"Sort: {args.sort}")
    print(f"Offset: {args.offset}")
    print(f"Scanned notes: {len(plan)}")
    print(f"Front updates: {len(to_update_front)}")
    print(f'"{args.review_tag}" tag additions: {len(to_tag_review)}')
    print("")

    for item in plan:
        actions: list[str] = []
        if item.front_changed:
            actions.append("update Front")
        if item.tag_will_be_added:
            actions.append(f'add tag "{args.review_tag}"')
        if not actions:
            actions.append("no change")
        print(f"Note nid:{item.note_id}: {', '.join(actions)}")
        if item.front_changed:
            print(f"  OLD: {preview(item.old_front)}")
            print(f"  NEW: {preview(item.new_front)}")

    if not args.apply:
        print("")
        print("Dry-run complete. Re-run with --apply to commit changes.")
        return 0

    try:
        ensure_collection_open(base_url, args.timeout_seconds)
    except RuntimeError as exc:
        raise RuntimeError(
            "Apply aborted before writing: Anki collection is not available. "
            "Keep Anki open with a profile loaded while running --apply."
        ) from exc

    applied_front = 0
    tags_applied = False
    try:
        for item in to_update_front:
            ankiconnect_invoke(
                base_url,
                "updateNoteFields",
                {"note": {"id": item.note_id, "fields": {"Front": item.new_front}}},
                args.timeout_seconds,
            )
            applied_front += 1

        if to_tag_review:
            ankiconnect_invoke(
                base_url,
                "addTags",
                {
                    "notes": [item.note_id for item in to_tag_review],
                    "tags": args.review_tag,
                },
                args.timeout_seconds,
            )
            tags_applied = True
    except RuntimeError as exc:
        tag_status = "completed" if tags_applied else "not completed"
        raise RuntimeError(
            "Apply interrupted. "
            f"Front updates applied before failure: {applied_front}/{len(to_update_front)}. "
            f'Review tag step: {tag_status}. '
            "Keep Anki open with the collection loaded while running --apply."
        ) from exc

    print("")
    print(f"Front updates applied: {applied_front}")
    print(f'"{args.review_tag}" tag step applied: {tags_applied or not to_tag_review}')
    print("Apply complete.")
    return 0


def process_cleanup_back_kanji(args: argparse.Namespace) -> int:
    base_url = f"http://{args.host}:{args.port}"
    query = resolve_query(args)
    note_ids = select_note_ids(base_url, query, args)
    if not note_ids:
        print("No notes left after applying sort/offset/limit.")
        return 0

    info_list = fetch_notes_info(base_url, note_ids, args.timeout_seconds)

    changes: list[tuple[int, str, str]] = []
    for info in info_list:
        note_id = info["noteId"]
        back = info.get("fields", {}).get("Back", {}).get("value", "")
        new_back = remove_back_kanji_tag(back)
        if new_back != back:
            changes.append((note_id, back, new_back))

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print(f"Operation: cleanup-back-kanji")
    print(f"Query: {query}")
    print(f"Sort: {args.sort}")
    print(f"Offset: {args.offset}")
    print(f"Scanned notes: {len(info_list)}")
    print(f'Back "(Kanji)" removals: {len(changes)}')
    print("")

    for note_id, old_back, new_back in changes[: max(args.top, 0)]:
        print(f"Note nid:{note_id}: update Back")
        print(f"  OLD: {preview(old_back)}")
        print(f"  NEW: {preview(new_back)}")

    if not args.apply:
        print("")
        print("Dry-run complete. Re-run with --apply to commit changes.")
        return 0

    ensure_collection_open(base_url, args.timeout_seconds)

    applied = 0
    failures: list[str] = []
    for note_id, _, new_back in changes:
        try:
            ankiconnect_invoke(
                base_url,
                "updateNoteFields",
                {"note": {"id": note_id, "fields": {"Back": new_back}}},
                args.timeout_seconds,
            )
            applied += 1
        except RuntimeError as exc:
            failures.append(f"nid:{note_id} | {exc}")

    remaining = 0
    verify_info = fetch_notes_info(base_url, note_ids, args.timeout_seconds)
    for info in verify_info:
        back = info.get("fields", {}).get("Back", {}).get("value", "")
        if BACK_KANJI_TAG_RE.search(back):
            remaining += 1

    print("")
    print(f"Back updates applied: {applied}/{len(changes)}")
    print(f"Failures: {len(failures)}")
    print(f'Remaining "(Kanji)" matches: {remaining}')
    if failures:
        print("Failure samples:")
        for line in failures[:10]:
            print(f"  {line}")
    print("Apply complete.")
    return 0


def process_report_duplicates(args: argparse.Namespace) -> int:
    base_url = f"http://{args.host}:{args.port}"
    query = resolve_query(args)
    note_ids = select_note_ids(base_url, query, args)
    if not note_ids:
        print("No notes left after applying sort/offset/limit.")
        return 0

    info_list = fetch_notes_info(base_url, note_ids, args.timeout_seconds)

    groups: dict[str, list[int]] = {}
    empty_count = 0
    for info in info_list:
        note_id = info["noteId"]
        front = info.get("fields", {}).get("Front", {}).get("value", "")
        expr = extract_main_expression(front)
        if not expr:
            empty_count += 1
            continue
        groups.setdefault(expr, []).append(note_id)

    duplicates = [
        (expr, sorted(note_ids_for_expr))
        for expr, note_ids_for_expr in groups.items()
        if len(note_ids_for_expr) > 1
    ]
    duplicates.sort(key=lambda item: (-len(item[1]), item[0]))

    lines: list[str] = []
    lines.append(f"Query: {query}")
    lines.append(f"Total notes: {len(info_list)}")
    lines.append(f"Entries with no detectable main expression: {empty_count}")
    lines.append(f"Duplicate expression groups: {len(duplicates)}")
    lines.append("")
    lines.append("Top duplicate groups:")
    for expr, group_note_ids in duplicates[: max(args.top, 0)]:
        nids_text = ", ".join(f"nid:{nid}" for nid in group_note_ids)
        lines.append(f"  {expr} x{len(group_note_ids)} -> {nids_text}")

    if args.report_path:
        with open(args.report_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    print(f"Operation: report-duplicates")
    print(f"Query: {query}")
    print(f"Scanned notes: {len(info_list)}")
    print(f"Duplicate groups: {len(duplicates)}")
    print(f"Entries with no detectable main expression: {empty_count}")
    if args.report_path:
        print(f"Report saved: {args.report_path}")
    print("")
    for line in lines[5 : 5 + min(max(args.top, 0), 20)]:
        print(line)
    return 0


def process_inject_strokes(args: argparse.Namespace) -> int:
    base_url = f"http://{args.host}:{args.port}"
    query = resolve_query(args)

    source_dir = (args.stroke_source_dir or "").strip()
    if source_dir and not os.path.isdir(source_dir):
        raise RuntimeError(f"Stroke source directory not found: {source_dir}")

    note_ids = select_note_ids(base_url, query, args)
    if not note_ids:
        print("No notes left after applying sort/offset/limit.")
        return 0
    info_list = fetch_notes_info(base_url, note_ids, args.timeout_seconds)

    plans: list[dict[str, Any]] = []
    missing_media = 0
    missing_kanji = 0
    already_has_image = 0
    using_existing_media = 0
    planned_download_items = 0
    max_kanji = max(0, int(args.stroke_max_kanji))

    for info in info_list:
        note_id = info["noteId"]
        front = info.get("fields", {}).get("Front", {}).get("value", "")
        if IMG_RE.search(front or ""):
            already_has_image += 1
            continue

        expr = extract_main_expression(front)
        kanji_chars = unique_kanji_chars(expr)
        if max_kanji > 0:
            kanji_chars = kanji_chars[:max_kanji]
        if not kanji_chars:
            missing_kanji += 1
            continue

        media_items: list[dict[str, Any]] = []
        unavailable = False
        for kanji_char in kanji_chars:
            media_name = media_filename(
                args.stroke_media_prefix, kanji_char, args.stroke_format
            )
            source_path = ""
            download_urls: list[str] = []
            needs_upload = False

            if source_dir:
                source_path = stroke_source_path(
                    source_dir, kanji_char, args.stroke_format
                )
                if source_path:
                    needs_upload = True

            media_already_exists = media_exists_in_anki(
                base_url, media_name, args.timeout_seconds
            )

            if source_path:
                pass
            elif media_already_exists:
                using_existing_media += 1
            elif args.stroke_auto_download:
                download_urls = stroke_download_urls(kanji_char, args)
                if not download_urls:
                    unavailable = True
                    break
                needs_upload = True
                planned_download_items += 1
            else:
                unavailable = True
                break

            media_items.append(
                {
                    "kanji": kanji_char,
                    "media_name": media_name,
                    "source_path": source_path,
                    "download_urls": download_urls,
                    "needs_upload": needs_upload,
                }
            )

        if unavailable:
            missing_media += 1
            continue

        media_names = [item["media_name"] for item in media_items]
        stroke_block = build_stroke_block(media_names, args, note_id)
        new_front = inject_stroke_tag_into_front(front, stroke_block)
        if new_front == front:
            already_has_image += 1
            continue

        plans.append(
            {
                "note_id": note_id,
                "old_front": front,
                "new_front": new_front,
                "media_items": media_items,
            }
        )

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print("Operation: inject-strokes")
    print(f"Query: {query}")
    print(f"Stroke source dir: {source_dir or '(not set; use existing Anki media only)'}")
    print(f"Stroke format: {args.stroke_format}")
    print(f"SVG embed mode: {args.stroke_svg_embed}")
    print(f"Auto-download: {args.stroke_auto_download}")
    print(f"Max kanji per note: {max_kanji if max_kanji else 'all'}")
    print(f"Scanned notes: {len(info_list)}")
    print(f"Planned Front updates: {len(plans)}")
    print(f"Skipped (already had image): {already_has_image}")
    print(f"Skipped (no kanji in main expression): {missing_kanji}")
    print(f"Using existing Anki media: {using_existing_media}")
    print(f"Planned downloads from remote: {planned_download_items}")
    print(f"Skipped (no source/media found): {missing_media}")
    print("")

    for plan in plans[: max(args.top, 0)]:
        names = ", ".join(item["media_name"] for item in plan["media_items"])
        print(f'Note nid:{plan["note_id"]}: add stroke media [{names}]')
        for item in plan["media_items"]:
            media_name = item["media_name"]
            source_path = item["source_path"]
            download_urls = item["download_urls"]
            if source_path:
                print(f"  {media_name}: source file {source_path}")
            elif download_urls:
                print(f"  {media_name}: download candidates {', '.join(download_urls[:2])} ...")
            else:
                print(f"  {media_name}: existing Anki media")
        print(f'  NEW: {preview(plan["new_front"])}')

    if not args.apply:
        print("")
        print("Dry-run complete. Re-run with --apply to upload media and commit Front updates.")
        return 0

    ensure_collection_open(base_url, args.timeout_seconds)

    uploaded_media: set[str] = set()
    downloaded_media: set[str] = set()
    applied = 0
    failures: list[str] = []
    needs_runtime = (
        bool(plans)
        and args.stroke_format == "svg"
        and args.stroke_svg_embed == "object"
    )
    if needs_runtime:
        try:
            upload_stroke_runtime_if_needed(
                base_url, args.timeout_seconds, uploaded_media
            )
        except RuntimeError as exc:
            raise RuntimeError(
                f"Could not upload {STROKE_RUNTIME_JS}: {exc}"
            ) from exc

    for plan in plans:
        note_id = plan["note_id"]
        new_front = plan["new_front"]
        try:
            for item in plan["media_items"]:
                media_name = item["media_name"]
                if not item["needs_upload"] or media_name in uploaded_media:
                    continue

                source_path = item["source_path"]
                download_urls = item["download_urls"]
                if source_path:
                    with open(source_path, "rb") as handle:
                        raw = handle.read()
                elif download_urls:
                    raw, used_url = download_first_available_url(
                        download_urls, args.stroke_download_timeout
                    )
                    downloaded_media.add(media_name)
                    item["used_download_url"] = used_url
                else:
                    raise RuntimeError(
                        f"Upload requested but no source is defined for {media_name}."
                    )

                ankiconnect_invoke(
                    base_url,
                    "storeMediaFile",
                    {"filename": media_name, "data": base64.b64encode(raw).decode("ascii")},
                    args.timeout_seconds,
                )
                uploaded_media.add(media_name)

            ankiconnect_invoke(
                base_url,
                "updateNoteFields",
                {"note": {"id": note_id, "fields": {"Front": new_front}}},
                args.timeout_seconds,
            )
            applied += 1
        except (RuntimeError, OSError) as exc:
            failures.append(f"nid:{note_id} | {exc}")

    print("")
    print(f"Front updates applied: {applied}/{len(plans)}")
    print(f"Uploaded media files: {len(uploaded_media)}")
    print(f"Downloaded from remote: {len(downloaded_media)}")
    print(f"Failures: {len(failures)}")
    if failures:
        print("Failure samples:")
        for line in failures[:10]:
            print(f"  {line}")
    print("Apply complete.")
    return 0


def resolve_stroke_media_items(
    base_url: str,
    kanji_chars: list[str],
    args: argparse.Namespace,
    source_dir: str,
) -> tuple[list[dict[str, Any]], int]:
    media_items: list[dict[str, Any]] = []
    using_existing_media = 0
    for kanji_char in kanji_chars:
        media_name = media_filename(args.stroke_media_prefix, kanji_char, args.stroke_format)
        source_path = ""
        needs_upload = False
        download_urls: list[str] = []

        if source_dir:
            source_path = stroke_source_path(source_dir, kanji_char, args.stroke_format)
            if source_path:
                needs_upload = True

        media_already_exists = media_exists_in_anki(
            base_url, media_name, args.timeout_seconds
        )

        if source_path:
            pass
        elif media_already_exists:
            using_existing_media += 1
        elif args.stroke_auto_download:
            download_urls = stroke_download_urls(kanji_char, args)
            if not download_urls:
                if args.stroke_format == "gif":
                    raise RuntimeError(
                        f"No stroke media for kanji '{kanji_char}' ({media_name}). "
                        "GIF auto-download is disabled because --stroke-gif-base-url is empty. "
                        "Use --stroke-source-dir with local generated GIFs or set --stroke-gif-base-url."
                    )
                raise RuntimeError(
                    f"No stroke media for kanji '{kanji_char}' ({media_name})."
                )
            needs_upload = True
        else:
            raise RuntimeError(
                f"No stroke media for kanji '{kanji_char}' ({media_name}). "
                "Provide --stroke-source-dir or enable --stroke-auto-download."
            )

        media_items.append(
            {
                "kanji": kanji_char,
                "media_name": media_name,
                "source_path": source_path,
                "download_urls": download_urls,
                "needs_upload": needs_upload,
            }
        )
    return media_items, using_existing_media


def parse_test_variants(raw: str) -> list[tuple[str, str, str]]:
    mapping = {
        "gif": ("gif", "img", "gif"),
        "svg": ("svg", "object", "svg"),
    }
    parts = [p.strip().lower() for p in (raw or "").split(",") if p.strip()]
    if not parts:
        parts = ["gif", "svg"]

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for part in parts:
        if part in seen:
            continue
        seen.add(part)
        entry = mapping.get(part)
        if entry is None:
            invalid.append(part)
            continue
        out.append(entry)

    if invalid:
        raise RuntimeError(
            f"Invalid --test-variants value(s): {', '.join(invalid)}. Allowed: gif,svg"
        )
    if not out:
        raise RuntimeError("No valid test variants selected.")
    return out


def process_create_stroke_tests(args: argparse.Namespace) -> int:
    base_url = f"http://{args.host}:{args.port}"
    source_dir = (args.stroke_source_dir or "").strip()
    if source_dir and not os.path.isdir(source_dir):
        raise RuntimeError(f"Stroke source directory not found: {source_dir}")
    if args.test_nid <= 0:
        raise RuntimeError("--test-nid is required for create-stroke-tests.")

    ensure_collection_open(base_url, args.timeout_seconds)

    info_list = ankiconnect_invoke(
        base_url, "notesInfo", {"notes": [args.test_nid]}, args.timeout_seconds
    )
    if not info_list:
        raise RuntimeError(f"Source note not found: nid:{args.test_nid}")

    source_note = info_list[0]
    fields = source_note.get("fields", {})
    if "Front" not in fields or "Back" not in fields:
        raise RuntimeError("Source note must have Front and Back fields.")

    original_front = fields["Front"]["value"]
    expr = extract_main_expression(original_front)
    kanji_chars = unique_kanji_chars(expr)
    max_kanji = max(0, int(args.stroke_max_kanji))
    if max_kanji > 0:
        kanji_chars = kanji_chars[:max_kanji]
    if not kanji_chars:
        raise RuntimeError("No kanji detected in source note Front.")

    model_name = source_note.get("modelName")
    if not model_name:
        raise RuntimeError("Could not determine source note model name.")

    card_ids = ankiconnect_invoke(
        base_url, "findCards", {"query": f"nid:{args.test_nid}"}, args.timeout_seconds
    )
    if not card_ids:
        raise RuntimeError("Source note has no cards; cannot determine deck.")
    cards_info = ankiconnect_invoke(
        base_url, "cardsInfo", {"cards": [card_ids[0]]}, args.timeout_seconds
    )
    deck_name = cards_info[0]["deckName"]

    base_fields = {name: data.get("value", "") for name, data in fields.items()}
    source_tags = list(source_note.get("tags", []))

    variants = parse_test_variants(args.test_variants)

    plans: list[dict[str, Any]] = []
    total_existing_media_hits = 0
    skipped_variants: list[tuple[str, str]] = []
    for fmt, embed, label in variants:
        variant_args = argparse.Namespace(**vars(args))
        variant_args.stroke_format = fmt
        variant_args.stroke_svg_embed = embed

        try:
            media_items, existing_hits = resolve_stroke_media_items(
                base_url, kanji_chars, variant_args, source_dir
            )
        except RuntimeError as exc:
            skipped_variants.append((label, str(exc)))
            continue
        total_existing_media_hits += existing_hits
        media_names = [item["media_name"] for item in media_items]
        stroke_block = build_stroke_block(media_names, variant_args, args.test_nid)
        front_with_stroke = inject_stroke_tag_into_front(original_front, stroke_block)

        variant_fields = dict(base_fields)
        variant_fields["Front"] = front_with_stroke

        variant_tags = list(dict.fromkeys(source_tags + [args.test_tag, f"{args.test_tag}_{label}"]))
        plans.append(
            {
                "label": label,
                "variant_args": variant_args,
                "fields": variant_fields,
                "tags": variant_tags,
                "media_items": media_items,
                "front_preview": preview(front_with_stroke),
            }
        )

    if not plans:
        detail = "; ".join(f"{label}: {reason}" for label, reason in skipped_variants)
        raise RuntimeError(f"No test notes could be planned. {detail}")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}")
    print("Operation: create-stroke-tests")
    print(f"Source note: nid:{args.test_nid}")
    print(f"Source expression: {expr}")
    print(f"Kanji used: {''.join(kanji_chars)}")
    print(f"Deck: {deck_name}")
    print(f"Model: {model_name}")
    print(f"Source dir: {source_dir or '(not set)'}")
    print(f"Auto-download: {args.stroke_auto_download}")
    print(f"Existing media hits: {total_existing_media_hits}")
    print("")
    for plan in plans:
        print(f'Variant: {plan["label"]}')
        names = ", ".join(item["media_name"] for item in plan["media_items"])
        print(f"  Media: {names}")
        print(f'  Front: {plan["front_preview"]}')
    if skipped_variants:
        print("")
        for label, reason in skipped_variants:
            print(f"Skipped variant {label}: {reason}")

    if not args.apply:
        print("")
        print("Dry-run complete. Re-run with --apply to create the selected test notes.")
        return 0

    uploaded_media: set[str] = set()
    downloaded_media: set[str] = set()
    created_notes: list[tuple[str, int]] = []
    failures: list[str] = []
    needs_runtime = any(
        plan["variant_args"].stroke_format == "svg"
        and plan["variant_args"].stroke_svg_embed == "object"
        for plan in plans
    )
    if needs_runtime:
        upload_stroke_runtime_if_needed(
            base_url, args.timeout_seconds, uploaded_media
        )

    for plan in plans:
        variant_args = plan["variant_args"]
        try:
            for item in plan["media_items"]:
                media_name = item["media_name"]
                if not item["needs_upload"] or media_name in uploaded_media:
                    continue

                source_path = item["source_path"]
                download_urls = item["download_urls"]
                if source_path:
                    with open(source_path, "rb") as handle:
                        raw = handle.read()
                elif download_urls:
                    raw, _used_url = download_first_available_url(
                        download_urls, variant_args.stroke_download_timeout
                    )
                    downloaded_media.add(media_name)
                else:
                    raise RuntimeError(
                        f"Upload requested but no source is defined for {media_name}."
                    )

                ankiconnect_invoke(
                    base_url,
                    "storeMediaFile",
                    {"filename": media_name, "data": base64.b64encode(raw).decode("ascii")},
                    variant_args.timeout_seconds,
                )
                uploaded_media.add(media_name)

            note_payload = {
                "note": {
                    "deckName": deck_name,
                    "modelName": model_name,
                    "fields": plan["fields"],
                    "options": {"allowDuplicate": True},
                    "tags": plan["tags"],
                }
            }
            created_nid = ankiconnect_invoke(
                base_url, "addNote", note_payload, variant_args.timeout_seconds
            )
            created_notes.append((plan["label"], created_nid))
        except (RuntimeError, OSError) as exc:
            failures.append(f'{plan["label"]} | {exc}')

    print("")
    print(f"Created notes: {len(created_notes)}/{len(plans)}")
    for label, nid in created_notes:
        print(f"  {label}: nid:{nid}")
    print(f"Uploaded media files: {len(uploaded_media)}")
    print(f"Downloaded from remote: {len(downloaded_media)}")
    print(f"Failures: {len(failures)}")
    for line in failures[:10]:
        print(f"  {line}")
    print("Apply complete.")
    return 0


def main() -> int:
    args = parse_args()
    try:
        if args.operation == "normalize-front":
            return process_notes(args)
        if args.operation == "cleanup-back-kanji":
            return process_cleanup_back_kanji(args)
        if args.operation == "report-duplicates":
            return process_report_duplicates(args)
        if args.operation == "inject-strokes":
            return process_inject_strokes(args)
        if args.operation == "create-stroke-tests":
            return process_create_stroke_tests(args)
        raise RuntimeError(f"Unknown operation: {args.operation}")
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyError as exc:
        print(
            f"Error: expected key missing in Anki data ({exc}). "
            "Check that notes have a Front field.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
