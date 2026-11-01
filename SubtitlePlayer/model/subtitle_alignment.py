"""Local interval alignment, with evidence checks independent of the solver."""
from bisect import bisect_left
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from statistics import median

from model.timing_map import TimingMap, digest

_blocked_helpers = set()


def normalized_reference(intervals):
    # Parsed VTT and a player's cue API can represent the same millisecond as
    # 165.456 or 165.45600000000002. Those are one observation, not competing
    # onsets. Keep microsecond precision without altering source cue times.
    return tuple(sorted({(round(a, 6), round(b, 6)) for a, b in intervals}))


def local_onset_offsets(source, reference, max_shift=120):
    """Short rendered tracks need distinct onsets, not two 120-second windows.

    Split/merged captions contribute only their shared starts. Unmatched internal
    splits are not paired by row number and cannot vote for a false correction.
    """
    from collections import Counter
    starts = sorted(a for a, _ in reference)
    votes = Counter()
    for a, _ in source:
        lo, hi = bisect_left(starts, a-max_shift), bisect_left(starts, a+max_shift)
        votes.update(round((t-a)/.05) for t in starts[lo:hi])
    peaks = sorted(votes, key=lambda k: sum(votes.get(k+n, 0) for n in range(-2, 3)), reverse=True)
    candidates = []
    for peak in peaks:
        offset = peak*.05
        if any(abs(offset-other[1]) < .8 for other in candidates):
            continue
        pairs = []
        for i, (a, _) in enumerate(source):
            j = bisect_left(starts, a+offset)
            near = [k for k in (j-1, j) if 0 <= k < len(starts) and abs(starts[k]-a-offset) < .22]
            if len(near) == 1:
                pairs.append((i, near[0], starts[near[0]]-a))
        # Each onset is used once; repeated dialogue patterns are ambiguous.
        counts = Counter(j for _, j, _ in pairs)
        pairs = [(i,j,d) for i,j,d in pairs if counts[j] == 1]
        if len(pairs) < 6:
            continue
        delta = median(d for _,_,d in pairs)
        inliers = [pair for pair in pairs if abs(pair[2]-delta) <= .15]
        groups = [[]]
        for pair in inliers:
            if groups[-1] and pair[0]-groups[-1][-1][0] > 3:
                groups.append([])
            groups[-1].append(pair)
        for group in groups:
            if len(group) >= 6 and source[group[-1][0]][0]-source[group[0][0]][0] >= 30:
                candidates.append((len(group), median(d for _,_,d in group), tuple(i for i,_,_ in group)))
        if len(candidates) >= 30:
            break
    candidates.sort(reverse=True)
    if not candidates:
        raise ValueError("Alignment uncertain: local speech/caption pattern is ambiguous")
    chosen = [candidates[0]]
    for candidate in candidates[1:]:
        overlaps = [other for other in chosen if
                    len(set(candidate[2]) & set(other[2])) >= .3*min(len(candidate[2]), len(other[2]))]
        if overlaps:
            if any(other[0] < candidate[0]+3 for other in overlaps):
                raise ValueError("Alignment uncertain: repeated onset pattern")
            continue
        # A step needs its own contiguous evidence region, rather than scattered
        # matches interleaved with another equally plausible offset.
        def separated(other):
            left, right = sorted((candidate, other), key=lambda c: median(c[2]))
            unique_left, unique_right = set(left[2])-set(right[2]), set(right[2])-set(left[2])
            return unique_left and unique_right and max(unique_left) < min(unique_right)
        if all(separated(other) for other in chosen):
            chosen.append(candidate)
    chosen.sort(key=lambda c: median(c[2]))
    boundaries = []
    for left, right in zip(chosen, chosen[1:]):
        lo = max(set(left[2])-set(right[2]))+1
        hi = min(set(right[2])-set(left[2]))
        boundaries.append(max(range(lo, hi+1), key=lambda i: source[i][0]-source[i-1][1]))
    return [chosen[sum(i >= boundary for boundary in boundaries)][1] for i in range(len(source))]


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


