from SubtitlePlayer.controller.controller import SubtitleController
from SubtitlePlayer.controller.anki_controller import AnkiController
from SubtitlePlayer.controller.hotkey_controller import HotkeyController
from SubtitlePlayer.controller.overlay_controller import OverlayController
from SubtitlePlayer.model.config_manager import ConfigManager
from SubtitlePlayer.model.word_database import WordDatabase, WordEntry
from SubtitlePlayer.view.settings_advanced_ui import SettingsAdvancedUI
from SubtitlePlayer.view.settings_ui import SettingsUI
from SubtitlePlayer.view.subtitle_overlay import SubtitleOverlayUI
from types import SimpleNamespace
from pynput.keyboard import Key
from pynput.mouse import Button
import queue
import threading


class _DictConfig:
    def __init__(self, values):
        self.values = dict(values)

    def get(self, key):
        return self.values.get(key)


class _CharKey:
    def __init__(self, char):
        self.char = char


class _VkKey:
    def __init__(self, vk):
        self.vk = vk


class _FocusOwner:
    def __init__(self, focused=None):
        self._focused = focused

    def focus_get(self):
        return self._focused


class _TextWidget:
    def winfo_exists(self):
        return True

    def winfo_class(self):
        return "Entry"


def test_video_click_flags(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"VIDEO_CLICK": true, "VIDEO_CLICK_PLAY": false, "VIDEO_CLICK_WINDOW": true}'
    )
    cfg = ConfigManager(str(cfg_path))

    ctrl = object.__new__(SubtitleController)
    ctrl.config = cfg
    ctrl._init_runtime_state()

    assert ctrl.video_click is True
    assert ctrl.video_click_play is False
    assert ctrl.video_click_window is True


def test_destroyed_text_widget_is_not_focused_input():
    class DestroyedWidget:
        def winfo_exists(self):
            raise Exception('bad window path name ".!toplevel6"')

        def winfo_class(self):
            raise AssertionError("winfo_class should not be called after winfo_exists fails")

    assert HotkeyController._is_text_input_widget(DestroyedWidget()) is False


def _hotkeys(values=None):
    controller = SimpleNamespace(
        config=_DictConfig(values or {}),
        shift_pressed=False,
        alt_pressed=False,
        ctrl_pressed=False,
        SHORTCUT_DEFAULTS=SubtitleController.SHORTCUT_DEFAULTS,
        HOTKEY_DISABLE_KEYS=SubtitleController.HOTKEY_DISABLE_KEYS,
    )
    return HotkeyController(controller)


def _hotkey_runtime(values=None):
    controller = SimpleNamespace(
        config=_DictConfig(values or {}),
        shift_pressed=False,
        alt_pressed=False,
        ctrl_pressed=False,
        translation_pressed=False,
        translation_provider="deepl",
        _active_translation_action=None,
        _single_fire_actions=set(),
        _input_actions=queue.Queue(),
        _shutting_down=False,
        _pending_seek_delta=0.0,
        _repeat_lock=threading.Lock(),
        _held_repeat_next_fire={},
        _held_repeat_fired=set(),
        _held_repeat_press_time={},
        _repeat_initial_delay_sec=0.22,
        settings=SimpleNamespace(
            input_mode=1,
            root=_FocusOwner(None),
            control_window=None,
            advanced_window=None,
            _last_skip_value=5.0,
        ),
        popup=SimpleNamespace(_popup=None),
        SHORTCUT_DEFAULTS=SubtitleController.SHORTCUT_DEFAULTS,
        HOTKEY_DISABLE_KEYS=SubtitleController.HOTKEY_DISABLE_KEYS,
    )
    return HotkeyController(controller), controller


def _drain_input_actions(hotkeys, controller):
    actions = []
    while not controller._input_actions.empty():
        item = controller._input_actions.get_nowait()
        action = item[0] if isinstance(item, tuple) else item
        actions.append(action)
        hotkeys._dispatch_input_action(action)
    return actions


def test_shortcut_matching_requires_exact_modifiers():
    hotkeys = _hotkeys()

    assert hotkeys._shortcut_matches("a", _CharKey("a")) is True

    hotkeys.ctrl_pressed = True
    assert hotkeys._shortcut_matches("a", _CharKey("\x01")) is False
    assert hotkeys._shortcut_matches("ctrl+a", _CharKey("\x01")) is True

    hotkeys.shift_pressed = True
    assert hotkeys._shortcut_matches("ctrl+a", _CharKey("\x01")) is False
    assert hotkeys._shortcut_matches("ctrl+shift+a", _CharKey("\x01")) is True


def test_altgr_binding_is_treated_as_ctrl_alt():
    hotkeys = _hotkeys()

    assert hotkeys._split_shortcut("altgr+i") == ({"ctrl", "alt"}, "i")


def test_physical_altgr_triggers_ctrl_alt_hover_combo():
    hotkeys, controller = _hotkey_runtime({
        "HOVER_DICTIONARY_ENABLED": True,
        "HOVER_DICTIONARY_HOTKEY": "ctrl+alt+i",
    })

    hotkeys._on_key_press(Key.alt_gr)
    hotkeys._on_key_press(_CharKey("i"))

    assert controller.ctrl_pressed is True
    assert controller.alt_pressed is True
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is True

    hotkeys._on_key_release(_CharKey("i"))
    hotkeys._on_key_release(Key.alt_gr)

    assert controller.ctrl_pressed is False
    assert controller.alt_pressed is False


