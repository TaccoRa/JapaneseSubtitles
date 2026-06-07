from __future__ import annotations

from functools import lru_cache
from typing import Callable, Optional

Segment = tuple[str, Optional[str]]
SingleKanjiReader = Callable[[str], str]


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
    if not ch:
        return False
    code = ord(ch)
    return 0x3040 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF or code == 0x30FC


def kata_to_hira(text: str) -> str:
    out: list[str] = []
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
    for ch in kata_to_hira(reading):
        if ch in small and moras:
            moras[-1] += ch
        else:
            moras.append(ch)
    return moras


VOICED = str.maketrans(
    {
        "か": "が",
        "き": "ぎ",
        "く": "ぐ",
        "け": "げ",
        "こ": "ご",
        "さ": "ざ",
        "し": "じ",
        "す": "ず",
        "せ": "ぜ",
        "そ": "ぞ",
        "た": "だ",
        "ち": "ぢ",
        "つ": "づ",
        "て": "で",
        "と": "ど",
        "は": "ば",
        "ひ": "び",
        "ふ": "ぶ",
        "へ": "べ",
        "ほ": "ぼ",
    }
)

COMMON_KANJI_READINGS = {
    "仮": ("か",),
    "名": ("な",),
    "図": ("ず", "と"),
    "字": ("じ",),
    "語": ("ご",),
    "本": ("ほん",),
    "口": ("くち",),
    "出": ("で",),
}


def voiced_variants(reading: str) -> list[str]:
    reading = kata_to_hira(reading)
    variants = [reading]
    if reading:
        voiced = reading[0].translate(VOICED) + reading[1:]
        if voiced != reading:
            variants.append(voiced)
    return variants


def _reader_values(reader: SingleKanjiReader | None, ch: str) -> list[str]:
    if not is_real_kanji(ch):
        return []
    values: list[str] = []
    for value in COMMON_KANJI_READINGS.get(ch, ()):
        hira = kata_to_hira(value.strip())
        if hira and hira not in values:
            values.append(hira)
    if reader is not None:
        try:
            hira = kata_to_hira((reader(ch) or "").strip())
        except Exception:
            hira = ""
        if hira and hira not in values:
            values.append(hira)
    return values


