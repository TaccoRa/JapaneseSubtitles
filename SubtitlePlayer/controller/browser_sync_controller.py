"""Browser clock, independent subtitle map and correlated native media writes."""
import base64
import json
import logging
from pathlib import Path
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, messagebox

from model.browser_bridge import BrowserBridge
from model.browser_protocol import MediaIdentity, PlaybackSnapshot
from model.browser_sources import BrowserSources
from model.browser_titles import browser_search_title
from model.browser_saved_search import prepare_selection
from model.alignment_preferences import AlignmentPreferences, MODES
from model.alignment_worker import AlignmentWorker
from model.source_worker import SourceWorker
from model.jimaku_series import SeriesConfirmationRequired
from model.asbplayer_legacy import LegacyAsbplayerCapture, legacy_value
from model.subtitle_alignment import parse_intervals
from model.timing_map import TimingMap, TimingMapCache, digest
from model.capture_session import CaptureSession, FrozenActionTarget
from controller.jimaku_navigation import JimakuNavigation
from controller.anilist_controller import AniListController

logger = logging.getLogger(__name__)
_pending_media_write_lock = threading.RLock()



class BrowserSyncController:
    def __init__(self, controller):
        self.controller = controller
        from controller.episode_automation import EpisodeAutomation
        self.episode_automation = EpisodeAutomation(self)
        self.snapshot = None
        self.identity = None
        self.following = False
        self._base_mapping = None
        self._trim = 0.0
        self._manual_offset = None
        self._manual_mapping = None
        self._trim_key = None
        self.alignment_mode = "captions"
        self._preference_context = None
        self._offset_region = None
        self._auto_offset = None
        self.status = "Chrome disconnected"
        self._source_key = None
        self._source_hash = ""
        self._source_intervals = ()
        self._generation = 0
        self._analyzing = False
        self._alignment_attempt_key = None
        self._alignment_problem = "Waiting for timing evidence"
        self._pending_alignment = None
        self._alignment_due = 0.0
        self._alignment_worker = None
        self._timing_cache_attempt = None
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
        self._cache_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="timing-cache")
        self._source_worker = SourceWorker()
        self._restore_serial = 0
        self._pending_caption_event = None
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
        self._last_recorder_probe = 0.0
        self._recorder_problem = ""
        self._asb = LegacyAsbplayerCapture(controller.config)
        self._browser_episode = None
        self._progress_binding = None
        self._source_selection_request = None
        self._source_selection_results = queue.Queue()
        self.jimaku_navigation = JimakuNavigation(controller)
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
        if not cfg.get("POST_CAPTURE_PLAYBACK"):
            updates["POST_CAPTURE_PLAYBACK"] = "play"
        if updates:
            cfg.set_many(updates)
            controller.settings.persist_active_profile_fields({"POST_CAPTURE_PLAYBACK": updates.get("POST_CAPTURE_PLAYBACK", cfg.get("POST_CAPTURE_PLAYBACK"))})
        port = cfg.get("BROWSER_BRIDGE_PORT")
        self.bridge = BrowserBridge(cfg.get("BROWSER_PAIRING_TOKEN"), int(port if port is not None else 8767),
                                    cfg.get("BROWSER_TRUSTED_EXTENSION_ORIGINS") or ())
        base = Path(cfg.local_path).resolve().parent
        self._source_bindings = BrowserSources(base / "browser_subtitle_sources.json")
        self._media_key = None
        self._last_episode_context = None
        self.cache = TimingMapCache(base / "timing_maps")
        self._trim_path = base / "subtitle_trims.json"
        try:
            self._trims = json.loads(self._trim_path.read_text("utf-8"))
        except (OSError, ValueError):
            self._trims = {}
        self.preferences = AlignmentPreferences(base / "alignment_preferences.json", self._trims)
        self.anilist = AniListController(self, base)
        controller.settings._on_alignment_mode = self.set_alignment_mode
        controller.settings._on_realign = self.request_alignment
        controller.settings._on_offset_begin = self.begin_offset_edit
        controller.settings._on_offset_end = self.end_offset_edit
        self._pending_directory = base / "pending_capture_media"
        if self._pending_directory.exists():
            for path in self._pending_directory.glob("*.json"):
                try:
                    saved = json.loads(path.read_text("utf-8"))
                    session = CaptureSession(FrozenActionTarget.create(selected_text="", subtitle_text="", sentence_key=""), id=saved["operationId"], backend=saved.get("backend", "native"), phase="failed")
                    session.set_target_note(saved["noteId"])
                    session.cancelled = bool(saved.get("cancelled"))
                    session.media_identity = saved.get("identity")
                    session.media_fingerprint = saved.get("fingerprint", "")
                    session.terminal_error = str(saved.get("terminalError") or "")
                    session.replace_sentence_media = bool(saved.get("replaceSentenceMedia"))
                    if session.target_note_id and not session.cancelled and not session.terminal_error:
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
        manual = self._manual_offset
        if manual is not None and self.snapshot and self._source_intervals:
            key = (self._source_hash, self.snapshot.fingerprint, id(self._source_intervals), manual)
            cached = self._manual_mapping
            if cached is None or cached[0] != key:
                source = self._source_intervals
                reference = digest(json.dumps(["manual", self._trim_key, manual]).encode())
                value = TimingMap(self._source_hash, self.snapshot.fingerprint, reference, source,
                    tuple((a+manual, b+manual) for a, b in source), (True,)*len(source), method="manual")
                cached = self._manual_mapping = (key, value)
            return cached[1]
        base = self._base_mapping
        if base is None or not self._trim:
            return base
        cached = getattr(self, "_shifted_mapping", None)
        if cached is None or cached[0] is not base or cached[1] != self._trim:
            cached = self._shifted_mapping = (base, self._trim, base.shifted(self._trim))
        return cached[2]

    @mapping.setter
    def mapping(self, value):
        self._base_mapping = value
        self._shifted_mapping = None

    def _preference_media(self):
        context = getattr(self, "_media_context", None) or {}
        if context.get("pageKey"):
            return digest(json.dumps([context["pageKey"], context.get("season"), context.get("episode")]).encode())
        manager = self.controller.sub_manager
        return digest(json.dumps([manager.get_anime_name(), manager.get_current_season(),
                                  manager.get_current_episode()], ensure_ascii=False).encode())

    def _load_trim(self):
        media = self._preference_media()
        if not (self._media_context or {}).get("pageKey"):
            legacy = (self.controller.sub_manager.source_descriptor or {}).get("legacy_media", [])
            try:
                self.preferences.inherit_media(media, legacy, self._source_hash)
            except OSError:
                logger.warning("Migrated timing is active, but could not be saved", exc_info=True)
        self.alignment_mode = self.preferences.mode(media)
        self._trim_key = self.preferences.trim_key(media, self._source_hash, self.alignment_mode)
        self._trim = self.preferences.trim(media, self._source_hash, self.alignment_mode)
        self._manual_offset = self.preferences.manual_offset(media, self._source_hash, self.alignment_mode)
        self._manual_mapping = None
        self._manual_adopt_due = 0
        self._preference_context = (media, self._source_hash, self.alignment_mode)
        settings = self.controller.settings
        self._offset_edit_base = None
        settings._offset_editing = False
        value = self._manual_offset if self._manual_offset is not None else self._trim
        settings.offset_var.set(settings._format_seconds(value))
        settings._last_offset_value = value
        if hasattr(settings, "alignment_mode_var"):
            settings.alignment_mode_var.set(MODES[self.alignment_mode])

    def set_alignment_mode(self, label):
        mode = next((key for key, value in MODES.items() if label in (key, value)), None)
        if mode is None:
            raise ValueError("Unknown alignment mode")
        self.preferences.set_mode(self._preference_media(), mode)
        self._load_trim()
        # A running clip owns its already frozen map, trim and backend.
        self.invalidate(cancel_capture=False)
        self._restore_timing_cache()
        self._align()
        self._update_timing_ui()

    def _alignment_scope(self, kind):
        return self.preferences.scope(self._source_hash, self._preference_media(),
                                      self.snapshot.fingerprint, self.alignment_mode, kind)

    def _cache_mapping(self, mapping, kind):
        def save_mapping():
            try:
                self.cache.save(mapping)
            except (OSError, ValueError, TypeError):
                logger.warning("Could not save local timing cache", exc_info=True)
        try:
            self._cache_worker.submit(save_mapping)
            self.preferences.data.setdefault("maps", {})[self._alignment_scope(kind)] = mapping.reference_hash
            self.preferences.save()
        except (OSError, ValueError, TypeError):
            # Persistence must never terminate the Tk clock/bridge poll loop.
            logger.warning("Timing remains active, but its local cache could not be saved", exc_info=True)

    def _cache_reference(self, fingerprint, intervals, language):
        try:
            self.cache.save_reference(fingerprint, intervals, language)
        except (OSError, ValueError, TypeError):
            logger.warning("Could not cache caption timing evidence", exc_info=True)

    def _restore_timing_cache(self):
        if (not self.snapshot or not self._source_hash or getattr(self, "_preferred_reference_pending", False)
                or getattr(self, "_refresh_generation", None) is not None):
            return
        attempt = (self._generation, self._source_hash, self.snapshot.fingerprint, self.alignment_mode)
        if attempt == getattr(self, "_timing_cache_attempt", None):
            return
        self._timing_cache_attempt = attempt
        references = [self.preferences.data.get("maps", {}).get(self._alignment_scope(kind))
                      for kind in (("audio",) if self.alignment_mode == "audio" else ("full", "rendered"))]
        source_hash, fingerprint = self._source_hash, self.snapshot.fingerprint
        def load():
            mapping = next((value for ref in references if ref
                            if (value := self.cache.load(source_hash, fingerprint, ref)) is not None), None)
            self._results.put(("timing-cache", attempt, mapping))
        self._cache_worker.submit(load)

    def _update_timing_ui(self):
        settings = self.controller.settings
        self._update_alignment_details()
        retry = getattr(settings, "alignment_retry_button", None)
        if retry is not None:
            # Automatic analysis must not prevent an explicit user retry.
            busy = getattr(self, "_refresh_generation", None) is not None
            state = "disabled" if busy else "normal"
            if str(retry.cget("state")) != state:
                retry.configure(state=state)
        label = getattr(settings, "auto_offset_var", None)
        if label is None:
            return
        def show(text):
            if label.get() != text:
                label.set(text)
        base = self._base_mapping
        self._effective_auto_offset = None
        def publish(offset=None, *, effective=None):
            self._effective_auto_offset = offset
            if not getattr(settings, "_offset_editing", False):
                value = (effective if effective is not None else
                         self._manual_offset if self._manual_offset is not None else
                         self._trim if offset is None else offset + self._trim)
                text = settings._format_seconds(round(value, 2))
                if settings.offset_var.get() != text:
                    settings.offset_var.set(text)
                settings._last_offset_value = value
        if self._manual_offset is not None:
            publish()
            show(f"Manuell: {self._manual_offset:+.2f} s")
            return
        if not base or not self.snapshot:
            publish()
            show("Auto: Hintergrundabgleich…" if getattr(self, "_analyzing", False)
                      else "Auto: unbestätigt · SRT-Vorschau")
            return
        from bisect import bisect_right
        now = self.snapshot.time_at() - self._trim
        idx = max(0, bisect_right(base.video_starts, now) - 1)
        if not base.display_supported(idx):
            publish()
            show("Auto: unbestätigt · SRT-Vorschau")
            return
        a, b = base.source[idx]
        c, d = base.video[idx]
        rate = (d-c)/(b-a)
        offset = c-a + (now-c)*(rate-1)/rate
        if not base.confidence[idx]:
            publish(effective=offset + self._trim)
            show(f"Auto: geschätzt {offset:+.3f} s")
            return
        publish(offset)
        show(f"Auto: {offset:+.3f} s")
        # Rate/intercept describe a region independently of the playback position.
        region = (round(rate, 5), round(c-rate*a, 2))
        if region != self._offset_region:
            previous = self._auto_offset
            message = (f"Auto-Offset bestätigt: {offset:+.3f} s" if previous is None else
                       f"Auto-Offset geändert: {previous:+.3f} s → {offset:+.3f} s")
            notice = getattr(settings, "timing_notice_var", None)
            if notice is not None:
                notice.set(message)
                self._inline_until = time.monotonic() + 7
            logger.info("%s (%s)", message, base.method)
            self._offset_region = region
        self._auto_offset = offset
        if time.monotonic() > getattr(self, "_inline_until", 0):
            notice = getattr(settings, "timing_notice_var", None)
            if notice is not None and notice.get():
                notice.set("")

    def _update_alignment_details(self):
        now = time.monotonic()
        if now < getattr(self, "_details_due", 0):
            return
        self._details_due = now + .5
        var = getattr(self.controller.settings, "alignment_detail_var", None)
        if var is None:
            return
        base = self._base_mapping
        reference = getattr(self, "_reference_metadata", None)
        if self.alignment_mode == "audio":
            origin = "Lokales Audio · " + (getattr(self._audio_aligner, "language", None) or "Sprache noch unbekannt")
        else:
            origin = (f"{reference[1]} [{reference[0]}]" if reference else "Website / gespeicherter Abgleich")
        lines = ["Referenz: " + origin]
        if self._manual_offset is not None:
            lines = ["Timing: manuell durch dich festgelegt.",
                     f"Offset: {self._manual_offset:+.2f} s",
                     "Gilt für Anzeige, Sprünge und native Aufnahme.",
                     "Kein automatisch bestätigter Abgleich."]
        elif base:
            supported, low, high = base.evidence_summary
            lines += ["Verfahren: " + base.method,
                      f"Bestätigt: {supported}/{len(base.source)} Untertitel"]
            if low is not None:
                lines.append(f"Auto-Offset: {low:+.2f} bis {high:+.2f} s")
            lines.append(f"Manuelle Feinkorrektur: {self._trim:+.2f} s")
        else:
            lines.append("Hintergrundabgleich läuft." if self._analyzing else "Noch kein bestätigter Abgleich; SRT-Vorschau.")
            if self._alignment_problem and not self._analyzing:
                lines.append(str(self._alignment_problem)[:140])
        if self.alignment_mode == "captions":
            lines.append("Audio nur bei manueller Auswahl.")
        text = "\n".join(lines)
        if var.get() != text:
            var.set(text)

    def set_trim(self, value):
        import math
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Offset must be a finite number")
        self._source()
        self._trim = value
        settings = self.controller.settings

        self.preferences.set_trim(self._preference_media(), self._source_hash, self.alignment_mode, value)

    def begin_offset_edit(self):
        self._update_timing_ui()
        self._offset_edit_base = (getattr(self, "_preference_context", None), getattr(self, "_effective_auto_offset", None))

    def end_offset_edit(self):
        self._offset_edit_base = None
        self._update_timing_ui()

    def set_effective_offset(self, value):
        frozen = getattr(self, "_offset_edit_base", None)
        context, base = frozen if frozen else (getattr(self, "_preference_context", None), getattr(self, "_effective_auto_offset", None))
        if context != getattr(self, "_preference_context", None):
            return
        if base is None:
            import math
            value = float(value)
            if not math.isfinite(value):
                raise ValueError("Offset must be finite")
            self._source()
            self.preferences.set_manual_offset(self._preference_media(), self._source_hash, self.alignment_mode, value)
            self._manual_offset, self._manual_mapping = value, None
            self._manual_adopt_due = 0
            self._alignment_problem = None
            logger.info("Manual subtitle timing selected: %+.3f s", value)
        else:
            self.set_trim(float(value) - base)

    def _adopt_manual_offset(self):
        """Preserve the displayed timing when automatic evidence becomes usable."""
        manual, base = self._manual_offset, self._base_mapping
        if (manual is None or base is None or not self.snapshot or self._capture
                or getattr(self.controller.settings, "_offset_editing", False)
                or getattr(self, "_refresh_generation", None) is not None
                or time.monotonic() < getattr(self, "_manual_adopt_due", 0)
                or base.source_hash != self._source_hash or base.media_fingerprint != self.snapshot.fingerprint):
            return
        from bisect import bisect_right
        now = self.snapshot.time_at()
        source_time = now - manual
        index = bisect_right(base.source_starts, source_time) - 1
        if not (0 <= index < len(base.source) and base.confidence[index]
                and base.source[index][0] <= source_time < base.source[index][1]):
            return
        trim = now - base.to_video(source_time)
        try:
            self.preferences.adopt_automatic(self._preference_media(), self._source_hash, self.alignment_mode, trim)
        except OSError:
            self._manual_adopt_due = time.monotonic() + 30
            logger.warning("Manual timing retained; automatic trim could not be saved", exc_info=True)
            return
        self._trim, self._manual_offset, self._manual_mapping = trim, None, None
        logger.info("Manual timing adopted as automatic-map correction: %+.3f s", trim)

    def _complete_manual_alignment(self):
        if getattr(self, "_refresh_generation", None) == self._generation:
            from bisect import bisect_right
            base = self._base_mapping
            index = max(0, bisect_right(base.video_starts, self.snapshot.time_at())-1) if base and self.snapshot else None
            if index is None or not base.confidence[index]:
                self._refresh_generation = None
                self._report_alignment_problem("Current section is not confirmed; manual offset retained")
                return False
            try:
                self.preferences.adopt_automatic(self._preference_media(), self._source_hash, self.alignment_mode, 0)
            except OSError:
                self._refresh_generation = None
                self.report_error("Automatic timing could not be saved; manual offset retained")
                return False
            self._trim, self._manual_offset, self._manual_mapping = 0, None, None
            self._refresh_generation = None
        return True

    @property
    def native(self):
        return self.controller.config.get("CAPTURE_BACKEND") != "asbplayer"

    @property
    def fresh(self):
        return self._connected and self.following and self.snapshot is not None and time.monotonic() - self.snapshot.received < 0.4

    def _build_status(self):
        self._status_var = tk.StringVar(master=self.controller.settings.root, value=self.status)


    def retry_pending_media(self):
        for session, result in list(self._pending_media.values()):
            if session.target_note_id and session.phase == "failed" and not session.cancelled and not session.terminal_error:
                self._attach_media(session, result)


    def _open_settings(self):
        root = tk.Toplevel(self.controller.settings.root)
        parent = getattr(self.controller.settings, "advanced_window", None)
        if parent is not None and parent.winfo_exists():
            root.transient(parent)
        root.lift()
        root.title("Browser connection")
        root.geometry("620x480")
        recorder = "bereit · Audiostream verbunden" if self._native_enabled else (
            getattr(self, "_recorder_problem", "") or "kein Audiostream für diesen Tab")
        tk.Label(root, text="Native Aufnahme: " + recorder, wraplength=590).pack(padx=12, pady=5)
        mapping = self.mapping
        if mapping and mapping.method == "manual":
            tk.Label(root, text=f"Manuelles Timing: {self._manual_offset:+.2f} s; von dir festgelegt, nicht automatisch bestätigt.", wraplength=590).pack(padx=12, pady=5)
        elif mapping:
            tk.Label(root, text=f"Timing evidence: {mapping.method}; {sum(mapping.confidence)}/{len(mapping.source)} supported cues; trim {self._trim:+.3f}s", wraplength=590).pack(padx=12, pady=5)
        tk.Label(root, text=f"Alignment mode: {MODES.get(self.alignment_mode)} · language: {getattr(self._audio_aligner, 'language', None) or 'not detected'}", wraplength=590).pack(padx=12, pady=5)
        if self._alignment_problem:
            tk.Label(root, text=self._alignment_problem, wraplength=590).pack(padx=12, pady=4)
        for session, _ in self._pending_media.values():
            if session.terminal_error:
                tk.Label(root, text=f"Blocked note {session.target_note_id}: {session.terminal_error}", wraplength=590).pack(padx=12)
        tk.Label(root, text="asbplayer: original capture shortcut " + str(legacy_value(self.controller.config, "ASBPLAYER_CAPTURE_HOTKEY")) + "; no WebSocket/proxy setup required.", wraplength=590).pack(padx=12, pady=4)
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
        if value == getattr(self, "status", None):
            return
        self.status = value
        self._status_var.set(value)

    def report_error(self, value):
        self._notice, self._notice_until = value, time.monotonic() + 20
        logger.warning("%s", value)
        self._set_status(value)
        # Recovery and failure details stay in diagnostics; no floating windows.

    def _report_alignment_problem(self, value, *, log_warning=True):
        reason = str(value).strip()
        for prefix in ("Alignment uncertain: ", "Alignment uncertain · "):
            while reason.startswith(prefix):
                reason = reason[len(prefix):].strip()
        self._alignment_problem = reason
        message = "Alignment uncertain · " + reason
        key = (self._generation, reason)
        now = time.monotonic()
        if log_warning and (key != getattr(self, "_alignment_notice_key", None)
                or now-getattr(self, "_alignment_notice_at", 0) >= 60):
            self._alignment_notice_key, self._alignment_notice_at = key, now
            self.report_error(message)
        else:
            self._set_status(message)

    def _source(self):
        manager = self.controller.sub_manager
        if not manager.srt_file:
            return "", ()
        path = Path(manager.srt_file)
        revision = (str(path), getattr(manager, "display_revision", id(manager.display_data)))
        now = time.monotonic()
        if (self._source_key is not None and revision == getattr(self, "_source_revision", None)
                and now < getattr(self, "_source_check_due", 0)):
            return self._source_hash, self._source_intervals
        stat = path.stat()
        identity = (str(path.absolute()), stat.st_mtime_ns, stat.st_size)
        key = (*identity, revision[1])
        self._source_revision, self._source_check_due = revision, now + 1
        if key != self._source_key:
            self._source_key = key
            self._source_hash = (getattr(manager, "display_source_hash", None)
                if identity == getattr(manager, "display_file_identity", None) else None) or digest(path.read_bytes())
            self._load_trim()
            self._source_intervals = tuple(zip(self.controller.episode_controller._get_display_start_times(),
                                              self.controller.episode_controller._get_display_end_times()))
            self.invalidate()
            self.remember_browser_source()
        return self._source_hash, self._source_intervals

    def remember_browser_source(self, explicit=False, *, verified=False):
        if explicit or verified:
            self._subtitle_navigation_only = False
        elif getattr(self, "_subtitle_navigation_only", False):
            return
        bindings = getattr(self, "_source_bindings", None)
        context = getattr(self, "_media_context", None)
        if bindings is None or not context or not context.get("pageKey"):
            return
        manager = self.controller.sub_manager
        normalize = lambda text: re.sub(r"[\W_]", "", str(text or "").casefold())
        name, title = normalize(manager.get_anime_name()), normalize(context.get("title"))
        # Initial association needs a matching title. An explicit source choice
        # is authoritative, including Japanese/English title aliases.
        if not explicit and not verified and (len(name) < 3 or name not in title):
            return
        key = digest(context["pageKey"].encode())
        try:
            bindings.remember(key, manager, explicit=explicit)
            if manager.source_kind == "jimaku":
                manager.jimaku.bind(key, manager.source_descriptor, context.get("title", ""))
                episode_context = self._last_episode_context or {}
                if (not context.get("pageUrl") or episode_context.get("pageUrl") == context.get("pageUrl")):
                    manager.jimaku.bind(episode_context.get("seriesKey"), manager.source_descriptor)
        except (OSError, TypeError, ValueError):
            logger.warning("Could not persist browser subtitle association", exc_info=True)
        self._media_key = key

    def _restore_browser_source(self, key):
        if getattr(self, '_episode_navigation', None):
            return False
        if (getattr(self, "_manual_source_open", False) or
                (self.identity, (self._media_context or {}).get("pageUrl")) == getattr(self, "_manual_source_context", None)):
            return
        if getattr(self.controller.sub_manager, "startup_needs_source", False):
            return
        bindings = getattr(self, "_source_bindings", None)
        if bindings is None or key not in bindings.entries:
            return
        state = deepcopy(bindings.entries[key])
        # Numbered remote sources are resolved by the existing asynchronous
        # episode pipeline. Do not synchronously load an old file first.
        if ((self._media_context or {}).get("episode")
                and (state.get("source_kind") in {"jimaku", "github"} or state.get("remote_flag"))):
            return
        self._restore_serial += 1
        serial = self._restore_serial
        manager = self.controller.sub_manager
        source = (manager.srt_file, getattr(manager, "display_revision", None))
        context = (self.identity, (self._media_context or {}).get("pageUrl"), key, source)
        def load():
            if self._closed or serial != self._restore_serial:
                return
            try:
                path = state["srt_file"]
                if not Path(path).is_file():
                    raise FileNotFoundError("Remembered subtitle file is missing")
                state["_prepared_key"], state["_prepared_payload"] = manager.prepare_subtitle_display(path)
                result = (context, state, None)
            except Exception as exc:
                result = (context, None, exc)
            self._results.put(("source-restore", serial, result))
        def start():
            if (not self._closed and serial == self._restore_serial and self.identity == context[0]
                    and context[1] == (self._media_context or {}).get("pageUrl")):
                self.submit_source_task("restore", load)
        self.controller.settings.root.after(250, start)

    def submit_source_task(self, lane, callback):
        """Bound CPU/network work and replace obsolete queued jobs per entry point."""
        if not self._closed:
            self._source_worker.submit(lane, callback)

    def _apply_source_restore(self, serial, result):
        context, state, error = result
        identity, page_url, key, source = context
        manager = self.controller.sub_manager
        if (serial != self._restore_serial or identity != self.identity or not self.following
                or page_url != (self._media_context or {}).get("pageUrl")
                or key != digest(str((self._media_context or {}).get("pageKey", "")).encode())
                or source != (manager.srt_file, getattr(manager, "display_revision", None))):
            return
        try:
            if error:
                raise error
            if state.get("_prepared_key") != manager._prepared_episode_key(state["srt_file"]):
                self._restore_browser_source(key)
                return
            changed = self._source_bindings.restore_state(state, manager)
            self._media_key = key
            if changed:
                self._browser_episode = None
                self._episode_problem = None
                self._source_key = None
                self.invalidate()
                self.controller.episode_controller._after_episode_change()
                self._restore_timing_cache()
                self._align()
        except FileNotFoundError:
            # A deleted cache/download is recoverable from the saved search map.
            # Do not keep presenting it as a fatal error on every announcement.
            self._episode_problem = "Remembered subtitle file is missing; checking saved search"
            logger.info("Remembered browser subtitle file missing; trying saved search")
        except Exception as exc:
            logger.exception("Could not restore browser subtitle source")
            self.report_error("Could not restore this video's subtitles: " + str(exc))

    def invalidate(self, cancel_capture=True):
        if cancel_capture:
            self._clear_capture_display()
            settings = self.controller.settings
            settings._offset_editing = False
            self._offset_edit_base = None
            callbacks = getattr(self, "_command_callbacks", {})
            pending_callbacks = list(callbacks.values())
            callbacks.clear()
            getattr(self, "_commands", {}).clear()
            self._pending_seek = None
            for callback in pending_callbacks:
                callback(False)
        self._refresh_generation = None
        self._generation += 1
        self.mapping = None
        self._analyzing = False
        self._alignment_attempt_key = None
        self._pending_alignment = None
        self._timing_cache_attempt = None
        worker = getattr(self, "_alignment_worker", None)
        if worker:
            worker.cancel_pending()
        self._audio_aligner = None
        self._offset_region = None
        self._auto_offset = None
        if cancel_capture and self._capture:
            self.abort("Subtitle or media source changed")

    def request_alignment(self):
        """Explicit retry keeps a valid same-video map and frozen captures usable."""
        if not self._connected or not self.snapshot:
            self.report_error("Abgleich: zuerst ein Browser-Video verbinden")
            return
        if getattr(self, "_refresh_generation", None) is not None:
            return
        self._source()
        previous = self._base_mapping
        self.invalidate(cancel_capture=False)
        self.mapping = previous
        self._refresh_generation = self._generation
        self._refresh_deadline = time.monotonic() + 60
        self._alignment_problem = None
        try:
            self.bridge.send(dict(type="RefreshCaptions", identity=self.identity.wire()))
        except Exception as exc:
            self._refresh_generation = None
            self._refresh_deadline = None
            self.report_error(f"Abgleich konnte nicht gestartet werden: {exc}")
            self._update_timing_ui()
            return
        if self.alignment_mode == "captions":
            self._align(force=True)
        self._set_status("Timing wird im Hintergrund neu abgeglichen")
        self._update_timing_ui()

    def _align(self, force=False):
        if (getattr(self, "alignment_mode", "captions") != "captions" or
                self._analyzing and not getattr(self, "_pending_alignment", None) or
                not self._reference or self.snapshot is None):
            return
        source_hash, source = self._source()
        if not source:
            return
        fingerprint = self.snapshot.fingerprint
        reference = tuple(self._reference)
        attempt = (source_hash, fingerprint, reference, self._reference_kind)
        if self._alignment_attempt_key == attempt:
            return
        self._alignment_attempt_key = attempt
        reference_hash = digest((self._alignment_scope(self._reference_kind) + json.dumps(reference)).encode())
        self._analyzing = True
        self._analyzing_reference = (self._reference_kind, reference)
        self._pending_alignment = ((self._generation, reference_hash), dict(
            source=source, reference=reference, source_hash=source_hash, fingerprint=fingerprint,
            reference_hash=reference_hash, kind=self._reference_kind,
            cache_directory=str(self.cache.directory), force=force))
        # Let episode metadata and full caption tracks settle before spending
        # CPU on them. Playback observations and all Tk input continue normally.
        self._alignment_due = time.monotonic() + (0 if force else .8)
        self._set_status("Timing wird im Hintergrund abgeglichen")

    def _start_pending_alignment(self):
        pending = getattr(self, "_pending_alignment", None)
        if pending and time.monotonic() >= self._alignment_due:
            self._pending_alignment = None
            if getattr(self, "_alignment_worker", None) is None:
                self._alignment_worker = AlignmentWorker(self._results)
            self._alignment_worker.submit(*pending)

    def command(self, action, **arguments):
        if not self._connected or not self.following or self.identity is None:
            raise ConnectionError("Connect a browser video to control playback")
        owner = arguments.pop("_capture_owner", None)
        if self._capture and self._capture is not owner and action != "abort":
            return None  # During a clip only an explicit abort may interrupt it.
        self._control_generation = getattr(self, "_control_generation", 0) + 1
        callback = arguments.pop("on_complete", None)
        operation = self._dispatch_player_command(self.identity, action, arguments, callback=callback)
        if action == "seek":
            self._clear_capture_display()
            self._pending_seek = (operation, float(arguments["time"]))
        return operation

    def _dispatch_player_command(self, identity, action, arguments, *, callback=None, track=True):
        """Single transport boundary, including cancellation of an owned clip."""
        operation = self.bridge.command(identity, action, **arguments)
        if callback and track:
            self._command_callbacks[operation] = callback
        if track:
            self._commands[operation] = time.monotonic() + (12.0 if action == "seek" else 3.0)
        return operation


    def seek_relative(self, seconds, on_complete=None):
        pending = self._pending_seek
        base = pending[1] if pending else self.snapshot.time_at()
        return self.command("seek", time=max(0, min(self.snapshot.duration, base + float(seconds))), on_complete=on_complete)

    def _terminal_media_failure(self, session, error):
        session.phase = "cancelled" if session.cancelled else "failed"
        self.report_error("Anki media incomplete - " + str(error))
        if session.id in getattr(self, "_restored_media", ()):
            return  # Startup recovery does not own playback or current action feedback.
        self._finish_native(session, False)
        try:
            self.bridge.send(dict(type="CaptureCommit", operationId=session.id, ok=False, error=str(error)))
        except ConnectionError:
            pass
        self._failure_pause = (session.media_identity, session.media_fingerprint)
        self._apply_failure_pause()
        self._update_timing_ui()

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
        # A manual map is explicitly user-confirmed, never automatic evidence.
        if self.mapping:
            from bisect import bisect_right
            idx = max(0, bisect_right(self.mapping.video_starts, video_time)-1)
            if self.mapping.display_supported(idx):
                return self.mapping.to_source(video_time)
        return video_time - (self._manual_offset if self._manual_offset is not None else self._trim)

    def video_time(self, source_time):
        if not self.mapping:
            return source_time + (self._manual_offset if self._manual_offset is not None else self._trim)
        from bisect import bisect_right
        idx = max(0, bisect_right(self.mapping.source_starts, source_time)-1)
        if not self.mapping.display_supported(idx):
            return source_time + self._trim
        return self.mapping.to_video(source_time)

    def _begin_capture_display(self, session):
        if session.target.cue_snapshot is not None and session.mapped_cue_start is not None:
            self._capture_display = dict(session=session, replay_seen=False, seek_seen=False)

    def _clear_capture_display(self, operation_id=None):
        hold = getattr(self, "_capture_display", None)
        if hold and (operation_id is None or hold["session"].id == operation_id):
            self._capture_display = None

    def _observe_capture_display(self, snapshot, event="tick"):
        hold = getattr(self, "_capture_display", None)
        if not hold:
            return
        session = hold["session"]
        if (session.cancelled or session.media_identity != snapshot.identity.wire()
                or session.media_fingerprint != snapshot.fingerprint):
            self._clear_capture_display(session.id)
            return
        if event == "seeking":
            hold["seek_seen"] = True
        if (snapshot.current_time < session.mapped_cue_start
                or session.recording_started_at > 0 or event == "seeked" and hold["seek_seen"]):
            hold["replay_seen"] = True
        if hold["replay_seen"] and snapshot.current_time >= session.mapped_cue_start and not snapshot.seeking:
            self._clear_capture_display(session.id)

    def capture_display_snapshot(self):
        hold = getattr(self, "_capture_display", None)
        if hold:
            target = hold["session"].target
            if target.source_hash and target.source_hash != getattr(self, "_source_hash", None):
                self._clear_capture_display(hold["session"].id)
                return None
            return target.cue_snapshot
        return None

    def _freeze_asb_capture(self, session):
        if self._capture:
            raise ValueError("An asbplayer recording is already in progress")
        session.backend = "asbplayer"
        session.media_identity = self.identity.wire() if self.identity else None
        session.media_fingerprint = self.snapshot.fingerprint if self.snapshot else ""
        session.timing_version = "asbplayer-original-shortcut"
        cue = session.target.cue_snapshot
        if cue is not None:
            session.mapped_interval = (self.video_time(cue.start_time), self.video_time(cue.end_time))
            session.mapped_cue_start = session.mapped_interval[0]
        self._freeze_playback_policy(session)

    def _capture_asb(self, session, note_id=None):
        if self._capture is not None and self._capture is not session:
            raise ValueError("An asbplayer recording is already in progress")
        if session.cancelled:
            raise ValueError("asbplayer capture cancelled")
        self._operations[session.id] = session
        self._capture = session
        if note_id is None:
            session.phase = "waiting_note"
            return
        if session.phase in ("preparing", "capture_sent"):
            return
        session.set_target_note(note_id)
        if not session.target_note_id:
            raise ValueError("asbplayer needs the card to be created first")
        session.phase = "preparing"
        def prepare():
            try:
                anki = self.controller.anki
                baseline = anki.capture_note_snapshot(session.target_note_id, include_media=True)
                # The original shortcut updates asbplayer's last-added card.
                # Check that identity rather than mining into an unrelated note.
                recent = anki._invoke("findNotes", {"query": "added:1"}) or []
                if not recent or max(map(int, recent)) != session.target_note_id:
                    raise ValueError("asbplayer shortcut updates the last-added Anki card. Another card was added since this target; add the intended card again first.")
                result = dict(backend="asbplayer", baseline=baseline)
                self._results.put(("asb-ready", session.id, result))
            except Exception as exc:
                self._results.put(("asb-result", session.id, str(exc)))
        threading.Thread(target=prepare, name="asbplayer-target", daemon=True).start()

    def _dispatch_asb_capture(self, session, result):
        def send():
            if session.cancelled or self._capture is not session:
                return
            try:
                self._pending_media[session.id] = (session, result)
                self._save_pending_media(session, result)
                self._begin_capture_display(session)
                chord = self._asb.dispatch(getattr(self.controller, "hotkey_controller", None))
                session.phase = "capture_sent"
                session.asb_dispatched = True
                session.dispatched_monotonic = time.monotonic()
                logger.info("Original asbplayer hotkey %s sent for note %s", chord, session.target_note_id)
            except Exception as exc:
                self._results.put(("asb-result", session.id, str(exc)))
                return
            def poll():
                error = None
                try:
                    deadline = time.monotonic() + 90
                    while not session.cancelled:
                        if self.controller.anki.verify_asbplayer_capture(session.target_note_id, result["baseline"]):
                            break
                        if time.monotonic() >= deadline:
                            raise ValueError("asbplayer media did not arrive. Check its original capture shortcut, audio recording and Anki field settings.")
                        time.sleep(self._asb.poll_seconds())
                    if session.cancelled:
                        raise RuntimeError("Capture cancelled")
                except Exception as exc:
                    error = str(exc)
                self._results.put(("asb-result", session.id, error))
            threading.Thread(target=poll, name="asbplayer-media", daemon=True).start()
            self._set_status("asbplayer recording · waiting for audio and image")
        self.controller.settings.root.after(self._asb.delay_ms(), send)

    def _freeze_playback_policy(self, session):
        key = "POST_CAPTURE_ONLY_PLAYBACK" if session.capture_only else "POST_CAPTURE_PLAYBACK"
        session.post_capture_policy = self.controller.config.get(key) or "play"
        lease = session.pause_lease
        session.previous_paused = not lease.was_playing if lease is not None else bool(self.snapshot.paused if self.snapshot else not getattr(self.controller, "playing", False))
        session.playback_generation = getattr(self, "_control_generation", 0)

    def _finish_asb_playback(self, session):
        if (session.cancelled or not self.fresh or self.identity.wire() != session.media_identity
                or self.snapshot.fingerprint != session.media_fingerprint
                or getattr(self, "_control_generation", 0) != session.playback_generation):
            return
        paused = session.post_capture_policy == "paused" or (session.post_capture_policy == "restore" and session.previous_paused)
        if self.snapshot.paused != paused:
            self.command("pause" if paused else "play")

    def freeze_capture(self, session):
        if session.target.source_hash and session.target.source_hash != getattr(self, "_source_hash", None):
            raise ValueError("Capture target belongs to a different subtitle source; add a card for this video first")
        if session.target.media_key and session.target.media_key != self._preference_media():
            raise ValueError("Capture target belongs to a different video; add a card for this video first")
        if not self.native:
            self._freeze_asb_capture(session)
            return
        if self._capture:
            raise ValueError("A recording is already in progress")
        if not self._connected or not self.identity or not self.snapshot:
            raise ValueError("Capture needs a connected browser video")
        if not self.fresh:
            self.bridge.send(dict(type="PlaybackProbe", identity=self.identity.wire()))
            raise ValueError("Video clock is being refreshed; try Capture again")
        mapping = self.mapping
        if mapping is None:
            raise ValueError("Timing is not confirmed; set and confirm Offset manually or retry automatic alignment")
        if not self._native_enabled:
            self.bridge.send(dict(type="RecorderProbe", identity=self.identity.wire()))
            raise ValueError("Native recording is not ready for this tab; open the extension once to check its audio connection")
        cue = session.target.cue_snapshot
        if cue is None:
            raise ValueError("Capture needs a frozen subtitle cue")
        cfg = self.controller.config
        context = session.target.sentence_context
        first = context.first_cue if context is not None else int(cue.cue_index)
        last = context.last_cue if context is not None else first
        start, end = mapping.capture_sentence_interval(first, last,
            float(cfg.get("CAPTURE_PADDING_BEFORE_MS") or 0) / 1000,
            float(cfg.get("AUDIO_PADDING") or 0) / 1000)
        if self.snapshot.duration > 0:
            end = min(self.snapshot.duration, end)
        if end <= start:
            raise ValueError("The selected subtitle is outside the video; check Offset")
        session.backend = "native"
        self._freeze_playback_policy(session)
        session.timing_version = mapping.version
        session.media_identity = self.identity.wire()
        session.media_fingerprint = self.snapshot.fingerprint
        session.mapped_interval = (start, end)
        from model.screenshot_position import ScreenshotPosition
        position = cfg.get("CAPTURE_SCREENSHOT_POSITION")
        if position is None:
            position = cfg.get("CAPTURE_SCREENSHOT_DELAY_MS")
        position = ScreenshotPosition.parse("500 ms" if position is None else position)
        cue_start = mapping.video[first][0]
        cue_end = max(end for _, end in mapping.video[first:last+1])
        session.screenshot_position = position
        session.screenshot_time = position.video_time(cue_start, cue_end, start, end)
        session.mapped_cue_start = cue_start

    def capture(self, session, note_id=None):
        if session and session.capture_only and session.id not in self.controller.anki_controller.mining_groups.groups:
            self.controller.anki_controller.mining_groups.start(session)
        if session and session.backend == "asbplayer":
            self._capture_asb(session, note_id)
            return
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
        self._begin_capture_display(session)
        try:
            self.bridge.send(dict(type="CaptureRequest", operationId=session.id, identity=session.media_identity,
                                  fingerprint=session.media_fingerprint, timingVersion=session.timing_version,
                                  start=start, end=end, noteId=note_id, screenshotTime=session.screenshot_time if session.screenshot_time is not None else (start + end) / 2,
                                  policy=session.post_capture_policy, previousPaused=session.previous_paused))
        except ConnectionError:
            session.phase = "failed"
            self._capture = None
            self._clear_capture_display(session.id)
            raise
        self._set_status("Recording subtitle…")

    def bind_capture_note(self, session, note_id):
        """Join independently completed recording and note creation on Tk's thread."""
        note_id = int(note_id or 0)
        if note_id <= 0 or (session.target_note_id and session.target_note_id != note_id):
            raise ValueError("Capture target must be the exact newly created note")
        session.set_target_note(note_id)
        if session.backend == "asbplayer" and session.cancelled:
            self._delete_cancelled_asb_note(session)
        if session.backend == "asbplayer" and not session.cancelled and session.phase == "waiting_note":
            self._capture_asb(session, note_id)
            return
        if session.cancelled or session.phase == "failed":
            self._finish_native(session, False)
            return
        if not session.cancelled and session.native_result:
            self._complete_native_result(session)

    def capture_note_failed(self, session, error="Anki card creation failed"):
        if session.id not in self._operations:
            return
        if session.phase in ("encoding", "saving_recording"):
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

    def _delete_cancelled_asb_note(self, session):
        if session.capture_only or not session.target_note_id:
            return
        note_id = session.target_note_id
        deletions = getattr(self, "_cancelled_note_deletions", set())
        self._cancelled_note_deletions = deletions
        if note_id in deletions:
            return
        deletions.add(note_id)
        anki = self.controller.anki_controller
        last = anki._last_subtitleplayer_added_card
        if last and last.get("note_id") == note_id:
            anki._last_subtitleplayer_added_card = None
        if getattr(self.controller, "_latest_added_anki_note_id", None) == note_id:
            self.controller._latest_added_anki_note_id = None
        def delete_owned_note():
            try:
                self.controller.anki._invoke("deleteNotes", {"notes": [note_id]})
            except Exception as exc:
                self._results.put(("cancel-delete-error", session.id, f"Could not delete cancelled note {note_id}: {exc}"))
        anki.submit_anki_work(delete_owned_note)

    def abort(self, reason="Cancelled"):
        session = self._capture
        if session:
            self._clear_capture_display(session.id)
        if session:
            session.cancel(reason)
            self.controller.anki_controller.mining_groups.cancel(session)
            if session.backend == "asbplayer":
                self._delete_cancelled_asb_note(session)
            try:
                if session.backend == "native":
                    self._dispatch_player_command(MediaIdentity.read(session.media_identity), "abort", {}, track=False)
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

    def _notify_recording_ready(self, session):
        if session.recording_feedback_done or session.cancelled:
            return
        session.recording_feedback_done = True
        # This indicator belongs to the clip, not a later note/media completion.
        anki = self.controller.anki_controller
        getattr(anki, "_action_feedback_tokens", {}).pop(session.action_feedback_key, None)
        note_id = session.target_note_id
        pending = getattr(self.controller, "_voice_capture_feedback", {})
        if note_id and isinstance(pending, dict):
            pending.pop(note_id, None)
        renderer = getattr(self.controller, "renderer", None)
        same_media = (not session.media_identity or (self.identity and self.identity.wire() == session.media_identity
                      and self.snapshot and self.snapshot.fingerprint == session.media_fingerprint))
        if (same_media and renderer is not None and session.feedback_generation ==
                getattr(renderer, "_voice_action_indicator_generation", None)):
            renderer.set_voice_action_indicator("success")
        logger.info("Recording verified and saved operation=%s; Anki completion is separate", session.id)

    def _finish_native(self, session, ok):
        self._clear_capture_display(session.id)
        if not ok:
            self.controller.anki_controller.mining_groups.cancel(session)
        # Sessions remain in add history; media belongs only to pending storage
        # or Anki, otherwise each completed capture would retain its WAV/PNG.
        session.native_result = None
        if session.target_note_id and not session.media_completion_notified and not session.recording_feedback_done:
            self.controller.hotkey_controller.notify_capture_media_finished(session.target_note_id, ok)
            session.media_completion_notified = True
        if session.id in self._finished_operations:
            return
        self._finished_operations.add(session.id)
        if session.recording_feedback_done:
            self.controller.anki_controller._action_feedback_tokens.pop(id(session.target), None)
        if ok:
            self.controller.anki_controller.mining_groups.completed(session)
        if session.backend == "asbplayer" and not session.capture_only:
            anki = self.controller.anki_controller
            anki._finish_word_feedback(session.target, ok)
            if ok:
                anki._record_successful_anki_add()
                if session.deferred_annotation_entries:
                    anki._store_mined_annotation_entries(session.deferred_annotation_entries, session.target)
            session.deferred_annotation_entries = []
        if len(self._finished_operations) > 512:
            self._finished_operations.pop()
        popup = getattr(self.controller, "popup", None)
        mark = getattr(popup, "mark_anki_success" if ok else "mark_anki_failure", None)
        if callable(mark):
            mark()
        if not session.recording_feedback_done:
            self.controller.anki_controller._finish_action_feedback_key(session.action_feedback_key, ok)
        self.controller.release_anki_pause_lease(session.pause_lease, resume=False)

    def _save_pending_media(self, session, result):
        with _pending_media_write_lock:
            self._pending_directory.mkdir(parents=True, exist_ok=True)
            path = self._pending_directory / (session.id + ".json")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(dict(operationId=session.id, noteId=session.target_note_id, identity=session.media_identity, fingerprint=session.media_fingerprint, cancelled=session.cancelled, terminalError=session.terminal_error, backend=session.backend, replaceSentenceMedia=session.replace_sentence_media, media=result)), "utf-8")
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
                if result.get("sharedFields"):
                    self.controller.anki.attach_shared_capture(session.target_note_id, result["sharedFields"])
                elif session.backend == "asbplayer":
                    if not self.controller.anki.verify_asbplayer_capture(session.target_note_id, result["baseline"]):
                        raise ValueError("asbplayer fresh media not verified; recording will not be repeated automatically")
                else:
                    self.controller.anki.attach_native_capture(session.target_note_id, result.get("operationId", session.id),
                        result["audio"], result["image"], is_cancelled=lambda: session.cancelled)
                # A new recording must also reach cards mined before this capture,
                # including add-only cards that have no media yet.
                self.controller.anki.replace_exact_sentence_capture(
                    session.target_note_id, is_cancelled=lambda: session.cancelled)
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
            self.controller._browser_input_context = None
            self._sent_backend = None
            self._shortcut_config = None
        elif kind == "TrustedOrigin":
            origins = set(self.controller.config.get("BROWSER_TRUSTED_EXTENSION_ORIGINS") or ())
            origins.add(event["origin"])
            self.controller.config.set("BROWSER_TRUSTED_EXTENSION_ORIGINS", sorted(origins))
        elif kind == "Bound":
            self._connected = True
            identity = MediaIdentity.read(event["identity"])
            if identity != self.identity:
                self._progress_binding = None
                logger.info("Browser video connected (tab %s)", identity.tab_id)
                self._native_enabled = False
                self._last_recorder_probe = 0.0
                self._last_episode_context = None
                had_media_context = bool(getattr(self, "_media_context", None))
                self._media_context = None
                self.controller.sub_manager._browser_source_context = {}
                if had_media_context:
                    self.controller.episode_controller.update_media_presentation()
                self._preferred_reference_pending = False
                self._pending_caption_event = None
                self._reference_metadata = None
                self.invalidate(); self.snapshot = None; self._reference = (); self._reference_kind = "none"
                self._caption_last = None; self._observed_intervals = []
            self.identity = identity
            self._shortcut_config = None
            self.following = True
            if not self.mapping:
                self._set_status("Finding timing?")
            self._send_recorder_config()
        elif kind == "InputContext":
            if not self.identity or MediaIdentity.read(event["identity"]) != self.identity:
                return
            previous = getattr(self.controller, "_browser_input_context", None)
            if previous and int(event.get("sequence", 0)) <= previous["sequence"]:
                return
            context = dict(identity=self.identity.wire(), active=bool(event.get("active")),
                           focused=bool(event.get("windowFocused")) and bool(event.get("pageFocused")), text=bool(event.get("textInput")),
                           sequence=int(event.get("sequence", 0)), received=time.monotonic())
            from utils import get_foreground_root_hwnd
            foreground = get_foreground_root_hwnd()
            hotkeys = getattr(self.controller, "hotkey_controller", None)
            context["hwnd"] = foreground if context["focused"] and hotkeys and hotkeys._foreground_is_chrome(foreground) else None
            if previous is None or any(previous.get(key) != context[key] for key in ("identity", "active", "focused", "text", "hwnd")):
                self.controller._browser_focus_epoch = getattr(self.controller, "_browser_focus_epoch", 0) + 1
            self.controller._browser_input_context = context
            self.controller._browser_text_input_focused = context["text"]
        elif kind == "PlaybackIntervention":
            if (self.identity and MediaIdentity.read(event["identity"]) == self.identity
                    and (not event.get("fingerprint") or self.snapshot and event["fingerprint"] == self.snapshot.fingerprint)):
                self._clear_capture_display()
                self._control_generation = getattr(self, "_control_generation", 0) + 1
        elif kind == "Disconnected":
            if getattr(self, '_episode_navigation', None):
                self._episode_navigation = None
                self.report_error('Browser disconnected before episode change was confirmed; subtitles unchanged')
            self._clear_capture_display()
            self._connected = False
            self.controller._browser_input_context = None
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
                context = {key: event.get(key) for key in ("title", "kind", "episode", "season", "pageKey", "pageUrl", "scope")}
                if context != getattr(self, "_media_context", None):
                    previous = self._media_context or {}
                    if any(context.get(key) != previous.get(key) for key in ("pageKey", "pageUrl", "season", "episode")):
                        self._progress_binding = None
                    self._media_context = context
                    self.controller.sub_manager._browser_source_context = dict(context)
                    if self.controller.sub_manager.startup_needs_source:
                        self.controller.episode_controller.update_media_presentation()
                    if context.get("pageKey"):
                        key = digest(context["pageKey"].encode())
                        if context["pageKey"] != previous.get("pageKey"):
                            self._restore_browser_source(key)
                        self.remember_browser_source()
                        if self._source_key is not None and getattr(self, "preferences", None):
                            expected = (self._preference_media(), self._source_hash, self.preferences.mode(self._preference_media()))
                            if expected != self._preference_context:
                                self._load_trim()
                                self.invalidate(cancel_capture=False)
                                self._restore_timing_cache()
                        pending = getattr(self, "_last_episode_context", None)
                        if (pending and MediaIdentity.read(pending["identity"]) == self.identity
                                and (not context.get("pageUrl") or pending.get("pageUrl") == context["pageUrl"])
                                and (not context.get("episode") or context["episode"] == pending.get("episode"))):
                            self._follow_episode(pending)
                        elif context.get("episode"):
                            self._follow_episode(dict(context, seriesKey=context["pageKey"],
                                                      scope=context.get("scope") or ("season" if context.get("season") else "global")))
                    self.controller.episode_controller.update_media_presentation()
        elif kind == "EpisodeContext":
            if self.identity and MediaIdentity.read(event["identity"]) == self.identity:
                media_url = (self._media_context or {}).get("pageUrl")
                if media_url and event.get("pageUrl") and media_url != event["pageUrl"]:
                    return
                self._last_episode_context = event
                self._follow_episode(event)
        elif kind == "RecorderState":
            if self.identity and MediaIdentity.read(event["identity"]) == self.identity:
                previous = self._native_enabled
                self._native_enabled = bool(event.get("enabled"))
                self._recorder_problem = str(event.get("reason") or "")
                if previous != self._native_enabled:
                    logger.info("Native tab audio %s for tab %s", "ready" if self._native_enabled else "unavailable", self.identity.tab_id)
        elif kind == "Unbound":
            self.abort("Disconnected by user")
            self.following = False
            if getattr(self, "_media_context", None):
                self._media_context = None
                self.controller.sub_manager._browser_source_context = {}
                self.controller.episode_controller.update_media_presentation()
            self._connected = False
            self.snapshot = None
            self.controller.playing = False
            self._set_status("Legacy controls · Chrome disconnected")
        elif kind == "PlaybackSnapshot":
            snapshot = PlaybackSnapshot.read(event, now=event.get("_received_monotonic"))
            if snapshot.identity != self.identity:
                return
            if self.snapshot and snapshot.sequence <= self.snapshot.sequence:
                return
            self._observe_capture_display(snapshot, event.get("event", "tick"))
            session = self._capture
            if session and session.backend == "native" and event.get("event") in ("play", "pause", "waiting", "seeking", "seeked"):
                logger.info("Native capture %s player %s at %.3f", session.id, event["event"], snapshot.current_time)
            if (session and session.backend == "asbplayer" and session.phase == "waiting_note" and self.snapshot
                    and (snapshot.seeking or snapshot.paused != self.snapshot.paused)):
                self.abort("Playback changed while waiting for the asbplayer target note")
            if self.snapshot and self.snapshot.fingerprint != snapshot.fingerprint:
                self._preferred_reference_pending = False
                self._reference_metadata = None
                self.invalidate(); self._reference = (); self._reference_kind = "none"
                self._caption_last = None; self._observed_intervals = []
            self.snapshot = snapshot
            automation = getattr(self, 'episode_automation', None)
            if automation:
                automation.tick(event.get('event'))
            navigation = getattr(self, '_episode_navigation', None)
            if navigation and navigation.get('context'):
                self._follow_episode(navigation['context'])
            if snapshot.seeking:
                self._caption_last = None
            if self._reference_fingerprint and self._reference_fingerprint != snapshot.fingerprint:
                self._reference = ()
                self._reference_kind = "none"
                self._reference_fingerprint = None
            self._source()
            pending = self._pending_caption_event
            if pending and pending[0] + 15 < time.monotonic():
                self._pending_caption_event = None
            elif pending and pending[1].get("fingerprint") == snapshot.fingerprint:
                self._pending_caption_event = None
                self._event(pending[1])
            if self._base_mapping is None and getattr(self, "preferences", None):
                self._restore_timing_cache()
            if self._reference and self._base_mapping is None:
                self._align()
            self.anilist.observe(snapshot, event.get("event", "tick"))
        elif kind == "CaptionReferencePending":
            if (MediaIdentity.read(event["identity"]) != self.identity or
                    self.snapshot and event.get("fingerprint") != self.snapshot.fingerprint):
                return
            if not getattr(self, "_preferred_reference_pending", False):
                self._preferred_reference_pending = True
                self._reference = ()
                self._reference_kind = "none"
                if self.alignment_mode == "captions":
                    self.invalidate(cancel_capture=False)
                    self._set_status("Waiting for English caption timing; English 2 excluded")
        elif kind in ("CaptionText", "CaptionIntervals"):
            if MediaIdentity.read(event["identity"]) != self.identity:
                return
            if event.get("fingerprint") and (not self.snapshot or event["fingerprint"] != self.snapshot.fingerprint):
                self._pending_caption_event = (time.monotonic(), dict(event))
                return
            intervals = parse_intervals(event["text"]) if kind == "CaptionText" else tuple(map(tuple, event["intervals"]))
            import math
            if (len(intervals) < 8 or len(intervals) > 10000 or
                    any(len(x) != 2 or not all(isinstance(t, (int, float)) and math.isfinite(t) for t in x)
                        or not 0 <= x[0] < x[1] < 86400 for x in intervals)):
                return
            from model.subtitle_alignment import normalized_reference
            intervals = normalized_reference(intervals)
            self._preferred_reference_pending = False
            self._reference_metadata = (event.get("language") or "?", event.get("label") or "Website")
            if intervals != self._reference:
                logger.info("Browser caption timing reference: %s intervals (%s), language=%s label=%s priority=%s",
                            len(intervals), kind, event.get("language"), event.get("label"), event.get("priority"))
                self._reference_kind = "full"
                self._reference_fingerprint = event.get("fingerprint")
                self._reference = intervals
                cache = getattr(self, "cache", None)
                if cache and self.snapshot:
                    self._cache_worker.submit(self._cache_reference, self.snapshot.fingerprint,
                                              intervals, event.get("language", ""))
                self._align()
        elif kind == "CaptionUnavailable":
            logger.warning("Browser caption resource: %s", event.get("error", "Unavailable"))
            if not self._reference:
                self._set_status(event["error"])
        elif kind == "CaptionReset":
            if (MediaIdentity.read(event["identity"]) == self.identity and self.snapshot and
                    event.get("fingerprint") == self.snapshot.fingerprint):
                self._caption_last = None
        elif kind == "CaptionObservation":
            if getattr(self, "_preferred_reference_pending", False):
                return
            if self._reference_kind == "full" or MediaIdentity.read(event["identity"]) != self.identity:
                return
            if event.get("fingerprint") and self.snapshot and event["fingerprint"] != self.snapshot.fingerprint:
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
            if getattr(self, "alignment_mode", "audio") != "audio":
                return
            if not self.snapshot or event.get("fingerprint") != self.snapshot.fingerprint or MediaIdentity.read(event["identity"]) != self.identity:
                return
            if self._audio_busy:
                return
            from model.audio_alignment import AudioAnchorAligner
            if self._audio_aligner is None:
                source_hash, source = self._source()
                base = Path(self.controller.config.path).resolve().parent
                model = self.controller.config.get("AUDIO_ALIGNMENT_MODEL_PATH") or str(base / "voice_models/whisper-base")
                self._audio_aligner = AudioAnchorAligner(model, source,
                    [x[0] for x in self.controller.sub_manager.display_data], source_hash, self.snapshot.fingerprint,
                    scope=self._alignment_scope("audio"))
            worker = self._audio_aligner
            generation = self._generation
            self._audio_busy = True
            def analyze():
                try:
                    mapping = worker.transcribe(base64.b64decode(event["audio"], validate=True), event["start"], event["rate"])
                    if worker.reference_changed:
                        self._results.put(("audio-reference-changed", generation, worker.language))
                    self._results.put(("audio-alignment", generation, mapping))
                except Exception as exc:
                    if worker.reference_changed:
                        self._results.put(("audio-reference-changed", generation, worker.language))
                    logger.info("Local audio timing incomplete: %s", exc)
                    self._results.put(("audio-error", generation, str(exc)))
                finally:
                    self._audio_busy = False
            threading.Thread(target=analyze, name="local-audio-alignment", daemon=True).start()
        elif kind == 'SkipIntroAck':
            automation = getattr(self, 'episode_automation', None)
            if automation:
                automation.ack(event)
        elif kind == 'EpisodeNavigationAck':
            navigation = getattr(self, '_episode_navigation', None)
            if navigation and event.get('operationId') == navigation['operation'] and not event.get('ok'):
                self._episode_navigation = None
                self.report_error('Episode navigation failed: ' + str(event.get('error') or event.get('status') or 'Unknown error'))
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
        elif kind == "CaptureStarted":
            session = self._operations.get(event.get("operationId"))
            if session and self._capture is session:
                session.recording_started_at = time.monotonic()
                hold = getattr(self, "_capture_display", None)
                if hold and hold["session"] is session:
                    hold["replay_seen"] = True
                    if float(event.get("time", 0)) >= session.mapped_cue_start:
                        self._clear_capture_display(session.id)
                logger.info("Native capture %s audio running at %.3f", session.id, float(event.get("time", 0)))
        elif kind == "RecordingEnded":
            self._clear_capture_display(event.get("operationId"))
            session = self._operations.get(event.get("operationId"))
            if session and self._capture is session:
                logger.info("Native capture %s audio ended at %.3f, elapsed %.3fs", session.id,
                            float(event.get("time", 0)), time.monotonic()-getattr(session, "recording_started_at", time.monotonic()))
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
            session.phase = "saving_recording"
            self._pending_media[session.id] = (session, event)
            def save_recording(operation=session, result=event):
                try:
                    from model.anki_client import AnkiClient
                    AnkiClient.validate_native_capture(result.get("audio", ""), result.get("image", ""))
                    self._save_pending_media(operation, result)
                    self._results.put(("recording-ready", operation.id, None))
                except Exception as exc:
                    self._results.put(("recording-ready", operation.id, str(exc)))
            threading.Thread(target=save_recording, name="save-native-recording", daemon=True).start()
        elif kind == "CaptureFinished":
            pass  # Media is verified locally; this acknowledgement cannot control playback.

    def _poll(self):
        if self._closed:
            return
        if self._connected:
            try:
                self._send_recorder_config()
                now = time.monotonic()
                if self.identity and now-getattr(self, "_last_recorder_probe", 0) >= 5:
                    self._last_recorder_probe = now
                    self.bridge.send(dict(type="RecorderProbe", identity=self.identity.wire()))
            except ConnectionError:
                pass
        try:
            deadline = time.monotonic() + .008
            for _ in range(80):
                if time.monotonic() >= deadline:
                    break
                self._event(self.bridge.events.get_nowait())
        except queue.Empty:
            pass
        except Exception as exc:
            self._set_status("Browser connection: " + str(exc))
        pending_overlay = getattr(self, "_source_search_overlay", None)
        if pending_overlay:
            request = pending_overlay[0]
            if (request != self._source_selection_request or request[0] != self.identity
                    or not self.following or request[6] != (self._media_context or {}).get("pageUrl")):
                self._close_source_search_overlay()
        deferred_source = getattr(self, "_deferred_source_selection", None)
        if (deferred_source and not getattr(self, "_manual_source_open", False)
                and not getattr(self, "_jimaku_fallback_open", False)
                and not getattr(self, "_series_confirmation_open", False)):
            self._deferred_source_selection = None
            key, event, episode, season, refresh = deferred_source
            if key == (self.identity, (self._media_context or {}).get("pageUrl")):
                self._queue_browser_selection(event, episode, season, refresh=refresh)
        # Read the latest tab/media identity before committing completed work.
        self._apply_browser_selection()
        self.jimaku_navigation.pump()
        self.controller.anki_controller.mining_groups.pump()
        self.anilist.poll()
        manager = self.controller.sub_manager
        if getattr(manager, "_jimaku_map_changed", False) and not self._capture:
            manager._jimaku_map_changed = False
            self._progress_binding = None
            self.controller.episode_controller.update_media_presentation()
            event = self._last_episode_context
            context = self._media_context or {}
            if context.get("episode") and (not event or event.get("pageUrl") != context.get("pageUrl")):
                event = dict(context, seriesKey=context.get("pageKey"), scope=context.get("scope") or "global")
            if self.following and self.identity and event and (not (self._media_context or {}).get("pageUrl") or
                    event.get("pageUrl") == self._media_context["pageUrl"]):
                self._source_selection_request = None
                self._queue_browser_selection(event, int(event["episode"]), event.get("season"))
        if (getattr(self, "_refresh_generation", None) is not None
                and time.monotonic() > getattr(self, "_refresh_deadline", float("inf"))):
            self._refresh_generation = None
            self._set_status("Re-alignment timed out; previous timing and manual correction retained")
        self._start_pending_alignment()
        try:
            deadline = time.monotonic() + .008
            for _ in range(50):
                if time.monotonic() >= deadline:
                    break
                kind, identity, value = self._results.get_nowait()
                if kind == "timing-cache":
                    if (identity == self._timing_cache_attempt and identity[0] == self._generation
                            and value is not None and self._base_mapping is None
                            and getattr(self, "_refresh_generation", None) is None):
                        self.mapping = value
                        self._alignment_problem = None
                        self._set_status("Gespeicherter Untertitel-Abgleich aktiv")
                elif kind == "source-restore":
                    if self._capture:
                        self._results.put((kind, identity, value))
                        break
                    self._apply_source_restore(identity, value)
                elif kind.startswith("alignment"):
                    generation, reference_hash = identity
                    if generation != self._generation:
                        continue
                    self._analyzing = False
                    newer_reference = reference_hash != digest((self._alignment_scope(self._reference_kind) + json.dumps(tuple(self._reference))).encode())
                    if newer_reference:
                        old_kind, old_reference = getattr(self, "_analyzing_reference", (None, ()))
                        if old_kind != self._reference_kind or not set(old_reference).issubset(self._reference):
                            self._align()
                            continue
                    if kind == "alignment":
                        self.mapping = value
                        if self._complete_manual_alignment():
                            self._alignment_problem = None
                        self._cache_mapping(value, self._reference_kind)
                        logger.info("Subtitle timing aligned: %s, %s/%s confident cues, offsets %.3f..%.3f",
                                    value.method, sum(value.confidence), len(value.source),
                                    min(v[0]-s[0] for s,v in zip(value.source,value.video)),
                                    max(v[0]-s[0] for s,v in zip(value.source,value.video)))
                        self.bridge.send(dict(type="TimingMap", version=value.version, video=value.video))
                    else:
                        self._refresh_generation = None
                        self._report_alignment_problem(value)
                    if newer_reference:
                        self._align()
                elif kind == "audio-reference-changed" and identity == self._generation:
                    self.mapping = None
                    self._offset_region = self._auto_offset = None
                    self.preferences.data.get("maps", {}).pop(self._alignment_scope("audio"), None)
                    self.preferences.save()
                    self.abort("Audio language changed; revalidating timing")
                    self._set_status("Audio language changed to " + str(value) + "; finding new timing")
                elif kind == "audio-alignment" and identity == self._generation:
                    if self.alignment_mode == "audio":
                        self.mapping = value
                        if self._complete_manual_alignment():
                            self._alignment_problem = None
                        logger.info("Audio timing aligned: %s, %s/%s confident cues, offsets %.3f..%.3f",
                                    value.method, sum(value.confidence), len(value.source),
                                    min(v[0]-s[0] for s,v in zip(value.source,value.video)),
                                    max(v[0]-s[0] for s,v in zip(value.source,value.video)))
                        self._cache_mapping(value, "audio")
                        self.bridge.send(dict(type="TimingMap", version=value.version, video=value.video))
                elif kind == "audio-error" and identity == self._generation:
                    self._refresh_generation = None
                    self._report_alignment_problem(value, log_warning=False)
                elif kind == "waiting-anki":
                    pending = self._pending_media.get(identity)
                    if pending and not pending[0].cancelled:
                        pending[0].phase = "waiting_anki"
                        self._retry_at[identity] = time.monotonic() + 5
                        self._set_status("Waiting for Anki - saved recordings will attach automatically")
                elif kind == "recording-ready":
                    session = self._operations.get(identity)
                    if session is None or session.cancelled or session.phase != "saving_recording":
                        continue
                    if value:
                        self._terminal_media_failure(session, "Recording could not be secured: " + value)
                        continue
                    session.phase = "waiting_note"
                    self._notify_recording_ready(session)
                    self.controller.anki_controller.mining_groups.ready(session, session.native_result)
                    if session.creation_error:
                        self._terminal_media_failure(session, session.creation_error)
                    elif session.target_note_id:
                        self._complete_native_result(session)
                    else:
                        self._set_status("Recording saved; waiting for the Anki card")
                elif kind == "asb-ready":
                    session = self._operations.get(identity)
                    if session and self._capture is session and not session.cancelled and session.phase == "preparing":
                        self._dispatch_asb_capture(session, value)
                elif kind == "asb-result":
                    session = self._operations.get(identity)
                    if session is None:
                        continue
                    if self._capture is session:
                        self._capture = None
                    if not value and not session.cancelled:
                        self._notify_recording_ready(session)
                        self._finish_asb_playback(session)
                        def share_asb(operation=session):
                            try:
                                fields = self.controller.anki.capture_note_snapshot(operation.target_note_id)["fields"]
                                self._results.put(("group-ready", operation.id, {"sharedFields": fields}))
                            except Exception as exc:
                                self._results.put(("group-error", operation.id, str(exc)))
                        self.controller.anki_controller.submit_anki_work(share_asb)
                    if identity not in self._pending_media:
                        self._terminal_media_failure(session, value or "asbplayer did not return media")
                    else:
                        self._results.put(("media", identity, value))
                elif kind == "group-ready":
                    session = self._operations.get(identity)
                    if session is None:
                        group = self.controller.anki_controller.mining_groups.groups.get(identity)
                        session = group["session"] if group else None
                    if session:
                        self.controller.anki_controller.mining_groups.ready(session, value)
                elif kind == "group-error":
                    group = self.controller.anki_controller.mining_groups.groups.get(identity)
                    if group:
                        self.controller.anki_controller.mining_groups.cancel(group["session"])
                    self.report_error("Could not share fresh recording: " + value)
                elif kind == "cancel-delete-error":
                    self.report_error(value)
                elif kind == "media":
                    pending = self._pending_media.get(identity)
                    if pending is None:
                        continue
                    session, result = pending
                    if value or session.cancelled:
                        session.phase = "cancelled" if session.cancelled else "failed"
                        attempts = self._retry_count.get(identity, 0)
                        permanent = any(text in str(value).lower() for text in (
                            "missing media field", "missing required media fields", "not configured", "invalid note", "note not found", "no longer exists", "asbplayer"))
                        if not session.cancelled and not permanent and attempts < 4:
                            self._retry_count[identity] = attempts + 1
                            self._retry_at[identity] = time.monotonic() + (1, 3, 10, 30)[attempts]
                            logger.info("Media attachment pending operation=%s note=%s attempt=%s reason=%s", identity, session.target_note_id, attempts + 1, value)
                            self._set_status("Media saved - retrying Anki attachment automatically")
                        elif session.cancelled:
                            self._save_pending_media(session, result)
                            self._finish_native(session, False)
                        else:
                            if permanent:
                                session.terminal_error = str(value)
                                self._save_pending_media(session, result)
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
            self._adopt_manual_offset()
            c = self.controller
            if self._capture and getattr(self._capture, "backend", "native") == "native" and time.monotonic() - self.snapshot.received > 5.0:
                self.abort("Video clock unavailable")
            c.current_time = self.snapshot.time_at()
            c.playing = self.fresh and not (self.snapshot.paused or self.snapshot.buffering or self.snapshot.seeking)
            c.playback._set_play_button_state()
            if not c.slider_dragging:
                c.settings.slider.config(to=self.snapshot.duration)
                c.playback._publish_current_time()
            if not self._capture and self.mapping and self.fresh and not self._pending_media:
                idx = self.mapping.cue_at(c.current_time)
                aligned = ("Manuelles Timing · Chrome" if self._manual_offset is not None else
                           "Aligned · enable native recording" if self.native and not self._native_enabled else "Aligned · Chrome")
                self._set_status(aligned if idx is None or self.mapping.confidence[idx] else "Alignment uncertain")
            elif not self.fresh:
                self._set_status("Clock observation delayed - clock frozen" if self._connected else "Chrome disconnected - clock frozen")
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
        self._update_timing_ui()
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
        bindings = []
        for action, binding in hotkeys._repeat_action_bindings() + hotkeys._single_fire_bindings():
            if action.startswith("capture_segments:"):
                bindings.extend(("digit" + binding, "numpad" + binding))
            else:
                bindings.append(binding)
        if not self.native:
            chord = frozenset(str(legacy_value(self.controller.config, "ASBPLAYER_CAPTURE_HOTKEY")).lower().split("+"))
            bindings = [binding for binding in bindings if frozenset(binding.lower().split("+")) != chord]
        config = tuple(bindings) if not hotkeys._hotkeys_disabled() else ()
        if config != self._shortcut_config:
            self.bridge.send(dict(type="ShortcutConfig", identity=self.identity.wire(), bindings=config))
            self._shortcut_config = config
        backend = "native"
        if backend != self._sent_backend:
            self.bridge.send(dict(type="RecorderConfig", backend=backend))
            self._sent_backend = backend

    def refresh_browser_source(self):
        return self.resolve_browser_source(refresh=True)

    def resolve_browser_source(self, *, refresh=False):
        """Resolve known browser media; only an explicit refresh rebuilds its map."""
        event = self._last_episode_context
        if not self.identity or not self.following:
            return False
        if not event and (self._media_context or {}).get("episode"):
            event = dict(self._media_context, seriesKey=self._media_context["pageKey"],
                         scope="season" if self._media_context.get("season") else "global")
        if not event:
            return False
        if not refresh and self._source_selection_request is not None:
            return True
        if getattr(self, "_refreshing_source_request", None) == self._source_selection_request:
            if self._source_selection_request is not None:
                return True
        self._source_selection_request = None
        self._queue_browser_selection(event, int(event["episode"]), event.get("season"), refresh=refresh)
        if refresh:
            self._set_status("Refreshing saved subtitle map in background")
        return True

    def begin_manual_source_selection(self):
        """User owns selection until completion; invalidate every earlier load."""
        self._manual_source_open = True
        self._manual_source_context = (self.identity, (self._media_context or {}).get("pageUrl"))
        self._source_selection_request = None
        self._refreshing_source_request = None
        self._deferred_source_selection = None
        self._restore_serial += 1
        self.jimaku_navigation.token += 1
        self._close_source_search_overlay()
        self.controller.sub_manager._startup_source_choice_handled = True

    def end_manual_source_selection(self):
        self._manual_source_open = False
        # Keep this context claimed even after Cancel. Repeated announcements
        # must not reopen a dialog the user just closed. A new page may resolve.

    def _close_source_search_overlay(self, request=None):
        pending = getattr(self, "_source_search_overlay", None)
        if pending is None or request is not None and pending[0] != request:
            return
        self._source_search_overlay = None
        pending[1].close()

    def _show_source_search_overlay(self, request, title):
        self._close_source_search_overlay()
        from view.overlays import LoadingOverlay
        root = self.controller.settings.root
        overlay = LoadingOverlay(root,
            text="Searching Jimaku...\n"+(title if len(title) <= 65 else title[:62]+"…"),
            modal=False, topmost=True)
        self._source_search_overlay = (request, overlay)

    def _queue_browser_selection(self, event, episode, season, *, refresh=False):
        """Discover/download in the background; commit only for the still-bound video."""
        context_key = (self.identity, (self._media_context or {}).get("pageUrl"))
        if getattr(self, "_manual_source_open", False):
            return
        if getattr(self, "_jimaku_fallback_open", False) or getattr(self, "_series_confirmation_open", False):
            self._deferred_source_selection = (context_key, dict(event), episode, season, refresh)
            return
        if not refresh and context_key == getattr(self, "_manual_source_context", None):
            return
        if refresh:
            self._manual_source_context = None
        if self._capture:
            return  # Playback/cue ownership stays with the active recording.
        media_context = dict(self._media_context or {})
        self.controller.sub_manager._browser_source_context = dict(media_context,
            seriesKey=event.get("seriesKey"), episode=episode, season=season, scope=event.get("scope"))
        title = browser_search_title(event.get("title") or media_context.get("title"),
                                     event.get("pageUrl") or media_context.get("pageUrl"))
        base_request = (self.identity, event.get("seriesKey"), title, season, episode)
        if (not refresh and self._source_selection_request is not None
                and base_request == self._source_selection_request[:5]
                and self._source_selection_request[6] == media_context.get("pageUrl")):
            return
        request = (*base_request, time.monotonic_ns() if refresh else None, media_context.get("pageUrl"))
        self._source_selection_request = request
        self._close_source_search_overlay()
        if refresh:
            self._refreshing_source_request = request
        manager = self.controller.sub_manager
        source = (manager.srt_file, manager.get_anime_name(), manager.current_episode, manager.current_season)
        scope = event.get("scope")
        remembered = dict(self._source_bindings.entries.get(digest(str(media_context.get("pageKey", "")).encode()), {}))
        current_jimaku = dict(manager.source_descriptor or {}) if manager.source_kind == "jimaku" else None
        # Show the operation immediately for a new, unbound title. Existing
        # episode maps continue loading quietly in the background.
        known = manager.jimaku.client.normalized(manager.get_anime_name()) == manager.jimaku.client.normalized(title)
        if not known and not remembered.get("manual_provider"):
            self._show_source_search_overlay(request, title)
        def load():
            if self._closed or request != self._source_selection_request:
                return
            record = None
            progress_candidate = None
            try:
                if remembered.get("manual_provider") and remembered.get("source_kind") == "github":
                    query = remembered.get("remote_search_query") or remembered.get("anime_folder_name")
                    record = next((row for row in manager._list_cached_github_search_records()
                                   if str(row.get("query", "")).casefold() == str(query).casefold()), None)
                    if not record:
                        raise ValueError("GitHub-Backup-Map fehlt. Bitte Backup ausdrücklich öffnen.")
                    state = prepare_selection(manager, record, episode, season, scope,
                                              refresh=False, allow_refresh=False)
                    state.update(source_kind="github", source_descriptor=None)
                elif remembered.get("manual_provider") and remembered.get("source_kind") == "local":
                    state = None
                else:
                    descriptor = manager.jimaku.find_series(title,
                        (event.get("seriesKey"), digest(str(media_context.get("pageKey", "")).encode())),
                        anime=manager.source_descriptor.get("anime", True) if manager.source_descriptor else True)
                    if manager.jimaku.series.get(descriptor):
                        target = manager.jimaku.series.resolve(descriptor, episode, season, scope)
                        progress_candidate = manager.jimaku.series.progress_binding(dict(descriptor, episode_key=target["key"]))
                    # Use the same absolute/season resolver as explicit episode
                    # navigation. Comparing local numbers via an old arithmetic
                    # mapping can incorrectly keep the previous episode loaded.
                    state = manager.jimaku.prepare(descriptor, episode, season, scope,
                        manager.extract_season_episode_global, refresh=refresh)
                    same_jimaku = current_jimaku and descriptor["entry_id"] == current_jimaku["entry_id"]
                    same_name = manager.jimaku.client.normalized(source[1]) == manager.jimaku.client.normalized(descriptor["title"])
                    if same_jimaku or same_name:
                        state["anime_folder_name"] = manager.anime_folder_name
                        state["display_anime_name"] = manager.display_anime_name
                        state["source_descriptor"] = dict(state["source_descriptor"], title=manager.display_anime_name or manager.anime_folder_name)
                        manager.jimaku.remember_path(state["srt_file"], state["source_descriptor"])
                if state is not None:
                    state["_prepared_key"], state["_prepared_payload"] = manager.prepare_subtitle_display(state["srt_file"])
                self._source_selection_results.put((request, source, event, state, None))
            except Exception as exc:
                logger.info("Automatic browser subtitle selection: %s", exc)
                fallback = ({"seriesProposal": exc.proposal} if isinstance(exc, SeriesConfirmationRequired) else
                            {"jimakuFallback": record.get("query") if record else title})
                if progress_candidate:
                    fallback["progressCandidate"] = progress_candidate
                self._source_selection_results.put((request, source, event, fallback, str(exc)))
        def start():
            if request == self._source_selection_request and self.identity == request[0] and not self._closed:
                self.submit_source_task("browser-search", load)
        self.controller.settings.root.after(350, start)

    def _open_jimaku_fallback(self, request, source, query):
        if getattr(self, "_manual_source_open", False) or self.controller.sub_manager.is_search_dialog_active():
            return
        if self._closed or getattr(self, "_jimaku_fallback_open", False) or self._capture:
            return
        manager = self.controller.sub_manager
        def valid():
            current = (manager.srt_file, manager.get_anime_name(), manager.current_episode, manager.current_season)
            return (not self._closed and self.following and self.identity == request[0]
                    and self._source_selection_request == request and current == source
                    and request[6] == (self._media_context or {}).get("pageUrl"))
        if not valid():
            return
        query = browser_search_title(query, (self._media_context or {}).get("pageUrl"))
        self._jimaku_fallback_open = True
        manager._set_modal_input_active(True)
        try:
            path = manager.choose_jimaku_source(self.controller.settings.root, query=query, valid=valid,
                expected={"episode": request[4], "season": request[3],
                          "scope": (self._last_episode_context or self._media_context or {}).get("scope", "season")})
            if path:
                manager._load_local_and_process(path)
                self._source_key = None
                self.invalidate()
                self.controller.episode_controller._after_episode_change()
                self.remember_browser_source(explicit=True)
                self._episode_problem = None
        except Exception:
            logger.exception("Jimaku fallback selection failed")
            self._episode_problem = "Jimaku subtitles could not be loaded; choose another source"
        finally:
            manager._set_modal_input_active(False)
            self._jimaku_fallback_open = False

    def _confirm_browser_series(self, request, source, event, proposal):
        manager = self.controller.sub_manager
        if getattr(self, "_manual_source_open", False) or manager.is_search_dialog_active():
            return
        def valid():
            return (not self._closed and self._source_selection_request == request and request[0] == self.identity
                    and request[6] == (self._media_context or {}).get("pageUrl")
                    and source == (manager.srt_file, manager.get_anime_name(), manager.current_episode, manager.current_season))
        if not valid() or getattr(self, "_series_confirmation_open", False) or self._capture:
            return
        self._series_confirmation_open = True
        manager._set_modal_input_active(True)
        try:
            first = proposal["parts"][0] if proposal["parts"] else None
            descriptor = {"entry_id": first["entry_id"] if first else 0, "title": proposal["title"], "anime": True}
            confirmed = manager.confirm_jimaku_series(self.controller.settings.root, descriptor, proposal=proposal, valid=valid)
            if confirmed and valid():
                # Persist the user's choice before retrying any download. A
                # network/file failure must not discard the confirmed map.
                if not confirmed.get("separate"):
                    parts = confirmed["parts"]
                    chosen = next((part for part in parts if part["entry_id"] == descriptor["entry_id"]), parts[0])
                    association = dict(descriptor, entry_id=chosen["entry_id"], series_id=confirmed["id"],
                                       title=confirmed["title"])
                    title = event.get("title") or (self._media_context or {}).get("title", "")
                    manager.jimaku.bind(event.get("seriesKey"), association, title)
                    page_key = (self._media_context or {}).get("pageKey")
                    if page_key:
                        manager.jimaku.bind(digest(page_key.encode()), association, title)
                self._source_selection_request = None
                self._queue_browser_selection(event, int(event["episode"]), event.get("season"))
        finally:
            manager._set_modal_input_active(False)
            self._series_confirmation_open = False

    def _apply_browser_selection(self):
        try:
            while True:
                request, source, event, state, error = self._source_selection_results.get_nowait()
                self._close_source_search_overlay(request)
                if request == getattr(self, "_refreshing_source_request", None):
                    self._refreshing_source_request = None
                manager = self.controller.sub_manager
                current = (manager.srt_file, manager.get_anime_name(), manager.current_episode, manager.current_season)
                if (request != self._source_selection_request or request[0] != self.identity
                        or not self.following or current != source
                        or request[6] != (self._media_context or {}).get("pageUrl")):
                    if request == self._source_selection_request and current != source:
                        self._source_selection_request = None
                    continue
                if error:
                    self._episode_problem = error
                    if state and state.get("progressCandidate"):
                        self._progress_binding = dict(state["progressCandidate"], identity=self.identity.wire(), page_url=request[6])
                    if state and state.get("seriesProposal"):
                        self.controller.settings.root.after_idle(
                            lambda request=request, source=source, event=event, proposal=state["seriesProposal"]:
                                self._confirm_browser_series(request, source, event, proposal))
                    elif state and state.get("jimakuFallback"):
                        self.controller.settings.root.after_idle(
                            lambda request=request, source=source, query=state["jimakuFallback"]:
                                self._open_jimaku_fallback(request, source, query))
                    continue
                if self._capture:
                    self._source_selection_results.put((request, source, event, state, error))
                    break
                if state is None:
                    self._episode_problem = "Lokale Quelle bleibt ausgewählt; andere Folge bitte manuell öffnen"
                    continue
                if state.get("_series_revision") is not None:
                    current_map = manager.jimaku.series.get(state["source_descriptor"])
                    if not current_map or state["_series_revision"] != current_map.get("revision", 0):
                        self._source_selection_request = None
                        self._queue_browser_selection(event, int(event["episode"]), event.get("season"))
                        continue
                if state.get("_prepared_key") != manager._prepared_episode_key(state["srt_file"]):
                    self._source_selection_request = None
                    self._queue_browser_selection(event, int(event["episode"]), event.get("season"))
                    continue
                try:
                    changed = self._source_bindings.restore_state(state, manager)
                    self._browser_episode = (event["seriesKey"], event.get("season"), int(event["episode"]))
                    if state.get("_series_episode"):
                        progress = manager.jimaku.series.progress_binding(state["source_descriptor"])
                        self._progress_binding = (dict(progress, identity=self.identity.wire(), page_url=request[6])
                                                  if progress else None)
                    self._episode_problem = None
                    if changed:
                        self._source_key = None
                        # These captions belong to the bound video, not to the
                        # previous SRT. They may arrive before its async download.
                        self.invalidate()
                        self.controller.episode_controller._after_episode_change()
                        if self.snapshot and self._reference:
                            self._restore_timing_cache()
                            self._align()
                    self.remember_browser_source(verified=True)
                    self.controller.episode_controller.update_media_presentation()
                except Exception:
                    logger.exception("Could not apply automatic subtitle selection")
                    self._episode_problem = "Could not load matching subtitles; select the source manually"
        except queue.Empty:
            pass

    def navigate_episode(self, direction, *, autoplay=False):
        """The website owns navigation while following; subtitles await confirmation."""
        if self._capture:
            self.report_error('Wait for recording to finish before changing episodes')
            return False
        if getattr(self, '_episode_navigation', None):
            self.report_error('An episode change is already pending')
            return False
        if not self.identity or not self.snapshot:
            self.report_error('Connect a browser video before changing episodes')
            return False
        if direction not in ('next', 'previous'):
            raise ValueError('Invalid episode direction')
        import uuid
        operation = uuid.uuid4().hex
        before = dict(self._last_episode_context or self._media_context or {})
        if not isinstance(before.get('episode'), int):
            self.report_error('Wait for the website episode information before changing episodes')
            return False
        self._episode_navigation = dict(operation=operation, direction=direction, before=before,
            identity=self.identity, fingerprint=self.snapshot.fingerprint,
            resource=getattr(self.snapshot, 'resource_fingerprint', ''),
            player=getattr(self.snapshot, 'player_fingerprint', ''), context=None, autoplay=bool(autoplay))
        try:
            self.bridge.send(dict(type='EpisodeNavigate', operationId=operation,
                                  identity=self.identity.wire(), direction=direction))
        except Exception as exc:
            self._episode_navigation = None
            self.report_error('Episode navigation failed: ' + str(exc))
            return False
        self.controller.settings.root.after(15000, lambda: self._episode_navigation_timeout(operation))
        self._set_status('Changing website episode…')
        return True

    def _episode_navigation_timeout(self, operation):
        pending = getattr(self, '_episode_navigation', None)
        if pending and pending['operation'] == operation:
            self._episode_navigation = None
            self.report_error('Episode change was not confirmed within 15 seconds; subtitles unchanged')

    def _episode_navigation_confirmed(self, event):
        pending = getattr(self, '_episode_navigation', None)
        if not pending:
            return True
        pending['context'] = event
        before = pending['before']
        old, new = before.get('episode'), event.get('episode')
        old_season, new_season = before.get('season'), event.get('season')
        correct_direction = (isinstance(old, int) and isinstance(new, int)
            and ((new > old) if pending['direction'] == 'next' else (new < old)))
        if isinstance(old_season, int) and isinstance(new_season, int) and isinstance(old, int) and isinstance(new, int):
            correct_direction = ((new_season, new) > (old_season, old) if pending['direction'] == 'next'
                                 else (new_season, new) < (old_season, old))
        identity_changed = self.identity != pending['identity']
        resource = getattr(self.snapshot, 'resource_fingerprint', '') if self.snapshot else ''
        media_changed = resource and pending['resource'] and resource != pending['resource']
        player = getattr(self.snapshot, 'player_fingerprint', '') if self.snapshot else ''
        media_changed = media_changed or player and pending['player'] and player != pending['player']
        if before.get('title') and event.get('title') and before['title'].strip().casefold() != event['title'].strip().casefold():
            return False
        if not correct_direction or not (identity_changed or media_changed):
            return False
        if not self.snapshot or self.snapshot.identity != self.identity:
            return False
        self._episode_navigation = None
        self._set_status('Website episode changed; loading matching subtitles')
        if pending.get('autoplay') and not self._capture:
            try:
                self.command('play')
            except ConnectionError:
                self.report_error('Episode changed, but playback could not be started')
        return True

    def _follow_episode(self, event, *, auto_select=True):
        """Use explicit site episode metadata within the already selected series."""
        if not self._episode_navigation_confirmed(event):
            return
        try:
            episode = int(event["episode"])
            season = int(event["season"]) if event.get("season") else None
            if not 0 < episode < 10000 or (season is not None and not 0 < season < 1000):
                return
        except (ValueError, TypeError, KeyError):
            return
        if auto_select:
            self._queue_browser_selection(event, episode, season)
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
        self._close_source_search_overlay()
        self._closed = True
        self.anilist.close()
        self._cache_worker.shutdown(wait=False, cancel_futures=True)
        self._source_worker.close()
        worker = getattr(self, "_alignment_worker", None)
        if worker:
            worker.close()
        self.abort("Application closed")
        if self._job:
            self.controller.settings.root.after_cancel(self._job)
        self.bridge.close()
