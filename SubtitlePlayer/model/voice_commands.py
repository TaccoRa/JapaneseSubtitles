"""Definitions and normalization for offline voice commands."""

from __future__ import annotations

import copy
import re
import unicodedata
from collections import OrderedDict
from typing import Any


VOICE_ACTIONS = OrderedDict(
    [
        ("play", {"label": "Play / pause", "dispatch": "voice_play", "en": ["play"], "de": ["abspielen"]}),
        ("pause", {"label": "Pause", "dispatch": "voice_pause", "en": ["pause"], "de": ["pausieren"]}),
        (
            "stop_listening",
            {
                "label": "Stop voice listening",
                "dispatch": "voice_stop_listening",
                "en": ["stop listening"],
                "de": ["zuhören stoppen", "spracherkennung stoppen"],
            },
        ),
        (
            "go_back",
            {
                "label": "Back",
                "dispatch": "voice_go_back",
                "repeatable": True,
                "sequence_first": True,
                "en": ["back", "go back"],
                "de": ["zurück", "zuruck"],
            },
        ),
        (
            "go_forward",
            {
                "label": "Forward",
                "dispatch": "voice_go_forward",
                "repeatable": True,
                "sequence_first": True,
                "en": ["forward", "go forward"],
                "de": ["vor"],
            },
        ),
        (
            "go_to_time",
            {
                "label": "Go to time",
                "dispatch": "voice_go_to_time",
                "timed": True,
                "en": ["go to", "jump to"],
                "de": ["gehe zu", "springe zu"],
            },
        ),
        (
            "subtitle_back",
            {
                "label": "Previous subtitle",
                "dispatch": "subtitle_back",
                "repeatable": True,
                "sequence_first": True,
                "en": ["previous subtitle"],
                "de": ["vorheriger untertitel"],
            },
        ),
        (
            "subtitle_forward",
            {
                "label": "Next subtitle",
                "dispatch": "subtitle_forward",
                "repeatable": True,
                "sequence_first": True,
                "en": ["next subtitle"],
                "de": ["nächster untertitel", "nachster untertitel"],
            },
        ),
        (
            "jump_sub_end",
            {
                "label": "Jump to subtitle end",
                "dispatch": "jump_sub_end",
                "sequence_first": True,
                "en": ["subtitle end"],
                "de": ["untertitel ende"],
            },
        ),
        (
            "show_subtitles",
            {
                "label": "Show subtitles",
                "dispatch": "voice_show_subtitles",
                "en": ["show subtitles"],
                "de": ["untertitel zeigen"],
            },
        ),
        (
            "hide_subtitles",
            {
                "label": "Hide subtitles",
                "dispatch": "voice_hide_subtitles",
                "en": ["hide subtitles"],
                "de": ["untertitel ausblenden"],
            },
        ),
        (
            "previous_episode",
            {
                "label": "Previous episode",
                "dispatch": "episode_dec",
                "en": ["previous episode"],
                "de": ["vorherige folge"],
            },
        ),
        (
            "next_episode",
            {
                "label": "Next episode",
                "dispatch": "episode_inc",
                "en": ["next episode"],
                "de": ["nächste folge", "nachste folge"],
            },
        ),
        (
            "speed_up",
            {
                "label": "Speed up",
                "dispatch": "fast_forward_speed_up",
                "repeatable": True,
                "en": ["speed up"],
                "de": ["schneller"],
            },
        ),
        (
            "speed_down",
            {
                "label": "Slow down",
                "dispatch": "fast_forward_speed_down",
                "repeatable": True,
                "en": ["slow down"],
                "de": ["langsamer"],
            },
        ),
        (
            "add_card",
            {"label": "Add Anki card", "dispatch": "voice_add_anki", "en": ["add card"], "de": ["karte hinzufügen", "karte hinzufugen"]},
        ),
        (
            "add_card_capture",
            {
                "label": "Add Anki card + capture",
                "dispatch": "voice_add_anki_capture",
                "indexed_capture": True,
                "en": ["add card with capture", "capture"],
                "de": ["karte mit aufnahme hinzufügen", "karte mit aufnahme hinzufugen", "aufnahme"],
            },
        ),
        (
            "capture_only",
            {
                "label": "Capture only",
                "dispatch": "voice_capture_only",
                "en": ["capture only"],
                "de": ["nur aufnehmen", "nur aufnahme"],
            },
        ),
        (
            "copy_selection",
            {
                "label": "Copy selection",
                "dispatch": "voice_copy_selection",
                "en": ["copy selection"],
                "de": ["auswahl kopieren"],
            },
        ),
        (
            "cycle_mode",
            {
                "label": "Next input mode",
                "dispatch": "voice_cycle_mode",
                "en": ["mode", "next mode", "switch mode"],
                "de": ["modus", "nächster modus", "modus wechseln"],
            },
        ),
        (
            "mode_1",
            {
                "label": "Input mode 1",
                "dispatch": "voice_mode_1",
                "en": ["mode 1", "mode one"],
                "de": ["modus 1", "modus eins"],
            },
        ),
        (
            "mode_2",
            {
                "label": "Input mode 2",
                "dispatch": "voice_mode_2",
                "en": ["mode 2", "mode two"],
                "de": ["modus 2", "modus zwei"],
            },
        ),
        (
            "mode_3",
            {
                "label": "Input mode 3",
                "dispatch": "voice_mode_3",
                "en": ["mode 3", "mode three"],
                "de": ["modus 3", "modus drei"],
            },
        ),
        ("show_app", {"label": "Show app", "dispatch": "alt_x", "en": ["show app"], "de": ["app zeigen"]}),
    ]
)


