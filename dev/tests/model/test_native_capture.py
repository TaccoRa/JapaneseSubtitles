import base64
import io
import wave
from types import SimpleNamespace

import pytest
from model.anki_client import AnkiClient
from model.capture_session import CaptureSession, FrozenActionTarget
from model.browser_protocol import PlaybackSnapshot, MediaIdentity
from model.timing_map import TimingMap
from controller.browser_sync_controller import BrowserSyncController
from model.audio_alignment import AudioAnchorAligner


def wav_audio():
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1); writer.setsampwidth(2); writer.setframerate(48000)
        writer.writeframes(b"\0\0" * 4800)
    return base64.b64encode(output.getvalue()).decode()


def test_native_attachment_retries_one_note_and_preserves_content():
    client = object.__new__(AnkiClient)
    client.sound_field, client.image_field = "Sound", "Image"
    fields = {name: {"value": value} for name, value in dict(Front="日本語", Definition="keep", SentenceJA="same sentence", Sound="", Image="").items()}
    calls = []
    def invoke(action, params):
        calls.append((action, params))
        if action == "notesInfo":
            assert params == {"notes": [99]}
            return [{"noteId": 99, "fields": fields}]
        if action == "storeMediaFile":
            return params["filename"]
        if action == "updateNoteFields":
            assert params["note"]["id"] == 99
            assert set(params["note"]["fields"]) == {"Sound", "Image"}
            fields.update({name: {"value": value} for name, value in params["note"]["fields"].items()})
    client._invoke = invoke
    image = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"test-image").decode()
    first = client.attach_native_capture(99, "a" * 32, wav_audio(), image)
    second = client.attach_native_capture(99, "a" * 32, wav_audio(), image)
    assert first == second
    assert fields["Front"]["value"] == "日本語" and fields["Definition"]["value"] == "keep"
    assert not any(action == "addNote" for action, _ in calls)
    assert first["Sound"].endswith(".wav]")


def test_cancelled_native_attachment_never_updates_fields():
    client = object.__new__(AnkiClient)
    client.sound_field, client.image_field = "Sound", "Image"
    actions = []
    client._invoke = lambda action, params: actions.append(action) or [{"fields": {"Sound": {}, "Image": {}}}]
    with pytest.raises(RuntimeError, match="cancelled"):
        client.attach_native_capture(99, "a" * 32, wav_audio(), base64.b64encode(b"\x89PNG\r\n\x1a\n").decode(), is_cancelled=lambda: True)
    assert actions == ["notesInfo"]


def browser():
    sync = object.__new__(BrowserSyncController)
    identity = dict(tabId=1, documentId="doc", frameId=3, videoId="video")
    sync.snapshot = PlaybackSnapshot.read(dict(identity=identity, sequence=1, currentTime=20, duration=100,
        paused=True, buffering=False, rate=1, fingerprint="episode"))
    sync.identity = MediaIdentity.read(identity)
    sync.following = sync._connected = True
    sync._native_enabled = True
    sync.controller = SimpleNamespace(config=SimpleNamespace(get=lambda key: {"CAPTURE_BACKEND": "native", "POST_CAPTURE_PLAYBACK": "play", "AUDIO_PADDING": 500}.get(key)))
    sync.mapping = TimingMap("source", "episode", "reference", ((10, 12), (30, 32)), ((11, 13), (22.784, 24.784)), (True, True))
    sync._capture = None
    sync._trim = 0.0
    sync._operations = {}
    sync._failure_pause = None
    sync._commands = {}
    sync._command_callbacks = {}
    sync._pending_seek = None
    sync._set_status = lambda value: None
    sync.sent = []
    sync.bridge = SimpleNamespace(send=sync.sent.append, command=lambda identity, action, **kwargs: "command")
    return sync


