"""Replay real caption evidence through the runtime, without touching a player."""
from dataclasses import replace
from pathlib import Path
import json
import queue
import time

import pytest
from model.alignment_preferences import AlignmentPreferences
from model.subtitle_alignment import parse_intervals
from model.timing_map import TimingMapCache, digest
from test_native_capture import browser
from test_native_playback import pollable

REVIEW = Path(__file__).resolve().parents[2] / "reviews/subtitleplayer-2026-10-18"


def runtime(tmp_path):
    sync = pollable(browser(), tmp_path)
    data = (REVIEW / "source-jp.srt").read_bytes()
    source = parse_intervals(data.decode("utf-8-sig"))
    sync.preferences = AlignmentPreferences(tmp_path / "prefs.json")
    sync.cache = TimingMapCache(tmp_path / "maps")
    sync._preference_media = lambda: "kimetsu-s1e5"
    sync._source_hash = digest(data)
    sync._source = lambda: (sync._source_hash, source)
    sync.alignment_mode = "captions"
    sync.mapping = None
    sync._generation, sync._analyzing = 1, False
    sync._alignment_attempt_key, sync._reference = None, ()
    sync._reference_kind, sync._reference_fingerprint = "none", None
    sync._caption_last, sync._observed_intervals = None, []
    sync._offset_region = sync._auto_offset = None
    sync.following = False
    return sync, source, data


def finish(sync):
    deadline = time.monotonic() + 8
    while sync._analyzing:
        sync._alignment_due = 0  # Exercise worker completion without waiting for debounce.
        sync._poll()
        if not sync._analyzing:
            break
        try:
            item = sync._results.get(timeout=.03)
        except queue.Empty:
            assert time.monotonic() < deadline, "Alignment worker did not finish"
            continue
        sync._results.put(item)
        sync._poll()
        assert time.monotonic() < deadline


def test_real_episode_detects_both_offsets_and_displays_untranslated_cues(tmp_path):
    sync, source, original = runtime(tmp_path)
    sync._event(dict(type="CaptionText", identity=sync.identity.wire(), fingerprint="episode", language="en",
                     text=(REVIEW / "source-en.vtt").read_text("utf-8-sig")))
    finish(sync)
    assert sync.mapping.video[0][0]-source[0][0] == pytest.approx(1, abs=.01)
    assert sync.mapping.video[-1][0]-source[-1][0] == pytest.approx(-7.216, abs=.01)
    for idx in (1, 135, 136, 137, 138, 264):
        mapped = sync.mapping.video[idx][0]
        assert sync.source_time(mapped+.1) == pytest.approx(source[idx][0]+.1)
        assert sync.video_time(source[idx][0]) == pytest.approx(mapped)
    assert not sync.mapping.confidence[136] and sync.mapping.preview[136]
    with pytest.raises(ValueError, match="uncertain"):
        sync.mapping.capture_interval(136)
    sync.controller.settings.offset_var = type("Var", (), {"set": lambda *args: None})()
    before = sync.snapshot
    sync.set_trim(-.25)
    assert sync.snapshot == before
    assert sync.video_time(source[1][0]) == pytest.approx(source[1][0]+.75)
    assert sync.video_time(source[138][0]) == pytest.approx(source[138][0]-7.466)
    assert sync.source_time(source[136][0]-7.466+.1) == pytest.approx(source[136][0]+.1)
    assert (REVIEW / "source-jp.srt").read_bytes() == original
    assert all(m["type"] == "TimingMap" for m in sync.sent)