DEFAULT_WAKE_PREFIXES = {"en": "subtitles", "de": "untertitel"}

VOICE_REPEAT_WORDS = {
    "en": {
        1: ("1", "one"),
        2: ("2", "two"),
        3: ("3", "three"),
        4: ("4", "four"),
        5: ("5", "five"),
        6: ("6", "six"),
        7: ("7", "seven"),
        8: ("8", "eight"),
        9: ("9", "nine"),
        10: ("10", "ten"),
        11: ("11", "eleven"),
        12: ("12", "twelve"),
        13: ("13", "thirteen"),
        14: ("14", "fourteen"),
        15: ("15", "fifteen"),
        16: ("16", "sixteen"),
        17: ("17", "seventeen"),
        18: ("18", "eighteen"),
        19: ("19", "nineteen"),
        20: ("20", "twenty"),
    },
    "de": {
        1: ("1", "eins"),
        2: ("2", "zwei"),
        3: ("3", "drei"),
        4: ("4", "vier"),
        5: ("5", "fünf", "funf"),
        6: ("6", "sechs"),
        7: ("7", "sieben"),
        8: ("8", "acht"),
        9: ("9", "neun"),
        10: ("10", "zehn"),
        11: ("11", "elf"),
        12: ("12", "zwölf", "zwolf"),
        13: ("13", "dreizehn"),
        14: ("14", "vierzehn"),
        15: ("15", "fünfzehn", "funfzehn"),
        16: ("16", "sechzehn"),
        17: ("17", "siebzehn"),
        18: ("18", "achtzehn"),
        19: ("19", "neunzehn"),
        20: ("20", "zwanzig"),
    },
}

_ENGLISH_NUMBER_WORDS = {
    0: "zero",
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
    9: "nine",
    10: "ten",
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
}

_GERMAN_NUMBER_WORDS = {
    0: "null",
    1: "eins",
    2: "zwei",
    3: "drei",
    4: "vier",
    5: "fünf",
    6: "sechs",
    7: "sieben",
    8: "acht",
    9: "neun",
    10: "zehn",
    11: "elf",
    12: "zwölf",
    13: "dreizehn",
    14: "vierzehn",
    15: "fünfzehn",
    16: "sechzehn",
    17: "siebzehn",
    18: "achtzehn",
    19: "neunzehn",
    20: "zwanzig",
    30: "dreißig",
    40: "vierzig",
    50: "fünfzig",
}


def _number_word(language: str, number: int) -> str:
    number = max(0, min(59, int(number)))
    if language == "de":
        direct = _GERMAN_NUMBER_WORDS.get(number)
        if direct:
            return direct
        tens, ones = divmod(number, 10)
        ones_word = "ein" if ones == 1 else _GERMAN_NUMBER_WORDS[ones]
        return f"{ones_word}und{_GERMAN_NUMBER_WORDS[tens * 10]}"
    direct = _ENGLISH_NUMBER_WORDS.get(number)
    if direct:
        return direct
    tens, ones = divmod(number, 10)
    return f"{_ENGLISH_NUMBER_WORDS[tens * 10]} {_ENGLISH_NUMBER_WORDS[ones]}"


