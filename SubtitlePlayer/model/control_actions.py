"""Input-independent action metadata. Legacy settings remain authoritative."""
from dataclasses import dataclass


EXTRA_VOICE_ACTIONS = {
    'skip_intro': dict(label='Skip intro / jump to saved title', dispatch='skip_intro', en=['skip intro', 'skip opening', 'skip to title'], de=['intro überspringen', 'vorspann überspringen', 'zum anfangstitel']),
    'remember_intro_target': dict(label='Remember intro/title target for this series', dispatch='remember_intro_target', en=['remember title start'], de=['anfangstitel merken']),
    'highlight_all': dict(label='Mark every word', dispatch='voice_highlight_all', en=['mark all', 'highlight all'], de=['alles markieren']),
    'copy_all': dict(label='Copy marked words or current subtitle', dispatch='voice_copy_all', en=['copy all'], de=['alles kopieren']),
    'submark': dict(label='Select a compound component', dispatch='voice_submark', subindexed=True, en=['submark', 'sub mark'], de=['teil markieren']),
    'toggle_voice_listening': dict(label='Start/stop voice listening', dispatch='toggle_voice_listening', en=['toggle listening'], de=['zuhören umschalten']),
    'toggle_debugging': dict(label='Toggle debugging', dispatch='toggle_debugging', en=['toggle debugging'], de=['debug umschalten']),
    'bring_to_front': dict(label='Bring app to front', dispatch='alt_x', en=['bring app to front'], de=['app nach vorne']),
    'toggle_subtitles': dict(label='Toggle subtitle visibility', dispatch='toggle_subtitles', en=['toggle subtitles'], de=['untertitel umschalten']),
    **{f'capture_segments_{n}': dict(label=f'Record subtitle range ({n})', dispatch=f'capture_segments:{n}',
        en=[f'record range {n}'], de=[f'bereich {n} aufnehmen']) for n in range(3, 8)},
    **{f'hover_{layer}': dict(label=f'Toggle {layer.replace("_", " ")} hover', dispatch=f'hover_toggle:{layer}',
        en=[f'toggle {layer.replace("_", " ")} hover'], de=[f'{label} hover umschalten'])
       for layer, label in [('dictionary', 'wörterbuch'), ('translation', 'übersetzung'), ('status', 'status')]},
    'popup_deepl_translate': dict(label='Translate popup with DeepL', dispatch='popup_deepl_translate', en=['popup deepl'], de=['popup deepl']),
    'popup_google_translate': dict(label='Translate popup with Google', dispatch='popup_google_translate', en=['popup google'], de=['popup google']),
    'popup_add_anki': dict(label='Add hovered/popup word', dispatch='popup_add_anki', en=['add hovered word'], de=['wort unter maus hinzufügen']),
    'popup_add_anki_capture': dict(label='Add hovered/popup word with recording', dispatch='popup_add_anki_capture', en=['record hovered word'], de=['wort unter maus aufnehmen']),
    'legacy_capture_only': dict(label='Record last added card', dispatch='capture_only', en=['record last card'], de=['letzte karte aufnehmen']),
}

# The two add workflows intentionally remain distinct: pointer/popup vs voice selection.
LEGACY_KEYS = {
    'play': 'SHORTCUT_TOGGLE_PLAY', 'go_back': 'SHORTCUT_GO_BACK', 'go_forward': 'SHORTCUT_GO_FORWARD',
    'subtitle_back': 'SHORTCUT_SUBTITLE_BACK', 'subtitle_forward': 'SHORTCUT_SUBTITLE_FORWARD',
    'jump_sub_end': 'SHORTCUT_JUMP_SUB_END', 'previous_episode': 'SHORTCUT_EPISODE_DEC',
    'next_episode': 'SHORTCUT_EPISODE_INC', 'speed_up': 'SHORTCUT_FAST_FORWARD_SPEED_UP',
    'speed_down': 'SHORTCUT_FAST_FORWARD_SPEED_DOWN', 'toggle_subtitles': 'SHORTCUT_TOGGLE_SUBTITLES',
    'bring_to_front': 'SHORTCUT_BRING_TO_FRONT', 'toggle_debugging': 'SHORTCUT_TOGGLE_DEBUGGING',
    'toggle_voice_listening': 'SHORTCUT_TOGGLE_VOICE',
    'popup_deepl_translate': 'SHORTCUT_POPUP_DEEPL_TRANSLATE', 'popup_google_translate': 'SHORTCUT_POPUP_GOOGLE_TRANSLATE',
    'popup_add_anki': 'SHORTCUT_POPUP_ADD_ANKI', 'popup_add_anki_capture': 'SHORTCUT_POPUP_ADD_ANKI_CAPTURE',
    'legacy_capture_only': 'SHORTCUT_CAPTURE_ONLY',
    'hover_dictionary': 'HOVER_DICTIONARY_HOTKEY', 'hover_translation': 'HOVER_TRANSLATION_HOTKEY',
    'hover_status': 'HOVER_STATUS_HOTKEY',
}


