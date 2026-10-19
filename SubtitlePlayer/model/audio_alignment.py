"""Local multilingual speech timing; Japanese text matching is language gated."""
from difflib import SequenceMatcher
from pathlib import Path
import re
from statistics import median

from model.timing_map import TimingMap, digest


def normalize(text):
    text = re.sub(r"<[^>]+>|\[[^\]]*\]|[（(][^）)]*[）)]", "", text)
    return "".join(re.findall(r"[\w一-龯ぁ-ゖァ-ヺ]", text)).lower()


class AudioAnchorAligner:
    def __init__(self, model_path, source, text, source_hash, fingerprint, *, scope="audio-v3"):
        self.model_path = Path(model_path)
        self.source = tuple(source)
        self.text = [normalize(value) for value in text]
        self.source_hash = source_hash
        self.fingerprint = fingerprint
        self.anchors = {}
        self._model = None
        self.scope = scope
        self.language = None
        self.reference_changed = False
        self.speech_intervals = []

    def transcribe(self, pcm, video_start, rate=1.0):
        self.reference_changed = False
        if not (self.model_path / "model.bin").is_file():
            raise RuntimeError("Local audio model missing; run dev/setup_audio_alignment.py once")
        import numpy as np
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(str(self.model_path), device="cpu", compute_type="int8", cpu_threads=4)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, info = self._model.transcribe(samples, language=None, vad_filter=True,
                                               word_timestamps=True, beam_size=3,
                                               condition_on_previous_text=False)
        if self.language and self.language != info.language:
            if getattr(info, "language_probability", 1) < .8:
                raise ValueError("Alignment uncertain: audio language detection is ambiguous")
            self.anchors.clear()
            self.speech_intervals.clear()
            self.reference_changed = True
        self.language = info.language
        for segment in segments:
            if segment.no_speech_prob > .5 or getattr(segment, "avg_logprob", 0) < -1:
                continue
            words = getattr(segment, "words", None)
            start = words[0].start if words else segment.start
            end = words[-1].end if words else segment.end
            if end <= start:
                continue
            onset = video_start + start * rate
            self.speech_intervals.append((onset, video_start + end * rate))
            # English/German speech must never become a Japanese text anchor.
            if self.language != "ja":
                continue
            query = normalize(segment.text)
            if len(query) < 8:
                continue
            # Speech segments and SRT lines can split the same sentence differently.
            ranked = []
            for i in range(len(self.text)):
                for count in (1, 2, 3):
                    if i + count > len(self.text):
                        continue
                    if count > 1 and self.source[i+count-1][0] - self.source[i][1] > 8:
                        continue
                    value = "".join(self.text[i:i+count])
                    ranked.append((SequenceMatcher(None, query, value).ratio(), i))
            ranked.sort(reverse=True)
            if not ranked:
                continue
            best, idx = ranked[0]
            rival = max((score for score, other in ranked if other != idx), default=0)
            if best >= .72 and best-rival >= .12:
                self.anchors[idx] = (self.source[idx][0], onset)
        if self.language == "ja":
            try:
                return self.fit()
            except ValueError:
                if len(self.speech_intervals) < 8:
                    raise
        # Different audio languages have no semantic Japanese text anchors.
        # Only a corroborated speech-onset pattern may establish timing.
        from model.subtitle_alignment import align_intervals
        from dataclasses import replace
        reference = tuple(sorted(set(self.speech_intervals)))
        mapping = align_intervals(self.source, reference, self.source_hash, self.fingerprint)
        return replace(mapping, method="local-speech-onsets:" + self.language,
                       reference_hash=digest((self.scope + repr(reference)).encode()))

    def fit(self):
        anchors = sorted(self.anchors.items())
        if len(anchors) < 6:
            raise ValueError("Finding timing: need more unique dialogue anchors")
        def regression(group):
            slopes = [(b[1][1]-a[1][1])/(b[1][0]-a[1][0])
                      for n, a in enumerate(group) for b in group[n+1:]
                      if b[1][0]-a[1][0] > 10]
            if not slopes:
                return None
            slope = median(slopes)
            intercept = median(y-slope*x for _, (x,y) in group)
            residual = max(abs(y-slope*x-intercept) for _, (x,y) in group)
            return slope, intercept, residual

        # A new step is accepted only when the following independent group can
        # establish its own model. Continuous rate drift stays in one model.
        groups, current = [], []
        for item in anchors:
            trial = current + [item]
            model = regression(trial)
            if len(current) >= 6 and model and model[2] > .35:
                groups.append(current)
                current = [item]
            else:
                current = trial
        groups.append(current)
        fits = []
        for group in groups:
            if len(group) < 6 or group[-1][1][0]-group[0][1][0] < 30:
                continue
            model = regression(group)
            if not model:
                continue
            slope, intercept, residual = model
            if not .9 <= slope <= 1.1 or residual > .35:
                continue
            fits.append((group[0][0], group[-1][0], slope, intercept))
        if not fits:
            raise ValueError("Alignment uncertain: need six independent dialogue anchors spanning 30 seconds")
        video, confidence = [], []
        for i, (a, b) in enumerate(self.source):
            first, last, slope, intercept = min(fits, key=lambda f: min(abs(i-f[0]), abs(i-f[1])))
            video.append((a * slope + intercept, b * slope + intercept))
            confidence.append(first <= i <= last)
        return TimingMap(self.source_hash, self.fingerprint,
                         digest((self.scope + repr(sorted(self.anchors.items()))).encode()),
                         self.source, tuple(video), tuple(confidence), "local-japanese-audio")
