"""Browser authority, timing trim and asynchronous operation regressions."""
import json
from types import SimpleNamespace as NS

import pytest

from controller.browser_sync_controller import BrowserSyncController
from controller.controller import SubtitleController
from controller.overlay_controller import OverlayController
from controller.playback_controller import PlaybackController
from model.native_settings import migrate
from model.subtitle_alignment import align_intervals
from test_native_capture import browser


def test_rapid_skips_accumulate_and_ack_never_sends_a_second_seek():
    sync = browser()
    sync.bridge.command = lambda identity, action, **kw: (sync.sent.append((action, kw)) or str(len(sync.sent)))
    sync.seek_relative(-1); sync.seek_relative(-1); sync.seek_relative(3)
    assert [kw["time"] for _, kw in sync.sent] == [19, 18, 21]
    sync._event(dict(type="CommandAck", operationId="1", ok=True))
    assert sync._pending_seek == ("3", 21)
    sync._event(dict(type="CommandAck", operationId="3", ok=True))
    assert sync._pending_seek is None and len(sync.sent) == 3


def test_voice_repetitions_use_skip_field_and_wait_for_ack():
    calls, done = [], []
    owner = NS(_shutting_down=False, settings=NS(_last_skip_value=1),
        _complete_voice_video_request=lambda req, ok: done.append(ok))
    owner.browser_sync = NS(following=True, snapshot=object(),
        seek_relative=lambda seconds, **kw: calls.append((seconds, kw["on_complete"])))
    SubtitleController._handle_voice_video_action(owner, {"kind": "seek", "direction": "back", "repeat_count": 3})
    assert calls[0][0] == -3 and not done
    calls[0][1](True)
    assert done == [True]


def test_hover_sends_one_pause_and_one_resume_without_keyboard_injection():
    calls = []
    owner = NS(subtitle_hover_pause_video=True, playing=True,
        browser_sync=NS(following=True, identity="video"),
        playback=NS(set_playing=calls.append))
    overlay = OverlayController(owner)
    overlay._trigger_background_video_space(paused=True)
    overlay._trigger_background_video_space(paused=True)
    owner.playing = False
    overlay._trigger_background_video_space(paused=False)
    overlay._trigger_background_video_space(paused=False)
    assert calls == [False, True]


def test_no_independent_clock_when_offline():
    owner = NS(playing=True, current_time=30, last_update=0, _update_loop_job="old")
    playback = PlaybackController(owner)
    assert playback._advance_playing_time_to_now(now=900) == 0
    playback.update_loop()
    assert owner.current_time == 30 and owner._update_loop_job is None


def test_trim_moves_mapping_and_capture_but_never_browser_time(tmp_path):
    sync = browser()
    sync.controller.settings = NS(offset_var=NS(set=lambda value: None))
    sync._trim_path = tmp_path / "trims.json"
    sync._trims, sync._trim_key = {}, "episode-a"
    sync._source = lambda: None
    original_time = sync.snapshot.current_time
    sync.set_trim(-.3)
    assert sync.mapping.video[0][0] == pytest.approx(10.7)
    assert sync.video_time(10) == pytest.approx(10.7)
    assert sync.snapshot.current_time == original_time and not sync.sent
    assert json.loads(sync._trim_path.read_text())["episode-a"] == -.3
    sync.set_trim(.4)
    assert sync.mapping.video[0][0] == pytest.approx(11.4)
    sync.set_trim(0)
    assert sync.mapping.video[0][0] == 11


def test_failure_pause_waits_for_active_clip_and_never_pauses_another_video():
    sync = browser()
    commands = []
    sync.command = lambda action: commands.append(action)
    sync._capture = object()
    sync._failure_pause = (sync.identity.wire(), sync.snapshot.fingerprint)
    sync._apply_failure_pause()
    assert not commands
    sync._capture = None
    sync._apply_failure_pause(); sync._apply_failure_pause()
    assert commands == ["pause"]

    sync._failure_pause = ({"tabId": 999}, "another-episode")
    sync._apply_failure_pause()
    assert commands == ["pause"]


def pollable(sync, tmp_path):
    import queue
    sync._closed = False
    sync._results = queue.Queue()
    sync._pending_media, sync._retry_at, sync._retry_count = {}, {}, {}
    sync._pending_directory = tmp_path
    sync._last_probe, sync._episode_problem = 0, None
    sync._send_recorder_config = lambda: None
    sync.bridge.events, sync.bridge.error = queue.Queue(), None
    sync.controller.settings = NS(root=NS(after=lambda *args: "scheduled"), slider=NS(config=lambda **kw: None))
    sync.controller.playback = NS(_set_play_button_state=lambda: None, _publish_current_time=lambda: None)
    sync.controller.slider_dragging = False
    return sync


