import pytest

from anki_modify.fix_sentence_ruby_spacing import repair


@pytest.mark.parametrize("boundary", ["\n", "\r\n", "\r", "<br>", "<br/>", "<BR />"])
def test_inserts_space_and_preserves_break_and_reading(boundary):
    value = "見[み]んでも" + boundary + "結果[けっか]は 一緒[いっしょ]じゃ"
    expected = "見[み]んでも" + boundary + " 結果[けっか]は 一緒[いっしょ]じゃ"
    assert repair(value) == expected
    assert repair(expected) == expected


@pytest.mark.parametrize("value", ["plain\nplain", "ても<br> 結果[けっか]", "ても\n　結果[けっか]", "ても<br><span>結果[けっか]</span>"])
def test_preserves_other_text(value):
    assert repair(value) == value
