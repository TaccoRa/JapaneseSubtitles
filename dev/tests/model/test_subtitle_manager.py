from SubtitlePlayer.model.config_manager import ConfigManager
from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.utils import parse_time_value
from collections import OrderedDict, defaultdict
import datetime
import json
import threading
import srt


class _DictConfig:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


def _cleaner(values):
    manager = object.__new__(SubtitleManager)
    manager.config = _DictConfig(values)
    return manager

def test_parse_time_value():
    assert parse_time_value("01:02", 0.0) == 62
    assert parse_time_value("1234", 0.0) == 12 * 60 + 34
    assert parse_time_value("not a time", 0.0) == 0.0


def test_episode_dropdown_items_include_season_episode_label():
    manager = object.__new__(SubtitleManager)
    manager.remote_flag = False
    manager.local_srt_files = [
        {"global": 27, "season": 2, "episode": 1, "name": "Show.S02E01.srt"},
        {"global": 28, "season": 2, "episode": 2, "name": "Show.S02E02.srt"},
    ]

    items = manager.get_episode_dropdown_items()

    assert [item["label"] for item in items] == ["27 (S2E1)", "28 (S2E2)"]
    assert items[0]["global"] == 27
    assert items[0]["season"] == 2
    assert items[0]["episode"] == 1


def test_remote_episode_item_merge_preserves_old_items_and_prefers_new_metadata():
    manager = object.__new__(SubtitleManager)
    old_items = [
        {"path": "subtitles/anime_tv/baki-dou/Season1/old.S01E01.srt", "name": "old.S01E01.srt", "global": 1},
        {"path": "subtitles/anime_tv/baki-dou/Season1/old.S01E02.srt", "name": "old.S01E02.srt", "global": 2},
    ]
    new_items = [
        {"path": "subtitles/anime_tv/baki-dou/Season1/old.S01E02.srt", "name": "new.S01E02.srt", "global": 2},
        {"path": "subtitles/anime_tv/baki-dou part 2/Season1/new.S01E13.srt", "name": "new.S01E13.srt", "global": 13},
    ]

    merged = manager._merge_remote_episode_items(old_items, new_items)

    assert [item["global"] for item in merged] == [1, 2, 13]
    assert merged[1]["name"] == "new.S01E02.srt"
    assert "baki-dou part 2" in merged[2]["path"]


def test_refresh_remote_episode_map_force_merges_broad_part_folder_results():
    manager = object.__new__(SubtitleManager)
    manager.remote_flag = True
    manager.anime_folder_name = "baki-dou"
    manager.github_owner = "owner"
    manager.github_repo = "repo"
    manager.all_results_items = [
        {"path": "subtitles/anime_tv/baki-dou/Season1/Baki.S01E01.srt", "name": "Baki.S01E01.srt", "season": 1, "episode": 1, "global": 1},
    ]
    force_values = []

    def create_map(force_refresh=False):
        force_values.append(force_refresh)
        manager.all_results_items = [
            {"path": "subtitles/anime_tv/baki-dou/Season1/Baki.S01E01.srt", "name": "Baki.S01E01.srt", "season": 1, "episode": 1, "global": 1},
        ]

    manager._create_remote_episode_map_per_season = create_map
    manager._search_remote_candidates_in_path = lambda anime, repo_path: [
        {"path": "subtitles/anime_tv/baki-dou part 2/Season1/Baki.S01E01.srt", "name": "Baki.S01E01.srt", "season": 1, "episode": 1, "global": 1},
    ]
    manager.update_local_srt_files = lambda: []
    manager._write_remote_episode_search_cache = lambda items, last_search="": None

    result = manager.refresh_remote_episode_map(force=True)

    assert result["ok"] is True
    assert force_values == [True]
    assert any("baki-dou part 2" in item["path"] for item in manager.all_results_items)
    assert manager.remote_episode_map_global[2]["episode"] == 1
    assert manager.remote_episode_map_global[2]["season"] == 2