def test_held_ctrl_can_switch_between_single_fire_hotkeys_without_repressing_ctrl():
    hotkeys, controller = _hotkey_runtime({
        "SHORTCUT_EPISODE_DEC": "ctrl+y",
        "SHORTCUT_EPISODE_INC": "ctrl+x",
    })

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(_CharKey("\x19"))
    hotkeys._on_key_release(_CharKey("\x19"))
    hotkeys._on_key_press(_CharKey("\x18"))

    actions = [controller._input_actions.get_nowait()[0] for _ in range(2)]
    assert actions == ["episode_dec", "episode_inc"]
    assert controller.ctrl_pressed is True


def test_global_shortcut_is_blocked_when_text_input_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_JUMP_SUB_END": "ctrl+shift+y"})
    controller.settings.root = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(Key.shift_l)
    hotkeys._on_key_press(_CharKey("\x19"))

    assert controller._input_actions.empty()


def test_hover_hold_combo_tracks_while_text_input_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({
        "HOVER_DICTIONARY_ENABLED": True,
        "HOVER_DICTIONARY_HOTKEY": "ctrl+y",
        "SHORTCUT_JUMP_SUB_END": "ctrl+y",
    })
    controller.settings.root = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(_CharKey("\x19"))

    assert controller._input_actions.empty()
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is True

    hotkeys._on_key_release(_CharKey("\x19"))
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is False

    hotkeys._on_key_release(Key.ctrl_l)
    assert controller.ctrl_pressed is False


