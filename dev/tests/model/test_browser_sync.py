import hashlib
from pathlib import Path
import json
import time

import pytest
from model.browser_protocol import PlaybackSnapshot, MediaIdentity
from model.browser_bridge import BrowserBridge
from model.timing_map import TimingMap, TimingMapCache
from model.subtitle_alignment import align_intervals, parse_intervals


def snapshot(**changes):
    value = dict(identity=dict(tabId=2, documentId="document", frameId=3, videoId="video"),
                 sequence=1, currentTime=100, duration=1000, paused=False, buffering=False,
                 seeking=False, rate=1.5, fingerprint="episode")
    return PlaybackSnapshot.read(value | changes, now=10)


def test_clock_interpolates_only_fresh_playing_observations():
    assert snapshot().time_at(10.1) == pytest.approx(100.15)
    assert snapshot().time_at(11) == 100
    assert snapshot(paused=True).time_at(10.1) == 100
    assert snapshot(buffering=True).time_at(10.1) == 100
    assert snapshot(seeking=True).time_at(10.1) == 100


def test_reject_nonfinite_clock():
    with pytest.raises(ValueError):
        snapshot(currentTime=float("nan"))


def test_disconnected_send_uses_the_recoverable_connection_error():
    from types import SimpleNamespace
    bridge = BrowserBridge("test-secret")
    def closed(_payload):
        raise OSError("connection closed during send")
    bridge._peer = SimpleNamespace(send=closed)
    with pytest.raises(ConnectionError):
        bridge.send({"type": "TimingMap"})


def test_bridge_http_readiness_contains_no_credentials_and_does_not_pair():
    import urllib.request
    bridge = BrowserBridge("do-not-expose-this-secret", port=0)
    bridge.start()
    assert bridge.ready.wait(3) and not bridge.error
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{bridge.port}/health", timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert json.load(response) == {"service": "SubtitlePlayer", "version": 1}
        assert bridge.events.empty() and not bridge.trusted_origins
    finally:
        bridge.close()


def test_bridge_preserves_arrival_time_through_a_delayed_ui_queue(monkeypatch):
    import model.browser_bridge as module
    bridge = BrowserBridge("code")
    monkeypatch.setattr(module.time, "monotonic", lambda: 10)
    bridge._emit({"type": "PlaybackSnapshot", "_received_monotonic": 999})
    event = bridge.events.get_nowait()
    assert event["_received_monotonic"] == 10


def test_bridge_closing_releases_port_and_can_be_restarted():
    bridge = BrowserBridge("secret", port=0)
    bridge.start()
    assert bridge.ready.wait(3) and not bridge.error
    port = bridge.port
    bridge.close()
    assert not bridge._thread.is_alive()
    replacement = BrowserBridge("other", port=port)
    try:
        replacement.start()
        assert replacement.ready.wait(3) and not replacement.error
    finally:
        replacement.close()
    bridge.start()
    try:
        assert bridge.ready.wait(3) and not bridge.error
    finally:
        bridge.close()


def test_busy_port_is_explained_and_retry_succeeds_after_release():
    import socket
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        bridge = BrowserBridge("secret", port=occupied.getsockname()[1])
        bridge.start()
        assert bridge.ready.wait(3)
        bridge.close()
        assert "Port" in bridge.error and "Retry connection" in bridge.error
    bridge.start()
    try:
        assert bridge.ready.wait(3) and not bridge.error
    finally:
        bridge.close()


def test_single_instance_lock_prevents_duplicate_launch_and_releases(tmp_path):
    from model.single_instance import SingleInstance
    one, two = SingleInstance(tmp_path / "instance"), SingleInstance(tmp_path / "instance")
    try:
        assert one.acquire()
        assert not two.acquire()
        one.close()
        assert two.acquire()
    finally:
        one.close()
        two.close()


def test_rendered_captions_are_evidence_and_do_not_replace_full_reference():
    from controller.browser_sync_controller import BrowserSyncController
    sync = object.__new__(BrowserSyncController)
    sync.identity = MediaIdentity(1, "document", 2, "video")
    sync._reference_kind = "none"
    sync._caption_last = None
    sync._observed_intervals = []
    aligned = []
    sync._align = lambda: aligned.append(sync._reference)
    for i in range(13):
        sync._event(dict(type="CaptionObservation", identity=sync.identity.wire(), time=i*5, text=f"Caption {i}"))
    assert len(aligned) == 1 and len(aligned[0]) == 12
    sync._reference_kind = "full"
    sync._reference = ((1, 2),)
    sync._event(dict(type="CaptionObservation", identity=sync.identity.wire(), time=70, text="new"))
    assert sync._reference == ((1, 2),)


