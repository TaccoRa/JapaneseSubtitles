from SubtitlePlayer.model.annotation_provider import AnnotationProvider
from SubtitlePlayer.model.word_database import WordDatabase, WordEntry
import logging


class _Config:
    def __init__(self, values):
        self.values = values

    def get(self, key):
        return self.values.get(key)


def _provider(tmp_path, values=None):
    cfg = _Config(
        {
            "ANNOTATION_ENABLED": True,
            "ANNOTATION_MARK_LOCAL": True,
            "ANNOTATION_COLORIZE_ANKI": True,
            "ANNOTATION_COLORIZE_WANIKANI": True,
            "ANNOTATION_MIN_TOKEN_LENGTH": 1,
            "ANNOTATION_INCLUDE_PARTICLES": False,
            **(values or {}),
        }
    )
    db = WordDatabase(str(tmp_path / "words.json"))
    return AnnotationProvider(cfg, db), db


def test_disabled_provider_refresh_does_not_build_or_log_index(tmp_path, caplog):
    cfg = _Config({"ANNOTATION_ENABLED": False})
    db = WordDatabase(str(tmp_path / "words.json"))
    caplog.set_level(logging.INFO)
    caplog.clear()

    provider = AnnotationProvider(cfg, db)
    db.upsert(WordEntry(surface="test"), save=False)
    provider.refresh()

    assert provider._index == {}
    assert provider.last_build_ms == 0.0
    assert "Built annotation index" not in caplog.text


def test_surface_match_and_annotation_segments(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="人間", reading="にんげん"), save=False)
    db.save()
    provider.refresh()

    segments = provider.annotate_segments(
        [("人間", "にんげん")],
        lambda _text: [{"surface": "人間", "lookup": "人間", "start": 0, "end": 2}],
    )

    assert len(segments[0]) == 3
    assert segments[0][2]["status"] == "local_known"


