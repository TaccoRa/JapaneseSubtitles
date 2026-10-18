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
    sync._set_status = lambda value: None
    sync.sent = []
    sync.bridge = SimpleNamespace(send=sync.sent.append)
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
