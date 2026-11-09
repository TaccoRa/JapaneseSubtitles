"""Shared controls, lexical components, asynchronous lookup, and episode ownership."""
from types import SimpleNamespace as NS
import tkinter as tk

import pytest

from model.config_manager import ConfigManager
from model.control_actions import action_catalog, keyboard_bindings, binding_conflicts
from model.anki_client import AnkiClient
from model.cue_snapshot import CueSnapshotBuilder
from model.renderer import SubtitleRenderer
from model.browser_protocol import MediaIdentity
from controller.browser_sync_controller import BrowserSyncController
from controller.hotkey_controller import HotkeyController


def test_new_bindings_are_independent_and_parameter_commands_remain_voice_only():
    values = {'VOICE_ENABLED': False, 'CONTROL_KEY_BINDINGS': {
        'copy_all': 'ctrl+alt+c', 'submark': 's', 'go_to_time': 'g', 'play': 'x'}}
    assert keyboard_bindings(NS(get=values.get)) == [('voice_copy_all', 'ctrl+alt+c')]
    assert {a.id for a in action_catalog() if a.parameters} >= {'submark', 'go_to_time', 'highlight_indexed'}
    assert not keyboard_bindings(ConfigManager('config.json'))


def test_contextual_conflict_validation():
    assert not binding_conflicts({'SHORTCUT_TOGGLE_PLAY': 'x', 'SHORTCUT_MODE2_TOGGLE_PLAY': 'x'})
    assert binding_conflicts({'SHORTCUT_TOGGLE_PLAY': 'ctrl+x', 'CONTROL_KEY_BINDINGS': {'copy_all': 'x+control'}})
    assert binding_conflicts({'CONTROL_KEY_BINDINGS': {'copy_all': '5'}})


def renderer_for(client, text):
    cue = CueSnapshotBuilder(client.word_spans).build(cue_index=1, start_time=0, end_time=3,
        clean_text=text, top_segments=[], bottom_segments=[(text, None)])
    renderer = object.__new__(SubtitleRenderer)
    renderer._cue_snapshot = cue
    renderer._voice_selected_word_keys = []
    renderer._word_regions = [dict(token.as_mapping(), base=token.surface, char_start=token.start,
        char_end=token.end, bbox=(token.start*20, 0, token.end*20, 30),
        line_position='second', sentence_lookup=text) for token in cue.tokens]
    renderer.canvas = NS(delete=lambda *a: None, create_rectangle=lambda *a, **k: None, tag_raise=lambda *a: None)
    renderer._show_hover_text = lambda *args: None
    return renderer


def test_compound_numbering_and_remembered_submark_context():
    client = AnkiClient(ConfigManager('config.json'))
    renderer = renderer_for(client, '海賊はな 化かし合いだぜ.')
    assert renderer.subtitle_words_for_anki() == ['海賊', 'は', 'な', '化かし合い', 'だ', 'ぜ']
    assert renderer.highlight_subtitle_words_for_voice([4])['ok']
    assert renderer.submark_for_voice([2])['ok']
    assert renderer.highlighted_subtitle_selection_for_anki()['text'] == '合い'
    assert renderer.submark_for_voice([1])['ok']
    assert renderer.highlighted_subtitle_selection_for_anki()['text'] == '化かし'
    assert not renderer.submark_for_voice([4, 3])['ok']
    renderer._cue_snapshot = None
    renderer._word_regions = []
    assert not renderer.submark_for_voice([1])['ok']
    client.close()


def test_submark_all_kanji_word_and_explicit_parent():
    client = AnkiClient(ConfigManager('config.json'))
    renderer = renderer_for(client, '化学')
    assert renderer.submark_for_voice([1, 2])['ok']
    assert renderer.highlighted_subtitle_selection_for_anki()['text'] == '学'
    client.close()


def test_proven_lexical_expression_does_not_duplicate_its_component_word_numbers():
    client = AnkiClient(ConfigManager('config.json'))
    renderer = renderer_for(client, '性格悪い')
    assert renderer.subtitle_words_for_anki() == ['性格悪い']
    assert renderer.submark_for_voice([1, 2])['ok']
    assert renderer.highlighted_subtitle_selection_for_anki()['text'] == '格'
    client.close()


