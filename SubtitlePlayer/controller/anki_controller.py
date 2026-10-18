"""Anki integration helper for note creation, wait dialogs, and success UI."""

import logging
import os
import shutil
import subprocess
import threading
import time
import tkinter as tk
from typing import Any
from model.capture_session import CaptureSession, FrozenActionTarget
from utils import (
    dispatch_to_tk,
    focus_window_by_title,
    get_foreground_root_hwnd,
    get_monitor_rects,
    make_nonactivating_tool_window,
    send_global_hotkey,
    show_window_no_activate,
)

logger = logging.getLogger(__name__)

class _ControllerProxy:
    """Proxy base that forwards attribute access and assignment to SubtitleController."""

    def __init__(self, controller: Any) -> None:
        object.__setattr__(self, "controller", controller)

    def __getattr__(self, name: str):
        return getattr(self.controller, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "controller":
            object.__setattr__(self, name, value)
        else:
            setattr(self.controller, name, value)

class AnkiController(_ControllerProxy):
    """Anki integration helper for note creation, wait dialogs, and success UI."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)
        # These are deliberately owned by the Anki controller rather than the
        # broad SubtitleController.  A capture operation is correlated by its
        # immutable sentence target, never by whichever Anki note is newest.
        self._capture_sessions: dict[str, CaptureSession] = {}
        self._capture_session_lock = threading.RLock()
        self._latest_capture_session_id = ""
        self._last_subtitleplayer_added_card: dict[str, Any] | None = None
        self._action_feedback_generation = 0
        self._action_feedback_tokens: dict[int, int] = {}

    def _freeze_action_target(
        self,
        *,
        selected_text: str,
        subtitle_text: str,
        cue_snapshot=None,
        selection_spans=None,
        initiator: str = "anki",
    ) -> FrozenActionTarget:
        subtitle = str(subtitle_text or "")
        if not subtitle and cue_snapshot is not None:
            subtitle = str(getattr(cue_snapshot, "plain_text", "") or "")
        sentence_key = ""
        try:
            sentence_key = self.anki.normalize_capture_sentence_key(subtitle)
        except Exception:
            sentence_key = " ".join(subtitle.split())
        return FrozenActionTarget.create(
            selected_text=selected_text,
            subtitle_text=subtitle,
            sentence_key=sentence_key,
            initiator=initiator,
            cue_snapshot=cue_snapshot,
            selection_spans=selection_spans,
        )

    def _new_capture_session(
        self,
        target: FrozenActionTarget,
        *,
        capture_only: bool,
        early_capture: bool,
        pause_lease=None,
        interaction_owner: str | None = None,
    ) -> CaptureSession:
        session = CaptureSession(
            target=target,
            capture_only=bool(capture_only),
            early_capture=bool(early_capture),
            pause_lease=pause_lease,
            interaction_owner=str(interaction_owner or target.initiator or "ui"),
            action_feedback_key=id(target),
        )
        browser = getattr(self.controller, "browser_sync", None)
        if browser is not None:
            browser.freeze_capture(session)
        if target.sentence_key:
            try:
                session.baseline_media = self.anki.snapshot_exact_sentence_media(target.subtitle_text)
            except Exception:
                logger.debug("Failed to snapshot exact-sentence media before capture", exc_info=True)
        with self._capture_session_lock:
            self._capture_sessions[session.id] = session
            self._latest_capture_session_id = session.id
        return session

    def _begin_action_feedback(self, target: FrozenActionTarget, *, force: bool = False) -> None:
        """Show progress for non-voice Anki work; voice owns its own generation."""
        if not force and str(target.initiator or "").strip().lower() == "voice":
            return
        self._action_feedback_generation += 1
        generation = self._action_feedback_generation
        self._action_feedback_tokens[id(target)] = generation
        setter = getattr(getattr(self.controller, "renderer", None), "set_voice_action_indicator", None)
        if callable(setter):
            setter("working")

    def _finish_action_feedback_key(self, key: int | None, succeeded: bool) -> None:
        if key is None:
            return
        generation = self._action_feedback_tokens.pop(int(key), None)
        if generation is None or generation != self._action_feedback_generation:
            return
        setter = getattr(getattr(self.controller, "renderer", None), "set_voice_action_indicator", None)
        if callable(setter):
            setter("success" if succeeded else "idle")

    def _finish_action_feedback(self, target: FrozenActionTarget | None, succeeded: bool) -> None:
        self._finish_action_feedback_key(id(target) if target is not None else None, succeeded)

    def cancel_capture_sessions(self, *, interaction_owner: str = "voice", reason: str = "aborted") -> int:
        """Stop app-controlled capture follow-up without retracting external requests."""
        owner = str(interaction_owner or "voice").strip().lower()
        cancelled: list[CaptureSession] = []
        with self._capture_session_lock:
            for session in self._capture_sessions.values():
                if session.cancelled or str(session.interaction_owner or "").strip().lower() != owner:
                    continue
                session.cancel(reason)
                cancelled.append(session)
        for session in cancelled:
            if session.backend == "native":
                browser = getattr(self.controller, "browser_sync", None)
                if browser is not None and browser._capture is session:
                    browser.abort(reason)
            self._finish_action_feedback_key(session.action_feedback_key, False)
            release = getattr(self.controller, "release_anki_pause_lease", None)
            if callable(release):
                try:
                    release(session.pause_lease, resume=False)
                except Exception:
                    logger.debug("Failed to release cancelled capture pause lease", exc_info=True)
        return len(cancelled)

    def _capture_session_is_latest(self, session: CaptureSession | None) -> bool:
        if session is None:
            return False
        with self._capture_session_lock:
            return bool(not session.cancelled and session.id == self._latest_capture_session_id)

    def _record_subtitleplayer_added_card(self, note_id: int, target: FrozenActionTarget) -> None:
        try:
            note_id = int(note_id)
        except Exception:
            return
        if note_id <= 0 or not target.sentence_key:
            return
        self._last_subtitleplayer_added_card = {"note_id": note_id, "target": target}

    def _acquire_pause_lease(self, target: FrozenActionTarget):
        acquire = getattr(self.controller, "acquire_anki_pause_lease", None)
        if callable(acquire):
            try:
                return acquire(target)
            except Exception:
                logger.debug("Failed to acquire Anki playback pause lease", exc_info=True)
        return None

    def _current_anki_anime_name(self) -> str:
            manager = getattr(self, "sub_manager", None)
            for getter_name in ("get_display_anime_name", "get_anime_name"):
                getter = getattr(manager, getter_name, None)
                if not callable(getter):
                    continue
                try:
                    value = str(getter() or "").strip()
                except Exception:
                    value = ""
                if value:
                    return value
            return str(
                self.config.get("LAST_DISPLAY_ANIME_NAME")
                or self.config.get("LAST_ANIME_NAME")
                or ""
            ).strip()

    def _resolve_anki_executable(self) -> tuple[str, str]:
            configured = os.path.expandvars(
                os.path.expanduser(str(self.config.get("ANKI_EXECUTABLE_PATH") or "").strip())
            )
            if configured:
                path = os.path.abspath(configured)
                if os.path.isfile(path):
                    return path, ""
                return "", f"Configured Anki executable was not found: {path}"

            candidates = [shutil.which("anki.exe"), shutil.which("anki")]
            local_app_data = os.environ.get("LOCALAPPDATA") or ""
            program_files = os.environ.get("ProgramFiles") or ""
            program_files_x86 = os.environ.get("ProgramFiles(x86)") or ""
            candidates.extend(
                os.path.join(base, *parts)
                for base, parts in (
                    (local_app_data, ("Programs", "Anki", "anki.exe")),
                    (program_files, ("Anki", "anki.exe")),
                    (program_files_x86, ("Anki", "anki.exe")),
                )
                if base
            )
            for candidate in candidates:
                if candidate and os.path.isfile(candidate):
                    return os.path.abspath(candidate), ""
            return "", "Anki executable was not found. Select it in Advanced Settings > Anki."

    @staticmethod
    def _anki_process_is_running(executable: str) -> bool:
            if os.name != "nt":
                return False
            process_name = os.path.basename(str(executable or "").strip())
            if not process_name:
                return False
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return process_name.casefold() in str(result.stdout or "").casefold()
            except Exception:
                logger.debug("Failed to inspect whether Anki is already running", exc_info=True)
                return False

    def _launch_anki_automatically(self) -> tuple[bool, str]:
            executable, error = self._resolve_anki_executable()
            if not executable:
                return False, error
            if self._anki_process_is_running(executable):
                return True, "Anki is already running; waiting for AnkiConnect..."

            now = time.monotonic()
            previous_request = float(getattr(self.controller, "_anki_auto_launch_requested_at", 0.0) or 0.0)
            if now - previous_request < 30.0:
                return True, "Anki startup was already requested; waiting for AnkiConnect..."
            try:
                subprocess.Popen(
                    [executable],
                    cwd=os.path.dirname(executable) or None,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
            except Exception as exc:
                logger.exception("Failed to start Anki executable %s", executable)
                return False, f"Could not start Anki: {exc}"
            self.controller._anki_auto_launch_requested_at = now
            logger.info("Started Anki automatically: %s", executable)
            return True, "Anki started; waiting for AnkiConnect..."

    def start_anki_with_app_if_enabled(self) -> None:
            if self.config.get("ANKI_ENABLED") is False:
                return
            if not bool(self.config.get("ANKI_START_WITH_APP") or False):
                return

            def worker() -> None:
                try:
                    if self.anki.ping():
                        return
                except Exception:
                    pass
                launched, message = self._launch_anki_automatically()
                if launched:
                    logger.info("Anki startup setting: %s", message)
                else:
                    logger.warning("Anki startup setting failed: %s", message)

            threading.Thread(target=worker, daemon=True, name="anki-startup").start()

    def _add_selection_to_anki(
        self,
        selected_text: str,
        subtitle_text: str = "",
        post_add_capture: bool = False,
        on_complete=None,
        cue_snapshot=None,
        selection_spans=None,
        initiator: str = "anki",
    ) -> None:
            selected = (selected_text or "").strip()
            if not selected:
                logger.debug("Add Selection To Anki skipped because no text is selected")
                if callable(on_complete):
                    on_complete(False, None)
                return

            action_target = self._freeze_action_target(
                selected_text=selected,
                subtitle_text=subtitle_text,
                cue_snapshot=cue_snapshot,
                selection_spans=selection_spans,
                initiator=initiator,
            )
            self._begin_action_feedback(action_target)
            subtitle = action_target.subtitle_text
            pause_lease = self._acquire_pause_lease(action_target)
            if not self.anki.ping():
                logger.info("AnkiConnect not reachable; showing wait dialog")
                self._show_anki_wait_dialog(
                    selected_text=selected,
                    subtitle_text=subtitle,
                    post_add_capture=bool(post_add_capture),
                    on_add_complete=on_complete,
                    cue_snapshot=cue_snapshot,
                    selection_spans=selection_spans,
                    action_target=action_target,
                    pause_lease=pause_lease,
                )
                return

            self._start_anki_add_worker(
                selected_text=selected,
                subtitle_text=subtitle,
                post_add_capture=bool(post_add_capture),
                on_complete=on_complete,
                cue_snapshot=cue_snapshot,
                selection_spans=selection_spans,
                action_target=action_target,
                pause_lease=pause_lease,
            )

    def _start_anki_add_worker(
        self,
        selected_text: str,
        subtitle_text: str = "",
        post_add_capture: bool = False,
        on_complete=None,
        cue_snapshot=None,
        selection_spans=None,
        action_target: FrozenActionTarget | None = None,
        pause_lease=None,
    ) -> None:
            selected = (selected_text or "").strip()
            if not selected:
                logger.debug("Add Selection To Anki skipped because no text is selected")
                return
            if bool(self.config.get("ANKI_PREVIEW_BEFORE_ADD") or False):
                self._start_anki_preview_worker(
                    selected_text=selected,
                    subtitle_text=subtitle_text,
                    post_add_capture=bool(post_add_capture),
                    on_complete=on_complete,
                    cue_snapshot=cue_snapshot,
                    selection_spans=selection_spans,
                    action_target=action_target,
                    pause_lease=pause_lease,
                )
                return
            self._start_anki_commit_worker(
                selected_text=selected,
                subtitle_text=subtitle_text,
                post_add_capture=bool(post_add_capture),
                on_complete=on_complete,
                cue_snapshot=cue_snapshot,
                selection_spans=selection_spans,
                action_target=action_target,
                pause_lease=pause_lease,
            )

    def _start_anki_preview_worker(
        self,
        selected_text: str,
        subtitle_text: str = "",
        post_add_capture: bool = False,
        on_complete=None,
        cue_snapshot=None,
        selection_spans=None,
        action_target: FrozenActionTarget | None = None,
        pause_lease=None,
    ) -> None:
            selected = (selected_text or "").strip()
            if not selected:
                return
            anime_name = self._current_anki_anime_name()
            self._set_busy_cursor(True)
            try:
                dispatch_to_tk(self.settings.root, self.popup.mark_anki_busy)
            except Exception:
                pass

            def worker():
                try:
                    if not self.anki.ping():
                        raise RuntimeError("AnkiConnect not reachable.")
                    prepared = self.anki.prepare_note_from_selection(
                        selection_text=selected,
                        subtitle_text=(action_target.subtitle_text if action_target is not None else subtitle_text),
                        anime_name=anime_name,
                        copy_existing_media=not bool(post_add_capture),
                        cue_snapshot=(action_target.cue_snapshot if action_target is not None else cue_snapshot),
                        selection_spans=(action_target.selection_spans if action_target is not None else selection_spans),
                    )
                    dispatch_to_tk(
                        self.settings.root,
                        self._show_anki_preview_window,
                        prepared,
                        selected,
                        subtitle_text,
                        post_add_capture=bool(post_add_capture),
                        on_complete=on_complete,
                        action_target=action_target,
                        pause_lease=pause_lease,
                    )
                except Exception as e:
                    logger.exception("Anki preview preparation failed: %s", e)
                    try:
                        dispatch_to_tk(self.settings.root, self.popup.mark_anki_failure)
                    except Exception:
                        pass
                    self._finish_action_feedback(action_target, False)
                    if callable(on_complete):
                        dispatch_to_tk(self.settings.root, on_complete, False, None)
                finally:
                    dispatch_to_tk(self.settings.root, self._set_busy_cursor, False)

            def start_worker() -> None:
                threading.Thread(target=worker, daemon=True).start()

            start_worker()

    def _start_anki_commit_worker(
        self,
        selected_text: str,
        subtitle_text: str = "",
        prepared: dict | None = None,
        post_add_capture: bool = False,
        on_complete=None,
        cue_snapshot=None,
        selection_spans=None,
        action_target: FrozenActionTarget | None = None,
        pause_lease=None,
    ) -> None:
            selected = (selected_text or "").strip()
            if not selected:
                logger.debug("Add Selection To Anki skipped because no text is selected")
                return
            action_target = action_target or self._freeze_action_target(
                selected_text=selected,
                subtitle_text=subtitle_text,
                cue_snapshot=cue_snapshot,
                selection_spans=selection_spans,
            )
            subtitle_text = action_target.subtitle_text
            anime_name = self._current_anki_anime_name()
            self._set_busy_cursor(True)
            try:
                dispatch_to_tk(self.settings.root, self.popup.mark_anki_busy)
            except Exception:
                pass
            capture_delay_ms = self._post_add_capture_delay_ms() if post_add_capture else None
            early_capture = post_add_capture and capture_delay_ms is not None and capture_delay_ms < 0
            capture_session = (
                self._new_capture_session(
                    action_target,
                    capture_only=False,
                    early_capture=bool(early_capture),
                    pause_lease=pause_lease,
                )
                if post_add_capture
                else None
            )
            if early_capture:
                self._schedule_post_add_external_capture_hotkey(
                    abs(int(capture_delay_ms)),
                    session=capture_session,
                )

            def worker():
                started = time.perf_counter()
                completion_result = None
                completion_success = False
                try:
                    if not self.anki.ping():
                        raise RuntimeError("AnkiConnect not reachable.")

                    if prepared is None:
                        result = self.anki.add_from_selection(
                            selection_text=selected,
                            subtitle_text=subtitle_text,
                            anime_name=anime_name,
                            copy_existing_media=not bool(post_add_capture),
                            cue_snapshot=action_target.cue_snapshot,
                            selection_spans=action_target.selection_spans,
                        )
                    else:
                        result = self.anki.commit_prepared_note(prepared)
                    completion_result = result
                    try:
                        added_note_id = int(result.get("note_id") or 0)
                    except Exception:
                        added_note_id = 0
                    if added_note_id > 0:
                        self.controller._latest_added_anki_note_id = added_note_id
                        self._record_subtitleplayer_added_card(added_note_id, action_target)
                        completion_success = True
                        dispatch_to_tk(self.settings.root, self._record_successful_anki_add)
                        if not post_add_capture:
                            try:
                                media_result = self.anki.copy_existing_sentence_media_to_note(added_note_id)
                                if str((media_result or {}).get("reason") or "") == "copied":
                                    logger.info(
                                        "Reconciled existing exact-sentence media onto new note %s: %s",
                                        added_note_id,
                                        sorted(((media_result or {}).get("copied") or {}).keys()),
                                    )
                            except Exception:
                                logger.debug(
                                    "Could not reconcile existing sentence media for new note %s",
                                    added_note_id,
                                    exc_info=True,
                                )
                        if capture_session is not None:
                            capture_session.set_target_note(added_note_id)
                            capture_session.phase = "adding"
                            # An early capture may have populated an older
                            # exact-sentence card already.  Reconcile that
                            # state immediately when the new card appears.
                            try:
                                reconciled = self.anki.reconcile_exact_sentence_media(
                                    added_note_id,
                                    baseline=capture_session.baseline_media,
                                )
                                capture_session.reuse_ready = bool(
                                    not reconciled.get("missing_media_fields", ["unknown"])
                                    and reconciled.get("available_media_fields")
                                    and not reconciled.get("failed_note_ids")
                                    and not reconciled.get("requires_fresh_media")
                                )
                            except Exception:
                                logger.debug(
                                    "Could not reconcile early exact-sentence media for note %s",
                                    added_note_id,
                                    exc_info=True,
                                )
                    annotation_entries = self._annotation_entries_for_added_anki_note(result)

                    elapsed = time.perf_counter() - started
                    self._log_anki_add_result(
                        result=result,
                        selected=selected,
                        elapsed=elapsed,
                    )

                    fields = result.get("stroke_svg_sync_fields")
                    lookup_text = str(result.get("selection_lookup_text") or selected).strip()
                    if fields:
                        self.anki.sync_missing_stroke_svgs_async(lookup_text or selected, fields)
                    if annotation_entries:
                        dispatch_to_tk(
                            self.settings.root,
                            self._store_added_anki_annotation_entries,
                            annotation_entries,
                        )
                    dispatch_to_tk(self.settings.root, self._schedule_ocr_sync_after_anki)
                    if result.get("routing_error"):
                        dispatch_to_tk(self.settings.root, self.popup.mark_anki_warning)
                    else:
                        dispatch_to_tk(self.settings.root, self.popup.mark_anki_success)
                        if (
                            not post_add_capture
                            and not bool(getattr(self.controller, "_voice_anki_batch_running", False))
                        ):
                            self._schedule_auto_jump_after_anki()
                    if post_add_capture and added_note_id > 0:
                        note_id = added_note_id
                        if note_id > 0:
                            if capture_session is not None and capture_session.phase == "failed":
                                result["capture_delivery_failed"] = True
                            elif early_capture:
                                self._schedule_post_add_capture_media_copy(
                                    note_id,
                                    session=capture_session,
                                )
                            else:
                                delay_ms = 0 if capture_delay_ms is None else max(0, int(capture_delay_ms))
                                self._schedule_post_add_external_capture_hotkey(
                                    delay_ms,
                                    note_id=note_id,
                                    session=capture_session,
                                )
                except Exception as e:
                    logger.exception("Anki add failed: %s", e)
                    try:
                        dispatch_to_tk(self.settings.root, self.popup.mark_anki_failure)
                    except Exception:
                        pass
                finally:
                    def finish() -> None:
                        self._set_busy_cursor(False)
                        if capture_session is None or not completion_success:
                            release = getattr(self.controller, "release_anki_pause_lease", None)
                            if callable(release):
                                release(pause_lease, resume=False)
                        if capture_session is None:
                            self._finish_action_feedback(action_target, bool(completion_success))
                        elif not completion_success:
                            self._finish_action_feedback_key(capture_session.action_feedback_key, False)
                        if callable(on_complete):
                            on_complete(bool(completion_success), completion_result)

                    dispatch_to_tk(self.settings.root, finish)

            threading.Thread(target=worker, daemon=True).start()

    def _show_anki_preview_window(
        self,
        prepared: dict,
        selected: str,
        subtitle_text: str = "",
        post_add_capture: bool = False,
        on_complete=None,
        action_target: FrozenActionTarget | None = None,
        pause_lease=None,
    ) -> None:
            note = prepared.get("note") if isinstance(prepared, dict) else None
            if not isinstance(note, dict):
                logger.debug("Anki preview skipped because prepared note is invalid")
                self._finish_action_feedback(action_target, False)
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release):
                    release(pause_lease, resume=False)
                if callable(on_complete):
                    on_complete(False, None)
                return
            fields = note.get("fields")
            if not isinstance(fields, dict):
                fields = {}
                note["fields"] = fields

            win = tk.Toplevel(self.settings.root)
            self.controller._anki_preview_window = win
            win.title("Preview Anki Note")
            win.resizable(True, True)
            try:
                win.transient(self.settings.root)
            except Exception:
                pass

            outer = tk.Frame(win, padx=10, pady=10)
            outer.pack(fill="both", expand=True)
            outer.grid_columnconfigure(0, weight=1)
            outer.grid_rowconfigure(1, weight=1)

            header = tk.Frame(outer)
            header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
            header.grid_columnconfigure(1, weight=1)
            tk.Label(header, text="Deck").grid(row=0, column=0, sticky="w")
            deck_var = tk.StringVar(value=str(note.get("deckName") or ""))
            tk.Entry(header, textvariable=deck_var).grid(row=0, column=1, sticky="ew", padx=(8, 0))
            tk.Label(header, text="Note type").grid(row=1, column=0, sticky="w", pady=(4, 0))
            model_var = tk.StringVar(value=str(note.get("modelName") or ""))
            tk.Entry(header, textvariable=model_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0))

            canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0)
            scroll = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
            fields_frame = tk.Frame(canvas)
            canvas_window = canvas.create_window((0, 0), window=fields_frame, anchor="nw")
            canvas.configure(yscrollcommand=scroll.set)
            canvas.grid(row=1, column=0, sticky="nsew")
            scroll.grid(row=1, column=1, sticky="ns")
            fields_frame.grid_columnconfigure(1, weight=1)

            text_widgets: dict[str, tk.Text] = {}
            preferred = [
                getattr(self.anki, "add_rubies_to_front_field", "AddRubiesToFront"),
                getattr(self.anki, "front_field", "Front"),
                getattr(self.anki, "back_field", "Back"),
                getattr(self.anki, "sentence_ja_field", "SentenceJA"),
                getattr(self.anki, "sentence_de_field", "SentenceDE"),
                getattr(self.anki, "sound_field", "Sound"),
                getattr(self.anki, "image_field", "Image"),
                getattr(self.anki, "definition_field", "Definition"),
                getattr(self.anki, "add_rubies_to_sentence_ja_field", "AddRubiesToSentenceJA"),
            ]
            ordered_names = []
            for name in preferred:
                if name in fields and name not in ordered_names:
                    ordered_names.append(name)
            for name in fields:
                if name not in ordered_names:
                    ordered_names.append(name)

            for row, name in enumerate(ordered_names):
                tk.Label(fields_frame, text=name, anchor="w").grid(row=row, column=0, sticky="nw", padx=(0, 8), pady=3)
                value = str(fields.get(name) or "")
                height = 4 if len(value) > 80 or "\n" in value else 2
                widget = tk.Text(fields_frame, height=height, width=68, wrap="word", undo=True)
                widget.insert("1.0", value)
                widget.grid(row=row, column=1, sticky="ew", pady=3)
                text_widgets[name] = widget

            tags_var = tk.StringVar(value=", ".join(str(tag) for tag in (note.get("tags") or [])))
            tag_row = len(ordered_names)
            tk.Label(fields_frame, text="Tags", anchor="w").grid(row=tag_row, column=0, sticky="w", padx=(0, 8), pady=3)
            tags_entry = tk.Entry(fields_frame, textvariable=tags_var)
            tags_entry.grid(row=tag_row, column=1, sticky="ew", pady=3)

            def _on_fields_configure(_event=None):
                try:
                    canvas.configure(scrollregion=canvas.bbox("all"))
                    canvas.itemconfigure(canvas_window, width=canvas.winfo_width())
                except Exception:
                    pass

            def _on_preview_mousewheel(event):
                try:
                    if getattr(event, "num", None) == 4:
                        units = -3
                    elif getattr(event, "num", None) == 5:
                        units = 3
                    else:
                        delta = int(getattr(event, "delta", 0) or 0)
                        units = -1 * int(delta / 120) if delta else 0
                    if units:
                        canvas.yview_scroll(units, "units")
                except Exception:
                    pass
                return "break"

            fields_frame.bind("<Configure>", _on_fields_configure)
            canvas.bind("<Configure>", _on_fields_configure)
            for wheel_widget in (win, outer, canvas, fields_frame, tags_entry, *text_widgets.values()):
                wheel_widget.bind("<MouseWheel>", _on_preview_mousewheel, add="+")
                wheel_widget.bind("<Button-4>", _on_preview_mousewheel, add="+")
                wheel_widget.bind("<Button-5>", _on_preview_mousewheel, add="+")

            btn_row = tk.Frame(outer)
            btn_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
            btn_row.grid_columnconfigure(0, weight=1)

            def _confirm() -> None:
                note["deckName"] = deck_var.get().strip() or self.anki.deck_name
                note["modelName"] = model_var.get().strip() or self.anki.model_name
                for field_name, widget in text_widgets.items():
                    fields[field_name] = widget.get("1.0", "end-1c")
                note["fields"] = fields
                note["tags"] = [tag.strip() for tag in tags_var.get().replace(";", ",").split(",") if tag.strip()]
                prepared["fields"] = fields
                prepared["stroke_svg_sync_fields"] = fields
                try:
                    win.destroy()
                except Exception:
                    pass
                self._start_anki_commit_worker(
                    selected_text=selected,
                    subtitle_text=subtitle_text,
                    prepared=prepared,
                    post_add_capture=bool(post_add_capture),
                    on_complete=on_complete,
                    action_target=action_target,
                    pause_lease=pause_lease,
                )

            def _cancel() -> None:
                try:
                    win.destroy()
                except Exception:
                    pass
                self._finish_action_feedback(action_target, False)
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release):
                    release(pause_lease, resume=False)
                if callable(on_complete):
                    on_complete(False, None)

            tk.Button(btn_row, text="Confirm Add", width=14, command=_confirm).pack(side="right")
            tk.Button(btn_row, text="Cancel", width=10, command=_cancel).pack(side="right", padx=(0, 8))

            win.update_idletasks()
            width = min(820, max(620, int(win.winfo_reqwidth() or 620)))
            height = min(680, max(420, int(win.winfo_reqheight() or 420)))
            try:
                root = self.settings.root
                x = int(root.winfo_rootx() + (root.winfo_width() - width) / 2)
                y = int(root.winfo_rooty() + (root.winfo_height() - height) / 2)
                if x < 0 or y < 0:
                    raise ValueError
            except Exception:
                x = int((win.winfo_screenwidth() - width) / 2)
                y = int((win.winfo_screenheight() - height) / 2)
            win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
            try:
                win.focus_set()
            except Exception:
                pass

            def _clear_preview_reference(event=None) -> None:
                if event is not None and getattr(event, "widget", None) is not win:
                    return
                if getattr(self.controller, "_anki_preview_window", None) is win:
                    self.controller._anki_preview_window = None

            win.bind("<Destroy>", _clear_preview_reference, add="+")
            win.protocol("WM_DELETE_WINDOW", _cancel)
            win.bind("<Escape>", lambda _event: _cancel())

    def _post_add_capture_delay_ms(self) -> int | None:
            browser = getattr(self.controller, "browser_sync", None)
            if browser is not None and browser.native:
                return 0
            raw = self.config.get("POST_ADD_CAPTURE_DELAY_MS")
            if raw is None or str(raw).strip() == "":
                return None
            try:
                value = int(float(str(raw).strip().replace(",", ".")))
            except Exception:
                return None
            return max(-60000, min(60000, value))

    def _post_add_capture_target_title(self) -> str:
            return str(
                self.config.get("POST_ADD_CAPTURE_TARGET_TITLE")
                or "ABSPlayer, Google Chrome, Chrome"
            ).strip()

    def _post_add_capture_media_copy_delay_ms(self) -> int:
            raw = self.config.get("POST_ADD_CAPTURE_MEDIA_COPY_DELAY_MS")
            try:
                value = int(float(str(raw).strip().replace(",", ".")))
            except Exception:
                value = 1500
            return max(0, min(60000, value))

    def _post_add_capture_hotkey(self) -> str:
            return str(self.config.get("POST_ADD_CAPTURE_EXTERNAL_HOTKEY") or "ctrl+shift+y").strip()

    def _schedule_post_add_external_capture_hotkey(
        self,
        delay_ms: int = 0,
        *,
        note_id: int | None = None,
        session: CaptureSession | None = None,
    ) -> None:
            delay_ms = max(0, int(delay_ms or 0))

            def _run() -> None:
                if session is not None and session.cancelled:
                    return
                sent = self._run_post_add_external_capture(note_id, session=session)
                if sent or session is None:
                    return
                session.phase = "failed"
                self._finish_action_feedback_key(session.action_feedback_key, False)
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release):
                    release(session.pause_lease, resume=False)
                try:
                    parsed_note_id = int(note_id or session.target_note_id or 0)
                except Exception:
                    parsed_note_id = 0
                if parsed_note_id > 0:
                    hotkeys = getattr(self.controller, "hotkey_controller", None)
                    notify = getattr(hotkeys, "notify_capture_media_finished", None)
                    if callable(notify):
                        notify(parsed_note_id, False)

            try:
                dispatch_to_tk(self.settings.root, _run, delay_ms=delay_ms)
            except Exception:
                _run()

    def _run_post_add_external_capture(
        self,
        note_id: int | None = None,
        *,
        capture_only: bool = False,
        session: CaptureSession | None = None,
    ) -> bool:
            if session is not None:
                if session.cancelled:
                    return False
                capture_only = bool(session.capture_only)
                if note_id is None:
                    note_id = session.target_note_id
            if session is not None and session.backend == "native":
                try:
                    self.controller.browser_sync.capture(session, note_id)
                    return True
                except Exception as exc:
                    self.controller.browser_sync._set_status("Capture failed · " + str(exc))
                    return False
            target_title = self._post_add_capture_target_title()
            external_hotkey = self._post_add_capture_hotkey()
            note_label = note_id if note_id is not None else "pending note"
            if not external_hotkey:
                logger.warning("Post-add capture skipped for %s because external hotkey is empty", note_label)
                return False
            command_service = getattr(self.controller, "video_commands", None)
            if command_service is not None:
                delivery = command_service.deliver("capture")
                if not delivery.ok:
                    logger.warning(
                        "Post-add capture could not deliver to the shared video target for %s: %s",
                        note_label,
                        delivery.reason,
                    )
                    return False
                try:
                    self._latest_post_add_capture_window_hwnd = int((delivery.target or {}).get("hwnd") or 0)
                except Exception:
                    self._latest_post_add_capture_window_hwnd = 0
            else:
                # Compatibility path for controller/test doubles that have not
                # yet constructed the shared video-command service.
                if target_title and not focus_window_by_title(target_title):
                    logger.warning(
                        "Post-add capture skipped for %s because target window was not found/focused: %s",
                        note_label,
                        target_title,
                    )
                    return False
                try:
                    self._latest_post_add_capture_window_hwnd = int(get_foreground_root_hwnd() or 0)
                except Exception:
                    self._latest_post_add_capture_window_hwnd = 0
                suppress = getattr(self.controller, "_suppress_external_hotkey_events", None)
                if callable(suppress):
                    suppress(external_hotkey, 1)
                if not send_global_hotkey(external_hotkey):
                    logger.warning("Post-add capture failed to send hotkey %s for %s", external_hotkey, note_label)
                    return False
            logger.info("Post-add capture hotkey %s sent for %s", external_hotkey, note_label)
            if session is not None:
                session.phase = "capture_sent"
                session.dispatched_monotonic = time.monotonic()
            if note_id is not None:
                try:
                    note_id_int = int(note_id)
                except Exception:
                    note_id_int = 0
                if note_id_int > 0:
                    self._schedule_post_add_capture_media_copy(
                        note_id_int,
                        capture_only=bool(capture_only),
                        session=session,
                    )
            return True

    def trigger_external_capture_only(
        self,
        *,
        use_capture_only_resume: bool = True,
        interaction_owner: str = "ui",
    ) -> dict[str, Any]:
            """Capture for the last card created by this SubtitlePlayer run.

            Falling back to a deck-wide "newest" note is unsafe: another Anki
            action can win that race.  The in-memory record also supplies the
            exact sentence key needed for reverse media reconciliation.
            """
            record = dict(self._last_subtitleplayer_added_card or {})
            try:
                source_note_id = int(record.get("note_id") or 0)
            except Exception:
                source_note_id = 0
            target = record.get("target")
            if source_note_id <= 0 or not isinstance(target, FrozenActionTarget):
                message = "Capture-only needs a card added by SubtitlePlayer since this launch. Add a card first."
                report = getattr(self.controller, "_report_voice_status", None)
                if callable(report):
                    report("error", message)
                return {
                    "accepted": False,
                    "queued": False,
                    "source_note_id": 0,
                    "reason": "no_current_run_target",
                }

            owner = str(interaction_owner or "ui").strip().lower() or "ui"
            if owner != "voice":
                self._begin_action_feedback(target, force=True)
            pause_lease = self._acquire_pause_lease(target)
            session = self._new_capture_session(
                target,
                capture_only=bool(use_capture_only_resume),
                early_capture=False,
                pause_lease=pause_lease,
                interaction_owner=owner,
            )
            session.set_target_note(source_note_id)
            sent = bool(
                self._run_post_add_external_capture(
                    source_note_id,
                    capture_only=bool(use_capture_only_resume),
                    session=session,
                )
            )
            if sent:
                report = getattr(self.controller, "_report_voice_status", None)
                if callable(report):
                    report(
                        "working",
                        f"Capture sent for note {source_note_id}; checking exact-sentence cards for media.",
                    )
            else:
                self._finish_action_feedback_key(session.action_feedback_key, False)
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release):
                    release(session.pause_lease, resume=False)
            return {
                "accepted": sent,
                "queued": False,
                "source_note_id": source_note_id,
                "session_id": session.id,
                "reason": "sent" if sent else "capture_delivery_failed",
            }

    def _schedule_post_add_capture_media_copy(
        self,
        note_id: int,
        *,
        capture_only: bool = False,
        session: CaptureSession | None = None,
    ) -> None:
            try:
                note_id = int(note_id)
            except Exception:
                return
            self._latest_post_add_capture_note_id = note_id
            if session is not None:
                session.set_target_note(note_id)
                session.phase = "media_polling"
            try:
                hotkeys = getattr(self.controller, "hotkey_controller", None)
                register = getattr(hotkeys, "register_capture_feedback", None)
                if callable(register):
                    register(note_id)
            except Exception:
                logger.debug("Failed to register capture feedback for note %s", note_id, exc_info=True)
            delay_ms = self._post_add_capture_media_copy_delay_ms()
            logger.info(
                "Anki media copy-back scheduled for source note %s; first check in %d ms",
                note_id,
                delay_ms,
            )

            def _start_worker() -> None:
                self._start_post_add_capture_media_copy_worker(
                    note_id,
                    capture_only=bool(capture_only),
                    session=session,
                )

            try:
                dispatch_to_tk(self.settings.root, _start_worker, delay_ms=delay_ms)
            except Exception:
                _start_worker()

    def _finish_capture_after_media_ready(
        self,
        note_id: int,
        *,
        capture_only: bool,
        session: CaptureSession | None = None,
    ) -> None:
            """Jump at media completion, then optionally resume synchronized playback."""
            try:
                note_id = int(note_id)
            except Exception:
                return
            if session is not None and session.cancelled:
                return
            if session is not None:
                session.phase = "completed"
                self._finish_action_feedback_key(session.action_feedback_key, True)
            try:
                hotkeys = getattr(self.controller, "hotkey_controller", None)
                notify = getattr(hotkeys, "notify_capture_media_finished", None)
                if callable(notify):
                    notify(note_id, True)
            except Exception:
                logger.debug("Failed to complete capture feedback for note %s", note_id, exc_info=True)
            latest_note_id = int(getattr(self.controller, "_latest_post_add_capture_note_id", 0) or 0)
            if (session is not None and not self._capture_session_is_latest(session)) or (
                session is None and note_id != latest_note_id
            ):
                logger.debug("Skipping delayed capture jump for superseded note %s", note_id)
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release) and session is not None:
                    release(session.pause_lease, resume=False)
                return

            jump_to_end = getattr(getattr(self.controller, "playback", None), "on_jump_sub_end", None)
            if not callable(jump_to_end):
                logger.warning("Capture media is ready for note %s, but subtitle-end jump is unavailable", note_id)
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release) and session is not None:
                    release(session.pause_lease, resume=False)
                return
            try:
                jump_to_end()
            except Exception:
                logger.exception("Failed to jump after capture media became ready for note %s", note_id)
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release) and session is not None:
                    release(session.pause_lease, resume=False)
                return

            logger.info("Capture media ready for note %s; jumped to subtitle end", note_id)
            resume_key = (
                "CAPTURE_ONLY_RESUME_PLAYBACK"
                if capture_only
                else "POST_ADD_CAPTURE_RESUME_PLAYBACK"
            )
            if bool(self.config.get(resume_key) or False):
                self.controller.resume_video_and_subtitles_after_capture(note_id, session=session)
            else:
                release = getattr(self.controller, "release_anki_pause_lease", None)
                if callable(release) and session is not None:
                    release(session.pause_lease, resume=False)

    def _start_post_add_capture_media_copy_worker(
        self,
        note_id: int,
        *,
        capture_only: bool = False,
        session: CaptureSession | None = None,
    ) -> None:
            def notify_finished(succeeded: bool) -> None:
                if session is not None and session.cancelled:
                    return
                try:
                    hotkeys = getattr(self.controller, "hotkey_controller", None)
                    notify = getattr(hotkeys, "notify_capture_media_finished", None)
                    if callable(notify):
                        dispatch_to_tk(self.settings.root, notify, note_id, bool(succeeded))
                except Exception:
                    logger.debug("Failed to report capture media completion for note %s", note_id, exc_info=True)
                if not succeeded and session is not None:
                    session.phase = "timeout" if session.phase == "media_polling" else "failed"
                    self._finish_action_feedback_key(session.action_feedback_key, False)
                    release = getattr(self.controller, "release_anki_pause_lease", None)
                    if callable(release):
                        try:
                            dispatch_to_tk(self.settings.root, release, session.pause_lease, resume=False)
                        except Exception:
                            release(session.pause_lease, resume=False)

            def worker() -> None:
                last_result = None
                retry_reasons = {
                    "source_media_missing",
                    "source_media_partial",
                    "source_note_missing",
                    "source_notes_info_failed",
                    "update_failed",
                }
                poll_interval_ms = max(250, self._post_add_capture_media_copy_delay_ms())
                timeout_sec = 60.0
                started = time.monotonic()
                attempt = 0
                last_available_fields: tuple[str, ...] = ()
                media_ready_handled = False
                logger.debug(
                    "Anki media copy-back polling started for source note %s; interval=%d ms timeout=%.0f s",
                    note_id,
                    poll_interval_ms,
                    timeout_sec,
                )
                while True:
                    if bool(getattr(self.controller, "_shutting_down", False)):
                        return
                    if session is not None and session.cancelled:
                        return
                    attempt += 1
                    try:
                        last_result = self.anki.reconcile_exact_sentence_media(
                            note_id,
                            baseline=(session.baseline_media if session is not None else None),
                        )
                    except Exception:
                        logger.exception("Post-capture media copy failed for note %s", note_id)
                        notify_finished(False)
                        return

                    reason = str((last_result or {}).get("reason") or "")
                    target_note_id = (last_result or {}).get("target_note_id")
                    target_note_ids = list((last_result or {}).get("target_note_ids") or [])
                    available_fields = list((last_result or {}).get("available_media_fields") or [])
                    missing_fields = list((last_result or {}).get("missing_media_fields") or [])
                    copied_fields = sorted(((last_result or {}).get("copied") or {}).keys())
                    copied_note_ids = list((last_result or {}).get("copied_note_ids") or [])
                    elapsed = time.monotonic() - started
                    logger.debug(
                        "Anki media copy-back check #%d source=%s targets=%s elapsed=%.1fs "
                        "available=%s missing=%s copied=%s result=%s",
                        attempt,
                        note_id,
                        target_note_ids or ([target_note_id] if target_note_id else []),
                        elapsed,
                        available_fields or "none",
                        missing_fields or "none",
                        copied_fields or "none",
                        reason or "unknown",
                    )
                    current_available_fields = tuple(sorted(available_fields))
                    if current_available_fields and current_available_fields != last_available_fields:
                        logger.info(
                            "Anki media copy-back detected media on source note %s after %.1fs: %s",
                            note_id,
                            elapsed,
                            list(current_available_fields),
                        )
                    last_available_fields = current_available_fields
                    if copied_note_ids:
                        logger.info(
                            "Anki media copy-back updated %d previous same-sentence note(s): notes=%s fields=%s%s",
                            len(copied_note_ids),
                            copied_note_ids,
                            copied_fields,
                            f"; still waiting for {missing_fields}" if missing_fields else "",
                        )

                    if (
                        not media_ready_handled
                        and available_fields
                        and not missing_fields
                    ):
                        media_ready_handled = True
                        dispatch_to_tk(
                            self.settings.root,
                            self._finish_capture_after_media_ready,
                            note_id,
                            capture_only=bool(capture_only),
                            session=session,
                        )

                    partial_result = bool(available_fields) and bool(missing_fields)
                    if reason not in retry_reasons and not partial_result:
                        break
                    if elapsed >= timeout_sec:
                        logger.warning(
                            "Anki media copy-back timed out for source note %s after %.1fs; missing=%s result=%s",
                            note_id,
                            elapsed,
                            missing_fields or "unknown",
                            reason or "unknown",
                        )
                        notify_finished(False)
                        return

                    wait_sec = min(poll_interval_ms / 1000.0, max(0.0, timeout_sec - elapsed))
                    shutdown_event = getattr(self.controller, "_shutdown_event", None)
                    if shutdown_event is not None and hasattr(shutdown_event, "wait"):
                        if shutdown_event.wait(wait_sec):
                            return
                    else:
                        time.sleep(wait_sec)
                    if session is not None and session.cancelled:
                        return

                if reason == "target_already_has_media":
                    logger.info(
                        "Anki media copy-back finished for source note %s; all %d matching previous note(s) already contain the available media",
                        note_id,
                        len(target_note_ids),
                    )
                elif reason == "same_sentence_missing":
                    logger.debug(
                        "Anki media copy-back finished for source note %s; no other exact-sentence note needs updating",
                        note_id,
                    )
                elif reason != "copied":
                    logger.warning("Anki media copy-back stopped for source note %s: %s", note_id, reason or "unknown")
                if not media_ready_handled:
                    notify_finished(False)

            threading.Thread(target=worker, daemon=True).start()

    def _schedule_auto_jump_after_anki(self) -> None:
            try:
                enabled = bool(self.config.get("ANKI_AUTO_JUMP_AFTER_ADD") or False)
            except Exception:
                enabled = False
            if not enabled:
                return

            def _jump() -> None:
                try:
                    self.playback.on_jump_sub_end()
                    logger.info("Auto jump after Anki add executed")
                except Exception:
                    logger.exception("Auto jump after Anki add failed")

            try:
                dispatch_to_tk(self.settings.root, _jump)
            except Exception:
                logger.debug("Failed to queue auto jump after Anki add", exc_info=True)

    def _log_anki_add_result(self, result: dict, selected: str, elapsed: float) -> None:
            candidates = result.get("translation_candidates") or {}
            word_cands = candidates.get("word") or {}
            sentence_cands = candidates.get("sentence") or {}
            lookup_text = str(result.get("selection_lookup_text") or selected).strip()
            copied_media = result.get("copied_media_fields") or {}
            routed = result.get("routed_cards") or {}
            routed_ids = [
                str(card_id)
                for values in routed.values()
                for card_id in (values or [])
            ]
            providers = result.get("translation_provider_used") or {}

            def _value(value, default="<empty>"):
                text = self._format_translation_csv(value)
                return text if text else default

            fallback_used = []
            if providers.get("word") and providers.get("word") != getattr(self.anki, "word_translate_provider", ""):
                fallback_used.append(f"word->{providers.get('word')}")
            if providers.get("sentence") and providers.get("sentence") != getattr(self.anki, "sentence_translate_provider", ""):
                fallback_used.append(f"sentence->{providers.get('sentence')}")

            def _print(line: str) -> None:
                print(line, flush=True)

            _print(f"Time: {elapsed:.2f}s")
            _print(f"Word: {selected}")
            _print(f"Lookup: {lookup_text or selected}")
            _print(f"Translation: {_value(result.get('word_translation', ''))}")
            _print(f"Sentence DeepL: {_value(sentence_cands.get('deepl', ''))}")
            _print(f"Sentence Google: {_value(sentence_cands.get('google', ''))}")
            _print(f"Definition: {_value(result.get('definition', ''))}")
            _print(f"Sound copied: {'yes' if self.anki.sound_field in copied_media else 'no'}")
            _print(f"Image copied: {'yes' if self.anki.image_field in copied_media else 'no'}")
            _print(f"Note ID: {result.get('note_id', '')}")
            _print(f"Card IDs: {', '.join(routed_ids) if routed_ids else '<none reported>'}")
            _print(f"Providers: word={providers.get('word', '') or 'none'} sentence={providers.get('sentence', '') or 'none'}")
            _print(f"Fallback: {', '.join(fallback_used) if fallback_used else 'none'}")
            _print(f"Word Jisho: {_value(word_cands.get('jisho', ''))}")
            _print(f"Word Google: {_value(word_cands.get('google', ''))}")
            if result.get("routing_error"):
                _print(f"Routing warning: note was created but card routing failed: {result.get('routing_error')}")


    def _schedule_ocr_sync_after_anki(self, duration_sec: float = 5.0, interval_sec: float = 1.0) -> None:
            browser = getattr(self.controller, "browser_sync", None)
            if browser is not None and browser.following:
                return
            if self._shutting_down:
                return
            if not self.config.get("OCR_ENABLED"):
                return
            if not self._ocr_sync_after_anki_enabled():
                return
            try:
                duration_sec = float(duration_sec)
            except Exception:
                duration_sec = 5.0
            try:
                interval_sec = float(interval_sec)
            except Exception:
                interval_sec = 1.0
            duration_sec = max(1.0, duration_sec)
            interval_sec = max(0.4, interval_sec)

            self._ocr_sync_generation += 1
            generation = self._ocr_sync_generation

            def worker():
                diffs = []
                deadline = time.perf_counter() + duration_sec
                while time.perf_counter() < deadline:
                    if self._shutting_down or generation != self._ocr_sync_generation:
                        return
                    started = time.perf_counter()
                    try:
                        base_time = float(self.current_time)
                    except Exception:
                        base_time = None
                    seconds = self._ocr_find_time_seconds(override={"OCR_DEBUG": False})
                    if seconds is not None and base_time is not None:
                        elapsed = time.perf_counter() - started
                        if self.playing:
                            base_time += elapsed
                        diffs.append(seconds - base_time)
                    sleep_for = interval_sec - (time.perf_counter() - started)
                    if sleep_for > 0:
                        time.sleep(sleep_for)
                if not diffs:
                    return
                diffs.sort()
                mid = len(diffs) // 2
                if len(diffs) % 2 == 1:
                    median = diffs[mid]
                else:
                    median = (diffs[mid - 1] + diffs[mid]) / 2.0
                try:
                    dispatch_to_tk(self.settings.root, self._apply_ocr_sync_delta, median)
                except Exception:
                    pass

            self._ocr_sync_thread = threading.Thread(target=worker, daemon=True)
            self._ocr_sync_thread.start()

    def _ocr_sync_after_anki_enabled(self) -> bool:
            raw = self.config.get("OCR_SYNC_AFTER_ANKI")
            if raw is None:
                return True
            return bool(raw)

    def prompt_anki_connection(self, *, on_ready=None) -> None:
            def _show() -> None:
                self._show_anki_wait_dialog(on_ready=on_ready)

            try:
                dispatch_to_tk(self.settings.root, _show)
            except Exception:
                logger.debug("Failed to queue Anki connection dialog", exc_info=True)

    def _show_anki_wait_dialog(
        self,
        selected_text: str = "",
        subtitle_text: str = "",
        on_ready=None,
        post_add_capture: bool = False,
        on_add_complete=None,
        cue_snapshot=None,
        selection_spans=None,
        action_target: FrozenActionTarget | None = None,
        pause_lease=None,
    ) -> None:
            selected = (selected_text or "").strip()
            auto_start = bool(self.config.get("ANKI_AUTO_START") or False)

            existing = getattr(self, "_anki_wait_window", None)
            if existing is not None:
                if existing.winfo_exists():
                    if selected:
                        self._pending_anki_payload = {
                            "selected_text": selected,
                            "subtitle_text": subtitle_text,
                            "post_add_capture": bool(post_add_capture),
                            "on_complete": on_add_complete,
                            "cue_snapshot": cue_snapshot,
                            "selection_spans": tuple(selection_spans or ()),
                            "action_target": action_target,
                            "pause_lease": pause_lease,
                        }
                    existing.deiconify()
                    existing.lift()
                    existing.attributes("-topmost", True)
                    return

            if selected:
                self._pending_anki_payload = {
                    "selected_text": selected,
                    "subtitle_text": subtitle_text,
                    "post_add_capture": bool(post_add_capture),
                    "on_complete": on_add_complete,
                    "cue_snapshot": cue_snapshot,
                    "selection_spans": tuple(selection_spans or ()),
                    "action_target": action_target,
                    "pause_lease": pause_lease,
                }
            else:
                self._pending_anki_payload = None

            parent = getattr(self.settings, "root", None)
            win = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
            self._anki_wait_window = win
            win.title("Anki Not Connected")
            win.attributes("-topmost", True)
            win.resizable(False, False)
            win.transient(parent)
            win.grab_set()

            body = tk.Frame(win, padx=12, pady=10)
            body.pack(fill="both", expand=True)
            tk.Label(
                body,
                text=(
                    "AnkiConnect is not reachable.\n"
                    + (
                        "SubtitlePlayer will start Anki and continue automatically."
                        if auto_start
                        else "Please open Anki, then press the button below."
                    )
                ),
                justify="left",
                anchor="w",
            ).pack(fill="x")

            status_var = tk.StringVar(value="Waiting for confirmation...")
            self._anki_wait_status_var = status_var
            tk.Label(body, textvariable=status_var, anchor="w", fg="#1a4d1a").pack(fill="x", pady=(8, 0))

            btn_row = tk.Frame(body)
            btn_row.pack(fill="x", pady=(10, 0))
            wait_state = {"running": False, "completed": False}
            closed_event = threading.Event()

            def _set_status(text: str) -> None:
                status = getattr(self, "_anki_wait_status_var", None)
                if status is not None:
                    status.set(text)

            def _begin_wait_for_anki(status_text: str = "Waiting for AnkiConnect...") -> None:
                if wait_state["running"]:
                    return
                wait_state["running"] = True
                open_btn.configure(state="disabled")
                _set_status(status_text)

                def wait_worker():
                    while not self._shutting_down and not closed_event.is_set():
                        if self.anki.ping():
                            payload = dict(self._pending_anki_payload or {})

                            def _finish():
                                if closed_event.is_set():
                                    return
                                wait_state["completed"] = True
                                self._pending_anki_payload = None
                                if win.winfo_exists():
                                    win.destroy()
                                payload_selected = (payload.get("selected_text", "") or "").strip()
                                if payload_selected:
                                    self._start_anki_add_worker(
                                        selected_text=payload_selected,
                                        subtitle_text=payload.get("subtitle_text", ""),
                                        post_add_capture=bool(payload.get("post_add_capture")),
                                        on_complete=payload.get("on_complete"),
                                        cue_snapshot=payload.get("cue_snapshot"),
                                        selection_spans=payload.get("selection_spans"),
                                        action_target=payload.get("action_target"),
                                        pause_lease=payload.get("pause_lease"),
                                    )
                                    return
                                if callable(on_ready):
                                    on_ready()

                            try:
                                dispatch_to_tk(self.settings.root, _finish)
                            except Exception:
                                pass
                            return
                        closed_event.wait(0.5)

                self._anki_wait_thread = threading.Thread(target=wait_worker, daemon=True)
                self._anki_wait_thread.start()

            open_btn = tk.Button(btn_row, text="Anki opened", width=14, command=_begin_wait_for_anki)
            open_btn.pack(side="left")
            tk.Button(btn_row, text="Cancel", width=10, command=win.destroy).pack(side="left", padx=(6, 0))

            def _on_destroy(_event):
                if _event.widget is not win:
                    return
                closed_event.set()
                self._anki_wait_window = None
                self._anki_wait_status_var = None
                self._anki_wait_thread = None
                if not wait_state.get("completed"):
                    payload = dict(self._pending_anki_payload or {})
                    target = payload.get("action_target")
                    self._finish_action_feedback(target if isinstance(target, FrozenActionTarget) else None, False)
                    release = getattr(self.controller, "release_anki_pause_lease", None)
                    if callable(release):
                        release(payload.get("pause_lease"), resume=False)
                    callback = payload.get("on_complete")
                    if callable(callback):
                        callback(False, None)
                self._pending_anki_payload = None

            win.bind("<Destroy>", _on_destroy)
            win.bind("<Escape>", lambda _e: win.destroy())

            self._center_anki_wait_window(win, parent)

            if auto_start:
                open_btn.configure(state="disabled")
                _set_status("Starting Anki...")

                def launch_worker() -> None:
                    launched, message = self._launch_anki_automatically()

                    def finish_launch() -> None:
                        if closed_event.is_set() or not win.winfo_exists():
                            return
                        if launched:
                            _begin_wait_for_anki(message)
                        else:
                            open_btn.configure(state="normal")
                            _set_status(message)

                    dispatch_to_tk(self.settings.root, finish_launch)

                threading.Thread(target=launch_worker, daemon=True, name="anki-auto-start").start()

    @staticmethod
    def _centered_position_on_monitor(
        width: int,
        height: int,
        monitors,
        anchor_x: int | None = None,
        anchor_y: int | None = None,
    ) -> tuple[int, int]:
            monitors = list(monitors or [(0, 0, 1920, 1080)])
            monitor = monitors[0]
            if anchor_x is not None and anchor_y is not None:
                containing = [
                    rect
                    for rect in monitors
                    if rect[0] <= anchor_x < rect[0] + rect[2]
                    and rect[1] <= anchor_y < rect[1] + rect[3]
                ]
                if containing:
                    monitor = containing[0]
                else:
                    monitor = min(
                        monitors,
                        key=lambda rect: (
                            anchor_x - (rect[0] + rect[2] / 2.0)
                        ) ** 2
                        + (
                            anchor_y - (rect[1] + rect[3] / 2.0)
                        ) ** 2,
                    )
            mx, my, mw, mh = monitor
            x = mx + max((mw - int(width)) // 2, 0)
            y = my + max((mh - int(height)) // 2, 0)
            return int(x), int(y)

    def _center_anki_wait_window(self, win, parent=None) -> None:
            try:
                win.update_idletasks()
                ww = int(win.winfo_reqwidth())
                wh = int(win.winfo_reqheight())

                anchor_x = anchor_y = None
                for source in (parent, win):
                    if source is None:
                        continue
                    try:
                        anchor_x = int(source.winfo_pointerx())
                        anchor_y = int(source.winfo_pointery())
                        break
                    except Exception:
                        pass
                if (anchor_x is None or anchor_y is None) and parent is not None:
                    try:
                        anchor_x = int(parent.winfo_rootx()) + int(parent.winfo_width()) // 2
                        anchor_y = int(parent.winfo_rooty()) + int(parent.winfo_height()) // 2
                    except Exception:
                        anchor_x = anchor_y = None

                monitors = get_monitor_rects(parent or win)
                x, y = self._centered_position_on_monitor(ww, wh, monitors, anchor_x, anchor_y)
                win.geometry(f"+{x}+{y}")
            except Exception:
                logger.debug("Failed to center Anki wait dialog", exc_info=True)

    def _format_translation_csv(self, value: str) -> str:
            text = (value or "").replace("\n", " ").replace("\r", " ").strip()
            text = " ".join(text.split())
            if not text:
                return "<empty>"
            text = text.replace(";", ",").replace("|", ",")
            parts = [part.strip() for part in text.split(",") if part.strip()]
            if not parts:
                return text
            return ", ".join(parts)

    def _set_busy_cursor(self, busy: bool) -> None:
            self.controller._anki_add_busy = bool(busy)
            if not busy:
                self.controller._voice_anki_action_queued = False
            cursor = self.anki_busy_cursor if busy else ""
            windows = [
                getattr(self.settings, "root", None),
                getattr(self.settings, "control_window", None),
                getattr(self.overlay, "sub_window", None),
                getattr(self.popup, "_popup", None),
            ]
            for win in windows:
                if not win:
                    continue
                try:
                    win.configure(cursor=cursor)
                except Exception:
                    pass
            try:
                self.popup.set_busy_cursor(cursor)
            except Exception:
                pass
            try:
                self.settings.root.update()
            except Exception:
                pass

    def on_anki_check_connection(self) -> bool:
            try:
                connected = bool(self.anki.ping())
            except Exception:
                connected = False
            if not connected:
                self.prompt_anki_connection()
            return connected

    def _show_anki_success_popup(self, message: str = "Anki card added") -> None:
            try:
                existing = getattr(self, "_anki_success_popup", None)
                if existing is not None and existing.winfo_exists():
                    existing.destroy()
            except Exception:
                pass

            root = self.settings.root
            popup = tk.Toplevel(root)
            self._anki_success_popup = popup

            popup.withdraw()
            popup.overrideredirect(True)
            popup.attributes("-topmost", True)
            make_nonactivating_tool_window(popup)
            popup.resizable(False, False)

            popup.configure(bg="white")

            border = tk.Frame(
                popup,
                bg="white",
                bd=1,
                relief="solid",
                padx=0,
                pady=0,
            )
            border.pack(fill="both", expand=True)

            body = tk.Frame(
                border,
                bg="white",
                padx=22,
                pady=12,
            )
            body.pack(fill="both", expand=True)

            text_label = tk.Label(
                body,
                text=message,
                font=("Segoe UI", 14, "bold"),
                bg="white",
                fg="black",
                justify="left",
            )
            text_label.pack()

            popup.update_idletasks()

            popup_w = popup.winfo_reqwidth()
            popup_h = popup.winfo_reqheight()

            try:
                sub_win = getattr(self.overlay, "sub_window", None)
                sub_win.update_idletasks()
                anchor_x = sub_win.winfo_rootx() + (sub_win.winfo_width() // 2)
                anchor_y = sub_win.winfo_rooty() + (sub_win.winfo_height() // 2)
            except Exception:
                anchor_x = root.winfo_rootx() + (root.winfo_width() // 2)
                anchor_y = root.winfo_rooty() + (root.winfo_height() // 2)

            monitors = get_monitor_rects(root)
            monitor = None
            for rect in monitors:
                mx, my, mw, mh = rect
                if mx <= anchor_x < mx + mw and my <= anchor_y < my + mh:
                    monitor = rect
                    break
            if monitor is None:
                monitor = min(
                    monitors,
                    key=lambda r: (anchor_x - (r[0] + r[2] / 2)) ** 2 + (anchor_y - (r[1] + r[3] / 2)) ** 2,
                )

            mx, my, mw, mh = monitor
            x = max(mx + (mw - popup_w) // 2, mx)
            y = max(my + (mh - popup_h) // 2, my)

            popup.geometry(f"{popup_w}x{popup_h}+{x}+{y}")
            show_window_no_activate(popup)

            if self._anki_success_popup_job is not None:
                try:
                    root.after_cancel(self._anki_success_popup_job)
                except Exception:
                    pass

            def close_popup():
                try:
                    if popup.winfo_exists():
                        popup.destroy()
                except Exception:
                    pass
                self._anki_success_popup = None
                self._anki_success_popup_job = None

            self._anki_success_popup_job = root.after(1000, close_popup)
