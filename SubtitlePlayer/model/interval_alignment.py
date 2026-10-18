"""Conservative interval-only bootstrap for source checkouts without a helper."""
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from statistics import median

@dataclass(frozen=True)
class Interval:
    start: float
    end: float

def nearest_error(times: list[float], value: float) -> float:
    i = bisect_left(times, value)
    return min((abs(times[k] - value) for k in (i - 1, i) if 0 <= k < len(times)), default=float("inf"))


def fit(target: list[Interval], reference: list[Interval], *, max_shift: float = 30.0) -> dict:
    """Return supported offsets and piecewise cue assignments without text anchors."""
    if max_shift <= 0:
        raise ValueError("max_shift must be positive")
    edges = [sorted(getattr(c, edge) for c in reference) for edge in ("start", "end")]
    windows = []
    for low in range(0, int(target[-1].end) + 1, 120):
        differences = []
        for cue in target:
            if not low <= cue.start < low + 120:
                continue
            for edge, times in zip(("start", "end"), edges):
                value = getattr(cue, edge)
                a, b = bisect_left(times, value - max_shift), bisect_left(times, value + max_shift)
                differences.extend(t - value for t in times[a:b])
        if not differences:
            continue
        counts = Counter(round(d / 0.05) for d in differences)
        winner = max(counts, key=lambda k: sum(counts.get(k + n, 0) for n in range(-2, 3))) * 0.05
        inliers = [d for d in differences if abs(d - winner) <= 0.15]
        if len(inliers) >= 4:
            windows.append({"start": low, "end": low + 120, "offset": median(inliers), "support": len(inliers)})
    clusters: list[list[dict]] = []
    for window in sorted(windows, key=lambda w: w["offset"]):
        if clusters and abs(window["offset"] - median(w["offset"] for w in clusters[-1])) < 0.6:
            clusters[-1].append(window)
        else:
            clusters.append([window])
    # A lone window is insufficient evidence for introducing a new time segment.
    offsets = [median(w["offset"] for w in group) for group in clusters if len(group) >= 2]
    if not offsets:
        raise ValueError("Too little repeated timing evidence; refusing to generate an alignment")

    def score(cue: Interval, offset: float) -> float:
        return sum(max(0.0, 1.0 - nearest_error(times, getattr(cue, edge) + offset) / 0.18)
                   for edge, times in zip(("start", "end"), edges))

    costs = [score(target[0], offset) for offset in offsets]
    parents = [[-1] * len(offsets)]
    for cue in target[1:]:
        next_costs, row = [], []
        for k, offset in enumerate(offsets):
            choices = [v - (6.0 if prev != k else 0.0) for prev, v in enumerate(costs)]
            prev = max(range(len(offsets)), key=lambda p: choices[p])
            next_costs.append(choices[prev] + score(cue, offset))
            row.append(prev)
        costs = next_costs
        parents.append(row)
    state = max(range(len(offsets)), key=lambda k: costs[k])
    labels = []
    for row in reversed(parents):
        labels.append(state)
        state = row[state]
    labels.reverse()

    # Silent/unmatched cues cannot locate a cut. Place it in the largest cue gap
    # between the last supporting old cue and the first supporting new cue.
    for i in range(1, len(labels)):
        if labels[i] == labels[i - 1]:
            continue
        old, new = labels[i - 1], labels[i]
        left = next((k for k in range(i - 1, -1, -1) if score(target[k], offsets[old]) >= 0.75), i - 1)
        right = next((k for k in range(i, len(target)) if score(target[k], offsets[new]) >= 0.75), i)
        boundary = max(range(left + 1, right + 1), key=lambda k: target[k].start - target[k - 1].end)
        for k in range(left + 1, right):
            labels[k] = old if k < boundary else new

    segments = []
    for i, label in enumerate(labels):
        if not segments or label != segments[-1]["label"]:
            segments.append({"label": label, "first_cue": i + 1, "last_cue": i + 1,
                             "subtitle_start": target[i].start, "offset": round(offsets[label], 3)})
        segments[-1]["last_cue"] = i + 1
    for segment in segments:
        segment.pop("label")
    return {"method": "experimental boundary votes + penalized piecewise fit",
            "offset_convention": "video_time = subtitle_time + offset",
            "windows": windows, "segments": segments,
            "cue_offsets": [round(offsets[k], 3) for k in labels]}

