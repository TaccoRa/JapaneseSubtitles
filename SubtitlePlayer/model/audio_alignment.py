"""Local Japanese transcription anchors. No OCR and no cloud speech service."""
from difflib import SequenceMatcher
from pathlib import Path
import re
from statistics import median

from model.timing_map import TimingMap, digest


def normalize(text):
    text = re.sub(r"<[^>]+>|\[[^\]]*\]|[（(][^）)]*[）)]", "", text)
    return "".join(re.findall(r"[\w一-龯ぁ-ゖァ-ヺ]", text)).lower()


class AudioAnchorAligner:
    def __init__(self, model_path, source, text, source_hash, fingerprint):
        self.model_path = Path(model_path)
        self.source = tuple(source)
        self.text = [normalize(value) for value in text]
        self.source_hash = source_hash
        self.fingerprint = fingerprint
        self.anchors = {}
        self._model = None

    def transcribe(self, pcm, video_start, rate=1.0):
        if not (self.model_path / "model.bin").is_file():
            raise RuntimeError("Local audio model missing; run dev/setup_audio_alignment.py once")
        import numpy as np
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(str(self.model_path), device="cpu", compute_type="int8", cpu_threads=4)
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(samples, language="ja", vad_filter=True,
                                              beam_size=3, condition_on_previous_text=False)
        for segment in segments:
            query = normalize(segment.text)
            if len(query) < 8 or segment.no_speech_prob > 0.5:
                continue
            ranked = sorted(((SequenceMatcher(None, query, value).ratio(), i) for i, value in enumerate(self.text)), reverse=True)
            if ranked[0][0] < .72 or ranked[0][0] - ranked[1][0] < .12:
                continue  # Repeated phrases cannot establish a unique anchor.
            idx = ranked[0][1]
            self.anchors[idx] = (self.source[idx][0], video_start + segment.start * rate)
        return self.fit()

    def fit(self):
        anchors = sorted(self.anchors.items())
        if len(anchors) < 6:
            raise ValueError("Finding timing: need more unique dialogue anchors")
        # Consecutive evidence groups; three anchors must corroborate a step.
        groups = [[]]
        for item in anchors:
            previous = groups[-1]
            offset = item[1][1] - item[1][0]
            if previous and abs(offset - median(y - x for _, (x, y) in previous)) > 1.0:
                groups.append([])
            groups[-1].append(item)
        fits = []
        for group in groups:
            if len(group) < 3 or group[-1][1][0] - group[0][1][0] < 20:
                continue
            slopes = [(b[1][1] - a[1][1]) / (b[1][0] - a[1][0]) for n, a in enumerate(group) for b in group[n+1:] if b[1][0] - a[1][0] > 10]
            slope = median(slopes)
            if not .9 <= slope <= 1.1:
                continue
            intercept = median(y - slope * x for _, (x, y) in group)
            residuals = [abs(y - slope * x - intercept) for _, (x, y) in group]
            if max(residuals) > .35:
                continue
            fits.append((group[0][0], group[-1][0], slope, intercept))
        if not fits:
            raise ValueError("Alignment uncertain: audio anchors disagree")
        video, confidence = [], []
        for i, (a, b) in enumerate(self.source):
            first, last, slope, intercept = min(fits, key=lambda f: min(abs(i-f[0]), abs(i-f[1])))
            video.append((a * slope + intercept, b * slope + intercept))
            confidence.append(first <= i <= last)
        return TimingMap(self.source_hash, self.fingerprint,
                         digest(repr(sorted(self.anchors.items())).encode()),
                         self.source, tuple(video), tuple(confidence), "local-japanese-audio")