def _number_variants(language: str, number: int) -> list[str]:
    word = _number_word(language, number)
    variants = [str(int(number)), word]
    if language == "de":
        ascii_word = word.translate(str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"}))
        if ascii_word != word:
            variants.append(ascii_word)
    return list(dict.fromkeys(normalize_voice_phrase(value) for value in variants if value))


def iter_voice_time_suffixes(language: str):
    """Yield spoken suffixes for seconds and mm:ss times up to 59:59."""
    language = "de" if str(language).lower() == "de" else "en"
    variants = {number: _number_variants(language, number) for number in range(60)}
    for seconds in range(60):
        for value in variants[seconds]:
            yield value, seconds
    for minutes in range(60):
        for seconds in range(60):
            spoken = normalize_voice_phrase(
                f"{_number_word(language, minutes)} {_number_word(language, seconds)}"
            )
            pair_variants = [f"{minutes} {seconds}", spoken]
            if language == "de":
                pair_variants.append(
                    spoken.translate(str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"}))
                )
            for value in dict.fromkeys(normalize_voice_phrase(item) for item in pair_variants):
                yield value, minutes * 60 + seconds


def iter_indexed_capture_suffixes(language: str):
    """Yield 1-based and end-relative word positions for capture commands."""
    language = "de" if str(language).lower() == "de" else "en"
    last_words = ("letzte", "letztes", "letzter") if language == "de" else ("last",)
    for number in range(1, 60):
        for value in _number_variants(language, number):
            yield value, number
    for last_word in last_words:
        yield last_word, -1
        for number in range(1, 60):
            for value in _number_variants(language, number):
                yield normalize_voice_phrase(f"{last_word} {value}"), -number


def encode_voice_sequence(steps: list[tuple[str, int]]) -> str:
    encoded = []
    for action, repeat_count in steps:
        action = str(action or "").strip()
        if not action or "|" in action or "#" in action:
            continue
        encoded.append(f"{action}#{max(1, min(20, int(repeat_count)))}")
    return "voice_sequence:" + "|".join(encoded)


def decode_voice_sequence(value: Any) -> list[tuple[str, int]]:
    text = str(value or "")
    if not text.startswith("voice_sequence:"):
        return []
    result = []
    for item in text.split(":", 1)[1].split("|"):
        action, separator, raw_count = item.rpartition("#")
        if not separator or not action:
            continue
        try:
            count = max(1, min(20, int(raw_count)))
        except Exception:
            count = 1
        result.append((action, count))
    return result


def _mapped_action_and_repeat(mapped: str | tuple[str, int]) -> tuple[str, int]:
    if isinstance(mapped, (tuple, list)) and mapped:
        try:
            repeat_count = max(1, min(20, int(mapped[1])))
        except Exception:
            repeat_count = 1
        return str(mapped[0]), repeat_count
    return str(mapped), 1


def normalize_voice_phrase(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.casefold().strip()
    return re.sub(r"\s+", " ", text)


def split_voice_aliases(value: Any) -> list[str]:
    if isinstance(value, str):
        source = re.split(r"[;\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        source = list(value)
    else:
        source = []
    result: list[str] = []
    for item in source:
        alias = normalize_voice_phrase(item)
        if alias and alias not in result:
            result.append(alias)
    return result


def default_voice_commands() -> dict[str, dict[str, Any]]:
    commands: dict[str, dict[str, Any]] = OrderedDict()
    for action, spec in VOICE_ACTIONS.items():
        commands[action] = {
            "enabled": True,
            "en": list(spec["en"]),
            "de": list(spec["de"]),
        }
    return commands


def merge_voice_commands(raw: Any) -> dict[str, dict[str, Any]]:
    merged = default_voice_commands()
    if not isinstance(raw, dict):
        return merged
    for action, defaults in merged.items():
        incoming = raw.get(action)
        if not isinstance(incoming, dict):
            continue
        defaults["enabled"] = bool(incoming.get("enabled", defaults["enabled"]))
        for language in ("en", "de"):
            if language in incoming:
                defaults[language] = split_voice_aliases(incoming.get(language))
    return merged


def merge_wake_prefixes(raw: Any) -> dict[str, str]:
    merged = dict(DEFAULT_WAKE_PREFIXES)
    if isinstance(raw, dict):
        for language in ("en", "de"):
            if language in raw:
                merged[language] = normalize_voice_phrase(raw.get(language))
    return merged


def voice_alias_conflicts(
    commands: Any,
    prefixes: Any,
    require_prefix: bool = True,
) -> dict[str, dict[str, list[str]]]:
    merged_commands = merge_voice_commands(commands)
    merged_prefixes = merge_wake_prefixes(prefixes)
    conflicts: dict[str, dict[str, list[str]]] = {}
    for language in ("en", "de"):
        phrase_actions: dict[str, list[str]] = {}
        prefix = normalize_voice_phrase(merged_prefixes.get(language)) if require_prefix else ""
        for action, command in merged_commands.items():
            if not command.get("enabled"):
                continue
            for alias in split_voice_aliases(command.get(language)):
                phrase = normalize_voice_phrase(f"{prefix} {alias}" if prefix else alias)
                if VOICE_ACTIONS[action].get("timed"):
                    for suffix, _seconds in iter_voice_time_suffixes(language):
                        timed_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                        phrase_actions.setdefault(timed_phrase, []).append(action)
                    continue
                phrase_actions.setdefault(phrase, []).append(action)
                if VOICE_ACTIONS[action].get("indexed_capture"):
                    for suffix, _position in iter_indexed_capture_suffixes(language):
                        indexed_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                        phrase_actions.setdefault(indexed_phrase, []).append(action)
                if not VOICE_ACTIONS[action].get("repeatable"):
                    continue
                for variants in VOICE_REPEAT_WORDS[language].values():
                    for variant in variants:
                        repeated_phrase = normalize_voice_phrase(f"{phrase} {variant}")
                        phrase_actions.setdefault(repeated_phrase, []).append(action)
        duplicates = {
            phrase: actions
            for phrase, actions in phrase_actions.items()
            if len(set(actions)) > 1
        }
        if duplicates:
            conflicts[language] = duplicates
    return conflicts


def build_voice_phrase_map(
    language: str,
    commands: Any,
    prefixes: Any,
    *,
    require_prefix: bool = True,
) -> tuple[dict[str, str | tuple[str, int]], list[str]]:
    language = "de" if str(language).lower() == "de" else "en"
    merged_commands = merge_voice_commands(commands)
    merged_prefixes = merge_wake_prefixes(prefixes)
    prefix = normalize_voice_phrase(merged_prefixes[language]) if require_prefix else ""
    phrase_map: dict[str, str | tuple[str, int]] = OrderedDict()
    sequence_first: list[tuple[str, str | tuple[str, int]]] = []
    sequence_second: list[tuple[str, str | tuple[str, int]]] = []
    for action, command in merged_commands.items():
        if not command.get("enabled"):
            continue
        dispatch = str(VOICE_ACTIONS[action]["dispatch"])
        for alias in split_voice_aliases(command.get(language)):
            bare_phrase = normalize_voice_phrase(alias)
            phrase = normalize_voice_phrase(f"{prefix} {alias}" if prefix else alias)
            if VOICE_ACTIONS[action].get("timed"):
                for suffix, seconds in iter_voice_time_suffixes(language):
                    timed_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                    if timed_phrase:
                        phrase_map[timed_phrase] = f"{dispatch}:{int(seconds)}"
                continue
            if phrase:
                phrase_map[phrase] = dispatch
            if VOICE_ACTIONS[action].get("indexed_capture"):
                for suffix, position in iter_indexed_capture_suffixes(language):
                    indexed_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                    if indexed_phrase:
                        phrase_map[indexed_phrase] = f"voice_add_anki_capture_index:{int(position)}"
            if action in {"play", "pause"} and bare_phrase:
                sequence_second.append((bare_phrase, dispatch))
            if VOICE_ACTIONS[action].get("sequence_first") and bare_phrase:
                sequence_first.append((bare_phrase, dispatch))
            if not VOICE_ACTIONS[action].get("repeatable"):
                continue
            for repeat_count, variants in VOICE_REPEAT_WORDS[language].items():
                for variant in variants:
                    repeated_phrase = normalize_voice_phrase(f"{phrase} {variant}")
                    if repeated_phrase:
                        phrase_map[repeated_phrase] = (dispatch, int(repeat_count))
                    if VOICE_ACTIONS[action].get("sequence_first"):
                        bare_repeated = normalize_voice_phrase(f"{bare_phrase} {variant}")
                        if bare_repeated:
                            sequence_first.append((bare_repeated, (dispatch, int(repeat_count))))

    connector = "und" if language == "de" else "and"
    for first_phrase, first_mapping in sequence_first:
        for second_phrase, second_mapping in sequence_second:
            combined = normalize_voice_phrase(f"{first_phrase} {connector} {second_phrase}")
            full_phrase = normalize_voice_phrase(f"{prefix} {combined}" if prefix else combined)
            first_action, first_count = _mapped_action_and_repeat(first_mapping)
            second_action, second_count = _mapped_action_and_repeat(second_mapping)
            phrase_map[full_phrase] = encode_voice_sequence(
                [(first_action, first_count), (second_action, second_count)]
            )
    return phrase_map, [*phrase_map.keys(), "[unk]"]


def voice_command_defaults_for_config() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(default_voice_commands())
