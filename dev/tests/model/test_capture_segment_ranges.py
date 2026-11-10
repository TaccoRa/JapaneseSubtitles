from types import SimpleNamespace as NS

import pytest

from model.sentence_context import segment_range_for_cue, sentence_for_cue
from model.anki_client import AnkiClient


@pytest.mark.parametrize('digit', range(3, 8))
@pytest.mark.parametrize('numpad', [False, True])
def test_unfocused_hover_range_keys_freeze_pointer_and_fire_once(monkeypatch, digit, numpad):
    import ctypes
    from pynput.keyboard import KeyCode
    from controller.hotkey_controller import HotkeyController, _NumpadDigitKey
    owner = NS(config=NS(get=lambda key: False), shift_pressed=False, alt_pressed=False, ctrl_pressed=False)
    hotkeys = HotkeyController(owner)
    queued = []
    def cursor(point):
        point._obj.x, point._obj.y = 123, 456
        return 1
    monkeypatch.setattr(ctypes.windll.user32, 'GetCursorPos', cursor)
    for name, callback in {
        '_global_focus_stamp': lambda: None,
        '_modal_input_blocked': lambda: False,
        '_point_inside_subtitle_window': lambda x, y: (x, y) == (123, 456),
        '_point_inside_popup_window': lambda x, y: False,
        '_enqueue_input_action': lambda action, **kwargs: queued.append((action, kwargs)),
    }.items():
        object.__setattr__(hotkeys, name, callback)
    key = _NumpadDigitKey(digit) if numpad else KeyCode.from_char(str(digit))
    hotkeys._on_key_press(key)
    hotkeys._on_key_press(key)
    assert queued == [(f'capture_segments:{digit}', dict(source='hover_keyboard', pointer=(123, 456)))]
    object.__setattr__(hotkeys, '_point_inside_subtitle_window', lambda *args: False)
    assert not hotkeys._queue_unfocused_hover_capture(key)


def test_unfocused_hover_capture_does_not_fall_back_to_previous_card():
    from controller.hotkey_controller import HotkeyController
    hotkeys = HotkeyController(NS(config=NS(get=lambda key: False)))
    object.__setattr__(hotkeys, '_modal_input_blocked', lambda: False)
    object.__setattr__(hotkeys, '_recording_word_at_pointer', lambda: {})
    object.__setattr__(hotkeys, '_trigger_capture_only_hotkey', lambda **kwargs: pytest.fail('No hovered word'))
    hotkeys._dispatch_input_action('capture_segments:5', source='hover_keyboard')


def source(texts, index):
    manager = NS(display_data=[(i, i*2, [(text, None)], []) for i, text in enumerate(texts)],
                 display_end_times=[i*2+1.5 for i in range(len(texts))])
    snapshot = NS(cue_index=index, start_time=index*2, plain_text=texts[index], inline_ruby_text=texts[index])
    return manager, snapshot


@pytest.mark.parametrize('before,after', [(0, 0), (1, 0), (2, 0), (0, 1), (0, 2), (1, 1)])
def test_explicit_range_ignores_punctuation_and_selects_exact_cues(before, after):
    manager, snapshot = source(['a.', 'b', 'c', 'd.', 'e'], 2)
    context = segment_range_for_cue(manager, snapshot, before, after)
    assert (context.first_cue, context.last_cue) == (2-before, 2+after)
    assert context.start_time == (2-before)*2
    assert context.end_time == (2+after)*2+1.5


def test_range_clamps_source_edges_and_rejects_stale_cue():
    manager, snapshot = source(['a', 'b'], 0)
    context = segment_range_for_cue(manager, snapshot, 0, 2)
    assert (context.first_cue, context.last_cue) == (0, 1)
    snapshot.plain_text = 'other episode'
    with pytest.raises(ValueError, match='outdated'):
        segment_range_for_cue(manager, snapshot)


def test_range_rejects_more_than_three_segments():
    manager, snapshot = source(['a', 'b', 'c', 'd', 'e'], 2)
    with pytest.raises(ValueError, match='three segments'):
        segment_range_for_cue(manager, snapshot, 2, 2)