def test_capture_freezes_map_and_rejects_competing_recording():
    sync = browser()
    session = CaptureSession(FrozenActionTarget.create(selected_text="語", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=1)))
    sync.freeze_capture(session)
    version = session.timing_version
    sync.mapping = TimingMap("source", "episode", "new", ((10, 12), (30, 32)), ((12, 14), (24, 26)), (True, True))
    sync.capture(session, 123)
    assert sync.sent[0]["start"] == pytest.approx(22.784)
    assert sync.sent[0]["end"] == pytest.approx(25.284)
    assert sync.sent[0]["timingVersion"] == version
    assert sync.sent[0]["noteId"] == 123
    with pytest.raises(RuntimeError, match="progress"):
        sync.capture(session, 124)


def test_capture_refuses_replaced_video():
    sync = browser()
    session = CaptureSession(FrozenActionTarget.create(selected_text="語", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    sync.freeze_capture(session)
    sync.identity = MediaIdentity(1, "replacement-doc", 3, "video")
    with pytest.raises(ValueError, match="changed"):
        sync.capture(session, 123)
    assert not sync.sent


def test_capture_preflight_requires_the_browser_recording_gesture_before_adding_a_note():
    sync = browser()
    sync._native_enabled = False
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    with pytest.raises(ValueError, match="Enable native recording"):
        sync.freeze_capture(session)
    assert not sync.sent


@pytest.mark.parametrize("note_first", [True, False])
def test_parallel_capture_joins_both_completion_orders_once(tmp_path, note_first):
    import json
    sync = browser()
    sync._pending_directory, sync._pending_media = tmp_path, {}
    attached = []
    def attach(session, result):
        session.phase = "pending_media"
        attached.append((session.target_note_id, result["audio"], result["image"]))
    sync._attach_media = attach
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    sync.freeze_capture(session); sync.capture(session)
    assert sync.sent[0]["noteId"] is None
    if note_first:
        sync.bind_capture_note(session, 123)
    result = dict(type="CaptureResult", ok=True, operationId=session.id, identity=session.media_identity, audio="audio", image="image")
    sync._event(result)
    if not note_first:
        assert not attached and session.phase == "waiting_note"
        assert json.loads((tmp_path / (session.id + ".json")).read_text())["noteId"] is None
        sync.bind_capture_note(session, 123)
    assert attached == [(123, "audio", "image")]
    sync.bind_capture_note(session, 123); sync._event(result)
    assert len(attached) == 1
    with pytest.raises(ValueError, match="exact"):
        sync.bind_capture_note(session, 999)


def test_parallel_capture_failure_does_not_attach_to_a_late_note():
    sync = browser()
    outcomes = []
    sync._finish_native = lambda session, ok: outcomes.append((session.target_note_id, ok))
    sync.report_error = lambda value: None
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    sync.freeze_capture(session); sync.capture(session)
    sync._event(dict(type="CaptureResult", ok=False, operationId=session.id, error="Recording cancelled"))
    sync.bind_capture_note(session, 123)
    assert outcomes == [(None, False), (123, False)]
    assert not sync._capture and session.phase == "failed" and not session.native_result
    assert not any(message["type"] == "CaptureCommit" for message in sync.sent)


def test_failure_before_note_creation_notifies_late_note_once_and_releases_media():
    sync = browser(); sync._finished_operations = set()
    notices, feedback, releases = [], [], []
    sync.controller.hotkey_controller = SimpleNamespace(notify_capture_media_finished=lambda *args: notices.append(args))
    sync.controller.anki_controller = SimpleNamespace(_finish_action_feedback_key=lambda *args: feedback.append(args))
    sync.controller.release_anki_pause_lease = lambda *args, **kwargs: releases.append(args)
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence"), phase="failed", native_result={"audio": "large recording"})
    sync._finish_native(session, False)
    assert session.native_result is None and not notices
    sync.bind_capture_note(session, 123); sync.bind_capture_note(session, 123)
    assert notices == [(123, False)] and len(feedback) == len(releases) == 1


def test_capture_always_requests_fresh_media():
    sync = browser()
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    sync.freeze_capture(session); sync.capture(session)
    assert "reuse" not in sync.sent[0]
    assert sync.sent[0]["start"] == 11



def test_next_recording_starts_while_previous_card_is_pending():
    sync = browser()
    def session():
        return CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    first = session(); sync.freeze_capture(first); sync.capture(first)
    with pytest.raises(ValueError, match="already in progress"):
        sync.freeze_capture(session())
    sync._event(dict(type="RecordingEnded", operationId=first.id))
    second = session(); sync.freeze_capture(second); sync.capture(second)
    assert sync._capture is second and first.id in sync._operations



@pytest.mark.parametrize("recording_finished", [True, False])
def test_card_creation_failure_cancels_parallel_capture_without_playing(tmp_path, recording_finished):
    import json
    sync = browser()
    sync._pending_directory, sync._pending_media = tmp_path, {}
    sync._retry_button = SimpleNamespace(pack=lambda **kwargs: None)
    sync.bridge.command = lambda identity, action: sync.sent.append({"type": "PlayerCommand", "action": action})
    sync._finish_native = lambda *args: None
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    sync.freeze_capture(session); sync.capture(session)
    if recording_finished:
        sync._event(dict(type="CaptureResult", ok=True, operationId=session.id, identity=session.media_identity, audio="audio", image="image"))
    sync.capture_note_failed(session)
    assert session.cancelled and sync._capture is None
    assert sync.sent[-1] == {"type": "PlayerCommand", "action": "abort"}
    assert not any(message.get("action") == "play" for message in sync.sent)
    if recording_finished:
        saved = json.loads((tmp_path / (session.id + ".json")).read_text())
        assert saved["noteId"] is None and saved["media"]["audio"] == "audio"


def test_local_audio_anchor_fit_handles_drift_and_uncertain_extrapolation(tmp_path):
    source = tuple((i * 10, i * 10 + 2) for i in range(12))
    aligner = AudioAnchorAligner(tmp_path, source, ["text"] * 12, "source", "episode")
    aligner.anchors = {i: (i * 10, i * 10 * 1.002 + 1.5) for i in range(1, 10)}
    mapping = aligner.fit()
    assert mapping.video[8][0] == pytest.approx(81.66)
    assert mapping.confidence[8] and not mapping.confidence[11]


def test_local_audio_anchor_fit_handles_corroborated_step(tmp_path):
    source = tuple((i * 10, i * 10 + 2) for i in range(16))
    aligner = AudioAnchorAligner(tmp_path, source, ["text"] * 16, "source", "episode")
    aligner.anchors = {i: (i * 10, i * 10 + (1 if i < 8 else -7.216)) for i in range(1, 15)}
    mapping = aligner.fit()
    assert mapping.video[2][0] == pytest.approx(21)
    assert mapping.video[12][0] == pytest.approx(112.784)


def test_note_creation_failure_after_boundary_keeps_encoding_without_aborting_another_clip(tmp_path):
    import json
    sync = browser()
    sync._pending_directory, sync._pending_media = tmp_path, {}
    outcomes = []
    sync.report_error = lambda message: None
    sync._terminal_media_failure = lambda session, error: outcomes.append(error)
    session = CaptureSession(FrozenActionTarget.create(selected_text="word", subtitle_text="sentence", sentence_key="sentence", cue_snapshot=SimpleNamespace(cue_index=0)))
    sync.freeze_capture(session); sync.capture(session)
    sync._event(dict(type="RecordingEnded", operationId=session.id))
    second = object(); sync._capture = second
    sync.capture_note_failed(session, "Anki creation uncertain")
    sync._event(dict(type="CaptureResult", ok=True, operationId=session.id, identity=session.media_identity, audio="saved audio", image="saved image"))
    assert sync._capture is second
    saved = json.loads((tmp_path / (session.id + ".json")).read_text())
    assert saved["noteId"] is None and saved["media"]["audio"] == "saved audio"
    assert outcomes == ["Anki creation uncertain"]
