import array
import sys
import threading
import zipfile
from types import SimpleNamespace

import pytest

import utils
import controller.controller as controller_module
from controller.controller import SubtitleController
from controller.playback_controller import PlaybackController
from controller.subtitle_navigation import SubtitleNavigationController
from model.renderer import SubtitleRenderer
from model.voice_service import VoiceCommandService, VoiceDownloadCancelled, VoiceModelManager


class FakeConfig:
    def __init__(self, path, values=None):
        self.path = str(path)
        self.local_path = str(path).replace("config.json", "config.local.json")
        self.values = dict(values or {})

    def get(self, key):
        return self.values.get(key)


def test_indexed_capture_words_use_ordered_noncompound_subtitle_tokens():
    renderer = object.__new__(SubtitleRenderer)
    renderer._word_regions = [
        {"base": "第一関門", "compound": True, "line_position": "first", "sentence_lookup": "第一関門", "char_start": 0, "char_end": 4},
        {"base": "第", "line_position": "first", "sentence_lookup": "第一関門", "char_start": 0, "char_end": 1},
        {"base": "一", "line_position": "first", "sentence_lookup": "第一関門", "char_start": 1, "char_end": 2},
        {"base": "関門", "line_position": "first", "sentence_lookup": "第一関門", "char_start": 2, "char_end": 4},
        {"base": "クリア", "line_position": "second", "sentence_lookup": "クリア", "char_start": 0, "char_end": 3},
    ]

    assert renderer.subtitle_words_for_anki() == ["第", "一", "関門", "クリア"]


def test_final_voice_results_dispatch_once_during_cooldown(tmp_path):
    actions = []
    statuses = []
    service = VoiceCommandService(
        FakeConfig(tmp_path / "config.json"),
        action_callback=lambda action, phrase, count: actions.append((action, phrase, count)),
        status_callback=statuses.append,
    )
    phrase_map = {
        "subtitles play": "voice_play",
        "subtitles start": "voice_play",
    }
    fired = {}

    assert service.process_final_result(
        {"text": "Subtitles Play"},
        phrase_map=phrase_map,
        last_fired=fired,
        cooldown_sec=1.0,
        language="en",
        now=10.0,
    )
    assert not service.process_final_result(
        {"text": "subtitles play"},
        phrase_map=phrase_map,
        last_fired=fired,
        cooldown_sec=1.0,
        language="en",
        now=10.5,
    )
    assert not service.process_final_result(
        {"text": "subtitles start"},
        phrase_map=phrase_map,
        last_fired=fired,
        cooldown_sec=1.0,
        language="en",
        now=10.6,
    )
    assert service.process_final_result(
        {"text": "subtitles play"},
        phrase_map=phrase_map,
        last_fired=fired,
        cooldown_sec=1.0,
        language="en",
        now=11.1,
    )

    assert actions == [
        ("voice_play", "subtitles play", 1),
        ("voice_play", "subtitles play", 1),
    ]
    assert statuses[-1]["state"] == "recognized"


def test_first_final_voice_result_is_not_suppressed_near_zero(tmp_path):
    actions = []
    service = VoiceCommandService(
        FakeConfig(tmp_path / "config.json"),
        action_callback=lambda action, phrase, count: actions.append((action, phrase, count)),
    )

    assert service.process_final_result(
        {"text": "subtitles play"},
        phrase_map={"subtitles play": "voice_play"},
        last_fired={},
        cooldown_sec=1.0,
        language="en",
        now=0.1,
    )
    assert actions == [("voice_play", "subtitles play", 1)]


def test_nonmatching_final_voice_result_does_not_dispatch(tmp_path):
    actions = []
    service = VoiceCommandService(
        FakeConfig(tmp_path / "config.json"),
        action_callback=lambda action, phrase, count: actions.append((action, phrase, count)),
    )

    assert not service.process_final_result(
        {"text": "play"},
        phrase_map={"subtitles play": "voice_play"},
        last_fired={},
        cooldown_sec=1.0,
        language="en",
        now=10.0,
    )
    assert actions == []


