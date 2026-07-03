from types import SimpleNamespace

import pytest

from SubtitlePlayer.controller.episode_controller import EpisodeController
from SubtitlePlayer.controller.controller import SubtitleController
from SubtitlePlayer.controller.subtitle_navigation import SubtitleNavigationController
from SubtitlePlayer.view.popup import CopyPopup


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeRoot:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []
        self.titles = []
        self.destroyed = False

    def after(self, delay_ms, callback):
        handle = f"after-{len(self.scheduled)}"
        self.scheduled.append((handle, delay_ms, callback))
        return handle

    def after_cancel(self, handle):
        self.cancelled.append(handle)

    def title(self, value):
        self.titles.append(value)

    def winfo_exists(self):
        return True

    def quit(self):
        pass

    def destroy(self):
        self.destroyed = True


class FakeCanvas:
    def __init__(self):
        self.items = []
        self.deleted = []

    def itemconfig(self, item, **kwargs):
        self.items.append((item, kwargs))

    def delete(self, tag):
        self.deleted.append(tag)

    def update_idletasks(self):
        pass


class FakeSlider:
    def __init__(self, value=0.0):
        self.value = float(value)

    def get(self):
        return self.value

    def set(self, value):
        self.value = float(value)


class FakeRenderer:
    def __init__(self):
        self.canvas = FakeCanvas()
        self.calls = []

    def update_canvas(self, canvas):
        self.canvas = canvas

    def render_subtitle(self, top, bottom, overlay, preview=False):
        self.calls.append({"top": top, "bottom": bottom, "preview": bool(preview)})


def make_slider_controller():
    root = FakeRoot()
    renderer = FakeRenderer()
    manager = SimpleNamespace(
        display_data=[
            ("first", 0.0, [("first", "first-ruby")], []),
            ("second", 10.0, [("second", "second-ruby")], []),
        ],
        display_start_times=[0.0, 10.0],
        ensure_auto_ruby_for_index=lambda _idx: pytest.fail("auto-ruby must not run during slider preview"),
        _auto_ruby_enabled=lambda: True,
        _auto_ruby_ready_indices=set(),
    )
    settings = SimpleNamespace(
        root=root,
        _last_offset_value=0.0,
        time_overlay=FakeCanvas(),
        time_overlay_text="time",
        control_time_str=FakeVar(),
        slider=FakeSlider(10.0),
        update_time_overlay_position=lambda: None,
    )
    overlay = SimpleNamespace(
        root=root,
        subtitle_canvas=renderer.canvas,
        max_w=800,
        max_h=120,
    )
    controller = SimpleNamespace(
        settings=settings,
        overlay=overlay,
        renderer=renderer,
        sub_manager=manager,
        current_time=0.0,
        total_duration=60.0,
        subtitle_timeout_job=None,
        last_rendered_index=None,
        last_subtitle_text="",
        last_subtitle_raw="",
        subtitle_deleted=False,
        subtitles_user_hidden=False,
        subtitles_hidden_until_index=None,
        slider_dragging=False,
        _shutting_down=False,
        _slider_render_job=None,
        _slider_pending_value=None,
        _defer_auto_ruby_once=False,
        _auto_ruby_generation_id=0,
        _auto_ruby_thread=None,
        entry_editing=False,
        _pending_seek_delta=0.0,
        hide_subtitles_ms=5000,
        _last_time_overlay_text=None,
        _record_perf_sample=lambda *_args, **_kwargs: None,
        _hide_subtitles_temporarily=lambda: None,
    )
    navigation = SubtitleNavigationController(controller)
    controller.subtitle_navigation = navigation
    controller._get_display_start_times = navigation._get_display_start_times if hasattr(navigation, "_get_display_start_times") else lambda: manager.display_start_times
    controller.playback = SimpleNamespace(set_current_time=lambda value: setattr(controller, "current_time", float(value)))
    return controller, navigation, renderer


def test_slider_drag_preview_skips_auto_ruby_and_uses_preview_rendering():
    controller, navigation, renderer = make_slider_controller()

    navigation.on_slider_press(None)
    navigation.on_slider_change("10.0")
    navigation._render_slider_preview()

    assert controller.current_time == pytest.approx(10.0)
    assert renderer.calls
    assert renderer.calls[-1]["preview"] is True


