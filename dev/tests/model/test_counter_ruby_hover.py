from SubtitlePlayer.furigana_splitter import (
    bracket_text,
    counter_reading,
    iter_number_counter_matches,
    split_furigana,
)
from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.view.popup import CopyPopup
from SubtitlePlayer.model.anki_ruby import AddonRubyGenerator


def test_splitter_keeps_number_counter_whole_for_hover_ruby():
    assert (
        bracket_text(split_furigana("\uff11\u5339", "\u3044\u3063\u3074\u304d"))
        == "\uff11\u5339[\u3044\u3063\u3074\u304d]"
    )
    assert (
        bracket_text(split_furigana("\uff11 \u5339", "\u3044\u3063\u3074\u304d"))
        == "\uff11 \u5339[\u3044\u3063\u3074\u304d]"
    )


def test_splitter_reads_one_hatsu_as_ippatsu():
    assert counter_reading("\uff11", "\u767a") == "\u3044\u3063\u3071\u3064"
    matches = list(iter_number_counter_matches("\uff11\u767a\u306f\u304f\u308c\u3066\u3084\u308b"))
    assert [(match.group(0), reading) for match, reading in matches] == [
        ("\uff11\u767a", "\u3044\u3063\u3071\u3064"),
    ]
    assert (
        bracket_text(split_furigana("\uff11\u767a", "\u3044\u3063\u3071\u3064"))
        == "\uff11\u767a[\u3044\u3063\u3071\u3064]"
    )


def test_subtitle_source_ruby_keeps_number_counter_whole():
    manager = object.__new__(SubtitleManager)
    manager._auto_ruby_segments = lambda _text: None
    manager._get_ruby_generator = lambda: None

    assert manager._parse_ruby_segments("\uff11\u5339(\u3044\u3063\u3074\u304d)", allow_auto=True) == [
        ("\uff11\u5339", "\u3044\u3063\u3074\u304d"),
    ]


def test_popup_inline_ruby_keeps_number_counter_whole():
    popup = object.__new__(CopyPopup)

    assert popup._parse_inline_ruby("\uff11\u5339[\u3044\u3063\u3074\u304d]") == [
        [("\uff11\u5339", "\u3044\u3063\u3074\u304d")]
    ]


def test_numeric_idiom_does_not_form_a_counter_with_the_following_word():
    for text in ('万が一本当に死んだら 僕は殺人犯か？', '万一本当に起きたら'):
        assert list(iter_number_counter_matches(text)) == []
    assert [(match.group(0), reading) for match, reading in iter_number_counter_matches('万が一、一本でも残れば')
            ] == [('一本', 'いっぽん')]


def test_full_sentence_keeps_mangaichi_and_hontou_readings_separate():
    with AddonRubyGenerator() as generator:
        segments = generator.segments('万が一本当に死んだら 僕は殺人犯か？', split_kanji_compounds=False)
    assert ''.join(base for base, _ in segments) == '万が一本当に死んだら 僕は殺人犯か？'
    assert ('一本', 'いっぽん') not in segments
    assert ('本当', 'ほんとう') in segments
    prefix = segments[:next(i for i, (base, _) in enumerate(segments) if base == '本当')]
    assert ''.join(reading or base for base, reading in prefix) == 'まんがいち'
