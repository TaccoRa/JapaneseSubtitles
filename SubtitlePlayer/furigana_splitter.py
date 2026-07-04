from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Callable, Optional

Segment = tuple[str, Optional[str]]
SingleKanjiReading = str | Iterable[str]
SingleKanjiReader = Callable[[str], SingleKanjiReading]

SMALL_KANA_JOINERS = set("ゃゅょぁぃぅぇぉゎっー")
INVALID_CHUNK_START = set("ゃゅょぁぃぅぇぉゎっーん")
SINGLE_VOWEL_CHUNKS = set("あいうえお")

IRREGULAR_WHOLE_WORDS = frozenset(
    {
        "今日",
        "明日",
        "昨日",
        "一昨日",
        "一昨昨日",
        "大人",
        "二十歳",
        "二十才",
        "下手",
        "上手",
        "土産",
        "田舎",
        "素人",
        "玄人",
        "紅葉",
        "台詞",
        "風邪",
        "海苔",
        "山車",
        "浴衣",
        "眼鏡",
        "煙草",
        "玩具",
        "果物",
        "七夕",
        "五月雨",
        "時雨",
        "従兄弟",
        "従姉妹",
        "従兄",
        "従弟",
        "従姉",
        "従妹",
        "叔父",
        "叔母",
        "伯父",
        "伯母",
    }
)


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