def test_subtitle_render_grows_overlay_when_lazy_ruby_line_is_wider():
    root = FakeRoot()
    renderer = FakeRenderer()
    update_calls = []

    class Overlay:
        def __init__(self):
            self.root = root
            self.subtitle_canvas = renderer.canvas
            self.max_w = 800
            self.max_h = 120

        def update_geometry(self, width, height):
            update_calls.append((width, height))
            self.max_w = int(width)
            self.max_h = int(height)

    manager = SimpleNamespace(
        display_data=[
            (
                "\u305d\u308c\u306f\n\u5e38\u306b\u65b0\u3057\u3044\u8840\u6db2\u304c\u6d41\u308c\u3066\u304f\u308b\u5834\u6240",
                496.078,
                [("\u305d\u308c\u306f", None)],
                [("\u5e38", "\u3064\u306d"), ("\u306b\u65b0\u3057\u3044\u8840\u6db2\u304c\u6d41\u308c\u3066\u304f\u308b\u5834\u6240", None)],
            )
        ],
        display_start_times=[496.078],
        ensure_auto_ruby_for_index=lambda _idx: None,
        calculate_geometry_for_display_lines=lambda _top, _bottom: (960, 150),
        _auto_ruby_enabled=lambda: True,
        _auto_ruby_ready_indices={0},
    )
    settings = SimpleNamespace(
        root=root,
        _last_offset_value=0.0,
        time_overlay=FakeCanvas(),
        time_overlay_text="time",
        control_time_str=FakeVar(),
        update_time_overlay_position=lambda: None,
    )
    controller = SimpleNamespace(
        settings=settings,
        overlay=Overlay(),
        renderer=renderer,
        sub_manager=manager,
        current_time=496.100,
        total_duration=600.0,
        subtitle_timeout_job=None,
        last_rendered_index=None,
        last_subtitle_text="",
        last_subtitle_raw="",
        subtitle_deleted=False,
        subtitles_user_hidden=False,
        slider_dragging=False,
        _shutting_down=False,
        _defer_auto_ruby_once=False,
        entry_editing=False,
        hide_subtitles_ms=5000,
        _last_time_overlay_text=None,
        _get_display_start_times=lambda: manager.display_start_times,
        _record_perf_sample=lambda *_args, **_kwargs: None,
        _hide_subtitles_temporarily=lambda: None,
    )
    navigation = SubtitleNavigationController(controller)

    navigation._update_subtitle_display(force=True)

    assert update_calls == [(960, 150)]
    assert controller.overlay.max_w == 960
    assert renderer.calls
    assert renderer.calls[-1]["bottom"] == manager.display_data[0][3]


def test_hidden_subtitle_reappears_on_next_cue():
    controller, navigation, renderer = make_slider_controller()
    controller.current_time = 0.1

    navigation._update_subtitle_display(force=True, allow_auto_ruby=False)
    assert renderer.calls[-1]["top"] == [("first", "first-ruby")]

    navigation.toggle_subtitle_visibility()
    assert controller.subtitles_user_hidden is True
    assert controller.subtitles_hidden_until_index == 0

    navigation._update_subtitle_display(allow_auto_ruby=False)
    assert controller.subtitles_user_hidden is True
    assert renderer.calls[-1]["top"] == [("first", "first-ruby")]

    controller.current_time = 10.1
    navigation._update_subtitle_display(allow_auto_ruby=False)

    assert controller.subtitles_user_hidden is False
    assert controller.subtitles_hidden_until_index is None
    assert renderer.calls[-1]["top"] == [("second", "second-ruby")]


def test_episode_switch_resets_time_and_schedules_preload():
    preload_calls = []

    manager = SimpleNamespace(
        current_episode=2,
        current_season=1,
        change_episode=lambda action, *args: (1, 2),
        get_episode_nav_state=lambda: (True, True, False),
        get_episode_dropdown_values=lambda: ["1", "2"],
        get_total_duration=lambda: 120.0,
        get_current_season=lambda: 1,
        get_current_episode=lambda: 2,
        get_anime_name=lambda: "Anime",
        get_subtitle_geometry=lambda: (800, 120),
        display_data=[("line", 0.0, [("line", None)], [])],
        display_start_times=[0.0],
        schedule_episode_preload_around_current=lambda: preload_calls.append("preload"),
    )
    root = FakeRoot()
    controller = SimpleNamespace(
        sub_manager=manager,
        settings=SimpleNamespace(
            episode_var=FakeVar("2"),
            set_episode_nav_state=lambda **_kwargs: None,
            set_episode_values=lambda _values: None,
            set_total_duration=lambda value: setattr(controller, "total_duration", value),
            root=root,
            _last_offset_value=0.0,
        ),
        overlay=SimpleNamespace(update_geometry=lambda *_args: None, subtitle_canvas=FakeCanvas()),
        renderer=FakeRenderer(),
        default_start_time=0.0,
        current_time=44.0,
        total_duration=0.0,
        _defer_auto_ruby_once=False,
        last_subtitle_text="old",
        playback=SimpleNamespace(set_current_time=lambda value: setattr(controller, "current_time", float(value))),
        ocr_controller=SimpleNamespace(_schedule_ocr_time_jump=lambda _reason: None),
        _record_perf_sample=lambda *_args, **_kwargs: None,
    )

    EpisodeController(controller).change_episode("set")

    assert controller.current_time == pytest.approx(0.0)
    assert controller.total_duration == pytest.approx(120.0)
    assert controller._defer_auto_ruby_once is True
    assert preload_calls == ["preload"]


