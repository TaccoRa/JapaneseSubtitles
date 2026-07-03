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


def render_split(base: str, reading: str) -> str:
    return bracket_text(split_furigana(base, reading, single_reader, split_kanji_compounds=True))


def test_single_kanji():
    assert render("愛", "あい") == "愛[あい]"


def test_default_keeps_all_kanji_compounds_whole():
    assert render("人間", "にんげん") == "人間[にんげん]"
    assert render("漢字", "かんじ") == "漢字[かんじ]"


def test_optional_kanji_compounds_split_from_suffix_anchors():
    assert render_split("合図", "あいず") == "合[あい] 図[ず]"
    assert render_split("漢字", "かんじ") == "漢[かん] 字[じ]"


def test_optional_three_kanji_compounds_split_from_suffix_anchors():
    assert render_split("平仮名", "ひらがな") == "平[ひら] 仮[が] 名[な]"
    assert render_split("日本語", "にほんご") == "日[に] 本[ほん] 語[ご]"


def test_rendaku_suffix_match():
    assert render_split("出口", "でぐち") == "出[で] 口[ぐち]"


def test_common_compounds_split_by_mora_shape_when_reader_is_sparse():
    assert render_split("世界", "せかい") == "世[せ] 界[かい]"
    assert render_split("人間", "にんげん") == "人[にん] 間[げん]"
    assert render_split("能力", "のうりょく") == "能[のう] 力[りょく]"
    assert render_split("太陽神", "たいようしん") == "太[たい] 陽[よう] 神[しん]"


def test_reader_alternatives_and_sound_changes():
    def reader(ch: str) -> str:
        return {
            "晶": "{あき|しょう|あきら}",
            "校": "こう",
            "表": "{おもて|あらわ|ひょう}",
        }.get(ch, "")

    assert bracket_text(split_furigana("結晶", "けっしょう", reader, split_kanji_compounds=True)) == "結[けっ] 晶[しょう]"
    assert bracket_text(split_furigana("学校", "がっこう", reader, split_kanji_compounds=True)) == "学[がっ] 校[こう]"
    assert bracket_text(split_furigana("発表", "はっぴょう", reader, split_kanji_compounds=True)) == "発[はっ] 表[ぴょう]"


def test_short_unanchored_reading_stays_whole():
    assert render_split("一日", "いちにち") == "一[いち] 日[にち]"
    assert render("一日", "にち") == "一日[にち]"


def test_mixed_kanji_kana_only_rubies_kanji():
    assert render("食べる", "たべる") == "食[た]べる"
    assert render("取り戻す", "とりもどす") == "取[と]り 戻[もど]す"
    assert render("乗り換える", "のりかえる") == "乗[の]り 換[か]える"
    assert render("煮え切らない", "にえきらない") == "煮[に]え 切[き]らない"


def test_mixed_kanji_kana_anchors_full_okurigana_run():
    assert render("笑われる", "わらわれる") == "笑[わら]われる"
    assert render("笑わせる", "わらわせる") == "笑[わら]わせる"


def test_repeated_kanji_keeps_iteration_mark_with_word():
    assert render("時々", "ときどき") == "時々[ときどき]"
    assert render("我々", "われわれ") == "我々[われわれ]"


def test_irregular_compound_stays_whole_when_no_anchor_matches():
    assert render("今日", "きょう") == "今日[きょう]"
    assert render("大人", "おとな") == "大人[おとな]"
    assert render("明日", "あした") == "明日[あした]"
    assert render("昨日", "きのう") == "昨日[きのう]"
    assert render("土産", "みやげ") == "土産[みやげ]"
