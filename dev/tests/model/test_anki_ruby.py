from SubtitlePlayer.model.anki_ruby import bracket_text_to_segments, escape_text


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
