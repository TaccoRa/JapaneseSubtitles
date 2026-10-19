import queue
import threading
from types import SimpleNamespace

import pytest

import SubtitlePlayer.controller.hotkey_controller as hotkey_module
from SubtitlePlayer.controller.hotkey_controller import HotkeyController
from SubtitlePlayer.controller.playback_controller import PlaybackController


class FakeRoot:
    def __init__(self):
        self.cancelled = []
        self.scheduled = []

    def after(self, delay_ms, callback):
        handle = f"after-{len(self.scheduled)}"
        self.scheduled.append((handle, delay_ms, callback))
        return handle

    def after_cancel(self, handle):
        self.cancelled.append(handle)


class FakeSlider:
    def __init__(self):
        self.values = []
        self.value = 0.0

    def winfo_exists(self):
        return True

    def get(self):
        return self.value

    def set(self, value):
        self.value = float(value)
        self.values.append(self.value)


class FakeButton:
    def __init__(self):
        self.configs = []

    def config(self, **kwargs):
        self.configs.append(kwargs)


def make_controller(now: float = 0.0):
    root = FakeRoot()
    slider = FakeSlider()
    button = FakeButton()
    display_updates = []
    controller = SimpleNamespace(
        _shutting_down=False,
        _update_loop_job=None,
        audio_padding=0.0,
        current_time=0.0,
        entry_editing=False,
        get_offset_value=lambda: 0.0,
        last_subtitle_text="",
        last_update=now,
        overlay=SimpleNamespace(root=root),
        playing=False,
        settings=SimpleNamespace(slider=slider, play_pause_btn=button, _last_skip_value=5.0),
        slider_dragging=False,
        sub_manager=SimpleNamespace(subtitles=[]),
        subtitle_deleted=False,
        subtitle_timeout_job=None,
        total_duration=3600.0,
        update_interval_ms=100,
        update_time_and_subtitle_displays=lambda: display_updates.append(float(controller.current_time)),
        _get_display_start_times=lambda: [],
        _get_display_end_times=lambda: [],
        _skip_buttons_use_subtitle_segments=lambda: False,
        _schedule_hide_controls=lambda: None,
        control_time_entry_return=lambda _event: None,
    )
    controller._display_updates = display_updates
    playback = PlaybackController(controller)
    return controller, playback, root, slider, button


























