from SubtitlePlayer.model.anki_ruby import MecabController, bracket_text_to_segments, escape_text


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


def test_mecab_output_formats_equal_katakana_reading_as_ruby():
    controller = object.__new__(MecabController)
    controller.kakasi = None

    assert controller._format_mecab_output(
        "\u30e0\u30c1\u30e3\u30af\u30c1\u30e3[\u30e0\u30c1\u30e3\u30af\u30c1\u30e3]"
    ) == "\u30e0\u30c1\u30e3\u30af\u30c1\u30e3[\u3080\u3061\u3083\u304f\u3061\u3083]"
