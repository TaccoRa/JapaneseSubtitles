"""Ground-truth timing checks for the standalone review experiment."""

import random

import pytest

from dev.experiments.probe_subtitle_alignment import Interval, fit, load_intervals


def speech_pattern():
    rng = random.Random(451)
    target = []
    t = 5.0
    for section in range(2):
        t = max(t, section * 500.0 + 5.0)
        for _ in range(55):
            duration = rng.uniform(1.1, 4.0)
            target.append(Interval(t, t + duration))
            t += duration + rng.uniform(1.2, 4.5)
    return target


def test_constant_offset_is_recovered_with_jitter():
    target = speech_pattern()
    rng = random.Random(218)
    reference = [Interval(c.start + 2.75 + rng.uniform(-.025, .025),
                          c.end + 2.75 + rng.uniform(-.025, .025)) for c in target]
    result = fit(target, reference)
    assert len(result["segments"]) == 1
    assert result["segments"][0]["offset"] == pytest.approx(2.75, abs=.06)


def test_midpoint_cut_is_recovered_without_any_manual_anchors():
    target = speech_pattern()
    reference = [Interval(c.start + (1 if i < 55 else -7.2),
                          c.end + (1 if i < 55 else -7.2)) for i, c in enumerate(target)]
    result = fit(target, reference)
    assert len(result["segments"]) == 2
    assert result["cue_offsets"][:55] == pytest.approx([1] * 55, abs=.06)
    assert result["cue_offsets"][55:] == pytest.approx([-7.2] * 55, abs=.06)


def test_sparse_evidence_refuses_to_guess():
    with pytest.raises(ValueError, match="Too little"):
        fit([Interval(1, 2)], [Interval(10, 11)])


def test_vtt_parser_supports_cue_settings_and_short_timestamps(tmp_path):
    path = tmp_path / "captions.vtt"
    path.write_text("WEBVTT\n\n00:01.250 --> 00:03.750 line:80%\nHello\n\n"
                    "00:01:01.000 --> 00:01:02.500\nWorld\n", encoding="utf-8")
    assert load_intervals(path) == [Interval(1.25, 3.75), Interval(61, 62.5)]