def test_mark_all_and_copy_all_use_japanese_sentence_and_selection():
    client = AnkiClient(ConfigManager('config.json'))
    renderer = renderer_for(client, '海賊はな 化かし合いだぜ.')
    statuses, copies = [], []
    hotkeys = HotkeyController(NS(renderer=renderer, config=NS(get=lambda key: False),
        _report_voice_status=lambda *args: statuses.append(args)))
    for name, callback in {'_modal_input_blocked': lambda: False,
        '_take_frozen_voice_target': lambda action: (action, None),
        '_copy_text_for_voice': lambda text: copies.append(text) or True,
        '_highlighted_anki_payload': renderer.highlighted_subtitle_selection_for_anki}.items():
        object.__setattr__(hotkeys, name, callback)
    hotkeys._dispatch_input_action('voice_copy_all', source='voice')
    assert copies[-1] == '海賊はな 化かし合いだぜ.'
    hotkeys._dispatch_input_action('voice_highlight_all', source='voice')
    assert renderer.highlighted_subtitle_selection_for_anki()['words'] == ['海賊', 'は', 'な', '化かし合い', 'だ', 'ぜ']
    renderer.highlight_subtitle_words_for_voice([4])
    hotkeys._dispatch_input_action('voice_copy_all', source='voice')
    assert copies[-1] == '化かし合い'
    client.close()


@pytest.mark.parametrize('popup', [False, True])
@pytest.mark.parametrize('value,error,expected', [('', False, 'No dictionary entry found'),
    ('definition', False, 'definition'), ('', True, 'Dictionary lookup failed')])
def test_dictionary_feedback_is_async_and_rejects_stale_targets(monkeypatch, value, error, expected, popup):
    import model.dictionary_feedback as module
    workers, finishes, rendered = [], [], []
    monkeypatch.setattr(module.threading, 'Thread', lambda target, **kwargs: NS(start=lambda: workers.append(target)))
    monkeypatch.setattr(module, 'dispatch_to_tk', lambda widget, finish: finishes.append(finish))
    def lookup(query):
        if error:
            raise RuntimeError('offline')
        return value
    owner = NS(_dictionary_lookup=lookup, _hover_active_region={'base': 'word'} if popup else 'current', _cue_snapshot=object(),
        canvas=object(), _show_hover_layers=lambda *args: rendered.append(args))
    region = dict(base='化かし合い', lookup='化かし合い')
    assert 'Looking up' in module.dictionary_layer_text(owner, region, ('dictionary_together',))
    assert workers and not rendered
    workers.pop()(); finishes.pop()()
    assert rendered
    assert expected in module.dictionary_layer_text(owner, region, ('dictionary_together',))
    region['lookup'] = '別'
    module.dictionary_layer_text(owner, region, ('dictionary',))
    owner._hover_active_region = 'another subtitle'
    rendered.clear(); workers.pop()(); finishes.pop()()
    assert not rendered


def test_former_voice_only_keyboard_action_uses_the_same_frozen_target_without_listening():
    owner = NS(config=NS(get=lambda key: False), _active_voice_target=None)
    hotkeys = HotkeyController(owner)
    frozen = dict(text='化かし合い', source='highlight', ok=True)
    seen = []
    for name, callback in {'_global_focus_stamp': lambda: ('allowed', 1),
        '_modal_input_blocked': lambda: False, '_take_frozen_voice_target': lambda action: (action, None),
        '_freeze_voice_target': lambda: frozen,
        '_show_voice_lookup_layers': lambda *args, **kwargs: seen.append(owner._active_voice_target)}.items():
        object.__setattr__(hotkeys, name, callback)
    hotkeys._dispatch_input_action('voice_dictionary', source='physical')
    assert seen == [frozen] and owner._active_voice_target is None


def test_spoken_hover_control_toggles_without_changing_keyboard_hold_settings():
    owner = NS(config=NS(get=lambda key: False), _voice_hover_mode=None,
               _report_voice_status=lambda *args: None)
    hotkeys = HotkeyController(owner)
    object.__setattr__(hotkeys, '_modal_input_blocked', lambda: False)
    hotkeys._dispatch_input_action('hover_toggle:dictionary', source='voice')
    assert owner._voice_hover_mode == 'dictionary' and owner._hover_refresh_requested
    hotkeys._dispatch_input_action('hover_toggle:dictionary', source='voice')
    assert owner._voice_hover_mode is None


