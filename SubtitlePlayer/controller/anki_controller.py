"""Anki integration helper for note creation, wait dialogs, and success UI."""

import logging
import threading
import time
import tkinter as tk
from typing import Any
from utils import get_monitor_rects, make_nonactivating_tool_window, show_window_no_activate

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

    def _add_selection_to_anki(self, selected_text: str, subtitle_text: str = "") -> None:
            selected = (selected_text or "").strip()
            if not selected:
                logger.debug("Add Selection To Anki skipped because no text is selected")
                return

            subtitle = subtitle_text or ""
            if not self.anki.ping():
                logger.info("AnkiConnect not reachable; showing wait dialog")
                self._show_anki_wait_dialog(selected_text=selected, subtitle_text=subtitle)
                return

            self._start_anki_add_worker(selected_text=selected, subtitle_text=subtitle)

    def _start_anki_add_worker(self, selected_text: str, subtitle_text: str = "") -> None:
            selected = (selected_text or "").strip()
            if not selected:
                logger.debug("Add Selection To Anki skipped because no text is selected")
                return
            if bool(self.config.get("ANKI_PREVIEW_BEFORE_ADD") or False):
                self._start_anki_preview_worker(selected_text=selected, subtitle_text=subtitle_text)
                return
            self._start_anki_commit_worker(selected_text=selected, subtitle_text=subtitle_text)

    def _start_anki_preview_worker(self, selected_text: str, subtitle_text: str = "") -> None:
            selected = (selected_text or "").strip()
            if not selected:
                return
            self._set_busy_cursor(True)
            try:
                self.settings.root.after(0, self.popup.mark_anki_busy)
            except Exception:
                pass

            def worker():
                try:
                    if not self.anki.ping():
                        raise RuntimeError("AnkiConnect not reachable.")
                    prepared = self.anki.prepare_note_from_selection(
                        selection_text=selected,
                        subtitle_text=subtitle_text,
                    )
                    self.settings.root.after(
                        0,
                        lambda payload=prepared: self._show_anki_preview_window(payload, selected, subtitle_text),
                    )
                except Exception as e:
                    logger.exception("Anki preview preparation failed: %s", e)
                    try:
                        self.settings.root.after(0, self.popup.mark_anki_failure)
                    except Exception:
                        pass
                finally:
                    self.settings.root.after(0, lambda: self._set_busy_cursor(False))

            threading.Thread(target=worker, daemon=True).start()

    def _start_anki_commit_worker(self, selected_text: str, subtitle_text: str = "", prepared: dict | None = None) -> None:
            selected = (selected_text or "").strip()
            if not selected:
                logger.debug("Add Selection To Anki skipped because no text is selected")
                return
            self._set_busy_cursor(True)
            try:
                self.settings.root.after(0, self.popup.mark_anki_busy)
            except Exception:
                pass

            def worker():
                started = time.perf_counter()
                try:
                    if not self.anki.ping():
                        raise RuntimeError("AnkiConnect not reachable.")

                    if prepared is None:
                        result = self.anki.add_from_selection(
                            selection_text=selected,
                            subtitle_text=subtitle_text,
                        )
                    else:
                        result = self.anki.commit_prepared_note(prepared)
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
                        self.settings.root.after(
                            0,
                            lambda entries=annotation_entries: self._store_added_anki_annotation_entries(entries),
                        )
                    self.settings.root.after(0, self._schedule_ocr_sync_after_anki)
                    if result.get("routing_error"):
                        self.settings.root.after(0, self.popup.mark_anki_warning)
                    else:
                        self.settings.root.after(0, self.popup.mark_anki_success)
                        self._schedule_auto_jump_after_anki()
                except Exception as e:
                    logger.exception("Anki add failed: %s", e)
                    try:
                        self.settings.root.after(0, self.popup.mark_anki_failure)
                    except Exception:
                        pass
                finally:
                    self.settings.root.after(0, lambda: self._set_busy_cursor(False))

            threading.Thread(target=worker, daemon=True).start()

    def _show_anki_preview_window(self, prepared: dict, selected: str, subtitle_text: str = "") -> None:
            note = prepared.get("note") if isinstance(prepared, dict) else None
            if not isinstance(note, dict):
                logger.debug("Anki preview skipped because prepared note is invalid")
                return
            fields = note.get("fields")
            if not isinstance(fields, dict):
                fields = {}
                note["fields"] = fields

            win = tk.Toplevel(self.settings.root)
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
                getattr(self.anki, "front_field", "Front"),
                getattr(self.anki, "back_field", "Back"),
                getattr(self.anki, "sentence_ja_field", "SentenceJA"),
                getattr(self.anki, "sentence_de_field", "SentenceDE"),
                getattr(self.anki, "sound_field", "Sound"),
                getattr(self.anki, "image_field", "Image"),
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
                self._start_anki_commit_worker(selected_text=selected, subtitle_text=subtitle_text, prepared=prepared)

            def _cancel() -> None:
                try:
                    win.destroy()
                except Exception:
                    pass

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
                self.settings.root.after(0, _jump)
            except Exception:
                _jump()

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
                    self.settings.root.after(0, lambda: self._apply_ocr_sync_delta(median))
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
                self.settings.root.after(0, _show)
            except Exception:
                _show()

    def _show_anki_wait_dialog(self, selected_text: str = "", subtitle_text: str = "", on_ready=None) -> None:
            selected = (selected_text or "").strip()

            existing = getattr(self, "_anki_wait_window", None)
            if existing is not None:
                if existing.winfo_exists():
                    if selected:
                        self._pending_anki_payload = {
                            "selected_text": selected,
                            "subtitle_text": subtitle_text,
                        }
                    existing.deiconify()
                    existing.lift()
                    existing.attributes("-topmost", True)
                    return

            if selected:
                self._pending_anki_payload = {
                    "selected_text": selected,
                    "subtitle_text": subtitle_text,
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
                    "Please open Anki, then press the button below."
                ),
                justify="left",
                anchor="w",
            ).pack(fill="x")

            status_var = tk.StringVar(value="Waiting for confirmation...")
            self._anki_wait_status_var = status_var
            tk.Label(body, textvariable=status_var, anchor="w", fg="#1a4d1a").pack(fill="x", pady=(8, 0))

            btn_row = tk.Frame(body)
            btn_row.pack(fill="x", pady=(10, 0))
            wait_state = {"running": False}

            def _set_status(text: str) -> None:
                status = getattr(self, "_anki_wait_status_var", None)
                if status is not None:
                    status.set(text)

            def _begin_wait_for_anki() -> None:
                if wait_state["running"]:
                    return
                wait_state["running"] = True
                open_btn.configure(state="disabled")
                _set_status("Waiting for AnkiConnect...")

                def wait_worker():
                    while not self._shutting_down:
                        win_ref = getattr(self, "_anki_wait_window", None)
                        if win_ref is None:
                            return
                        try:
                            if not win_ref.winfo_exists():
                                return
                        except Exception:
                            return
                        if self.anki.ping():
                            payload = dict(self._pending_anki_payload or {})

                            def _finish():
                                if win_ref.winfo_exists():
                                    win_ref.destroy()
                                if callable(on_ready):
                                    on_ready()
                                    return
                                payload_selected = (payload.get("selected_text", "") or "").strip()
                                if payload_selected:
                                    self._start_anki_add_worker(
                                        selected_text=payload_selected,
                                        subtitle_text=payload.get("subtitle_text", ""),
                                    )

                            try:
                                self.settings.root.after(0, _finish)
                            except Exception:
                                _finish()
                            return
                        time.sleep(0.5)

                self._anki_wait_thread = threading.Thread(target=wait_worker, daemon=True)
                self._anki_wait_thread.start()

            open_btn = tk.Button(btn_row, text="Anki opened", width=14, command=_begin_wait_for_anki)
            open_btn.pack(side="left")
            tk.Button(btn_row, text="Cancel", width=10, command=win.destroy).pack(side="left", padx=(6, 0))

            def _on_destroy(_event):
                if _event.widget is not win:
                    return
                self._anki_wait_window = None
                self._anki_wait_status_var = None
                self._anki_wait_thread = None

            win.bind("<Destroy>", _on_destroy)
            win.bind("<Escape>", lambda _e: win.destroy())

            self._center_anki_wait_window(win, parent)

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
