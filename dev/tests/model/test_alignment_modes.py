"""Timing evidence, independent preferences and language safety."""
import json
from types import SimpleNamespace as NS
import pytest
from model.alignment_preferences import AlignmentPreferences
from model.audio_alignment import AudioAnchorAligner
from model.subtitle_alignment import align_intervals, local_onset_offsets
from model.timing_map import TimingMap
from test_native_capture import browser


def test_modes_and_trims_survive_restart_without_legacy_double_offset(tmp_path):
    path = tmp_path / "alignment.json"
    preferences = AlignmentPreferences(path, {"shrek": 25})
    assert preferences.mode("shrek") == "audio"
    assert preferences.trim("shrek", "ja-srt", "audio") == 0
    preferences.set_trim("shrek", "ja-srt", "audio", -.3)
    preferences.set_mode("shrek", "captions")
    restored = AlignmentPreferences(path)
    assert restored.mode("shrek") == "captions"
    assert restored.trim("shrek", "ja-srt", "captions") == 0
    assert restored.trim("shrek", "ja-srt", "audio") == -.3
    assert restored.trim("anime", "ja-srt", "audio") == 0
    assert restored.data["legacyTrims"] == {"shrek": 25}
    assert len({restored.scope("s", "m", track, mode, kind)
                for track in ("ja", "en") for mode in ("audio", "captions") for kind in ("full", "rendered")}) == 8


def test_audio_language_is_detected_and_english_never_creates_japanese_anchors(tmp_path, monkeypatch):
    import model.subtitle_alignment as solver
    (tmp_path / "model.bin").write_bytes(b"fixture")
    source = tuple((i*8, i*8+2) for i in range(12))
    aligner = AudioAnchorAligner(tmp_path, source, ["これは日本語の字幕です"]*12, "s", "m")
    settings = []
    def transcribe(samples, **kwargs):
        settings.append(kwargs)
        return iter([NS(start=2, end=4, text="これは日本語の字幕です", no_speech_prob=0, words=None)]), NS(language="en")
    aligner._model = NS(transcribe=transcribe)
    monkeypatch.setattr(solver, "align_intervals", lambda *args: TimingMap("s", "m", "r", source, source, (True,)*12))
    result = aligner.transcribe(b"\0\0"*16000, 20)
    assert settings[0]["language"] is None and settings[0]["word_timestamps"]
    assert not aligner.anchors and result.method == "local-speech-onsets:en"
    assert aligner.speech_intervals == [(22, 24)]


def irregular_source():
    starts = [10, 13.9, 19.2, 26.4, 29.5, 37.8, 42.2, 48.6, 55.9, 63.3, 69.1, 77.8, 86.2, 94.5]
    return tuple((a, a+1.5) for a in starts)


def test_short_split_merged_caption_reference_uses_shared_onsets(tmp_path):
    source = irregular_source()
    reference = []
    for i,(a,b) in enumerate(source):
        if i % 4 != 1:  # some Japanese cues are merged into previous English cue
            reference.append((a+25, b+25.2))
        if i % 3 == 0:  # some English cues have an internal split
            reference.append((a+25.8, b+25.2))
    mapping = align_intervals(source, sorted(reference), "s", "m", helper=tmp_path/"absent")
    assert mapping.video[7][0] == pytest.approx(source[7][0]+25, abs=.05)
    assert sum(mapping.confidence) >= 8


def test_repeated_onsets_and_stillness_do_not_establish_a_map():
    repeated = tuple((i*8, i*8+2) for i in range(10))
    with pytest.raises(ValueError, match="ambiguous|repeated"):
        local_onset_offsets(repeated, [(a+25,b+25) for a,b in repeated])
    with pytest.raises(ValueError):
        align_intervals(repeated, [], "s", "m")


def test_audio_long_rate_drift_remains_one_confirmed_model(tmp_path):
    source = tuple((i*60, i*60+2) for i in range(20))
    aligner = AudioAnchorAligner(tmp_path, source, ["text"]*20, "s", "m")
    aligner.anchors = {i: (a,a*1.01+2) for i,(a,b) in enumerate(source)}
    mapping = aligner.fit()
    assert all(mapping.confidence)
    assert mapping.video[-1][0] == pytest.approx(source[-1][0]*1.01+2)