def test_remote_metadata_preserves_saved_search_query_for_last_github_url():
    manager = object.__new__(SubtitleManager)
    config = _DictConfig(
        {
            "LAST_REMOTE_SEARCH_QUERY": "Baki-Dou",
            "LAST_ANIME_NAME": "Baki-Dou",
        }
    )
    manager.config = config
    manager._cached_search_query_for_remote_path = lambda remote_path: ""

    manager._extract_and_set_remote_episode_metadata(
        "https://github.com/o/r/blob/main/subtitles/anime_tv/Baki-Dou%20Part%20Two/Season1/Baki.S01E14.srt",
        preserve_search_query=True,
    )

    assert manager.anime_folder_name == "Baki-Dou"
    assert manager.get_display_anime_name() == "Baki-Dou Part Two"
    assert manager.remote_search_query == "Baki-Dou"
    assert config.get("LAST_REMOTE_SEARCH_QUERY") == "Baki-Dou"
    assert config.get("LAST_ANIME_NAME") == "Baki-Dou"
    assert config.get("LAST_DISPLAY_ANIME_NAME") == "Baki-Dou Part Two"


def test_remote_metadata_direct_url_can_replace_old_saved_search_query():
    manager = object.__new__(SubtitleManager)
    config = _DictConfig(
        {
            "LAST_REMOTE_SEARCH_QUERY": "Old Anime",
            "LAST_ANIME_NAME": "Old Anime",
        }
    )
    manager.config = config
    manager._cached_search_query_for_remote_path = lambda remote_path: ""

    manager._extract_and_set_remote_episode_metadata(
        "https://github.com/o/r/blob/main/subtitles/anime_tv/New%20Anime/Season1/New.S01E01.srt",
        preserve_search_query=False,
    )

    assert manager.anime_folder_name == "New Anime"
    assert manager.get_display_anime_name() == "New Anime"
    assert config.get("LAST_REMOTE_SEARCH_QUERY") == "New Anime"


def test_remote_metadata_recovers_search_query_from_matching_cached_map(tmp_path):
    remote_path = "subtitles/anime_tv/Baki-Dou Part Two/Season1/Baki.S01E14.srt"
    cache_path = tmp_path / "github_search_Baki-Dou.json"
    cache_path.write_text(
        json.dumps(
            {
                "anime_query": "Baki-Dou",
                "items": [
                    {"name": "Baki.S01E01.srt", "path": "subtitles/anime_tv/Baki-Dou/Season1/Baki.S01E01.srt"},
                    {"name": "Baki.S01E14.srt", "path": remote_path},
                ],
            }
        ),
        encoding="utf-8",
    )

    manager = object.__new__(SubtitleManager)
    config = _DictConfig({"LAST_ANIME_NAME": "Baki-Dou Part Two"})
    manager.config = config
    manager._list_cached_github_search_records = lambda: [
        {"query": "Baki-Dou", "path": str(cache_path)},
    ]

    manager._extract_and_set_remote_episode_metadata(
        "https://github.com/o/r/blob/main/" + remote_path.replace(" ", "%20"),
        preserve_search_query=True,
    )

    assert manager.anime_folder_name == "Baki-Dou"
    assert manager.get_display_anime_name() == "Baki-Dou Part Two"
    assert config.get("LAST_REMOTE_SEARCH_QUERY") == "Baki-Dou"


def test_remote_url_sync_updates_display_name_without_changing_search_key():
    manager = object.__new__(SubtitleManager)
    config = _DictConfig(
        {
            "LAST_REMOTE_SEARCH_QUERY": "Baki-Dou",
            "LAST_ANIME_NAME": "Baki-Dou",
        }
    )
    manager.config = config
    manager.remote_flag = True
    manager.github_owner = "o"
    manager.github_repo = "r"
    manager.github_ref = "main"
    manager.anime_folder_name = "Baki-Dou"
    manager.remote_search_query = "Baki-Dou"
    manager.current_season = 2
    manager.current_episode = 1
    item = {
        "season": 2,
        "episode": 1,
        "global": 14,
        "path": "subtitles/anime_tv/Baki-Dou Part Two/Season1/Baki.S01E01.srt",
        "name": "Baki.S01E01.srt",
    }
    manager.remote_episode_map_global = {14: item}
    manager.remote_episode_map_season = defaultdict(list, {2: [item]})

    manager._sync_remote_url_to_current_episode(item)

    assert manager.anime_folder_name == "Baki-Dou"
    assert manager.remote_search_query == "Baki-Dou"
    assert manager.get_display_anime_name() == "Baki-Dou Part Two"
    assert config.get("LAST_DISPLAY_ANIME_NAME") == "Baki-Dou Part Two"


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


def test_strip_parenthetical_notes_preserves_source_ruby_when_auto_ruby_enabled():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": True,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u6f22\u5b57\uff08\u304b\u3093\u3058\uff09") == "\u6f22\u5b57\uff08\u304b\u3093\u3058\uff09"


