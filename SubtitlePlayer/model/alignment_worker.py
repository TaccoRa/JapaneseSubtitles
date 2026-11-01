"""Serial, replaceable timing jobs outside Tk's process and Python GIL.

Only cue times, hashes and cache paths are passed to our local Python worker.
No GUI imports, network access, compiler, or subtitle-file writes are involved.
"""
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import sys
import threading


def calculate(request):
    from model.subtitle_alignment import align_intervals
    from model.timing_map import TimingMap, TimingMapCache
    cached = None
    if request.get("cache_directory") and not request.get("force"):
        cached = TimingMapCache(request["cache_directory"]).load(
            request["source_hash"], request["fingerprint"], request["reference_hash"])
    if cached:
        return cached
    original = tuple(map(tuple, request["source"]))
    source, first = original, 0
    reference = request["reference"]
    if request["kind"] == "rendered":
        indices = [i for i, (a, b) in enumerate(source)
                   if b >= reference[0][0]-30 and a <= reference[-1][1]+30]
        if not indices:
            raise ValueError("No source cues in the observed caption section")
        first = indices[0]
        source = source[first:indices[-1]+1]
    result = align_intervals(source, reference, request["source_hash"], request["fingerprint"])
    if len(source) != len(original):
        video, confidence, preview = [], [], []
        for i, interval in enumerate(original):
            if first <= i < first+len(source):
                video.append(result.video[i-first])
                confidence.append(result.confidence[i-first])
                preview.append(result.display_supported(i-first))
            else:
                edge = 0 if i < first else -1
                delta = result.video[edge][0]-result.source[edge][0]
                video.append(tuple(t+delta for t in interval))
                confidence.append(False)
                preview.append(False)
        result = TimingMap(request["source_hash"], request["fingerprint"], result.reference_hash,
                           original, tuple(video), tuple(confidence), "rendered-captions", tuple(preview))
    return replace(result, reference_hash=request["reference_hash"])


class AlignmentWorker:
    """One active child and one pending job; newer pending work replaces older."""
    def __init__(self, results):
        self.results = results
        self._condition = threading.Condition()
        self._pending = None
        self._closed = False
        self._process = None
        self._epoch = 0
        threading.Thread(target=self._run, name="alignment-supervisor", daemon=True).start()

    def submit(self, identity, request):
        with self._condition:
            self._pending = (identity, request)
            self._condition.notify()

    def cancel_pending(self):
        with self._condition:
            self._epoch += 1
            self._pending = None
            if self._process is not None:
                try:
                    self._process.terminate()
                except OSError:
                    pass

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            if self._process is not None:
                try:
                    self._process.terminate()
                except OSError:
                    pass
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                identity, request = self._pending
                epoch = self._epoch
                self._pending = None
            process = None
            try:
                with self._condition:
                    if self._closed:
                        return
                    if epoch != self._epoch:
                        continue
                    command = ([sys.executable, "--subtitleplayer-alignment-worker"] if getattr(sys, "frozen", False)
                               else [sys.executable, str(Path(__file__).resolve())])
                    process = self._process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, encoding="utf-8",
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                output, error = process.communicate(json.dumps(request), timeout=75)
                if process.returncode:
                    raise RuntimeError("Local timing worker failed: " + error.strip()[-400:])
                response = json.loads(output)
                if response.get("error"):
                    raise ValueError(response["error"])
                from model.timing_map import TimingMap
                result = TimingMap(**response["mapping"])
                self.results.put(("alignment", identity, result))
            except Exception as exc:
                self.results.put(("alignment-error", identity, str(exc)))
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.communicate()
                with self._condition:
                    self._process = None


def run():
    try:
        print(json.dumps({"mapping": asdict(calculate(json.load(sys.stdin)))}))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    run()