def is_katakana_ruby_base(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    has_katakana = False
    for ch in value:
        code = ord(ch)
        if 0x30A0 <= code <= 0x30FF or ch == "ー":
            has_katakana = True
            continue
        if ch in {"・", "･"}:
            continue
        return False
    return has_katakana


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
    moras: list[str] = []
    for ch in kata_to_hira(reading):
        if ch in SMALL_KANA_JOINERS and moras:
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

SEMI_VOICED = str.maketrans(
    {
        "は": "ぱ",
        "ひ": "ぴ",
        "ふ": "ぷ",
        "へ": "ぺ",
        "ほ": "ぽ",
    }
)

UNVOICED = str.maketrans(
    {
        "が": "か",
        "ぎ": "き",
        "ぐ": "く",
        "げ": "け",
        "ご": "こ",
        "ざ": "さ",
        "じ": "し",
        "ず": "す",
        "ぜ": "せ",
        "ぞ": "そ",
        "だ": "た",
        "ぢ": "ち",
        "づ": "つ",
        "で": "て",
        "ど": "と",
        "ば": "は",
        "び": "ひ",
        "ぶ": "ふ",
        "べ": "へ",
        "ぼ": "ほ",
        "ぱ": "は",
        "ぴ": "ひ",
        "ぷ": "ふ",
        "ぺ": "へ",
        "ぽ": "ほ",
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
    "一": ("いち", "いつ", "ひと"),
    "日": ("にち", "じつ", "ひ", "か", "び"),
    "世": ("せ", "せい", "よ"),
    "界": ("かい",),
    "科": ("か",),
    "学": ("がく",),
    "人": ("にん", "じん", "ひと", "と"),
    "間": ("かん", "けん", "ま", "あいだ"),
    "神": ("かみ", "しん", "じん"),
    "様": ("さま", "よう"),
    "戦": ("せん",),
    "争": ("そう",),
    "勝": ("しょう", "か", "かち"),
    "利": ("り",),
    "魔": ("ま",),
    "法": ("ほう",),
    "能": ("のう",),
    "力": ("りょく", "りき", "ちから"),
    "目": ("もく", "め"),
    "的": ("てき",),
    "太": ("たい", "た"),
    "陽": ("よう",),
    "反": ("はん",),
    "骨": ("こつ", "ほね"),
    "英": ("えい",),
    "雄": ("ゆう",),
    "前": ("ぜん", "まえ"),
    "進": ("しん", "すす"),
    "結": ("けつ",),
    "晶": ("しょう",),
    "石": ("せき", "いし"),
    "器": ("き",),
    "校": ("こう",),
    "発": ("はつ", "ほつ"),
    "表": ("ひょう",),
    "杯": ("はい",),
}

FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
KANJI_DIGIT_VALUES = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
NUMBER_READINGS = {
    0: "れい",
    1: "いち",
    2: "に",
    3: "さん",
    4: "よん",
    5: "ご",
    6: "ろく",
    7: "なな",
    8: "はち",
    9: "きゅう",
    10: "じゅう",
}
COUNTER_READING_OVERRIDES = {
    "匹": {
        1: "いっぴき",
        2: "にひき",
        3: "さんびき",
        4: "よんひき",
        5: "ごひき",
        6: "ろっぴき",
        7: "ななひき",
        8: "はっぴき",
        9: "きゅうひき",
        10: "じゅっぴき",
    },
    "本": {
        1: "いっぽん",
        2: "にほん",
        3: "さんぼん",
        4: "よんほん",
        5: "ごほん",
        6: "ろっぽん",
        7: "ななほん",
        8: "はっぽん",
        9: "きゅうほん",
        10: "じゅっぽん",
    },
    "杯": {
        1: "いっぱい",
        2: "にはい",
        3: "さんばい",
        4: "よんはい",
        5: "ごはい",
        6: "ろっぱい",
        7: "ななはい",
        8: "はっぱい",
        9: "きゅうはい",
        10: "じゅっぱい",
    },
    "人": {
        1: "ひとり",
        2: "ふたり",
        3: "さんにん",
        4: "よにん",
        5: "ごにん",
        6: "ろくにん",
        7: "ななにん",
        8: "はちにん",
        9: "きゅうにん",
        10: "じゅうにん",
    },
}
COUNTER_BASE_READINGS = {
    "枚": "まい",
    "個": "こ",
    "回": "かい",
    "階": "かい",
    "円": "えん",
    "歳": "さい",
    "才": "さい",
    "時": "じ",
    "分": "ふん",
}
NUMBER_COUNTER_RE = re.compile(
    r"(?<![0-9０-９一二三四五六七八九十百千])"
    r"([0-9０-９]+|[一二三四五六七八九十]+)"
    r"([ \t\u3000]*)"
    r"(匹|本|杯|人|枚|個|回|階|円|歳|才|時|分)"
)


def parse_japanese_number(text: str) -> int | None:
    value = (text or "").strip().translate(FULLWIDTH_DIGITS)
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value in KANJI_DIGIT_VALUES:
        return KANJI_DIGIT_VALUES[value]
    if "十" in value:
        left, _sep, right = value.partition("十")
        tens = KANJI_DIGIT_VALUES.get(left, 1) if left else 1
        ones = KANJI_DIGIT_VALUES.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def counter_reading(number_text: str, counter: str) -> str:
    number = parse_japanese_number(number_text)
    if number is None:
        return ""
    overrides = COUNTER_READING_OVERRIDES.get(counter)
    if overrides and number in overrides:
        return overrides[number]
    number_reading = NUMBER_READINGS.get(number)
    base_reading = COUNTER_BASE_READINGS.get(counter)
    if number_reading and base_reading:
        return number_reading + base_reading
    return ""


def iter_number_counter_matches(text: str):
    for match in NUMBER_COUNTER_RE.finditer(text or ""):
        reading = counter_reading(match.group(1), match.group(3))
        if reading:
            yield match, reading


def _expand_reading_text(text: str) -> list[str]:
    text = kata_to_hira((text or "").strip())
    if not text:
        return []
    if "{" not in text:
        return [part for part in text.split("|") if part]

    expanded = [""]
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "{":
            expanded = [prefix + ch for prefix in expanded]
            i += 1
            continue

        end = text.find("}", i + 1)
        if end < 0:
            return [text]
        choices = [choice for choice in text[i + 1 : end].split("|") if choice]
        if not choices:
            return [text]
        if len(expanded) * len(choices) > 64:
            return [text]
        expanded = [prefix + choice for prefix in expanded for choice in choices]
        i = end + 1
    return expanded


def _expand_reader_result(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _expand_reading_text(value)
    if isinstance(value, Iterable):
        out: list[str] = []
        for item in value:
            out.extend(_expand_reader_result(item))
        return out
    return _expand_reading_text(str(value))


def _add_unique(values: list[str], reading: str) -> None:
    for value in _expand_reading_text(reading):
        hira = kata_to_hira(value.strip())
        if hira and hira not in values:
            values.append(hira)


def reading_variants(reading: str) -> list[str]:
    reading = kata_to_hira(reading)
    if not reading:
        return []

    variants = [reading]
    first_char_maps = (VOICED, SEMI_VOICED, UNVOICED)
    for table in first_char_maps:
        shifted = reading[0].translate(table) + reading[1:]
        if shifted != reading and shifted not in variants:
            variants.append(shifted)

    for variant in list(variants):
        if len(variant) > 1 and variant[-1] in {"く", "き", "つ", "ち"}:
            sokuon = variant[:-1] + "っ"
            if sokuon not in variants:
                variants.append(sokuon)
    return variants


def voiced_variants(reading: str) -> list[str]:
    return reading_variants(reading)


def _reader_values(reader: SingleKanjiReader | None, ch: str) -> list[str]:
    if not is_real_kanji(ch):
        return []
    values: list[str] = []
    for value in COMMON_KANJI_READINGS.get(ch, ()):
        _add_unique(values, value)
    if reader is not None:
        try:
            raw = reader(ch)
        except Exception:
            raw = None
        for hira in _expand_reader_result(raw):
            if hira and hira not in values:
                values.append(hira)
    return values


def _candidate_variants(
    ch: str,
    single_kanji_reader: SingleKanjiReader | None,
) -> dict[str, float]:
    variants: dict[str, float] = {}
    for reading in _reader_values(single_kanji_reader, ch):
        for idx, variant in enumerate(reading_variants(reading)):
            score = 8.0 if idx == 0 else 6.0
            variants[variant] = max(score, variants.get(variant, 0.0))
    return variants


def _chunk_shape_score(chunk: str, mora_count: int) -> float | None:
    if not chunk or chunk[0] in INVALID_CHUNK_START:
        return None
    if chunk in {"ん", "っ", "ー"}:
        return None
    if mora_count == 1:
        if chunk in SINGLE_VOWEL_CHUNKS:
            return -2.5
        return 1.8
    if mora_count == 2:
        return 3.0
    if mora_count == 3:
        return 0.8
    if mora_count == 4:
        return -1.0
    return None


def _split_kanji_run_by_mora_shape(
    base: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None,
) -> list[Segment] | None:
    if base in IRREGULAR_WHOLE_WORDS or "々" in base:
        return None
    if not base or not all(is_real_kanji(ch) for ch in base):
        return None

    moras = split_moras(reading)
    n_chars = len(base)
    n_moras = len(moras)
    if n_moras < n_chars or n_moras > n_chars * 4:
        return None

    candidate_maps = [_candidate_variants(ch, single_kanji_reader) for ch in base]

    @lru_cache(maxsize=None)
    def best(char_idx: int, mora_idx: int) -> tuple[float, tuple[int, ...], int] | None:
        if char_idx == n_chars:
            if mora_idx == n_moras:
                return 0.0, (), 0
            return None

        chars_left = n_chars - char_idx
        moras_left = n_moras - mora_idx
        if moras_left < chars_left:
            return None

        best_result: tuple[float, tuple[int, ...], int] | None = None
        max_take = min(4, moras_left - (chars_left - 1))
        for take in range(1, max_take + 1):
            chunk = "".join(moras[mora_idx : mora_idx + take])
            shape_score = _chunk_shape_score(chunk, take)
            if shape_score is None:
                continue

            known_score = candidate_maps[char_idx].get(chunk, 0.0)
            score = known_score + shape_score
            known_matches = 1 if known_score else 0

            rest = best(char_idx + 1, mora_idx + take)
            if rest is None:
                continue
            rest_score, rest_lengths, rest_known = rest
            total = score + rest_score
            total_known = known_matches + rest_known

            if best_result is None:
                best_result = (total, (take,) + rest_lengths, total_known)
                continue

            best_score, best_lengths, best_known = best_result
            if (
                total > best_score
                or (total == best_score and total_known > best_known)
                or (
                    total == best_score
                    and total_known == best_known
                    and (take,) + rest_lengths < best_lengths
                )
            ):
                best_result = (total, (take,) + rest_lengths, total_known)

        return best_result

    result = best(0, 0)
    if result is None:
        return None
    score, lengths, known_matches = result
    if len(lengths) != n_chars:
        return None
    if known_matches == 0 and score <= (n_chars * 1.8):
        return None

    out: list[Segment] = []
    pos = 0
    for ch, take in zip(base, lengths):
        chunk = "".join(moras[pos : pos + take])
        if not chunk:
            return None
        out.append((ch, chunk))
        pos += take
    return out


def _split_kanji_run_by_suffix_anchors(
    base: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None,
) -> list[Segment] | None:
    parts: list[Segment] = [(ch, None) for ch in base]
    remaining = reading

    # Anchor known single-kanji readings from the right. This catches common
    # compounds where the final kanji keeps a dictionary-like reading, including
    # rendaku/sokuon shifts: 漢字, 合図, 日本語, 科学, 学校, 結晶.
    for idx in range(len(base) - 1, 0, -1):
        ch = base[idx]
        if not is_real_kanji(ch):
            return None
        singles = _reader_values(single_kanji_reader, ch)
        if not singles:
            return None

        matched = ""
        candidates: list[str] = []
        for single in singles:
            candidates.extend(reading_variants(single))
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


def split_kanji_run(
    base: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None = None,
    split_kanji_compounds: bool = False,
) -> list[Segment] | None:
    base = base or ""
    reading = kata_to_hira(reading or "")
    if not base or not reading or not all(is_kanji(ch) for ch in base):
        return None

    real_count = sum(1 for ch in base if is_real_kanji(ch))
    if real_count == 0:
        return [(base, None)]

    # Keep repetition-mark words together so one ruby label spans the whole word.
    if "々" in base:
        return [(base, reading)]

    if real_count == 1:
        return [(base, reading)] if base != "々" else [(base, None)]

    if not split_kanji_compounds:
        return [(base, reading)]

    anchored = _split_kanji_run_by_suffix_anchors(base, reading, single_kanji_reader)
    if anchored:
        return anchored
    return _split_kanji_run_by_mora_shape(base, reading, single_kanji_reader)


def split_furigana(
    base: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None = None,
    split_kanji_compounds: bool = False,
) -> list[Segment]:
    base = base or ""
    reading = kata_to_hira(reading or "")
    if not base:
        return []
    if not reading:
        return [(base, None)]
    for match, _counter_reading in iter_number_counter_matches(base):
        if match.start() == 0 and match.end() == len(base):
            return [(base, reading)]
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
        split = split_kanji_run(
            core,
            core_reading,
            single_kanji_reader,
            split_kanji_compounds=split_kanji_compounds,
        )
        out.extend(split if split else [(core, core_reading)])
    else:
        out.extend(
            _split_mixed_core(
                core,
                core_reading,
                single_kanji_reader,
                split_kanji_compounds=split_kanji_compounds,
            )
        )

    if suffix:
        out.append((suffix, None))
    return out


def _split_mixed_core(
    core: str,
    reading: str,
    single_kanji_reader: SingleKanjiReader | None,
    split_kanji_compounds: bool = False,
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
                    start = k
                    while k < len(core) and is_kana(core[k]):
                        k += 1
                    next_kana = kata_to_hira(core[start:k])
                    break
                if is_real_kanji(core[k]):
                    break
                k += 1

            if next_kana:
                end = reading.find(next_kana, pos)
                if end < pos and len(next_kana) > 1:
                    end = reading.find(next_kana[0], pos)
                if end < pos:
                    chunk = reading[pos:]
                    pos = len(reading)
                else:
                    chunk = reading[pos:end]
                    pos = end
            else:
                chunk = reading[pos:]
                pos = len(reading)

            split = split_kanji_run(
                run,
                chunk,
                single_kanji_reader,
                split_kanji_compounds=split_kanji_compounds,
            )
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
        if ruby and (any(is_kanji(ch) for ch in base) or is_katakana_ruby_base(base)):
            if (
                space_between_ruby
                and out
                and not out[-1].endswith((" ", "\n", "\t"))
                and not out[-1].endswith(("[", "(", "（", "{", "｛", "<", "＜", "「", "『", "【"))
            ):
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
        ("合図", "あいず", "合図[あいず]"),
        ("漢字", "かんじ", "漢字[かんじ]"),
        ("平仮名", "ひらがな", "平仮名[ひらがな]"),
        ("日本語", "にほんご", "日本語[にほんご]"),
        ("出口", "でぐち", "出口[でぐち]"),
        ("世界", "せかい", "世界[せかい]"),
        ("科学", "かがく", "科学[かがく]"),
        ("神様", "かみさま", "神様[かみさま]"),
        ("結晶", "けっしょう", "結晶[けっしょう]"),
        ("食べる", "たべる", "食[た]べる"),
        ("取り戻す", "とりもどす", "取[と]り 戻[もど]す"),
        ("乗り換える", "のりかえる", "乗[の]り 換[か]える"),
        ("時々", "ときどき", "時々[ときどき]"),
        ("今日", "きょう", "今日[きょう]"),
    )
