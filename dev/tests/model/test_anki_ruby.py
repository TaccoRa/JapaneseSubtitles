import logging
from types import SimpleNamespace

from SubtitlePlayer.model.anki_ruby import MecabController, _kata_to_hira, bracket_text_to_segments, escape_text


def test_escape_text_preserves_br_tags_and_strips_other_html():
    assert escape_text("a<br/>b\n<c>x</c>\uff5e") == "a<br>b x~"


def test_bracket_text_to_segments_splits_compound_ruby():
    assert bracket_text_to_segments("\u6f22\u5b57[\u304b\u3093\u3058]") == [
        ("\u6f22", "\u304b\u3093"),
        ("\u5b57", "\u3058"),
    ]


def test_bracket_text_to_segments_keeps_non_kanji_base_plain():
    assert bracket_text_to_segments("abc[\u3048\u30fc\u3073\u30fc\u3057\u30fc]") == [
        ("abc", None),
    ]


def test_bracket_text_to_segments_keeps_katakana_base_ruby_as_hiragana():
    assert bracket_text_to_segments(
        "\u30e0\u30c1\u30e3\u30af\u30c1\u30e3[\u30e0\u30c1\u30e3\u30af\u30c1\u30e3]"
    ) == [
        ("\u30e0\u30c1\u30e3\u30af\u30c1\u30e3", "\u3080\u3061\u3083\u304f\u3061\u3083"),
    ]


def test_bracket_text_to_segments_keeps_iteration_mark_with_word():
    assert bracket_text_to_segments("\u6211\u3005[\u308f\u308c\u308f\u308c]") == [
        ("\u6211\u3005", "\u308f\u308c\u308f\u308c"),
    ]


def test_mecab_output_formats_equal_katakana_reading_as_ruby():
    controller = object.__new__(MecabController)
    controller.kakasi = None

    assert controller._format_mecab_output(
        "\u30e0\u30c1\u30e3\u30af\u30c1\u30e3[\u30e0\u30c1\u30e3\u30af\u30c1\u30e3]"
    ) == "\u30e0\u30c1\u30e3\u30af\u30c1\u30e3[\u3080\u3061\u3083\u304f\u3061\u3083]"


def test_mecab_output_handles_empty_katakana_and_fullwidth_space(caplog):
    controller = object.__new__(MecabController)
    controller.kakasi = SimpleNamespace(reading=_kata_to_hira)
    caplog.set_level(logging.WARNING)

    result = controller._format_mecab_output(
        "\u30df\u30ae\u30fc[] \uff1f[\uff1f] \u3000[\u3000] "
        "\u30df\u30ae\u30fc[] \u304c[\u30ac] \u6cbb\u7642[\u30c1\u30ea\u30e7\u30a6] \u3092[\u30f2] \uff1f[\uff1f] "
    )

    assert "\u30df\u30ae\u30fc[\u307f\u304e\u30fc]" in result
    assert "\u6cbb\u7642[\u3061\u308a\u3087\u3046]" in result
    assert "Unexpected output from mecab" not in caplog.text