def binding_conflicts(values):
    """Compare only contexts that can be active together; modes 1/2 are exclusive."""
    seen, conflicts = {}, []
    bindings = dict(values.get('CONTROL_KEY_BINDINGS') or {})
    rows = [(key, value, 'mode2' if key.startswith('SHORTCUT_MODE2_') else 'mode1'
             if key in {'SHORTCUT_TOGGLE_PLAY', 'SHORTCUT_GO_BACK', 'SHORTCUT_GO_FORWARD',
                        'SHORTCUT_SUBTITLE_BACK', 'SHORTCUT_SUBTITLE_FORWARD'} else 'all')
            for key, value in values.items() if key.startswith('SHORTCUT_') and isinstance(value, str)
            and key not in {'SHORTCUTS_DISABLED'}]
    rows += [(key, value, 'all') for key, value in bindings.items()]
    rows += [(f'capture_segments:{digit}', str(digit), 'all') for digit in range(3, 8)]
    aliases = {'control': 'ctrl', 'escape': 'esc', 'return': 'enter', 'arrowleft': 'left',
               'arrowright': 'right', 'spacebar': 'space', '.': 'period', 'dot': 'period', '>': 'period',
               'num0': 'numpad0', 'np0': 'numpad0', 'num4': 'numpad4', 'np4': 'numpad4',
               'num6': 'numpad6', 'np6': 'numpad6'}
    for action, value, context in rows:
        for binding in str(value).split(','):
            tokens = [aliases.get(token.strip().lower(), token.strip().lower()) for token in binding.split('+') if token.strip()]
            tokens = [part for token in tokens for part in (('ctrl', 'alt') if token in {'altgr', 'rightalt', 'altgraph'} else (token,))]
            if not tokens:
                continue
            normalized = '+'.join(sorted(set(tokens)))
            for previous, previous_context in seen.get(normalized, []):
                if previous != action and (context == 'all' or previous_context == 'all' or context == previous_context):
                    conflicts.append(f'{binding.strip()}: {previous} / {action}')
            seen.setdefault(normalized, []).append((action, context))
    return conflicts


@dataclass(frozen=True)
class ControlAction:
    id: str
    label: str
    dispatch: str
    group: str
    config_key: str = ''
    parameters: bool = False
    hold: bool = False


def action_catalog():
    from model.voice_commands import VOICE_ACTIONS
    result = []
    for key, spec in VOICE_ACTIONS.items():
        dispatch = spec['dispatch']
        if key in {'previous_episode', 'next_episode', 'skip_intro', 'remember_intro_target'}:
            group = 'Episodes'
        elif key.startswith(('highlight', 'submark', 'copy')):
            group = 'Selection'
        elif any(word in key for word in ('dictionary', 'translation', 'translate', 'status', 'hover_')):
            group = 'Dictionary/Translation'
        elif key.startswith(('add', 'capture')):
            group = 'Anki/Recording'
        elif key in {'play', 'pause', 'go_back', 'go_forward', 'go_to_time', 'speed_up', 'speed_down'}:
            group = 'Playback'
        elif 'subtitle' in key or key == 'jump_sub_end':
            group = 'Subtitles'
        else:
            group = 'Application'
        result.append(ControlAction(key, spec['label'], dispatch, group, LEGACY_KEYS.get(key, ''),
            bool(spec.get('timed') or spec.get('indexed_only') or spec.get('subindexed')), key.startswith('hover_')))
    return result


def keyboard_bindings(config):
    """Only new bindings; old actions retain their contextual binding logic."""
    mapping = config.get('CONTROL_KEY_BINDINGS') or {}
    if not isinstance(mapping, dict):
        return []
    result = []
    for action in action_catalog():
        if action.config_key or action.parameters or action.id.startswith('capture_segments_'):
            continue
        disable = ('DISABLE_HOTKEY_POPUP_ADD_ANKI_CAPTURE' if action.id in {'capture', 'capture_together'} else
                   'DISABLE_HOTKEY_POPUP_ADD_ANKI' if action.id in {'add', 'add_together'} else
                   'DISABLE_HOTKEY_CAPTURE_ONLY' if action.id == 'capture_only' else None)
        if disable and config.get(disable):
            continue
        for binding in str(mapping.get(action.id) or '').split(','):
            if binding.strip():
                result.append((action.dispatch, binding.strip()))
    return result
