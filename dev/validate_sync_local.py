"""Exercise the current app/Tk/bridge with a simulated browser clock.

This is an integration check, not live browser/site acceptance. It never connects
to Anki or touches personal configuration and uses the archived subtitle fixture.
"""
from pathlib import Path
import json
import logging
import os
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "SubtitlePlayer"))


def main():
    from app import SubtitlePlayerApp
    from controller.controller import SubtitleController
    from model.subtitle_manager import SubtitleManager
    from websockets.sync.client import connect
    fixture = ROOT / "dev/reviews/subtitleplayer-2026-10-18"
    report = {"mode": "simulated browser / actual current desktop app", "clock_errors_ms": []}
    SubtitleController._start_input_listeners = lambda self: None
    with tempfile.TemporaryDirectory(prefix="subtitle-sync-validation-") as directory:
        folder = Path(directory)
        config = json.loads((ROOT / "config.json").read_text("utf-8"))
        config.update(REMOTE_FLAG=False, LAST_LOCAL_SRT_FILE=str(fixture / "source-jp.srt"),
                      VOICE_ENABLED=False, ANKI_AUTO_START=False, ANNOTATION_ENABLED=False,
                      OCR_ENABLED=False, STARTUP_RESUME_PLAY=False, CLEANUP_CACHED_SUBTITLES_ON_EXIT=False,
                      BROWSER_BRIDGE_PORT=0)
        (folder / "config.json").write_text(json.dumps(config), "utf-8")
        cache = folder / "cache"
        cache.mkdir()
        SubtitleManager._get_cache_base_dir = lambda self: str(cache)
        SubtitleManager._get_unparsed_log_path = lambda self: str(folder / "unparsed.tsv")
        os.chdir(folder)
        app = SubtitlePlayerApp()
        ready = threading.Event()
        finished = threading.Event()
        shared = {"position": 100.0, "anchor": time.monotonic(), "rate": 1.0, "paused": True, "buffering": False, "stable": False}
        errors = []
        identity = dict(tabId=123, documentId="isolated-test-document", frameId=2, videoId="isolated-test-video")

        def expected():
            return shared["position"] if shared["paused"] or shared["buffering"] else shared["position"] + (time.monotonic() - shared["anchor"]) * shared["rate"]

        def browser():
            if not ready.wait(20):
                report["error"] = "App startup timed out"; finished.set(); return
            sync = app.controller.browser_sync
            if not sync.bridge.ready.wait(5) or sync.bridge.error:
                report["error"] = sync.bridge.error or "Bridge startup timed out"; finished.set(); return
            try:
                with connect(f"ws://127.0.0.1:{sync.bridge.port}") as peer:
                    peer.send(json.dumps(dict(type="Hello", version=1, token=sync.bridge.token)))
                    assert json.loads(peer.recv(timeout=3))["type"] == "Welcome"
                    peer.send(json.dumps(dict(type="Bound", identity=identity)))
                    sequence = 0
                    def snapshot():
                        nonlocal sequence
                        sequence += 1
                        value = dict(type="PlaybackSnapshot", identity=identity, sequence=sequence,
                                     currentTime=expected(), duration=1447.829, rate=shared["rate"],
                                     paused=shared["paused"], buffering=shared["buffering"], fingerprint="isolated-fixture")
                        peer.send(json.dumps(value))
                    peer.send(json.dumps(dict(type="CaptionText", identity=identity, text=(fixture / "source-en.vtt").read_text("utf-8-sig"))))
                    # Exercise the startup race: captions may precede the clock.
                    time.sleep(.2)
                    snapshot()
                    deadline = time.monotonic() + 25
                    while sync.mapping is None and time.monotonic() < deadline:
                        snapshot(); time.sleep(.1)
                    assert sync.mapping is not None, "Automatic alignment did not complete"
                    report["timing_method"] = sync.mapping.method
                    report["offsets"] = [round(sync.mapping.video[i][0] - sync.mapping.source[i][0], 3) for i in (0, -1)]
                    for position, rate, paused, buffering in [(100, 1, False, False), (400, 1.5, False, False), (700, 1.5, False, True), (830, 1, True, False), (830, 1, False, False)]:
                        shared.update(position=position, anchor=time.monotonic(), rate=rate, paused=paused, buffering=buffering, stable=False)
                        snapshot()
                        time.sleep(.2)
                        convergence = abs(app.controller.current_time - expected()) * 1000
                        report.setdefault("phase_errors_ms", []).append(round(convergence, 2))
                        shared["stable"] = True
                        end = time.monotonic() + 1.5
                        while time.monotonic() < end:
                            snapshot(); time.sleep(.1)
                    shared["stable"] = False
                    peer.close()
                    time.sleep(.6)
                    frozen = app.controller.current_time
                    time.sleep(.3)
                    report["disconnected_clock_frozen"] = app.controller.current_time == frozen
            except Exception as exc:
                report["error"] = repr(exc)
            finally:
                finished.set()

        original = app._build_root
        def build():
            original()
            def monitor():
                if app.controller:
                    ready.set()
                    if shared["stable"]:
                        errors.append(abs(app.controller.current_time - expected()) * 1000)
                if finished.is_set():
                    app._on_close()
                else:
                    app.root.after(20, monitor)
            app.root.after(50, monitor)
            app.root.after(45000, app._on_close)
        app._build_root = build
        thread = threading.Thread(target=browser, daemon=True)
        thread.start()
        try:
            app.run()
        finally:
            logging.shutdown()
            os.chdir(ROOT)
        if errors:
            report.pop("clock_errors_ms")
            report["samples"] = len(errors)
            report["clock_p95_ms"] = round(sorted(errors)[int(len(errors) * .95)], 2)
            report["clock_max_ms"] = round(max(errors), 2)
        target = ROOT / "logs/sync-current-script-report.json"
        target.parent.mkdir(exist_ok=True)
        target.write_text(json.dumps(report, indent=2), "utf-8")
        print(json.dumps(report, indent=2))
        if report.get("error") or not report.get("disconnected_clock_frozen") or report.get("clock_p95_ms", 1000) >= 150:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
