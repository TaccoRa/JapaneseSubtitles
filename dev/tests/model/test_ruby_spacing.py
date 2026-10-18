import pytest

from SubtitlePlayer.model.anki_client import AnkiClient
from SubtitlePlayer.model.anki_ruby import AddonRubyGenerator, bracket_text_to_segments, escape_text
from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.model.text_spacing import normalize_ruby_spacing


SEPARATORS = [" ", "\t", "\u00a0", "\u1680", "\u2007", "\u2009", "\u200b", "\u202f", "\u3000", "\ufeff", "&nbsp;", "&#160;", "&#x200B;", "&thinsp;"]


@pytest.mark.parametrize("separator", SEPARATORS)
def test_bracket_ruby_does_not_cross_space_like_boundary(separator):
    assert bracket_text_to_segments(f"一{separator}日[にち]") == [
        ("一 ", None), ("日", "にち"),
    ]


@pytest.mark.parametrize("separator", SEPARATORS)
def test_source_counter_ruby_covers_number_and_unit(separator):
    manager = object.__new__(SubtitleManager)
    assert manager._parse_ruby_segments(f"１{separator}匹(いっぴき)", allow_auto=False) == [
        ("１ 匹", "いっぴき"),
    ]


@pytest.mark.parametrize("separator", SEPARATORS)
def test_export_uses_real_space_between_plain_text_and_ruby(separator):
    client = object.__new__(AnkiClient)
    assert client._segments_to_bracket_text(
        [(f"そうなの{separator}", None), ("続", "つづ"), ("いて", None)],
        sentence_spacing=True,
    ) == "そうなの 続[つづ]いて"


@pytest.mark.parametrize("separator", SEPARATORS)
def test_reading_engine_receives_normalized_space(separator):
    assert escape_text(f"彼{separator}全身") == "彼 全身"


@pytest.mark.parametrize("separator", ["\u00a0", "\u200b", "\u3000", "&nbsp;"])
def test_real_reading_engine_keeps_bases_and_rubies_aligned(separator):
    with AddonRubyGenerator() as generator:
        assert generator.segments(f"彼{separator}全身") == [
            ("彼", "かれ"), (" ", None), ("全身", "ぜんしん"),
        ]


def test_normalization_preserves_lines_spacing_and_japanese_characters():
    assert normalize_ruby_spacing("Ａ１\u3000\u3000匹\r\n次\n行") == "Ａ１  匹\r\n次\n行"
    assert normalize_ruby_spacing("&lt;literal&gt; &amp; &nbsp;") == "&lt;literal&gt; &amp;  "
    assert normalize_ruby_spacing("一\u2060日") == "一\u2060日"  # A word joiner is not a separator.


def test_cleaned_subtitle_decodes_space_entities_before_ruby_parsing():
    manager = object.__new__(SubtitleManager)
    manager.config = type("Config", (), {"get": lambda self, key: None})()
    cleaned = manager._clean_text("<i>１&nbsp;匹(いっぴき)</i>\n彼&#x200B;全身")
    assert cleaned == "１ 匹(いっぴき)\n彼 全身"
    assert manager._parse_ruby_segments(cleaned.splitlines()[0], allow_auto=False) == [("１ 匹", "いっぴき")]


def test_new_stroke_media_uses_the_protected_prefix():
    assert AnkiClient.DEFAULT_STROKE_MEDIA_PREFIX == "_stroke_"