def test_hover_hold_can_switch_key_while_ctrl_remains_held_in_text_input(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({
        "HOVER_DICTIONARY_ENABLED": True,
        "HOVER_DICTIONARY_HOTKEY": "ctrl+y, ctrl+x",
    })
    controller.settings.root = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(_CharKey("\x19"))

    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is True

    hotkeys._on_key_release(_CharKey("\x19"))
    assert controller.ctrl_pressed is True
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is False

    hotkeys._on_key_press(_CharKey("\x18"))
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is True


def test_hover_hold_ctrl_alt_combo_tracks_while_text_input_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({
        "HOVER_DICTIONARY_ENABLED": True,
        "HOVER_DICTIONARY_HOTKEY": "ctrl+alt+i",
    })
    controller.settings.root = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(Key.alt_l)
    hotkeys._on_key_press(_CharKey("i"))

    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is True

    hotkeys._on_key_release(_CharKey("i"))
    hotkeys._on_key_release(Key.alt_l)
    hotkeys._on_key_release(Key.ctrl_l)

    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is False


def test_stale_text_focus_does_not_block_hotkeys_when_app_is_not_foreground(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_TOGGLE_PLAY": "space"})
    controller.settings.root = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: False)

    hotkeys._on_key_press(Key.space)

    action, _event_time = controller._input_actions.get_nowait()
    assert action == "toggle_play"


def test_cached_external_foreground_allows_subtitle_toggle_with_stale_text_focus(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_TOGGLE_SUBTITLES": "s"})
    controller.settings.root = _FocusOwner(_TextWidget())
    controller._app_window_hwnds = {12345}

    def foreground_check(windows):
        assert windows == {12345}
        return False

    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", foreground_check)

    hotkeys._on_key_press(_CharKey("s"))

    action, _event_time = controller._input_actions.get_nowait()
    assert action == "toggle_subtitles"


def test_cached_app_foreground_blocks_subtitle_toggle_when_text_input_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_TOGGLE_SUBTITLES": "s"})
    controller.settings.root = _FocusOwner(_TextWidget())
    controller._app_window_hwnds = {12345}
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(_CharKey("s"))

    assert controller._input_actions.empty()


def test_settings_button_shows_when_settings_window_was_manually_minimized():
    ui = object.__new__(SettingsUI)
    ui._settings_window_from_control = True
    ui.advanced_window = None

    calls = []
    ui.root = SimpleNamespace(
        state=lambda: "iconic",
        winfo_viewable=lambda: False,
        attributes=lambda *args: True,
    )
    ui.show_settings_window = lambda: calls.append("show")
    ui.demote_settings_window = lambda: calls.append("demote")

    assert ui.toggle_settings_window_from_control() is True
    assert calls == ["show"]


def test_settings_button_demotes_when_settings_window_is_visible_topmost():
    ui = object.__new__(SettingsUI)
    ui._settings_window_from_control = True
    ui.advanced_window = None

    calls = []
    ui.root = SimpleNamespace(
        state=lambda: "normal",
        winfo_viewable=lambda: True,
        attributes=lambda *args: True,
    )
    ui.show_settings_window = lambda: calls.append("show")
    ui.demote_settings_window = lambda: calls.append("demote")

    assert ui.toggle_settings_window_from_control() is False
    assert calls == ["demote"]


def test_settings_button_hides_advanced_window_with_settings_window(monkeypatch):
    import SubtitlePlayer.view.settings_ui as settings_ui_module

    ui = object.__new__(SettingsUI)
    ui._settings_window_from_control = True

    calls = []

    class _AdvancedWin:
        def winfo_exists(self):
            return True

        def state(self):
            return "normal"

        def winfo_viewable(self):
            return True

        def withdraw(self):
            calls.append("withdraw_advanced")

    ui.advanced_window = _AdvancedWin()
    ui.root = SimpleNamespace(
        state=lambda: "normal",
        winfo_viewable=lambda: True,
        attributes=lambda *args: True,
    )
    ui.adv_settings = SimpleNamespace(_save_advanced_window_position=lambda _win: calls.append("save_advanced"))
    ui.demote_settings_window = lambda: calls.append("demote")
    ui.show_settings_window = lambda: calls.append("show")
    monkeypatch.setattr(settings_ui_module, "is_any_window_foreground", lambda _windows: None)

    assert ui.toggle_settings_window_from_control() is False
    assert calls == ["save_advanced", "withdraw_advanced", "demote"]


def test_settings_button_raises_advanced_window_when_external_window_is_foreground(monkeypatch):
    import SubtitlePlayer.view.settings_ui as settings_ui_module

    ui = object.__new__(SettingsUI)
    ui._settings_window_from_control = True

    calls = []

    class _AdvancedWin:
        def winfo_exists(self):
            return True

        def state(self):
            return "normal"

        def winfo_viewable(self):
            return True

        def attributes(self, *_args):
            return True

    win = _AdvancedWin()
    ui.advanced_window = win
    ui.control_window = object()
    ui.root = SimpleNamespace(
        state=lambda: "normal",
        winfo_viewable=lambda: True,
        attributes=lambda *args: True,
    )
    ui.adv_settings = SimpleNamespace(_show_advanced_window=lambda window: calls.append(("show_advanced", window is win)))
    ui.show_settings_window = lambda: calls.append("show")
    ui.demote_settings_window = lambda: calls.append("demote")
    monkeypatch.setattr(settings_ui_module, "is_any_window_foreground", lambda _windows: False)

    assert ui.toggle_settings_window_from_control() is True
    assert calls == ["show", ("show_advanced", True)]


def test_settings_button_shows_withdrawn_advanced_window_with_settings_window():
    ui = object.__new__(SettingsUI)
    ui._settings_window_from_control = False

    calls = []

    class _AdvancedWin:
        def winfo_exists(self):
            return True

        def state(self):
            return "withdrawn"

        def winfo_viewable(self):
            return False

    win = _AdvancedWin()
    ui.advanced_window = win
    ui.root = SimpleNamespace(
        state=lambda: "normal",
        winfo_viewable=lambda: False,
        attributes=lambda *args: False,
    )
    ui.adv_settings = SimpleNamespace(_show_advanced_window=lambda window: calls.append(("show_advanced", window is win)))
    ui.show_settings_window = lambda: calls.append("show")
    ui.demote_settings_window = lambda: calls.append("demote")

    assert ui.toggle_settings_window_from_control() is True
    assert calls == ["show", ("show_advanced", True)]


def test_advanced_window_show_uses_topmost_no_activate(monkeypatch):
    import SubtitlePlayer.view.settings_advanced_ui as advanced_module

    calls = []
    ui = object.__new__(SettingsAdvancedUI)
    win = SimpleNamespace(
        deiconify=lambda: calls.append("deiconify"),
        lift=lambda: calls.append("lift"),
        attributes=lambda *args: calls.append(("attributes", args)),
    )

    monkeypatch.setattr(
        advanced_module,
        "show_normal_window_no_activate",
        lambda window, topmost=False: calls.append(("show", window is win, topmost)) or True,
    )

    SettingsAdvancedUI._show_advanced_window(ui, win)

    assert calls == [("show", True, True)]


def test_settings_entry_focusout_does_not_steal_next_entry_focus():
    ui = object.__new__(SettingsUI)
    focus_calls = []
    applied = []

    master = SimpleNamespace(focus_set=lambda: focus_calls.append("focus"))

    class _Entry:
        def __init__(self, text):
            self.text = text
            self.master = master

        def get(self):
            return self.text

        def delete(self, *_args):
            self.text = ""

        def insert(self, _index, value):
            self.text = str(value)

    entry = _Entry("7")
    ui.offset_entry = entry
    ui.skip_entry = _Entry("5")
    ui.offset_var = SimpleNamespace(set=lambda _value: None)
    ui.skip_var = SimpleNamespace(set=lambda _value: None)
    ui._last_offset_value = 5.0
    ui._parse_number = SettingsUI._parse_number.__get__(ui, SettingsUI)
    ui._set_entry_value = SettingsUI._set_entry_value.__get__(ui, SettingsUI)
    ui._get_last_value = SettingsUI._get_last_value.__get__(ui, SettingsUI)
    ui._apply_offset_change = lambda value, persist, previous_value=None: applied.append((value, persist, previous_value))

    SettingsUI._on_entry_focus_out(ui, SimpleNamespace(widget=entry))

    assert focus_calls == []
    assert applied == [(7.0, True, 5.0)]


def test_set_to_commit_does_not_steal_focus():
    ui = object.__new__(SettingsUI)
    focus_calls = []
    committed = []
    ui.setto_var = SimpleNamespace(get=lambda: "12.5")

    class _Entry:
        master = SimpleNamespace(focus_set=lambda: focus_calls.append("focus"))

        def delete(self, *_args):
            raise AssertionError("delete should not be called for valid set-to value")

    ui.setto_entry = _Entry()
    ui._on_set_to_return = lambda text: committed.append(text)

    assert SettingsUI._on_set_to_commit(ui, SimpleNamespace()) == "break"
    assert committed == ["12.5"]
    assert focus_calls == []


def test_hover_pause_toggles_app_timer_with_spacebar():
    calls = []

    class _Playback:
        def toggle_play(self, event_time=None):
            calls.append(event_time)
            controller.playing = not controller.playing

    class _Keyboard:
        def __init__(self):
            self.events = []

        def press(self, key):
            self.events.append(("press", key))

        def release(self, key):
            self.events.append(("release", key))

    keyboard = _Keyboard()
    controller = SimpleNamespace(
        subtitle_hover_pause_video=True,
        _hover_video_pause_active=False,
        _hover_timer_pause_active=False,
        _hover_pause_keyboard=keyboard,
        playing=True,
        playback=_Playback(),
    )
    overlay = OverlayController(controller)

    overlay._trigger_background_video_space(paused=True)
    assert controller.playing is False
    assert controller._hover_timer_pause_active is True
    assert len(calls) == 1
    assert [event[0] for event in keyboard.events] == ["press", "release"]

    overlay._trigger_background_video_space(paused=False)
    assert controller.playing is True
    assert controller._hover_timer_pause_active is False
    assert len(calls) == 2


def test_subtitle_center_snap_is_horizontal_only(monkeypatch):
    import SubtitlePlayer.view.subtitle_overlay as subtitle_overlay_module

    geometries = []
    ui = object.__new__(SubtitleOverlayUI)
    ui.config = SimpleNamespace(
        get=lambda key: {
            "SUBTITLE_CENTER_SNAP_ENABLED": True,
            "SUBTITLE_CENTER_SNAP_THRESHOLD_PX": 20,
        }.get(key)
    )
    ui.root = object()
    ui.sub_window = SimpleNamespace(geometry=lambda value: geometries.append(value))
    ui._sync_handle_to_subtitle = lambda: None
    monkeypatch.setattr(subtitle_overlay_module, "get_monitor_rects", lambda _root: [(0, 0, 1000, 800)])

    center_x, center_y = SubtitleOverlayUI._snap_center_position(ui, 510, 390, 200, 100)

    assert center_x == 500
    assert center_y == 390
    assert geometries == ["+400+340"]


def test_anime_specific_offset_is_saved_and_restored():
    class _Config:
        def __init__(self):
            self.values = {
                "ANIME_OFFSETS": {"anime a": 1.25},
                "EXTRA_OFFSET": 0.0,
            }

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    apply_calls = []
    offset_text = []
    controller = object.__new__(SubtitleController)
    controller.config = _Config()
    controller.default_offset = 0.0
    controller.sub_manager = SimpleNamespace(get_anime_name=lambda: "Anime A")
    controller.settings = SimpleNamespace(
        _last_offset_value=0.0,
        default_offset=0.0,
        offset_var=SimpleNamespace(set=lambda text: offset_text.append(text)),
        _format_number=lambda value: f"{float(value):g}",
        _apply_offset_change=lambda value, persist, previous_value=None, adjust_current=True: apply_calls.append(
            (value, persist, previous_value, adjust_current)
        ),
    )

    assert controller._apply_saved_offset_for_current_anime() is True
    assert controller.default_offset == 1.25
    assert controller.settings._last_offset_value == 1.25
    assert controller.config.values["EXTRA_OFFSET"] == 1.25
    assert apply_calls == [(1.25, False, 0.0, False)]
    assert offset_text == ["1.25 s"]

    controller._remember_current_anime_offset(2.5)
    assert controller.config.values["ANIME_OFFSETS"]["anime a"] == 2.5


def test_subtitle_handle_enablement_follows_phone_mode_only():
    calls = []
    controller = SimpleNamespace(
        overlay=SimpleNamespace(
            set_handle_enabled=lambda enabled: calls.append(("set", enabled)),
            hide_handle=lambda: calls.append(("hide", None)),
        ),
        _pointer_inside_settings_windows=lambda: False,
    )
    overlay = OverlayController(controller)

    overlay.show_subtitle_handle(False)
    overlay.show_subtitle_handle(True)
    controller._pointer_inside_settings_windows = lambda: True
    overlay.show_subtitle_handle(True)

    assert calls == [("set", False), ("set", True), ("hide", None)]


def test_annotation_disabled_callbacks_do_not_create_services():
    controller = object.__new__(SubtitleController)
    controller.config = _DictConfig({"ANNOTATION_ENABLED": False})
    controller.word_database = None
    controller.annotation_provider = None

    assert controller.annotation_list_words("") == []
    result = controller.annotation_add_word({"surface": "test"})

    assert result["ok"] is False
    assert result["error"] == "Annotation is disabled."
    assert controller.word_database is None
    assert controller.annotation_provider is None


def test_annotation_edit_anki_word_writes_back_to_same_note(tmp_path):
    controller = object.__new__(SubtitleController)
    controller.config = _DictConfig(
        {
            "ANNOTATION_ENABLED": True,
            "ANNOTATION_ANKI_MEANING_FIELDS": "Back",
            "ANNOTATION_ANKI_WORD_FIELDS": "Front",
        }
    )
    database = WordDatabase(str(tmp_path / "words.json"))
    existing = database.upsert(
        WordEntry(
            surface="\u5207\u308b",
            meaning="old",
            source="anki",
            status="anki_mature",
            extra={"note_id": 123, "field": "Front", "meaning_field": "Back"},
        )
    )
    controller.word_database = database
    controller.annotation_provider = None
    controller._ensure_annotation_services = lambda: (database, None)
    controller._refresh_annotation_runtime = lambda **_kwargs: None
    calls = []

    class _Anki:
        def _invoke(self, action, params=None):
            calls.append((action, params or {}))
            return None

    controller.anki = _Anki()

    result = controller.annotation_add_word(
        {
            "old_key": existing.key,
            "surface": "\u5207\u308b",
            "source": "anki",
            "status": "anki_mature",
            "meaning": "new",
            "extra": existing.extra,
        }
    )

    assert result["ok"] is True
    assert database.get("anki", "\u5207\u308b").meaning == "new"
    assert database.get("local", "\u5207\u308b") is None
    assert calls == [("updateNoteFields", {"note": {"id": 123, "fields": {"Back": "new"}}})]


def test_anki_check_connection_prompts_when_unavailable(monkeypatch):
    calls = []
    controller = SimpleNamespace(anki=SimpleNamespace(ping=lambda: False))
    anki_controller = AnkiController(controller)
    monkeypatch.setattr(AnkiController, "prompt_anki_connection", lambda self: calls.append("prompt"))

    assert anki_controller.on_anki_check_connection() is False
    assert calls == ["prompt"]


def test_generic_anki_prompt_does_not_clear_pending_add_payload():
    calls = []

    class _ExistingWindow:
        def winfo_exists(self):
            return True

        def deiconify(self):
            calls.append("deiconify")

        def lift(self):
            calls.append("lift")

        def attributes(self, *args):
            calls.append(("attributes", args))

    controller = SimpleNamespace(
        _pending_anki_payload={"selected_text": "\u5207\u308b", "subtitle_text": "\u5207\u308b"},
        _anki_wait_window=_ExistingWindow(),
    )
    anki_controller = AnkiController(controller)

    anki_controller._show_anki_wait_dialog()

    assert controller._pending_anki_payload == {"selected_text": "\u5207\u308b", "subtitle_text": "\u5207\u308b"}
    assert calls == ["deiconify", "lift", ("attributes", ("-topmost", True))]


def test_anki_wait_dialog_centers_on_anchor_monitor():
    x, y = AnkiController._centered_position_on_monitor(
        400,
        200,
        [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)],
        anchor_x=2500,
        anchor_y=400,
    )

    assert (x, y) == (2360, 412)


def test_annotation_anki_refresh_prompts_when_unavailable():
    calls = []
    controller = object.__new__(SubtitleController)
    controller.config = _DictConfig({"ANNOTATION_ENABLED": True})
    controller.anki = SimpleNamespace(ping=lambda: False)
    controller.anki_controller = SimpleNamespace(prompt_anki_connection=lambda: calls.append("prompt"))

    result = SubtitleController.annotation_anki_refresh(controller)

    assert result["ok"] is False
    assert "AnkiConnect is not reachable" in result["error"]
    assert result["decks"] == []
    assert result["models"] == []
    assert calls == ["prompt"]


def test_annotation_edit_anki_word_prompts_when_unavailable(tmp_path):
    calls = []
    controller = object.__new__(SubtitleController)
    controller.config = _DictConfig(
        {
            "ANNOTATION_ENABLED": True,
            "ANNOTATION_ANKI_MEANING_FIELDS": "Back",
            "ANNOTATION_ANKI_WORD_FIELDS": "Front",
        }
    )
    database = WordDatabase(str(tmp_path / "words.json"))
    existing = database.upsert(
        WordEntry(
            surface="\u5207\u308b",
            meaning="old",
            source="anki",
            status="anki_mature",
            extra={"note_id": 123, "field": "Front", "meaning_field": "Back"},
        )
    )
    controller.word_database = database
    controller.annotation_provider = None
    controller._ensure_annotation_services = lambda: (database, None)
    controller._refresh_annotation_runtime = lambda **_kwargs: None
    controller.anki = SimpleNamespace(ping=lambda: False)
    controller.anki_controller = SimpleNamespace(prompt_anki_connection=lambda: calls.append("prompt"))

    result = controller.annotation_add_word(
        {
            "old_key": existing.key,
            "surface": "\u5207\u308b",
            "source": "anki",
            "status": "anki_mature",
            "meaning": "new",
            "extra": existing.extra,
        }
    )

    assert result["ok"] is False
    assert "AnkiConnect is not reachable" in result["error"]
    assert database.get("anki", "\u5207\u308b").meaning == "old"
    assert calls == ["prompt"]


def test_hover_translation_uses_word_database_meaning(tmp_path):
    database = WordDatabase(str(tmp_path / "words.json"))
    database.upsert(WordEntry(surface="\u732b", meaning="cat"), save=False)
    controller = object.__new__(SubtitleController)
    controller.word_database = database
    controller.annotation_provider = None
    controller.anki = SimpleNamespace(
        translate_hover_selection=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("external translation should not be called")
        )
    )

    assert SubtitleController._translate_hover_selection(controller, "\u732b", provider="google") == "\u732b \u2014 cat"


