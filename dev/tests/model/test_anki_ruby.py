import logging
from types import SimpleNamespace

from SubtitlePlayer.model.anki_ruby import (
    AddonRubyGenerator,
    MecabController,
    _kata_to_hira,
    bracket_text_to_segments,
    escape_text,
)
from SubtitlePlayer.furigana_splitter import bracket_text


def test_escape_text_preserves_br_tags_and_strips_other_html():
    assert escape_text("a<br/>b\n<c>x</c>\uff5e") == "a<br>b x~"


def test_bracket_text_to_segments_keeps_compound_ruby_whole_by_default():
    assert bracket_text_to_segments("\u6f22\u5b57[\u304b\u3093\u3058]") == [
        ("\u6f22\u5b57", "\u304b\u3093\u3058"),
    ]


def test_bracket_text_to_segments_splits_compound_ruby_when_enabled():
    assert bracket_text_to_segments("\u6f22\u5b57[\u304b\u3093\u3058]", split_kanji_compounds=True) == [
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


def test_bracket_text_to_segments_does_not_attach_previous_plain_text():
    assert bracket_text_to_segments("\u4e00 \u65e5[\u306b\u3061]") == [
        ("\u4e00 ", None),
        ("\u65e5", "\u306b\u3061"),
    ]


def test_addon_single_kanji_reading_uses_kakasi_alternatives():
    with AddonRubyGenerator() as generator:
        value = generator.single_kanji_reading("\u529b")

    assert "\u308a\u3087\u304f" in value


def test_addon_segments_use_source_parenthetical_ruby():
    with AddonRubyGenerator() as generator:
        rendered = bracket_text(generator.segments("\u5927\u6728(\u304a\u304a\u304d)\u5927\u6a39(\u305f\u3044\u3058\u3085)"))

    assert rendered == "\u5927\u6728[\u304a\u304a\u304d] \u5927\u6a39[\u305f\u3044\u3058\u3085]"


def test_addon_segments_collapse_repeated_inline_reading():
    with AddonRubyGenerator() as generator:
        rendered = bracket_text(generator.segments("\u5982\u6708\u304d\u3055\u3089\u304e\u3055\u3093"))

    assert rendered == "\u5982\u6708[\u304d\u3055\u3089\u304e]\u3055\u3093"


def test_addon_segments_use_inline_reading_to_correct_context_reading():
    with AddonRubyGenerator() as generator:
        rendered = bracket_text(
            generator.segments(
                "\u89d2\u304b\u304f\u306f "
                "\u52dd\u304b\u3066\u306a\u3044 "
                "\u9003\u306b\u3052\u308d "
                "\u614c\u3042\u308f\u3066\u308b\u306a "
                "\u9593\u9055\u307e\u3061\u304c\u3044\u306d"
            )
        )

    assert rendered == (
        "\u89d2[\u304b\u304f]\u306f "
        "\u52dd[\u304b]\u3066\u306a\u3044 "
        "\u9003[\u306b]\u3052\u308d "
        "\u614c[\u3042\u308f]\u3066\u308b\u306a "
        "\u9593\u9055[\u307e\u3061\u304c]\u3044\u306d"
    )


def test_addon_segments_accept_single_kanji_inline_reading_before_next_kanji():
    with AddonRubyGenerator() as generator:
        segments = generator.segments("\u8535\u304f\u3089\u9593\u304b\u3093\u5ba4\u9577\u3057\u3064\u3061\u3087\u3046")

    assert segments == [
        ("\u8535", "\u304f\u3089"),
        ("\u9593", "\u304b\u3093"),
        ("\u5ba4\u9577", "\u3057\u3064\u3061\u3087\u3046"),
    ]


def test_addon_segments_do_not_treat_okurigana_before_next_kanji_as_source_ruby():
    with AddonRubyGenerator() as generator:
        rendered = bracket_text(
            generator.segments(
                "\u901a\u3063\u3066\u53f3\u624b "
                "\u4e57\u308a\u63db\u3048\u308b "
                "\u771f\u3063\u6697"
            )
        )

    assert rendered == (
        "\u901a[\u3068\u304a]\u3063\u3066 \u53f3\u624b[\u307f\u304e\u3066] "
        "\u4e57[\u306e]\u308a \u63db[\u304b]\u3048\u308b "
        "\u771f[\u307e]\u3063 \u6697[\u304f\u3089]"
    )


def test_addon_segments_render_katakana_ruby_and_common_reading_overrides():
    with AddonRubyGenerator() as generator:
        rendered = bracket_text(
            generator.segments(
                "\u30aa\u30ea\u30f3\u30d4\u30c3\u30af "
                "\u30df\u30ae\u30fc "
                "\u30d1\u30e9\u30b5\u30a4\u30c8\u3067\u5341\u5206\u3060"
            )
        )

    assert rendered == (
        "\u30aa\u30ea\u30f3\u30d4\u30c3\u30af[\u304a\u308a\u3093\u3074\u3063\u304f] "
        "\u30df\u30ae\u30fc[\u307f\u304e\u30fc] "
        "\u30d1\u30e9\u30b5\u30a4\u30c8[\u3071\u3089\u3055\u3044\u3068]\u3067 "
        "\u5341\u5206[\u3058\u3085\u3046\u3076\u3093]\u3060"
    )


def test_addon_segments_merge_adjacent_kanji_name_tokens_by_default():
    with AddonRubyGenerator() as generator:
        rendered = bracket_text(generator.segments("\u65b0\u4e00 \u65b0\u4e00"))

    assert rendered == "\u65b0\u4e00[\u3057\u3093\u3044\u3061] \u65b0\u4e00[\u3057\u3093\u3044\u3061]"


def test_addon_segments_counter_reading_spans_number_and_counter():
    with AddonRubyGenerator() as generator:
        rendered = bracket_text(generator.segments("\uff11 \u5339"))

    assert rendered == "\uff11 \u5339[\u3044\u3063\u3074\u304d]"


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