def test_missing_punctuation_never_expands_into_next_speaker():
    manager, snapshot = source(['previous.', 'speaker one', 'speaker two.'], 1)
    assert sentence_for_cue(manager, snapshot) is None


def test_punctuation_still_groups_a_sentence_ending_in_current_cue():
    manager, snapshot = source(['previous.', 'sentence start', 'sentence end.'], 2)
    context = sentence_for_cue(manager, snapshot)
    assert (context.first_cue, context.last_cue) == (1, 2)


def test_automatic_sentence_never_exceeds_three_segments():
    manager, snapshot = source(['previous.', 'a', 'b', 'c', 'end.'], 4)
    assert sentence_for_cue(manager, snapshot) is None


def timed_source(texts, starts, ends, index, gap_limit=None):
    manager, snapshot = source(texts, index)
    manager.display_data = [(text, start, [(text, None)], []) for text, start in zip(texts, starts)]
    manager.display_end_times = ends
    manager.config = NS(get=lambda key: gap_limit if key == 'CAPTURE_SENTENCE_MAX_GAP_SEC' else None)
    snapshot.start_time = starts[index]
    return manager, snapshot


@pytest.mark.parametrize('gap,grouped', [(0, True), (.5, True), (1.5, True), (1.501, False), (3, False), (10, False)])
def test_automatic_sentence_uses_a_conservative_end_to_start_gap(gap, grouped):
    manager, snapshot = timed_source(['previous.', 'sentence start', 'sentence end.'],
        [0, 2, 4+gap], [1, 4, 6+gap], 2)
    context = sentence_for_cue(manager, snapshot)
    assert (context is not None) == grouped
    if grouped:
        assert (context.first_cue, context.last_cue) == (1, 2)


def test_a_long_pause_delimits_the_current_dialogue_block_without_discarding_its_sentence():
    manager, snapshot = timed_source(['unrelated unpunctuated speech', 'sentence start', 'sentence end.'],
        [0, 8, 10], [1, 9.5, 12], 2)
    context = sentence_for_cue(manager, snapshot)
    assert (context.first_cue, context.last_cue) == (1, 2)
    assert context.plain_text == 'sentence start\nsentence end.'
    assert context.start_time == 8


@pytest.mark.parametrize('setting,grouped', [(0, False), (.25, False), (2, True), ('2', True),
    ('invalid', False), (float('nan'), False), (float('inf'), False), (-1, False), (11, False)])
def test_sentence_pause_limit_is_adjustable_and_invalid_settings_use_the_default(setting, grouped):
    manager, snapshot = timed_source(['previous.', 'start', 'end.'], [0, 2, 6], [1, 4, 8], 2, setting)
    assert (sentence_for_cue(manager, snapshot) is not None) == grouped


def test_explicit_segment_range_can_cross_a_pause_when_deliberately_requested():
    manager, snapshot = timed_source(['start', 'end.'], [0, 10], [1, 11], 0)
    context = segment_range_for_cue(manager, snapshot, 0, 1)
    assert (context.first_cue, context.last_cue) == (0, 1)
    assert context.start_time == 0 and context.end_time == 11


def test_pause_limit_is_available_in_browser_recording_settings():
    from view.settings_advanced_ui import SettingsAdvancedUI
    settings = object.__new__(SettingsAdvancedUI)
    columns = settings._advanced_anki_columns()
    specs = [spec for column in columns for name, section in column for spec in section
             if spec.get('key') == 'CAPTURE_SENTENCE_MAX_GAP_SEC']
    assert len(specs) == 1
    assert specs[0]['default'] == 1.5 and specs[0]['type'] == 'float'


