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
        _skip_buttons_use_subtitle_segments=lambda: False,
        _schedule_hide_controls=lambda: None,
        control_time_entry_return=lambda _event: None,
    )
    controller._display_updates = display_updates
    playback = PlaybackController(controller)
    return controller, playback, root, slider, button


def test_pause_advances_time_since_last_update():
    now = 100.0
    controller, playback, root, slider, button = make_controller(now)
    playback._now = lambda: now

    controller.playing = True
    controller.current_time = 10.0
    controller.last_update = now
    controller._update_loop_job = "pending-update"

    now += 0.073
    playback.toggle_play()

    assert controller.playing is False
    assert controller.current_time == pytest.approx(10.073)
    assert controller.last_update == pytest.approx(100.073)
    assert root.cancelled == ["pending-update"]
    assert slider.values[-1] == pytest.approx(10.073)
    assert button.configs[-1]["text"] == "Play"


def test_pause_publishes_current_time_once_after_sync():
    now = 100.0
    controller, playback, _root, slider, _button = make_controller(now)
    playback._now = lambda: now

    controller.playing = True
    controller.current_time = 10.0
    controller.last_update = now

    now += 0.073
    playback.toggle_play()

    assert controller.current_time == pytest.approx(10.073)
    assert controller._display_updates == [pytest.approx(10.073)]
    assert slider.values == [pytest.approx(10.073)]


def test_repeated_fast_pause_play_does_not_accumulate_lost_time():
    now = 0.0
    controller, playback, _root, _slider, _button = make_controller(now)
    playback._now = lambda: now

    durations = [0.017, 0.083, 0.041, 0.099, 0.012, 0.064] * 50
    expected = 0.0

    for duration in durations:
        playback.toggle_play()
        assert controller.playing is True
        now += duration
        expected += duration
        playback.toggle_play()
        assert controller.playing is False

    assert controller.current_time == pytest.approx(expected, abs=1e-9)


def test_update_loop_uses_monotonic_delta_and_reschedules():
    now = 5.0
    controller, playback, root, _slider, _button = make_controller(now)
    playback._now = lambda: now

    controller.playing = True
    controller.current_time = 20.0
    controller.last_update = now

    now += 0.125
    playback.update_loop()

    assert controller.current_time == pytest.approx(20.125)
    assert controller.last_update == pytest.approx(5.125)
    assert len(root.scheduled) == 1
    assert root.scheduled[0][1] == 100


def test_pause_uses_event_time_even_if_update_tick_ran_later():
    now = 100.0
    controller, playback, _root, _slider, _button = make_controller(now)
    playback._now = lambda: now

    controller.playing = True
    controller.current_time = 10.0
    controller.last_update = now

    key_time = now + 0.040
    now += 0.100
    playback.update_loop()
    assert controller.current_time == pytest.approx(10.100)

    playback.toggle_play(event_time=key_time)

    assert controller.playing is False
    assert controller.current_time == pytest.approx(10.040)
    assert controller.last_update == pytest.approx(key_time)


def test_hotkey_queue_preserves_event_timestamp():
    calls = []

    class Playback:
        def toggle_play(self, event_time=None):
            calls.append(event_time)

    class Root:
        def after(self, *_args):
            return "pump"

    controller = SimpleNamespace(
        _input_actions=queue.Queue(),
        _input_pump_job=None,
        _shutting_down=False,
        playback=Playback(),
        settings=SimpleNamespace(root=Root()),
        subtitle_navigation=SimpleNamespace(toggle_subtitle_visibility=lambda: None),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._enqueue_input_action("toggle_play", event_time=12.345)
    hotkeys._process_input_queue()

    assert calls == [pytest.approx(12.345)]


def test_skip_back_uses_event_time_even_if_update_tick_ran_later():
    now = 100.0
    controller, playback, _root, _slider, _button = make_controller(now)
    playback._now = lambda: now

    controller.playing = True
    controller.current_time = 40.0
    controller.last_update = now

    key_time = now + 0.040
    now += 0.100
    playback.update_loop()
    assert controller.current_time == pytest.approx(40.100)

    playback.go_back(event_time=key_time)

    assert controller.playing is True
    assert controller.current_time == pytest.approx(35.040)
    assert controller.last_update == pytest.approx(key_time)


def test_skip_back_publishes_current_time_once_after_sync():
    now = 100.0
    controller, playback, _root, slider, _button = make_controller(now)
    playback._now = lambda: now

    controller.playing = True
    controller.current_time = 40.0
    controller.last_update = now

    playback.go_back(event_time=now + 0.040)

    assert controller.current_time == pytest.approx(35.040)
    assert controller.last_update == pytest.approx(100.040)
    assert controller._display_updates == [pytest.approx(35.040)]
    assert slider.values == [pytest.approx(35.040)]


def test_skip_forward_uses_event_time_even_if_update_tick_ran_later():
    now = 100.0
    controller, playback, _root, _slider, _button = make_controller(now)
    playback._now = lambda: now

    controller.playing = True
    controller.current_time = 35.0
    controller.last_update = now

    key_time = now + 0.040
    now += 0.100
    playback.update_loop()
    assert controller.current_time == pytest.approx(35.100)

    playback.go_forward(event_time=key_time)

    assert controller.playing is True
    assert controller.current_time == pytest.approx(40.040)
    assert controller.last_update == pytest.approx(key_time)


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
        popup=SimpleNamespace(add_selected_to_anki_if_pointer_inside=lambda: calls.append("popup_add")),
        playback=SimpleNamespace(),
        subtitle_navigation=SimpleNamespace(toggle_subtitle_visibility=lambda: None),
        config=SimpleNamespace(get=lambda _key: False),
    )
    hotkeys = HotkeyController(controller)

    hotkeys._dispatch_input_action("popup_add_anki")

    assert calls == ["popup_add"]


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