def test_final_voice_result_dispatches_repeat_count(tmp_path):
    actions = []
    service = VoiceCommandService(
        FakeConfig(tmp_path / "config.json"),
        action_callback=lambda action, phrase, count: actions.append((action, phrase, count)),
    )

    assert service.process_final_result(
        {"text": "subtitles back five"},
        phrase_map={"subtitles back five": ("voice_go_back", 5)},
        last_fired={},
        cooldown_sec=1.0,
        language="en",
        now=10.0,
    )
    assert actions == [("voice_go_back", "subtitles back five", 5)]


def test_only_terminal_play_pause_phrases_dispatch_from_partials():
    phrase_map = {
        "subtitles play": "voice_play",
        "subtitles play now": "voice_play",
        "subtitles back": "voice_go_back",
    }

    assert not VoiceCommandService._is_fast_terminal_partial("subtitles play", phrase_map)
    assert not VoiceCommandService._is_fast_terminal_partial("subtitles back", phrase_map)
    assert VoiceCommandService._is_fast_terminal_partial(
        "subtitles play",
        {"subtitles play": "voice_play"},
    )


def test_managed_model_archive_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")

    destination = tmp_path / "extract"
    destination.mkdir()
    with zipfile.ZipFile(archive_path, "r") as archive:
        with pytest.raises(ValueError, match="unsafe path"):
            VoiceModelManager._safe_extract(archive, str(destination), threading.Event())

    assert not (tmp_path / "outside.txt").exists()


def test_managed_model_archive_honors_cancellation(tmp_path):
    archive_path = tmp_path / "model.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("model/am/final.mdl", "model")

    destination = tmp_path / "extract"
    destination.mkdir()
    cancelled = threading.Event()
    cancelled.set()
    with zipfile.ZipFile(archive_path, "r") as archive:
        with pytest.raises(VoiceDownloadCancelled):
            VoiceModelManager._safe_extract(archive, str(destination), cancelled)


def test_managed_model_validation_uses_required_vosk_files(tmp_path):
    model = tmp_path / "model"
    (model / "am").mkdir(parents=True)
    (model / "conf").mkdir(parents=True)
    (model / "am" / "final.mdl").write_bytes(b"model")
    (model / "conf" / "mfcc.conf").write_text("sample-frequency=16000", encoding="utf-8")

    assert VoiceModelManager._valid_model(str(model))


def test_model_publish_retries_transient_windows_directory_lock(monkeypatch):
    attempts = []

    def replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise PermissionError(5, "access denied")

    monkeypatch.setattr("model.voice_service.os.replace", replace)
    monkeypatch.setattr("model.voice_service.time.sleep", lambda _delay: None)

    VoiceModelManager._replace_directory_with_retry("source", "destination")
    assert len(attempts) == 3


def test_microphone_resolution_ignores_portaudio_trailing_name_space(monkeypatch):
    monkeypatch.setattr(
        VoiceCommandService,
        "list_input_devices",
        staticmethod(
            lambda: [
                {
                    "index": 1,
                    "name": "Microphone (Microsoft LifeCam ",
                    "host_api": "MME",
                    "default": True,
                    "sample_rate": 44100,
                }
            ]
        ),
    )

    resolved = VoiceCommandService._resolve_input_device(
        {"name": "Microphone (Microsoft LifeCam ", "host_api": "MME"}
    )
    assert resolved["index"] == 1


def test_microphone_level_uses_a_readable_dbfs_scale():
    silence = array.array("h", [0] * 128).tobytes()
    speech = array.array("h", [12000, -12000] * 64).tobytes()

    assert VoiceCommandService._int16_audio_level(silence) == {"level": 0.0, "peak": 0.0}
    measured = VoiceCommandService._int16_audio_level(speech)
    assert 0.8 < measured["level"] < 1.0
    assert measured["peak"] >= measured["level"]