def test_hotkey_dispatch_passes_event_time_to_skip_actions():
    calls = []

    class Playback:
        def toggle_play(self, event_time=None):
            calls.append(("toggle", event_time))

        def go_back(self, event_time=None):
            calls.append(("back", event_time))

        def go_forward(self, event_time=None):
            calls.append(("forward", event_time))

    controller = SimpleNamespace(
        _shutting_down=False,
        playback=Playback(),
        subtitle_navigation=SimpleNamespace(toggle_subtitle_visibility=lambda: None),
        config=SimpleNamespace(get=lambda _key: False),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._dispatch_input_action("go_back", event_time=10.0)
    hotkeys._dispatch_input_action("go_forward", event_time=11.0)

    assert calls == [("back", pytest.approx(10.0)), ("forward", pytest.approx(11.0))]


def test_jump_sub_end_uses_display_end_time_and_audio_padding():
    controller, playback, _root, _slider, _button = make_controller()
    controller.current_time = 12.0
    controller.audio_padding = 500.0
    controller._get_display_start_times = lambda: [10.0]
    controller._get_display_end_times = lambda: [15.0]

    playback.on_jump_sub_end()

    assert controller.current_time == pytest.approx(15.5)
    assert controller._display_updates == [pytest.approx(15.5)]


def test_jump_sub_end_does_not_jump_back_after_current_display_cue_ended():
    controller, playback, _root, _slider, _button = make_controller()
    controller.current_time = 16.0
    controller._get_display_start_times = lambda: [10.0]
    controller._get_display_end_times = lambda: [15.0]

    playback.on_jump_sub_end()

    assert controller.current_time == pytest.approx(16.0)
    assert controller._display_updates == []


def test_repeated_jump_sub_end_advances_from_padding_gap_to_next_cue():
    controller, playback, _root, _slider, _button = make_controller()
    controller.current_time = 12.0
    controller.audio_padding = 500.0
    controller._get_display_start_times = lambda: [10.0, 20.0]
    controller._get_display_end_times = lambda: [15.0, 23.0]

    playback.on_jump_sub_end()
    assert controller.current_time == pytest.approx(15.5)

    playback.on_jump_sub_end()

    assert controller.current_time == pytest.approx(23.5)
    assert controller._display_updates == [pytest.approx(15.5), pytest.approx(23.5)]


def test_hotkey_dispatch_toggles_debugging():
    calls = []
    controller = SimpleNamespace(
        _shutting_down=False,
        toggle_debugging=lambda: calls.append("toggle_debugging"),
        playback=SimpleNamespace(),
        subtitle_navigation=SimpleNamespace(toggle_subtitle_visibility=lambda: None),
        config=SimpleNamespace(get=lambda _key: False),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._dispatch_input_action("toggle_debugging")

    assert calls == ["toggle_debugging"]


def test_hotkey_dispatch_adds_popup_selection_to_anki():
    calls = []
    controller = SimpleNamespace(
        _shutting_down=False,
        popup=SimpleNamespace(add_selected_to_anki_if_pointer_inside=lambda **kwargs: (calls.append("popup_add") or True)),
        playback=SimpleNamespace(),
        subtitle_navigation=SimpleNamespace(toggle_subtitle_visibility=lambda: None),
        config=SimpleNamespace(get=lambda _key: False),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._dispatch_input_action("popup_add_anki")

    assert calls == ["popup_add"]


def test_hotkey_dispatch_adds_popup_selection_to_anki_with_capture():
    calls = []
    controller = SimpleNamespace(
        _shutting_down=False,
        popup=SimpleNamespace(
            add_selected_to_anki_if_pointer_inside=lambda **kwargs: (calls.append(kwargs) or True)
        ),
        playback=SimpleNamespace(),
        subtitle_navigation=SimpleNamespace(toggle_subtitle_visibility=lambda: None),
        config=SimpleNamespace(get=lambda _key: False),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._dispatch_input_action("popup_add_anki_capture")

    assert calls == [{"post_add_capture": True, "on_complete": None}]


def test_repeat_seek_preview_updates_time_display_only():
    calls = []
    controller = SimpleNamespace(
        _pending_seek_delta=0.0,
        config=SimpleNamespace(get=lambda _key: False),
        settings=SimpleNamespace(_last_skip_value=5.0),
        update_time_display=lambda: calls.append("time"),
        update_time_and_subtitle_displays=lambda: calls.append("full"),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._accumulate_pending_seek("go_back")
    hotkeys._clear_pending_seek_preview()

    assert controller._pending_seek_delta == pytest.approx(0.0)
    assert calls == ["time", "time"]


def test_quick_tap_seek_uses_key_down_time_when_enqueued_on_release(monkeypatch):
    monkeypatch.setattr(hotkey_module.time, "perf_counter", lambda: 20.0)
    controller = SimpleNamespace(
        _input_actions=queue.Queue(),
        _pending_seek_delta=0.0,
        _repeat_lock=threading.Lock(),
        _held_repeat_next_fire={},
        _held_repeat_fired=set(),
        _held_repeat_press_time={},
        _repeat_initial_delay_sec=0.22,
        _shutting_down=False,
        config=SimpleNamespace(get=lambda _key: False),
    )
    hotkeys = HotkeyController(controller)

    assert hotkeys._hold_repeat_action("go_back") is True
    monkeypatch.setattr(hotkey_module.time, "perf_counter", lambda: 20.150)
    hotkeys._release_repeat_actions({"go_back"})

    action, event_time = controller._input_actions.get_nowait()
    assert action == "go_back"
    assert event_time == pytest.approx(20.0)


def test_release_seek_drops_existing_queued_seek_action_to_prevent_double_jump():
    controller = SimpleNamespace(
        _input_actions=queue.Queue(),
        _pending_seek_delta=0.0,
        _repeat_lock=threading.Lock(),
        _held_repeat_next_fire={},
        _held_repeat_fired=set(),
        _held_repeat_press_time={},
        _repeat_initial_delay_sec=0.22,
        _shutting_down=False,
        config=SimpleNamespace(get=lambda _key: False),
        settings=SimpleNamespace(_last_skip_value=5.0),
    )
    hotkeys = HotkeyController(controller)

    controller._input_actions.put_nowait(("go_back", None))
    hotkeys._release_repeat_actions({"go_back"})

    queued_actions = [item[0] if isinstance(item, tuple) else item for item in list(controller._input_actions.queue)]
    assert queued_actions == ["go_back"]