def test_episode_switch_restores_saved_episode_time():
    saved_key = (
        "v1|anime=anime|s=1|e=2|path="
        "d:/tobi/programme/vscode_projects/japanesesubtitles/subs/anime/show.s01e02.srt"
    )

    class Config:
        def __init__(self):
            self.values = {
                "START_EPISODES_AT_DEFAULT_TIME": False,
                "EPISODE_TIME_POSITIONS": {saved_key: 37.5},
            }
            self.saved = []

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value
            self.saved.append((key, value))

    class Manager:
        def __init__(self):
            self.current_episode = 1
            self.current_season = 1
            self.srt_file = r"D:\Tobi\Programme\VSCode_projects\JapaneseSubtitles\subs\Anime\Show.S01E01.srt"
            self.display_data = [("line", 0.0, [("line", None)], [])]
            self.display_start_times = [0.0]

        def change_episode(self, _action, *_args):
            self.current_episode = 2
            self.srt_file = r"D:\Tobi\Programme\VSCode_projects\JapaneseSubtitles\subs\Anime\Show.S01E02.srt"
            return 1, 2

        def get_episode_nav_state(self):
            return True, True, False

        def get_episode_dropdown_values(self):
            return ["1", "2"]

        def get_total_duration(self):
            return 120.0

        def get_current_season(self):
            return self.current_season

        def get_current_episode(self):
            return self.current_episode

        def get_current_global(self):
            return None

        def get_anime_name(self):
            return "Anime"

        def get_subtitle_geometry(self):
            return 800, 120

        def schedule_episode_preload_around_current(self):
            pass

    manager = Manager()
    root = FakeRoot()
    config = Config()
    controller = SimpleNamespace(
        config=config,
        sub_manager=manager,
        settings=SimpleNamespace(
            episode_var=FakeVar("2"),
            set_episode_nav_state=lambda **_kwargs: None,
            set_episode_values=lambda _values: None,
            set_total_duration=lambda value: setattr(controller, "total_duration", value),
            root=root,
            _last_offset_value=0.0,
        ),
        overlay=SimpleNamespace(update_geometry=lambda *_args: None, subtitle_canvas=FakeCanvas()),
        renderer=FakeRenderer(),
        default_start_time=0.0,
        current_time=12.0,
        playing=False,
        total_duration=120.0,
        _defer_auto_ruby_once=False,
        last_subtitle_text="old",
        playback=SimpleNamespace(set_current_time=lambda value: setattr(controller, "current_time", float(value))),
        ocr_controller=SimpleNamespace(_schedule_ocr_time_jump=lambda _reason: None),
        _record_perf_sample=lambda *_args, **_kwargs: None,
    )

    EpisodeController(controller).change_episode("set")

    assert controller.current_time == pytest.approx(37.5)
    assert config.values["EPISODE_TIME_POSITIONS"]


def test_episode_switch_default_start_toggle_ignores_saved_time():
    class Config:
        def __init__(self):
            self.values = {
                "START_EPISODES_AT_DEFAULT_TIME": True,
                "EPISODE_TIME_POSITIONS": {"anything": 37.5},
            }

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    manager = SimpleNamespace(
        current_episode=2,
        current_season=1,
        srt_file=r"D:\subs\Anime\Show.S01E02.srt",
        change_episode=lambda action, *args: (1, 2),
        get_episode_nav_state=lambda: (True, True, False),
        get_episode_dropdown_values=lambda: ["1", "2"],
        get_total_duration=lambda: 120.0,
        get_current_season=lambda: 1,
        get_current_episode=lambda: 2,
        get_current_global=lambda: None,
        get_anime_name=lambda: "Anime",
        get_subtitle_geometry=lambda: (800, 120),
        display_data=[("line", 0.0, [("line", None)], [])],
        display_start_times=[0.0],
        schedule_episode_preload_around_current=lambda: None,
    )
    controller = SimpleNamespace(
        config=Config(),
        sub_manager=manager,
        settings=SimpleNamespace(
            episode_var=FakeVar("2"),
            set_episode_nav_state=lambda **_kwargs: None,
            set_episode_values=lambda _values: None,
            set_total_duration=lambda value: setattr(controller, "total_duration", value),
            root=FakeRoot(),
            _last_offset_value=0.0,
        ),
        overlay=SimpleNamespace(update_geometry=lambda *_args: None, subtitle_canvas=FakeCanvas()),
        renderer=FakeRenderer(),
        default_start_time=8.0,
        current_time=12.0,
        playing=False,
        total_duration=120.0,
        _defer_auto_ruby_once=False,
        last_subtitle_text="old",
        playback=SimpleNamespace(set_current_time=lambda value: setattr(controller, "current_time", float(value))),
        ocr_controller=SimpleNamespace(_schedule_ocr_time_jump=lambda _reason: None),
        _record_perf_sample=lambda *_args, **_kwargs: None,
    )

    EpisodeController(controller).change_episode("set")

    assert controller.current_time == pytest.approx(8.0)