def test_disabling_subtitle_keyboard_binding_does_not_disable_its_voice_action():
    toggled = []
    owner = NS(subtitle_navigation=NS(toggle_subtitle_visibility=lambda: toggled.append(True)))
    hotkeys = HotkeyController(owner)
    for name, callback in {'_modal_input_blocked': lambda: False,
        '_take_frozen_voice_target': lambda action: (action, None),
        '_hotkey_action_disabled': lambda action: True,
        '_sync_control_window_for_subtitle_visibility': lambda: None}.items():
        object.__setattr__(hotkeys, name, callback)
    hotkeys._dispatch_input_action('toggle_subtitles', source='voice')
    assert toggled == [True]


def test_dictionary_results_retain_other_sections():
    from model.dictionary_feedback import retain_voice_results
    owner = NS(_hover_active_mode='voice_highlight', _cue_snapshot=object())
    region = dict(base='化かし合い', char_start=5, char_end=10)
    retain_voice_results(owner, region, [('translation', 'translation result')])
    result = dict(retain_voice_results(owner, region, [('dictionary', 'No dictionary entry found')]))
    assert result['translation'] == 'translation result'
    assert result['action'] == 'Done'


def navigation_browser():
    sync = object.__new__(BrowserSyncController)
    sync.identity = MediaIdentity(1, 'document', 0, 'video')
    sync.snapshot = NS(identity=sync.identity, fingerprint='old-media', resource_fingerprint='old-resource')
    sync._capture = None
    sync._last_episode_context = dict(episode=7, season=1)
    sync._media_context = {}
    sync.sent, sync.errors, sync.timers = [], [], []
    sync.bridge = NS(send=sync.sent.append)
    sync.report_error = sync.errors.append
    sync._set_status = lambda *args: None
    sync.controller = NS(settings=NS(root=NS(after=lambda delay, callback: sync.timers.append((delay, callback)))))
    return sync


def test_episode_navigation_requires_both_video_and_episode_confirmation():
    sync = navigation_browser()
    assert sync.navigate_episode('next')
    assert not sync.navigate_episode('next')
    assert not sync._episode_navigation_confirmed(dict(episode=8, season=1))
    sync.snapshot.fingerprint = 'new-media'
    assert not sync._episode_navigation_confirmed(dict(episode=8, season=1))  # URL change alone is insufficient.
    sync.snapshot.resource_fingerprint = 'new-resource'
    assert not sync._episode_navigation_confirmed(dict(episode=7, season=1))
    assert sync._episode_navigation_confirmed(dict(episode=8, season=1))
    assert sync._episode_navigation is None


def test_episode_navigation_timeout_and_recording_rejection():
    sync = navigation_browser()
    sync._capture = object()
    assert not sync.navigate_episode('next') and not sync.sent
    sync._capture = None
    assert sync.navigate_episode('previous')
    assert sync.timers[0][0] == 15000
    sync.timers[0][1]()
    assert sync._episode_navigation is None
    assert 'not confirmed' in sync.errors[-1]


def test_netflix_seamless_player_can_confirm_new_episode_without_replacing_the_blob():
    sync = navigation_browser()
    sync.snapshot.player_fingerprint = 'netflix-episode-seven'
    assert sync.navigate_episode('next')
    sync.snapshot.player_fingerprint = 'netflix-episode-eight'
    assert sync._episode_navigation_confirmed(dict(episode=8, season=1))


def test_automatic_episode_navigation_starts_playback_only_after_confirmation():
    sync = navigation_browser()
    commands = []
    sync.command = lambda action: commands.append(action)
    assert sync.navigate_episode('next', autoplay=True)
    assert not commands
    sync.snapshot.resource_fingerprint = 'next-resource'
    assert sync._episode_navigation_confirmed(dict(episode=8, season=1))
    assert commands == ['play']


