"""Browser clock, independent subtitle map and correlated native media writes."""
import base64
import json
import logging
from pathlib import Path
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

from model.browser_bridge import BrowserBridge
from model.browser_protocol import MediaIdentity, PlaybackSnapshot
from model.subtitle_alignment import align_intervals, parse_intervals
from model.timing_map import TimingMapCache, digest
from model.capture_session import CaptureSession, FrozenActionTarget

logger = logging.getLogger(__name__)


class BrowserSyncController:
    def __init__(self, controller):
        self.controller = controller
        self.snapshot = None
        self.identity = None
        self.following = False
        self._base_mapping = None
        self._trim = 0.0
        self._trim_key = None
        self.status = "Chrome disconnected"
        self._source_key = None
        self._generation = 0
        self._analyzing = False
        self._alignment_attempt_key = None
        self._reference = ()
        self._reference_kind = "none"
        self._results = queue.Queue()
        self._capture = None
        self._control_generation = 0
        self._operations = {}
        self._retry_at = {}
        self._restored_media = set()
        self._retry_count = {}
        self._failure_pause = None
        self._pending_seek = None
        self._last_probe = 0
        self._pending_media = {}
        self._finished_operations = set()
        self._job = None
        self._closed = False
        self._caption_last = None
        self._observed_intervals = []
        self._commands = {}
        self._command_callbacks = {}
        self._connected = False
        self._audio_aligner = None
        self._audio_busy = False
        self._sent_backend = None
        self._shortcut_config = None
        self._native_enabled = False
        self._browser_episode = None
        self._media_context = None
        self._episode_problem = None
        self._reference_fingerprint = None
        self._notice_until = 0.0
        self._notice = ""
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
        port = cfg.get("BROWSER_BRIDGE_PORT")
        self.bridge = BrowserBridge(cfg.get("BROWSER_PAIRING_TOKEN"), int(port if port is not None else 8767),
                                    cfg.get("BROWSER_TRUSTED_EXTENSION_ORIGINS") or ())
        base = Path(cfg.local_path).resolve().parent
        self.cache = TimingMapCache(base / "timing_maps")
        self._trim_path = base / "subtitle_trims.json"
        try:
            self._trims = json.loads(self._trim_path.read_text("utf-8"))
        except (OSError, ValueError):
            self._trims = {}
        self._pending_directory = base / "pending_capture_media"
        if self._pending_directory.exists():
            for path in self._pending_directory.glob("*.json"):
                try:
                    saved = json.loads(path.read_text("utf-8"))
                    session = CaptureSession(FrozenActionTarget.create(selected_text="", subtitle_text="", sentence_key=""), id=saved["operationId"], backend="native", phase="failed")
                    session.set_target_note(saved["noteId"])
                    session.cancelled = bool(saved.get("cancelled"))
                    session.media_identity = saved.get("identity")
                    session.media_fingerprint = saved.get("fingerprint", "")
                    if session.target_note_id and not session.cancelled:
                        self._restored_media.add(session.id)
                        session.phase = "waiting_anki"
                        self._retry_at[session.id] = time.monotonic() + 1
                    self._pending_media[session.id] = (session, saved["media"])
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        self.bridge.start()
        controller.settings._on_browser_diagnostics = self._open_settings
        self._build_status()
        self._poll()

    @property
    def mapping(self):
        base = self._base_mapping
        return base.shifted(self._trim) if base is not None and self._trim else base

    @mapping.setter
    def mapping(self, value):
        self._base_mapping = value

    def _load_trim(self):
        manager = self.controller.sub_manager
        self._trim_key = digest(json.dumps([self._source_hash, manager.get_anime_name(),
            manager.get_current_season(), manager.get_current_episode()], ensure_ascii=False).encode())
        self._trim = float(self._trims.get(self._trim_key, 0.0))
        settings = self.controller.settings
        settings._last_offset_value = self._trim
        settings.offset_var.set(f"{self._trim:g} s")

    def set_trim(self, value):
        import math
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Offset must be a finite number")
        self._source()
        self._trim = value
        settings = self.controller.settings
        settings._last_offset_value = value
        settings.offset_var.set(f"{value:g} s")
        self._trims[self._trim_key] = value
        temporary = self._trim_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._trims, indent=2), "utf-8")
        temporary.replace(self._trim_path)

    @property
    def native(self):
        return True

    @property
    def fresh(self):
        return self._connected and self.following and self.snapshot is not None and time.monotonic() - self.snapshot.received < 0.4

    def _build_status(self):
        self._status_var = tk.StringVar(master=self.controller.settings.root, value=self.status)


    def retry_pending_media(self):
        for session, result in list(self._pending_media.values()):
            if session.target_note_id and session.phase == "failed" and not session.cancelled:
                self._attach_media(session, result)


    def _open_settings(self):
        root = tk.Toplevel(self.controller.settings.root)
        root.title("Browser connection")
        root.geometry("620x440")
        mapping = self.mapping
        if mapping:
            tk.Label(root, text=f"Timing evidence: {mapping.method}; {sum(mapping.confidence)}/{len(mapping.source)} supported cues; trim {self._trim:+.3f}s", wraplength=590).pack(padx=12, pady=5)
        tk.Label(root, text=f"Pending media operations: {len(self._pending_media)}", anchor="w").pack(padx=12, pady=5)
        text = "Open the companion extension on your video tab, select the video and click Connect.\nAllow its first connection here once; later connections are automatic.\nRecorder and after-capture options are in Advanced Settings → Anki → Browser Recording."
        tk.Label(root, text=text, justify="left", wraplength=560).pack(padx=12, pady=12)
        tk.Label(root, textvariable=self._status_var, wraplength=560).pack(padx=12, pady=5)
        ttk.Button(root, text="Show subtitles", command=lambda: self.controller.subtitle_navigation.on_refresh_subtitles(None)).pack(pady=5)
        def retry():
            self.bridge.start()
            self._set_status("Waiting for Chrome connection")
        ttk.Button(root, text="Retry connection", command=retry).pack(pady=5)
        code = ttk.Entry(root, width=65)
        code.insert(0, self.bridge.token)
        def copy():
            root.clipboard_clear(); root.clipboard_append(self.bridge.token)
        copy_button = ttk.Button(root, text="Copy pairing code", command=copy)
        def show_manual():
            code.pack(padx=12); copy_button.pack(pady=5)
        ttk.Button(root, text="Manual pairing fallback", command=show_manual).pack(pady=5)

    def _set_status(self, value):
        self.status = value
        self._status_var.set(value)

    def report_error(self, value):
        self._notice, self._notice_until = value, time.monotonic() + 20
        logger.warning("%s", value)
        self._set_status(value)
        # Brief, non-modal failure feedback; detailed state stays in Advanced Settings.
        root = self.controller.settings.root
        previous = getattr(self, "_error_toast", None)
        if previous is not None:
            try:
                previous.destroy()
            except tk.TclError:
                pass
        toast = self._error_toast = tk.Toplevel(root)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        tk.Label(toast, text=value[:300], justify="left", wraplength=420,
                 bg="#fff2d5", fg="#5c3000", padx=12, pady=9).pack()
        toast.geometry(f"+{max(0, root.winfo_rootx())}+{max(0, root.winfo_rooty() + root.winfo_height() + 6)}")
        from utils import make_nonactivating_tool_window, show_window_no_activate
        make_nonactivating_tool_window(toast, topmost=True)
        show_window_no_activate(toast)
        root.after(7000, lambda: toast.destroy() if toast.winfo_exists() else None)

    def _source(self):
        manager = self.controller.sub_manager
        path = Path(manager.srt_file)
        stat = path.stat()
        key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size,
               tuple(self.controller.episode_controller._get_display_start_times()))
        if key != self._source_key:
            self._source_key = key
            self._source_hash = digest(path.read_bytes())
            self._load_trim()
            self._source_intervals = tuple(zip(self.controller.episode_controller._get_display_start_times(),
                                              self.controller.episode_controller._get_display_end_times()))
            self.invalidate()
        return self._source_hash, self._source_intervals

    def invalidate(self):
        callbacks = getattr(self, "_command_callbacks", {})
        pending_callbacks = list(callbacks.values())
        callbacks.clear()
        getattr(self, "_commands", {}).clear()
        self._pending_seek = None
        for callback in pending_callbacks:
            callback(False)
        self._generation += 1
        self.mapping = None
        self._analyzing = False
        self._alignment_attempt_key = None
        self._audio_aligner = None
        if self._capture:
            self.abort("Subtitle or media source changed")

    def _align(self):
        if self._analyzing or not self._reference or self.snapshot is None:
            return
        source_hash, source = self._source()
        fingerprint = self.snapshot.fingerprint
        reference = tuple(self._reference)
        attempt = (source_hash, fingerprint, reference, self._reference_kind)
        if self._alignment_attempt_key == attempt:
            return
        self._alignment_attempt_key = attempt
        original_source = source
        first = 0
        if self._reference_kind == "rendered":
            indices = [i for i, (a, b) in enumerate(source) if b >= reference[0][0] - 30 and a <= reference[-1][1] + 30]
            if not indices:
                return
            first = indices[0]
            source = source[first:indices[-1] + 1]
        reference_hash = digest(("onsets-v2:" + json.dumps(reference)).encode())
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
                if len(source) != len(original_source):
                    from model.timing_map import TimingMap
                    video, confidence = [], []
                    for i, interval in enumerate(original_source):
                        if first <= i < first + len(source):
                            video.append(result.video[i-first]); confidence.append(result.confidence[i-first])
                        else:
                            edge = 0 if i < first else -1
                            offset = result.video[edge][0] - result.source[edge][0]
                            video.append(tuple(t + offset for t in interval)); confidence.append(False)
                    result = TimingMap(source_hash, fingerprint, result.reference_hash, original_source,
                                       tuple(video), tuple(confidence), "rendered-captions")
                self.cache.save(result)
                self._results.put(("alignment", (generation, reference_hash), result))
            except Exception as exc:
                self._results.put(("alignment-error", (generation, reference_hash), str(exc)))
        threading.Thread(target=work, name="subtitle-alignment", daemon=True).start()

    def command(self, action, **arguments):
        if not self._connected or not self.following or self.identity is None:
            raise ConnectionError("Connect a browser video to control playback")
        if self._capture and action != "abort":
            self.abort("Playback changed by SubtitlePlayer")
        self._control_generation = getattr(self, "_control_generation", 0) + 1
        callback = arguments.pop("on_complete", None)
        operation = self.bridge.command(self.identity, action, **arguments)
        if callback:
            self._command_callbacks[operation] = callback
        self._commands[operation] = time.monotonic() + (12.0 if action == "seek" else 3.0)
        if action == "seek":
            self._pending_seek = (operation, float(arguments["time"]))
        return operation


    def seek_relative(self, seconds, on_complete=None):
        pending = self._pending_seek
        base = pending[1] if pending else self.snapshot.time_at()
        return self.command("seek", time=max(0, min(self.snapshot.duration, base + float(seconds))), on_complete=on_complete)

    def _terminal_media_failure(self, session, error):
        session.phase = "cancelled" if session.cancelled else "failed"
        self.report_error("Anki media incomplete ? " + str(error))
        self._finish_native(session, False)
        try:
            self.bridge.send(dict(type="CaptureCommit", operationId=session.id, ok=False, error=str(error)))
        except ConnectionError:
            pass
        self._failure_pause = (session.media_identity, session.media_fingerprint)
        self._apply_failure_pause()

    def _apply_failure_pause(self):
        pending = self._failure_pause
        if not pending or self._capture:
            return
        self._failure_pause = None
        if (self._connected and self.identity and self.snapshot and
                pending == (self.identity.wire(), self.snapshot.fingerprint)):
            try:
                self.command("pause")
            except ConnectionError:
                pass

    def source_time(self, video_time):
        return self.mapping.to_source(video_time) if self.mapping else -1.0

    def video_time(self, source_time):
        if not self.mapping:
            raise ValueError("Finding subtitle timing")
        return self.mapping.to_video(source_time)

    def freeze_capture(self, session):
        if not self.native:
            return
        if self._capture:
            raise ValueError("A recording is already in progress")
        if not self.fresh or not self.mapping:
            raise ValueError("Alignment uncertain: connect Chrome and wait for timing")
        if not self._native_enabled:
            raise ValueError("Click Enable native recording in the browser extension first")
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

    def capture(self, session, note_id=None):
        if self._capture:
            raise RuntimeError("A recording is already in progress")
        if not session or session.backend != "native" or session.cancelled:
            raise ValueError("Invalid native capture operation")
        if note_id is not None and int(note_id) <= 0:
            raise ValueError("Native capture requires a valid Anki note ID")
        if not self.fresh or session.media_identity != self.identity.wire() or session.media_fingerprint != self.snapshot.fingerprint:
            raise ValueError("Connected video changed; capture cancelled")
        if note_id is not None:
            session.set_target_note(note_id)
        session.phase = "recording"
        self._capture = session
        self._operations[session.id] = session
        logger.info("Native capture %s started for note %s, interval %.3f..%.3f, map %s",
                    session.id, note_id, *session.mapped_interval, session.timing_version)
        start, end = session.mapped_interval
        try:
            self.bridge.send(dict(type="CaptureRequest", operationId=session.id, identity=session.media_identity,
                                  fingerprint=session.media_fingerprint, timingVersion=session.timing_version,
                                  start=start, end=end, noteId=note_id, screenshotTime=(start + end) / 2,
                                  policy=session.post_capture_policy))
        except ConnectionError:
            session.phase = "failed"
            self._capture = None
            raise
        self._set_status("Recording subtitle…")

    def bind_capture_note(self, session, note_id):
        """Join independently completed recording and note creation on Tk's thread."""
        note_id = int(note_id or 0)
        if note_id <= 0 or (session.target_note_id and session.target_note_id != note_id):
            raise ValueError("Capture target must be the exact newly created note")
        session.set_target_note(note_id)
        if session.cancelled or session.phase == "failed":
            self._finish_native(session, False)
            return
        if not session.cancelled and session.native_result:
            self._complete_native_result(session)

    def capture_note_failed(self, session, error="Anki card creation failed"):
        if session.id not in self._operations:
            return
        if session.phase == "encoding" and not session.native_result:
            # The clip already ended. Keep its encoding result even if Anki failed.
            session.creation_error = error
            self.report_error(error + ". Finished recording will be saved without a guessed target.")
            return
        if session.native_result:
            # Keep a finished recording even when card creation failed. Without
            # an explicit note ID it must never attach to a guessed/newest note.
            self._pending_media[session.id] = (session, session.native_result)
            try:
                self._save_pending_media(session, session.native_result)
            except OSError:
                logger.exception("Could not save recording after card creation failed")
        if self._capture is session:
            self.abort(error)
        else:
            session.cancel(error)
            self._terminal_media_failure(session, error)

    def _complete_native_result(self, session):
        if not session.target_note_id or session.cancelled:
            return
        result = session.native_result
        if not result or session.phase != "waiting_note":
            return
        self._attach_media(session, result)

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

    def cancel_pending(self, session, reason="Cancelled"):
        if self._capture is session:
            self.abort(reason)
            return
        session.cancel(reason)
        self._retry_at.pop(session.id, None)
        pending = self._pending_media.get(session.id)
        if pending:
            self._save_pending_media(*pending)
        self._finish_native(session, False)
        try:
            self.bridge.send(dict(type="CaptureCommit", operationId=session.id, ok=False, error=reason))
        except ConnectionError:
            pass

    def _finish_native(self, session, ok):
        # Sessions remain in add history; media belongs only to pending storage
        # or Anki, otherwise each completed capture would retain its WAV/PNG.
        session.native_result = None
        if session.target_note_id and not session.media_completion_notified:
            self.controller.hotkey_controller.notify_capture_media_finished(session.target_note_id, ok)
            session.media_completion_notified = True
        if session.id in self._finished_operations:
            return
        self._finished_operations.add(session.id)
        if len(self._finished_operations) > 512:
            self._finished_operations.pop()
        popup = getattr(self.controller, "popup", None)
        mark = getattr(popup, "mark_anki_success" if ok else "mark_anki_failure", None)
        if callable(mark):
            mark()
        self.controller.anki_controller._finish_action_feedback_key(session.action_feedback_key, ok)
        self.controller.release_anki_pause_lease(session.pause_lease, resume=False)

    def _save_pending_media(self, session, result):
        self._pending_directory.mkdir(parents=True, exist_ok=True)
        path = self._pending_directory / (session.id + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(dict(operationId=session.id, noteId=session.target_note_id, identity=session.media_identity, fingerprint=session.media_fingerprint, cancelled=session.cancelled, media=result)), "utf-8")
        temporary.replace(path)

    def _attach_media(self, session, result):
        self._pending_media[session.id] = (session, result)
        session.phase = "pending_media"
        self._set_status("Finishing Anki media…")
        def work():
            try:
                if session.id in getattr(self, "_restored_media", ()):
                    if not self.controller.anki.ping_quietly():
                        self._results.put(("waiting-anki", session.id, None))
                        return
                self._save_pending_media(session, result)
                if not session.target_note_id:
                    raise RuntimeError("Recording saved without an Anki note. Card creation failed; no media was attached.")
                if session.cancelled:
                    raise RuntimeError("Capture cancelled")
                self.controller.anki.attach_native_capture(session.target_note_id, session.id,
                    result["audio"], result["image"], is_cancelled=lambda: session.cancelled)
                logger.info("Native capture %s media verified on note %s", session.id, session.target_note_id)
                self._results.put(("media", session.id, None))
            except Exception as exc:
                error = str(exc)
                if "affected editor" in error.lower():
                    error = "Close Anki's note editor. Your capture is saved and attachment retries automatically."
                self._results.put(("media", session.id, error))
        self.controller.anki_controller.submit_anki_work(work)

    def _event(self, event):
        kind = event["type"]
        if kind == "PairingRequested":
            settings = self.controller.settings
            settings.adv_settings._set_modal_input_active(True)
            try:
                allowed = messagebox.askyesno("Connect browser?",
                    "Allow the browser extension to connect to SubtitlePlayer?\n\nOnly allow this if you just clicked Connect in the SubtitlePlayer companion extension. It can control the selected video and provide Anki capture media.\n\nYou only need to approve it once.",
                    parent=settings.root)
            finally:
                settings.adv_settings._set_modal_input_active(False)
            self.bridge.approve_pairing(event["requestId"], allowed)
        elif kind == "Connected":
            self._sent_backend = None
            self._shortcut_config = None
        elif kind == "TrustedOrigin":
            origins = set(self.controller.config.get("BROWSER_TRUSTED_EXTENSION_ORIGINS") or ())
            origins.add(event["origin"])
            self.controller.config.set("BROWSER_TRUSTED_EXTENSION_ORIGINS", sorted(origins))
        elif kind == "Bound":
            logger.info("Browser video bound: %s", event["identity"])
            self._connected = True
            identity = MediaIdentity.read(event["identity"])
            if identity != self.identity:
                had_media_context = bool(getattr(self, "_media_context", None))
                self._media_context = None
                if had_media_context:
                    self.controller.episode_controller.update_media_presentation()
                self.invalidate(); self.snapshot = None; self._reference = (); self._reference_kind = "none"
                self._caption_last = None; self._observed_intervals = []
            self.identity = identity
            self._shortcut_config = None
            self.following = True
            if not self.mapping:
                self._set_status("Finding timing?")
            self._send_recorder_config()
        elif kind == "Disconnected":
            self._connected = False
            self._native_enabled = False
            self.abort("Chrome disconnected")
            # Keep following enabled: never fall back silently to the old clock.
            self._set_status("Chrome disconnected · clock frozen")
        elif kind == "VideoUnavailable":
            self.abort("Browser video changed")
            self.snapshot = None
            self.invalidate()
            self._set_status("Waiting for the next browser video")
        elif kind == "MediaContext":
            if self.identity and MediaIdentity.read(event["identity"]) == self.identity:
                context = {key: event.get(key) for key in ("title", "kind", "episode", "season", "pageKey")}
                if context != getattr(self, "_media_context", None):
                    self._media_context = context
                    self.controller.episode_controller.update_media_presentation()
        elif kind == "EpisodeContext":
            if self.identity and MediaIdentity.read(event["identity"]) == self.identity:
                self._follow_episode(event)
        elif kind == "RecorderState":
            if self.identity and MediaIdentity.read(event["identity"]) == self.identity:
                self._native_enabled = bool(event.get("enabled"))
        elif kind == "Unbound":
            self.abort("Disconnected by user")
            self.following = False
            if getattr(self, "_media_context", None):
                self._media_context = None
                self.controller.episode_controller.update_media_presentation()
            self._connected = False
            self.snapshot = None
            self.controller.playing = False
            self._set_status("Legacy controls · Chrome disconnected")
        elif kind == "PlaybackSnapshot":
            self.controller._browser_text_input_focused = bool(event.get("textInput"))
            snapshot = PlaybackSnapshot.read(event, now=event.get("_received_monotonic"))
            if snapshot.identity != self.identity:
                return
            if self.snapshot and snapshot.sequence <= self.snapshot.sequence:
                return
            if self.snapshot and self.snapshot.fingerprint != snapshot.fingerprint:
                self.invalidate(); self._reference = (); self._reference_kind = "none"
                self._caption_last = None; self._observed_intervals = []
            self.snapshot = snapshot
            if self._reference_fingerprint and self._reference_fingerprint != snapshot.fingerprint:
                self._reference = ()
                self._reference_kind = "none"
                self._reference_fingerprint = None
            self._source()
            if self._reference and self.mapping is None:
                self._align()
        elif kind in ("CaptionText", "CaptionIntervals"):
            if MediaIdentity.read(event["identity"]) != self.identity:
                return
            if event.get("fingerprint") and self.snapshot and event["fingerprint"] != self.snapshot.fingerprint:
                return
            intervals = parse_intervals(event["text"]) if kind == "CaptionText" else tuple(map(tuple, event["intervals"]))
            if intervals != self._reference:
                logger.info("Browser caption timing reference: %s intervals (%s)", len(intervals), kind)
                self._reference_kind = "full"
                self._reference_fingerprint = event.get("fingerprint")
                self._reference = intervals
                self._align()
        elif kind == "CaptionUnavailable":
            logger.warning("Browser caption resource: %s", event.get("error", "Unavailable"))
            if not self._reference:
                self._set_status(event["error"])
        elif kind == "CaptionObservation":
            if self._reference_kind == "full" or MediaIdentity.read(event["identity"]) != self.identity:
                return
            previous = self._caption_last
            current = (str(event.get("text") or ""), float(event["time"]))
            self._caption_last = current
            if previous and previous[0] and .3 <= current[1] - previous[1] <= 15:
                self._observed_intervals.append((previous[1], current[1]))
                self._observed_intervals = self._observed_intervals[-1000:]
                if len(self._observed_intervals) >= 12 and len(self._observed_intervals) % 4 == 0:
                    self._reference_kind = "rendered"
                    self._reference = tuple(sorted(set(self._observed_intervals)))
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
                    self._results.put(("audio-alignment", generation, mapping))
                except Exception as exc:
                    self._results.put(("audio-error", generation, str(exc)))
                finally:
                    self._audio_busy = False
            threading.Thread(target=analyze, name="local-audio-alignment", daemon=True).start()
        elif kind == "CommandAck":
            self._commands.pop(event.get("operationId"), None)
            callback = self._command_callbacks.pop(event.get("operationId"), None)
            if self._pending_seek and self._pending_seek[0] == event.get("operationId"):
                self._pending_seek = None
            if not event.get("ok"):
                self._set_status("Video command failed: " + event.get("error", "Unknown error"))
            elif event.get("state", {}).get("identity"):
                self._event({**event["state"], "type": "PlaybackSnapshot", "_received_monotonic": event.get("_received_monotonic")})
            if callback:
                callback(bool(event.get("ok")))
        elif kind == "RecordingEnded":
            session = self._operations.get(event.get("operationId"))
            if session and self._capture is session:
                session.phase = "encoding"
                self._capture = None
            self._apply_failure_pause()
        elif kind == "CaptureResult":
            session = self._operations.get(event.get("operationId"))
            if not session or event.get("operationId") != session.id or session.cancelled:
                return
            if not event.get("ok"):
                ended = session.phase == "encoding"
                session.phase = "failed"
                if self._capture is session:
                    self._capture = None
                error = event.get("error", "Unknown error")
                if ended:
                    self._terminal_media_failure(session, error)
                else:
                    # The extension has already stopped/paused an active failed clip.
                    self.report_error("Capture failed - " + error)
                    self._finish_native(session, False)
                return
            if session.phase not in ("recording", "encoding"):
                return
            if event.get("identity") != session.media_identity:
                session.cancel("Stale capture result")
                self._finish_native(session, False)
                return
            session.native_result = event
            session.phase = "waiting_note"
            if not session.target_note_id:
                if not event.get("reused"):
                    self._pending_media[session.id] = (session, event)
                    self._save_pending_media(session, event)
                self._set_status("Recording ready · waiting for Anki card…")
            if session.creation_error:
                self._pending_media[session.id] = (session, event)
                self._save_pending_media(session, event)
                self._terminal_media_failure(session, session.creation_error)
            else:
                self._complete_native_result(session)
        elif kind == "CaptureFinished":
            pass  # Media is verified locally; this acknowledgement cannot control playback.

    def _poll(self):
        if self._closed:
            return
        if self._connected:
            try:
                self._send_recorder_config()
            except ConnectionError:
                pass
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
                    generation, reference_hash = identity
                    if generation != self._generation:
                        continue
                    self._analyzing = False
                    if reference_hash != digest(("onsets-v2:" + json.dumps(tuple(self._reference))).encode()):
                        self._align()
                        continue
                    if kind == "alignment":
                        self.mapping = value
                        logger.info("Subtitle timing aligned: %s, %s/%s confident cues, offsets %.3f..%.3f",
                                    value.method, sum(value.confidence), len(value.source),
                                    min(v[0]-s[0] for s,v in zip(value.source,value.video)),
                                    max(v[0]-s[0] for s,v in zip(value.source,value.video)))
                        self.bridge.send(dict(type="TimingMap", version=value.version, video=value.video))
                    else:
                        self.report_error("Alignment uncertain · " + value)
                elif kind == "audio-alignment" and identity == self._generation:
                    if self._reference_kind != "full":
                        self.mapping = value
                        self.bridge.send(dict(type="TimingMap", version=value.version, video=value.video))
                elif kind == "audio-error" and identity == self._generation:
                    self._set_status("Alignment uncertain · " + value)
                elif kind == "waiting-anki":
                    pending = self._pending_media.get(identity)
                    if pending and not pending[0].cancelled:
                        pending[0].phase = "waiting_anki"
                        self._retry_at[identity] = time.monotonic() + 5
                        self._set_status("Waiting for Anki - saved recordings will attach automatically")
                elif kind == "media":
                    pending = self._pending_media.get(identity)
                    if pending is None:
                        continue
                    session, result = pending
                    if value or session.cancelled:
                        session.phase = "cancelled" if session.cancelled else "failed"
                        attempts = self._retry_count.get(identity, 0)
                        permanent = any(text in str(value).lower() for text in (
                            "missing media field", "not configured", "invalid note", "note not found", "no longer exists"))
                        if not session.cancelled and not permanent and attempts < 4:
                            self._retry_count[identity] = attempts + 1
                            self._retry_at[identity] = time.monotonic() + (1, 3, 10, 30)[attempts]
                            self.report_error("Media saved ? retrying Anki attachment automatically")
                        elif session.cancelled:
                            self._save_pending_media(session, result)
                            self._finish_native(session, False)
                        else:
                            self._terminal_media_failure(session, value)
                    else:
                        del self._pending_media[identity]
                        getattr(self, "_restored_media", set()).discard(identity)
                        (self._pending_directory / (identity + ".json")).unlink(missing_ok=True)
                        self._retry_at.pop(identity, None)
                        session.phase = "completed"
                        self._operations.pop(session.id, None)
                        self._finish_native(session, True)
                        try:
                            self.bridge.send(dict(type="CaptureCommit", operationId=identity, ok=True))
                        except ConnectionError:
                            pass
        except queue.Empty:
            pass
        except ConnectionError:
            self._set_status("Chrome disconnected · media pending")
        if self.following and self.snapshot:
            c = self.controller
            if self._capture and time.monotonic() - self.snapshot.received > 5.0:
                self.abort("Video clock unavailable")
            c.current_time = self.snapshot.time_at()
            c.playing = self.fresh and not (self.snapshot.paused or self.snapshot.buffering or self.snapshot.seeking)
            c.playback._set_play_button_state()
            if not c.slider_dragging:
                c.settings.slider.config(to=self.snapshot.duration)
                c.playback._publish_current_time()
            if not self._capture and self.mapping and self.fresh and not self._pending_media:
                idx = self.mapping.cue_at(c.current_time)
                aligned = "Aligned · enable native recording" if self.native and not self._native_enabled else "Aligned · Chrome"
                self._set_status(aligned if idx is None or self.mapping.confidence[idx] else "Alignment uncertain")
            elif not self.fresh:
                self._set_status("Clock observation delayed ? clock frozen" if self._connected else "Chrome disconnected ? clock frozen")
                now = time.monotonic()
                if self._connected and now - self._last_probe > .5:
                    self._last_probe = now
                    try:
                        self.bridge.send(dict(type="PlaybackProbe", identity=self.identity.wire()))
                    except ConnectionError:
                        pass
        for operation, due in list(self._retry_at.items()):
            if time.monotonic() >= due:
                del self._retry_at[operation]
                pending = self._pending_media.get(operation)
                if pending and pending[0].target_note_id and not pending[0].cancelled:
                    self._attach_media(*pending)
        self._apply_failure_pause()
        if self._episode_problem:
            self._set_status(self._episode_problem)
        if time.monotonic() < getattr(self, "_notice_until", 0):
            self._set_status(self._notice)
        for operation, deadline in list(self._commands.items()):
            if time.monotonic() > deadline:
                del self._commands[operation]
                if self._pending_seek and self._pending_seek[0] == operation:
                    self._pending_seek = None
                callback = self._command_callbacks.pop(operation, None)
                if callback:
                    callback(False)
                self._set_status("Video command not acknowledged")
        if self.bridge.error:
            self._set_status("Browser bridge unavailable: " + self.bridge.error)
        self._job = self.controller.settings.root.after(50, self._poll)

    def _send_recorder_config(self):
        hotkeys = self.controller.hotkey_controller
        bindings = [binding for _, binding in hotkeys._repeat_action_bindings() + hotkeys._single_fire_bindings()]
        config = tuple(bindings) if not hotkeys._hotkeys_disabled() else ()
        if config != self._shortcut_config:
            self.bridge.send(dict(type="ShortcutConfig", identity=self.identity.wire(), bindings=config))
            self._shortcut_config = config
        backend = "native"
        if backend != self._sent_backend:
            self.bridge.send(dict(type="RecorderConfig", backend=backend))
            self._sent_backend = backend

    def _follow_episode(self, event):
        """Use explicit site episode metadata within the already selected series."""
        try:
            episode = int(event["episode"])
            season = int(event["season"]) if event.get("season") else None
            if not 0 < episode < 10000 or (season is not None and not 0 < season < 1000):
                return
        except (ValueError, TypeError, KeyError):
            return
        context = (event["seriesKey"], season, episode)
        if context == self._browser_episode:
            return
        manager = self.controller.sub_manager
        normalize = lambda value: re.sub(r"[\W_]", "", str(value).casefold())
        anime = normalize(manager.get_anime_name())
        if self._browser_episode is None:
            title = normalize(event.get("title", ""))
            if len(anime) < 4 or anime not in title:
                self._episode_problem = "Select matching anime subtitles before following browser episodes"
                return
        elif context[0] != self._browser_episode[0]:
            self._episode_problem = "New browser series or season: select its matching subtitles"
            return
        self._browser_episode = context
        if season is None and event.get("scope") == "season":
            season = manager.get_current_season()
        navigation = self.controller.episode_controller
        if navigation._requested_episode_is_current(episode, season):
            self._episode_problem = None
            return
        # Clear the previous map before loading or displaying another episode.
        self.invalidate()
        self._reference = ()
        self._reference_kind = "none"
        self._reference_fingerprint = None
        navigation.save_current_episode_position()
        target_season, target_episode = manager.change_episode("set", episode, season)
        if target_episode is None or not navigation._requested_episode_is_current(episode, season):
            self._episode_problem = f"Subtitle episode {episode} unavailable: refresh its episode list"
            return
        navigation._after_episode_change()
        self._episode_problem = None
        self._set_status(f"Episode {episode} · finding timing")

    def close(self):
        self._closed = True
        self.abort("Application closed")
        if self._job:
            self.controller.settings.root.after_cancel(self._job)
        self.bridge.close()
