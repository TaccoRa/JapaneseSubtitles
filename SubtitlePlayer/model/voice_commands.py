"""Definitions and normalization for offline voice commands."""

from __future__ import annotations

import copy
import re
import unicodedata
from collections import OrderedDict
from itertools import combinations, permutations, product
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
            "abort",
            {
                "label": "Abort current voice action",
                "dispatch": "voice_abort",
                "en": ["abort", "cancel command"],
                "de": ["abbrechen", "befehl abbrechen"],
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
            "add",
            {
                "label": "Add",
                "dispatch": "voice_add",
                "indexed_dispatch": "voice_add_index",
                "multi_indexed_dispatch": "voice_add",
                "en": ["add"],
                "de": ["hinzufügen", "hinzufugen"],
            },
        ),
        (
            "add_together",
            {
                "label": "Add together",
                "dispatch": "voice_add_together",
                "multi_indexed_dispatch": "voice_add_together",
                "en": ["add together"],
                "de": ["zusammen hinzufügen", "zusammen hinzufugen"],
            },
        ),
        (
            "capture",
            {
                "label": "Capture",
                "dispatch": "voice_capture",
                "indexed_dispatch": "voice_capture_index",
                "multi_indexed_dispatch": "voice_capture",
                "en": ["capture"],
                "de": ["aufnehmen", "aufnahme"],
            },
        ),
        (
            "capture_together",
            {
                "label": "Capture together",
                "dispatch": "voice_capture_together",
                "multi_indexed_dispatch": "voice_capture_together",
                "en": ["capture together"],
                "de": ["zusammen aufnehmen"],
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
            "highlight_indexed",
            {
                "label": "Highlight indexed subtitle word",
                "dispatch": "voice_highlight_index",
                "indexed_dispatch": "voice_highlight_index",
                "indexed_only": True,
                "multi_indexed_dispatch": "voice_highlight",
                "multi_indexed_max_count": 5,
                "multi_indexed_max_position": 20,
                "en": ["highlight"],
                "de": ["markiere", "hervorheben"],
            },
        ),
        (
            "highlight_add",
            {
                "label": "Add highlighted word",
                "dispatch": "voice_highlight_add_index",
                "indexed_dispatch": "voice_highlight_add_index",
                "indexed_only": True,
                "multi_indexed_dispatch": "voice_highlight_add",
                "multi_indexed_max_count": 5,
                "multi_indexed_max_position": 20,
                "en": ["add highlight"],
                "de": ["markierung hinzufügen", "markierung hinzufugen"],
            },
        ),
        (
            "highlight_remove",
            {
                "label": "Remove highlighted word",
                "dispatch": "voice_highlight_remove_index",
                "indexed_dispatch": "voice_highlight_remove_index",
                "indexed_only": True,
                "multi_indexed_dispatch": "voice_highlight_remove",
                "multi_indexed_max_count": 5,
                "multi_indexed_max_position": 20,
                "en": ["remove highlight"],
                "de": ["markierung entfernen"],
            },
        ),
        (
            "highlight_clear",
            {
                "label": "Remove highlighted words",
                "dispatch": "voice_highlight_clear",
                "en": ["highlight remove", "remove highlight", "clear highlight"],
                "de": ["markierung entfernen", "markierung löschen", "markierung loschen"],
            },
        ),
        (
            "show_highlighted_dictionary",
            {
                "label": "Dictionary",
                "dispatch": "voice_dictionary",
                "indexed_dispatch": "voice_dictionary_index",
                "multi_indexed_dispatch": "voice_dictionary",
                "en": ["show dictionary", "dictionary"],
                "de": ["wörterbuch", "woerterbuch", "wörterbuch zeigen", "woerterbuch zeigen"],
            },
        ),
        (
            "show_highlighted_dictionary_together",
            {
                "label": "Dictionary together",
                "dispatch": "voice_dictionary_together",
                "multi_indexed_dispatch": "voice_dictionary_together",
                "en": ["dictionary together", "show dictionary together"],
                "de": [
                    "wörterbuch zusammen",
                    "woerterbuch zusammen",
                    "wörterbuch zusammen zeigen",
                    "woerterbuch zusammen zeigen",
                ],
            },
        ),
        (
            "show_highlighted_translation",
            {
                "label": "Translate together",
                "dispatch": "voice_translate",
                "indexed_dispatch": "voice_translate_index",
                "multi_indexed_dispatch": "voice_translate",
                "en": ["translate", "show translation", "translation"],
                "de": ["übersetzen", "uebersetzen", "übersetzung zeigen", "uebersetzung zeigen"],
            },
        ),
        (
            "translate_each_highlighted",
            {
                "label": "Translate each",
                "dispatch": "voice_translate_each",
                "multi_indexed_dispatch": "voice_translate_each",
                "en": ["translate each"],
                "de": [
                    "jedes wort übersetzen",
                    "jedes wort uebersetzen",
                    "einzeln übersetzen",
                    "einzeln uebersetzen",
                ],
            },
        ),
        (
            "show_highlighted_status",
            {
                "label": "Anki/database status",
                "dispatch": "voice_status",
                "indexed_dispatch": "voice_status_index",
                "multi_indexed_dispatch": "voice_status",
                "en": ["status", "show status"],
                "de": ["status", "status zeigen"],
            },
        ),
        (
            "copy_selection",
            {
                "label": "Copy active result",
                "dispatch": "voice_copy",
                "indexed_dispatch": "voice_copy_index",
                "multi_indexed_dispatch": "voice_copy",
                "en": ["copy", "copy selection"],
                "de": ["kopieren", "auswahl kopieren"],
            },
        ),
        (
            "copy_kanji",
            {
                "label": "Copy highlighted Japanese",
                "dispatch": "voice_copy_kanji",
                "en": ["copy kanji", "copy highlighted"],
                "de": ["kanji kopieren", "markierung kopieren"],
            },
        ),
        (
            "copy_kanji_each",
            {
                "label": "Copy highlighted Japanese separately",
                "dispatch": "voice_copy_kanji_each",
                "en": ["copy kanji each", "copy each kanji"],
                "de": ["jedes kanji kopieren", "kanji einzeln kopieren"],
            },
        ),
        (
            "copy_dictionary",
            {
                "label": "Copy dictionary for each word",
                "dispatch": "voice_copy_dictionary",
                "en": ["copy dictionary", "copy dictionary each"],
                "de": ["wörterbuch kopieren", "wörterbuch einzeln kopieren", "woerterbuch kopieren"],
            },
        ),
        (
            "copy_dictionary_together",
            {
                "label": "Copy dictionary together",
                "dispatch": "voice_copy_dictionary_together",
                "en": ["copy dictionary together"],
                "de": ["wörterbuch zusammen kopieren", "woerterbuch zusammen kopieren"],
            },
        ),
        (
            "copy_translation",
            {
                "label": "Copy combined translation",
                "dispatch": "voice_copy_translation",
                "en": ["copy translation", "copy translation together"],
                "de": ["übersetzung kopieren", "uebersetzung kopieren", "übersetzung zusammen kopieren"],
            },
        ),
        (
            "copy_translation_each",
            {
                "label": "Copy translations separately",
                "dispatch": "voice_copy_translation_each",
                "en": ["copy translation each", "copy each translation"],
                "de": ["jede übersetzung kopieren", "jede uebersetzung kopieren"],
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
        (
            "language_english",
            {
                "label": "Recognition language: English",
                "dispatch": "voice_language_english",
                "en": ["English voice", "switch to English"],
                "de": ["Englisch", "zu Englisch wechseln"],
            },
        ),
        (
            "language_german",
            {
                "label": "Recognition language: German",
                "dispatch": "voice_language_german",
                "en": ["German voice", "switch to German"],
                "de": ["Deutsch", "zu Deutsch wechseln"],
            },
        ),
        (
            "show_app",
            {
                "label": "Show / hide app settings",
                "dispatch": "voice_toggle_app",
                "en": ["app", "show app"],
                "de": ["app", "app zeigen"],
            },
        ),
        (
            "toggle_display",
            {
                "label": "Show / hide subtitles and controls",
                "dispatch": "voice_toggle_display",
                "en": ["show", "show controls"],
                "de": ["anzeigen", "steuerung anzeigen"],
            },
        ),
        (
            "show_settings",
            {
                "label": "Show settings",
                "dispatch": "voice_show_settings",
                "en": ["settings", "show settings"],
                "de": ["einstellungen", "einstellungen zeigen"],
            },
        ),
        (
            "open_advanced_settings",
            {
                "label": "Open Advanced Settings",
                "dispatch": "voice_open_advanced_settings",
                "en": ["open advanced settings", "advanced settings"],
                "de": ["erweiterte einstellungen öffnen", "erweiterte einstellungen oeffnen"],
            },
        ),
        (
            "close_advanced_settings",
            {
                "label": "Close Advanced Settings",
                "dispatch": "voice_close_advanced_settings",
                "en": ["close advanced settings"],
                "de": ["erweiterte einstellungen schließen", "erweiterte einstellungen schliessen"],
            },
        ),
    ]
)


VOICE_ACTION_GROUPS = OrderedDict(
    [
        (
            "Playback",
            ("play", "pause", "go_back", "go_forward", "go_to_time", "speed_up", "speed_down"),
        ),
        (
            "Subtitles & Episodes",
            (
                "subtitle_back",
                "subtitle_forward",
                "jump_sub_end",
                "show_subtitles",
                "hide_subtitles",
                "previous_episode",
                "next_episode",
            ),
        ),
        (
            "Anki & Capture",
            ("add", "add_together", "capture", "capture_together", "capture_only"),
        ),
        (
            "Highlight, Lookup & Copy",
            (
                "highlight_indexed",
                "highlight_add",
                "highlight_remove",
                "highlight_clear",
                "show_highlighted_dictionary",
                "show_highlighted_dictionary_together",
                "show_highlighted_translation",
                "translate_each_highlighted",
                "show_highlighted_status",
                "copy_selection",
                "copy_kanji",
                "copy_kanji_each",
                "copy_dictionary",
                "copy_dictionary_together",
                "copy_translation",
                "copy_translation_each",
            ),
        ),
        (
            "Voice & App",
            (
                "stop_listening",
                "abort",
                "cycle_mode",
                "mode_1",
                "mode_2",
                "mode_3",
                "language_english",
                "language_german",
                "show_app",
                "toggle_display",
                "show_settings",
                "open_advanced_settings",
                "close_advanced_settings",
            ),
        ),
    ]
)


VOICE_ACTION_TOOLTIPS = {
    "play": "Toggle video and SubtitlePlayer playback. This works whether playback is currently running or paused.",
    "pause": "Pause the external video and SubtitlePlayer without toggling an already paused state.",
    "stop_listening": "Disable voice recognition. Use the configured physical voice-toggle hotkey to enable it again.",
    "abort": "Cancel pending steps from the current voice action and immediately clear its wait indicator. An Anki request already sent cannot be undone.",
    "go_back": "Move the video and subtitles back. Add a count, for example 'back 5', to repeat the configured browser Back hotkey.",
    "go_forward": "Move the video and subtitles forward. Add a count, for example 'forward 5', to repeat the configured browser Forward hotkey.",
    "go_to_time": "Jump to a timestamp. Examples: 'go to 2' means 00:02 and 'go to fifteen twelve' means 15:12.",
    "subtitle_back": "Jump to an earlier subtitle. An optional number repeats the action.",
    "subtitle_forward": "Jump to a later subtitle. An optional number repeats the action.",
    "jump_sub_end": "Jump to the end of the subtitle currently containing the playback position.",
    "show_subtitles": "Show the subtitle overlay without toggling it when it is already visible.",
    "hide_subtitles": "Hide the subtitle overlay without toggling it when it is already hidden.",
    "previous_episode": "Switch to the previous available episode in the current episode map.",
    "next_episode": "Switch to the next available episode in the current episode map.",
    "speed_up": "Increase fast-forward speed by 0.1. An optional number repeats the increase.",
    "speed_down": "Decrease fast-forward speed by 0.1. An optional number repeats the decrease.",
    "add": "With highlighted words, create one Anki card per word. Without highlights, add the popup selection or hovered subtitle word. Position lists such as 'add 8, 9 and 12' add those words separately.",
    "add_together": "Create one Anki card from all highlighted words as one expression. A position list such as 'add together 8, 9 and 12' selects and combines those words first.",
    "capture": "With highlighted words, create one card per word, capture media once on the newest card, and copy it to the other exact-sentence cards. Position lists such as 'capture 8, 9 and 12' are supported.",
    "capture_together": "Create one card from all highlighted words together and capture its media once. A position list selects and combines those words first.",
    "capture_only": "Run external media capture for the latest Anki card without adding another card. Exact-sentence media propagation still runs.",
    "highlight_indexed": "Highlight subtitle words by position. Examples: 'highlight 2', 'highlight 2 until 5', 'highlight 4 and 6 and 7', and 'highlight 8, 9 and 12'. Lists support up to five ascending positions from the first 12 words. 'Last 2' means the second word from the end.",
    "highlight_add": "Add positions to the existing subtitle highlight, for example 'add highlight 8' or 'add highlight 2 and 14 and 15'.",
    "highlight_remove": "Remove positions from the existing subtitle highlight, for example 'remove highlight 9'.",
    "highlight_clear": "Remove every persistent voice highlight from the current subtitle.",
    "show_highlighted_dictionary": "Show dictionary entries for highlighted words. Without highlighting first, use a position such as 'dictionary 2', 'dictionary last', or 'dictionary end 2'. Combine it with translate, translate each, or status using 'and'.",
    "show_highlighted_dictionary_together": "Look up all highlighted words together as one dictionary expression. Combine it with translate or translate each using 'and'.",
    "show_highlighted_translation": "Translate all highlighted words together as one phrase. Without highlighting first, use a position such as 'translate 2' or 'translate last'. Combine it with dictionary or status using 'and'.",
    "translate_each_highlighted": "Translate every highlighted word independently and show the individual results together. Combine it with dictionary or status using 'and'.",
    "show_highlighted_status": "Show Anki/database status for highlighted words. Use 'status 2' for one indexed word, or combine status with dictionary or translation using 'and'.",
    "copy_selection": "Copy the currently displayed hover result. Translation is preferred when a translation is visible. Indexed forms such as 'copy 2' copy that subtitle word.",
    "copy_kanji": "Copy the highlighted Japanese as one sentence-faithful selection.",
    "copy_kanji_each": "Copy each highlighted Japanese word on a separate line.",
    "copy_dictionary": "Copy one dictionary result for each highlighted word.",
    "copy_dictionary_together": "Copy one dictionary lookup for the combined highlighted expression.",
    "copy_translation": "Copy the combined highlighted-word translation after it has been translated.",
    "copy_translation_each": "Copy each highlighted-word translation on a separate labelled line.",
    "cycle_mode": "Cycle to the next SubtitlePlayer input mode.",
    "mode_1": "Switch directly to input mode 1.",
    "mode_2": "Switch directly to input mode 2.",
    "mode_3": "Switch directly to input mode 3.",
    "language_english": "Switch voice recognition to English when the English offline model is installed.",
    "language_german": "Switch voice recognition to German when the German offline model is installed.",
    "show_app": "Toggle the main settings window and an already-open Advanced Settings window without changing subtitle/control visibility.",
    "toggle_display": "Show or hide the subtitle and control windows together without changing settings-window visibility.",
    "show_settings": "Show and raise the main SubtitlePlayer settings window.",
    "open_advanced_settings": "Open or raise Advanced Settings without closing it when it is already visible.",
    "close_advanced_settings": "Close Advanced Settings without hiding the main settings window.",
}

_LEGACY_VOICE_ACTION_KEYS = {
    "add_card",
    "add_indexed",
    "add_each_highlighted",
    "add_card_capture",
    "capture_each_highlighted",
}

_LEGACY_LOOKUP_ALIAS_UPGRADES = {
    "show_highlighted_dictionary": {
        "en": ("dictionary",),
        "de": ("wörterbuch", "woerterbuch"),
    },
    "show_highlighted_translation": {
        "en": ("translate",),
        "de": ("übersetzen", "uebersetzen"),
    },
    "copy_selection": {
        "en": ("copy",),
        "de": ("kopieren",),
    },
}

_VOICE_ALIAS_UPGRADES = {
    "show_app": {
        "en": ("app", "show app"),
        "de": ("app", "app zeigen"),
    },
}

for _group_name, _group_actions in VOICE_ACTION_GROUPS.items():
    for _action in _group_actions:
        if _action in VOICE_ACTIONS:
            VOICE_ACTIONS[_action]["group"] = _group_name
for _action, _spec in VOICE_ACTIONS.items():
    _spec["tooltip"] = VOICE_ACTION_TOOLTIPS.get(
        _action,
        f"Run the {_spec.get('label') or _action} voice action.",
    )


VOICE_HOVER_LAYER_ACTIONS = OrderedDict(
    [
        ("show_highlighted_dictionary", "dictionary"),
        ("show_highlighted_dictionary_together", "dictionary_together"),
        ("show_highlighted_translation", "translation"),
        ("translate_each_highlighted", "translation_each"),
        ("show_highlighted_status", "status"),
    ]
)
VOICE_HOVER_LAYER_ORDER = (
    "dictionary",
    "dictionary_together",
    "translation",
    "translation_each",
    "status",
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


def iter_indexed_word_suffixes(language: str):
    """Yield 1-based and end-relative subtitle-word positions."""
    language = "de" if str(language).lower() == "de" else "en"
    last_words = ("letzte", "letztes", "letzter", "ende") if language == "de" else ("last", "end")
    for number in range(1, 60):
        for value in _number_variants(language, number):
            yield value, number
    if language == "en":
        # The constrained recognizer can prefer these homophones because "to"
        # also appears in highlight ranges.
        yield "to", 2
        yield "too", 2
    for last_word in last_words:
        yield last_word, -1
        for number in range(1, 60):
            for value in _number_variants(language, number):
                yield normalize_voice_phrase(f"{last_word} {value}"), -number


def iter_multi_indexed_suffixes(
    language: str,
    max_position: int = 12,
    max_selection_count: int = 3,
):
    """Yield bounded ranges and natural spoken lists of subtitle positions."""
    language = "de" if str(language).lower() == "de" else "en"
    range_words = ("bis",) if language == "de" else ("to", "until")
    and_word = "und" if language == "de" else "and"
    maximum_position = max(2, min(20, int(max_position)))
    positions = range(1, maximum_position + 1)
    words = {number: _number_word(language, number) for number in positions}

    def phrase_variants(value: str):
        yield normalize_voice_phrase(value)

    for left in positions:
        for right in positions:
            if left == right:
                continue
            for range_word in range_words:
                for phrase in phrase_variants(f"{words[left]} {range_word} {words[right]}"):
                    yield phrase, "range", left, right

    maximum_count = max(2, min(5, int(max_selection_count)))
    for count in range(2, maximum_count + 1):
        selection_positions = (
            positions
            if count <= 3
            else range(1, min(12, maximum_position) + 1)
        )
        for selected in combinations(selection_positions, count):
            selected_words = [words[value] for value in selected]
            spoken_variants = [f" {and_word} ".join(selected_words)]
            if count > 2:
                spoken_variants.append(f"{' '.join(selected_words[:-1])} {and_word} {selected_words[-1]}")
            for spoken in dict.fromkeys(spoken_variants):
                for phrase in phrase_variants(spoken):
                    yield phrase, "selection", *selected


def iter_multi_highlight_suffixes(language: str, max_position: int = 12, max_selection_count: int = 5):
    """Compatibility alias for callers using the former highlight-only helper."""
    yield from iter_multi_indexed_suffixes(language, max_position, max_selection_count)


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
    legacy_schema = bool(_LEGACY_VOICE_ACTION_KEYS.intersection(raw))
    for action, defaults in merged.items():
        incoming = raw.get(action)
        if not isinstance(incoming, dict):
            continue
        defaults["enabled"] = bool(incoming.get("enabled", defaults["enabled"]))
        for language in ("en", "de"):
            if language in incoming:
                defaults[language] = split_voice_aliases(incoming.get(language))
    if legacy_schema:
        for action, language_aliases in _LEGACY_LOOKUP_ALIAS_UPGRADES.items():
            command = merged.get(action)
            if not isinstance(command, dict):
                continue
            for language, aliases in language_aliases.items():
                current = split_voice_aliases(command.get(language))
                command[language] = list(dict.fromkeys([*aliases, *current]))
    for action, language_aliases in _VOICE_ALIAS_UPGRADES.items():
        command = merged.get(action)
        if not isinstance(command, dict):
            continue
        for language, aliases in language_aliases.items():
            current = split_voice_aliases(command.get(language))
            command[language] = list(dict.fromkeys([*aliases, *current]))
    return merged


def merge_wake_prefixes(raw: Any) -> dict[str, str]:
    merged = dict(DEFAULT_WAKE_PREFIXES)
    if isinstance(raw, dict):
        for language in ("en", "de"):
            if language in raw:
                merged[language] = normalize_voice_phrase(raw.get(language))
    return merged


def encode_voice_hover_layers(layers: Any) -> str:
    requested = {str(layer or "").strip() for layer in (layers or ())}
    requested.intersection_update(VOICE_HOVER_LAYER_ORDER)
    if "translation_each" in requested:
        requested.discard("translation")
    if "dictionary_together" in requested:
        requested.discard("dictionary")
    ordered = [layer for layer in VOICE_HOVER_LAYER_ORDER if layer in requested]
    return "voice_hover_layers:" + ",".join(ordered)


def decode_voice_hover_layers(value: Any) -> tuple[str, ...]:
    text = str(value or "")
    if not text.startswith("voice_hover_layers:"):
        return ()
    requested = {
        part.strip()
        for part in text.split(":", 1)[1].split(",")
        if part.strip() in VOICE_HOVER_LAYER_ORDER
    }
    if "translation_each" in requested:
        requested.discard("translation")
    if "dictionary_together" in requested:
        requested.discard("dictionary")
    return tuple(layer for layer in VOICE_HOVER_LAYER_ORDER if layer in requested)


def iter_voice_hover_layer_phrases(language: str, commands: dict[str, dict[str, Any]]):
    """Yield combined hover aliases and one atomic multi-layer dispatch."""
    language = "de" if str(language).lower() == "de" else "en"
    connector = "und" if language == "de" else "and"
    entries = []
    for action, layer in VOICE_HOVER_LAYER_ACTIONS.items():
        command = commands.get(action)
        if not isinstance(command, dict) or not command.get("enabled"):
            continue
        aliases = split_voice_aliases(command.get(language))
        if aliases:
            entries.append((action, layer, aliases))

    seen = set()
    for length in range(2, min(3, len(entries)) + 1):
        for ordered_entries in permutations(entries, length):
            layers = tuple(entry[1] for entry in ordered_entries)
            if "translation" in layers and "translation_each" in layers:
                continue
            if "dictionary" in layers and "dictionary_together" in layers:
                continue
            dispatch = encode_voice_hover_layers(layers)
            actions = tuple(entry[0] for entry in ordered_entries)
            for aliases in product(*(entry[2] for entry in ordered_entries)):
                for separator in (f" {connector} ", " "):
                    phrase = normalize_voice_phrase(separator.join(aliases))
                    marker = (phrase, dispatch)
                    if not phrase or marker in seen:
                        continue
                    seen.add(marker)
                    yield phrase, dispatch, actions


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
                if not VOICE_ACTIONS[action].get("indexed_only"):
                    phrase_actions.setdefault(phrase, []).append(action)
                if VOICE_ACTIONS[action].get("indexed_dispatch"):
                    for suffix, _position in iter_indexed_word_suffixes(language):
                        indexed_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                        phrase_actions.setdefault(indexed_phrase, []).append(action)
                if VOICE_ACTIONS[action].get("multi_indexed_dispatch"):
                    max_count = int(VOICE_ACTIONS[action].get("multi_indexed_max_count") or 3)
                    max_position = int(VOICE_ACTIONS[action].get("multi_indexed_max_position") or 12)
                    for suffix, _kind, *_positions in iter_multi_indexed_suffixes(
                        language,
                        max_position=max_position,
                        max_selection_count=max_count,
                    ):
                        multi_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                        phrase_actions.setdefault(multi_phrase, []).append(action)
                if not VOICE_ACTIONS[action].get("repeatable"):
                    continue
                for variants in VOICE_REPEAT_WORDS[language].values():
                    for variant in variants:
                        repeated_phrase = normalize_voice_phrase(f"{phrase} {variant}")
                        phrase_actions.setdefault(repeated_phrase, []).append(action)
        for bare_phrase, dispatch, _actions in iter_voice_hover_layer_phrases(language, merged_commands):
            phrase = normalize_voice_phrase(f"{prefix} {bare_phrase}" if prefix else bare_phrase)
            phrase_actions.setdefault(phrase, []).append(dispatch)
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
            if phrase and not VOICE_ACTIONS[action].get("indexed_only"):
                phrase_map[phrase] = dispatch
            indexed_dispatch = str(VOICE_ACTIONS[action].get("indexed_dispatch") or "").strip()
            if indexed_dispatch:
                for suffix, position in iter_indexed_word_suffixes(language):
                    indexed_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                    if indexed_phrase:
                        phrase_map[indexed_phrase] = f"{indexed_dispatch}:{int(position)}"
            multi_indexed_dispatch = str(
                VOICE_ACTIONS[action].get("multi_indexed_dispatch") or ""
            ).strip()
            if multi_indexed_dispatch:
                max_count = int(VOICE_ACTIONS[action].get("multi_indexed_max_count") or 3)
                max_position = int(VOICE_ACTIONS[action].get("multi_indexed_max_position") or 12)
                for suffix, kind, *positions in iter_multi_indexed_suffixes(
                    language,
                    max_position=max_position,
                    max_selection_count=max_count,
                ):
                    multi_phrase = normalize_voice_phrase(f"{phrase} {suffix}")
                    encoded_positions = ",".join(str(int(position)) for position in positions)
                    if multi_phrase and encoded_positions:
                        phrase_map[multi_phrase] = (
                            f"{multi_indexed_dispatch}_{kind}:{encoded_positions}"
                        )
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

    for bare_phrase, dispatch, _actions in iter_voice_hover_layer_phrases(language, merged_commands):
        phrase = normalize_voice_phrase(f"{prefix} {bare_phrase}" if prefix else bare_phrase)
        if phrase:
            phrase_map[phrase] = dispatch

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
