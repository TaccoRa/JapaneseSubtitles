from types import SimpleNamespace

from controller.browser_sync_controller import BrowserSyncController
from controller.episode_controller import EpisodeController


def setup_sync():
    sync = object.__new__(BrowserSyncController)
    sync._browser_episode = None
    sync._episode_problem = None
    sync._reference_fingerprint = None
    sync._reference = ((1, 2),)
    sync._reference_kind = "full"
    calls = []
    state = {"season": 1, "episode": 5}
    manager = SimpleNamespace(get_anime_name=lambda: "Kimetsu no Yaiba",
                              get_current_season=lambda: state["season"])
    def change(action, episode, season):
        calls.append((action, episode, season))
        state.update(episode=episode, season=season or 1)
        return state["season"], episode
    manager.change_episode = change
    navigation = SimpleNamespace(
        _requested_episode_is_current=lambda e, s: state["episode"] == e and (s is None or state["season"] == s),
        save_current_episode_position=lambda: calls.append("save"),
        _after_episode_change=lambda: calls.append("refresh"))
    sync.controller = SimpleNamespace(sub_manager=manager, episode_controller=navigation)
    sync.invalidate = lambda: calls.append("invalidate")
    sync._set_status = lambda value: calls.append(value)
    return sync, state, calls


def context(episode, **extra):
    return dict(seriesKey="same-series", episode=episode, season=None, scope="season",
                title="Watch Demon Slayer: Kimetsu no Yaiba · Miruro") | extra


def test_browser_episode_follows_matching_series_once_and_clears_old_timing():
    sync, state, calls = setup_sync()
    sync._follow_episode(context(5))
    assert not calls
    sync._follow_episode(context(6))
    assert state["episode"] == 6
    assert calls[:4] == ["invalidate", "save", ("set", 6, 1), "refresh"]
    assert sync._reference == () and sync._reference_kind == "none"
    before = list(calls)
    sync._follow_episode(context(6))
    assert calls == before


def test_initial_connection_corrects_episode_number_for_the_matching_anime():
    sync, state, calls = setup_sync()
    sync._follow_episode(context(6))
    assert state["episode"] == 6
    assert ("set", 6, 1) in calls


def test_unrelated_anime_or_series_does_not_silently_select_wrong_subtitles():
    sync, state, calls = setup_sync()
    sync._follow_episode(context(6, title="Watch another anime"))
    assert state["episode"] == 5 and not calls
    assert sync._episode_problem
    sync._follow_episode(context(5))
    sync._follow_episode(context(1, seriesKey="new-series"))
    assert state["episode"] == 5 and not calls
    assert "New browser series" in sync._episode_problem


def test_loading_browser_episode_never_seeks_to_the_desktop_resume_position():
    calls = []
    browser = SimpleNamespace(following=True, snapshot=SimpleNamespace(time_at=lambda: 18.5),
                              invalidate=lambda: calls.append("invalidate"))
    manager = SimpleNamespace(get_total_duration=lambda: 1440, get_current_season=lambda: 1,
                              get_current_episode=lambda: 6, get_anime_name=lambda: "Kimetsu no Yaiba",
                              schedule_episode_preload_around_current=lambda: calls.append("prefetch"))
    controller = SimpleNamespace(browser_sync=browser, sub_manager=manager,
        settings=SimpleNamespace(episode_var=SimpleNamespace(set=lambda value: None),
                                 set_total_duration=lambda value: None, root=SimpleNamespace(title=lambda value: None)),
        playback=SimpleNamespace(set_current_time=lambda value: calls.append(("unexpected-seek", value))),
        ocr_controller=SimpleNamespace(_schedule_ocr_time_jump=lambda value: calls.append("unexpected-ocr")))
    navigation = EpisodeController(controller)
    # This controller forwards normal assignments to its parent. Override these
    # collaborators on the instance so the real _after_episode_change runs.
    object.__setattr__(navigation, "_current_episode_label", lambda: "6")
    object.__setattr__(navigation, "update_episode_nav_controls", lambda: None)
    object.__setattr__(navigation, "update_max_width", lambda: None)
    object.__setattr__(navigation, "remember_current_anime_episode", lambda: calls.append("remember"))
    navigation._after_episode_change()
    assert calls == ["invalidate", "remember", "prefetch"]
    assert controller.current_time == 18.5
