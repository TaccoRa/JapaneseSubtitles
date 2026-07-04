from SubtitlePlayer.furigana_splitter import bracket_text, split_furigana
from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.view.popup import CopyPopup


def test_splitter_keeps_number_counter_whole_for_hover_ruby():
    assert (
        bracket_text(split_furigana("\uff11\u5339", "\u3044\u3063\u3074\u304d"))
        == "\uff11\u5339[\u3044\u3063\u3074\u304d]"
    )
    assert (
        bracket_text(split_furigana("\uff11 \u5339", "\u3044\u3063\u3074\u304d"))
        == "\uff11 \u5339[\u3044\u3063\u3074\u304d]"
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
