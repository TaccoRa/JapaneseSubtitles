"""Anki integration helper for note creation, wait dialogs, and success UI."""

import threading
import time
import tkinter as tk
from typing import Any
from utils import get_monitor_rects, make_nonactivating_tool_window, show_window_no_activate

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
                print("Add Selection To Anki: no text selected.")
                return

            subtitle = subtitle_text or ""
            if not self.anki.ping():
                print("AnkiConnect not reachable. Start Anki + AnkiConnect and confirm with 'Anki opened'.")
                self._show_anki_wait_dialog(selected_text=selected, subtitle_text=subtitle)
                return

            self._start_anki_add_worker(selected_text=selected, subtitle_text=subtitle)

    def _start_anki_add_worker(self, selected_text: str, subtitle_text: str = "") -> None:
            selected = (selected_text or "").strip()
            if not selected:
                print("Add Selection To Anki: no text selected.")
                return

            try:
                self._set_busy_cursor(True)
            except Exception:
                pass

            def worker():
                started = time.perf_counter()
                try:
                    if not self.anki.ping():
                        raise RuntimeError("AnkiConnect not reachable.")

                    result = self.anki.add_from_selection(
                        selection_text=selected,
                        subtitle_text=subtitle_text,
                    )

                    elapsed = time.perf_counter() - started
                    print(f"Anki note created in {elapsed:.2f}s")

                    candidates = result.get("translation_candidates") or {}
                    word_cands = candidates.get("word") or {}
                    sentence_cands = candidates.get("sentence") or {}

                    print(f"Note ID: {result.get('note_id', '')}")
                    print(f"Marked Word: {selected}")
                    print(f"Word Jisho: {self._format_translation_csv(word_cands.get('jisho', ''))}")
                    print(f"Word Google: {self._format_translation_csv(word_cands.get('google', ''))}")
                    print(f"Sentence DeepL: {self._format_translation_csv(sentence_cands.get('deepl', ''))}")
                    print(f"Sentence Google: {self._format_translation_csv(sentence_cands.get('google', ''))}")

                    fields = result.get("stroke_svg_sync_fields")
                    if fields:
                        self.anki.sync_missing_stroke_svgs_async(selected, fields)
                    try:
                        self.settings.root.after(0, self._schedule_ocr_sync_after_anki)
                    except Exception:
                        pass
                    try:
                        self.settings.root.after(0, self.popup.mark_anki_success)
                    except Exception:
                        pass
                    print("Anki card added.")
                except Exception as e:
                    print(f"Anki add failed: {e}")
                finally:
                    try:
                        self.settings.root.after(0, lambda: self._set_busy_cursor(False))
                    except Exception:
                        try:
                            self._set_busy_cursor(False)
                        except Exception:
                            pass

            threading.Thread(target=worker, daemon=True).start()

    def _show_anki_wait_dialog(self, selected_text: str, subtitle_text: str = "") -> None:
            self._pending_anki_payload = {
                "selected_text": selected_text,
                "subtitle_text": subtitle_text,
            }

            existing = getattr(self, "_anki_wait_window", None)
            if existing is not None:
                try:
                    if existing.winfo_exists():
                        existing.deiconify()
                        existing.lift()
                        existing.attributes("-topmost", True)
                        return
                except Exception:
                    pass

            parent = getattr(self.settings, "root", None)
            win = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
            self._anki_wait_window = win
            win.title("Anki Not Connected")
            win.attributes("-topmost", True)
            win.resizable(False, False)
            try:
                win.transient(parent)
            except Exception:
                pass
            try:
                win.grab_set()
            except Exception:
                pass

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
                    try:
                        status.set(text)
                    except Exception:
                        pass

            def _begin_wait_for_anki() -> None:
                if wait_state["running"]:
                    return
                wait_state["running"] = True
                try:
                    open_btn.configure(state="disabled")
                except Exception:
                    pass
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
                                try:
                                    if win_ref.winfo_exists():
                                        win_ref.destroy()
                                except Exception:
                                    pass
                                self._start_anki_add_worker(
                                    selected_text=payload.get("selected_text", ""),
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

            try:
                win.update_idletasks()
                if parent is not None:
                    px, py = parent.winfo_rootx(), parent.winfo_rooty()
                    pw, ph = parent.winfo_width(), parent.winfo_height()
                    ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
                    x = px + max((pw - ww) // 2, 0)
                    y = py + max((ph - wh) // 2, 0)
                    win.geometry(f"+{x}+{y}")
            except Exception:
                pass

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
                return bool(self.anki.ping())
            except Exception:
                return False

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