def automation_browser(values=None):
    from controller.episode_automation import EpisodeAutomation
    sync = navigation_browser()
    config = dict(values or {})
    sync.following = True
    sync.snapshot.current_time, sync.snapshot.duration = 30, 1400
    sync.snapshot.paused = sync.snapshot.seeking = sync.snapshot.buffering = False
    sync._last_episode_context = dict(seriesKey='one-piece', episode=7, season=1)
    sync.controller.config = NS(get=config.get, set=lambda key, value: config.update({key: value}))
    sync.commands = []
    sync.command = lambda action, **kwargs: sync.commands.append((action, kwargs)) or 'seek'
    return sync, EpisodeAutomation(sync), config


def test_manual_intro_skip_works_with_both_automations_disabled():
    sync, automation, config = automation_browser()
    automation.tick('ended')
    assert not sync.sent
    assert automation.skip_intro(manual=True)
    assert sync.sent[-1]['type'] == 'SkipIntro'
    automation.ack(dict(operationId=sync.sent[-1]['operationId'], ok=True))
    assert automation.skipped is None  # A click acknowledgement alone is insufficient.
    sync.snapshot.current_time = 120
    automation.tick()
    assert automation.skipped == automation.media_key()


def test_auto_intro_is_once_per_media_and_respects_recording():
    sync, automation, config = automation_browser({'AUTO_SKIP_INTRO': True})
    sync._capture = object(); automation.tick()
    assert not sync.sent
    sync._capture = None; automation.tick()
    operation = sync.sent[-1]['operationId']
    automation.ack(dict(operationId=operation, ok=True))
    sync.snapshot.current_time = 120
    automation.tick()
    automation.last_probe = 0; automation.tick()
    assert len(sync.sent) == 1
    sync.snapshot.fingerprint = 'next episode'
    automation.last_probe = 0; automation.tick()
    assert len(sync.sent) == 2


def test_saved_intro_title_marker_is_per_series_and_fallback_is_verified():
    sync, automation, config = automation_browser()
    sync.snapshot.current_time = 300
    assert '300.0' in automation.marker('remember')
    assert config['INTRO_SKIP_TARGETS'] == {'one-piece': 300}
    sync.snapshot.current_time = 30
    automation.skip_intro()
    automation.ack(dict(operationId=sync.sent[-1]['operationId'], ok=False))
    assert sync.commands[0][0] == 'seek' and sync.commands[0][1]['time'] == 300
    sync.commands[0][1]['on_complete'](True)
    assert automation.skipped == automation.media_key()
    sync._last_episode_context['seriesKey'] = 'another show'
    assert 'website Skip Intro button only' in automation.marker()


def test_intro_ack_for_replaced_video_cannot_seek_new_episode():
    sync, automation, config = automation_browser({'INTRO_SKIP_TARGETS': {'one-piece': 300}})
    automation.skip_intro(); operation = sync.sent[-1]['operationId']
    sync.snapshot.fingerprint = 'new episode'
    automation.ack(dict(operationId=operation, ok=False))
    assert not sync.commands


def test_auto_next_only_on_ended_and_once_per_media():
    sync, automation, config = automation_browser({'AUTO_NEXT_EPISODE': True})
    calls = []
    sync.navigate_episode = lambda direction, **kwargs: calls.append((direction, kwargs))
    automation.tick('pause'); assert not calls
    automation.tick('ended'); automation.tick('ended')
    assert calls == [('next', {'autoplay': True})]


def test_automation_identity_tracks_seamless_netflix_player_changes():
    sync, automation, config = automation_browser()
    sync.snapshot.player_fingerprint = 'episode-seven'
    before = automation.media_key()
    sync.snapshot.player_fingerprint = 'episode-eight'
    assert automation.media_key() != before


def test_spoken_submark_and_intro_commands_share_action_dispatch():
    from model.voice_commands import build_voice_phrase_map
    commands = {action.id: dict(enabled=action.id in {'submark', 'skip_intro'}, en=[], de=[]) for action in action_catalog()}
    commands['submark'] = dict(enabled=True, en=['submark'], de=['teil markieren'])
    commands['skip_intro'] = dict(enabled=True, en=['skip intro'], de=['intro überspringen'])
    phrases, _ = build_voice_phrase_map('en', commands, {}, require_prefix=False)
    assert phrases['submark seven two'] == 'voice_submark:7:2'
    assert phrases['submark 2'] == 'voice_submark:2'
    assert phrases['skip intro'] == 'skip_intro'


