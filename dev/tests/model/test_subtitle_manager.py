from SubtitlePlayer.model.config_manager import ConfigManager
from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.utils import parse_time_value


class _DictConfig:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key):
        return self.values.get(key)


def _cleaner(values):
    manager = object.__new__(SubtitleManager)
    manager.config = _DictConfig(values)
    return manager

def test_parse_time_value():
    assert parse_time_value("01:02", 0.0) == 62
    assert parse_time_value("1234", 0.0) == 12 * 60 + 34
    assert parse_time_value("not a time", 0.0) == 0.0


def test_clean_text_strips_html_tags_without_allowlist():
    manager = _cleaner({"SUBTITLE_AUTO_RUBY": False})
    assert manager._clean_text("<i>\u884c\u304f</i>") == "\u884c\u304f"


def test_speaker_template_preserves_trailing_space():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "template",
            "SUBTITLE_SPEAKER_TEMPLATE": "-({name})  ",
            "SUBTITLE_STRIP_PAREN_NOTES": False,
        }
    )
    assert manager._clean_text("\uff08\u30b4\u30f3\uff09\u884c\u304f") == "-(\u30b4\u30f3)  \u884c\u304f"


def test_strip_parenthetical_sound_note_inside_line():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u884c\u304f\uff08\u8db3\u97f3\uff09") == "\u884c\u304f"


def test_strip_parenthetical_descriptive_sound_note():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u884c\u304f\uff08 \u5012\u308c\u305f \u97f3\uff09") == "\u884c\u304f"


def test_strip_parenthetical_descriptive_sound_note_only_line():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\uff08 \u5012\u308c\u305f \u97f3\uff09") == ""


def test_strip_parenthetical_breath_note_with_trailing_punctuation():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\uff08 \u65b0\u4e00\u306e \u8352\u3044 \u606f\uff09 ?") == ""


def test_strip_parenthetical_breath_note_inside_line_with_trailing_punctuation():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u884c\u304f\uff08 \u65b0\u4e00\u306e \u8352\u3044 \u606f\uff09 ?") == "\u884c\u304f ?"


def test_strip_parenthetical_notes_removes_any_parenthetical_text():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u884c\u304f\uff08\u30b4\u30f3\uff09") == "\u884c\u304f"
    assert manager._clean_text("\u884c\u304f(\u30b4\u30f3)") == "\u884c\u304f"


def test_strip_parenthetical_notes_preserves_source_ruby_after_kanji():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u6f22\u5b57\uff08\u304b\u3093\u3058\uff09") == "\u6f22\u5b57\uff08\u304b\u3093\u3058\uff09"


def test_strip_parenthetical_notes_removes_source_ruby_when_auto_ruby_enabled():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": True,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u6f22\u5b57\uff08\u304b\u3093\u3058\uff09") == "\u6f22\u5b57"


def test_parenthetical_note_only_line_respects_setting():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": False,
        }
    )
    assert manager._clean_text("\uff08\u6b53\u58f0\uff09") == "\uff08\u6b53\u58f0\uff09"

def test_print_first_subtitles():
    config = ConfigManager("config.json")
    manager = SubtitleManager(config)
    subs = manager.load_subtitles(manager.srt_file)
    print("\n--- Middle 5 subtitles ---")
    for sub in subs[1:5]:
        print(f"{sub.index}: {sub.start} --> {sub.end}\n{sub.content}\n")
    assert len(subs) > 0  # Just to make pytest happy

def test_print_cleaned_subtitles():
    config = ConfigManager("config.json")
    manager = SubtitleManager(config)
    subs = manager.load_subtitles(manager.srt_file)
    cleaned_subs = [manager._clean_text(s.content) for s in subs]
    print("\n--- Cleaned Subtitles ---")
    i = 1
    for sub in cleaned_subs[1:6]:
        print(i,": ", sub)
        i += 1
    assert len(cleaned_subs) > 0  # Just to make pytest happy
