from model.voice_commands import (
    build_voice_phrase_map,
    decode_voice_sequence,
    default_voice_commands,
    normalize_voice_phrase,
    voice_alias_conflicts,
)


def test_voice_phrase_normalization_and_prefix_mapping():
    commands = default_voice_commands()
    phrase_map, grammar = build_voice_phrase_map(
        "en",
        commands,
        {"en": "  Subtitles  ", "de": "Untertitel"},
    )

    assert normalize_voice_phrase("  SUBTITLES\tPLAY  ") == "subtitles play"
    assert phrase_map["subtitles play"] == "voice_play"
    assert "play" not in phrase_map
    assert grammar[-1] == "[unk]"


def test_voice_language_selects_only_that_languages_phrases():
    phrase_map, _grammar = build_voice_phrase_map(
        "de",
        default_voice_commands(),
        {"en": "subtitles", "de": "untertitel"},
    )

    assert phrase_map["untertitel abspielen"] == "voice_play"
    assert "subtitles play" not in phrase_map


def test_voice_alias_conflicts_are_reported_per_language():
    commands = default_voice_commands()
    commands["pause"]["en"] = ["play"]

    conflicts = voice_alias_conflicts(
        commands,
        {"en": "subtitles", "de": "untertitel"},
    )

    assert conflicts["en"]["subtitles play"] == ["play", "pause"]
    assert "de" not in conflicts


def test_disabled_voice_action_is_removed_from_grammar():
    commands = default_voice_commands()
    commands["add_card_capture"]["enabled"] = False

    phrase_map, _grammar = build_voice_phrase_map(
        "en",
        commands,
        {"en": "subtitles", "de": "untertitel"},
    )

    assert "subtitles add card with capture" not in phrase_map


def test_repeatable_voice_actions_accept_numeric_and_spoken_counts():
    phrase_map, _grammar = build_voice_phrase_map(
        "en",
        default_voice_commands(),
        {"en": "subtitles", "de": "untertitel"},
    )

    assert phrase_map["subtitles back 5"] == ("voice_go_back", 5)
    assert phrase_map["subtitles back five"] == ("voice_go_back", 5)
    assert phrase_map["subtitles next subtitle three"] == ("subtitle_forward", 3)


def test_voice_prefix_can_be_disabled_or_left_empty():
    commands = default_voice_commands()
    without_prefix, _grammar = build_voice_phrase_map(
        "en",
        commands,
        {"en": "subtitles", "de": "untertitel"},
        require_prefix=False,
    )
    empty_prefix, _grammar = build_voice_phrase_map(
        "en",
        commands,
        {"en": "", "de": "untertitel"},
        require_prefix=True,
    )

    assert without_prefix["play"] == "voice_play"
    assert empty_prefix["play"] == "voice_play"
    assert "subtitles play" not in without_prefix


def test_toggle_playback_is_not_a_voice_command():
    commands = default_voice_commands()
    phrase_map, _grammar = build_voice_phrase_map(
        "en",
        commands,
        {"en": "subtitles", "de": "untertitel"},
    )

    assert "toggle_playback" not in commands
    assert "subtitles toggle playback" not in phrase_map


def test_voice_absolute_time_phrases_map_to_seconds():
    phrase_map, _grammar = build_voice_phrase_map(
        "en",
        default_voice_commands(),
        {"en": "subtitles", "de": "untertitel"},
    )

    assert phrase_map["subtitles go to 2"] == "voice_go_to_time:2"
    assert phrase_map["subtitles go to fifteen twelve"] == "voice_go_to_time:912"
    assert phrase_map["subtitles jump to 15 12"] == "voice_go_to_time:912"

    german_map, _grammar = build_voice_phrase_map(
        "de",
        default_voice_commands(),
        {"en": "subtitles", "de": "untertitel"},
    )
    assert german_map["untertitel gehe zu 2"] == "voice_go_to_time:2"
    assert german_map["untertitel gehe zu fünfzehn zwölf"] == "voice_go_to_time:912"


def test_capture_phrases_are_exact_and_support_indexed_positions():
    commands = default_voice_commands()
    commands["add_card_capture"]["en"] = ["capture"]
    phrase_map, _grammar = build_voice_phrase_map(
        "en",
        commands,
        {"en": "subtitles", "de": "untertitel"},
    )

    assert phrase_map["subtitles capture"] == "voice_add_anki_capture"
    assert phrase_map["subtitles capture only"] == "voice_capture_only"
    assert phrase_map["subtitles capture 6"] == "voice_add_anki_capture_index:6"
    assert phrase_map["subtitles capture last"] == "voice_add_anki_capture_index:-1"
    assert phrase_map["subtitles capture last two"] == "voice_add_anki_capture_index:-2"


def test_back_and_play_builds_a_sequential_command():
    phrase_map, _grammar = build_voice_phrase_map(
        "en",
        default_voice_commands(),
        {"en": "subtitles", "de": "untertitel"},
    )

    assert decode_voice_sequence(phrase_map["subtitles back and play"]) == [
        ("voice_go_back", 1),
        ("voice_play", 1),
    ]
    assert decode_voice_sequence(phrase_map["subtitles back five and play"]) == [
        ("voice_go_back", 5),
        ("voice_play", 1),
    ]


def test_voice_mode_and_stop_listening_commands_are_available():
    phrase_map, _grammar = build_voice_phrase_map(
        "en",
        default_voice_commands(),
        {"en": "subtitles", "de": "untertitel"},
    )

    assert phrase_map["subtitles stop listening"] == "voice_stop_listening"
    assert phrase_map["subtitles mode"] == "voice_cycle_mode"
    assert phrase_map["subtitles next mode"] == "voice_cycle_mode"
    assert phrase_map["subtitles mode 1"] == "voice_mode_1"
    assert phrase_map["subtitles mode 3"] == "voice_mode_3"