def test_mode_switch_invalidates_analysis_without_touching_active_recording(tmp_path):
    sync = browser()
    sync.preferences = AlignmentPreferences(tmp_path/"prefs.json", {"legacy":25})
    sync._preference_media = lambda: "movie"
    sync._source_hash = "s"
    sync._generation = 10
    sync._source_key = "source"
    sync._capture = recording = object()
    sync.controller.settings = NS(offset_var=NS(set=lambda value: None))
    sync._restore_timing_cache = lambda: None
    sync._align = lambda: None
    sync.set_alignment_mode("Website-Untertitel")
    assert sync.alignment_mode == "captions" and sync._generation == 11
    assert sync._capture is recording and sync.mapping is None and sync._trim == 0
    assert not sync.sent


def test_inline_offset_notifies_both_directions_at_known_break():
    from dataclasses import replace
    sync = browser()
    values, notices = [], []
    sync.controller.settings = NS(auto_offset_var=NS(set=values.append), timing_notice_var=NS(set=notices.append))
    sync._offset_region = sync._auto_offset = None
    sync.snapshot = replace(sync.snapshot, current_time=11.5)
    sync._update_timing_ui(); sync._update_timing_ui()
    sync.snapshot = replace(sync.snapshot, current_time=23)
    sync._update_timing_ui()
    sync.snapshot = replace(sync.snapshot, current_time=11.5)
    sync._update_timing_ui()
    assert len(notices) == 3
    assert "+1.000" in notices[0] and "-7.216" in notices[1] and "+1.000" in notices[2]
    assert not sync.sent


def test_local_captions_corroborate_a_later_step_without_two_large_windows(tmp_path):
    first = irregular_source()
    second = tuple((a+150,b+150) for a,b in first)
    source = first+second
    reference = [(a+1,b+1.2) for a,b in first]+[(a-7.216,b-7.016) for a,b in second]
    offsets = local_onset_offsets(source, reference)
    assert offsets[3] == pytest.approx(1)
    assert offsets[-3] == pytest.approx(-7.216)


def test_uncertain_region_uses_original_time_preview_and_cannot_record():
    from dataclasses import replace
    sync = browser()
    sync.mapping = replace(sync.mapping, confidence=(False,True))
    assert sync.source_time(11.5) == 11.5
    assert sync.video_time(10.5) == 10.5
    with pytest.raises(ValueError,match="uncertain"):
        sync.mapping.capture_interval(0)
    assert not sync.sent


def test_stale_audio_results_cannot_replace_new_mode_mapping(tmp_path):
    from test_native_playback import pollable
    sync = pollable(browser(),tmp_path)
    sync.following = False
    sync.alignment_mode = "audio"
    sync._generation = 3
    expected = sync.mapping
    sync._results.put(("audio-alignment",2,object()))
    sync._poll()
    assert sync.mapping == expected
    assert not sync.sent


def test_capture_only_target_cannot_follow_another_source():
    from model.capture_session import CaptureSession,FrozenActionTarget
    sync = browser();sync._source_hash = "current"
    target = FrozenActionTarget.create(selected_text="word",subtitle_text="sentence",sentence_key="s",source_hash="previous")
    with pytest.raises(ValueError,match="different subtitle"):
        sync.freeze_capture(CaptureSession(target))
    assert not sync.sent


def test_capture_only_diagnostics_distinguish_missing_target_timing_and_backend():
    from controller.hotkey_controller import HotkeyController
    for reason,error,expected in [
        ("no_current_run_target", "", "app session"),
        ("capture_not_ready", "Alignment uncertain for this cue", "Alignment uncertain"),
        ("capture_not_ready", "Enable native recording", "Enable native"),
        ("capture_delivery_failed", "asbplayer backend unavailable", "asbplayer backend"),
    ]:
        result = dict(reason=reason,error=error)
        message = HotkeyController._capture_only_failure_message(result)
        assert expected in message
        assert "window or hotkey" not in message
