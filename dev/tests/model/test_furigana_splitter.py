from SubtitlePlayer.furigana_splitter import bracket_text, split_furigana


SINGLE_READINGS = {
    "図": "ず",
    "字": "じ",
    "名": "な",
    "仮": "か",
    "語": "ご",
    "本": "ほん",
    "口": "くち",
}


def single_reader(ch: str) -> str:
    return SINGLE_READINGS.get(ch, "")


def render(base: str, reading: str) -> str:
    return bracket_text(split_furigana(base, reading, single_reader))


def test_single_kanji():
    assert render("愛", "あい") == "愛[あい]"


def test_two_kanji_compounds_split_from_suffix_anchors():
    assert render("合図", "あいず") == "合[あい] 図[ず]"
    assert render("漢字", "かんじ") == "漢[かん] 字[じ]"


def test_three_kanji_compounds_split_from_suffix_anchors():
    assert render("平仮名", "ひらがな") == "平[ひら] 仮[が] 名[な]"
    assert render("日本語", "にほんご") == "日[に] 本[ほん] 語[ご]"


def test_rendaku_suffix_match():
    assert render("出口", "でぐち") == "出[で] 口[ぐち]"


def test_mixed_kanji_kana_only_rubies_kanji():
    assert render("食べる", "たべる") == "食[た]べる"
    assert render("取り戻す", "とりもどす") == "取[と]り戻[もど]す"


def test_repeated_kanji_keeps_iteration_mark_with_word():
    assert render("時々", "ときどき") == "時々[ときどき]"
    assert render("我々", "われわれ") == "我々[われわれ]"


def test_irregular_compound_stays_whole_when_no_anchor_matches():
    assert render("今日", "きょう") == "今日[きょう]"
    assert render("大人", "おとな") == "大人[おとな]"