def split_kanji_run(
    base: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None = None,
) -> list[Segment] | None:
    base = base or ""
    reading = kata_to_hira(reading or "")
    if not base or not reading or not all(is_kanji(ch) for ch in base):
        return None

    real_count = sum(1 for ch in base if is_real_kanji(ch))
    if real_count == 0:
        return [(base, None)]

    # Repetition marks normally share the previous kanji, but bracket ruby above
    # the mark itself is visually noisy in Anki. Keep the mark plain.
    if len(base) == 2 and base[1] == "々":
        moras = split_moras(reading)
        take = max(1, len(moras) // 2)
        return [(base[0], "".join(moras[:take])), ("々", None)]

    if real_count == 1:
        return [(base, reading)] if base != "々" else [(base, None)]

    parts: list[Segment] = [(ch, None) for ch in base]
    remaining = reading

    # Anchor known single-kanji readings from the right. This catches common
    # compounds where the final kanji keeps a dictionary-like reading, including
    # rendaku: 漢字, 合図, 日本語, 平仮名, 出口.
    for idx in range(len(base) - 1, 0, -1):
        ch = base[idx]
        if not is_real_kanji(ch):
            continue
        singles = _reader_values(single_kanji_reader, ch)
        if not singles:
            return None

        matched = ""
        candidates: list[str] = []
        for single in singles:
            candidates.extend(voiced_variants(single))
        for candidate in sorted(set(candidates), key=len, reverse=True):
            if remaining.endswith(candidate):
                matched = remaining[-len(candidate) :]
                break
        if not matched:
            return None
        parts[idx] = (ch, matched)
        remaining = remaining[: -len(matched)]
        if not remaining:
            return None

    first = base[0]
    if not is_real_kanji(first):
        return None
    parts[0] = (first, remaining)
    return parts


def split_furigana(
    base: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None = None,
) -> list[Segment]:
    base = base or ""
    reading = kata_to_hira(reading or "")
    if not base:
        return []
    if not reading:
        return [(base, None)]
    if not any(is_real_kanji(ch) for ch in base):
        return [(base, None)]

    first = next((i for i, ch in enumerate(base) if is_kanji(ch)), None)
    last = next((i for i in range(len(base) - 1, -1, -1) if is_kanji(base[i])), None)
    if first is None or last is None:
        return [(base, None)]

    prefix = base[:first]
    core = base[first : last + 1]
    suffix = base[last + 1 :]
    core_reading = reading
    prefix_hira = kata_to_hira(prefix)
    suffix_hira = kata_to_hira(suffix)
    if prefix_hira and core_reading.startswith(prefix_hira):
        core_reading = core_reading[len(prefix_hira) :]
    if suffix_hira and core_reading.endswith(suffix_hira):
        core_reading = core_reading[: -len(suffix_hira)]

    out: list[Segment] = []
    if prefix:
        out.append((prefix, None))

    if all(is_kanji(ch) for ch in core):
        split = split_kanji_run(core, core_reading, single_kanji_reader)
        out.extend(split if split else [(core, core_reading)])
    else:
        out.extend(_split_mixed_core(core, core_reading, single_kanji_reader))

    if suffix:
        out.append((suffix, None))
    return out


def _split_mixed_core(
    core: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None,
) -> list[Segment]:
    out: list[Segment] = []
    pos = 0
    i = 0
    reading = kata_to_hira(reading)

    while i < len(core):
        ch = core[i]
        if is_kana(ch):
            hira = kata_to_hira(ch)
            if reading.startswith(hira, pos):
                pos += len(hira)
            out.append((ch, None))
            i += 1
            continue

        if is_real_kanji(ch):
            j = i
            while j < len(core) and is_real_kanji(core[j]):
                j += 1
            run = core[i:j]

            next_kana = ""
            k = j
            while k < len(core):
                if is_kana(core[k]):
                    next_kana = kata_to_hira(core[k])
                    break
                if is_real_kanji(core[k]):
                    break
                k += 1

            if next_kana:
                end = reading.find(next_kana, pos)
                if end < pos:
                    chunk = reading[pos:]
                    pos = len(reading)
                else:
                    chunk = reading[pos:end]
                    pos = end
            else:
                chunk = reading[pos:]
                pos = len(reading)

            split = split_kanji_run(run, chunk, single_kanji_reader)
            out.extend(split if split else [(run, chunk or None)])
            i = j
            continue

        out.append((ch, None))
        i += 1

    return out or [(core, reading)]


def bracket_text(segments: list[Segment], space_between_ruby: bool = True) -> str:
    out: list[str] = []
    prev_ruby = False
    for base, ruby in segments:
        if not base:
            continue
        if ruby and any(is_real_kanji(ch) for ch in base):
            if space_between_ruby and prev_ruby:
                out.append(" ")
            out.append(f"{base}[{ruby}]")
            prev_ruby = True
        else:
            out.append(base)
            prev_ruby = False
    return "".join(out)


@lru_cache(maxsize=1)
def splitter_self_test_cases() -> tuple[tuple[str, str, str], ...]:
    return (
        ("愛", "あい", "愛[あい]"),
        ("合図", "あいず", "合[あい] 図[ず]"),
        ("漢字", "かんじ", "漢[かん] 字[じ]"),
        ("平仮名", "ひらがな", "平[ひら] 仮[が] 名[な]"),
        ("日本語", "にほんご", "日[に] 本[ほん] 語[ご]"),
        ("出口", "でぐち", "出[で] 口[ぐち]"),
        ("食べる", "たべる", "食[た]べる"),
        ("取り戻す", "とりもどす", "取[と]り 戻[もど]す"),
        ("時々", "ときどき", "時[とき]々"),
        ("今日", "きょう", "今日[きょう]"),
    )
