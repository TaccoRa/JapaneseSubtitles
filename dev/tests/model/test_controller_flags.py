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


def test_shortcut_matching_requires_exact_modifiers():
    hotkeys = _hotkeys()

    assert hotkeys._shortcut_matches("a", _CharKey("a")) is True

    hotkeys.ctrl_pressed = True
    assert hotkeys._shortcut_matches("a", _CharKey("\x01")) is False
    assert hotkeys._shortcut_matches("ctrl+a", _CharKey("\x01")) is True

    hotkeys.shift_pressed = True
    assert hotkeys._shortcut_matches("ctrl+a", _CharKey("\x01")) is False
    assert hotkeys._shortcut_matches("ctrl+shift+a", _CharKey("\x01")) is True


def test_global_shortcut_is_blocked_when_text_input_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_JUMP_SUB_END": "ctrl+shift+y"})
    controller.settings.root = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(Key.ctrl_l)
    hotkeys._on_key_press(Key.shift_l)
    hotkeys._on_key_press(_CharKey("\x19"))

    assert controller._input_actions.empty()


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


def test_settings_button_hides_advanced_window_with_settings_window():
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

    assert ui.toggle_settings_window_from_control() is False
    assert calls == ["save_advanced", "withdraw_advanced", "demote"]


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


def test_global_shortcut_is_blocked_when_advanced_entry_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_TOGGLE_PLAY": "space"})
    controller.settings.advanced_window = _FocusOwner(_TextWidget())
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(Key.space)

    assert controller._input_actions.empty()


def test_popup_shortcut_is_blocked_when_popup_entry_is_focused(monkeypatch):
    import SubtitlePlayer.controller.hotkey_controller as hotkey_module

    hotkeys, controller = _hotkey_runtime({"SHORTCUT_POPUP_DEEPL_TRANSLATE": "t"})
    controller.popup = SimpleNamespace(
        _popup=_FocusOwner(_TextWidget()),
        is_open=lambda: True,
        refresh_hover_display=lambda: None,
    )
    monkeypatch.setattr(hotkey_module, "is_any_window_foreground", lambda _windows: True)

    hotkeys._on_key_press(_CharKey("t"))

    assert controller.translation_pressed is False
    assert controller._input_actions.empty()


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


def test_empty_shortcut_value_stays_disabled():
    hotkeys = _hotkeys({"SHORTCUT_POPUP_ADD_ANKI": ""})

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
        _popup=None,
        is_open=lambda: True,
        refresh_hover_display=lambda: refresh_calls.append("refresh"),
    )

    hotkeys._on_key_press(_CharKey("y"))

    assert controller.translation_pressed is True
    assert controller._active_translation_action == "popup_deepl_translate"

    hotkeys._on_key_release(_CharKey("y"))

    assert controller.translation_pressed is False
    assert controller._active_translation_action is None
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
    controller.config = _DictConfig({"SHIFT_HOVER_KANJI_DICTIONARY": True})
    controller.shift_pressed = True
    controller.ctrl_pressed = False
    controller.alt_pressed = False

    assert controller._plain_shift_hover_active() is True

    controller.ctrl_pressed = True
    assert controller._plain_shift_hover_active() is False

    controller.ctrl_pressed = False
    controller.alt_pressed = True
    assert controller._plain_shift_hover_active() is False