def test_growing_reference_does_not_starve_valid_results(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from model.alignment_worker import calculate
    sync, source, original = runtime(tmp_path)
    reference = parse_intervals((REVIEW / "source-en.vtt").read_text("utf-8-sig"))
    jobs = []
    sync._alignment_worker = SimpleNamespace(submit=lambda *job: jobs.append(job))
    event = dict(type="CaptionIntervals", identity=sync.identity.wire(), fingerprint="episode", language="en")
    sync._event(event | dict(intervals=reference[:80]))
    sync._alignment_due = 0
    sync._start_pending_alignment()
    sync._event(event | dict(intervals=reference))
    identity, request = jobs.pop(0)
    sync._results.put(("alignment", identity, calculate(request)));sync._poll()
    assert sync.mapping is not None  # Earlier valid section appears immediately.
    assert sync._pending_alignment  # Full reference is next, without another competing worker.
    sync._alignment_due = 0
    sync._start_pending_alignment()
    identity, request = jobs.pop(0)
    sync._results.put(("alignment", identity, calculate(request)));sync._poll()
    assert sync.mapping.video[-1][0]-source[-1][0] == pytest.approx(-7.216, abs=.01)
    assert not any(m["type"] == "PlayerCommand" for m in sync.sent)


def test_pending_caption_changes_coalesce_and_manual_retry_keeps_current_map(tmp_path):
    from model.subtitle_alignment import normalized_reference
    sync, _, _ = runtime(tmp_path)
    reference = parse_intervals((REVIEW / "source-en.vtt").read_text("utf-8-sig"))
    event = dict(type="CaptionIntervals", identity=sync.identity.wire(), fingerprint="episode", language="en")
    sync._event(event | dict(intervals=reference[:80]))
    sync._event(event | dict(intervals=reference))
    assert sync._pending_alignment[1]["reference"] == normalized_reference(reference)
    assert getattr(sync, "_alignment_worker", None) is None
    previous = browser().mapping
    sync.mapping = previous
    sync.request_alignment()
    assert sync.mapping == previous
    assert sync._pending_alignment[1]["force"]
    assert [m["type"] for m in sync.sent] == ["RefreshCaptions"]
    finish(sync)
    assert sync.mapping.video[-1][0]-sync.mapping.source[-1][0] == pytest.approx(-7.216, abs=.01)


def test_seek_resets_rendered_caption_edges_and_old_media_evidence_is_ignored(tmp_path):
    sync, _, _ = runtime(tmp_path)
    identity = sync.identity.wire()
    sync._event(dict(type="CaptionObservation", identity=identity, fingerprint="episode", text="first", time=10))
    sync._event(dict(type="CaptionReset", identity=identity, fingerprint="episode"))
    sync._event(dict(type="CaptionObservation", identity=identity, fingerprint="episode", text="second", time=15))
    assert sync._observed_intervals == []
    sync._event(dict(type="CaptionObservation", identity=identity, fingerprint="old-episode", text="third", time=18))
    assert sync._caption_last == ("second", 15)
    assert not sync.sent


def test_real_audio_scalar_types_can_be_cached_and_sent_without_stopping_clock(tmp_path):
    import numpy as np
    from model.audio_alignment import AudioAnchorAligner
    sync, _, _ = runtime(tmp_path)
    source = tuple((np.float32(i*9), np.float32(i*9+2)) for i in range(14))
    aligner = AudioAnchorAligner(tmp_path, source, ["text"]*14, "s", "episode")
    aligner.anchors = {np.int64(i): (a, np.float32(a+1)) for i, (a, _) in enumerate(source)}
    mapping = aligner.fit()
    assert all(type(x) is bool for x in mapping.confidence)
    assert mapping.version
    sync.alignment_mode = "audio"
    sync._results.put(("audio-alignment", sync._generation, mapping))
    sync._poll()
    assert sync.mapping == mapping
    assert sync.cache.load("s", "episode", mapping.reference_hash) == mapping


def test_disk_failure_does_not_stop_clock_or_discard_alignment(tmp_path, monkeypatch):
    sync, _, _ = runtime(tmp_path)
    sync.alignment_mode = "audio"
    mapping = browser().mapping
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(sync.cache, "save", fail)
    sync._results.put(("audio-alignment", sync._generation, mapping))
    sync._poll()
    assert sync.mapping == mapping
    assert sync.controller.settings.root.after(lambda: None) == "scheduled"


def test_live_episode_nine_reference_automatically_changes_region_in_both_directions(tmp_path):
    from types import SimpleNamespace as NS
    fixture = json.loads((Path(__file__).parents[1]/"fixtures/kimetsu-s1e9-timing.json").read_text("utf-8"))
    sync, _, _ = runtime(tmp_path)
    source = tuple(map(tuple, fixture["source"]))
    sync._source_hash = fixture["source_hash"]
    sync._source = lambda: (sync._source_hash, source)
    sync._preference_media = lambda: "kimetsu-s1e9"
    sync._event(dict(type="CaptionIntervals", identity=sync.identity.wire(), fingerprint="episode",
                     language="en", intervals=fixture["reference"]))
    finish(sync)
    early = min(range(len(source)), key=lambda i: abs(source[i][0]-600))
    late = min(range(len(source)), key=lambda i: abs(source[i][0]-1000))
    assert sync.mapping.video[early][0]-source[early][0] == pytest.approx(1, abs=.02)
    assert sync.mapping.video[late][0]-source[late][0] == pytest.approx(-6.987, abs=.03)
    values, notices = [], []
    sync.controller.settings.auto_offset_var = NS(set=values.append)
    sync.controller.settings.timing_notice_var = NS(set=notices.append)
    for i in (early, late, early):
        sync.snapshot = replace(sync.snapshot, current_time=sync.mapping.video[i][0]+.1)
        sync._update_timing_ui()
        assert sync.source_time(sync.snapshot.current_time) == pytest.approx(source[i][0]+.1)
    assert len(notices) == 3
    assert "+1.000" in notices[0] and "-6.987" in notices[1] and "+1.000" in notices[2]
    assert not any(m["type"] == "PlayerCommand" for m in sync.sent)


def test_episode_seven_repeated_dialogue_does_not_discard_the_entire_timing_map(tmp_path):
    from model.subtitle_alignment import normalized_reference
    fixture = json.loads((Path(__file__).parents[1]/"fixtures/kimetsu-s1e7-timing.json").read_text("utf-8"))
    sync, _, _ = runtime(tmp_path)
    source = tuple(map(tuple, fixture["source"]))
    sync._source_hash = fixture["source_hash"]
    sync._source = lambda: (sync._source_hash, source)
    sync._preference_media = lambda: "kimetsu-s1e7"
    sync._event(dict(type="CaptionIntervals", identity=sync.identity.wire(), fingerprint="episode",
                     language="en", intervals=fixture["reference"]))
    finish(sync)
    assert sync.mapping is not None
    assert sync._reference == normalized_reference(fixture["reference"])
    assert len(sync._reference) == 194  # Float-equivalent observations count once.
    assert sync.mapping.video[120][0]-source[120][0] == pytest.approx(1, abs=.06)
    assert sync.mapping.video[150][0]-source[150][0] == pytest.approx(-7.008, abs=.02)
    # Both repeated phrases preceding the edit remain in their original order.
    assert sync.mapping.video[131][0]-source[131][0] == pytest.approx(1, abs=.06)
    assert all(a[0] <= b[0] for a, b in zip(sync.mapping.video, sync.mapping.video[1:]))
    assert sum(sync.mapping.confidence) > 240
    assert not any(m["type"] == "PlayerCommand" for m in sync.sent)
