from SubtitlePlayer.model.config_manager import ConfigManager
from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.utils import parse_time_value

def test_parse_time_value():
    assert parse_time_value("01:02", 0.0) == 62
    assert parse_time_value("1234", 0.0) == 12 * 60 + 34
    assert parse_time_value("not a time", 0.0) == 0.0

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