def test_parenthetical_note_only_line_respects_setting():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": False,
        }
    )
    assert manager._clean_text("\uff08\u6b53\u58f0\uff09") == "\uff08\u6b53\u58f0\uff09"


def test_clean_text_preserves_source_ruby_when_auto_ruby_enabled():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": True,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )
    assert manager._clean_text("\u5927\u6728(\u304a\u304a\u304d)\u304c\u6765\u305f\uff08\u8db3\u97f3\uff09") == "\u5927\u6728(\u304a\u304a\u304d)\u304c\u6765\u305f"


def test_clean_text_strips_speaker_with_inner_source_ruby_parentheses():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": True,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
        }
    )

    assert manager._clean_text("\uff08\u771f\u6a39\u5b50(\u307e\u304d\u3053)\uff09\u3044\u306a\u3044\u306a\u3042") == "\u3044\u306a\u3044\u306a\u3042"


def test_parse_ruby_segments_mixes_source_and_auto_ruby():
    manager = _cleaner({"SUBTITLE_AUTO_RUBY": True})
    manager._auto_ruby_segments = lambda text: [("\u79d1", "\u304b"), ("\u5b66", "\u304c\u304f"), (" ", None)] if text == "\u79d1\u5b66 " else None
    manager._get_ruby_generator = lambda: None

    assert manager._parse_ruby_segments("\u79d1\u5b66 \u5927\u6728(\u304a\u304a\u304d)", allow_auto=True) == [
        ("\u79d1", "\u304b"),
        ("\u5b66", "\u304c\u304f"),
        (" ", None),
        ("\u5927\u6728", "\u304a\u304a\u304d"),
    ]


def test_parse_ruby_segments_default_keeps_source_compound_whole():
    manager = _cleaner({"SUBTITLE_AUTO_RUBY": True, "ANKI_SPLIT_KANJI_MORAS": False})
    manager._auto_ruby_segments = lambda _text: None
    manager._get_ruby_generator = lambda: None

    assert manager._parse_ruby_segments("\u4eba\u9593(\u306b\u3093\u3052\u3093)", allow_auto=True) == [
        ("\u4eba\u9593", "\u306b\u3093\u3052\u3093"),
    ]


def test_auto_ruby_allows_katakana_in_hiragana_line_without_kanji():
    manager = _cleaner({"SUBTITLE_AUTO_RUBY": True, "ANKI_SPLIT_KANJI_MORAS": False})
    manager._auto_ruby_lock = None
    manager._auto_ruby_cache = {}
    manager._ruby_stats = {
        "cache_hits": 0,
        "cache_misses": 0,
        "generator_calls": 0,
        "generator_time": 0.0,
    }

    class Generator:
        def segments(self, text, split_kanji_compounds=False):
            assert text == "\u305d\u3046\u304b \u3082\u306e\u3059\u3054\u3044\u30b9\u30d4\u30fc\u30c9\u3067"
            assert split_kanji_compounds is False
            return [
                ("\u305d\u3046\u304b \u3082\u306e\u3059\u3054\u3044", None),
                ("\u30b9\u30d4\u30fc\u30c9", "\u3059\u3074\u30fc\u3069"),
                ("\u3067", None),
            ]

    manager._get_ruby_generator = lambda: Generator()

    assert manager._parse_ruby_segments(
        "\u305d\u3046\u304b \u3082\u306e\u3059\u3054\u3044\u30b9\u30d4\u30fc\u30c9\u3067",
        allow_auto=True,
    ) == [
        ("\u305d\u3046\u304b \u3082\u306e\u3059\u3054\u3044", None),
        ("\u30b9\u30d4\u30fc\u30c9", "\u3059\u3074\u30fc\u3069"),
        ("\u3067", None),
    ]


