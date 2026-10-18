"""Local interval alignment, with evidence checks independent of the solver."""
from bisect import bisect_left
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from statistics import median

from model.timing_map import TimingMap, digest


def parse_intervals(text):
    def seconds(value):
        return sum(float(part) * 60 ** i for i, part in enumerate(reversed(value.replace(",", ".").split(":"))))
    result = []
    for line in text.splitlines():
        if "-->" in line:
            a, b = line.split("-->", 1)
            result.append((seconds(a.strip()), seconds(b.strip().split()[0])))
    return tuple(sorted(result))


def nearest(times, value):
    i = bisect_left(times, value)
    return min((abs(times[j] - value) for j in (i - 1, i) if 0 <= j < len(times)), default=float("inf"))


def align_intervals(source, reference, source_hash, media_fingerprint, *, helper=None):
    source, reference = tuple(map(tuple, source)), tuple(map(tuple, reference))
    if len(source) < 8 or len(reference) < 8:
        raise ValueError("Not enough timing evidence yet")
    reference_hash = digest(json.dumps(reference).encode())
    helper = Path(helper) if helper else Path(__file__).parents[1] / "bin" / "subtitle-align.exe"
    candidates = []
    # Common broadcast/frame-rate conversions; arbitrary drift is handled by
    # the transcription anchor fitter, rather than manufacturing timing steps.
    for rate in (1.0, 25 / 23.976, 23.976 / 25, 24 / 23.976, 23.976 / 24):
        scaled = [(a * rate, b * rate) for a, b in source]
        if helper.is_file():
            request = json.dumps(dict(source=scaled, reference=reference))
            process = subprocess.run([str(helper)], input=request, text=True, capture_output=True,
                                     timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode:
                raise RuntimeError("Local alignment helper failed")
            offsets = json.loads(process.stdout)["offsets"]
            method = "alass-core"
        else:
            # Small pure-Python bootstrap keeps source installations usable.
            # The packaged Windows distribution includes the alass helper.
            from model.interval_alignment import Interval, fit
            try:
                offsets = fit([Interval(*x) for x in scaled], [Interval(*x) for x in reference])["cue_offsets"]
            except ValueError:
                continue
            method = "boundary-evidence"
        if len(offsets) != len(source):
            raise ValueError("Incomplete alignment response")
        video = tuple((a + float(d), b + float(d)) for (a, b), d in zip(scaled, offsets))
        starts, ends = sorted(x[0] for x in reference), sorted(x[1] for x in reference)
        errors = [min(nearest(starts, a), nearest(ends, b)) for a, b in video]
        # Each timing region needs multiple cues in separated time windows.
        trusted = [False] * len(source)
        first = 0
        for stop in range(1, len(source) + 1):
            if stop < len(source) and abs(offsets[stop] - offsets[stop - 1]) < 0.35:
                continue
            support = [i for i in range(first, stop) if errors[i] < 0.22]
            enough = (len(support) >= 6 and source[support[-1]][0] - source[support[0]][0] >= 30
                      and len(support) / (stop - first) >= 0.3)
            trusted[first:stop] = [enough] * (stop - first)
            first = stop
        try:
            mapping = TimingMap(source_hash, media_fingerprint, reference_hash, source, video,
                                tuple(trusted), method)
        except ValueError:
            continue
        score = sum(e < 0.22 for e in errors) - sum(not x for x in trusted)
        candidates.append((score, mapping))
    if not candidates:
        raise ValueError("Alignment uncertain: insufficient corroborating evidence")
    result = max(candidates, key=lambda x: x[0])[1]
    if not any(result.confidence):
        raise ValueError("Alignment uncertain: no supported timing region")
    return result