def test_delayed_observation_probes_without_disconnecting_buffering_or_cancelling(tmp_path):
    from dataclasses import replace
    import time
    sync = pollable(browser(), tmp_path)
    sync.snapshot = replace(sync.snapshot, received=time.monotonic() - .6)
    sync._capture = object()
    statuses = []
    sync._set_status = statuses.append
    sync._poll(); sync._poll()
    assert sync._connected and sync._capture is not None
    assert len([m for m in sync.sent if m["type"] == "PlaybackProbe"]) == 1
    assert "delayed" in statuses[-1] and "disconnected" not in statuses[-1] and "buffering" not in statuses[-1]
    assert sync.controller.current_time == 20


def test_temporary_media_error_retries_same_note_then_terminal_failure_pauses_once(tmp_path, monkeypatch):
    import controller.browser_sync_controller as module
    from model.capture_session import CaptureSession, FrozenActionTarget
    now = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    sync = pollable(browser(), tmp_path)
    sync.following = False
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence"))
    session.set_target_note(123)
    session.media_identity, session.media_fingerprint = sync.identity.wire(), sync.snapshot.fingerprint
    sync._pending_media[session.id] = (session, {"audio": "saved", "image": "saved"})
    attempts, failures, pauses = [], [], []
    sync.report_error = lambda error: None
    sync._attach_media = lambda s, result: attempts.append(s.target_note_id)
    sync._finish_native = lambda s, ok: failures.append(ok)
    sync.command = pauses.append
    for delay in (1, 3, 10, 30):
        sync._results.put(("media", session.id, "Anki temporarily unavailable"))
        sync._poll()
        assert not failures and not pauses
        now[0] += delay
        sync._poll()
    assert attempts == [123] * 4
    sync._results.put(("media", session.id, "Anki still unavailable"))
    sync._poll(); sync._poll()
    assert failures == [False] and pauses == ["pause"]
    assert session.id in sync._pending_media


def test_retired_settings_are_archived_without_touching_vocabulary_or_profiles():
    old = {"CAPTURE_BACKEND": "asbplayer", "OCR_ENABLED": True, "EXTRA_OFFSET": 3,
        "VOICE_COMMANDS": {"add": "keep"}, "DEFAULT_SKIP": 1}
    new = migrate(old)
    assert new["ARCHIVED_PLAYBACK_SETTINGS"] == {k: old[k] for k in ("CAPTURE_BACKEND", "OCR_ENABLED", "EXTRA_OFFSET")}
    assert new["DEFAULT_SKIP"] == 1 and new["VOICE_COMMANDS"] == old["VOICE_COMMANDS"]
    assert migrate(new) == new and old["CAPTURE_BACKEND"] == "asbplayer"


def test_alignment_refines_late_onsets_even_when_ends_match(tmp_path, monkeypatch):
    # Solver deliberately returns an end-aligned answer 300 ms too late.
    import model.interval_alignment as solver
    monkeypatch.setattr(solver, "fit", lambda source, reference: {"cue_offsets": [1.3] * len(source)})
    helper = tmp_path / "absent.exe"
    source = tuple((i * 8 + 10, i * 8 + 12) for i in range(12))
    reference = tuple((a + 1, b + 1.3) for a, b in source)
    mapping = align_intervals(source, reference, "source", "episode", helper=helper)
    assert mapping.video[6][0] == pytest.approx(reference[6][0], abs=.05)
    assert any(mapping.confidence)


def test_cancelled_pending_media_is_persisted_without_retry_or_player_commands(tmp_path):
    from model.capture_session import CaptureSession, FrozenActionTarget
    sync = pollable(browser(), tmp_path)
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence"))
    session.set_target_note(123)
    sync._pending_media[session.id] = (session, {"audio": "saved", "image": "saved"})
    sync._retry_at[session.id] = 50
    sync._finish_native = lambda s, ok: None
    sync.cancel_pending(session)
    saved = json.loads((tmp_path / (session.id + ".json")).read_text())
    assert saved["cancelled"] and saved["noteId"] == 123
    assert session.id not in sync._retry_at
    assert not any(m["type"] == "PlayerCommand" for m in sync.sent)


def test_restored_media_waits_quietly_then_attaches_to_original_note(tmp_path):
    from model.capture_session import CaptureSession, FrozenActionTarget
    sync = pollable(browser(), tmp_path)
    sync.following = False
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence"))
    session.set_target_note(123)
    sync._restored_media = {session.id}
    available, attachments, failures = [False], [], []
    sync.controller.anki_controller = NS(submit_anki_work=lambda work: work())
    sync.controller.anki = NS(ping_quietly=lambda: available[0],
        attach_native_capture=lambda note, *args, **kw: attachments.append(note))
    sync.report_error = failures.append
    media = dict(audio={"data": "saved-audio"}, image={"data": "saved-image"})
    path = tmp_path / (session.id + ".json")
    sync._save_pending_media(session, media)
    original = path.read_bytes()
    for _ in range(3):
        sync._attach_media(session, media)
        sync._poll()
    assert not attachments and not failures and not sync._retry_count
    assert session.phase == "waiting_anki" and path.read_bytes() == original
    available[0] = True
    sync._attach_media(session, media)
    assert attachments == [123]
    assert sync._results.get_nowait() == ("media", session.id, None)