def test_build_display_payload_skips_empty_cleaned_duplicate_timestamp():
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
            "DEFAULT_START_TIME": 0.0,
            "AUTO_RUBY_EAGER_WINDOW_SEC": 0.0,
        }
    )
    manager._ruby_stats = {"episode_load_times": []}
    manager.load_subtitles = lambda _path: [
        srt.Subtitle(
            index=98,
            start=datetime.timedelta(seconds=410.993),
            end=datetime.timedelta(seconds=414.080),
            content="\uff08\u65b0\u4e00\uff09\u30db\u30f3\u30c8\u306b \u3088\u304f\u6ce3\u304f\u4eba\u3060\u306a",
        ),
        srt.Subtitle(
            index=99,
            start=datetime.timedelta(seconds=410.993),
            end=datetime.timedelta(seconds=414.080),
            content="{\\an8}\uff08\u5b87\u7530\u306e\u6ce3\u304d\u58f0\uff09",
        ),
    ]

    payload = manager._build_subtitle_display_payload("dummy.srt", allow_eager_auto=False)

    assert payload["display_start_times"] == [410.993]
    assert payload["display_end_times"] == [414.080]
    assert payload["display_data"][0][0] == "\u30db\u30f3\u30c8\u306b \u3088\u304f\u6ce3\u304f\u4eba\u3060\u306a"


def test_set_display_data_redownloads_missing_remote_cache_file(tmp_path):
    manager = _cleaner(
        {
            "SUBTITLE_AUTO_RUBY": False,
            "SUBTITLE_SPEAKER_MODE": "hide",
            "SUBTITLE_STRIP_PAREN_NOTES": True,
            "DEFAULT_START_TIME": 0.0,
            "AUTO_RUBY_EAGER_WINDOW_SEC": 0.0,
        }
    )
    manager.remote_flag = True
    manager.github_owner = "owner"
    manager.github_repo = "repo"
    manager.github_ref = "main"
    manager.anime_folder_name = "MASHLE"
    manager.current_season = 1
    manager.current_episode = 5
    manager.is_movie = False
    manager._prepared_episode_cache = OrderedDict()
    manager._prepared_episode_lock = threading.Lock()
    manager._episode_prepare_seen = set()
    manager._ruby_stats = {"episode_load_times": []}
    manager._get_cache_base_dir = lambda: str(tmp_path / "cache_github")

    remote_path = "subtitles/anime_tv/MASHLE/Show.S01E05.ja.srt"
    item = {
        "season": 1,
        "episode": 5,
        "global": 5,
        "path": remote_path,
        "name": "Show.S01E05.ja.srt",
    }
    manager.remote_episode_map_global = {5: item}
    manager.remote_episode_map_season = defaultdict(list, {1: [item]})
    local_path = tmp_path / "cache_github" / "MASHLE" / "Season1" / "Show.S01E05.ja.srt"
    downloads = []

    def download(raw_url, path):
        downloads.append((raw_url, path))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("1\n00:00:01,000 --> 00:00:02,000\n\u3053\u3053\u306f\u30de\u30c3\u30b7\u30e5\n")

    manager._download_file = download

    manager.set_subtitle_display_data(str(local_path))

    assert local_path.exists()
    assert downloads == [("https://raw.githubusercontent.com/owner/repo/main/" + remote_path, str(local_path))]
    assert manager.srt_file == str(local_path)
    assert manager.display_data[0][0] == "\u3053\u3053\u306f\u30de\u30c3\u30b7\u30e5"


def test_candidate_geometry_lines_keep_source_subtitle_lines_separate():
    manager = _cleaner({"SUBTITLE_GEOMETRY_CANDIDATE_LINES": 32})
    top = [("\u305d\u308c\u306f", None)]
    bottom = [
        ("\u5e38", "\u3064\u306d"),
        ("\u306b\u65b0\u3057\u3044\u8840\u6db2\u304c\u6d41\u308c\u3066\u304f\u308b\u5834\u6240", None),
    ]
    manager.display_data = [("dummy", 1.0, top, bottom)]

    candidates = list(manager._candidate_geometry_lines())

    assert top in candidates
    assert bottom in candidates
    assert top + [(" ", None)] + bottom not in candidates


def test_ensure_auto_ruby_invalidates_geometry_cache():
    manager = _cleaner({"SUBTITLE_AUTO_RUBY": True})
    manager.display_data = [("\u6f22\u5b57", 1.0, [], [("\u6f22\u5b57", None)])]
    manager._auto_ruby_ready_indices = set()
    manager._geometry_cache = {"old": (100, 80)}
    manager._parse_ruby_segments = lambda _line, allow_auto=True: [("\u6f22\u5b57", "\u304b\u3093\u3058")]

    manager.ensure_auto_ruby_for_index(0)

    assert manager._geometry_cache == {}
    assert manager.display_data[0][3] == [("\u6f22\u5b57", "\u304b\u3093\u3058")]


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