def test_hover_translation_uses_single_token_lookup_from_word_database(tmp_path):
    database = WordDatabase(str(tmp_path / "words.json"))
    database.upsert(WordEntry(surface="\u98df\u3079\u308b", meaning="eat"), save=False)
    controller = object.__new__(SubtitleController)
    controller.word_database = database
    controller.annotation_provider = None
    controller.anki = SimpleNamespace(
        word_spans=lambda _text: [
            {
                "surface": "\u98df\u3079\u305f",
                "lookup": "\u98df\u3079\u308b",
            }
        ],
        translate_hover_selection=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("external translation should not be called")
        ),
    )

    assert SubtitleController._translate_hover_selection(controller, "\u98df\u3079\u305f") == "\u98df\u3079\u305f \u2014 eat"


def test_hover_translation_falls_back_when_word_database_has_no_meaning(tmp_path):
    database = WordDatabase(str(tmp_path / "words.json"))
    database.upsert(WordEntry(surface="\u72ac"), save=False)
    calls = []
    controller = object.__new__(SubtitleController)
    controller.word_database = database
    controller.annotation_provider = None
    controller.anki = SimpleNamespace(
        translate_hover_selection=lambda text, provider="deepl": calls.append((text, provider)) or "\u72ac \u2014 dog"
    )

    assert SubtitleController._translate_hover_selection(controller, "\u72ac", provider="google") == "\u72ac \u2014 dog"
    assert calls == [("\u72ac", "google")]