def test_lazy_settings_views_share_variables_and_preserve_unopened_values(monkeypatch):
    from view.settings_ui import SettingsUI
    root = tk.Tk(); root.withdraw()
    try:
        ui = SettingsUI(root, ConfigManager('config.json'), 100, start_hidden=True)
        monkeypatch.setattr('view.voice_tab.VoiceTab.refresh_devices', lambda self: None)
        ui.adv_settings._open_advanced_settings_window()
        voice, controls = ui._voice_views.values()
        assert not voice._built and not controls._built
        assert voice._command_vars is controls._command_vars
        assert voice.enabled_var is controls.enabled_var
        voice._command_vars['copy_all']['en'].set('copy everything')
        assert controls._command_vars['copy_all']['en'].get() == 'copy everything'
        ui.adv_settings._ensure_advanced_tab_built(ui._controls_tab_id)
        assert controls._built and not voice._built
        controls._binding_vars['copy_all'].set('ctrl+alt+c')
        controls.enabled_var.set(False)
        collected = ui.adv_settings._collect_advanced_values()
        assert not collected.get('errors')
        assert collected['values']['CONTROL_KEY_BINDINGS']['copy_all'] == 'ctrl+alt+c'
        assert collected['values']['VOICE_ENABLED'] is False
        assert collected['values']['SHORTCUT_TOGGLE_PLAY'] == 'space'
        assert 'HOVER_DICTIONARY_HOTKEY' in collected['values']
        ui.adv_settings._ensure_advanced_tab_built(ui._voice_tab_id)
        assert voice._command_vars['copy_all']['en'].get() == 'copy everything'
        controls.reset_defaults()
        assert voice._binding_vars['copy_all'].get() == ''
        ui.adv_settings._load_advanced_values_into_vars()
        assert voice._command_vars['copy_all']['en'].get() == 'copy all'
    finally:
        root.destroy()


def test_real_controller_startup_binds_intro_callback_before_browser_exists(monkeypatch):
    """Exercise the actual app construction order without external input/network."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    # Windows Tcl's process-wide loader state is unreliable after a previous
    # full settings window has been destroyed. Exercise startup in a fresh process.
    if os.environ.get('SUBTITLE_ISOLATED_STARTUP_TEST') != '1':
        result = subprocess.run([sys.executable, '-m', 'pytest', str(Path(__file__).resolve()),
            '-k', 'real_controller_startup', '-q', '--basetemp=' + str(Path.cwd() / 'startup-child-temp')],
            env=dict(os.environ, SUBTITLE_ISOLATED_STARTUP_TEST='1'), capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return
    from app import SubtitlePlayerApp
    from controller.controller import SubtitleController
    from controller.anki_controller import AnkiController
    from model.voice_service import VoiceCommandService
    from model.browser_bridge import BrowserBridge
    from model.subtitle_manager import SubtitleManager

    monkeypatch.setattr(SubtitleController, '_start_input_listeners', lambda self: None)
    monkeypatch.setattr(SubtitleController, '_publish_anki_add_counts', lambda self: None)
    monkeypatch.setattr(AnkiController, 'start_anki_with_app_if_enabled', lambda self: None)
    monkeypatch.setattr(VoiceCommandService, 'start', lambda self: None)
    monkeypatch.setattr(BrowserBridge, 'start', lambda self: None)
    monkeypatch.setattr(BrowserSyncController, '_poll', lambda self: None)
    player = SubtitlePlayerApp()
    player.config = ConfigManager('config.json')
    player.config.set_many({'LAST_LOCAL_SRT_FILE': '', 'SUBTITLE_AUTO_RUBY': False, 'ANNOTATION_ENABLED': False})
    try:
        player._build_root()
        player.sub_manager = SubtitleManager(player.config)
        player.total_duration = player.sub_manager.get_total_duration()
        player._build_ui()
        player._build_renderer()
        player._build_controller()
        assert player.controller.browser_sync.episode_automation is not None
        assert 'Connect a video' in player.settings_ui._on_intro_target('status')
    finally:
        if player.controller is not None:
            player.controller.shutdown(save_state=False)
        elif player.root is not None:
            player.root.destroy()
