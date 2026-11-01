"""A delayed browser handshake must finish before the generic source chooser."""
from types import SimpleNamespace as NS

import pytest

import app as app_module
from app import SubtitlePlayerApp
from controller.browser_sync_controller import BrowserSyncController


@pytest.fixture
def startup(monkeypatch):
    now, scheduled, choices, resolved = [0.0], [], [], []
    monkeypatch.setattr(app_module.time, 'monotonic', lambda: now[0])
    player = SubtitlePlayerApp()
    player.root = NS(after=lambda delay, callback: scheduled.append((delay, callback)))
    player.sub_manager = NS(startup_needs_source=True, is_search_dialog_active=lambda: False)
    browser = NS(identity=None, following=True, _connected=False, _media_context={},
                 _last_episode_context={}, _source_selection_request=None)
    def resolve():
        resolved.append(True)
        browser._source_selection_request = ('automatic-lookup',)
    def choose():
        choices.append(True)
        player.sub_manager._startup_source_choice_handled = True
    browser.resolve_browser_source = resolve
    browser.refresh_browser_source = lambda: pytest.fail('Startup must not force a saved-map refresh')
    player.controller = NS(browser_sync=browser, episode_controller=NS(on_open_srt=choose))
    player._startup_browser_deadline, player._startup_browser_identity = 15, None
    return NS(player=player, browser=browser, now=now, scheduled=scheduled, choices=choices, resolved=resolved)


def test_six_second_browser_connection_wins_before_source_prompt(startup):
    state = startup
    for moment in (0, 4.1, 5.9):
        state.now[0] = moment
        state.player._choose_startup_source()
    assert not state.choices
    state.now[0] = 6
    state.browser.identity, state.browser._connected = 'browser-video', True
    state.player._choose_startup_source()  # The video is known, title still loading.
    assert not state.choices and not state.resolved
    state.now[0] = 8
    state.browser._media_context = dict(episode=1, season=1)
    state.player._choose_startup_source()
    assert state.resolved == [True] and not state.choices


def test_slow_automatic_lookup_never_stacks_a_generic_prompt(startup):
    state = startup
    state.browser.identity, state.browser._connected = 'browser-video', True
    state.browser._last_episode_context = dict(episode=1)
    state.player._choose_startup_source()
    for moment in (20, 40, 90):
        state.now[0] = moment
        state.player._choose_startup_source()
    assert state.resolved == [True] and not state.choices
    state.player.sub_manager.startup_needs_source = False
    before = len(state.scheduled)
    state.player._choose_startup_source()
    assert len(state.scheduled) == before


def test_connection_near_deadline_gets_its_own_metadata_grace_period(startup):
    state = startup
    state.now[0] = 14
    state.browser.identity, state.browser._connected = 'browser-video', True
    state.player._choose_startup_source()
    state.now[0] = 20
    state.player._choose_startup_source()
    assert not state.choices
    state.now[0] = 25
    state.player._choose_startup_source()
    assert state.choices == [True]


def test_no_browser_eventually_offers_one_manual_choice(startup):
    state = startup
    state.now[0] = 16
    state.player._choose_startup_source()
    state.player._choose_startup_source()
    assert state.choices == [True] and not state.resolved


@pytest.mark.parametrize('user_owned', ['dialog', 'handled', 'closing'])
def test_startup_does_not_interrupt_an_explicit_source_choice(startup, user_owned):
    state = startup
    state.now[0] = 60
    if user_owned == 'dialog':
        state.player.sub_manager.is_search_dialog_active = lambda: True
    elif user_owned == 'handled':
        state.player.sub_manager._startup_source_choice_handled = True
    else:
        state.player._closing = True
    state.player._choose_startup_source()
    assert not state.choices and not state.resolved and not state.scheduled


def test_source_resolution_reuses_cached_map_until_user_requests_refresh():
    sync = object.__new__(BrowserSyncController)
    sync.identity, sync.following = 'browser-video', True
    sync._last_episode_context = dict(episode=2, season=1, seriesKey='series')
    sync._media_context = {}
    sync._source_selection_request = None
    sync._refreshing_source_request = None
    queued, statuses = [], []
    def queue(event, episode, season, **options):
        queued.append((episode, season, options))
        sync._source_selection_request = ('queued',)
    sync._queue_browser_selection = queue
    sync._set_status = statuses.append
    assert sync.resolve_browser_source()
    assert sync.resolve_browser_source()
    assert queued == [(2, 1, {'refresh': False})] and not statuses
    assert sync.refresh_browser_source()
    assert queued[-1] == (2, 1, {'refresh': True}) and len(queued) == 2
    assert statuses == ['Refreshing saved subtitle map in background']