def test_global_shortcut_is_blocked_when_advanced_entry_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_TOGGLE_PLAY": "space"})
    controller.settings.advanced_window = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(Key.space)

    assert controller._input_actions.empty()


def test_popup_translation_shortcut_works_when_popup_entry_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    refresh_calls = []
    hotkeys, controller = _hotkey_runtime({"SHORTCUT_POPUP_DEEPL_TRANSLATE": "t"})
    controller.popup = SimpleNamespace(
        _popup=_FocusOwner(_TextWidget()),
        is_open=lambda: True,
        refresh_hover_display=lambda: refresh_calls.append("refresh"),
    )
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(_CharKey("t"))

    assert controller.translation_pressed is True
    assert controller._active_translation_action == "popup_deepl_translate"
    assert _drain_input_actions(hotkeys, controller) == ["_refresh_popup_translation"]
    assert refresh_calls == ["refresh"]

    hotkeys._on_key_release(_CharKey("t"))

    assert controller.translation_pressed is False
    assert _drain_input_actions(hotkeys, controller) == ["_refresh_popup_translation"]
    assert refresh_calls == ["refresh", "refresh"]


def test_shift_shortcut_does_not_fire_while_ctrl_is_held():
    hotkeys, controller = _hotkey_runtime({"SHORTCUT_SUBTITLE_BACK": "shift+left"})

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(Key.shift_l)
    hotkeys._on_key_press(Key.left)

    assert controller._input_actions.empty()


