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