def test_lemma_match(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="食べる", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token({"surface": "食べた", "lookup": "食べる", "start": 0, "end": 3})

    assert match.status == "anki_mature"


def test_suru_database_form_matches_inflected_subtitle_tokens(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u52c9\u5f37\u3059\u308b"), save=False)
    db.save()
    provider.refresh()

    line = "\u52c9\u5f37\u3057\u305f"
    tokens = [
        {"surface": "\u52c9\u5f37", "lookup": "\u52c9\u5f37", "pos1": "\u540d\u8a5e", "start": 0, "end": 2},
        {"surface": "\u3057", "lookup": "\u3059\u308b", "pos1": "\u52d5\u8a5e", "start": 2, "end": 3},
        {"surface": "\u305f", "lookup": "\u305f", "pos1": "\u52a9\u52d5\u8a5e", "start": 3, "end": 4},
    ]

    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    assert len(segments[0]) == 3
    assert segments[0][0] == line
    assert segments[0][2]["status"] == "local_known"


def test_suru_whole_token_inflection_matches_database_form(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u52c9\u5f37\u3059\u308b"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {"surface": "\u52c9\u5f37\u3057\u305f", "lookup": "\u52c9\u5f37\u3057\u305f", "start": 0, "end": 4}
    )

    assert match.status == "local_known"


def test_verb_auxiliary_form_spans_and_matches_dictionary_form(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u6bba\u3059", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    line = "\u6bba\u3055\u308c\u308b"
    tokens = [
        {"surface": "\u6bba\u3055", "lookup": "\u6bba\u3059", "pos1": "\u52d5\u8a5e", "start": 0, "end": 2},
        {"surface": "\u308c\u308b", "lookup": "\u308c\u308b", "pos1": "\u52a9\u52d5\u8a5e", "start": 2, "end": 4},
    ]

    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    assert len(segments[0]) == 3
    assert segments[0][0] == line
    assert segments[0][2]["status"] == "anki_mature"


def test_whole_token_passive_suru_suffix_matches_dictionary_form(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u6bba\u3059"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {"surface": "\u6bba\u3055\u308c\u308b", "lookup": "\u6bba\u3055\u308c\u308b", "start": 0, "end": 4}
    )

    assert match.status == "local_known"


def test_hover_annotation_details_are_optional(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u4eba", source="anki", status="anki_mature", meaning="person"), save=False)
    db.save()
    provider.refresh()

    segments = provider.annotate_segments(
        [("\u4eba", "\u3072\u3068")],
        lambda _text: [{"surface": "\u4eba", "lookup": "\u4eba", "start": 0, "end": 1}],
    )

    assert segments[0][2]["annotation_text"] == ""


def test_hover_annotation_details_can_show_status_and_meaning(tmp_path):
    provider, db = _provider(
        tmp_path,
        {"ANNOTATION_SHOW_CARD_STATUS_ON_HOVER": True, "ANNOTATION_SHOW_MEANING_ON_HOVER": True},
    )
    db.upsert(WordEntry(surface="\u4eba", source="anki", status="anki_mature", meaning="person"), save=False)
    db.save()
    provider.refresh()

    segments = provider.annotate_segments(
        [("\u4eba", "\u3072\u3068")],
        lambda _text: [{"surface": "\u4eba", "lookup": "\u4eba", "start": 0, "end": 1}],
    )

    assert segments[0][2]["annotation_text"] == "anki mature: person"


def test_ignored_priority_over_anki(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="猫", source="anki", status="anki_mature"), save=False)
    db.upsert(WordEntry(surface="猫", source="local", status="ignored"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token({"surface": "猫", "lookup": "猫", "start": 0, "end": 1})

    assert match.status == "ignored"


def test_particles_disabled_by_default(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="は"), save=False)
    db.save()
    provider.refresh()

    assert provider.match_token({"surface": "は", "lookup": "は", "pos1": "助詞", "start": 0, "end": 1}) is None


def test_particle_matching_can_be_enabled(tmp_path):
    provider, db = _provider(tmp_path, {"ANNOTATION_INCLUDE_PARTICLES": True})
    db.upsert(WordEntry(surface="は"), save=False)
    db.save()
    provider.refresh()

    assert provider.match_token({"surface": "は", "lookup": "は", "pos1": "助詞", "start": 0, "end": 1}).status == "local_known"


def test_hide_ruby_for_mature_known_words(tmp_path):
    provider, db = _provider(tmp_path, {"ANNOTATION_RUBY_MODE": "hide_mature"})
    db.upsert(WordEntry(surface="人間", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    segments = provider.annotate_segments(
        [("人間", "にんげん")],
        lambda _text: [{"surface": "人間", "lookup": "人間", "start": 0, "end": 2}],
    )

    assert segments[0][2]["hide_ruby"] is True
    assert segments[0][2]["hidden_ruby"] == "にんげん"


def test_partial_annotation_inside_ruby_segment_keeps_compound_ruby(tmp_path):
    provider, db = _provider(tmp_path, {"ANNOTATION_RUBY_MODE": "hide_mature"})
    db.upsert(WordEntry(surface="\u91cd\u8981", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    segments = provider.annotate_segments(
        [("\u91cd\u8981\u5668\u5b98", "\u3058\u3085\u3046\u3088\u3046\u304d\u304b\u3093")],
        lambda _text: [{"surface": "\u91cd\u8981", "lookup": "\u91cd\u8981", "start": 0, "end": 2}],
    )

    assert [segment[0] for segment in segments] == ["\u91cd\u8981", "\u5668\u5b98"]
    assert all(segment[1] is None for segment in segments)
    assert segments[0][2]["annotation"] is True
    assert segments[0][2]["hide_ruby"] is False
    assert segments[0][2]["hidden_ruby"] == ""
    assert segments[0][2]["ruby_group_ruby"] == "\u3058\u3085\u3046\u3088\u3046\u304d\u304b\u3093"
    assert segments[1][2]["ruby_group_ruby"] == "\u3058\u3085\u3046\u3088\u3046\u304d\u304b\u3093"


def test_renyou_form_matches_dictionary_verb_without_lookup(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u5207\u308b", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    segments = provider.annotate_segments(
        [("\u5207\u308a", None)],
        lambda _text: [{"surface": "\u5207\u308a", "lookup": "\u5207\u308a", "pos1": "\u52d5\u8a5e", "start": 0, "end": 2}],
    )

    assert len(segments[0]) == 3
    assert segments[0][2]["status"] == "anki_mature"


def test_derived_verb_noun_does_not_match_dictionary_verb_by_default(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u52d5\u304f", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u52d5\u304d",
            "lookup": "\u52d5\u304d",
            "reading": "\u3046\u3054\u304d",
            "pos1": "\u540d\u8a5e",
            "start": 0,
            "end": 2,
        }
    )

    assert match is None


def test_exact_derived_verb_noun_still_matches(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u52d5\u304d", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u52d5\u304d",
            "lookup": "\u52d5\u304d",
            "reading": "\u3046\u3054\u304d",
            "pos1": "\u540d\u8a5e",
            "start": 0,
            "end": 2,
        }
    )

    assert match.status == "anki_mature"


def test_noun_token_does_not_match_different_lookup_by_default(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u52d5\u304f", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u52d5\u304d",
            "lookup": "\u52d5\u304f",
            "reading": "\u3046\u3054\u304d",
            "pos1": "\u540d\u8a5e",
            "start": 0,
            "end": 2,
        }
    )

    assert match is None


def test_derived_verb_noun_matching_can_be_enabled(tmp_path):
    provider, db = _provider(tmp_path, {"ANNOTATION_MATCH_DERIVED_VERB_NOUNS": True})
    db.upsert(WordEntry(surface="\u52d5\u304f", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u52d5\u304d",
            "lookup": "\u52d5\u304d",
            "reading": "\u3046\u3054\u304d",
            "pos1": "\u540d\u8a5e",
            "start": 0,
            "end": 2,
        }
    )

    assert match.status == "anki_mature"


def test_other_derived_verb_nouns_are_blocked_by_default(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u4f11\u3080", source="anki", status="anki_mature"), save=False)
    db.upsert(WordEntry(surface="\u5207\u308b", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    assert provider.match_token(
        {"surface": "\u4f11\u307f", "lookup": "\u4f11\u307f", "pos1": "\u540d\u8a5e", "start": 0, "end": 2}
    ) is None
    assert provider.match_token(
        {"surface": "\u5207\u308a", "lookup": "\u5207\u308a", "pos1": "\u540d\u8a5e", "start": 0, "end": 2}
    ) is None


def test_real_verb_inflections_still_match_dictionary_form(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u52d5\u304f", source="anki", status="anki_mature"), save=False)
    db.upsert(WordEntry(surface="\u98df\u3079\u308b", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    assert provider.match_token(
        {"surface": "\u52d5\u3044\u305f", "lookup": "\u52d5\u304f", "pos1": "\u52d5\u8a5e", "start": 0, "end": 3}
    ).status == "anki_mature"
    assert provider.match_token(
        {"surface": "\u98df\u3079\u307e\u3057\u305f", "lookup": "\u98df\u3079\u308b", "pos1": "\u52d5\u8a5e", "start": 0, "end": 6}
    ).status == "anki_mature"


def test_adjective_adverbial_matches_but_nominalization_does_not(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u9ad8\u3044", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    assert provider.match_token(
        {"surface": "\u9ad8\u304f", "lookup": "\u9ad8\u3044", "pos1": "\u5f62\u5bb9\u8a5e", "start": 0, "end": 2}
    ).status == "anki_mature"
    assert provider.match_token(
        {"surface": "\u9ad8\u3055", "lookup": "\u9ad8\u3055", "pos1": "\u540d\u8a5e", "start": 0, "end": 2}
    ) is None


def test_same_kanji_different_reading_does_not_match(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(
        WordEntry(surface="\u4eba\u6c17", reading="\u306b\u3093\u304d", source="anki", status="anki_mature"),
        save=False,
    )
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u4eba\u6c17",
            "lookup": "\u4eba\u6c17",
            "reading": "\u3072\u3068\u3051",
            "pos1": "\u540d\u8a5e",
            "start": 0,
            "end": 2,
        }
    )

    assert match is None


def test_cjk_reading_field_value_does_not_block_surface_match(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u5c11\u5e74", reading="\u5c11\u5e74", source="anki", status="anki_unknown"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u5c11\u5e74",
            "lookup": "\u5c11\u5e74",
            "reading": "\u3057\u3087\u3046\u306d\u3093",
            "pos1": "\u540d\u8a5e",
            "start": 0,
            "end": 2,
        }
    )

    assert match.status == "anki_unknown"


def test_exact_phrase_split_by_tokenizer_still_matches(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u3060\u304b\u3089", source="anki", status="anki_young"), save=False)
    db.save()
    provider.refresh()

    line = "\u3060\u304b\u3089"
    tokens = [
        {"surface": "\u3060", "lookup": "\u3060", "pos1": "\u52a9\u52d5\u8a5e", "start": 0, "end": 1},
        {"surface": "\u304b\u3089", "lookup": "\u304b\u3089", "pos1": "\u52a9\u8a5e", "start": 1, "end": 3},
    ]
    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    assert len(segments[0]) == 3
    assert segments[0][0] == line
    assert segments[0][2]["status"] == "anki_young"


def test_exact_phrase_does_not_match_inside_longer_token(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u5c11\u5e74", source="anki", status="anki_young"), save=False)
    db.save()
    provider.refresh()

    line = "\u5c11\u5e74\u305f\u3061"
    tokens = [
        {"surface": line, "lookup": line, "pos1": "\u540d\u8a5e", "start": 0, "end": 4},
    ]
    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    assert segments == [(line, None)]


def test_kana_only_adverbial_does_not_match_different_adjective_lemma(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u65e8\u3044", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u3046\u307e\u304f",
            "lookup": "\u65e8\u3044",
            "reading": "\u3046\u307e\u304f",
            "pos1": "\u5f62\u5bb9\u8a5e",
            "start": 0,
            "end": 3,
        }
    )

    assert match is None


def test_exact_kana_only_entry_still_matches(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u3046\u307e\u304f", source="anki", status="anki_young"), save=False)
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u3046\u307e\u304f",
            "lookup": "\u65e8\u3044",
            "reading": "\u3046\u307e\u304f",
            "pos1": "\u5f62\u5bb9\u8a5e",
            "start": 0,
            "end": 3,
        }
    )

    assert match.status == "anki_young"


def test_inflected_dictionary_match_ignores_expected_reading_difference(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(
        WordEntry(surface="\u6bba\u3059", reading="\u3053\u308d\u3059", source="anki", status="anki_mature"),
        save=False,
    )
    db.save()
    provider.refresh()

    match = provider.match_token(
        {
            "surface": "\u6bba\u3055\u308c\u308b",
            "lookup": "\u6bba\u3059",
            "reading": "\u3053\u308d\u3055\u308c\u308b",
            "pos1": "\u52d5\u8a5e",
            "start": 0,
            "end": 4,
        }
    )

    assert match.status == "anki_mature"


def test_ruby_segment_can_match_inside_combined_token(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u524d", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    tokens = [
        {
            "surface": "\u304a\u524d",
            "lookup": "\u5fa1\u524d",
            "reading": "\u304a\u307e\u3048",
            "pos1": "\u4ee3\u540d\u8a5e",
            "start": 1,
            "end": 3,
        }
    ]
    segments = provider.annotate_segments(
        [
            ("\u201c\u304a", None),
            ("\u524d", "\u307e\u3048"),
            ("\u3082\u201d\uff1f", None),
        ],
        lambda _text: tokens,
    )

    assert [segment[0] for segment in segments] == [
        "\u201c\u304a",
        "\u524d",
        "\u3082\u201d\uff1f",
    ]
    assert segments[1][2]["status"] == "anki_mature"


def test_purpose_ni_renyou_form_matches_dictionary_verb_after_object_particle(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u6bba\u3059", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    line = "\u4ffa\u3092 \u6bba\u3057\u306b\uff1f"
    tokens = [
        {"surface": "\u4ffa", "lookup": "\u4ffa", "pos1": "\u540d\u8a5e", "start": 0, "end": 1},
        {"surface": "\u3092", "lookup": "\u3092", "pos1": "\u52a9\u8a5e", "start": 1, "end": 2},
        {"surface": " ", "lookup": " ", "pos1": "\u7a7a\u767d", "start": 2, "end": 3},
        {"surface": "\u6bba\u3057", "lookup": "\u6bba\u3057", "pos1": "\u540d\u8a5e", "start": 3, "end": 5},
        {"surface": "\u306b", "lookup": "\u306b", "pos1": "\u52a9\u8a5e", "start": 5, "end": 6},
        {"surface": "\uff1f", "lookup": "\uff1f", "pos1": "\u88dc\u52a9\u8a18\u53f7", "start": 6, "end": 7},
    ]

    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    matched = [segment for segment in segments if len(segment) == 3 and segment[0] == "\u6bba\u3057\u306b"]
    assert matched
    assert matched[0][2]["status"] == "anki_mature"


def test_purpose_ni_renyou_form_does_not_reopen_general_derived_noun_matching(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u52d5\u304f", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    line = "\u52d5\u304d\u306b"
    tokens = [
        {"surface": "\u52d5\u304d", "lookup": "\u52d5\u304d", "pos1": "\u540d\u8a5e", "start": 0, "end": 2},
        {"surface": "\u306b", "lookup": "\u306b", "pos1": "\u52a9\u8a5e", "start": 2, "end": 3},
    ]

    assert provider.annotate_segments([(line, None)], lambda _text: tokens) == [(line, None)]


def test_spaced_non_self_verb_tail_does_not_mark_components_from_base_verbs(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u5206\u304b\u308b", source="anki", status="anki_mature"), save=False)
    db.upsert(WordEntry(surface="\u5408\u3046", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    line = "\u5206\u304b\u308a \u5408\u3048\u308b"
    tokens = [
        {
            "surface": "\u5206\u304b\u308a",
            "lookup": "\u5206\u304b\u308b",
            "pos1": "\u52d5\u8a5e",
            "pos2": "\u4e00\u822c",
            "start": 0,
            "end": 3,
        },
        {"surface": " ", "lookup": " ", "pos1": "\u7a7a\u767d", "start": 3, "end": 4},
        {
            "surface": "\u5408\u3048\u308b",
            "lookup": "\u5408\u3048\u308b",
            "pos1": "\u52d5\u8a5e",
            "pos2": "\u975e\u81ea\u7acb\u53ef\u80fd",
            "start": 4,
            "end": 7,
        },
    ]

    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    assert segments == [(line, None)]


def test_spaced_compound_verb_does_not_mark_unrelated_component_verbs(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u5f15\u304f", source="anki", status="anki_mature"), save=False)
    db.upsert(WordEntry(surface="\u4e0a\u3052\u308b", source="anki", status="anki_mature"), save=False)
    db.save()
    provider.refresh()

    line = "\u4eca\u65e5\u306f\u3053\u308c\u3067 \u5f15\u304d \u63da\u3052\u3088\u3046"
    tokens = [
        {"surface": "\u4eca\u65e5", "lookup": "\u4eca\u65e5", "pos1": "\u540d\u8a5e", "start": 0, "end": 2},
        {
            "surface": "\u5f15\u304d",
            "lookup": "\u5f15\u304f-\u4ed6\u52d5\u8a5e",
            "pos1": "\u52d5\u8a5e",
            "pos2": "\u4e00\u822c",
            "start": 7,
            "end": 9,
        },
        {
            "surface": "\u63da\u3052\u3088\u3046",
            "lookup": "\u4e0a\u3052\u308b",
            "pos1": "\u52d5\u8a5e",
            "pos2": "\u975e\u81ea\u7acb\u53ef\u80fd",
            "start": 10,
            "end": 14,
        },
    ]

    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    assert all(not (len(segment) == 3 and segment[0] in {"\u5f15\u304d", "\u63da\u3052\u3088\u3046"}) for segment in segments)


def test_exact_compound_phrase_across_subtitle_space_wins_over_partial_base_verb(tmp_path):
    provider, db = _provider(tmp_path)
    db.upsert(WordEntry(surface="\u5206\u304b\u308b", source="anki", status="anki_mature"), save=False)
    db.upsert(WordEntry(surface="\u5206\u304b\u308a\u5408\u3048\u308b", source="anki", status="anki_unknown"), save=False)
    db.save()
    provider.refresh()

    line = "\u5206\u304b\u308a \u5408\u3048\u308b"
    tokens = [
        {
            "surface": "\u5206\u304b\u308a",
            "lookup": "\u5206\u304b\u308b",
            "pos1": "\u52d5\u8a5e",
            "pos2": "\u4e00\u822c",
            "start": 0,
            "end": 3,
        },
        {"surface": " ", "lookup": " ", "pos1": "\u7a7a\u767d", "start": 3, "end": 4},
        {
            "surface": "\u5408\u3048\u308b",
            "lookup": "\u5408\u3048\u308b",
            "pos1": "\u52d5\u8a5e",
            "pos2": "\u975e\u81ea\u7acb\u53ef\u80fd",
            "start": 4,
            "end": 7,
        },
    ]

    segments = provider.annotate_segments([(line, None)], lambda _text: tokens)

    assert len(segments[0]) == 3
    assert segments[0][0] == line
    assert segments[0][2]["status"] == "anki_unknown"