def test_live_microphone_test_uses_same_host_api_output(monkeypatch):
    devices = {
        1: {
            "name": "Test microphone",
            "hostapi": 0,
            "max_input_channels": 1,
            "max_output_channels": 0,
            "default_samplerate": 44100,
        },
        4: {
            "name": "Test speakers",
            "hostapi": 0,
            "max_input_channels": 0,
            "max_output_channels": 2,
            "default_samplerate": 44100,
        },
    }
    opened = []

    class FakeRawStream:
        def __init__(self, **kwargs):
            opened.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[1, 4]),
        query_devices=lambda index: devices[index],
        query_hostapis=lambda index: {"default_output_device": 4},
        check_input_settings=lambda **_kwargs: None,
        check_output_settings=lambda **_kwargs: None,
        RawStream=FakeRawStream,
        RawInputStream=FakeRawStream,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)
    monkeypatch.setattr(
        VoiceCommandService,
        "_resolve_input_device",
        classmethod(
            lambda _cls, _requested: {
                "index": 1,
                "name": "Test microphone",
                "host_api": "Test API",
                "host_api_index": 0,
                "default": True,
                "sample_rate": 44100,
            }
        ),
    )
    stopped = threading.Event()
    events = []

    def on_event(event):
        events.append(event)
        if event.get("type") == "started":
            stopped.set()

    result = VoiceCommandService.run_input_device_test(
        {"name": "Test microphone", "host_api": "Test API"},
        stopped,
        monitor_getter=lambda: True,
        event_callback=on_event,
    )

    assert result["monitor_available"] is True
    assert result["output_device"]["name"] == "Test speakers"
    assert events[0]["type"] == "started"
    assert opened[0]["device"] == (1, 4)
    assert opened[0]["channels"] == (1, 1)
    audio = array.array("h", [1200, -1200]).tobytes()
    monitored = bytearray(len(audio))
    opened[0]["callback"](audio, monitored, 2, None, None)
    assert bytes(monitored) == audio


def test_window_target_matching_requires_saved_title_and_process(monkeypatch):
    windows = [
        {"hwnd": 10, "title": "ABSPlayer - Episode 1", "process": "chrome.exe", "class_name": "Chrome_WidgetWin_1"},
        {"hwnd": 20, "title": "Documentation", "process": "chrome.exe", "class_name": "Chrome_WidgetWin_1"},
    ]
    monkeypatch.setattr(utils, "list_visible_windows", lambda exclude_hwnds=None: windows)

    target = {
        "title_filter": "ABSPlayer",
        "process": "chrome.exe",
        "class_name": "Chrome_WidgetWin_1",
    }
    assert utils.find_window_target(target)["hwnd"] == 10
    assert utils.find_window_target({**target, "title_filter": "Missing"}) is None


def test_voice_video_target_prefers_current_anime_then_saved_title(monkeypatch):
    controller = object.__new__(SubtitleController)
    values = {
        "VOICE_PLAYBACK_TARGET": {"title_filter": "Saved Player", "process": "chrome.exe"},
        "LAST_DISPLAY_ANIME_NAME": "",
        "LAST_ANIME_NAME": "",
        "LAST_REMOTE_SEARCH_QUERY": "",
        "POST_ADD_CAPTURE_TARGET_TITLE": "Google Chrome",
    }
    controller.config = SimpleNamespace(get=values.get)
    controller.sub_manager = SimpleNamespace(
        get_display_anime_name=lambda: "Current Anime",
        get_anime_name=lambda: "Current Anime",
    )
    controller._app_window_hwnds = set()
    windows = [
        {"hwnd": 10, "title": "Saved Player - Google Chrome", "process": "chrome.exe"},
        {"hwnd": 20, "title": "Current Anime - Google Chrome", "process": "chrome.exe"},
    ]
    monkeypatch.setattr(controller_module, "list_visible_windows", lambda exclude_hwnds=None: windows)

    assert controller._resolve_voice_video_target()["hwnd"] == 20
    controller.sub_manager = SimpleNamespace(
        get_display_anime_name=lambda: "Wrong Anime",
        get_anime_name=lambda: "Wrong Anime",
    )
    assert controller._resolve_voice_video_target()["hwnd"] == 10