def align_intervals(source, reference, source_hash, media_fingerprint, *, helper=None, _onset_only=False):
    source, reference = tuple(map(tuple, source)), normalized_reference(reference)
    if len(source) < 8 or len(reference) < 8:
        raise ValueError("Not enough timing evidence yet")
    reference_hash = digest(("onsets-v4:" + json.dumps(reference)).encode())
    helper = Path(helper) if helper else Path(__file__).parents[1] / "bin" / "subtitle-align.exe"
    candidates = []
    # Common broadcast/frame-rate conversions; arbitrary drift is handled by
    # the transcription anchor fitter, rather than manufacturing timing steps.
    for rate in (1.0, 25 / 23.976, 23.976 / 25, 24 / 23.976, 23.976 / 24):
        scaled = [(a * rate, b * rate) for a, b in source]
        offsets = None
        if not _onset_only and helper.is_file() and str(helper) not in _blocked_helpers:
            request = json.dumps(dict(source=scaled, reference=reference))
            try:
                process = subprocess.run([str(helper)], input=request, text=True, encoding="utf-8", errors="replace", capture_output=True,
                                         timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if process.returncode:
                    raise RuntimeError("Local alignment helper failed")
                offsets = json.loads(process.stdout)["offsets"]
                method = "alass-core"
            except OSError as exc:
                if getattr(exc, "winerror", None) != 4551:
                    raise
                _blocked_helpers.add(str(helper))  # Respect application control.
        if offsets is None:
            # Small pure-Python bootstrap keeps source installations usable.
            # The packaged Windows distribution includes the alass helper.
            from model.interval_alignment import Interval, fit
            try:
                if _onset_only:
                    offsets = local_onset_offsets(scaled, reference)
                else:
                    offsets = fit([Interval(*x) for x in scaled], [Interval(*x) for x in reference])["cue_offsets"]
            except ValueError:
                if _onset_only:
                    continue
                try:
                    offsets = local_onset_offsets(scaled, reference)
                except ValueError:
                    continue
            method = "boundary-evidence"
        if len(offsets) != len(source):
            raise ValueError("Incomplete alignment response")
        video = tuple((a + float(d), b + float(d)) for (a, b), d in zip(scaled, offsets))
        starts, ends = sorted(x[0] for x in reference), sorted(x[1] for x in reference)
        errors = [nearest(starts, a) for a, b in video]
        video = list(video)
        # Each timing region needs multiple cues in separated time windows.
        trusted = [False] * len(source)
        preview = [False] * len(source)
        first = 0
        for stop in range(1, len(source) + 1):
            if stop < len(source) and abs(offsets[stop] - offsets[stop - 1]) < 0.35:
                continue
            # Mutual, unique onset pairs avoid matching split/merged captions or
            # repeated close dialogue. Refine each region without moving its break.
            pairs = []
            for i in range(first, stop):
                onset = video[i][0]
                near = [j for j in (bisect_left(starts, onset)-1, bisect_left(starts, onset))
                        if 0 <= j < len(starts) and abs(starts[j]-onset) <= .65]
                if len(near) != 1:
                    continue
                j = near[0]
                rivals = [k for k in range(first, stop) if abs(video[k][0]-starts[j]) <= .65]
                if len(rivals) == 1:
                    pairs.append((i, starts[j]-onset))
            if len(pairs) >= 6 and source[pairs[-1][0]][0] - source[pairs[0][0]][0] >= 30:
                residual = median(delta for _, delta in pairs)
                consistent = [abs(delta-residual) for _, delta in pairs]
                before = median(abs(delta) for _, delta in pairs)
                if max(consistent) <= .15 and before - median(consistent) >= .025:
                    for i in range(first, stop):
                        a, b = video[i]
                        video[i] = (a+residual, b+residual)
                        errors[i] = nearest(starts, video[i][0])
            support = [i for i, _ in pairs if errors[i] < 0.22]
            enough = (len(support) >= 6 and source[support[-1]][0] - source[support[0]][0] >= 30
                      and len(support) / (support[-1]-support[0]+1) >= 0.3)
            if enough and rate != 1.0:
                # A slightly different frame rate can accidentally fit isolated
                # onsets around an edit. Do not turn those into continuous drift.
                # Require distributed, consistent evidence and a real improvement
                # over the best constant offset within this timing region.
                corrections = [delta for _, delta in pairs]
                center = median(corrections)
                inliers = [(i, delta) for i, delta in pairs if abs(delta-center) <= .15]
                span = source[support[-1]][0]-source[support[0]][0]
                bins = [0, 0, 0, 0]
                for i, _ in inliers:
                    bucket = min(3, int(4*(source[i][0]-source[support[0]][0])/span))
                    if bucket >= 0:
                        bins[bucket] += 1
                after = median(abs(delta-center) for _, delta in pairs)
                constant = [delta + (rate-1)*source[i][0] for i, delta in pairs]
                constant_center = median(constant)
                before = median(abs(delta-constant_center) for delta in constant)
                enough = (len(inliers) >= 12 and len(inliers) >= .85*len(pairs)
                          and min(bins) >= 3 and abs(rate-1)*span >= .25
                          and before-after >= .08)
            if enough:
                # A short observed reference says nothing about earlier/later
                # unobserved dialogue. Avoid rejecting its valid local section
                # because unrelated surrounding cues inflate the denominator.
                trusted[support[0]:support[-1]+1] = [True] * (support[-1]-support[0]+1)
                # Missing/merged translations near a known edit must not jump
                # back to the original clock. Keep the regional correction as
                # an estimated preview inside the observed reference window.
                for i in range(first, stop):
                    preview[i] = starts[0] <= video[i][0] <= max(ends)
            first = stop
        try:
            mapping = TimingMap(source_hash, media_fingerprint, reference_hash, source, tuple(video),
                                tuple(trusted), method, tuple(preview))
        except ValueError:
            continue
        # Untrusted solver output is not a candidate. It must not displace a
        # supported model merely because coincidental onset hits score well.
        if not any(trusted):
            continue
        score = sum(e < 0.22 for e in errors) - sum(not x for x in trusted)
        candidates.append((score, mapping))
    if not candidates:
        if not _onset_only:
            # The interval solver may return a mathematically valid fit driven
            # by caption ends. Rejection by onset validation must also trigger
            # the independent onset solver, not only a solver exception.
            return align_intervals(source, reference, source_hash, media_fingerprint,
                                   helper=helper, _onset_only=True)
        raise ValueError("Alignment uncertain: no corroborated subtitle-onset region")
    result = max(candidates, key=lambda x: x[0])[1]
    if not any(result.confidence):
        raise ValueError("Alignment uncertain: no supported timing region")
    return result
