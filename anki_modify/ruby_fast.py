#!/usr/bin/env python3

from __future__ import annotations

import base64
import concurrent.futures
import functools
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from SubtitlePlayer.model.anki_ruby import AddonRubyGenerator

try:
    from fugashi import Tagger
except Exception:
    Tagger = None

ANKI = "http://127.0.0.1:8765"
DECK = "Japanese N3"
DEEPL_URL = "https://api-free.deepl.com/v2/translate"
JISHO_URL = "https://jisho.org/api/v1/search/words"
TATOEBA_URL = "https://tatoeba.org/en/api_v0/search"
DEFAULT_WORKERS = 6

TAG_RE = re.compile(r"<[^>]+>")
RUBY_RE = re.compile(r"\[[^\[\]]*\]")
RUBY_PAIR_RE = re.compile(r"([^\[\]]+)\[([^\[\]]*)\]")
SPLIT_RE = re.compile(r"[;,、/]+")
SPACE_RE = re.compile(r"\s+")
THREAD_LOCAL = threading.local()
RUBY_LOCK = threading.RLock()
GLOBAL_RUBY: AddonRubyGenerator | None = None


def call(action: str, params: dict[str, Any] | None = None) -> Any:
    data = json.dumps({"action": action, "version": 6, "params": params or {}}).encode()
    req = urllib.request.Request(ANKI, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        res = json.loads(response.read())
    if res.get("error"):
        raise RuntimeError(res["error"])
    return res["result"]


def strip_html(text: str) -> str:
    return TAG_RE.sub("", text or "")


def strip_ruby(text: str) -> str:
    return RUBY_RE.sub("", text or "")


def normalize_text(text: str) -> str:
    return SPACE_RE.sub(" ", (text or "").strip())


def normalize_key(text: str) -> str:
    return SPACE_RE.sub("", strip_ruby(strip_html(text or ""))).strip()


def is_kanji(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (
        code == 0x3005
        or 0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2A6DF
        or 0x2A700 <= code <= 0x2B73F
        or 0x2B740 <= code <= 0x2B81F
        or 0x2B820 <= code <= 0x2CEAF
    )


def is_real_kanji(ch: str) -> bool:
    return is_kanji(ch) and ch != "々"


def is_kana(ch: str) -> bool:
    code = ord(ch)
    return 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF or code == 0x30FC


def kata_to_hira(text: str) -> str:
    out = []
    for ch in text or "":
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def split_moras(reading: str) -> list[str]:
    small = set("ゃゅょぁぃぅぇぉゎっー")
    moras: list[str] = []
    for ch in kata_to_hira(reading or ""):
        if ch in small and moras:
            moras[-1] += ch
        else:
            moras.append(ch)
    return moras


def has_real_kanji(text: str) -> bool:
    return any(is_real_kanji(ch) for ch in text or "")


def has_empty_brackets(text: str) -> bool:
    return "[]" in (text or "")


def single_kanji_reading(ch: str, ruby: AddonRubyGenerator | None) -> str:
    if ruby is None or not is_real_kanji(ch):
        return ""
    try:
        with RUBY_LOCK:
            segments = ruby.segments(ch)
    except Exception:
        return ""
    if not segments:
        return ""
    base, reading = segments[0]
    if base == ch and reading:
        return kata_to_hira(normalize_text(reading))
    return ""


def split_by_single_kanji_readings(
    base: str,
    reading: str,
    ruby: AddonRubyGenerator | None,
) -> list[tuple[str, str | None]] | None:
    if ruby is None or not base or not reading:
        return None
    if not all(is_kanji(ch) for ch in base):
        return None
    pieces: list[tuple[str, str | None]] = []
    combined = ""
    i = 0
    while i < len(base):
        ch = base[i]
        if ch == "々":
            pieces.append((ch, None))
            i += 1
            continue
        piece = single_kanji_reading(ch, ruby)
        if not piece:
            return None
        pieces.append((ch, piece))
        combined += piece
        i += 1
    return pieces if combined == kata_to_hira(reading) else None


def split_front_loaded(base: str, reading: str) -> list[tuple[str, str | None]] | None:
    chars = list(base or "")
    real_kanji = [ch for ch in chars if is_real_kanji(ch)]
    moras = split_moras(reading)
    if not chars or len(chars) != len(real_kanji) or len(real_kanji) > len(moras):
        return None
    if len(real_kanji) > 4:
        return None
    first_take = len(moras) - (len(real_kanji) - 1)
    if first_take < 1:
        return None
    out: list[tuple[str, str | None]] = []
    idx = 0
    for pos, ch in enumerate(chars):
        take = first_take if pos == 0 else 1
        piece = "".join(moras[idx:idx + take])
        if not piece:
            return None
        out.append((ch, piece))
        idx += take
    return out


def distribute_moras(base: str, reading: str) -> list[tuple[str, str | None]] | None:
    chars = list(base or "")
    real_kanji = [ch for ch in chars if is_real_kanji(ch)]
    moras = split_moras(reading)
    if not chars or not real_kanji or not moras:
        return None
    if len(real_kanji) > len(moras):
        return None

    if len(chars) == 2 and chars[1] == "々":
        take = max(1, len(moras) // 2)
        return [(chars[0], "".join(moras[:take])), ("々", None)]

    # Only allow distribution if it cleanly matches mora boundaries
    if len(chars) == 2 and len(real_kanji) == 2:
        if len(moras) == 2:
            return [(chars[0], moras[0]), (chars[1], moras[1])]
        # DO NOT split 3+ moras blindly
        return None

    out: list[tuple[str, str | None]] = []
    idx = 0
    remaining_kanji = len(real_kanji)
    for ch in chars:
        if ch == "々" or not is_real_kanji(ch):
            out.append((ch, None))
            continue
        remaining_moras = len(moras) - idx
        if remaining_kanji <= 1:
            take = remaining_moras
        else:
            take = max(1, remaining_moras // remaining_kanji)
        piece = "".join(moras[idx:idx + take])
        if not piece:
            return None
        out.append((ch, piece))
        idx += take
        remaining_kanji -= 1
    return out


def split_mixed_kanji_kana(
    base: str,
    reading: str,
    ruby: AddonRubyGenerator | None,
) -> list[tuple[str, str | None]] | None:
    base = base or ""
    reading = kata_to_hira(reading or "")
    if not base or not reading:
        return None

    out: list[tuple[str, str | None]] = []
    pos = 0
    i = 0
    while i < len(base):
        ch = base[i]
        if is_kana(ch):
            hira = kata_to_hira(ch)
            if reading.startswith(hira, pos):
                pos += len(hira)
            out.append((ch, None))
            i += 1
            continue

        if is_real_kanji(ch):
            j = i
            while j < len(base) and is_real_kanji(base[j]):
                j += 1
            kanji_chunk = base[i:j]

            next_kana = ""
            k = j
            while k < len(base):
                if is_kana(base[k]):
                    next_kana = kata_to_hira(base[k])
                    break
                if is_real_kanji(base[k]):
                    break
                k += 1

            if next_kana:
                # STRICT boundary: match kana exactly at position, not via .find()
                if reading.startswith(next_kana, pos):
                    ruby_chunk = ""
                else:
                    # fallback: consume until we hit the kana properly
                    end = pos
                    while end < len(reading) and not reading.startswith(next_kana, end):
                        end += 1
                    ruby_chunk = reading[pos:end]
                    pos = end
            else:
                ruby_chunk = reading[pos:]
                pos = len(reading)

            if len(kanji_chunk) == 1:
                out.append((kanji_chunk, ruby_chunk or None))
            else:
                split = split_by_single_kanji_readings(kanji_chunk, ruby_chunk, ruby)
                if split is None and len(kanji_chunk) <= 2:
                    split = split_all_kanji_chars(kanji_chunk, ruby_chunk)
                if split is None:
                    split = split_by_single_kanji_readings(kanji_chunk, ruby_chunk, ruby)
                if split is None:
                    split = distribute_moras(kanji_chunk, ruby_chunk)
                out.extend(split if split else [(kanji_chunk, ruby_chunk or None)])
            i = j
            continue

        out.append((ch, None))
        i += 1

    return out or None


def split_segment(
    base: str,
    reading: str | None,
    ruby: AddonRubyGenerator | None,
) -> list[tuple[str, str | None]]:
    base = base or ""
    reading = kata_to_hira(normalize_text(reading or ""))
    if not base:
        return []
    if not reading or not has_real_kanji(base):
        return [(base, None)]
    if len(base) == 1 and is_real_kanji(base):
        return [(base, reading)]

    if all(is_kanji(ch) for ch in base):
        # 1. Try dictionary-based splitting (most reliable)
        split = split_by_single_kanji_readings(base, reading, ruby)
        if split is not None:
            return split

        # 2. Only allow heuristic splitting for VERY safe cases
        if len(base) <= 2:
            split = split_all_kanji_chars(base, reading)
            if split is not None:
                return split

        # 3. Otherwise keep as compound
        return [(base, reading)]

    return split_mixed_kanji_kana(base, reading, ruby) or [(base, reading)]


def render_ruby_parts(parts: list[tuple[str, str | None]], failed: set[str]) -> str:
    out: list[str] = []
    previous_was_ruby = False

    for base, reading in parts:
        base = base or ""
        reading = kata_to_hira(normalize_text(reading or ""))
        if not base:
            continue

        if base == "々" and not reading:
            out.append(base)
            previous_was_ruby = False
            continue

        if reading and has_real_kanji(base):
            if previous_was_ruby:
                out.append(" ")
            out.append(f"{base}[{reading}]")
            previous_was_ruby = True
            continue

        out.append(base)
        previous_was_ruby = False
        failed.update(ch for ch in base if is_real_kanji(ch))

    return "".join(out)

def split_all_kanji_chars(
    surface: str,
    reading: str,
) -> list[tuple[str, str | None]] | None:
    if not surface or not reading:
        return None
    if not all(is_kanji(ch) for ch in surface):
        return None

    reading = kata_to_hira(reading)
    moras = split_moras(reading)
    n_chars = len(surface)
    n_moras = len(moras)

    # reject if reading is too uneven
    if len(moras) >= 3 and len(surface) == 2:
        return None
    
    if n_moras < n_chars:
        return None

    if n_chars == 2 and surface[1] == "々":
        take = max(1, n_moras // 2)
        first = "".join(moras[:take])
        second = "".join(moras[take:]) or first
        if not first:
            return None
        return [(surface[0], first), ("々", first)]

    def chunk_score(length: int, is_last: bool) -> float:
        if length == 1:
            score = 2.0
        elif length == 2:
            score = 5.0
        elif length == 3:
            score = 4.0
        elif length == 4:
            score = 2.0
        else:
            score = 0.5 - 0.5 * (length - 4)

        if is_last and length == 1:
            score -= 3.0
        return score

    @functools.lru_cache(maxsize=None)
    def best(start: int, parts_left: int) -> tuple[float, tuple[int, ...]] | None:
        if parts_left == 1:
            take = n_moras - start
            if take < 1:
                return None
            return chunk_score(take, True), (take,)

        best_score: float | None = None
        best_lengths: tuple[int, ...] | None = None

        max_take = n_moras - start - (parts_left - 1)
        if max_take < 1:
            return None

        for take in range(1, max_take + 1):
            rest = best(start + take, parts_left - 1)
            if rest is None:
                continue

            rest_score, rest_lengths = rest
            score = chunk_score(take, False) + rest_score

            if rest_lengths:
                if take <= rest_lengths[0]:
                    score += 0.6
                else:
                    score -= 0.4

            if best_score is None or score > best_score:
                best_score = score
                best_lengths = (take,) + rest_lengths

        if best_score is None or best_lengths is None:
            return None
        return best_score, best_lengths

    result = best(0, n_chars)
    if result is None:
        return None

    _, lengths = result
    if len(lengths) != n_chars:
        return None

    out: list[tuple[str, str | None]] = []
    idx = 0
    for ch, take in zip(surface, lengths):
        chunk = "".join(moras[idx:idx + take])
        if not chunk:
            return None
        out.append((ch, chunk))
        idx += take

    return out

def build_ruby_from_segments(text: str, ruby: AddonRubyGenerator, failed: set[str]) -> str:
    plain = strip_html(strip_ruby(text or ""))
    if not plain:
        return ""

    parts = re.split(r"(\s+)", plain)
    out: list[str] = []

    with RUBY_LOCK:
        for part in parts:
            if not part:
                continue
            if part.isspace():
                out.append(part)
                continue

            segments = ruby.segments(part)
            if not segments:
                failed.update(ch for ch in part if is_real_kanji(ch))
                out.append(part)
                continue

            ruby_parts: list[tuple[str, str | None]] = []
            for base, reading in segments:
                ruby_parts.extend(split_segment(base, reading, ruby))
            rendered = render_ruby_parts(ruby_parts, failed)
            out.append(rendered.replace("[]", "").strip())

    return "".join(out).strip()


def keep_existing_front_if_correct(front_old: str, base_word: str) -> str | None:
    if has_empty_brackets(front_old):
        return None
    if normalize_key(front_old) != normalize_key(base_word):
        return None
    pairs = RUBY_PAIR_RE.findall(front_old or "")
    if not pairs:
        return None
    for base, reading in pairs:
        if not reading:
            return None
        if sum(1 for ch in base if is_real_kanji(ch)) > 1:
            return None
    return front_old

def extract_tatoeba_translation(entry: dict[str, Any], lang_code: str) -> str:
    for tr_group in entry.get("translations", []):
        for item in tr_group:
            if item.get("lang") == lang_code and item.get("text"):
                return normalize_text(str(item["text"]))
    return ""


def audio_url_from_entry(entry: dict[str, Any]) -> str:
    for audio in entry.get("audios", []) or []:
        url = audio.get("url") or audio.get("download_url") or ""
        if url:
            return str(url)
    return ""


def has_kanji_boundary_match(sentence: str, base_word: str) -> bool:
    exact = normalize_key(base_word)
    haystack = normalize_key(sentence)
    if not exact:
        return False
    start = 0
    while True:
        pos = haystack.find(exact, start)
        if pos < 0:
            return False
        before = haystack[pos - 1] if pos > 0 else ""
        after_pos = pos + len(exact)
        after = haystack[after_pos] if after_pos < len(haystack) else ""
        if not is_real_kanji(before) and not is_real_kanji(after):
            return True
        start = pos + 1


def sentence_has_exact_word(sentence: str, base_word: str) -> bool:
    exact = normalize_key(base_word)
    if not exact:
        return False
    if exact == normalize_key(sentence):
        return True
    if not has_kanji_boundary_match(sentence, exact):
        return False

    tagger = get_tagger()
    if tagger is None:
        return exact in normalize_key(sentence)

    try:
        tokens = list(tagger(sentence))
    except Exception:
        return exact in normalize_key(sentence)

    for token in tokens:
        surface = normalize_key(getattr(token, "surface", "") or str(token))
        if surface == exact:
            return True
        feature = getattr(token, "feature", None)
        for attr in ("lemma", "orthBase", "formBase"):
            value = getattr(feature, attr, "") if feature is not None else ""
            if value and normalize_key(str(value)) == exact:
                return True
    return False


@functools.lru_cache(maxsize=8192)
def tatoeba_search(base_word: str, to_lang: str, require_audio: bool) -> tuple[str, str, str]:
    exact = normalize_key(base_word)
    if not exact:
        return "", "", ""
    params = {
        "from": "jpn",
        "to": to_lang,
        "query": exact,
        "sort": "relevance",
        "trans_filter": "limit",
        "trans_link": "direct",
        "trans_to": to_lang,
        "orphans": "no",
        "unapproved": "no",
    }
    url = TATOEBA_URL + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read())
    except Exception:
        return "", "", ""

    for entry in data.get("results", []) or []:
        ja = normalize_text(str(entry.get("text") or ""))
        if not sentence_has_exact_word(ja, exact):
            continue

        if normalize_key(ja) == exact:
            continue
        translation = extract_tatoeba_translation(entry, to_lang)
        if not translation:
            continue
        audio = audio_url_from_entry(entry)
        if require_audio and not audio:
            continue
        return ja, translation, audio
    return "", "", ""


@functools.lru_cache(maxsize=8192)
def deepl(text: str, src: str, tgt: str) -> str:
    key = os.environ.get("DEEPL_TOKEN") or os.environ.get("DEEPL_AUTH_KEY") or os.environ.get("DEEPL_API_KEY")
    if not key or not text:
        return ""
    data = urllib.parse.urlencode({"text": text, "source_lang": src, "target_lang": tgt}).encode()
    req = urllib.request.Request(DEEPL_URL, data=data, headers={"Authorization": f"DeepL-Auth-Key {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            res = json.loads(response.read())
    except Exception:
        return ""
    translations = res.get("translations", [])
    if not translations:
        return ""
    return normalize_text(str(translations[0].get("text") or ""))


def get_sentence(base_word: str) -> tuple[str, str, str]:
    ja, de, audio = tatoeba_search(base_word, "deu", require_audio=True)
    if ja and de and audio:
        return ja, de, audio

    ja, de, audio = tatoeba_search(base_word, "deu", require_audio=False)
    if ja and de:
        return ja, de, audio

    ja, en, audio = tatoeba_search(base_word, "eng", require_audio=False)
    if ja and en:
        return ja, deepl(en, "EN", "DE") or en, audio

    return "", "", ""


@functools.lru_cache(maxsize=8192)
def jisho_meanings(base_word: str) -> tuple[str, ...]:
    word = normalize_key(base_word)
    if not word:
        return ()
    url = JISHO_URL + "?" + urllib.parse.urlencode({"keyword": word})
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read())
    except Exception:
        return ()

    entries = data.get("data", [])
    if not entries:
        return ()

    best = entries[0]
    best_score = -1
    for entry in entries:
        score = 0
        for jp in entry.get("japanese", []):
            if jp.get("word") == word:
                score += 100
            if jp.get("reading") == word:
                score += 80
        if score > best_score:
            best = entry
            best_score = score

    meanings: list[str] = []
    for sense in best.get("senses", []) or []:
        for item in sense.get("english_definitions", []) or []:
            value = normalize_text(str(item or ""))
            if value and value not in meanings:
                meanings.append(value)
    return tuple(meanings)


def first_three_phrases(text: str) -> str:
    parts = [p.strip() for p in SPLIT_RE.split(text or "") if p.strip()]
    return "; ".join(parts[:3])


def build_definition_en(meanings: tuple[str, ...]) -> str:
    parts: list[str] = []
    for meaning in meanings:
        meaning = normalize_text(meaning)
        if meaning and meaning not in parts:
            parts.append(meaning)
    return "; ".join(parts)

def google_translate(text: str, source_lang: str, target_lang: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    try:
        url = "https://translate.googleapis.com/translate_a/single"
        params = {
            "client": "gtx",
            "sl": source_lang,
            "tl": target_lang,
            "dt": "t",
            "q": text,
        }
        full_url = url + "?" + urllib.parse.urlencode(params)

        with urllib.request.urlopen(full_url, timeout=30) as response:
            payload = json.loads(response.read())

        translated = ""
        for chunk in payload[0]:
            if isinstance(chunk, list) and chunk:
                translated += str(chunk[0] or "")

        return normalize_text(translated)
    except Exception:
        return ""
    
def valid_value(value: str | None) -> bool:
    return value is not None and value != ""


def set_field(fields: dict[str, str], name: str, value: str | None) -> None:
    if valid_value(value):
        fields[name] = str(value)


def set_field_if_changed(
    update_fields: dict[str, str],
    current_fields: dict[str, Any],
    name: str,
    value: str | None,
) -> None:
    if not valid_value(value):
        return
    current = field_text(current_fields, name)
    if str(value) != current:
        update_fields[name] = str(value)


def safe_out(text: Any) -> str:
    value = str(text if text is not None else "")
    enc = sys.stdout.encoding or "utf-8"
    return value.encode(enc, errors="backslashreplace").decode(enc, errors="ignore")


def print_safe(*parts: Any) -> None:
    print(*(safe_out(part) for part in parts))


def show_progress(label: str, current: int, total: int) -> None:
    if total <= 0:
        return
    width = 32
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    percent = int(100 * current / total)
    line = f"\r{label}: [{bar}] {current}/{total} ({percent:3d}%)"
    sys.stdout.write(safe_out(line))
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def download_audio_to_anki(audio_url: str) -> str:
    if not audio_url:
        return ""
    try:
        if audio_url.startswith("//"):
            audio_url = "https:" + audio_url
        elif audio_url.startswith("/"):
            audio_url = "https://tatoeba.org" + audio_url
        filename = audio_url.split("/")[-1].split("?", 1)[0] or "tatoeba_audio.mp3"
        with urllib.request.urlopen(audio_url, timeout=30) as response:
            media_data = response.read()
        call("storeMediaFile", {"filename": filename, "data": base64.b64encode(media_data).decode("ascii")})
        return f"[sound:{filename}]"
    except Exception:
        return ""


def get_ruby() -> AddonRubyGenerator:
    global GLOBAL_RUBY
    with RUBY_LOCK:
        if GLOBAL_RUBY is None:
            GLOBAL_RUBY = AddonRubyGenerator()
        return GLOBAL_RUBY


def close_ruby_generator(ruby: AddonRubyGenerator | None) -> None:
    if ruby is None:
        return
    for controller_name in ("mecab", "kakasi"):
        controller = getattr(ruby, controller_name, None)
        proc = getattr(controller, controller_name, None) if controller is not None else None
        if proc is None:
            continue
        for stream_name in ("stdin", "stdout", "stderr"):
            stream = getattr(proc, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def close_global_ruby() -> None:
    global GLOBAL_RUBY
    with RUBY_LOCK:
        ruby = GLOBAL_RUBY
        GLOBAL_RUBY = None
    close_ruby_generator(ruby)


def get_tagger():
    tagger = getattr(THREAD_LOCAL, "tagger", None)
    if tagger is None and Tagger is not None:
        try:
            tagger = Tagger()
        except Exception:
            tagger = False
        THREAD_LOCAL.tagger = tagger
    return tagger if tagger is not False else None


def field_text(fields: dict[str, Any], name: str) -> str:
    return str(fields.get(name, {}).get("value", "") or "")


def has_field_value(fields: dict[str, Any], name: str) -> bool:
    return bool(normalize_text(strip_html(field_text(fields, name))))


def front_needs_update(front_old: str, base_word: str) -> bool:
    if keep_existing_front_if_correct(front_old, base_word) is not None:
        return False
    return True


def is_new_card(fields: dict[str, Any]) -> bool:
    return not has_field_value(fields, "Definition") or not has_field_value(fields, "SentenceJA")

def is_bad_sentence(sentence: str, base_word: str) -> bool:
    s = normalize_key(sentence)
    w = normalize_key(base_word)

    if not s:
        return True

    if s == w:
        return True

    # stronger length rule
    if len(s) < len(w) + 3:
        return True

    # must contain at least one kana (real sentence indicator)
    if not any(is_kana(ch) for ch in sentence):
        return True

    return False

def plan_note(note: dict[str, Any], mode: str, force: bool, only_new: bool, reset_def: bool, fix_sep, fix_ruby) -> tuple[dict[str, Any] | None, int | None]:
    ruby = get_ruby()
    nid = int(note["noteId"])
    fields = note["fields"]

    if fix_sep:
        update_fields = {}

        definition_old = field_text(fields, "Definition")
        back_old = field_text(fields, "Back")

        definition_new = replace_semicolons(definition_old)
        back_new = replace_semicolons(back_old)

        set_field_if_changed(update_fields, fields, "Definition", definition_new)
        set_field_if_changed(update_fields, fields, "Back", back_new)

        if not update_fields:
            return None, None

        return {
            "id": int(note["noteId"]),
            "fields": update_fields,
            "old_front": field_text(fields, "Front"),
            "preview_fields": {
                "Definition": definition_new,
                "Back": back_new,
            },
            "new_front": field_text(fields, "Front"),
            "add_rubies_to_front": field_text(fields, "AddRubiesToFront"),
            "sound_added": False,
            "audio_url": "",
        }, None

    definition_old = field_text(fields, "Definition")

    needs_def_reset = False
    if reset_def:
        if is_single_word_definition(definition_old):
            needs_def_reset = True
    if only_new and not is_new_card(fields) and not (reset_def and needs_def_reset):
        return None, None
    front_old = field_text(fields, "Front")
    add_rubies_to_front = normalize_text(strip_ruby(strip_html(field_text(fields, "AddRubiesToFront"))))
    if not add_rubies_to_front:
        return None, None
    base_word = add_rubies_to_front
    if fix_ruby:
        failed: set[str] = set()
        new_front = build_ruby_from_segments(base_word, ruby, failed)

        # normalize both for safe comparison
        old_clean = normalize_text(front_old)
        new_clean = normalize_text(new_front)

        if new_front and old_clean != new_clean:
            update_fields = {}
            set_field(update_fields, "Front", new_front)

            return {
                "id": nid,
                "fields": update_fields,
                "old_front": front_old,
                "preview_fields": {
                    "Front": new_front,
                    "SentenceJA": field_text(fields, "SentenceJA"),
                    "SentenceDE": field_text(fields, "SentenceDE"),
                    "Definition": field_text(fields, "Definition"),
                    "Back": field_text(fields, "Back"),
                    "Sound": field_text(fields, "Sound"),
                },
                "new_front": new_front,
                "add_rubies_to_front": add_rubies_to_front,
                "sound_added": False,
                "audio_url": "",
            }, None

        return None, None
    failed: set[str] = set()
    update_fields: dict[str, str] = {}

    if mode == "all" and (force or front_needs_update(front_old, base_word)):
        keep = keep_existing_front_if_correct(front_old, base_word)
        new_front = keep if keep is not None else build_ruby_from_segments(base_word, ruby, failed)
        if new_front and new_front != front_old:
            set_field(update_fields, "Front", new_front)

    audio = ""
    ja_raw, sentence_de, audio = get_sentence(base_word)

    current_sentence = field_text(fields, "SentenceJA")

    # detect bad existing sentence
    current_bad = is_bad_sentence(current_sentence, base_word)

    # detect bad new sentence
    new_bad = is_bad_sentence(ja_raw, base_word)

    if ja_raw:
        clean_new = normalize_key(ja_raw)
        clean_word = normalize_key(base_word)

        # extra strict filtering
        if (
            not new_bad
            and clean_new != clean_word
            and len(clean_new) > len(clean_word) + 2
        ):
            sentence_ja = build_ruby_from_segments(ja_raw, ruby, failed)

            set_field_if_changed(update_fields, fields, "AddRubiesToSentenceJA", ja_raw)
            set_field_if_changed(update_fields, fields, "SentenceJA", sentence_ja)
            set_field_if_changed(update_fields, fields, "SentenceDE", sentence_de)

        elif current_bad:
            # remove garbage if no valid replacement
            set_field(update_fields, "SentenceJA", "")
            set_field(update_fields, fields, "SentenceDE", "")

    if mode == "all" and (force or needs_def_reset):
        meanings = jisho_meanings(base_word)

        # FULL English definition (multiple meanings)
        definition_en = build_definition_en(meanings)

        # First 3 meanings → German
        back_source = "; ".join(meanings[:3])
        back_de = ""

        if back_source:
            back_de = google_translate(back_source, "en", "de")

        # fallback if Google fails
        if not back_de:
            back_de = deepl(base_word, "JA", "DE")

        set_field_if_changed(update_fields, fields, "Definition", definition_en)
        set_field_if_changed(update_fields, fields, "Back", back_de)

    tag_nid = nid if failed else None
    if not update_fields and not audio:
        return None, tag_nid

    preview_fields = {
        "Front": update_fields.get("Front", front_old),
        "SentenceJA": update_fields.get("SentenceJA", field_text(fields, "SentenceJA")),
        "SentenceDE": update_fields.get("SentenceDE", field_text(fields, "SentenceDE")),
        "Definition": update_fields.get("Definition", field_text(fields, "Definition")),
        "Back": update_fields.get("Back", field_text(fields, "Back")),
        "Sound": update_fields.get("Sound", field_text(fields, "Sound")),
    }

    return {
        "id": nid,
        "fields": update_fields,
        "old_front": front_old,
        "preview_fields": preview_fields,
        "new_front": preview_fields["Front"],
        "add_rubies_to_front": add_rubies_to_front,
        "sound_added": bool(audio),
        "audio_url": audio,
    }, tag_nid

def replace_semicolons(text: str) -> str:
    text = text or ""
    # replace ";" with ", " and normalize spacing
    text = text.replace(";", ",")
    text = re.sub(r"\s*,\s*", ", ", text)
    return text.strip()

def parse_args(argv: list[str]) -> tuple[str, int | None, int, bool, bool, bool, bool, bool]:
    mode = "all"
    limit = None
    workers = DEFAULT_WORKERS
    force = False
    apply = False
    only_new = False
    reset_def = False
    fix_sep = False
    fix_ruby = False

    for arg in argv:
        if arg == "sentences":
            mode = "sentences"
        elif arg == "all":
            mode = "all"
        elif arg == "force":
            force = True
        elif arg in {"apply", "--apply"}:
            apply = True
        elif arg in {"new", "--new"}:
            only_new = True
        elif arg in {"--resetDEF", "resetDEF"}:
            reset_def = True
        elif arg in {"--fixSEP", "fixSEP"}:
            fix_sep = True
        elif arg in {"--fixRUBY", "fixRUBY"}:
            fix_ruby = True
        elif arg.startswith("limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg.startswith("workers="):
            workers = max(1, int(arg.split("=", 1)[1]))
        elif arg.startswith("--workers="):
            workers = max(1, int(arg.split("=", 1)[1]))

    return mode, limit, workers, force, apply, only_new, reset_def, fix_sep, fix_ruby

def is_single_word_definition(text: str) -> bool:
    text = normalize_text(text)
    if not text:
        return True

    # split on common separators
    parts = re.split(r"[;,/]", text)
    parts = [p.strip() for p in parts if p.strip()]

    # only one entry → considered bad
    if len(parts) <= 1:
        return True

    return False

BRACKET_RUBY_RE = re.compile(r"\[([^\[\]]+)\]")

def _strip_existing_ruby(text: str) -> str:
    if not text:
        return ""
    # remove [reading]
    text = re.sub(r"\[[^\[\]]+\]", "", text)
    return text

def main() -> int:
    mode, limit, workers, force, apply, only_new, reset_def, fix_sep, fix_ruby = parse_args(sys.argv[1:])
    note_ids = call("findNotes", {"query": f'deck:"{DECK}"'})
    if limit:
        note_ids = note_ids[:limit]

    notes = call("notesInfo", {"notes": note_ids}) if note_ids else []

    updates: list[dict[str, Any]] = []
    tag_updates: list[int] = []
    interrupted = False
    processed = 0

    show_progress("Building updates", 0, len(notes))
    executor: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
        futures = [
            executor.submit(plan_note, note, mode, force, only_new, reset_def, fix_sep, fix_ruby)
            for note in notes
        ]
        for future in concurrent.futures.as_completed(futures):
            processed += 1
            update, tag_nid = future.result()
            if update:
                updates.append(update)
            if tag_nid:
                tag_updates.append(tag_nid)
            show_progress("Building updates", processed, len(notes))
    except KeyboardInterrupt:
        interrupted = True
        if executor:
            executor.shutdown(wait=True, cancel_futures=True)
        sys.stdout.write("\n")
        sys.stdout.flush()
        print_safe(f"Interrupted at {processed}/{len(notes)} notes. Showing collected updates so far.")
    finally:
        if executor and not interrupted:
            executor.shutdown(wait=True)

    print_safe(f"\nMode: {mode}")
    print_safe(f"Workers: {workers}")
    print_safe(f"Force regenerate filled fields: {'YES' if force else 'NO'}")
    print_safe(f"Auto apply: {'YES' if apply else 'NO'}")
    print_safe(f"Only new cards: {'YES' if only_new else 'NO'}")
    print_safe(f"Scanned notes: {processed if interrupted else len(notes)} of {len(notes)}")
    print_safe(f"Prepared updates: {len(updates)}")
    print_safe(f"missingRuby tags to add: {len(set(tag_updates))}")

    for update in updates[:20]:
        update_fields = update["fields"]
        print_safe("\n----------------------")
        print_safe("NOTE ID:", update["id"])
        print_safe("AddRubiesToFront:", update["add_rubies_to_front"])
        print_safe("OLD Front:", update["old_front"])
        print_safe("NEW Front:", update["new_front"])
        preview_fields = update["preview_fields"]
        print_safe("SentenceJA:", preview_fields.get("SentenceJA", ""))
        print_safe("SentenceDE:", preview_fields.get("SentenceDE", ""))
        print_safe("Definition:", preview_fields.get("Definition", ""))
        print_safe("Back:", preview_fields.get("Back", ""))
        print_safe("Sound added:", "YES" if update["sound_added"] else "NO")

    if not apply and input("\nApply? (y/n): ").strip().lower() != "y":
        close_global_ruby()
        return 0

    show_progress("Applying updates", 0, len(updates))
    for index, update in enumerate(updates, start=1):
        fields_to_write = dict(update["fields"])
        if update.get("audio_url"):
            set_field(fields_to_write, "Sound", download_audio_to_anki(update["audio_url"]))
        if fields_to_write:
            call("updateNoteFields", {"note": {"id": update["id"], "fields": fields_to_write}})
        show_progress("Applying updates", index, len(updates))

    if tag_updates:
        print_safe("Adding missingRuby tag...")
        call("addTags", {"notes": sorted(set(tag_updates)), "tags": "missingRuby"})

    close_global_ruby()
    print_safe("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