def test_only_explicit_shortcuts_disabled_setting_blocks_hotkeys():
    hotkeys, controller = _hotkey_runtime({"SHORTCUTS_DISABLED": False})
    controller.settings.input_mode = 3
    controller.sub_manager = SimpleNamespace(is_search_dialog_active=lambda: True)

    assert hotkeys._hotkeys_disabled() is False

    controller.config = _DictConfig({"SHORTCUTS_DISABLED": True})
    assert hotkeys._hotkeys_disabled() is True


def test_voice_listening_toggle_remains_available_in_mode_3():
    hotkeys, _controller = _hotkey_runtime(
        {
            "SHORTCUTS_DISABLED": True,
            "SHORTCUT_TOGGLE_VOICE": "shift+l",
        }
    )
    hotkeys.shift_pressed = True

    candidates = hotkeys._matching_shortcut_candidates(_CharKey("l"))

    assert [(item["action"], item["binding"]) for item in candidates] == [
        ("toggle_voice_listening", "shift+l")
    ]


def test_combined_voice_command_waits_for_seek_before_toggling_playback():
    calls = []
    statuses = []
    controller = SimpleNamespace(
        config=_DictConfig({}),
        shift_pressed=False,
        alt_pressed=False,
        ctrl_pressed=False,
        settings=SimpleNamespace(root=SimpleNamespace(after=lambda _delay, callback: callback())),
        SHORTCUT_DEFAULTS=SubtitleController.SHORTCUT_DEFAULTS,
        HOTKEY_DISABLE_KEYS=SubtitleController.HOTKEY_DISABLE_KEYS,
        handle_voice_seek_action=lambda direction, repeat_count=1, on_complete=None: (
            calls.append(("seek", direction, repeat_count)),
            on_complete(True),
        ),
        handle_voice_playback_action=lambda desired, on_complete=None: (
            calls.append(("playback", desired)),
            on_complete(True),
        ),
        _report_voice_status=lambda state, message: statuses.append((state, message)),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._start_voice_sequence([("voice_go_back", 5), ("voice_play", 1)])

    assert calls == [("seek", "back", 5), ("playback", None)]
    assert statuses[-1] == ("listening", "Combined voice command completed.")


def test_empty_shortcut_value_stays_disabled():
    hotkeys = _hotkeys({"SHORTCUT_POPUP_ADD_ANKI": "", "SHORTCUT_POPUP_ADD_ANKI_CAPTURE": ""})

    assert hotkeys._get_shortcut_value("SHORTCUT_POPUP_ADD_ANKI") == ""
    assert hotkeys._popup_add_anki_bindings() == []


def test_multiple_shortcut_values_expand_and_ignore_invalid(caplog):
    hotkeys = _hotkeys({"SHORTCUT_POPUP_DEEPL_TRANSLATE": "t, ctrl+shift+y, ctrl+j+k"})

    assert hotkeys._shortcut_bindings_for_key("SHORTCUT_POPUP_DEEPL_TRANSLATE") == [
        "t",
        "ctrl+shift+y",
    ]
    assert "Ignoring invalid shortcut" in caplog.text


def test_popup_translation_release_checks_all_multi_bindings():
    refresh_calls = []
    hotkeys, controller = _hotkey_runtime(
        {
            "SHORTCUT_POPUP_DEEPL_TRANSLATE": "t, y",
            "SHORTCUT_POPUP_GOOGLE_TRANSLATE": "g",
        }
    )
    controller.popup = SimpleNamespace(
        _popup=object(),
        is_open=lambda: True,
        refresh_hover_display=lambda: refresh_calls.append("refresh"),
    )

    hotkeys._on_key_press(_CharKey("y"))

    assert controller.translation_pressed is True
    assert controller._active_translation_action == "popup_deepl_translate"
    assert _drain_input_actions(hotkeys, controller) == ["_refresh_popup_translation"]
    assert refresh_calls == ["refresh"]

    hotkeys._on_key_release(_CharKey("y"))

    assert controller.translation_pressed is False
    assert controller._active_translation_action is None
    assert _drain_input_actions(hotkeys, controller) == ["_refresh_popup_translation"]
    assert refresh_calls == ["refresh", "refresh"]


def test_popup_translation_key_repeat_does_not_refire_while_held():
    refresh_calls = []
    hotkeys, controller = _hotkey_runtime({"SHORTCUT_POPUP_DEEPL_TRANSLATE": "t"})
    controller.popup = SimpleNamespace(
        _popup=object(),
        refresh_hover_display=lambda: refresh_calls.append("refresh"),
    )

    hotkeys._on_key_press(_CharKey("t"))
    hotkeys._on_key_press(_CharKey("t"))
    hotkeys._on_key_press(_CharKey("t"))

    assert controller.translation_pressed is True
    assert _drain_input_actions(hotkeys, controller) == ["_refresh_popup_translation"]
    assert refresh_calls == ["refresh"]


def test_popup_add_key_repeat_does_not_refire_while_held():
    hotkeys, controller = _hotkey_runtime({"SHORTCUT_POPUP_ADD_ANKI": "q"})
    controller.popup = SimpleNamespace(_popup=object())

    hotkeys._on_key_press(_CharKey("q"))
    hotkeys._on_key_press(_CharKey("q"))

    queued = [controller._input_actions.get_nowait()[0] for _ in range(controller._input_actions.qsize())]
    assert queued == ["popup_add_anki"]

    hotkeys._on_key_release(_CharKey("q"))
    hotkeys._on_key_press(_CharKey("q"))

    queued = [controller._input_actions.get_nowait()[0] for _ in range(controller._input_actions.qsize())]
    assert queued == ["popup_add_anki"]


def test_popup_add_capture_default_v_queues_capture_action():
    hotkeys, controller = _hotkey_runtime({})
    controller.popup = SimpleNamespace(_popup=object())

    hotkeys._on_key_press(_CharKey("v"))

    queued = [controller._input_actions.get_nowait()[0] for _ in range(controller._input_actions.qsize())]
    assert queued == ["popup_add_anki_capture"]


def test_external_click_clears_stale_popup_hotkey_state(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    refresh_calls = []
    hotkeys, controller = _hotkey_runtime(
        {
            "SHORTCUT_POPUP_DEEPL_TRANSLATE": "t",
            "SHORTCUT_POPUP_ADD_ANKI": "q",
        }
    )
    controller.ctrl_pressed = True
    controller.translation_pressed = True
    controller._active_translation_action = "popup_deepl_translate"
    controller._active_translation_binding = "t"
    controller._single_fire_actions.add("popup_add_anki")
    controller.popup = SimpleNamespace(
        _popup=object(),
        _hover_ruby_window=None,
        refresh_hover_display=lambda: refresh_calls.append("refresh"),
    )
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: False)

    hotkeys._on_global_click(5000, 5000, Button.left, True)

    assert controller.ctrl_pressed is False
    assert controller.translation_pressed is False
    assert controller._single_fire_actions == set()

    hotkeys._on_key_press(_CharKey("t"))

    assert controller.translation_pressed is True
    assert controller.translation_provider == "deepl"
    assert _drain_input_actions(hotkeys, controller) == [
        "_refresh_popup_translation",
        "_refresh_popup_translation",
    ]
    assert refresh_calls == ["refresh", "refresh"]


def test_ambiguous_shortcut_assignments_are_ignored(caplog):
    hotkeys, controller = _hotkey_runtime(
        {
            "SHORTCUT_TOGGLE_PLAY": "space",
            "SHORTCUT_TOGGLE_SUBTITLES": "space",
        }
    )

    hotkeys._on_key_press(Key.space)

    assert controller._input_actions.empty()
    assert "Ignoring ambiguous shortcut assignment" in caplog.text


def test_same_action_duplicate_shortcut_aliases_are_deduped():
    hotkeys, controller = _hotkey_runtime({"SHORTCUT_TOGGLE_PLAY": "space, spacebar"})

    hotkeys._on_key_press(Key.space)

    action, _event_time = controller._input_actions.get_nowait()
    assert action == "toggle_play"
    assert controller._input_actions.empty()


def test_mode2_play_shortcut_does_not_fire_in_mode1():
    hotkeys, controller = _hotkey_runtime(
        {
            "SHORTCUT_TOGGLE_PLAY": "space",
            "SHORTCUT_MODE2_TOGGLE_PLAY": "numpad0",
        }
    )
    controller.settings.input_mode = 1

    hotkeys._on_key_press(_VkKey(96))

    assert controller._input_actions.empty()

    hotkeys._on_key_press(Key.space)
    action, _event_time = controller._input_actions.get_nowait()
    assert action == "toggle_play"


def test_mode2_play_shortcut_replaces_space_in_mode2():
    hotkeys, controller = _hotkey_runtime(
        {
            "SHORTCUT_TOGGLE_PLAY": "space",
            "SHORTCUT_MODE2_TOGGLE_PLAY": "numpad0",
        }
    )
    controller.settings.input_mode = 2
    controller.settings.numpad_mode_enabled = True

    hotkeys._on_key_press(Key.space)

    assert controller._input_actions.empty()

    hotkeys._on_key_press(_VkKey(96))
    action, _event_time = controller._input_actions.get_nowait()
    assert action == "toggle_play"


def test_control_window_sync_uses_user_hidden_not_blank_subtitle_state():
    class Window:
        def __init__(self):
            self.actions = []

        def winfo_exists(self):
            return True

        def withdraw(self):
            self.actions.append("withdraw")

        def deiconify(self):
            self.actions.append("deiconify")

        def lift(self):
            self.actions.append("lift")

        def attributes(self, *args):
            self.actions.append(("attributes", args))

    control = Window()
    overlay = Window()
    controller = SimpleNamespace(
        settings=SimpleNamespace(control_window=control),
        overlay=SimpleNamespace(sub_window=overlay),
        subtitles_user_hidden=False,
        subtitle_deleted=True,
    )
    hotkeys = HotkeyController(controller)

    hotkeys._sync_control_window_for_subtitle_visibility()

    assert "deiconify" in control.actions
    assert "withdraw" not in control.actions

    control.actions.clear()
    overlay.actions.clear()
    controller.subtitles_user_hidden = True

    hotkeys._sync_control_window_for_subtitle_visibility()

    assert "withdraw" in control.actions
    assert "withdraw" in overlay.actions


def test_clear_subtitle_action_syncs_control_window():
    class Window:
        def __init__(self):
            self.actions = []

        def winfo_exists(self):
            return True

        def withdraw(self):
            self.actions.append("withdraw")

    controller = SimpleNamespace(
        config=_DictConfig({}),
        settings=SimpleNamespace(control_window=Window()),
        overlay=SimpleNamespace(sub_window=Window()),
        subtitles_user_hidden=False,
        HOTKEY_DISABLE_KEYS=SubtitleController.HOTKEY_DISABLE_KEYS,
    )

    def toggle():
        controller.subtitles_user_hidden = True

    controller.subtitle_navigation = SimpleNamespace(toggle_subtitle_visibility=toggle)
    hotkeys = HotkeyController(controller)

    hotkeys._dispatch_input_action("clear_subtitle")

    assert "withdraw" in controller.settings.control_window.actions
    assert "withdraw" in controller.overlay.sub_window.actions

def test_plain_shift_hover_requires_no_ctrl_or_alt():
    controller = object.__new__(SubtitleController)
    controller.config = _DictConfig({
        "HOVER_DICTIONARY_ENABLED": True,
        "HOVER_DICTIONARY_HOTKEY": "shift",
    })
    controller.shift_pressed = True
    controller.ctrl_pressed = False
    controller.alt_pressed = False
    controller.hotkey_controller = HotkeyController(controller)

    assert controller._plain_shift_hover_active() is True

    controller.ctrl_pressed = True
    assert controller._plain_shift_hover_active() is False

    controller.ctrl_pressed = False
    controller.alt_pressed = True
    assert controller._plain_shift_hover_active() is False


def test_hover_hold_active_supports_configurable_modifier_and_key_combo():
    hotkeys, controller = _hotkey_runtime({
        "HOVER_DICTIONARY_ENABLED": True,
        "HOVER_DICTIONARY_HOTKEY": "ctrl+d",
    })

    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is False

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(_CharKey("d"))

    assert controller._pressed_key_tokens == {"d"}
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is True

    hotkeys._on_key_press(Key.shift_l)
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is False

    hotkeys._on_key_release(Key.shift_l)
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is True

    hotkeys._on_key_release(_CharKey("d"))
    assert hotkeys.hover_hold_active("HOVER_DICTIONARY_ENABLED", "HOVER_DICTIONARY_HOTKEY", "shift") is False


def test_specific_hover_combo_wins_over_single_modifier_hover():
    controller = object.__new__(SubtitleController)
    controller.config = _DictConfig({
        "HOVER_DICTIONARY_ENABLED": True,
        "HOVER_DICTIONARY_HOTKEY": "ctrl+alt+i",
        "HOVER_TRANSLATION_ENABLED": True,
        "HOVER_TRANSLATION_HOTKEY": "alt",
    })
    controller.shift_pressed = False
    controller.ctrl_pressed = True
    controller.alt_pressed = True
    controller.translation_pressed = False
    controller._pressed_key_tokens = {"i"}
    controller.SHORTCUT_DEFAULTS = SubtitleController.SHORTCUT_DEFAULTS
    controller.HOTKEY_DISABLE_KEYS = SubtitleController.HOTKEY_DISABLE_KEYS
    controller.hotkey_controller = HotkeyController(controller)

    assert controller._hover_modifier_mode() == "dictionary"