def test_piecewise_map_keeps_gap_and_original_times(tmp_path):
    source = ((10, 12), (20, 22), (40, 42))
    mapping = TimingMap("source", "media", "reference", source, ((11, 13), (21, 23), (32.784, 34.784)), (True, True, False))
    assert mapping.to_source(33) == pytest.approx(40.216)
    assert mapping.to_source(28) == -1
    assert mapping.to_video(40) == 32.784
    assert mapping.capture_interval(0, .5, .5) == (10.5, 13.5)
    with pytest.raises(ValueError, match="uncertain"):
        mapping.capture_interval(2)
    cache = TimingMapCache(tmp_path)
    cache.save(mapping)
    assert cache.load("source", "media", "reference") == mapping
    assert cache.load("source", "other", "reference") is None
    assert mapping.source == source


def test_measured_episode_split_preserves_source_bytes():
    directory = Path(__file__).resolve().parents[2] / "reviews/subtitleplayer-2026-10-18"
    source = directory / "source-jp.srt"
    before = source.read_bytes()
    intervals = parse_intervals(before.decode("utf-8-sig"))
    reference = parse_intervals((directory / "source-en.vtt").read_text("utf-8-sig"))
    result = align_intervals(intervals, reference, hashlib.sha256(before).hexdigest(), "episode")
    assert result.video[0][0] - result.source[0][0] == pytest.approx(1, abs=.1)
    assert result.video[-1][0] - result.source[-1][0] == pytest.approx(-7.216, abs=.1)
    assert result.confidence[0] and result.confidence[-1]
    assert source.read_bytes() == before


def test_alignment_refuses_sparse_reference():
    with pytest.raises(ValueError):
        align_intervals(((1, 2),), ((2, 3),), "source", "media")


def test_bridge_requires_token_and_routes_exact_identity():
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed
    bridge = BrowserBridge("a" * 32, port=0)
    bridge.start()
    assert bridge.ready.wait(3) and not bridge.error
    url = f"ws://127.0.0.1:{bridge.port}"
    try:
        with connect(url) as bad:
            bad.send(json.dumps(dict(type="Hello", version=1, token="wrong")))
            with pytest.raises(ConnectionClosed):
                bad.recv(timeout=2)
        with connect(url) as peer:
            peer.send(json.dumps(dict(type="Hello", version=1, token="a" * 32)))
            assert json.loads(peer.recv(timeout=2))["type"] == "Welcome"
            identity = MediaIdentity(2, "doc", 4, "video")
            operation = bridge.command(identity, "pause")
            message = json.loads(peer.recv(timeout=2))
            assert message["identity"] == identity.wire()
            assert message["operationId"] == operation
    finally:
        bridge.close()


def test_automatic_pairing_requires_first_approval_and_remembers_extension():
    from websockets.sync.client import connect
    bridge = BrowserBridge("test-secret", port=0)
    bridge.start()
    assert bridge.ready.wait(3)
    origin = "chrome-extension://" + "a" * 32
    try:
        with connect(f"ws://127.0.0.1:{bridge.port}", origin=origin) as peer:
            peer.send(json.dumps(dict(type="Hello", version=1, token="", pair=True)))
            request = bridge.events.get(timeout=2)
            assert request["type"] == "PairingRequested"
            bridge.approve_pairing(request["requestId"], True)
            assert json.loads(peer.recv(timeout=2))["token"] == "test-secret"
        deadline = time.monotonic() + 2
        while bridge._peer is not None and time.monotonic() < deadline:
            time.sleep(.01)
        with connect(f"ws://127.0.0.1:{bridge.port}", origin=origin) as peer:
            peer.send(json.dumps(dict(type="Hello", version=1, token="", pair=True)))
            assert json.loads(peer.recv(timeout=2))["type"] == "Welcome"
    finally:
        bridge.close()


@pytest.mark.parametrize("origin", [None, "https://video.test"])
def test_ordinary_pages_and_unidentified_clients_cannot_auto_pair(origin):
    from websockets.sync.client import connect
    from websockets.exceptions import ConnectionClosed
    bridge = BrowserBridge("test-secret", port=0)
    bridge.start()
    assert bridge.ready.wait(3)
    try:
        with connect(f"ws://127.0.0.1:{bridge.port}", origin=origin) as peer:
            # The server may reject an invalid origin before Hello is sent.
            with pytest.raises(ConnectionClosed) as rejected:
                peer.send(json.dumps(dict(type="Hello", version=1, token="", pair=True)))
                peer.recv(timeout=2)
            assert rejected.value.rcvd.code == 1008
        assert bridge.events.empty()
    finally:
        bridge.close()