@pytest.mark.parametrize("expand", [False, True])
def test_fresh_capture_replaces_only_exact_sentence_including_listening(expand):
    client = object.__new__(AnkiClient)
    client.sound_field, client.image_field = 'Sound', 'Image'
    client.add_rubies_to_sentence_ja_field, client.sentence_ja_field = 'Raw', 'SentenceJA'
    def note(nid, sentence, raw=''):
        return {'noteId': nid, 'fields': {name: {'value': value} for name, value in
            dict(Raw=raw, SentenceJA=sentence, Sound='[sound:old.wav]', Image='<img src="old.png">').items()}}
    notes = {1: note(1, '結果[けっか]は<br> 一緒[いっしょ]', '結果は\n一緒'),
             2: note(2, '結果[けっか]は 一緒[いっしょ]'),
             3: note(3, 'different sentence')}
    notes[1]['fields']['Sound']['value'] = '[sound:fresh.wav]'
    notes[1]['fields']['Image']['value'] = '<img src="fresh.png">'
    updates = []
    def invoke(action, params):
        if action == 'findNotes':
            return list(notes)
        if action == 'notesInfo':
            return [notes[nid] for nid in params['notes']]
        assert action == 'updateNoteFields'
        update = params['note']
        updates.append(update)
        notes[update['id']]['fields'].update({key: {'value': value} for key, value in update['fields'].items()})
    client._invoke = invoke
    context = None
    if expand:
        client.sentence_de_field = 'Translation'
        client._translate_sentence = lambda text: 'new translation'
        context = NS(plain_text='結果は\n一緒\n追加。', inline_ruby_text='結果[けっか]は\n一緒[いっしょ]\n追加[ついか]。')
    assert client.replace_exact_sentence_capture(1, sentence_context=context) == [2]
    if expand:
        assert notes[1]['fields']['Raw']['value'] == '結果は<br> 一緒<br> 追加。'
        assert notes[2]['fields']['SentenceJA']['value'].endswith('追加[ついか]。')
        assert notes[3]['fields']['SentenceJA']['value'] == 'different sentence'
        updates = [update for update in updates if 'Sound' in update['fields']]
    assert [update['id'] for update in updates] == [2]
    assert set(updates[0]['fields']) == {'Sound', 'Image'}
    assert notes[2]['fields']['Sound']['value'] == '[sound:fresh.wav]'
    assert notes[3]['fields']['Sound']['value'] == '[sound:old.wav]'


@pytest.mark.parametrize('digit,expected', [(3, (2, 0)), (4, (1, 0)), (5, (0, 0)), (6, (0, 1)), (7, (0, 2))])
def test_number_capture_dispatches_capture_only_range(digit, expected):
    from controller.hotkey_controller import HotkeyController
    captured = []
    hotkeys = HotkeyController(NS())
    object.__setattr__(hotkeys, '_trigger_capture_only_hotkey', lambda **kw: captured.append(kw))
    object.__setattr__(hotkeys, '_modal_input_blocked', lambda: False)
    object.__setattr__(hotkeys, '_take_frozen_voice_target', lambda action: (action, None))
    # Exercise the same semantic action that physical key resolution enqueues.
    hotkeys.controller._shutting_down = False
    hotkeys.controller._voice_abort_requested = False
    hotkeys._dispatch_input_action(f'capture_segments:{digit}')
    assert captured == [{'segment_range': expected}]


def test_number_capture_bindings_respect_capture_disable():
    from controller.hotkey_controller import HotkeyController
    disabled = False
    hotkeys = HotkeyController(NS(config=NS(get=lambda key: disabled if key == 'DISABLE_HOTKEY_CAPTURE_ONLY' else None), HOTKEY_DISABLE_KEYS={}))
    object.__setattr__(hotkeys, '_mode2_numpad_enabled', lambda: False)
    object.__setattr__(hotkeys, '_shortcut_bindings_for_key', lambda key: [])
    captures = [(f'capture_segments:{digit}', str(digit)) for digit in range(3, 8)]
    playback = [('toggle_play', 'numpad0'), ('toggle_play', 'numpadenter')]
    assert hotkeys._single_fire_bindings() == captures + playback
    disabled = True
    assert hotkeys._single_fire_bindings() == playback


