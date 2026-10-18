"""Browser clock, independent subtitle map and correlated native media writes."""
import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from model.browser_bridge import BrowserBridge
from model.browser_protocol import MediaIdentity, PlaybackSnapshot
from model.subtitle_alignment import align_intervals, parse_intervals
from model.timing_map import TimingMapCache, digest
from model.capture_session import CaptureSession, FrozenActionTarget


class BrowserSyncController:
    def __init__(self, controller):
        self.controller = controller
        self.snapshot = None
        self.identity = None
        self.following = False
        self.mapping = None
        self.status = "Chrome disconnected"
        self._source_key = None
        self._generation = 0
        self._analyzing = False
        self._reference = ()
        self._results = queue.Queue()
        self._capture = None
        self._pending_media = {}
        self._finished_operations = set()
        self._job = None
        self._closed = False
        self._caption_last = None
        self._observed_intervals = []
        self._commands = {}
        self._connected = False
        self._audio_aligner = None
        self._audio_busy = False
        cfg = controller.config
        updates = {}
        if not cfg.get("BROWSER_PAIRING_TOKEN"):
            import secrets
            updates["BROWSER_PAIRING_TOKEN"] = secrets.token_urlsafe(32)
        if "POST_CAPTURE_PLAYBACK" not in getattr(cfg, "local_config", {}):
            updates["POST_CAPTURE_PLAYBACK"] = "play"
        if updates:
            cfg.set_many(updates)
            controller.settings.persist_active_profile_fields({"POST_CAPTURE_PLAYBACK": updates.get("POST_CAPTURE_PLAYBACK", cfg.get("POST_CAPTURE_PLAYBACK"))})
        self.bridge = BrowserBridge(cfg.get("BROWSER_PAIRING_TOKEN"), int(cfg.get("BROWSER_BRIDGE_PORT") or 8767))
        base = Path(cfg.local_path).resolve().parent
        self.cache = TimingMapCache(base / "timing_maps")
        self._pending_directory = base / "pending_capture_media"
        if self._pending_directory.exists():
            for path in self._pending_directory.glob("*.json"):
                try:
                    saved = json.loads(path.read_text("utf-8"))
                    session = CaptureSession(FrozenActionTarget.create(selected_text="", subtitle_text="", sentence_key=""), id=saved["operationId"], backend="native", phase="failed")
                    session.set_target_note(saved["noteId"])
                    self._pending_media[session.id] = (session, saved["media"])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        self.bridge.start()
        self._build_status()
        self._poll()

    @property
    def native(self):
        return self.controller.config.get("CAPTURE_BACKEND") == "native"

    @property
    def fresh(self):
        return self._connected and self.following and self.snapshot is not None and time.monotonic() - self.snapshot.received < 0.4

    def _build_status(self):
        settings = self.controller.settings
        settings.root.minsize(280, 145)
        frame = tk.Frame(settings.settings_frame)
        frame.grid(row=2, column=0, sticky="ew")
        self._status_var = tk.StringVar(value=self.status)
        tk.Label(frame, textvariable=self._status_var, anchor="w", width=24).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browser…", command=self._open_settings).pack(side="right")
        self._retry_button = ttk.Button(frame, text="Retry media", command=self.retry_pending_media)
        if self._pending_media:
            self._retry_button.pack(side="right")

    def retry_pending_media(self):
        for session, result in list(self._pending_media.values()):
            if session.phase == "failed" and not session.cancelled:
                self._attach_media(session, result)

    def _open_settings(self):
        root = tk.Toplevel(self.controller.settings.root)
        root.title("Browser connection")
        root.geometry("590x390")
        text = "1. Load browser-extension in chrome://extensions (Load unpacked).\n2. Open the extension on your video tab and enter this pairing code.\n3. Select the video and click Connect once to allow tab recording."
        tk.Label(root, text=text, justify="left", wraplength=560).pack(padx=12, pady=12)
        code = ttk.Entry(root, width=65)
        code.insert(0, self.bridge.token)
        code.pack(padx=12)
        def copy():
            root.clipboard_clear(); root.clipboard_append(self.bridge.token)
        ttk.Button(root, text="Copy pairing code", command=copy).pack(pady=5)
        cfg = self.controller.config
        tk.Label(root, text="Recorder (asbplayer remains the validated default)").pack()
        backend = tk.StringVar(value=cfg.get("CAPTURE_BACKEND") or "asbplayer")
        ttk.Combobox(root, textvariable=backend, values=("asbplayer", "native"), state="readonly").pack()
        tk.Label(root, text="After successful native capture").pack(pady=(8, 0))
        policy = tk.StringVar(value=cfg.get("POST_CAPTURE_PLAYBACK") or "play")
        ttk.Combobox(root, textvariable=policy, values=("play", "paused", "restore"), state="readonly").pack()
        def save():
            values = {"CAPTURE_BACKEND": backend.get(), "POST_CAPTURE_PLAYBACK": policy.get()}
            cfg.set_many(values)
            self.controller.settings.persist_active_profile_fields(values)
            root.destroy()
        ttk.Button(root, text="Save", command=save).pack(pady=12)

    def _set_status(self, value):
        self.status = value
        self._status_var.set(value)

    def _source(self):
        manager = self.controller.sub_manager
        path = Path(manager.srt_file)
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size,
               tuple(self.controller.episode_controller._get_display_start_times()))
        if key != self._source_key:
            self._source_key = key
            self._source_hash = digest(path.read_bytes())
            self._source_intervals = tuple(zip(self.controller.episode_controller._get_display_start_times(),
                                              self.controller.episode_controller._get_display_end_times()))
            self.invalidate()
        return self._source_hash, self._source_intervals

    def invalidate(self):
        self._generation += 1
        self.mapping = None
        self._analyzing = False
        self._audio_aligner = None
        if self._capture:
            self.abort("Subtitle or media source changed")

    def _align(self):
        if self._analyzing or not self._reference or self.snapshot is None:
            return
        source_hash, source = self._source()
        fingerprint = self.snapshot.fingerprint
        reference = tuple(self._reference)
        reference_hash = digest(json.dumps(reference).encode())
        cached = self.cache.load(source_hash, fingerprint, reference_hash)
        if cached:
            self.mapping = cached
            return
        self._analyzing = True
        generation = self._generation
        self._set_status("Finding timing…")
        def work():
            try:
                result = align_intervals(source, reference, source_hash, fingerprint)
                self.cache.save(result)
                self._results.put(("alignment", generation, result))
            except Exception as exc:
                self._results.put(("alignment-error", generation, str(exc)))
        threading.Thread(target=work, name="subtitle-alignment", daemon=True).start()

    def command(self, action, **arguments):
        if not self.fresh:
            raise ConnectionError("Chrome clock is disconnected or stale")
        if self._capture and action != "abort":
            self.abort("Playback changed by SubtitlePlayer")
        operation = self.bridge.command(self.identity, action, **arguments)
        self._commands[operation] = time.monotonic()
        return operation

    def source_time(self, video_time):
        return self.mapping.to_source(video_time) if self.mapping else -1.0

    def video_time(self, source_time):
        if not self.mapping:
            raise ValueError("Finding subtitle timing")
        return self.mapping.to_video(source_time)

    def freeze_capture(self, session):
        if not self.native:
            return
        if not self.fresh or not self.mapping:
            raise ValueError("Alignment uncertain: connect Chrome and wait for timing")
        cue = session.target.cue_snapshot
        if cue is None:
            raise ValueError("Capture needs a frozen subtitle cue")
        cfg = self.controller.config
        start, end = self.mapping.capture_interval(int(cue.cue_index),
            float(cfg.get("CAPTURE_PADDING_BEFORE_MS") or 0) / 1000,
            float(cfg.get("AUDIO_PADDING") or 0) / 1000)
        session.backend = "native"
        session.post_capture_policy = cfg.get("POST_CAPTURE_PLAYBACK") or "play"
        session.timing_version = self.mapping.version
        session.media_identity = self.identity.wire()
        session.media_fingerprint = self.snapshot.fingerprint
        session.mapped_interval = (start, end)

    def capture(self, session, note_id):
        if self._capture:
            raise RuntimeError("A recording is already in progress")
        if not session or session.backend != "native" or session.cancelled:
            raise ValueError("Invalid native capture operation")
        if not note_id:
            raise ValueError("Native capture requires the exact Anki note ID")
        if not self.fresh or session.media_identity != self.identity.wire() or session.media_fingerprint != self.snapshot.fingerprint:
            raise ValueError("Connected video changed; capture cancelled")
        session.set_target_note(note_id)
        session.phase = "recording"
        self._capture = session
        start, end = session.mapped_interval
        self.bridge.send(dict(type="CaptureRequest", operationId=session.id, identity=session.media_identity,
                              fingerprint=session.media_fingerprint, timingVersion=session.timing_version,
                              start=start, end=end, noteId=note_id, screenshotTime=(start + end) / 2,
                              reuse=session.reuse_ready and not session.capture_only))
        self._set_status("Recording subtitle…")

    def abort(self, reason="Cancelled"):
        session = self._capture
        if session:
            session.cancel(reason)
            try:
                self.bridge.command(MediaIdentity.read(session.media_identity), "abort")
            except ConnectionError:
                pass
            self._capture = None
            self._set_status("Capture cancelled · " + reason)
            self._finish_native(session, False)

    def _finish_native(self, session, ok):
        if session.id in self._finished_operations:
            return
        self._finished_operations.add(session.id)
        self.controller.anki_controller._finish_action_feedback_key(session.action_feedback_key, ok)
        if session.target_note_id:
            self.controller.hotkey_controller.notify_capture_media_finished(session.target_note_id, ok)
        self.controller.release_anki_pause_lease(session.pause_lease, resume=False)

    def _attach_media(self, session, result):
        self._pending_media[session.id] = (session, result)
        session.phase = "pending_media"
        self._set_status("Finishing Anki media…")
        def work():
            try:
                self._pending_directory.mkdir(parents=True, exist_ok=True)
                path = self._pending_directory / (session.id + ".json")
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps(dict(operationId=session.id, noteId=session.target_note_id, media=result)), "utf-8")
                temporary.replace(path)
                if session.cancelled:
                    raise RuntimeError("Capture cancelled")
                self.controller.anki.attach_native_capture(session.target_note_id, session.id,
                    result["audio"], result["image"], is_cancelled=lambda: session.cancelled)
                self._results.put(("media", session.id, None))
            except Exception as exc:
                self._results.put(("media", session.id, str(exc)))
        threading.Thread(target=work, name="anki-native-media", daemon=True).start()

    def _event(self, event):
        kind = event["type"]
        if kind == "Bound":
            self._connected = True
            identity = MediaIdentity.read(event["identity"])
            if identity != self.identity:
                self.invalidate(); self.snapshot = None; self._reference = ()
            self.identity = identity
            self.following = True
            self._set_status("Finding timing…")
        elif kind == "Disconnected":
            self._connected = False
            self.abort("Chrome disconnected")
            # Keep following enabled: never fall back silently to the old clock.
            self._set_status("Chrome disconnected · clock frozen")
        elif kind == "Unbound":
            self.abort("Disconnected by user")
            self.following = False
            self._connected = False
            self.snapshot = None
            self.controller.playing = False
            self._set_status("Legacy controls · Chrome disconnected")
        elif kind == "PlaybackSnapshot":
            snapshot = PlaybackSnapshot.read(event)
            if snapshot.identity != self.identity:
                return
            if self.snapshot and snapshot.sequence <= self.snapshot.sequence:
                return
            if self.snapshot and self.snapshot.fingerprint != snapshot.fingerprint:
                self.invalidate(); self._reference = ()
            self.snapshot = snapshot
            self._source()
        elif kind in ("CaptionText", "CaptionIntervals"):
            if MediaIdentity.read(event["identity"]) != self.identity:
                return
            intervals = parse_intervals(event["text"]) if kind == "CaptionText" else tuple(map(tuple, event["intervals"]))
            if intervals != self._reference:
                self._reference = intervals
                self._align()
        elif kind == "AudioChunk":
            if not self.snapshot or event.get("fingerprint") != self.snapshot.fingerprint or MediaIdentity.read(event["identity"]) != self.identity:
                return
            if self._audio_busy or (self.mapping and all(self.mapping.confidence)):
                return
            from model.audio_alignment import AudioAnchorAligner
            if self._audio_aligner is None:
                source_hash, source = self._source()
                base = Path(self.controller.config.path).resolve().parent
                model = self.controller.config.get("AUDIO_ALIGNMENT_MODEL_PATH") or str(base / "voice_models/whisper-base")
                self._audio_aligner = AudioAnchorAligner(model, source,
                    [x[0] for x in self.controller.sub_manager.display_data], source_hash, self.snapshot.fingerprint)
            worker = self._audio_aligner
            generation = self._generation
            self._audio_busy = True
            def analyze():
                try:
                    mapping = worker.transcribe(base64.b64decode(event["audio"], validate=True), event["start"], event["rate"])
                    self.cache.save(mapping)
                    self._results.put(("alignment", generation, mapping))
                except Exception as exc:
                    self._results.put(("audio-error", generation, str(exc)))
                finally:
                    self._audio_busy = False
            threading.Thread(target=analyze, name="local-audio-alignment", daemon=True).start()
        elif kind == "CommandAck":
            self._commands.pop(event.get("operationId"), None)
            if not event.get("ok"):
                self._set_status("Video command failed: " + event.get("error", "Unknown error"))
            elif event.get("state", {}).get("identity"):
                self._event({**event["state"], "type": "PlaybackSnapshot"})
        elif kind == "CaptureResult":
            session = self._capture
            if not session or event.get("operationId") != session.id or session.cancelled:
                return
            if not event.get("ok"):
                session.phase = "failed"
                self._capture = None
                self._set_status("Capture failed · " + event.get("error", "Unknown error"))
                self._finish_native(session, False)
                return
            if session.phase != "recording":
                return
            if event.get("identity") != session.media_identity:
                self.abort("Stale capture result")
                return
            if event.get("reused"):
                session.phase = "verifying_media"
                def verify():
                    try:
                        self.controller.anki.verify_capture_media(session.target_note_id)
                        self._results.put(("reuse", session.id, None))
                    except Exception as exc:
                        self._results.put(("reuse", session.id, str(exc)))
                threading.Thread(target=verify, name="verify-reused-media", daemon=True).start()
                return
            self._attach_media(session, event)
        elif kind == "CaptureFinished":
            if self._capture and event.get("operationId") == self._capture.id:
                session = self._capture
                session.phase = "completed"
                self._capture = None
                self._finish_native(session, True)

    def _poll(self):
        if self._closed:
            return
        try:
            while True:
                self._event(self.bridge.events.get_nowait())
        except queue.Empty:
            pass
        except Exception as exc:
            self._set_status("Browser connection: " + str(exc))
        try:
            while True:
                kind, identity, value = self._results.get_nowait()
                if kind.startswith("alignment"):
                    if identity != self._generation:
                        continue
                    self._analyzing = False
                    if kind == "alignment":
                        self.mapping = value
                        self.bridge.send(dict(type="TimingMap", version=value.version, video=value.video))
                    else:
                        self._set_status("Alignment uncertain · " + value)
                elif kind == "audio-error" and identity == self._generation:
                    self._set_status("Alignment uncertain · " + value)
                elif kind == "reuse" and self._capture and self._capture.id == identity:
                    session = self._capture
                    self.bridge.send(dict(type="CaptureCommit", operationId=identity,
                        ok=not value and not session.cancelled, error=value, policy=session.post_capture_policy))
                elif kind == "media":
                    session, result = self._pending_media[identity]
                    if value or session.cancelled:
                        session.phase = "cancelled" if session.cancelled else "failed"
                        self._set_status("Media pending · " + (value or "Cancelled"))
                    else:
                        del self._pending_media[identity]
                        (self._pending_directory / (identity + ".json")).unlink(missing_ok=True)
                    if self._capture is session:
                        self.bridge.send(dict(type="CaptureCommit", operationId=identity,
                            ok=not value and not session.cancelled, error=value,
                            policy=session.post_capture_policy))
                    elif not value and not session.cancelled:
                        # Retrying a saved media attachment has no playback side effects.
                        session.phase = "completed"
                        self._set_status("Anki media attached")
                    if self._pending_media:
                        self._retry_button.pack(side="right")
                    else:
                        self._retry_button.pack_forget()
        except queue.Empty:
            pass
        except ConnectionError:
            self._set_status("Chrome disconnected · media pending")
        if self.following and self.snapshot:
            c = self.controller
            if self._capture and time.monotonic() - self.snapshot.received > 1.0:
                self.abort("Video clock unavailable")
            c.current_time = self.snapshot.time_at()
            c.playing = self.fresh and not (self.snapshot.paused or self.snapshot.buffering or self.snapshot.seeking)
            c.playback._set_play_button_state()
            if not c.slider_dragging:
                c.settings.slider.config(to=self.snapshot.duration)
                c.playback._publish_current_time()
            if not self._capture and self.mapping and self.fresh and not self._pending_media:
                idx = self.mapping.cue_at(c.current_time)
                self._set_status("Aligned · Chrome" if idx is None or self.mapping.confidence[idx] else "Alignment uncertain")
            elif not self.fresh:
                self._set_status("Chrome disconnected or buffering · clock frozen")
        for operation, started in list(self._commands.items()):
            if time.monotonic() - started > 3:
                del self._commands[operation]
                self._set_status("Video command not acknowledged")
        if self.bridge.error:
            self._set_status("Browser bridge unavailable: " + self.bridge.error)
        self._job = self.controller.settings.root.after(50, self._poll)

    def close(self):
        self._closed = True
        self.abort("Application closed")
        if self._job:
            self.controller.settings.root.after_cancel(self._job)
        self.bridge.close()
