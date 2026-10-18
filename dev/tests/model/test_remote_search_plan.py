import pytest

from SubtitlePlayer.model.remote_search_plan import (
    RemoteSearchProfileError,
    build_remote_search_query_plan,
    describe_remote_search_query_plan,
    normalize_remote_search_profiles,
    remote_search_plan_fingerprint,
    remote_search_profiles_to_config,
)
from SubtitlePlayer.model.subtitle_manager import SubtitleManager


def test_default_query_plan_includes_hulu_and_season_one_unseasoned_fallback():
    plan = build_remote_search_query_plan("Example Show", 1)

    assert [query.profile_id for query in plan] == ["netflix", "amazon", "hulu", "unseasoned"]
    assert plan[2].phrase == "Example Show s01 Hulu"
    assert plan[-1].is_seasonal is False


def test_custom_profiles_keep_config_order_and_optional_fallbacks():
    profiles = [
        {"id": "crunchyroll", "label": "Crunchyroll", "enabled": True, "template": "{show} Season {season}"},
        {"id": "disabled", "label": "Disabled", "enabled": False, "template": "{show} s{season02} Disabled"},
    ]

    plan = build_remote_search_query_plan(
        "My Show",
        3,
        profiles,
        generic_enabled=True,
        unseasoned_enabled=True,
    )

    assert [(query.profile_id, query.rank, query.phrase) for query in plan] == [
        ("crunchyroll", 0, "My Show Season 3"),
        ("generic-season", 2, "My Show s03"),
    ]
    assert all(query.profile_id != "unseasoned" for query in plan)


@pytest.mark.parametrize(
    "template",
    [
        "{show} repo:other/repo",
        '"{show}"',
        "{unknown}",
        "{show!r}",
        "{show:{season}}",
        "{show",
    ],
)
def test_unsafe_or_malformed_templates_are_rejected(template):
    with pytest.raises(RemoteSearchProfileError):
        normalize_remote_search_profiles(
            [{"id": "unsafe", "label": "Unsafe", "enabled": True, "template": template}]
        )


def test_explicit_empty_profile_list_disables_provider_queries_but_preserves_selected_fallbacks():
    plan = build_remote_search_query_plan(
        "No Providers",
        2,
        [],
        generic_enabled=True,
        unseasoned_enabled=False,
    )

    assert [query.profile_id for query in plan] == ["generic-season"]


def test_profile_fingerprint_is_canonical_and_tracks_behavior_changes():
    profiles = [{"id": "netflix", "label": "Netflix", "enabled": True, "template": "{show} s{season02} Netflix"}]

    first = remote_search_plan_fingerprint(profiles, generic_enabled=False, unseasoned_enabled=True)
    equivalent = remote_search_plan_fingerprint(remote_search_profiles_to_config(profiles), generic_enabled=False, unseasoned_enabled=True)
    changed = remote_search_plan_fingerprint(profiles, generic_enabled=True, unseasoned_enabled=True)

    assert first == equivalent
    assert first != changed


def test_plan_description_is_gui_ready_without_github_operators():
    description = describe_remote_search_query_plan(build_remote_search_query_plan("Show", 2))

    assert description[0] == "Netflix (provider, priority 1): Show s02 Netflix"
    assert all("repo:" not in line and "path:" not in line for line in description)


def test_explicit_provider_rank_is_used_after_subtitle_quality_ties():
    manager = object.__new__(SubtitleManager)
    manager.all_results_items = [
        {
            "name": "Show.S01E01.ja.srt",
            "path": "subtitles/show/amazon/Show.S01E01.ja.srt",
            "season": 1,
            "episode": 1,
            "global": 1,
            "search_provider_id": "amazon",
            "search_provider_label": "Amazon",
            "search_provider_rank": 1,
        },
        {
            "name": "Show.S01E01.ja.srt",
            "path": "subtitles/show/netflix/Show.S01E01.ja.srt",
            "season": 1,
            "episode": 1,
            "global": 1,
            "search_provider_id": "netflix",
            "search_provider_label": "Netflix",
            "search_provider_rank": 0,
        },
    ]

    manager.build_remote_episode_maps()

    assert manager.remote_episode_map_global[1]["search_provider_id"] == "netflix"
    assert manager.remote_episode_map_global[1]["path"].endswith("/netflix/Show.S01E01.ja.srt")