def test_both_keyboard_sections_capture_instead_of_legacy_numeric_navigation():
    from controller.hotkey_controller import HotkeyController
    hotkeys = HotkeyController(NS())
    for name, fn in {
        '_always_available_single_fire_bindings': lambda: [],
        '_popup_add_anki_bindings': lambda: [],
        '_popup_translation_bindings': lambda: [],
        '_is_popup_open': lambda: False,
        '_hotkeys_disabled': lambda: False,
        '_single_fire_bindings': lambda: [('capture_segments:4', '4')],
        '_repeat_action_bindings': lambda: [('go_back', '4')],
        '_shortcut_matches': lambda binding, key: binding == key.char,
    }.items():
        object.__setattr__(hotkeys, name, fn)
    assert [c['action'] for c in hotkeys._matching_shortcut_candidates(NS(char='4', vk=52))] == ['capture_segments:4']
    assert [c['action'] for c in hotkeys._matching_shortcut_candidates(NS(char='4', vk=100))] == ['capture_segments:4']


@pytest.mark.parametrize('vk,scan,flags,token', [(13, 0x1c, 1, 'numpadenter'),
    (96, 0x52, 0, 'numpad0'), (45, 0x52, 0, 'numpad0'),
    (99, 0x51, 0, 'numpad3'), (34, 0x51, 0, 'numpad3'),
    (100, 0x4b, 0, 'numpad4'), (37, 0x4b, 0, 'numpad4'),
    (101, 0x4c, 0, 'numpad5'), (12, 0x4c, 0, 'numpad5'),
    (102, 0x4d, 0, 'numpad6'), (39, 0x4d, 0, 'numpad6'),
    (103, 0x47, 0, 'numpad7'), (36, 0x47, 0, 'numpad7')])
def test_windows_filter_preserves_numpad_identity_with_numlock_on_or_off(vk, scan, flags, token):
    from controller.hotkey_controller import HotkeyController
    hotkeys = HotkeyController(NS())
    pressed, released = [], []
    object.__setattr__(hotkeys, '_on_key_press', pressed.append)
    object.__setattr__(hotkeys, '_on_key_release', released.append)
    data = NS(vkCode=vk, scanCode=scan, flags=flags)
    assert hotkeys._windows_keyboard_event_filter(0x100, data) is False
    assert hotkeys._windows_keyboard_event_filter(0x101, data) is False
    assert token in hotkeys._key_tokens(pressed[0])
    assert hotkeys._key_tokens(pressed[0]) == hotkeys._key_tokens(released[0])


@pytest.mark.parametrize('vk,scan,flags', [(13, 0x1c, 0), (45, 0x52, 1), (37, 0x4b, 1), (52, 0x05, 0)])
def test_windows_filter_does_not_claim_regular_enter_insert_arrows_or_top_row(vk, scan, flags):
    from controller.hotkey_controller import HotkeyController
    hotkeys = HotkeyController(NS())
    object.__setattr__(hotkeys, '_on_key_press', lambda key: pytest.fail('Unexpected interception'))
    assert hotkeys._windows_keyboard_event_filter(0x100, NS(vkCode=vk, scanCode=scan, flags=flags)) is True


@pytest.mark.parametrize('mode2', [False, True])
def test_extra_numpad_playback_bindings_only_in_mode_one(mode2):
    from controller.hotkey_controller import HotkeyController
    hotkeys = HotkeyController(NS(config=NS(get=lambda key: None), HOTKEY_DISABLE_KEYS={}))
    object.__setattr__(hotkeys, '_mode2_numpad_enabled', lambda: mode2)
    object.__setattr__(hotkeys, '_shortcut_bindings_for_key', lambda key:
        ['space'] if key == 'SHORTCUT_TOGGLE_PLAY' else ['numpad0'] if key == 'SHORTCUT_MODE2_TOGGLE_PLAY' else [])
    playback = [binding for action, binding in hotkeys._single_fire_bindings() if action == 'toggle_play']
    assert playback == (['numpad0'] if mode2 else ['space', 'numpad0', 'numpadenter'])


