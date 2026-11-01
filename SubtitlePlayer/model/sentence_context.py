"""Read-only sentence grouping on whole SRT display entries, never retiming them."""
from dataclasses import dataclass
import math
import re
from .ruby_boundary import needs_ruby_boundary


DEFAULT_SENTENCE_MAX_GAP_SEC = 1.5


def _sentence_max_gap(manager):
    try:
        value = manager.config.get('CAPTURE_SENTENCE_MAX_GAP_SEC')
        value = DEFAULT_SENTENCE_MAX_GAP_SEC if value is None else float(value)
        if math.isfinite(value) and 0 <= value <= 10:
            return value
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    return DEFAULT_SENTENCE_MAX_GAP_SEC


@dataclass(frozen=True, slots=True)
class SentenceContext:
    first_cue: int
    last_cue: int
    plain_text: str
    inline_ruby_text: str
    start_time: float
    end_time: float


def _ends_sentence(text):
    text = str(text).rstrip().rstrip('\"\'\u201d\u2019\u300d\u300f\u3011\uff09)]}').rstrip()
    if not text or text[-1] not in '.?!\u3002\uff1f\uff01\uff0e':
        return False
    if text.endswith('..') or re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e)\.$', text, re.I):
        return False
    return True


def _entry_text(entry):
    plain, ruby = [], []
    for line in entry[2:4]:
        segments = [(str(segment[0]), segment[1]) for segment in line]
        if not segments:
            continue
        plain.append(''.join(base for base, _ in segments))
        output = []
        for index, (base, reading) in enumerate(segments):
            if needs_ruby_boundary(segments, index, ''.join(output)):
                output.append(' ')
            output.append(f'{base}[{reading}]' if reading else base)
        ruby.append(''.join(output))
    return '\n'.join(plain), '\n'.join(ruby)


def sentence_for_cue(manager, snapshot):
    """Expand only when both boundaries are known within a nearby dialogue block.

    Whole entries stay intact, including entries containing several sentences.
    Missing punctuation, long gaps, or excessive ranges keep the selected cue.
    No tokenization, model work, or file writes are performed on the UI thread.
    """
    if snapshot is None:
        return None
    data = getattr(manager, 'display_data', ())
    ends = getattr(manager, 'display_end_times', ())
    index = int(snapshot.cue_index)
    if not 0 <= index < len(data) or len(ends) != len(data):
        return None
    current = _entry_text(data[index])
    if (current[0] != snapshot.plain_text
            or abs(float(data[index][1])-float(snapshot.start_time)) > .001):
        return None  # A stale popup cannot expand using another episode's cues.
    if not _ends_sentence(current[0]):
        return None  # Do not chase another speaker's eventual punctuation.
    max_gap = _sentence_max_gap(manager)
    text = {index: (snapshot.plain_text, snapshot.inline_ruby_text)}
    def entry(i):
        if i not in text:
            text[i] = _entry_text(data[i])
        return text[i]
    def nearby(first, last):
        return last-first < 3 and float(ends[last])-float(data[first][1]) <= 30
    first = last = index
    while first > 0 and not _ends_sentence(entry(first-1)[0]):
        if float(data[first][1])-float(ends[first-1]) > max_gap:
            break  # A pause starts a new dialogue block even without punctuation.
        if not nearby(first-1, last):
            return None
        first -= 1
    while not _ends_sentence(entry(last)[0]):
        if (last+1 >= len(data) or not nearby(first, last+1)
                or float(data[last+1][1])-float(ends[last]) > max_gap):
            return None
        last += 1
    if first == last:
        return None
    entries = [entry(i) for i in range(first, last+1)]
    return SentenceContext(first, last, '\n'.join(item[0] for item in entries),
        '\n'.join(item[1] for item in entries), float(data[first][1]), max(float(t) for t in ends[first:last+1]))


def segment_range_for_cue(manager, snapshot, before=0, after=0):
    """Explicit whole-cue capture range, clamped to the current subtitle source."""
    if snapshot is None:
        raise ValueError("Capture range needs a current subtitle segment")
    if before not in range(3) or after not in range(3) or before + after > 2:
        raise ValueError("Capture range supports at most three segments total")
    data = getattr(manager, 'display_data', ())
    ends = getattr(manager, 'display_end_times', ())
    index = int(snapshot.cue_index)
    if not 0 <= index < len(data) or len(ends) != len(data):
        raise ValueError("Capture subtitle segment is no longer available")
    if (_entry_text(data[index])[0] != snapshot.plain_text
            or abs(float(data[index][1]) - float(snapshot.start_time)) > .001):
        raise ValueError("Capture subtitle segment belongs to an outdated source")
    first, last = max(0, index-before), min(len(data)-1, index+after)
    entries = [_entry_text(data[i]) for i in range(first, last+1)]
    return SentenceContext(first, last, '\n'.join(item[0] for item in entries),
        '\n'.join(item[1] for item in entries), float(data[first][1]),
        max(float(t) for t in ends[first:last+1]))