def test_popup_add_selection_callback_flow():
    calls = []

    popup = object.__new__(CopyPopup)
    popup._entry_widget = SimpleNamespace(get=lambda start, end: " 選択 ")
    popup._on_add_anki = lambda **kwargs: calls.append(kwargs)

    class Root:
        def after(self, _delay, callback):
            callback()

    fake_popup = SimpleNamespace(after=Root().after)
    popup._add_selection_to_anki_from_popup(fake_popup, "字幕")

    assert calls == [{"selected_text": "選択", "subtitle_text": "字幕"}]


def test_popup_hotkey_add_requires_pointer_inside_and_selection():
    calls = []

    class Root:
        def after(self, _delay, callback):
            callback()

    popup = object.__new__(CopyPopup)
    popup._popup = SimpleNamespace(winfo_exists=lambda: True, after=Root().after)
    popup._line_segments = [[("\u5b57\u5e55", None)]]
    popup._get_selected_text = lambda: "\u9078\u629e"
    popup._pointer_inside_window = lambda _window: True
    popup._on_add_anki = lambda **kwargs: calls.append(kwargs)

    assert popup.add_selected_to_anki_if_pointer_inside() is True
    assert calls == [{"selected_text": "\u9078\u629e", "subtitle_text": "\u5b57\u5e55"}]

    popup._pointer_inside_window = lambda _window: False
    assert popup.add_selected_to_anki_if_pointer_inside() is False
    assert len(calls) == 1

    popup._pointer_inside_window = lambda _window: True
    popup._get_selected_text = lambda: ""
    assert popup.add_selected_to_anki_if_pointer_inside() is False
    assert len(calls) == 1


def test_popup_inline_ruby_parser_uses_trailing_kanji_or_katakana_base():
    popup = object.__new__(CopyPopup)

    assert popup._parse_inline_ruby(
        "\u4e57[\u306e]\u308a\u63db[\u304b]\u3048\u308b "
        "\u3042\u3042 \u30d1\u30e9\u30b5\u30a4\u30c8\u3067\u5341\u5206[\u3058\u3085\u3046\u3076\u3093]\u3060"
    ) == [
        [
            ("\u4e57", "\u306e"),
            ("\u308a", None),
            ("\u63db", "\u304b"),
            ("\u3048\u308b \u3042\u3042 \u30d1\u30e9\u30b5\u30a4\u30c8\u3067", None),
            ("\u5341\u5206", "\u3058\u3085\u3046\u3076\u3093"),
            ("\u3060", None),
        ]
    ]


def test_popup_close_is_cancelled_when_pointer_is_inside():
    class PopupWindow:
        def __init__(self):
            self.destroyed = False

        def winfo_exists(self):
            return True

        def destroy(self):
            self.destroyed = True

    window = PopupWindow()
    popup = object.__new__(CopyPopup)
    popup._popup = window
    popup._close_job = "job"
    popup._pointer_inside_window = lambda _window: True
    popup._destroy_hover_ruby_window = lambda: None
    popup._reset_popup_state = lambda: None

    popup._close(window)

    assert window.destroyed is False
    assert popup._popup is window
    assert popup._close_job is None


def test_shutdown_sets_event_runs_cleanup_and_destroys_root():
    calls = []
    root = FakeRoot()
    event = SimpleNamespace(set=lambda: calls.append("event"))

    controller = object.__new__(SubtitleController)
    controller._shutting_down = False
    controller._shutdown_event = event
    controller.settings = SimpleNamespace(root=root)
    controller._stop_listeners_and_jobs = lambda: calls.append("stop")
    controller._close_auxiliary_windows = lambda: calls.append("windows")
    controller._shutdown_background_services = lambda: calls.append("services")

    controller.shutdown(destroy_root=True, save_state=False)

    assert controller._shutting_down is True
    assert calls == ["event", "stop", "windows", "services"]
    assert root.destroyed is True