def test_capture_only_range_uses_current_cue_without_changing_card_sentence():
    from controller.anki_controller import AnkiController
    from model.capture_session import FrozenActionTarget
    manager, cue = source(['a', 'b', 'c', 'd'], 2)
    captures = []
    owner = NS(sub_manager=manager, current_cue_snapshot=cue,
               browser_sync=NS(native=True, freeze_capture=lambda session: None,
                               capture=lambda session, nid: captures.append((session, nid))))
    action = AnkiController(owner)
    target = FrozenActionTarget.create(selected_text='word', subtitle_text='original Anki sentence', sentence_key='original Anki sentence')
    action._last_subtitleplayer_added_card = {'note_id': 42, 'target': target}
    result = action.trigger_external_capture_only(interaction_owner='voice', segment_range=(2, 0))
    assert result['accepted']
    session, nid = captures[0]
    assert nid == 42 and session.capture_only and session.replace_sentence_media
    assert session.target.subtitle_text == 'original Anki sentence'
    assert session.target.cue_snapshot is cue
    assert (session.target.sentence_context.first_cue, session.target.sentence_context.last_cue) == (0, 2)


@pytest.mark.parametrize('index,before,after', [(1, 1, 0), (0, 0, 1)])
def test_native_recorder_freezes_the_explicit_interval(index, before, after):
    from test_native_capture import browser
    from model.capture_session import FrozenActionTarget, CaptureSession
    manager, cue = source(['a', 'b'], index)
    context = segment_range_for_cue(manager, cue, before, after)
    session = CaptureSession(FrozenActionTarget.create(selected_text='word', subtitle_text='original', sentence_key='original',
        cue_snapshot=cue, sentence_context=context), capture_only=True)
    sync = browser()
    sync._manual_offset = None
    sync.freeze_capture(session)
    assert session.mapped_interval == pytest.approx((11, 25.284))


@pytest.mark.parametrize('before,after', [(2, 0), (1, 0), (0, 0), (0, 1), (0, 2)])
def test_hovered_recording_key_creates_word_card_with_exact_recorded_sentence(before, after):
    from controller.anki_controller import AnkiController
    from controller.hotkey_controller import HotkeyController
    manager, cue = source(['previous two.', 'previous.', 'hovered word', 'sentence continues', 'sentence ends.'], 2)
    queued, errors = [], []
    spans = ((0, 0, 7),)
    owner = NS(sub_manager=manager, config=NS(get=lambda key: False),
        renderer=NS(subtitle_target_at_pointer_for_anki=lambda position: {'text': 'hovered', 'snapshot': cue, 'spans': spans}),
        popup=NS(), browser_sync=NS(native=True, _capture=None),
        anki=NS(ping=lambda: True, normalize_capture_sentence_key=lambda text: ' '.join(text.split())),
        _report_voice_status=lambda *args: errors.append(args))
    action = AnkiController(owner)
    owner.anki_controller = action
    owner._add_selection_to_anki = action._add_selection_to_anki
    for name, callback in {
        '_begin_action_feedback': lambda *args: None,
        '_begin_word_feedback': lambda *args: None,
        '_acquire_pause_lease': lambda *args: None,
        '_start_anki_add_worker': lambda **kwargs: queued.append(kwargs),
    }.items():
        object.__setattr__(action, name, callback)
    assert HotkeyController(owner)._trigger_capture_only_hotkey(segment_range=(before, after))
    assert len(queued) == 1 and not errors
    request = queued[0]
    target = request['action_target']
    expected = '\n'.join(row[2][0][0] for row in manager.display_data[2-before:3+after])
    assert request['post_add_capture'] and target.selected_text == 'hovered'
    assert target.initiator == 'popup_capture_key'  # Uses recording's pointer-away behavior.
    assert target.subtitle_text == target.sentence_context.plain_text == expected
    assert target.sentence_context.first_cue == 2-before
    assert target.sentence_context.last_cue == 2+after
    assert target.cue_snapshot is cue and target.selection_spans == spans
    assert action._last_subtitleplayer_added_card is None  # No previous card is required.
    cue.plain_text = 'another UI cue'
    assert target.subtitle_text == expected  # In-flight work retains the recorded sentence.


