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