def test_reconfigure_ignores_non_recognition_settings(tmp_path, monkeypatch):
    config = FakeConfig(
        tmp_path / "config.json",
        {
            "VOICE_ENABLED": False,
            "VOICE_LANGUAGE": "en",
            "VOICE_PLAYBACK_TARGET": {"title_filter": "ABSPlayer"},
        },
    )
    service = VoiceCommandService(config, action_callback=lambda _action, _phrase, _count: None)
    service.start()
    starts = []
    monkeypatch.setattr(service, "start", lambda: starts.append(True))

    config.values["VOICE_PLAYBACK_TARGET"] = {"title_filter": "Another window"}
    assert service.reconfigure() is False
    assert starts == []

    config.values["VOICE_LANGUAGE"] = "de"
    assert service.reconfigure() is True
    assert starts == [True]


def test_voice_semantic_playback_and_subtitle_states_are_idempotent(monkeypatch):
    class Controller:
        playing = False
        subtitles_user_hidden = True

    controller = Controller()
    playback = PlaybackController(controller)
    playback_calls = []

    def toggle_play(event_time=None):
        playback_calls.append(event_time)
        controller.playing = not controller.playing

    playback.toggle_play = toggle_play
    assert playback.set_playing(False) is False
    assert playback.set_playing(True, event_time=3.0) is True
    assert playback.set_playing(True) is False
    assert playback_calls == [3.0]

    navigation = SubtitleNavigationController(controller)
    visibility_calls = []

    def toggle_visibility(self):
        visibility_calls.append(True)
        self.subtitles_user_hidden = not self.subtitles_user_hidden

    monkeypatch.setattr(SubtitleNavigationController, "toggle_subtitle_visibility", toggle_visibility)
    assert navigation.set_subtitle_visibility(False) is False
    assert navigation.set_subtitle_visibility(True) is True
    assert navigation.set_subtitle_visibility(True) is False
    assert visibility_calls == [True]


def test_external_hotkey_delivery_restores_foreground_on_success_and_failure(monkeypatch):
    target_window = {"hwnd": 20, "title": "ABSPlayer", "process": "chrome.exe"}
    calls = []
    monkeypatch.setattr(utils, "find_window_target", lambda target, exclude_hwnds=None: target_window)
    monkeypatch.setattr(utils, "get_foreground_root_hwnd", lambda: 10)
    monkeypatch.setattr(utils, "focus_windows_hwnd", lambda hwnd: calls.append(("focus", hwnd)) or True)
    monkeypatch.setattr(utils, "send_global_hotkey", lambda hotkey: calls.append(("send", hotkey)) or True)

    result = utils.send_hotkey_to_window({"title_filter": "ABSPlayer"}, "space")
    assert result["ok"] is True
    assert calls == [("focus", 20), ("send", "space"), ("focus", 10)]

    calls.clear()
    monkeypatch.setattr(utils, "send_global_hotkey", lambda hotkey: calls.append(("send", hotkey)) or False)
    result = utils.send_hotkey_to_window({"title_filter": "ABSPlayer"}, "space")
    assert result["ok"] is False
    assert result["reason"] == "hotkey_send_failed"
    assert calls == [("focus", 20), ("send", "space"), ("focus", 10)]


def test_external_hotkey_delivery_repeats_without_refocusing(monkeypatch):
    target_window = {"hwnd": 20, "title": "ABSPlayer", "process": "chrome.exe"}
    calls = []
    monkeypatch.setattr(utils, "find_window_target", lambda target, exclude_hwnds=None: target_window)
    monkeypatch.setattr(utils, "get_foreground_root_hwnd", lambda: 10)
    monkeypatch.setattr(utils, "focus_windows_hwnd", lambda hwnd: calls.append(("focus", hwnd)) or True)
    monkeypatch.setattr(utils, "send_global_hotkey", lambda hotkey: calls.append(("send", hotkey)) or True)
    monkeypatch.setattr(utils.time, "sleep", lambda _delay: None)

    result = utils.send_hotkey_to_window(
        {"title_filter": "ABSPlayer"},
        "left",
        repeat_count=3,
        repeat_interval_ms=120,
        before_send=lambda window: calls.append(("sync", window["hwnd"])),
    )

    assert result == {
        "ok": True,
        "reason": "sent",
        "window": target_window,
        "sent_count": 3,
    }
    assert calls == [
        ("focus", 20),
        ("sync", 20),
        ("send", "left"),
        ("send", "left"),
        ("send", "left"),
        ("focus", 10),
    ]