def test_recording_without_hover_preserves_capture_only_and_ignores_old_selection():
    from controller.hotkey_controller import HotkeyController
    captures = []
    owner = NS(renderer=NS(subtitle_target_at_pointer_for_anki=lambda position: {}), popup=NS(),
        anki_controller=NS(trigger_external_capture_only=lambda **kwargs: captures.append(kwargs) or {'accepted': True}),
        _add_selection_to_anki=lambda **kwargs: pytest.fail('A previous selection must not add a card'))
    hotkeys = HotkeyController(owner)
    object.__setattr__(hotkeys, '_highlighted_anki_payload', lambda: {'text': 'old selected word'})
    assert hotkeys._trigger_capture_only_hotkey(segment_range=(0, 1))
    assert captures == [dict(use_capture_only_resume=True, interaction_owner='physical', segment_range=(0, 1))]


def test_blank_popup_space_cannot_mine_an_underlying_subtitle_word():
    from controller.hotkey_controller import HotkeyController
    captures = []
    owner = NS(popup=NS(word_target_at_pointer_for_anki=lambda position: {'inside': True, 'text': ''}),
        renderer=NS(subtitle_target_at_pointer_for_anki=lambda position: pytest.fail('Popup covers this word')),
        anki_controller=NS(trigger_external_capture_only=lambda **kwargs: captures.append(kwargs) or {'accepted': True}))
    assert HotkeyController(owner)._trigger_capture_only_hotkey(segment_range=(0, 1))
    assert len(captures) == 1


def test_popup_word_uses_the_pointer_position_frozen_when_the_key_was_pressed():
    from controller.hotkey_controller import HotkeyController
    manager, cue = source(['word', 'next segment'], 0)
    positions, added = [], []
    def hit(position):
        positions.append(position)
        return {'inside': True, 'text': 'word', 'snapshot': cue, 'spans': ((0, 0, 4),)}
    owner = NS(popup=NS(word_target_at_pointer_for_anki=hit), _add_selection_to_anki=lambda **kwargs: added.append(kwargs))
    hotkeys = HotkeyController(owner)
    hotkeys._pointer_action_position = (123, 456)
    assert hotkeys._trigger_capture_only_hotkey(segment_range=(0, 1))
    assert positions == [(123, 456)] and added[0]['selected_text'] == 'word'
    assert added[0]['segment_range'] == (0, 1) and added[0]['cue_snapshot'] is cue


def test_popup_word_hit_does_not_reuse_selected_text_or_change_the_selection():
    from view.popup import CopyPopup
    popup = object.__new__(CopyPopup)
    window = NS(winfo_exists=lambda: True, winfo_viewable=lambda: True,
        winfo_rootx=lambda: 100, winfo_rooty=lambda: 200, winfo_width=lambda: 300, winfo_height=lambda: 100)
    entry = NS(winfo_rootx=lambda: 110, winfo_rooty=lambda: 210, winfo_width=lambda: 280, winfo_height=lambda: 80)
    popup._popup, popup._entry_widget = window, entry
    popup._segment_hits, popup._word_hits = [], [dict(base='word', line_index=0, char_start=3, char_end=7)]
    popup._cue_snapshot = object()
    popup._get_selected_text = lambda: pytest.fail('Recording must not use a stored selection')
    popup._region_at_pointer = lambda widget, regions, x, y, **kwargs: regions[0] if regions and x < 50 else None
    result = popup.word_target_at_pointer_for_anki((130, 230))
    assert result['text'] == 'word' and result['spans'] == ((0, 3, 7),)
    assert result['snapshot'] is popup._cue_snapshot and result['inside']
    assert popup.word_target_at_pointer_for_anki((300, 230))['text'] == ''
    assert popup.word_target_at_pointer_for_anki((500, 500)) == {}
