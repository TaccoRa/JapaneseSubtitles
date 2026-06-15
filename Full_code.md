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
            self._set_busy_cursor(True)

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
                    self.settings.root.after(0, self._schedule_ocr_sync_after_anki)
                    self.settings.root.after(0, self.popup.mark_anki_success)
                    print("Anki card added.")
                except Exception as e:
                    print(f"Anki add failed: {e}")
                finally:
                    self.settings.root.after(0, lambda: self._set_busy_cursor(False))

            threading.Thread(target=worker, daemon=True).start()

    def _show_anki_wait_dialog(self, selected_text: str, subtitle_text: str = "") -> None:
            self._pending_anki_payload = {
                "selected_text": selected_text,
                "subtitle_text": subtitle_text,
            }

            existing = getattr(self, "_anki_wait_window", None)
            if existing is not None:
                if existing.winfo_exists():
                    existing.deiconify()
                    existing.lift()
                    existing.attributes("-topmost", True)
                    return

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
"""
Controller glue between:
- SubtitleManager (model)
- SettingsUI + SubtitleOverlayUI + CopyPopup (view)
- SubtitleRenderer (rendering)

Handles input (buttons, keyboard, global mouse), time updates, and episode changes.
"""
import time
import threading
import queue
import re
from pynput.mouse import Listener as MouseListener
from pynput.keyboard import Listener as KeyboardListener

from model.config_manager import ConfigManager
from model.anki_client import AnkiClient
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer

from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from view.popup import CopyPopup

from controller.playback_controller import PlaybackController
from controller.subtitle_navigation import SubtitleNavigationController
from controller.episode_controller import EpisodeController
from controller.overlay_controller import OverlayController
from controller.hotkey_controller import HotkeyController
from controller.anki_controller import AnkiController
from controller.ocr_controller import OCRController

class SubtitleController:
    SHORTCUT_DEFAULTS = {
        "SHORTCUT_TOGGLE_PLAY": "space",
        "SHORTCUT_GO_BACK": "left",
        "SHORTCUT_GO_FORWARD": "right",
        "SHORTCUT_SUBTITLE_BACK": "shift+left",
        "SHORTCUT_SUBTITLE_FORWARD": "shift+right",
        "SHORTCUT_MODE2_TOGGLE_PLAY": "numpad0",
        "SHORTCUT_MODE2_GO_BACK": "4",
        "SHORTCUT_MODE2_GO_FORWARD": "6",
        "SHORTCUT_MODE2_SUBTITLE_BACK": "alt+4",
        "SHORTCUT_MODE2_SUBTITLE_FORWARD": "alt+6",
        "SHORTCUT_BRING_TO_FRONT": "alt+x",
        "SHORTCUT_EPISODE_INC": "alt+c",
        "SHORTCUT_EPISODE_DEC": "alt+y",
        "SHORTCUT_JUMP_SUB_END": "ctrl+shift+y",
    }
    HOTKEY_DISABLE_KEYS = {
        "toggle_play": "DISABLE_HOTKEY_TOGGLE_PLAY",
        "go_back": "DISABLE_HOTKEY_GO_BACK",
        "go_forward": "DISABLE_HOTKEY_GO_FORWARD",
        "subtitle_back": "DISABLE_HOTKEY_SUBTITLE_BACK",
        "subtitle_forward": "DISABLE_HOTKEY_SUBTITLE_FORWARD",
        "jump_sub_end": "DISABLE_HOTKEY_JUMP_SUB_END",
    }
    OCR_TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)[/\\|](\d{1,2}:\d{2}(?::\d{2})?)")
    
    def __init__(self,
                config: ConfigManager,
                manager: SubtitleManager,
                renderer: SubtitleRenderer,
                settings_ui: SettingsUI,
                overlay_ui: SubtitleOverlayUI,
                popup: CopyPopup,
                total_duration: float = 0.0):
        
        self.config  = config
        self.sub_manager = manager
        self.renderer = renderer
        self.settings = settings_ui
        self.overlay = overlay_ui
        self.popup   = popup
        self.total_duration = total_duration

        self.playback = PlaybackController(self)
        self.subtitle_navigation = SubtitleNavigationController(self)
        self.episode_controller = EpisodeController(self)
        self.overlay_controller = OverlayController(self)
        self.hotkey_controller = HotkeyController(self)
        self.anki_controller = AnkiController(self)
        self.ocr_controller = OCRController(self)
        self.anki = AnkiClient(self.config)

        self.default_start_time = self.current_time = self.config.get("DEFAULT_START_TIME")
        self._startup_resume_play = self.config.get("STARTUP_RESUME_PLAY") #bool
        self.default_skip = self.config.get("DEFAULT_SKIP")
        self.default_offset = self.config.get("EXTRA_OFFSET")
        self.audio_padding = self.config.get("AUDIO_PADDING")
        self.phone_windows_hide_control_ms = self.config.get("PHONEMODE_WINDOWS_HIDE_DELAY_MS")   # hides control window after # ms in phone mode
        self.windows_hide_control_ms = self.config.get("WINDOWS_HIDE_DELAY_MS")   # hides control window after # ms in phone mode
        self.hide_subtitles_ms = self.config.get("SUBTITLE_TIMEOUT_MS")                     # clears subtitle canvas after # ms
        self.update_interval_ms = self.config.get("UPDATE_INTERVAL_MS")                        # updates the time display every # ms
        self.video_click = self.config.get("VIDEO_CLICK")
        self.anki_busy_cursor = (self.config.get("ANKI_BUSY_CURSOR") or "wait")

        self.playing      = False
        self.entry_editing  = False
        self.subtitle_deleted = False
        self.alt_pressed = False
        self.ctrl_pressed = False
        self.shift_pressed = False
        self.sub_hidden = False
        self.slider_dragging = False
        self._shutting_down = False
        self._single_fire_actions = set()
        self.subtitle_timeout_job = None
        self.last_subtitle_text = ""
        self.last_subtitle_raw = ""
        self.last_rendered_index = None
        self.last_rendered_sub_time = None



        self._single_fire_actions = set()
        self._input_actions: "queue.Queue[str]" = queue.Queue()
        self._input_pump_job = None
        self._repeat_job = None
        self._repeat_lock = threading.Lock()
        self._held_repeat_next_fire: dict[str, float] = {}
        self._held_repeat_fired: set[str] = set()
        self._repeat_initial_delay_sec = 0.22
        self._repeat_interval_sec = 0.04
        self._pending_seek_delta = 0.0
        self._ocr_job = None
        self._ocr_thread = None
        self._ocr_generation = 0
        self._ocr_pending_time = None
        self._ocr_sync_generation = 0
        self._ocr_sync_thread = None
        self._anki_wait_window = None
        self._anki_wait_status_var = None
        self._anki_wait_thread = None
        self._pending_anki_payload = None
        self._anki_success_popup = None
        self._anki_success_popup_job = None
        self.subtitle_timeout_job = None
        self._input_pump_job = None
        self._repeat_job = None
        self._ocr_job = None

        self.episode_controller.restore_startup_time_and_mode()

        self.settings.bind_back(self.playback.go_back)
        self.settings.bind_forward(self.playback.go_forward)
        self.settings.bind_play_pause(self.playback.toggle_play)
        self.settings.bind_slider(
            on_chg    = self.on_slider_change,
            on_pr     = self.on_slider_press,
            on_rl     = self.on_slider_release
        )
        self.settings.bind_episode_change(
            on_ent    = lambda: self.change_episode('set'),
            on_inc    = lambda: self.change_episode('inc'),
            on_dec    = lambda: self.change_episode('dec')
        )
        self.settings.bind_open_srt                  (self.episode_controller.on_open_srt)
        self.settings.bind_set_to_return             (self.on_set_to_return)
        self.settings.bind_time_entry_return         (self.control_time_entry_return)
        self.settings.bind_time_entry_clear          (self.control_clear_time_entry)
        self.settings.bind_control_window_enter      (self.control_window_enter)
        self.settings.bind_control_window_leave      (self.control_window_leave)
        self.settings.bind_show_subtitle_handle      (self.show_subtitle_handle)
        self.settings.bind_refresh_subtitles         (self.on_refresh_subtitles)
        self.settings.bind_advanced_apply            (self.apply_advanced_settings)
        self.settings.bind_ocr_read_now              (self.on_ocr_read_now)
        self.settings.bind_ocr_sync_now              (self.on_ocr_sync_now)
        self.settings.bind_anki_check                (self.on_anki_check_connection)
        self.settings.bind_settings_open             (self._hide_subtitle_handle_for_settings)

        self.settings.bind_update_display            (self.update_time_and_subtitle_displays)
        
        self.overlay.subtitle_canvas.bind("<Button-3>", self._on_copy_popup)
        self.popup.bind_add_to_anki(self._add_selection_to_anki)
        self.overlay.bind_sub_window_enter(self.sub_window_enter)
        self.overlay.bind_sub_window_leave(self.sub_window_leave)
        self.overlay.bind_sub_handel_enter(self.sub_handel_enter)   
        self.settings.root.bind("<Enter>", lambda _e: self._hide_subtitle_handle_for_settings(), add="+")
        self.settings.root.bind("<Leave>", lambda _e: self._restore_subtitle_handle_after_settings(), add="+")

        self._mouse_listener = MouseListener(on_click=self.hotkey_controller._on_global_click)
        self._mouse_listener.start()
        self._keyboard_listener = KeyboardListener(on_press=self._on_key_press, on_release=self._on_key_release)
        self._keyboard_listener.start()
        self._input_pump_job = self.settings.root.after(15, self._process_input_queue)
        self._repeat_job = self.settings.root.after(16, self._process_repeat_actions)

        self.last_update  = time.time()
        self.playback.set_current_time(float(self.current_time or 0.0))
        self.update_episode_nav_controls()
        self._schedule_ocr_time_jump("startup")
        if self._startup_resume_play: self.playback.toggle_play()
        self.settings.root.protocol("WM_DELETE_WINDOW", self._on_app_close)

    def _process_repeat_actions(self):
        return self.hotkey_controller._process_repeat_actions()

    def _process_input_queue(self):
        return self.hotkey_controller._process_input_queue()
    
    def _schedule_ocr_time_jump(self, reason: str) -> None:
        return self.ocr_controller._schedule_ocr_time_jump(reason)

    def get_offset_value(self) -> float:
        return self.subtitle_navigation.get_offset_value()

    def update_episode_nav_controls(self) -> None:
        return self.episode_controller.update_episode_nav_controls()

    def _get_display_start_times(self):
        return self.episode_controller._get_display_start_times()

    def _on_copy_popup(self, event=None):
        print(self.last_subtitle_raw)
        self.popup.open_copy_popup(self.last_subtitle_raw)
        return "break"
    
    def _skip_buttons_use_subtitle_segments(self) -> bool:
        return self.hotkey_controller._skip_buttons_use_subtitle_segments()
        
    def _add_selection_to_anki(self, selected_text: str, subtitle_text: str='') -> None:
        return self.anki_controller._add_selection_to_anki(selected_text, subtitle_text)

    def _set_busy_cursor(self, busy: bool) -> None:
        return self.anki_controller._set_busy_cursor(busy)

    def on_anki_check_connection(self) -> bool:
        return self.anki_controller.on_anki_check_connection()

    def apply_advanced_settings(self, values: dict) -> None:
        if not isinstance(values, dict):
            return
        try:
            cfg = getattr(self.config, "config", None)
            if isinstance(cfg, dict):
                cfg.update(values)
        except Exception:
            pass

        def _as_int(key: str, default: int) -> int:
            try:
                return int(values.get(key, default))
            except Exception:
                return int(default)

        self.update_interval_ms = max(15, _as_int("UPDATE_INTERVAL_MS", self.update_interval_ms))
        self.hide_subtitles_ms = max(100, _as_int("SUBTITLE_TIMEOUT_MS", self.hide_subtitles_ms))
        self.windows_hide_control_ms = max(100, _as_int("WINDOWS_HIDE_DELAY_MS", self.windows_hide_control_ms))
        self.phone_windows_hide_control_ms = max(100, _as_int("PHONEMODE_WINDOWS_HIDE_DELAY_MS", self.phone_windows_hide_control_ms))

        if "VIDEO_CLICK" in values:
            try:
                self.video_click = bool(values.get("VIDEO_CLICK"))
            except Exception:
                pass

        if "AUDIO_PADDING" in values:
            try:
                self.audio_padding = float(values.get("AUDIO_PADDING"))
            except Exception:
                self.audio_padding = 0.1

        if "POPUP_CLOSE_TIMER" in values:
            try:
                self.popup.close_delay = max(100, int(values.get("POPUP_CLOSE_TIMER")))
                popup = getattr(self.popup, "_popup", None)
                if popup is not None and popup.winfo_exists():
                    if (not getattr(self.popup, "_pinned", False)
                            and not getattr(self.popup, "_menu_open", False)
                            and not getattr(self.popup, "_dragging", False)):
                        self.popup._restart_close()
            except Exception:
                pass

        # Popup style fields apply immediately for newly opened popups, and update the
        # currently open popup widget where possible.
        try:
            if "POPUP_FONT" in values:
                self.popup.font_name = str(values.get("POPUP_FONT") or self.popup.font_name)
            if "POPUP_FONT_COLOR" in values:
                self.popup.font_color = str(values.get("POPUP_FONT_COLOR") or self.popup.font_color)
            if "POPUP_BG_COLOR" in values:
                self.popup.bg_color = str(values.get("POPUP_BG_COLOR") or self.popup.bg_color)
            if "POPUP_FONT_SIZE" in values:
                self.popup.font_size = max(8, int(values.get("POPUP_FONT_SIZE")))

            popup_win = getattr(self.popup, "_popup", None)
            entry = getattr(self.popup, "_entry_widget", None)
            if popup_win is not None and popup_win.winfo_exists():
                try:
                    popup_win.configure(bg=self.popup.bg_color)
                except Exception:
                    pass
            if entry is not None:
                try:
                    entry.configure(
                        bg=self.popup.bg_color,
                        fg=self.popup.font_color,
                        font=(self.popup.font_name, self.popup.font_size, "bold"),
                    )
                except Exception:
                    pass
        except Exception:
            pass

        # Subtitle style fields are read by renderer on each draw; trigger a refresh now.
        subtitle_style_keys = {
            "SUBTITLE_FONT",
            "SUBTITLE_FONT_SIZE",
            "SUBTITLE_COLOR",
            "SUBTITLE_WRAP_LIMIT_PX",
            "SUBTITLE_HOVER_RUBY",
            "GLOW_COLOR",
            "GLOW_RADIUS",
        }
        if any(k in values for k in subtitle_style_keys):
            try:
                self.last_subtitle_text = ""
                self.update_time_and_subtitle_displays()
            except Exception:
                pass

        subtitle_cleaning_keys = {
            "SUBTITLE_AUTO_RUBY",
            "SUBTITLE_CUSTOM_HTML_TAGS",
            "SUBTITLE_SPEAKER_MODE",
            "SUBTITLE_KEEP_SPEAKER_NAMES",
            "SUBTITLE_SPEAKER_TEMPLATE",
            "SUBTITLE_STRIP_PAREN_NOTES",
        }
        if any(k in values for k in subtitle_cleaning_keys):
            try:
                srt_path = getattr(self.sub_manager, "srt_file", None)
                if srt_path:
                    self.sub_manager.set_subtitle_display_data(srt_path)
                    self.last_subtitle_text = ""
                    self.update_time_and_subtitle_displays()
            except Exception:
                pass

        startup_value_keys = {"DEFAULT_START_TIME", "EXTRA_OFFSET", "DEFAULT_SKIP"}
        if any(k in values for k in startup_value_keys):
            try:
                self.default_start_time = float(values.get("DEFAULT_START_TIME", self.default_start_time))
            except Exception:
                pass
            try:
                if "EXTRA_OFFSET" in values:
                    off = float(values.get("EXTRA_OFFSET"))
                    old_off = float(getattr(self.settings, "_last_offset_value", self.default_offset) or 0.0)
                    self.default_offset = off
                    self.settings._last_offset_value = off
                    self.settings.offset_var.set(f"{self.settings._format_number(off)} s")
                    self.settings._apply_offset_change(off, persist=False, previous_value=old_off)
            except Exception:
                pass
            try:
                if "DEFAULT_SKIP" in values:
                    skip = float(values.get("DEFAULT_SKIP"))
                    self.default_skip = skip
                    self.settings._last_skip_value = skip
                    self.settings.skip_var.set(f"{self.settings._format_number(skip)} s")
                    self.settings._apply_skip_change(skip, persist=False)
            except Exception:
                pass
            try:
                self.settings._sync_advanced_startup_vars_from_runtime()
            except Exception:
                pass

        if "SHORTCUTS_DISABLED" in values:
            try:
                self.settings.set_hotkeys_disabled(bool(values.get("SHORTCUTS_DISABLED")))
            except Exception:
                pass
            if self._hotkeys_disabled():
                self._reset_hotkey_state()
        if (
            "SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS" in values
            or any(str(k).startswith("DISABLE_HOTKEY_") for k in values.keys())
        ):
            self._reset_hotkey_state()

        if any(str(k).startswith("ANKI_") for k in values.keys()):
            try:
                self.anki = AnkiClient(self.config)
                self.anki_busy_cursor = (self.anki_busy_cursor or "wait")
            except Exception:
                pass


    # ——— Time handling ———————————————————————————————————
    def update_loop(self):
        return self.playback.update_loop()

    def schedule_update(self):
        return self.playback.schedule_update()

    def set_current_time(self, t: float):
        return self.playback.set_current_time(t)

    def on_set_to_return(self, text: str):
        return self.subtitle_navigation.on_set_to_return(text)

    def _release_time_entry_focus(self) -> None:
        return self.subtitle_navigation._release_time_entry_focus()

    def control_time_entry_return(self, event):
        return self.subtitle_navigation.control_time_entry_return(event)

    def control_clear_time_entry(self, event):
        return self.subtitle_navigation.control_clear_time_entry(event)




    # ——— Updating Logic —————————————————————————————————————

    def update_time_and_subtitle_displays(self):
        return self.subtitle_navigation.update_time_and_subtitle_displays()

    def on_refresh_subtitles(self, event):
        return self.subtitle_navigation.on_refresh_subtitles(event)


    # ——— Change srt file ———————————————————————————————————
    def on_open_srt(self, event=None):
        return self.episode_controller.on_open_srt(event)

    def change_episode(self, action: str):
        return self.episode_controller.change_episode(action)

    def _after_episode_change(self):
        return self.episode_controller._after_episode_change()
        
    def update_max_width(self) -> None:
        return self.episode_controller.update_max_width()

    # ——— Playback controls ———————————————————————————————————
    def toggle_play(self):
        return self.playback.toggle_play()

    def go_forward(self):
        return self.playback.go_forward()

    def go_back(self):
        return self.playback.go_back()

    def jump_subtitle_segment(self, direction: str) -> None:
        return self.playback.jump_subtitle_segment(direction)

    def on_jump_sub_end(self, event=None):
        return self.playback.on_jump_sub_end(event)

    def _schedule_hide_controls(self):
        return self.overlay_controller._schedule_hide_controls()

    def _hide_controls_after(self, ms: int):
        return self.overlay_controller._hide_controls_after(ms)

    def on_slider_press(self, event):
        return self.subtitle_navigation.on_slider_press(event)
    
    def on_slider_change(self, value):
        return self.subtitle_navigation.on_slider_change(value)

    
    def on_slider_release(self, event):
        return self.subtitle_navigation.on_slider_release(event)

    def _on_key_press(self, key):
        return self.hotkey_controller._on_key_press(key)

    def _on_key_release(self, key):
        return self.hotkey_controller._on_key_release(key)

    def on_alt_x(self, event=None):
        return self.hotkey_controller.on_alt_x(event)


    # ——— Hide window logic —————————————————————————————————————

    def sub_window_enter(self, event):
        return self.overlay_controller.sub_window_enter(event)

    def sub_window_leave(self, event):
        return self.overlay_controller.sub_window_leave(event)

    def sub_handel_enter(self, event):
        return self.overlay_controller.sub_handel_enter(event)

    def control_window_enter(self, event):
        return self.overlay_controller.control_window_enter(event)

    def control_window_leave(self, event):
        return self.overlay_controller.control_window_leave(event)

    def show_subtitle_handle(self, is_phone):
        return self.overlay_controller.show_subtitle_handle(is_phone)

    def _hide_subtitle_handle_for_settings(self):
        return self.overlay_controller._hide_subtitle_handle_for_settings()

    def _restore_subtitle_handle_after_settings(self):
        return self.overlay_controller._restore_subtitle_handle_after_settings()

    def on_ocr_read_now(self, override: dict | None=None) -> None:
        return self.ocr_controller.on_ocr_read_now(override)
    
    def on_ocr_sync_now(self, override: dict | None=None) -> None:
        return self.ocr_controller.on_ocr_sync_now(override)

    def _on_app_close(self):
        self._shutting_down = True
        def _read_settings_geometry():
            try:
                geo = self.settings.root.winfo_geometry()
                size, pos = geo.split("+", 1)
                w_s, h_s = size.split("x", 1)
                x_s, y_s = pos.split("+", 1)
                return int(x_s), int(y_s), int(w_s), int(h_s)
            except Exception:
                try:
                    return (
                        int(self.settings.root.winfo_x()),
                        int(self.settings.root.winfo_y()),
                        int(self.settings.root.winfo_width()),
                        int(self.settings.root.winfo_height()),
                    )
                except Exception:
                    return None
        # Persist window positions/state before destroying any windows.
        try:
            geom = _read_settings_geometry()
            if geom is None:
                raise ValueError("Could not read settings geometry")
            x, y, w, h = geom
            if (x, y) != (self.config.get("LAST_SETTINGS_WINDOW_X"),
                          self.config.get("LAST_SETTINGS_WINDOW_Y")):
                self.config.set("LAST_SETTINGS_WINDOW_X", x)
                self.config.set("LAST_SETTINGS_WINDOW_Y", y)
            if (w, h) != (self.config.get("LAST_SETTINGS_WINDOW_WIDTH"),
                          self.config.get("LAST_SETTINGS_WINDOW_HEIGHT")):
                self.config.set("LAST_SETTINGS_WINDOW_WIDTH", w)
                self.config.set("LAST_SETTINGS_WINDOW_HEIGHT", h)
        except Exception:
            pass
        try:
            session_anime = self.sub_manager.get_anime_name()
            updates = {
                "LAST_ANIME_NAME": str(session_anime or ""),
                "LAST_SESSION_TIME_SEC": float(self.current_time or 0.0),
                "LAST_SESSION_PLAY_MODE": bool(self.playing),
            }
            if hasattr(self.config, "set_many"):
                self.config.set_many(updates)
            else:
                for key, value in updates.items():
                    self.config.set(key, value)
        except Exception:
            pass
        try:
            self.settings.save_state()  # control window position
        except Exception:
            pass
        try:
            self.overlay.save_state()   # subtitle overlay center position
        except Exception:
            pass
        try:
            self.sub_manager.save_state()
        except Exception:
            pass

        for job in ("subtitle_timeout_job", "_con_hide_job", "_input_pump_job", "_repeat_job", "_ocr_job"):
            handle = getattr(self, job, None)
            if handle is not None:
                try:
                    self.settings.root.after_cancel(handle)
                except Exception:
                    pass
        for listener_attr in ("_mouse_listener", "_keyboard_listener"):
            listener = getattr(self, listener_attr, None)
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        try:
            self.popup._cancel_close()
        except AttributeError:
            pass
        for w in (self.settings.control_window, self.overlay.sub_window, self.popup._popup):
            if w:
                try: w.destroy()
                except: pass

        self.settings.root.destroy()
    














# #wrappers for benchmark
    def _segments_to_copy_text(self, top_segments, bottom_segments):
        return self.subtitle_navigation.segments_to_copy_text(top_segments, bottom_segments)

    def _update_subtitle_display(self, force: bool = False):
        return self.subtitle_navigation._update_subtitle_display(force)
    

# from video_sync_server import get_video_time

        #   self.settings.root.after(self.video_sync_interval_ms, self._sync_loop)
        #   self.video_sync_interval_ms = self.config.get("VIDEO_SYNC_INTERVAL_MS") or 500
        #   self.video_sync_threshold = self.config.get("VIDEO_SYNC_THRESHOLD") or 0.5

    # def sync_with_video(self):
    #     video_time, video_duration = get_video_time()
    #     if video_duration <= 0:
    #         return
    #     drift = video_time - self.current_time
    #     if abs(drift) > self.video_sync_threshold:
    #         print(f"[SYNC] correcting drift: {drift:.2f}s → {video_time:.2f}")
    #         self.playback.set_current_time(video_time)
    # def _sync_loop(self):
    #     if not self._shutting_down:
    #         try:
    #             self.sync_with_video()
    #         except Exception:
    #             pass
    #         self.settings.root.after(self.video_sync_interval_ms, self._sync_loop)
"""Episode / subtitle-file switching and geometry refresh helper."""

import bisect
import re
from typing import Any

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

class EpisodeController(_ControllerProxy):
    """Episode / subtitle-file switching and geometry refresh helper."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def update_episode_nav_controls(self) -> None:
            """
            Grey out + / - when we know from the episode maps/index that no prev/next exists.
            """
            try:
                can_dec, can_inc, is_movie = self.sub_manager.get_episode_nav_state()
            except Exception:
                # Unknown -> keep enabled, don't break the UI.
                can_dec, can_inc = True, True
                is_movie = (self.sub_manager.current_episode is None)
            try:
                self.settings.set_episode_nav_state(can_dec=can_dec, can_inc=can_inc, is_movie=is_movie)
            except Exception:
                pass

            # Update dropdown values for the episode entry (combobox).
            try:
                values = self.sub_manager.get_episode_dropdown_values()
                self.settings.set_episode_values(values)
            except Exception:
                pass

    @staticmethod
    def _normalize_anime_key(value) -> str:
            try:
                return re.sub(r"\s+", " ", str(value or "").strip()).casefold()
            except Exception:
                return ""

    def restore_startup_time_and_mode(self) -> None:
            self._startup_resume_play = False
            try:
                current_anime = self._normalize_anime_key(self.sub_manager.get_anime_name())
                last_anime = self._normalize_anime_key(self.config.get("LAST_ANIME_NAME"))
                if not current_anime or current_anime != last_anime:
                    self.current_time = float(self.default_start_time or 0.0)
                    return
                saved_time = self.config.get("LAST_SESSION_TIME_SEC")
                if saved_time is not None:
                    try:
                        self.current_time = max(0.0, float(saved_time))
                    except Exception:
                        self.current_time = float(self.default_start_time or 0.0)
                else:
                    self.current_time = float(self.default_start_time or 0.0)

                play_mode = self.config.get("LAST_SESSION_PLAY_MODE")
                if play_mode is None:
                    play_mode = self.config.get("LAST_SESSION_PLAYING")
                self._startup_resume_play = bool(play_mode)
            except Exception:
                self.current_time = float(self.default_start_time or 0.0)
                self._startup_resume_play = False

    def _get_display_start_times(self):
            start_times = getattr(self.sub_manager, "display_start_times", None)
            if isinstance(start_times, list) and start_times:
                return start_times
            return [item[1] for item in getattr(self.sub_manager, "display_data", [])]

    def on_open_srt(self, event=None):
            path = self.sub_manager.set_new_file()
            if path:
                self._after_episode_change()

    def change_episode(self, action: str):
            def _restore_entry():
                if self.sub_manager.current_episode is None:
                    self.settings.episode_var.set("Movie")
                else:
                    self.settings.episode_var.set(str(self.sub_manager.current_episode))

            raw = self.settings.episode_var.get().strip()
            if action in ("inc", "dec"):
                target_season, target_episode = self.sub_manager.change_episode(action)
                if target_episode is not None:
                    self.settings.episode_var.set(str(target_episode))
                    self._after_episode_change()
                else:
                    _restore_entry()
                return

            if not raw:
                _restore_entry()
                return
            if raw.lower() == 'movie':
                return

            raw_int = None
            season_hint = None
            parsed_global = None
            try:
                candidate = int(raw)
                if candidate > 0:
                    raw_int = candidate
            except ValueError:
                try:
                    parsed_s, parsed_e, parsed_g = self.sub_manager.extract_season_episode_global(raw)
                except Exception:
                    parsed_s, parsed_e, parsed_g = None, None, None
                if parsed_s is not None and parsed_e is not None:
                    season_hint = int(parsed_s)
                    raw_int = int(parsed_e)
                    parsed_global = int(parsed_g) if parsed_g is not None else None
                elif parsed_g is not None:
                    raw_int = int(parsed_g)

            if raw_int is None or raw_int <= 0:
                _restore_entry()
                return

            before = (
                getattr(self.sub_manager, "srt_file", None),
                getattr(self.sub_manager, "current_season", None),
                getattr(self.sub_manager, "current_episode", None),
            )
            target_season, target_episode = self.sub_manager.change_episode("set", raw_int, season_hint)
            after = (
                getattr(self.sub_manager, "srt_file", None),
                getattr(self.sub_manager, "current_season", None),
                getattr(self.sub_manager, "current_episode", None),
            )

            # Fallback: if explicit SxxEyy did not resolve, and parser also gave a global
            # candidate, try the global target once.
            if before == after and season_hint is not None and parsed_global is not None and parsed_global > 0:
                target_season, target_episode = self.sub_manager.change_episode("set", parsed_global, None)

            if target_episode is not None:
                self.settings.episode_var.set(str(target_episode))
                self._after_episode_change() #reset all with new srt data
            else: #change not allowed
                _restore_entry()

    def _after_episode_change(self):
            if self.sub_manager.current_episode is None:
                self.settings.episode_var.set("Movie")
            else: 
                self.settings.episode_var.set(str(self.sub_manager.current_episode))
            self.update_episode_nav_controls()

            new_total = self.sub_manager.get_total_duration() ##maybe not needed anymore
            self.settings.set_total_duration(new_total)
            self.total_duration = new_total
            self.update_max_width()
            title= f'S{self.sub_manager.get_current_season()}E{self.sub_manager.get_current_episode()} {self.sub_manager.get_anime_name()}'
            self.settings.root.title(title)
            self.current_time = self.default_start_time
            self.playback.set_current_time(self.current_time)
            self._schedule_ocr_time_jump("episode_change")

    def update_max_width(self) -> None:
            # Recompute content width + padding
            max_w, max_h = self.sub_manager.get_subtitle_geometry()
            max_w, max_h = int(max_w), int(max_h)

            # Apply geometry on overlay and update renderer's canvas ref
            self.overlay.update_geometry(max_w, max_h)
            self.renderer.update_canvas(self.overlay.subtitle_canvas)

            # ensure layout finalized so canvas.winfo_width() matches what renderer expects
            self.overlay.subtitle_canvas.update_idletasks()

            # Immediately re-render the subtitle at current_time (same logic as _update_subtitle_display)
            offset = self.settings._last_offset_value
            sub_t = self.current_time - offset
            start_times = self._get_display_start_times()
            idx = bisect.bisect_right(start_times, sub_t) - 1
            if idx < 0:
                # nothing to draw
                self.renderer.canvas.delete("all")
                return

            try:
                self.sub_manager.ensure_auto_ruby_for_index(idx)
            except Exception:
                pass
            _, _, top_segments, bottom_segments = self.sub_manager.display_data[idx]
            # render freshly using updated overlay/canvas
            try:
                self.renderer.canvas.delete("all")
                self.renderer.render_subtitle(top_segments, bottom_segments, self.overlay)
            except Exception:
                # keep app alive if rendering fails; log if you have logger
                pass
"""Keyboard shortcut, repeat-action, and global hotkey helper."""

import time
import queue
from pynput.mouse import Button
from typing import Any
from pynput.keyboard import Key

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

class HotkeyController(_ControllerProxy):
    """Keyboard shortcut, repeat-action, and global hotkey helper."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def _enqueue_input_action(self, action: str) -> None:
            if self._shutting_down:
                return
            try:
                self._input_actions.put_nowait(action)
            except Exception:
                pass

    def _drop_pending_input_actions(self, actions_to_remove: set[str]) -> None:
            if not actions_to_remove:
                return
            kept = []
            while True:
                try:
                    action = self._input_actions.get_nowait()
                except queue.Empty:
                    break
                except Exception:
                    break
                if action not in actions_to_remove:
                    kept.append(action)
            for action in kept:
                try:
                    self._input_actions.put_nowait(action)
                except Exception:
                    break

    def _is_seek_repeat_action(self, action: str) -> bool:
            return (action in {"go_back", "go_forward"}) and (not self._skip_buttons_use_subtitle_segments())
    
    @staticmethod
    def _seek_step_action_name(action: str) -> str | None:
            if action == "go_back":
                return "_seek_step_back"
            if action == "go_forward":
                return "_seek_step_forward"
            return None

    def _queue_names_for_repeat_action(self, action: str, settle_seek: bool = True) -> set[str]:
            if self._is_seek_repeat_action(action):
                step_name = self._seek_step_action_name(action)
                return {step_name} if step_name else set()
            return {action}

    def _queue_repeat_step(self, action: str) -> None:
            step_name = self._seek_step_action_name(action)
            if step_name:
                self._enqueue_input_action(step_name)
            else:
                self._enqueue_input_action(action)

    def _seek_delta_for_action(self, action: str) -> float:
            if not self._is_seek_repeat_action(action):
                return 0.0
            try:
                skip = float(self.settings._last_skip_value or 0.0)
            except Exception:
                skip = 0.0
            if action == "go_back":
                return -skip
            if action == "go_forward":
                return skip
            return 0.0

    def _accumulate_pending_seek(self, action: str) -> None:
            delta = self._seek_delta_for_action(action)
            if abs(delta) < 0.000001:
                return
            try:
                self._pending_seek_delta = float(self._pending_seek_delta) + delta
            except Exception:
                self._pending_seek_delta = delta
            self.update_time_and_subtitle_displays()

    def _clear_pending_seek_preview(self) -> None:
            if abs(float(getattr(self, "_pending_seek_delta", 0.0) or 0.0)) < 0.000001:
                return
            self._pending_seek_delta = 0.0
            self.update_time_and_subtitle_displays()

    def _apply_pending_seek(self) -> None:
            try:
                delta = float(self._pending_seek_delta or 0.0)
            except Exception:
                delta = 0.0
            if abs(delta) < 0.000001:
                return
            self._pending_seek_delta = 0.0
            self.playback.set_current_time(self.current_time + delta)
            self._schedule_hide_controls()

    def _hold_repeat_action(self, action: str) -> bool:
            now = time.perf_counter()
            with self._repeat_lock:
                if action in self._held_repeat_next_fire:
                    return False
                self._held_repeat_next_fire[action] = now + self._repeat_initial_delay_sec
                self._held_repeat_fired.discard(action)
            return True

    def _release_repeat_actions(self, actions: set[str], settle_seek: bool = True) -> None:
            if not actions:
                return
            released_seek_actions = set()
            released_seek_fired = {}
            with self._repeat_lock:
                for action in actions:
                    if self._is_seek_repeat_action(action):
                        released_seek_actions.add(action)
                        released_seek_fired[action] = (action in self._held_repeat_fired)
                    self._held_repeat_next_fire.pop(action, None)
                    self._held_repeat_fired.discard(action)
                still_held = set(self._held_repeat_next_fire.keys())

            queue_names = set()
            for action in actions:
                queue_names.update(self._queue_names_for_repeat_action(action, settle_seek=settle_seek))
            self._drop_pending_input_actions(queue_names)

            if released_seek_actions:
                seek_still_held = bool(still_held & {"go_back", "go_forward"})
                if not seek_still_held:
                    try:
                        pending = float(getattr(self, "_pending_seek_delta", 0.0) or 0.0)
                    except Exception:
                        pending = 0.0
                    if settle_seek:
                        if abs(pending) >= 0.000001:
                            self._enqueue_input_action("apply_pending_seek")
                        else:
                            unresolved = [a for a in released_seek_actions if not released_seek_fired.get(a, False)]
                            if "go_forward" in unresolved and "go_back" not in unresolved:
                                self._enqueue_input_action("go_forward")
                            elif "go_back" in unresolved and "go_forward" not in unresolved:
                                self._enqueue_input_action("go_back")
                    else:
                        if abs(pending) >= 0.000001:
                            self._enqueue_input_action("clear_pending_seek")

    def _process_repeat_actions(self):
            if self._shutting_down:
                return
            now = time.perf_counter()
            due_actions = []
            with self._repeat_lock:
                for action, next_fire in list(self._held_repeat_next_fire.items()):
                    if now >= next_fire:
                        due_actions.append(action)
                        self._held_repeat_next_fire[action] = now + self._repeat_interval_sec

            for action in due_actions:
                with self._repeat_lock:
                    if action not in self._held_repeat_next_fire:
                        continue
                    self._held_repeat_fired.add(action)
                self._queue_repeat_step(action)

            try:
                self._repeat_job = self.settings.root.after(16, self._process_repeat_actions)
            except Exception:
                self._repeat_job = None

    def _process_input_queue(self):
            if self._shutting_down:
                return
            try:
                for _ in range(50):
                    try:
                        action = self._input_actions.get_nowait()
                    except queue.Empty:
                        break
                    self._dispatch_input_action(action)
            finally:
                try:
                    self._input_pump_job = self.settings.root.after(15, self._process_input_queue)
                except Exception:
                    self._input_pump_job = None

    def _dispatch_input_action(self, action: str) -> None:
            if action == "toggle_play":
                self.playback.toggle_play()
            elif action == "go_back":
                self.playback.go_back()
            elif action == "go_forward":
                self.playback.go_forward()
            elif action == "subtitle_back":
                self.playback.jump_subtitle_segment("prev")
            elif action == "subtitle_forward":
                self.playback.jump_subtitle_segment("next")
            elif action == "jump_sub_end":
                self.playback.on_jump_sub_end()
            elif action == "alt_x":
                self.on_alt_x()
            elif action == "episode_inc":
                self.change_episode("inc")
            elif action == "episode_dec":
                self.change_episode("dec")
            elif action == "clear_subtitle":
                self.renderer.canvas.delete("all")
                # Keep last_subtitle_text intact so _update_subtitle_display() won't immediately redraw
                # the same subtitle on the next timer tick. It will render again once the subtitle changes.
                self.subtitle_deleted = True
            elif action == "toggle_m3_mode":
                self._toggle_m3_mode()
            elif action == "_seek_step_back":
                self._accumulate_pending_seek("go_back")
            elif action == "_seek_step_forward":
                self._accumulate_pending_seek("go_forward")
            elif action == "apply_pending_seek":
                self._apply_pending_seek()
            elif action == "clear_pending_seek":
                self._clear_pending_seek_preview()

    def _skip_buttons_use_subtitle_segments(self) -> bool:
            try:
                return bool(self.config.get("SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS") or False)
            except Exception:
                return False

    def _hotkey_action_disabled(self, action: str) -> bool:
            key = self.HOTKEY_DISABLE_KEYS.get(str(action or "").strip())
            if not key:
                return False
            try:
                return bool(self.config.get(key) or False)
            except Exception:
                return False
    @staticmethod
    def _is_numpad_vk_key(key, *codes: int) -> bool:
            try:
                return hasattr(key, "vk") and int(getattr(key, "vk")) in codes
            except Exception:
                return False

    def _get_shortcut_value(self, config_key: str) -> str:
            default = self.SHORTCUT_DEFAULTS.get(config_key, "")
            raw = self.config.get(config_key)
            if not isinstance(raw, str) or not raw.strip():
                value = default
            else:
                value = raw
            value = str(value).strip().lower()
            if config_key == "SHORTCUT_TOGGLE_PLAY":
                try:
                    if bool(self.config.get("DISABLE_SPACE_HOTKEY") or False):
                        if self._normalize_shortcut_token(value) == "space":
                            return ""
                except Exception:
                    pass
            return value
    
    @staticmethod
    def _normalize_shortcut_token(token: str) -> str:
            t = str(token or "").strip().lower().replace(" ", "")
            alias = {
                "arrowleft": "left",
                "arrowright": "right",
                "spacebar": "space",
                "ins": "insert",
                "num0": "numpad0",
                "np0": "numpad0",
                "num4": "numpad4",
                "np4": "numpad4",
                "num6": "numpad6",
                "np6": "numpad6",
            }
            return alias.get(t, t)

    def _split_shortcut(self, binding: str):
            text = str(binding or "").strip().lower().replace(" ", "")
            if not text:
                return set(), None
            parts = [p for p in text.split("+") if p]
            mods = set()
            key_token = None
            for p in parts:
                token = self._normalize_shortcut_token(p)
                if token in {"shift", "alt", "ctrl"}:
                    mods.add(token)
                elif key_token is None:
                    key_token = token
            return mods, key_token
    
    @staticmethod
    def _is_text_input_widget(widget) -> bool:
            if widget is None:
                return False
            try:
                cls = str(widget.winfo_class() or "").lower()
            except Exception:
                cls = ""
            return cls in {
                "entry",
                "tentry",
                "ttk::entry",
                "text",
                "combobox",
                "tcombobox",
                "ttk::combobox",
                "spinbox",
                "ttk::spinbox",
            }

    def _is_text_input_focused(self) -> bool:
            windows = [
                getattr(self.settings, "root", None),
                getattr(self.settings, "control_window", None),
                getattr(self.settings, "advanced_window", None),
                getattr(self.popup, "_popup", None),
            ]
            for owner in windows:
                if owner is None:
                    continue
                try:
                    widget = owner.focus_get()
                except Exception:
                    widget = None
                try:
                    # Treat control time entry as text-focused only while actively editing.
                    if widget is getattr(self.settings, "time_entry", None) and not bool(self.entry_editing):
                        continue
                except Exception:
                    pass
                if self._is_text_input_widget(widget):
                    return True
            return False

    def _key_tokens(self, key) -> set[str]:
            tokens = set()

            def _add(*vals):
                for v in vals:
                    if v:
                        tokens.add(str(v).lower())

            if key == Key.space:
                _add("space")
            if key == Key.left:
                _add("left")
            if key == Key.right:
                _add("right")
            if key == Key.insert:
                _add("insert")
            if hasattr(key, "char") and key.char:
                ch = key.char
                # Convert Ctrl-letter control codes (e.g. '\x19') back to letters
                if len(ch) == 1 and ord(ch) < 32:
                    ch = chr(ord(ch) + 96)
                _add(ch.lower())

            if self._is_numpad_vk_key(key, 96, 45):
                _add("numpad0", "0")
            if self._is_numpad_vk_key(key, 100):
                _add("numpad4", "4")
            if self._is_numpad_vk_key(key, 102):
                _add("numpad6", "6")

            return {self._normalize_shortcut_token(t) for t in tokens}

    def _shortcut_matches(self, binding: str, key) -> bool:
            mods, key_token = self._split_shortcut(binding)
            if not key_token:
                return False

            if "shift" in mods and not self.shift_pressed:
                return False
            if "alt" in mods and not self.alt_pressed:
                return False
            if "ctrl" in mods and not self.ctrl_pressed:
                return False

            return key_token in self._key_tokens(key)

    def _repeat_action_bindings(self):
            try:
                mode2_numpad = int(getattr(self.settings, "input_mode", 1)) == 2
            except Exception:
                mode2_numpad = bool(getattr(self.settings, "numpad_mode_enabled", False))
            if mode2_numpad:
                bindings = [
                    ("subtitle_back", self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_BACK")),
                    ("subtitle_forward", self._get_shortcut_value("SHORTCUT_MODE2_SUBTITLE_FORWARD")),
                    ("go_back", self._get_shortcut_value("SHORTCUT_MODE2_GO_BACK")),
                    ("go_forward", self._get_shortcut_value("SHORTCUT_MODE2_GO_FORWARD")),
                ]
            else:
                bindings = [
                    ("subtitle_back", self._get_shortcut_value("SHORTCUT_SUBTITLE_BACK")),
                    ("subtitle_forward", self._get_shortcut_value("SHORTCUT_SUBTITLE_FORWARD")),
                    ("go_back", self._get_shortcut_value("SHORTCUT_GO_BACK")),
                    ("go_forward", self._get_shortcut_value("SHORTCUT_GO_FORWARD")),
                ]
            return [(action, binding) for action, binding in bindings if not self._hotkey_action_disabled(action)]

    def _single_fire_bindings(self):
            bindings = [
                ("toggle_play", self._get_shortcut_value("SHORTCUT_TOGGLE_PLAY")),
                ("toggle_play", self._get_shortcut_value("SHORTCUT_MODE2_TOGGLE_PLAY")),
                ("alt_x", self._get_shortcut_value("SHORTCUT_BRING_TO_FRONT")),
                ("episode_inc", self._get_shortcut_value("SHORTCUT_EPISODE_INC")),
                ("episode_dec", self._get_shortcut_value("SHORTCUT_EPISODE_DEC")),
                ("jump_sub_end", self._get_shortcut_value("SHORTCUT_JUMP_SUB_END")),
            ]
            return [(action, binding) for action, binding in bindings if not self._hotkey_action_disabled(action)]

    def _hotkeys_disabled(self) -> bool:
            try:
                if bool(getattr(self.sub_manager, "is_search_dialog_active", lambda: False)()):
                    return True
            except Exception:
                pass
            try:
                if int(getattr(self.settings, "input_mode", 1)) == 3:
                    return True
            except Exception:
                pass
            try:
                return bool(self.config.get("SHORTCUTS_DISABLED") or False)
            except Exception:
                return False

    def _reset_hotkey_state(self) -> None:
            self.shift_pressed = False
            self.alt_pressed = False
            self.ctrl_pressed = False
            self._single_fire_actions.clear()
            repeat_actions = {action for action, _ in self._repeat_action_bindings()}
            self._release_repeat_actions(repeat_actions, settle_seek=False)

    def _toggle_m3_mode(self) -> None:
            enable_m3 = not self._hotkeys_disabled()
            try:
                self.settings.set_hotkeys_disabled(enable_m3)
            except Exception:
                return
            # Clear any stuck modifier state when toggling hotkeys on/off.
            self._reset_hotkey_state()

    def _on_key_press(self, key):
            if self._hotkeys_disabled():
                self._reset_hotkey_state()
                return

            if key in (Key.shift_l, Key.shift_r):
                self.shift_pressed = True
                return
            if key in (Key.alt_l, Key.alt_r):
                self.alt_pressed = True
                return
            if key in (Key.ctrl_l, Key.ctrl_r):
                self.ctrl_pressed = True
                return

            if self._is_text_input_focused():
                self._reset_hotkey_state()
                return

            mapping = []
            mapping.extend((binding, action, True) for action, binding in self._single_fire_bindings())
            mapping.extend((binding, action, False) for action, binding in self._repeat_action_bindings())

            for binding, action, single_fire in mapping:
                if not self._shortcut_matches(binding, key):
                    continue
                if single_fire and action in self._single_fire_actions:
                    return
                if single_fire:
                    self._single_fire_actions.add(action)
                    self._enqueue_input_action(action)
                    return
                if not self._hold_repeat_action(action):
                    return
                if not self._is_seek_repeat_action(action):
                    self._queue_repeat_step(action)
                return

    def _on_key_release(self, key):
            if self._hotkeys_disabled():
                self._reset_hotkey_state()
                return

            if self._is_text_input_focused():
                self._reset_hotkey_state()
                return

            if key in (Key.shift_l, Key.shift_r):
                self.shift_pressed = False
            if key in (Key.alt_l, Key.alt_r):
                self.alt_pressed = False
            if key in (Key.ctrl_l, Key.ctrl_r):
                self.ctrl_pressed = False

            released_tokens = self._key_tokens(key)
            for action, binding in self._single_fire_bindings():
                _, key_token = self._split_shortcut(binding)
                if key_token and key_token in released_tokens:
                    self._single_fire_actions.discard(action)

            repeat_actions_to_drop = set()
            for action, binding in self._repeat_action_bindings():
                _, key_token = self._split_shortcut(binding)
                if key_token and key_token in released_tokens:
                    repeat_actions_to_drop.add(action)
            if key in (Key.shift_l, Key.shift_r, Key.alt_l, Key.alt_r, Key.ctrl_l, Key.ctrl_r):
                repeat_actions_to_drop.update(action for action, _ in self._repeat_action_bindings())
            self._release_repeat_actions(repeat_actions_to_drop, settle_seek=True)

    def on_alt_x(self, event=None):
            self.settings.control_window.attributes("-topmost", True)
            try:
                self.popup.ensure_on_top()
            except Exception:
                pass

    def _on_global_click(self, x, y, button, pressed):
            if button == Button.x2 and pressed:
                self._enqueue_input_action("clear_subtitle")
            if button == Button.x1 and pressed:
                self._enqueue_input_action("toggle_m3_mode")"""OCR capture, timecode extraction, and OCR sync helper."""

import os
import re
import subprocess
import tempfile
import threading
import time
import pyautogui
from PIL import ImageGrab, ImageOps, ImageStat, Image
from typing import Any
from utils import get_monitor_rects, show_window_no_activate, parse_time_value

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

class OCRController(_ControllerProxy):
    """OCR capture, timecode extraction, and OCR sync helper."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def _schedule_ocr_time_jump(self, reason: str) -> None:
            if self._shutting_down:
                return
            if not self.config.get("OCR_ENABLED"):
                return
            delay_ms = 800 if reason == "startup" else 500
            try:
                if self._ocr_job is not None:
                    self.settings.root.after_cancel(self._ocr_job)
            except Exception:
                pass
            self._ocr_generation += 1
            try:
                self._ocr_pending_time = float(self.current_time)
            except Exception:
                self._ocr_pending_time = None

            generation = self._ocr_generation

            def _kickoff():
                self._run_ocr_time_jump_async(generation)

            try:
                self._ocr_job = self.settings.root.after(delay_ms, _kickoff)
            except Exception:
                self._ocr_job = None

    def _run_ocr_time_jump_async(self, generation: int) -> None:
            if self._shutting_down:
                return

            def worker():
                seconds = self._ocr_find_time_seconds()
                if seconds is None:
                    return
                try:
                    self.settings.root.after(0, lambda: self._apply_ocr_time(seconds, generation))
                except Exception:
                    pass

            self._ocr_thread = threading.Thread(target=worker, daemon=True)
            self._ocr_thread.start()

    def _apply_ocr_time(self, seconds: float, generation: int) -> None:
            if self._shutting_down:
                return
            if generation != self._ocr_generation:
                return
            try:
                if self.playing:
                    return
            except Exception:
                pass
            pending = getattr(self, "_ocr_pending_time", None)
            if pending is not None:
                try:
                    if abs(float(self.current_time) - float(pending)) > 0.75:
                        return
                except Exception:
                    pass
            self.playback.set_current_time(seconds)

    @staticmethod
    def _rects_intersect(a, b) -> bool:
            if not a or not b:
                return False
            ax, ay, aw, ah = a
            bx, by, bw, bh = b
            return (ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by)
    
    @staticmethod
    def _window_screen_rect(win):
            if win is None:
                return None
            try:
                if not win.winfo_exists():
                    return None
                win.update_idletasks()
                x = int(win.winfo_rootx())
                y = int(win.winfo_rooty())
                w = int(win.winfo_width()) or int(win.winfo_reqwidth())
                h = int(win.winfo_height()) or int(win.winfo_reqheight())
                if w <= 0 or h <= 0:
                    return None
                return (x, y, w, h)
            except Exception:
                return None

    def _temporarily_hide_windows_for_ocr(self, override: dict | None = None):
            try:
                ocr_regions = [tuple(region) for _idx, region, _custom in self._get_ocr_capture_regions(override)]
            except Exception:
                ocr_regions = []
            if not ocr_regions:
                return lambda: None

            windows = [
                getattr(self.settings, "control_window", None),
                getattr(self.overlay, "sub_window", None),
                getattr(self.overlay, "subtitle_handle", None),
                getattr(self.settings, "advanced_window", None),
                getattr(self.settings, "root", None),
                getattr(self.popup, "_popup", None),
                getattr(self, "_anki_wait_window", None),
                getattr(self, "_anki_success_popup", None),
            ]
            hidden = []
            seen = set()

            for win in windows:
                if win is None:
                    continue
                try:
                    key = str(win)
                except Exception:
                    key = id(win)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    if not win.winfo_exists():
                        continue
                    state = str(win.state())
                    if state == "withdrawn":
                        continue
                    rect = self._window_screen_rect(win)
                    if not any(self._rects_intersect(rect, region) for region in ocr_regions):
                        continue
                    hidden.append((win, state))
                    win.withdraw()
                except Exception:
                    pass

            try:
                self.settings.root.update_idletasks()
            except Exception:
                pass

            passive_windows = {
                getattr(self.overlay, "subtitle_handle", None),
                getattr(self.popup, "_popup", None),
                getattr(self, "_anki_success_popup", None),
            }

            def restore():
                for win, state in hidden:
                    try:
                        if not win.winfo_exists():
                            continue
                        if state == "iconic":
                            win.iconify()
                            continue
                        if win in passive_windows:
                            show_window_no_activate(win)
                        else:
                            win.deiconify()
                            try:
                                if win is getattr(self.settings, "control_window", None):
                                    win.attributes("-topmost", True)
                                elif win is getattr(self.overlay, "sub_window", None):
                                    win.attributes("-topmost", True)
                                elif win is getattr(self.settings, "advanced_window", None):
                                    win.attributes("-topmost", True)
                            except Exception:
                                pass
                    except Exception:
                        pass
                try:
                    self.popup.ensure_on_top()
                except Exception:
                    pass

            return restore

    def on_ocr_read_now(self, override: dict | None = None) -> None:
            if self._shutting_down:
                return

            restore_windows = self._temporarily_hide_windows_for_ocr(override=override)

            def start_worker():
                def worker():
                    try:
                        seconds = self._ocr_find_time_seconds(override=override)
                        if seconds is None:
                            self._log_ocr_read_failure(override=override)
                            return
                        try:
                            self.settings.root.after(0, lambda: self._apply_ocr_time_manual(seconds))
                        except Exception:
                            pass
                    finally:
                        try:
                            self.settings.root.after(0, restore_windows)
                        except Exception:
                            restore_windows()

                threading.Thread(target=worker, daemon=True).start()

            try:
                self.settings.root.after(140, start_worker)
            except Exception:
                start_worker()

    def _log_ocr_read_failure(self, override: dict | None = None) -> None:
            try:
                regions = self._get_ocr_capture_regions(override=override)
            except Exception:
                regions = []
            if not regions:
                print("OCR read-now failed: no capture region available.")
                return
            details = []
            for region_idx, region, is_custom in regions:
                try:
                    x, y, w, h = region
                    mode = "custom" if is_custom else "default"
                    details.append(f"#{region_idx} {x},{y} {w}x{h} ({mode})")
                except Exception:
                    details.append(f"#{region_idx} <invalid region>")
            joined = "; ".join(details)
            print(f"OCR read-now failed: no valid timecode detected. Checked {len(regions)} region(s): {joined}")

    def on_ocr_sync_now(self, override: dict | None = None) -> None:
            if self._shutting_down:
                return
            if not self.playing:
                try:
                    self.playback.toggle_play()
                except Exception:
                    pass
            self._start_ocr_live_sync(duration_sec=5.0, interval_sec=0.25, override=override)

    def _apply_ocr_time_manual(self, seconds: float) -> None:
            if self._shutting_down:
                return
            self.playback.set_current_time(seconds)

    def _start_ocr_live_sync(
            self,
            duration_sec: float = 5.0,
            interval_sec: float = 0.25,
            override: dict | None = None,
        ) -> None:
            if self._shutting_down:
                return
            if not self.config.get("OCR_ENABLED"):
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
            interval_sec = max(0.1, interval_sec)

            self._ocr_sync_generation += 1
            generation = self._ocr_sync_generation

            def worker():
                deadline = time.perf_counter() + duration_sec
                snapped_initial = False
                while time.perf_counter() < deadline:
                    if self._shutting_down or generation != self._ocr_sync_generation:
                        return
                    started = time.perf_counter()
                    try:
                        base_time = float(self.current_time)
                    except Exception:
                        base_time = None
                    seconds = self._ocr_find_time_seconds(override=override)
                    if seconds is not None and base_time is not None:
                        if not snapped_initial:
                            try:
                                self.settings.root.after(0, lambda s=seconds: self._apply_ocr_time_manual(s))
                            except Exception:
                                pass
                            snapped_initial = True
                            sleep_for = interval_sec - (time.perf_counter() - started)
                            if sleep_for > 0:
                                time.sleep(sleep_for)
                            continue
                        elapsed = time.perf_counter() - started
                        if self.playing:
                            base_time += elapsed
                        delta = seconds - base_time
                        if abs(delta) >= 0.15:
                            adjust = max(-0.5, min(0.5, delta * 0.5))
                            try:
                                self.settings.root.after(0, lambda d=adjust: self._apply_ocr_sync_delta(d))
                            except Exception:
                                pass
                    sleep_for = interval_sec - (time.perf_counter() - started)
                    if sleep_for > 0:
                        time.sleep(sleep_for)

            threading.Thread(target=worker, daemon=True).start()

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

    def _apply_ocr_sync_delta(self, delta: float) -> None:
            if self._shutting_down:
                return
            try:
                delta = float(delta)
            except Exception:
                return
            if abs(delta) < 0.15:
                return
            try:
                new_time = float(self.current_time) + delta
            except Exception:
                return
            self.playback.set_current_time(new_time)
            print(f"OCR sync: adjusted by {delta:+.2f}s")

    def _ocr_find_time_seconds(self, override: dict | None = None):
            regions = self._get_ocr_capture_regions(override)
            if not regions:
                return None

            started = time.perf_counter()
            variant_makers = []

            def _make_autocontrast(img):
                gray = ImageOps.grayscale(img)
                return ImageOps.autocontrast(gray)

            def _make_gray(img):
                return ImageOps.grayscale(img)

            variant_makers.append(("autocontrast", _make_autocontrast))
            variant_makers.append(("gray", _make_gray))

            # Threshold variant (disabled for now; keep for later tuning)
            # def _make_threshold(img):
            #     gray = ImageOps.grayscale(img)
            #     stat = ImageStat.Stat(gray)
            #     median = int(stat.median[0]) if stat.median else 128
            #     threshold = min(255, max(0, median + 10))
            #     thresh_img = gray.point(lambda p, t=threshold: 255 if p >= t else 0)
            #     if median < 128:
            #         thresh_img = ImageOps.invert(thresh_img)
            #     return thresh_img
            # variant_makers.append(("threshold", _make_threshold))

            region_images = []
            for region_idx, region, is_custom in regions:
                img = self._capture_ocr_image(region)
                if img is None:
                    region_images.append((region_idx, None))
                    continue
                if is_custom:
                    try:
                        img = img.resize((img.width * 2, img.height * 2), Image.BICUBIC)
                    except Exception:
                        pass
                region_images.append((region_idx, img))

            for label, maker in variant_makers:
                for region_idx, img in region_images:
                    if img is None:
                        continue
                    try:
                        variant = maker(img)
                    except Exception:
                        variant = img
                    text = self._ocr_image_to_text(variant, override=override)
                    if not text:
                        continue
                    result = self._extract_time_from_ocr_text(text, override=override)
                    if result is None:
                        continue
                    seconds, left, right = result
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    print(f"OCR result: {left} / {right} -> {seconds:.2f}s (box {region_idx}, {label}, {elapsed_ms:.0f} ms)")
                    return seconds
            return None

    def _ocr_image_to_text(self, image, override: dict | None = None) -> str:
            config_str, config_args = self._build_tesseract_config(override)
            tesseract_cmd = self._resolve_tesseract_cmd(override)
            try:
                import pytesseract
                if tesseract_cmd:
                    try:
                        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
                    except Exception:
                        pass
                return pytesseract.image_to_string(image, config=config_str) or ""
            except Exception:
                pass

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    tmp_path = tmp.name
                    image.save(tmp_path)
                cmd = tesseract_cmd or "tesseract"
                result = subprocess.run(
                    [cmd, tmp_path, "stdout", *config_args],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                return result.stdout or ""
            except Exception:
                return ""
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

    def _extract_time_from_ocr_text(self, text: str, override: dict | None = None):
            if not text:
                return None
            cleaned = text.replace(" ", "").replace("\n", "").replace("\t", "")
            cleaned = (cleaned
                    .replace("O", "0")
                    .replace("o", "0")
                    .replace("I", "1")
                    .replace("l", "1")
                    .replace(";", ":"))

            matches = self.OCR_TIME_PATTERN.findall(cleaned)
            if not matches:
                # Fallback 1: digit-only timecodes (e.g. 1823/2341)
                digit_matches = re.findall(r"(\d{3,4})[/\\|](\d{3,4})", cleaned)
                if digit_matches:
                    matches = digit_matches

            if not matches:
                # Fallback 2: 8-digit compact timecodes (e.g. 18232341 -> 18:23 / 23:41)
                digits_only = re.sub(r"\D", "", cleaned)
                for i in range(max(0, len(digits_only) - 7)):
                    run = digits_only[i:i + 8]
                    if len(run) < 8:
                        continue
                    left_digits = run[:4]
                    right_digits = run[4:]
                    try:
                        mm1 = int(left_digits[:2])
                        ss1 = int(left_digits[2:])
                        mm2 = int(right_digits[:2])
                        ss2 = int(right_digits[2:])
                    except Exception:
                        continue
                    if mm1 > 59 or ss1 > 59 or mm2 > 59 or ss2 > 59:
                        continue
                    left = f"{mm1:02d}:{ss1:02d}"
                    right = f"{mm2:02d}:{ss2:02d}"
                    matches = [(left, right)]
                    break

            if not matches:
                # Fallback 3: two timecodes found back-to-back (missing separator)
                time_re = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
                time_hits = list(time_re.finditer(cleaned))
                for i in range(len(time_hits) - 1):
                    gap = time_hits[i + 1].start() - time_hits[i].end()
                    if gap <= 2:
                        matches = [(time_hits[i].group(0), time_hits[i + 1].group(0))]
                        break

            if not matches:
                # Fallback 4: left time + trailing digits (e.g. 18:2312341 -> 18:23 / 23:41)
                tail_match = re.search(r"(\d{1,2}:\d{2})(\d{4,6})", cleaned)
                if tail_match:
                    left = tail_match.group(1)
                    tail = re.sub(r"\D", "", tail_match.group(2) or "")
                    if len(tail) >= 4:
                        right_digits = tail[-4:]
                        try:
                            mm = int(right_digits[:2])
                            ss = int(right_digits[2:])
                        except Exception:
                            mm, ss = 99, 99
                        if mm > 59 or ss > 59:
                            right_digits = ""
                        if not right_digits:
                            pass
                        else:
                            right = f"{right_digits[:2]}:{right_digits[2:]}"
                            matches = [(left, right)]
            if not matches:
                return None

            max_allow = None
            try:
                max_allow = float(self.total_duration) + float(self.settings._last_offset_value or 0.0)
            except Exception:
                max_allow = None

            for left, right in matches:
                try:
                    left_sec = parse_time_value(left)
                    right_sec = parse_time_value(right)
                except Exception:
                    continue
                if right_sec > 0 and left_sec > right_sec + 1.0:
                    continue
                if max_allow is not None and max_allow > 0 and left_sec > (max_allow + 2.0):
                    continue
                return left_sec, left, right
            return None

    @staticmethod
    def coerce_int(value, default: int = 0) -> int:
            try:
                return int(float(str(value).strip().replace(",", ".")))
            except Exception:
                return int(default)
    
    @staticmethod
    def coerce_float(value, default: float = 0.0) -> float:
            try:
                return float(str(value).strip().replace(",", "."))
            except Exception:
                return float(default)

    def _ocr_sync_after_anki_enabled(self) -> bool:
            raw = self.config.get("OCR_SYNC_AFTER_ANKI")
            if raw is None:
                return True
            return bool(raw)

    def _build_tesseract_config(self, override: dict | None = None):
            psm = self.coerce_int(
                (override or {}).get("OCR_TESSERACT_PSM", self.config.get("OCR_TESSERACT_PSM")),
                default=6,
            )
            oem = self.coerce_int(
                (override or {}).get("OCR_TESSERACT_OEM", self.config.get("OCR_TESSERACT_OEM")),
                default=3,
            )
            whitelist = (override or {}).get("OCR_CHAR_WHITELIST", self.config.get("OCR_CHAR_WHITELIST"))
            whitelist = str(whitelist or "").strip() or "0123456789:/"

            args = ["--psm", str(psm)]
            if oem >= 0:
                args += ["--oem", str(oem)]
            if whitelist:
                args += ["-c", f"tessedit_char_whitelist={whitelist}"]

            return " ".join(args), args

    def _resolve_tesseract_cmd(self, override: dict | None = None):
            raw = ""
            if override and "OCR_TESSERACT_CMD" in override:
                raw = str(override.get("OCR_TESSERACT_CMD") or "").strip()
            if not raw:
                raw = str(self.config.get("OCR_TESSERACT_CMD") or "").strip()
            if not raw:
                raw = (os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH") or "").strip()
            if not raw:
                return None
            path = os.path.expandvars(raw)
            if os.path.isdir(path):
                exe = os.path.join(path, "tesseract.exe")
                if os.path.exists(exe):
                    return exe
            return path

    def _get_ocr_setting(self, key: str, override: dict | None = None, default=None):
            if override and key in override:
                return override.get(key)
            value = self.config.get(key)
            return default if value is None else value

    def _get_ocr_region_count(self, override: dict | None = None) -> int:
            raw = self._get_ocr_setting("OCR_REGION_COUNT", override, default=2)
            count = self.coerce_int(raw, default=2)
            return max(1, min(8, count))

    def _get_ocr_capture_regions(self, override: dict | None = None):
            monitors = get_monitor_rects(self.settings.root)
            if not monitors:
                monitors = [(0, 0, 1920, 1080)]

            regions = []
            count = self._get_ocr_region_count(override)
            default_screen = self.coerce_int(
                self._get_ocr_setting("OCR_SCREEN_INDEX", override, default=1),
                default=1,
            )

            def _suffix(idx: int) -> str:
                return "" if idx == 1 else str(idx)

            def _build_region(idx: int, default_bottom: bool):
                suffix = _suffix(idx)
                region_screen = self.coerce_int(
                    self._get_ocr_setting(f"OCR_REGION{suffix}_SCREEN", override, default=default_screen),
                    default=default_screen,
                )
                base_x, base_y, base_w, base_h = self._resolve_ocr_screen_rect(monitors, region_screen)
                rx = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_X", override, default=0), default=0)
                ry = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_Y", override, default=0), default=0)
                rw = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_W", override, default=0), default=0)
                rh = self.coerce_int(self._get_ocr_setting(f"OCR_REGION{suffix}_H", override, default=0), default=0)

                if rw <= 0 or rh <= 0:
                    if not default_bottom:
                        return None
                    half_h = max(1, int(base_h / 2))
                    return (base_x, base_y + (base_h - half_h), base_w, half_h), False

                if rx < 0:
                    rx = 0
                if ry < 0:
                    ry = 0
                if rw > base_w:
                    rw = base_w
                if rh > base_h:
                    rh = base_h
                return (base_x + rx, base_y + ry, rw, rh), True

            for idx in range(1, count + 1):
                default_bottom = (idx == 1)
                built = _build_region(idx, default_bottom=default_bottom)
                if built:
                    region, is_custom = built
                    regions.append((idx, region, is_custom))
            return regions
    
    @staticmethod
    def _resolve_ocr_screen_rect(monitors, screen_idx: int):
            if not monitors:
                return (0, 0, 1920, 1080)
            if screen_idx <= 0:
                min_x = min(r[0] for r in monitors)
                min_y = min(r[1] for r in monitors)
                max_x = max(r[0] + r[2] for r in monitors)
                max_y = max(r[1] + r[3] for r in monitors)
                x, y = int(min_x), int(min_y)
                w, h = int(max_x - min_x), int(max_y - min_y)
                return x, y, w, h

            if screen_idx > len(monitors):
                screen_idx = 1
            x, y, w, h = monitors[screen_idx - 1]
            return int(x), int(y), int(w), int(h)

    def _get_ocr_base_rect(self, override: dict | None = None):
            monitors = get_monitor_rects(self.settings.root)
            if not monitors:
                monitors = [(0, 0, 1920, 1080)]
            screen_idx = self.coerce_int(
                self._get_ocr_setting("OCR_SCREEN_INDEX", override, default=1),
                default=1,
            )
            return self._resolve_ocr_screen_rect(monitors, screen_idx)

    def _get_ocr_capture_region(self, override: dict | None = None):
            regions = self._get_ocr_capture_regions(override)
            if not regions:
                return None
            return regions[0][1]

    def _capture_ocr_image(self, region):
            if not region:
                return None
            x, y, w, h = region
            bbox = (int(x), int(y), int(x + w), int(y + h))
            try:
                return ImageGrab.grab(bbox=bbox, all_screens=True)
            except Exception:
                pass
            try:
                return ImageGrab.grab(bbox=bbox)
            except Exception:
                pass
            try:
                # Fallback to pyautogui (may ignore negative coords)
                if x >= 0 and y >= 0:
                    return pyautogui.screenshot(region=(int(x), int(y), int(w), int(h)))
                return pyautogui.screenshot()
            except Exception:
                return None
"""Overlay/window visibility helper for control-window and subtitle-handle behavior."""

from typing import Any

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

class OverlayController(_ControllerProxy):
    """Overlay/window visibility helper for control-window and subtitle-handle behavior."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def _schedule_hide_controls(self):
            if self.settings.default_phone_mode:
                self._hide_controls_after(self.phone_windows_hide_control_ms)

    def _hide_controls_after(self, ms: int):
            if getattr(self, "_con_hide_job", None):
                self.settings.control_window.after_cancel(self._con_hide_job)
            self._con_hide_job = self.settings.control_window.after(
                ms,
                self.settings.control_window.lower
            )

    def sub_window_enter(self, event):
            self.overlay.sub_window.attributes("-transparentcolor", "") #not transparent
            self.settings.control_window.attributes("-topmost", True)
            self.overlay.sub_window.attributes("-topmost", True)
            try:
                self.popup.ensure_on_top()
            except Exception:
                pass
            if getattr(self, "_con_hide_job", None) is not None:
                self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
                self._con_hide_job = None
            self.subtitle_deleted = False

    def sub_window_leave(self, event):
            self.overlay.sub_window.attributes("-transparentcolor", "grey")
            if not self.settings.default_phone_mode:
                self._hide_controls_after(self.windows_hide_control_ms)

    def sub_handel_enter(self, event):
            self.settings.control_window.attributes("-topmost", True)
            self.overlay.sub_window.attributes("-topmost", True)
            try:
                self.popup.ensure_on_top()
            except Exception:
                pass
            if getattr(self, "_con_hide_job", None):
                self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
                self._con_hide_job = None
            self.subtitle_deleted = False

    def control_window_enter(self, event):
            self.settings.control_window.attributes("-topmost", True)
            self.overlay.sub_window.attributes("-topmost", True)
            try:
                self.popup.ensure_on_top()
            except Exception:
                pass
            if getattr(self, "_con_hide_job", None):
                self.settings.control_window.after_cancel(self._con_hide_job) #cancel hide after calls if triggered
                self._con_hide_job = None
            self.subtitle_deleted = False

    def control_window_leave(self, event):
            delay = (self.phone_windows_hide_control_ms
                    if self.settings.default_phone_mode
                    else self.windows_hide_control_ms)
            self._hide_controls_after(delay)

    def show_subtitle_handle(self, is_phone):
            if is_phone:
                self.overlay.show_handle()
            else:
                self.overlay.hide_handle()

    def _hide_subtitle_handle_for_settings(self):
            try:
                if self.settings.default_phone_mode:
                    self.overlay.hide_handle()
            except Exception:
                pass

    def _restore_subtitle_handle_after_settings(self):
            try:
                if self.settings.default_phone_mode:
                    self.overlay.show_handle()
            except Exception:
                pass
import time
import bisect
import tkinter as tk


class PlaybackController:
    def __init__(self, controller=None):
        self.controller = controller

    def set_controller(self, controller):
        self.controller = controller

    def update_loop(self):
        if self.controller.playing:
            now = time.time()
            delta = now - self.controller.last_update
            self.controller.last_update = now
            self.set_current_time(self.controller.current_time + delta)

        self.schedule_update()

    def schedule_update(self):
        self.controller.overlay.root.after(self.controller.update_interval_ms, self.update_loop)

    def set_current_time(self, t: float):
        if t is None:
            return

        offset = self.controller.get_offset_value()
        # print(offset, "test")##testing
        t = max(0, min(t, self.controller.total_duration + offset))

        if t - offset >= self.controller.total_duration and self.controller.playing:
            self.toggle_play()

        self.controller.current_time = t
        if not self.controller.slider_dragging:
            self.controller.settings.slider.set(t)
            self.controller.update_time_and_subtitle_displays()

    def toggle_play(self):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        self.controller.playing = not self.controller.playing

        if self.controller.playing:
            self.controller.settings.play_pause_btn.config(text="Stop", bg="red", activebackground="red")
            self.controller.last_update = time.time()
            self.schedule_update()
        else:
            self.controller.settings.play_pause_btn.config(text="Play", bg="green", activebackground="green")
            if self.controller.subtitle_timeout_job:
                self.controller.overlay.root.after_cancel(self.controller.subtitle_timeout_job)
                self.controller.subtitle_timeout_job = None
            if self.controller.subtitle_deleted and self.controller.last_subtitle_text:
                self.controller.subtitle_deleted = False

        self.controller.update_time_and_subtitle_displays()
        self.controller._schedule_hide_controls()

    def go_forward(self):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        if self.controller._skip_buttons_use_subtitle_segments():
            self.jump_subtitle_segment("next")
            return

        skip = self.controller.settings._last_skip_value
        max_time = self.controller.total_duration + self.controller.get_offset_value()
        if self.controller.current_time <= max_time:
            self.set_current_time(self.controller.current_time + skip)
            self.controller._schedule_hide_controls()

    def go_back(self):
        if self.controller.entry_editing:
            self.controller.control_time_entry_return(None)

        if self.controller._skip_buttons_use_subtitle_segments():
            self.jump_subtitle_segment("prev")
            return

        skip = self.controller.settings._last_skip_value
        if self.controller.current_time >= 0:
            self.set_current_time(self.controller.current_time - skip)
            self.controller._schedule_hide_controls()

    def jump_subtitle_segment(self, direction: str) -> None:
        start_times = self.controller._get_display_start_times()
        if not start_times:
            return

        offset = self.controller.get_offset_value()
        sub_t = max(0.0, float(self.controller.current_time) - offset)
        epsilon = 0.05

        if direction == "prev":
            target_idx = bisect.bisect_right(start_times, sub_t - epsilon) - 1
        elif direction == "next":
            target_idx = bisect.bisect_right(start_times, sub_t + epsilon)
        else:
            return

        if target_idx < 0 or target_idx >= len(start_times):
            return

        self.set_current_time(float(start_times[target_idx]) + offset)
        self.controller._schedule_hide_controls()

    def on_jump_sub_end(self, event=None):
        start_times = self.controller._get_display_start_times()
        if not start_times:
            return

        offset = self.controller.get_offset_value()
        sub_t = max(0.0, float(self.controller.current_time) - offset)
        epsilon = 0.05

        idx = bisect.bisect_right(start_times, sub_t + epsilon) - 1
        if idx < 0 or idx >= len(self.controller.sub_manager.subtitles):
            return

        sub = self.controller.sub_manager.subtitles[idx]
        target_time = sub.end.total_seconds() + float(self.controller.audio_padding or 0.0) + offset

        self.set_current_time(target_time)
        self.controller._schedule_hide_controls()
"""Subtitle time display, slider sync, and subtitle redraw helper."""

import bisect
import tkinter as tk
import re
from typing import Any
from utils import format_time, parse_time_value

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

class SubtitleNavigationController(_ControllerProxy):
    """Subtitle time display, slider sync, and subtitle redraw helper."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def get_offset_value(self) -> float: return float(self.settings._last_offset_value)
    
    @staticmethod
    def _format_delta_seconds(value: float) -> str:
            text = f"{abs(float(value)):.2f}".rstrip("0").rstrip(".")
            return text if text else "0"

    def on_set_to_return(self, text: str):
            self.settto_editing = False
            if not re.fullmatch(r"[\d:.]+", text):
                self.settings.control_time_str.set(format_time(self.current_time))
                self._release_time_entry_focus()
                return
            secs = parse_time_value(text)
            self.playback.set_current_time(secs)
            self.settings.setto_entry.delete(0, tk.END)
            self.settings.root.focus_set()

    def _release_time_entry_focus(self) -> None:
            try:
                focused = self.settings.control_window.focus_get()
            except Exception:
                focused = None
            try:
                time_entry = self.settings.time_entry
            except Exception:
                time_entry = None
            if focused is not time_entry:
                return
            try:
                target = getattr(self.overlay, "sub_window", None)
                if target is not None and target.winfo_exists():
                    target.focus_force()
                    return
            except Exception:
                pass
            try:
                self.settings.control_window.after_idle(
                    lambda: self.settings.control_window.tk.call("focus", "")
                )
                return
            except Exception:
                pass
            try:
                self.settings.control_window.focus_set()
            except Exception:
                pass

    def control_time_entry_return(self, event):
            self.entry_editing  = False
            text = self.settings.control_time_str.get().strip()
            if not re.fullmatch(r"[\d:.]+", text):
                self.settings.control_time_str.set(format_time(self.current_time))
                self._release_time_entry_focus()
                return
            new_time = parse_time_value(text)
            self.playback.set_current_time(new_time)
            self._release_time_entry_focus()

    def control_clear_time_entry(self, event):
            if self.playing:
                self.playback.toggle_play()
            self.entry_editing  = True
            event.widget.delete(0, tk.END)

    def update_time_and_subtitle_displays(self):#updates settings time overlay and control window entry
            text = format_time(self.current_time)
            try:
                pending = float(getattr(self, "_pending_seek_delta", 0.0) or 0.0)
            except Exception:
                pending = 0.0
            if abs(pending) >= 0.001:
                sign = "+" if pending > 0 else "-"
                text = f"{text} ({sign}{self._format_delta_seconds(pending)}s)"
            self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
            self.settings.update_time_overlay_position()
            if not self.entry_editing:
                self.settings.control_time_str.set(text)
            self._update_subtitle_display()

    def _update_subtitle_display(self, force: bool = False):
            offset = self.settings._last_offset_value
            sub_t = self.current_time - offset

            if sub_t < 0 or sub_t > self.total_duration:
                self.last_rendered_index = None
                self.last_subtitle_text = ""
                self._reset_canvas()
                return

            start_times = self._get_display_start_times()
            idx = bisect.bisect_right(start_times, sub_t) - 1

            if idx < 0:
                self.last_rendered_index = None
                self.last_subtitle_text = ""
                self._reset_canvas()
                return

            clean, _, top, bottom = self.sub_manager.display_data[idx]
            copy_text = self.segments_to_copy_text(top, bottom) or clean
            self.last_subtitle_raw = copy_text

            if (
                not force
                and not self.subtitle_deleted
                and idx == self.last_rendered_index
                and copy_text == self.last_subtitle_text
            ):
                return

            try:
                self.sub_manager.ensure_auto_ruby_for_index(idx)
            except Exception:
                pass

            clean, _, top, bottom = self.sub_manager.display_data[idx]
            copy_text = self.segments_to_copy_text(top, bottom) or clean
            self.last_subtitle_raw = copy_text

            if self.subtitle_timeout_job:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None

            # self.renderer.canvas.delete("all")

            # add this only if overlay may recreate the canvas
            self.renderer.update_canvas(self.overlay.subtitle_canvas)

            self.last_subtitle_text = copy_text
            self.last_rendered_index = idx
            self.subtitle_deleted = False

            self.renderer.render_subtitle(top, bottom, self.overlay)

            self.subtitle_timeout_job = self.overlay.root.after(
                self.hide_subtitles_ms,
                self._hide_subtitles_temporarily
            )
    @staticmethod
    def segments_to_copy_text(top_segments, bottom_segments) -> str:
        def _line_text(segments) -> str:
            out = []
            for base, ruby in segments or []:
                if ruby:
                    out.append(f"{base}[{ruby}]")
                else:
                    out.append(str(base or ""))
            return "".join(out).strip()

        lines = []
        for segments in (top_segments, bottom_segments):
            text = _line_text(segments)
            if text:
                lines.append(text)
        return "\n".join(lines)
    
    def _reset_canvas(self):
            self.renderer.canvas.delete("all")
            self.last_rendered_index = None
            self.last_subtitle_text = ""
            self.subtitle_deleted = True

    def _hide_subtitles_temporarily(self):
            if not self.playing:
                self.subtitle_timeout_job = None
                return
            if not self.subtitle_deleted:
                self.renderer.canvas.delete("all")
                self.subtitle_deleted = True
                self.last_rendered_index = None
            self.subtitle_timeout_job = None

    def on_refresh_subtitles(self, event):
            # 1) cancel any pending hide‐job
            if self.subtitle_timeout_job:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            self.last_subtitle_text = ""
            self.subtitle_deleted    = False

            self.update_time_and_subtitle_displays()

    def on_slider_press(self, event):
            self.slider_dragging = True

    def on_slider_change(self, value):
            if self.slider_dragging:
                text = format_time(float(value))
                self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
                self.settings.update_time_overlay_position()
                if not self.entry_editing:
                    self.settings.control_time_str.set(text)
                self.current_time = float(value)
                self._update_subtitle_display()

    def on_slider_release(self, event):
            self.slider_dragging = False
            self.playback.set_current_time(self.settings.slider.get())
"""
AnkiConnect client used to create notes from popup selections.
"""

from __future__ import annotations
import base64
import os
import re
import sys
from html import escape
from typing import Dict, List, Optional

import requests

try:
    from SubtitlePlayer.furigana_splitter import split_furigana, split_moras
except ImportError:
    try:
        from furigana_splitter import split_furigana, split_moras
    except ImportError:
        _package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _package_dir not in sys.path:
            sys.path.insert(0, _package_dir)
        from furigana_splitter import split_furigana, split_moras

try:
    from fugashi import Tagger
except Exception:
    Tagger = None


class AnkiClient:
    DEFAULT_JISHO_URL = "https://jisho.org/api/v1/search/words"
    DEFAULT_GOOGLE_TRANSLATE_URL = "https://translate.googleapis.com/translate_a/single"
    DEFAULT_DEEPL_URL = "https://api-free.deepl.com/v2/translate"
    DEFAULT_STROKE_SVG_BASE_URL = "https://raw.githubusercontent.com/KanjiVG/kanjivg/master/kanji"
    DEFAULT_STROKE_MEDIA_PREFIX = "stroke_"

    def __init__(self, config) -> None:
        self.config = config
        self.url = (self.config.get("ANKI_CONNECT_URL") or "http://127.0.0.1:8765").strip()
        self.http_timeout = float(self.config.get("ANKI_HTTP_TIMEOUT_SEC") or 4.0)

        self._model_fields_cache: Optional[set[str]] = None
        self._word_translation_cache: Dict[str, str] = {}
        self._word_definition_cache: Dict[str, str] = {}
        self._sentence_translation_cache: Dict[str, str] = {}
        self._jisho_entries_cache: Dict[str, List[Dict]] = {}
        self._stroke_media_exists_cache: Dict[str, bool] = {}
        self._stroke_sync_failed_cache: set[str] = set()

        # when using Jisho we keep the full english definition list here so the
        # note builder can put the full string into the "Definition" field
        self._last_jisho_full_definition: str = ""

        self._tagger = None
        if Tagger is not None:
            try:
                self._tagger = Tagger()
            except Exception:
                self._tagger = None

        # Deck/model defaults are hardcoded; config can still override if needed.
        self.deck_name = (self.config.get("ANKI_DECK") or "Japanese").strip()
        self.reading_deck = (self.config.get("ANKI_READING_DECK") or f"{self.deck_name}::Reading").strip()
        self.reverse_deck = (self.config.get("ANKI_REVERSE_DECK") or f"{self.deck_name}::DE -> JA").strip()
        self.model_name = (
            self.config.get("ANKI_MODEL") or "Standard (und umgekehrte Karte) Japanese"
        ).strip()

        self.add_rubies_to_front_field = (
            self.config.get("ANKI_FIELD_ADD_RUBIES_FRONT") or "AddRubiesToFront"
        ).strip()
        self.front_field = (self.config.get("ANKI_FIELD_FRONT") or "Front").strip()
        self.back_field = (self.config.get("ANKI_FIELD_BACK") or "Back").strip()
        self.sentence_ja_field = (
            self.config.get("ANKI_FIELD_SENTENCE_JA") or "SentenceJA"
        ).strip()
        self.sentence_de_field = (
            self.config.get("ANKI_FIELD_SENTENCE_DE") or "SentenceDE"
        ).strip()
        self.sound_field = (self.config.get("ANKI_FIELD_SOUND") or "Sound").strip()
        self.image_field = (self.config.get("ANKI_FIELD_IMAGE") or "Image").strip()
        self.add_rubies_to_sentence_ja_field = (
            self.config.get("ANKI_FIELD_ADD_RUBIES_SENTENCE_JA") or "AddRubiesToSentenceJA"
        ).strip()
        self.definition_field = (
            self.config.get("ANKI_FIELD_DEFINITION") or "Definition"
        ).strip()

        self.sentence_target_lang = (self.config.get("ANKI_SENTENCE_TARGET_LANG") or "de").strip()
        self.word_target_lang = (self.config.get("ANKI_WORD_TARGET_LANG") or "de").strip()

        # Prefer Jisho for single-word meanings; keep DeepL for sentence translation.
        self.word_translate_provider = "jisho"
        self.sentence_translate_provider = "deepl"

        self.jisho_url = self.DEFAULT_JISHO_URL
        self.google_translate_url = self.DEFAULT_GOOGLE_TRANSLATE_URL
        self.deepl_translate_url = self.DEFAULT_DEEPL_URL
        self.stroke_svg_base_url = (
            self.config.get("ANKI_STROKE_SVG_BASE_URL") or self.DEFAULT_STROKE_SVG_BASE_URL
        ).strip()
        self.stroke_media_prefix = (
            self.config.get("ANKI_STROKE_MEDIA_PREFIX") or self.DEFAULT_STROKE_MEDIA_PREFIX
        ).strip() or self.DEFAULT_STROKE_MEDIA_PREFIX
        stroke_auto_sync = self.config.get("ANKI_STROKE_AUTO_SYNC")
        self.stroke_auto_sync = True if stroke_auto_sync is None else bool(stroke_auto_sync)
        try:
            self.stroke_download_timeout = float(
                self.config.get("ANKI_STROKE_DOWNLOAD_TIMEOUT_SEC") or max(self.http_timeout, 20.0)
            )
        except Exception:
            self.stroke_download_timeout = max(self.http_timeout, 20.0)

        # Keep API key out of config when sharing repo.
        self.deepl_api_key = (
            os.environ.get("DEEPL_TOKEN")
            or os.environ.get("DEEPL_AUTH_KEY")
            or os.environ.get("DEEPL_API_KEY")
            or ""
        ).strip()
        enabled = self.config.get("ANKI_ENABLED")
        self.enabled = True if enabled is None else bool(enabled)
        tags = self.config.get("ANKI_TAGS")
        if tags is None:
            self.tags = ["subtitleplayer"]
        elif isinstance(tags, list):
            self.tags = [str(t).strip() for t in tags if str(t).strip()]
        elif isinstance(tags, str):
            self.tags = [t.strip() for t in tags.replace(";", ",").split(",") if t.strip()]
        else:
            self.tags = []

    def is_enabled(self) -> bool:
        return self.enabled

    def ping(self) -> bool:
        try:
            result = self._invoke("version")
            return isinstance(result, int)
        except Exception:
            return False

    def add_from_selection(
        self,
        selection_text: str,
        subtitle_text: str = "",
    ) -> Dict:
        selected = (selection_text or "").strip()
        if not selected:
            raise ValueError("No selected text.")

        subtitle = (subtitle_text or "").strip()
        word_translation = self._translate_word(selected)
        sentence_translation = self._translate_sentence(subtitle)
        full_definition = self._last_jisho_full_definition
        translation_candidates = self._collect_translation_candidates(selected, subtitle)
        self._last_jisho_full_definition = full_definition
        word_provider_used = self._detect_translation_provider(
            chosen=word_translation,
            candidates=translation_candidates.get("word", {}),
            configured=self.word_translate_provider,
        )
        sentence_provider_used = self._detect_translation_provider(
            chosen=sentence_translation,
            candidates=translation_candidates.get("sentence", {}),
            configured=self.sentence_translate_provider,
        )
        fields = self._build_note_fields(
            selected=selected,
            subtitle=subtitle,
            word_translation=word_translation,
            sentence_translation=sentence_translation,
        )

        self._ensure_deck(self.deck_name)
        self._ensure_deck(self.reading_deck)
        self._ensure_deck(self.reverse_deck)

        note = {
            "deckName": self.deck_name,
            "modelName": self.model_name,
            "fields": fields,
            "tags": self.tags,
            "options": {"allowDuplicate": True},
        }
        note_id = self._invoke("addNote", {"note": note})
        routed = self._route_new_cards(note_id)
        # stroke_sync = self._sync_missing_stroke_svgs_for_new_note(
        #     selected_text=selected,
        #     fields=fields,
        # )

        return {
            "note_id": note_id,
            "routed_cards": routed,
            "word_translation": word_translation,
            "sentence_translation": sentence_translation,
            # "stroke_svg_sync": stroke_sync,
            "translation_candidates": translation_candidates,
            "translation_provider_used": {
                "word": word_provider_used,
                "sentence": sentence_provider_used,
            },
            "stroke_svg_sync_fields": fields,
        }


    def sync_missing_stroke_svgs_async(self, selected_text: str, fields: Dict[str, str]):
        import threading

        thread = threading.Thread(
            target=self._sync_missing_stroke_svgs_for_new_note,
            args=(selected_text, fields),
            daemon=True,
        )
        thread.start()

    def _build_note_fields(
        self,
        selected: str,
        subtitle: str,
        word_translation: str,
        sentence_translation: str,
    ) -> Dict[str, str]:
        model_fields = self._get_model_field_names()
        fields: Dict[str, str] = {}

        missing_required = [
            name
            for name in (
                self.add_rubies_to_front_field,
                self.front_field,
                self.back_field,
                self.sentence_ja_field,
                self.sentence_de_field,
                self.add_rubies_to_sentence_ja_field,
            )
            if name not in model_fields
        ]
        if missing_required:
            raise RuntimeError(
                f'Anki model "{self.model_name}" is missing fields: {", ".join(missing_required)}.'
            )

        selected_raw = (selected or "").strip()
        subtitle_raw = (subtitle or "").strip()
        selected_with_rubies = self._to_furigana_brackets(
            selected_raw,
            collapse_inline_reading=False,
            sentence_spacing=False,
        )
        sentence_with_rubies = self._to_furigana_brackets(
            subtitle_raw,
            collapse_inline_reading=True,
            sentence_spacing=True,
        )

        back_value = word_translation
        sentence_de = (sentence_translation).strip()

        fields[self.add_rubies_to_front_field] = selected_raw
        fields[self.front_field] = selected_with_rubies or selected_raw
        fields[self.back_field] = back_value
        fields[self.sentence_ja_field] = sentence_with_rubies
        fields[self.sentence_de_field] = sentence_de
        fields[self.add_rubies_to_sentence_ja_field] = subtitle_raw
        if self.definition_field in model_fields:
            fields[self.definition_field] = self._last_jisho_full_definition

        # Optional media fields stay empty by design; they are filled by capture pipeline later.
        if self.sound_field in model_fields:
            fields[self.sound_field] = ""
        if self.image_field in model_fields:
            fields[self.image_field] = ""

        return fields

    def _to_furigana_brackets(
        self,
        text: str,
        collapse_inline_reading: bool,
        sentence_spacing: bool,
    ) -> str:
        text = (text or "").strip()
        if not text:
            return ""

        segments = self._tokenize_with_reading(
            text=text,
            collapse_inline_reading=collapse_inline_reading,
        )
        return self._segments_to_bracket_text(segments, sentence_spacing=sentence_spacing)

    def _segments_to_bracket_text(
        self,
        segments: List[tuple[str, Optional[str]]],
        sentence_spacing: bool,
    ) -> str:
        out: List[str] = []
        for i, (base, ruby) in enumerate(segments):
            if not base:
                continue
            prev_had_ruby = i > 0 and segments[i - 1][1] is not None
            if (
                ruby
                and self._starts_with_kanji(base)
                and out
                and not out[-1].endswith((" ", "\n", "\t"))
                and not out[-1].endswith(("[", "(", "{", "<", "\u300c", "\u300e"))
                and (
                    prev_had_ruby
                    or (
                        sentence_spacing
                        and not self._has_okurigana_continuation(segments, i)
                    )
                )
            ):
                out.append(" ")
            if ruby:
                out.append(f"{escape(base)}[{escape(ruby)}]")
            else:
                out.append(escape(base))
        return "".join(out)

    def _has_okurigana_continuation(
        self,
        segments: List[tuple[str, Optional[str]]],
        idx: int,
    ) -> bool:
        if idx + 1 >= len(segments):
            return False
        next_base, next_ruby = segments[idx + 1]
        if next_ruby is not None:
            return False
        return bool(next_base) and self._starts_with_kana(next_base)

    def _tokenize_with_reading(
        self,
        text: str,
        collapse_inline_reading: bool,
    ) -> List[tuple[str, Optional[str]]]:
        if self._tagger is None:
            return [(text, None)]
        try:
            tokens = list(self._tagger(text))
        except Exception:
            return [(text, None)]
        if not tokens:
            return [(text, None)]

        surfaces_and_readings: List[tuple[str, str]] = []
        for token in tokens:
            surface = str(token.surface or "")
            if not surface:
                continue
            reading_hira = self._katakana_to_hiragana(self._token_reading(token))
            surfaces_and_readings.append((surface, reading_hira))

        segments: List[tuple[str, Optional[str]]] = []
        i = 0
        n = len(surfaces_and_readings)
        while i < n:
            surface, reading_hira = surfaces_and_readings[i]
            consumed_until = i

            # Convert inline subtitle style like "kanji+reading" into bracket furigana.
            if (
                collapse_inline_reading
                and reading_hira
                and self._contains_kanji(surface)
                and not self._looks_numeric(surface)
            ):
                merged = ""
                j = i + 1
                while j < n:
                    nxt_surface, _ = surfaces_and_readings[j]
                    if self._contains_kanji(nxt_surface):
                        break
                    if not nxt_surface:
                        break
                    merged += nxt_surface
                    if merged == reading_hira:
                        consumed_until = j
                        break
                    if not reading_hira.startswith(merged):
                        break
                    j += 1

            segments.extend(self._split_surface_and_reading(surface, reading_hira))
            i = consumed_until + 1

        return segments or [(text, None)]

    def _token_reading(self, token) -> str:
        feature = getattr(token, "feature", None)
        if feature is None:
            return ""
        for attr in ("kana", "pron", "pronBase", "form"):
            value = getattr(feature, attr, None)
            if value and value != "*":
                return str(value)
        return ""

    def _split_surface_and_reading(self, surface: str, reading_kata: str) -> List[tuple[str, Optional[str]]]:
        if not reading_kata:
            return [(surface, None)]

        reading = self._katakana_to_hiragana(reading_kata)
        if not reading:
            return [(surface, None)]
        if surface == reading:
            return [(surface, None)]
        if self._katakana_to_hiragana(surface) == reading:
            return [(surface, None)]
        if not self._contains_kanji(surface):
            return [(surface, None)]
        if self._looks_numeric(surface):
            return [(surface, None)]
        return split_furigana(surface, reading, self._single_kanji_reading_for_split)

    def _single_kanji_reading_for_split(self, ch: str) -> str:
        if self._tagger is None or not self._is_kanji_char(ch):
            return ""
        try:
            tokens = list(self._tagger(ch))
        except Exception:
            return ""
        if len(tokens) != 1:
            return ""
        token = tokens[0]
        if str(getattr(token, "surface", "") or "") != ch:
            return ""
        return self._katakana_to_hiragana(self._token_reading(token))

    def _split_all_kanji_chars(
        self,
        surface: str,
        reading: str,
    ) -> List[tuple[str, Optional[str]]] | None:
        if not surface or not reading:
            return None
        if not self._is_all_kanji(surface):
            return None

        reading = self._katakana_to_hiragana(reading)
        moras = self._split_moras(reading)
        n_chars = len(surface)
        n_moras = len(moras)

        if n_moras < n_chars:
            return None

        def score_take(length: int, is_last: bool) -> float:
            if length == 1:
                score = 2.0
            elif length == 2:
                score = 5.0
            elif length == 3:
                score = 4.0
            elif length == 4:
                score = 2.0
            else:
                score = 1.0 - 0.5 * (length - 4)

            if is_last and length == 1:
                score -= 4.0
            return score

        def materialize(lengths: tuple[int, ...]) -> List[tuple[str, Optional[str]]]:
            out: List[tuple[str, Optional[str]]] = []
            idx = 0
            for ch, take in zip(surface, lengths):
                chunk = "".join(moras[idx:idx + take])
                if not chunk:
                    return []
                out.append((ch, chunk))
                idx += take
            return out

        # Special handling for 々
        if n_chars == 2 and surface[1] == "々":
            first_len = max(1, n_moras // 2)
            first = "".join(moras[:first_len])
            if not first:
                return None
            return [(surface[0], first), ("々", first)]

        # Strong, explicit rules for 2-kanji compounds
        if n_chars == 2:
            if n_moras == 2:
                return materialize((1, 1))
            if n_moras == 3:
                return materialize((1, 2))
            if n_moras == 4:
                return materialize((2, 2))
            if n_moras == 5:
                return materialize((2, 3))

        @functools.lru_cache(maxsize=None)
        def best(start: int, parts_left: int) -> tuple[float, tuple[int, ...]] | None:
            if parts_left == 1:
                take = n_moras - start
                if take < 1:
                    return None
                return score_take(take, True), (take,)

            best_score: float | None = None
            best_lengths: tuple[int, ...] | None = None

            max_take = n_moras - start - (parts_left - 1)
            if max_take < 1:
                return None

            for take in range(1, max_take + 1):
                rest = best(start + take, parts_left - 1)
                if rest is None:
                    continue

                rest_score, rest_lengths = rest
                score = score_take(take, False) + rest_score

                if rest_lengths:
                    if take <= rest_lengths[0]:
                        score += 0.8
                    else:
                        score -= 0.8

                if best_score is None or score > best_score:
                    best_score = score
                    best_lengths = (take,) + rest_lengths

            if best_score is None or best_lengths is None:
                return None
            return best_score, best_lengths

        result = best(0, n_chars)
        if result is None:
            return None

        _, lengths = result
        if len(lengths) != n_chars:
            return None

        return materialize(lengths)

    def _split_moras(self, reading: str) -> List[str]:
        return split_moras(reading)

    def _katakana_to_hiragana(self, text: str) -> str:
        out = []
        for ch in text:
            code = ord(ch)
            if 0x30A1 <= code <= 0x30F6:
                out.append(chr(code - 0x60))
            else:
                out.append(ch)
        return "".join(out)

    def _contains_kanji(self, text: str) -> bool:
        return any(self._is_kanji_char(ch) for ch in text or "")

    def _starts_with_kanji(self, text: str) -> bool:
        return bool(text) and self._is_kanji_char(text[0])

    def _is_all_kanji(self, text: str) -> bool:
        return bool(text) and all(self._is_kanji_char(ch) for ch in text)

    def _starts_with_kana(self, text: str) -> bool:
        if not text:
            return False
        code = ord(text[0])
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)

    def _looks_numeric(self, text: str) -> bool:
        kansuji = "\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341\u767e\u5343\u4e07\u5104\u5146\u3007\u96f6"
        full_width_digits = "".join(chr(cp) for cp in range(0xFF10, 0xFF1A))
        number_chars = set("0123456789" + full_width_digits + kansuji)
        cleaned = "".join(ch for ch in text if ch.strip())
        return bool(cleaned) and all(ch in number_chars for ch in cleaned)

    def _translate_word(self, text: str) -> str:
        self._last_jisho_full_definition = ""
        text = (text or "").strip()
        if not text:
            return ""
        cached = self._word_translation_cache.get(text)
        if cached is not None:
            self._last_jisho_full_definition = self._word_definition_cache.get(text, "")
            return cached
        
        translation = self._translate_word_with_jisho(text)
        if not translation:
            translation = self._translate_google(text, source_lang="ja", target_lang=self.word_target_lang)
        self._word_translation_cache[text] = translation
        self._word_definition_cache[text] = self._last_jisho_full_definition
        return translation

    def _translate_word_with_jisho(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        entries = self._fetch_jisho_entries(text)
        definitions = self._extract_jisho_translation(text, entries)
        # definitions is now a list of strings (may be empty)
        if not definitions:
            return ""
        self._last_jisho_full_definition = ", ".join(definitions)
        summary_en = ", ".join(definitions[:3])
        if self.word_target_lang.lower() == "en":
            return summary_en

        translated = self._translate_deepl(
            summary_en,
            source_lang="en",
            target_lang=self.word_target_lang,
        )
        if not translated:
            translated = self._translate_google(
                summary_en,
                source_lang="en",
                target_lang=self.word_target_lang,
            )
        return translated or summary_en

    def _fetch_jisho_entries(self, text: str) -> List[Dict]:
        text = (text or "").strip()
        if not text:
            return []
        cached = self._jisho_entries_cache.get(text)
        if cached is not None:
            return cached

        entries: List[Dict] = []
        try:
            r = requests.get(
                self.jisho_url,
                params={"keyword": text},
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            payload = r.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, list):
                entries = data
        except Exception:
            entries = []

        self._jisho_entries_cache[text] = entries
        return entries

    def _extract_jisho_translation(self, query: str, entries: List[Dict]) -> List[str]:
        if not entries:
            return []

        best = entries[0]
        best_score = -1
        for entry in entries:
            score = 0
            for jp in entry.get("japanese") or []:
                word = str(jp.get("word") or "")
                reading = str(jp.get("reading") or "")
                if word == query:
                    score += 100
                if reading == query:
                    score += 80
                if query and query in word:
                    score += 30
                if query and query in reading:
                    score += 20
            if score > best_score:
                best = entry
                best_score = score

        definitions: List[str] = []
        for sense in best.get("senses") or []:
            for item in sense.get("english_definitions") or []:
                gloss = str(item).strip()
                if gloss and gloss not in definitions:
                    definitions.append(gloss)
            if len(definitions) >= 6:
                break
        return definitions

    def _translate_sentence(self, text: str) -> str:
        text = self._normalize_translation_input(text)
        if not text:
            return ""

        cached = self._sentence_translation_cache.get(text)
        if cached is not None:
            return cached

        provider = self.sentence_translate_provider
        translated = ""
        if provider == "deepl":
            translated = self._translate_deepl(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
            if not translated:
                translated = self._translate_google(
                    text,
                    source_lang="ja",
                    target_lang=self.sentence_target_lang,
                )
        elif provider == "google":
            translated = self._translate_google(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
        else:
            translated = self._translate_deepl(
                text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            )
            if not translated:
                translated = self._translate_google(
                    text,
                    source_lang="ja",
                    target_lang=self.sentence_target_lang,
                )

        self._sentence_translation_cache[text] = translated
        return translated

    def _normalize_translation_input(self, text: str) -> str:
        value = (text or "").strip()
        if not value:
            return ""
        value = value.replace("\u3000", " ")
        # Translate subtitle lines as one sentence block.
        value = re.sub(r"\s*\n+\s*", " ", value)
        value = re.sub(r"[ \t]+", " ", value)
        return value.strip()

    def _collect_translation_candidates(self, selected: str, subtitle: str) -> Dict[str, Dict[str, str]]:
        selected_text = (selected or "").strip()
        subtitle_text = self._normalize_translation_input(subtitle)

        word_candidates = {
            "jisho": self._translate_word_with_jisho(selected_text) if selected_text else "",
            "google": self._translate_google(
                selected_text,
                source_lang="ja",
                target_lang=self.word_target_lang,
            ) if selected_text else "",
        }

        sentence_candidates = {
            "deepl": (
                self._sentence_translation_cache.get(subtitle_text)
                or self._translate_deepl(
                    subtitle_text, source_lang="ja", target_lang=self.sentence_target_lang
                )
                if subtitle_text
                else ""
            ),
            "google": self._translate_google(
                subtitle_text,
                source_lang="ja",
                target_lang=self.sentence_target_lang,
            ) if subtitle_text else "",
        }

        return {
            "word": word_candidates,
            "sentence": sentence_candidates,
        }

    def _detect_translation_provider(
        self,
        chosen: str,
        candidates: Dict[str, str],
        configured: str,
    ) -> str:
        selected = (chosen or "").strip()
        if not selected:
            return "none"
        configured_key = (configured or "").strip().lower()
        if configured_key and selected == (candidates.get(configured_key) or "").strip():
            return configured_key
        for name, value in candidates.items():
            if selected == (value or "").strip():
                return name
        return "fallback/unknown"

    def _translate_google(self, text: str, source_lang: str, target_lang: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        try:
            r = requests.get(
                self.google_translate_url,
                params={
                    "client": "gtx",
                    "sl": source_lang,
                    "tl": target_lang,
                    "dt": "t",
                    "q": text,
                },
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            payload = r.json()
            translated = ""
            chunks = payload[0] if isinstance(payload, list) and payload else []
            for chunk in chunks:
                if isinstance(chunk, list) and chunk:
                    translated += str(chunk[0] or "")
            return translated.strip()
        except Exception:
            return ""

    def _translate_deepl(self, text: str, source_lang: str, target_lang: str) -> str:
        text = (text or "").strip()
        if not text or not self.deepl_api_key:
            return ""

        source = self._normalize_deepl_source_lang(source_lang)
        target = self._normalize_deepl_target_lang(target_lang)
        if not target:
            return ""

        payload = {
            "text": text,
            "target_lang": target,
        }
        if source:
            payload["source_lang"] = source

        try:
            r = requests.post(
                self.deepl_translate_url,
                headers={"Authorization": f"DeepL-Auth-Key {self.deepl_api_key}"},
                data=payload,
                timeout=self.http_timeout,
            )
            r.raise_for_status()
            data = r.json()
            translations = data.get("translations") or []
            if not translations:
                return ""
            return str((translations[0] or {}).get("text") or "").strip()
        except Exception:
            return ""

    def _normalize_deepl_source_lang(self, lang: str) -> str:
        code = str(lang or "").strip().replace("_", "-").upper()
        if not code:
            return ""
        if "-" in code:
            code = code.split("-", 1)[0]
        if len(code) > 2:
            code = code[:2]
        return code

    def _normalize_deepl_target_lang(self, lang: str) -> str:
        code = str(lang or "").strip().replace("_", "-").upper()
        if not code:
            return ""
        allowed = {
            "AR", "BG", "CS", "DA", "DE", "EL", "EN", "EN-GB", "EN-US", "ES",
            "ET", "FI", "FR", "HU", "ID", "IT", "JA", "KO", "LT", "LV", "NB",
            "NL", "PL", "PT", "PT-BR", "PT-PT", "RO", "RU", "SK", "SL", "SV",
            "TR", "UK", "ZH", "ZH-HANS", "ZH-HANT",
        }
        if code in allowed:
            return code
        if "-" in code:
            base = code.split("-", 1)[0]
            if base in allowed:
                return base
        if len(code) >= 2:
            short = code[:2]
            if short in allowed:
                return short
        return ""

    def _route_new_cards(self, note_id: int) -> Dict[str, List[int]]:
        routed = {"reading": [], "reverse": [], "unrouted": []}
        if not note_id:
            return routed

        card_ids = self._invoke("findCards", {"query": f"nid:{note_id}"}) or []
        if not card_ids:
            return routed

        info = self._invoke("cardsInfo", {"cards": card_ids}) or []
        to_reading = []
        to_reverse = []
        for card in info:
            card_id = card.get("cardId")
            ord_index = card.get("ord")
            if card_id is None:
                continue
            if ord_index == 0:
                to_reading.append(card_id)
            elif ord_index == 1:
                to_reverse.append(card_id)
            else:
                routed["unrouted"].append(card_id)

        if to_reading:
            self._invoke("changeDeck", {"cards": to_reading, "deck": self.reading_deck})
            routed["reading"].extend(to_reading)
        if to_reverse:
            self._invoke("changeDeck", {"cards": to_reverse, "deck": self.reverse_deck})
            routed["reverse"].extend(to_reverse)
        return routed

    def _ensure_deck(self, deck_name: str) -> None:
        if not deck_name:
            return
        self._invoke("createDeck", {"deck": deck_name})

    def _get_model_field_names(self) -> set[str]:
        if self._model_fields_cache is None:
            names = self._invoke("modelFieldNames", {"modelName": self.model_name}) or []
            self._model_fields_cache = set(str(name) for name in names)
        return self._model_fields_cache

    def _invoke(self, action: str, params: Optional[Dict] = None):
        payload = {
            "action": action,
            "version": 6,
            "params": params or {},
        }
        response = requests.post(self.url, json=payload, timeout=self.http_timeout)
        response.raise_for_status()
        data = response.json()
        if data.get("error"):
            raise RuntimeError(str(data["error"]))
        return data.get("result")

    def _sync_missing_stroke_svgs_for_new_note(self, selected_text: str, fields: Dict[str, str]) -> Dict[str, int]:
        result = {
            "enabled": int(bool(self.stroke_auto_sync)),
            "required": 0,
            "missing": 0,
            "uploaded": 0,
            "failed": 0,
        }
        if not self.stroke_auto_sync:
            return result

        texts = [
            selected_text,
            fields.get(self.add_rubies_to_front_field, ""),
            fields.get(self.front_field, ""),
        ]
        chars = self._extract_kanji_chars_for_strokes(texts)
        result["required"] = len(chars)
        if not chars:
            return result

        for kanji_char in chars:
            filename = self._stroke_media_filename(kanji_char)
            if self._stroke_media_exists(filename):
                continue
            result["missing"] += 1

            if kanji_char in self._stroke_sync_failed_cache:
                result["failed"] += 1
                continue

            try:
                raw_svg = self._download_stroke_svg(kanji_char)
                self._store_media_file(filename, raw_svg)
                self._stroke_media_exists_cache[filename] = True
                result["uploaded"] += 1
            except Exception:
                self._stroke_sync_failed_cache.add(kanji_char)
                result["failed"] += 1
        return result

    def _extract_kanji_chars_for_strokes(self, texts: List[str]) -> List[str]:
        seen: set[str] = set()
        out: List[str] = []
        for raw in texts or []:
            value = str(raw or "")
            value = re.sub(r"<[^>]+>", "", value)
            value = re.sub(r"\[[^\[\]]+\]", "", value)
            for ch in value:
                if not self._is_kanji_char(ch):
                    continue
                if ch in seen:
                    continue
                seen.add(ch)
                out.append(ch)
        return out

    def _is_kanji_char(self, ch: str) -> bool:
        if not ch:
            return False
        code = ord(ch)
        return (
            code == 0x3005
            or 0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
            or 0x20000 <= code <= 0x2A6DF
            or 0x2A700 <= code <= 0x2B73F
            or 0x2B740 <= code <= 0x2B81F
            or 0x2B820 <= code <= 0x2CEAF
        )

    def _stroke_media_filename(self, kanji_char: str) -> str:
        prefix = re.sub(r"[^a-zA-Z0-9_.-]", "_", self.stroke_media_prefix or self.DEFAULT_STROKE_MEDIA_PREFIX)
        return f"{prefix}{ord(kanji_char):05x}.svg"

    def _stroke_media_exists(self, filename: str) -> bool:
        cached = self._stroke_media_exists_cache.get(filename)
        if cached is not None:
            return cached
        try:
            result = self._invoke("retrieveMediaFile", {"filename": filename})
            exists = isinstance(result, str) and bool(result)
        except Exception:
            exists = False
        self._stroke_media_exists_cache[filename] = exists
        return exists

    def _stroke_download_candidates(self, kanji_char: str) -> List[str]:
        base = (self.stroke_svg_base_url or self.DEFAULT_STROKE_SVG_BASE_URL).strip().rstrip("/")
        code = ord(kanji_char)
        hex_candidates = [
            f"{code:05x}",
            f"{code:04x}",
            f"{code:x}",
            f"{code:05X}",
            f"{code:04X}",
            f"{code:X}",
        ]
        return [f"{base}/{code_hex}.svg" for code_hex in hex_candidates]

    def _download_stroke_svg(self, kanji_char: str) -> bytes:
        last_error = None
        for url in self._stroke_download_candidates(kanji_char):
            try:
                response = requests.get(url, timeout=self.stroke_download_timeout)
                response.raise_for_status()
                if response.content:
                    return response.content
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"No stroke SVG URL worked for {kanji_char}") from last_error

    def _store_media_file(self, filename: str, raw: bytes) -> None:
        encoded = base64.b64encode(raw).decode("ascii")
        self._invoke("storeMediaFile", {"filename": filename, "data": encoded})
import os
import re
import subprocess
import sys
from typing import List, Optional, Tuple

try:
    from SubtitlePlayer.furigana_splitter import split_furigana
except ImportError:
    try:
        from furigana_splitter import split_furigana
    except ImportError:
        _package_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if _package_dir not in sys.path:
            sys.path.insert(0, _package_dir)
        from furigana_splitter import split_furigana

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

KAKASI_ARGS = ["-isjis", "-osjis", "-u", "-JH", "-KH"]
MECAB_ARGS = ["--node-format=%m[%f[7]] ", "--eos-format=\n", "--unk-format=%m[] "]

DEFAULT_SUPPORT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "anki_reading_support")
)

BRACKET_RUBY_RE = re.compile(r"([^\[\]]+)\[([^\[\]]+)\]")


def _is_kanji_char(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (
        (code == 0x3005)  # 々 (ideographic iteration mark) should follow kanji ruby too
        or (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0xF900 <= code <= 0xFAFF)
        or (0x20000 <= code <= 0x2A6DF)
        or (0x2A700 <= code <= 0x2B73F)
        or (0x2B740 <= code <= 0x2B81F)
        or (0x2B820 <= code <= 0x2CEAF)
    )


def _is_kana_char(ch: str) -> bool:
    if not ch:
        return False
    code = ord(ch)
    return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)


def _kata_to_hira(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _split_kanji_kana_core(base: str, ruby: str) -> Optional[List[Tuple[str, Optional[str]]]]:
    if not base or not ruby:
        return None
    has_kanji = any(_is_kanji_char(ch) for ch in base)
    has_kana = any(_is_kana_char(ch) for ch in base)
    if not (has_kanji and has_kana):
        return None

    reading = _kata_to_hira(ruby)
    r_pos = 0
    out: List[Tuple[str, Optional[str]]] = []
    i = 0
    n = len(base)
    while i < n:
        ch = base[i]
        if _is_kana_char(ch):
            hira = _kata_to_hira(ch)
            if reading.startswith(hira, r_pos):
                r_pos += len(hira)
            out.append((ch, None))
            i += 1
            continue

        if _is_kanji_char(ch):
            j = i
            while j < n and _is_kanji_char(base[j]):
                j += 1
            kanji_chunk = base[i:j]

            next_kana = None
            k = j
            while k < n:
                if _is_kana_char(base[k]):
                    next_kana = _kata_to_hira(base[k])
                    break
                if _is_kanji_char(base[k]):
                    break
                k += 1

            if next_kana:
                idx = reading.find(next_kana, r_pos)
                if idx <= r_pos:
                    ruby_chunk = reading[r_pos:]
                    r_pos = len(reading)
                else:
                    ruby_chunk = reading[r_pos:idx]
                    r_pos = idx
            else:
                ruby_chunk = reading[r_pos:]
                r_pos = len(reading)

            if ruby_chunk:
                out.append((kanji_chunk, ruby_chunk))
            else:
                out.append((kanji_chunk, None))
            i = j
            continue

        out.append((ch, None))
        i += 1

    if r_pos < len(reading):
        for idx in range(len(out) - 1, -1, -1):
            base_chunk, ruby_chunk = out[idx]
            if ruby_chunk:
                out[idx] = (base_chunk, ruby_chunk + reading[r_pos:])
                break
        else:
            return None
    return out or None


def _split_ruby_base(base: str, ruby: str, single_kanji_reader=None) -> List[Tuple[str, Optional[str]]]:
    if not base:
        return []
    if not ruby:
        return [(base, None)]
    return split_furigana(base, ruby, single_kanji_reader)


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def escape_text(text: str) -> str:
    text = (text or "").replace("\n", " ")
    text = text.replace("\uff5e", "~")
    text = re.sub(r"<br( /)?>", "---newline---", text)
    text = strip_html(text)
    text = text.replace("---newline---", "<br>")
    return text


def munge_for_platform(popen: List[str]) -> List[str]:
    if IS_WIN:
        popen = [os.path.normpath(x) for x in popen]
        popen[0] += ".exe"
    elif not IS_MAC:
        popen[0] += ".lin"
    return popen


def _startupinfo():
    if not IS_WIN:
        return None
    si = subprocess.STARTUPINFO()
    try:
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    except Exception:
        si.dwFlags |= subprocess._subprocess.STARTF_USESHOWWINDOW
    return si


class KakasiController:
    def __init__(self, support_dir: str) -> None:
        self.support_dir = support_dir
        self.kakasi: subprocess.Popen | None = None
        self.kakasi_cmd: List[str] | None = None

    def setup(self) -> None:
        self.kakasi_cmd = munge_for_platform(
            [os.path.join(self.support_dir, "kakasi")] + KAKASI_ARGS
        )
        os.environ["ITAIJIDICT"] = os.path.join(self.support_dir, "itaijidict")
        os.environ["KANWADICT"] = os.path.join(self.support_dir, "kanwadict")
        if not IS_WIN:
            os.chmod(self.kakasi_cmd[0], 0o755)

    def ensure_open(self) -> None:
        if not self.kakasi:
            self.setup()
            try:
                self.kakasi = subprocess.Popen(
                    self.kakasi_cmd,
                    bufsize=-1,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    startupinfo=_startupinfo(),
                )
            except OSError as exc:
                raise Exception("Please install kakasi") from exc

    def reading(self, expr: str) -> str:
        self.ensure_open()
        expr = escape_text(expr)
        assert self.kakasi
        self.kakasi.stdin.write(expr.encode("sjis", "ignore") + b"\n")
        self.kakasi.stdin.flush()
        res = self.kakasi.stdout.readline().rstrip(b"\r\n").decode("sjis", "replace")
        return res


class MecabController:
    def __init__(self, support_dir: str, kakasi: KakasiController) -> None:
        self.support_dir = support_dir
        self.kakasi = kakasi
        self.mecab: subprocess.Popen | None = None
        self.mecab_cmd: List[str] | None = None

    def setup(self) -> None:
        self.mecab_cmd = munge_for_platform(
            [os.path.join(self.support_dir, "mecab")]
            + MECAB_ARGS
            + [
                "-d",
                self.support_dir,
                "-r",
                os.path.join(self.support_dir, "mecabrc"),
                "-u",
                os.path.join(self.support_dir, "user_dic.dic"),
            ]
        )
        os.environ["DYLD_LIBRARY_PATH"] = self.support_dir
        os.environ["LD_LIBRARY_PATH"] = self.support_dir
        if not IS_WIN:
            os.chmod(self.mecab_cmd[0], 0o755)

    def ensure_open(self) -> None:
        if not self.mecab:
            self.setup()
            try:
                self.mecab = subprocess.Popen(
                    self.mecab_cmd,
                    bufsize=-1,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    startupinfo=_startupinfo(),
                )
            except OSError as exc:
                raise Exception(
                    "Please ensure your system has 64 bit binary support."
                ) from exc

    def reading(self, expr: str) -> str:
        self.ensure_open()
        expr = escape_text(expr)
        assert self.mecab
        self.mecab.stdin.write(expr.encode("utf-8", "ignore") + b"\n")
        self.mecab.stdin.flush()
        expr = self.mecab.stdout.readline().rstrip(b"\r\n").decode("utf-8", "replace")
        out = []
        for node in expr.split(" "):
            if not node:
                break
            m = re.match(r"(.+)\[(.*)\]", node)
            if not m:
                sys.stderr.write(
                    "Unexpected output from mecab: {}\n".format(repr(expr))
                )
                return ""

            (kanji, reading) = m.groups()
            if kanji == reading or not reading:
                out.append(kanji)
                continue
            if kanji == self.kakasi.reading(reading):
                out.append(kanji)
                continue
            reading = self.kakasi.reading(reading)
            if reading == kanji:
                out.append(kanji)
                continue
            if kanji in "一二三四五六七八九十０１２３４５６７８９":
                out.append(kanji)
                continue

            place_l = 0
            place_r = 0
            for i in range(1, len(kanji)):
                if kanji[-i] != reading[-i]:
                    break
                place_r = i
            for i in range(0, len(kanji) - 1):
                if kanji[i] != reading[i]:
                    break
                place_l = i + 1

            if place_l == 0:
                if place_r == 0:
                    out.append(" %s[%s]" % (kanji, reading))
                else:
                    out.append(
                        " %s[%s]%s"
                        % (kanji[:-place_r], reading[:-place_r], reading[-place_r:])
                    )
            else:
                if place_r == 0:
                    out.append(
                        "%s %s[%s]"
                        % (reading[:place_l], kanji[place_l:], reading[place_l:])
                    )
                else:
                    out.append(
                        "%s %s[%s]%s"
                        % (
                            reading[:place_l],
                            kanji[place_l:-place_r],
                            reading[place_l:-place_r],
                            reading[-place_r:],
                        )
                    )

        fin = ""
        for c, s in enumerate(out):
            if c < len(out) - 1 and re.match(r"^[A-Za-z0-9]+$", out[c + 1]):
                s += " "
            fin += s
        return fin.strip().replace("< br>", "<br>")


class AddonRubyGenerator:
    def __init__(self, support_dir: Optional[str] = None) -> None:
        self.support_dir = support_dir or DEFAULT_SUPPORT_DIR
        self.kakasi = KakasiController(self.support_dir)
        self.mecab = MecabController(self.support_dir, self.kakasi)
        self._single_kanji_reading_cache: dict[str, str] = {}

    def reading(self, text: str) -> str:
        return self.mecab.reading(text)

    def single_kanji_reading(self, ch: str) -> str:
        if ch in self._single_kanji_reading_cache:
            return self._single_kanji_reading_cache[ch]
        if not _is_kanji_char(ch) or ch == "々":
            return ""
        raw = self.reading(ch).strip()
        match = BRACKET_RUBY_RE.fullmatch(raw)
        if match and match.group(1) == ch:
            value = _kata_to_hira(match.group(2))
        elif raw and raw != ch and "[" not in raw and "]" not in raw:
            value = _kata_to_hira(raw)
        else:
            value = ""
        self._single_kanji_reading_cache[ch] = value
        return value

    def segments(self, text: str) -> List[Tuple[str, Optional[str]]]:
        ruby_text = self.reading(text)
        return bracket_text_to_segments(ruby_text, self.single_kanji_reading)


def bracket_text_to_segments(text: str, single_kanji_reader=None) -> List[Tuple[str, Optional[str]]]:
    segments: List[Tuple[str, Optional[str]]] = []
    last = 0
    for m in BRACKET_RUBY_RE.finditer(text or ""):
        plain = text[last:m.start()]
        if plain:
            segments.append((plain, None))
        base = m.group(1)
        ruby = m.group(2)
        if base:
            for sub_base, sub_ruby in _split_ruby_base(base, ruby, single_kanji_reader):
                if sub_ruby is not None and not any(_is_kanji_char(ch) for ch in sub_base):
                    segments.append((sub_base, None))
                else:
                    segments.append((sub_base, sub_ruby))
        last = m.end()
    tail = (text or "")[last:]
    if tail:
        segments.append((tail, None))
    return segments
"""
Simple JSON-backed configuration wrapper.

Reads/writes `config.json` and exposes get/set helpers.
"""

import json
import os

class ConfigManager:
    def __init__(self, path="config.json"):
        self.path = path
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Config file not found: {self.path}")
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                self.config = json.load(f)
            self._key_order = list(self.config.keys())
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse config file: {e}")

    def get(self, key):
        return self.config.get(key)

    def set(self, key, value):
        if key not in self.config:
            self._key_order.append(key)
        self.config[key] = value
        self._save()

    def set_many(self, updates):
        if not isinstance(updates, dict) or not updates:
            return
        changed = False
        for key, value in updates.items():
            if key not in self.config:
                self._key_order.append(key)
            if self.config.get(key) != value:
                self.config[key] = value
                changed = True
        if changed:
            self._save()
         
    def _save(self):
        ordered = {}
        for key in list(getattr(self, "_key_order", [])):
            if key in self.config:
                ordered[key] = self.config[key]
        for key, value in self.config.items():
            if key not in ordered:
                ordered[key] = value
                self._key_order.append(key)
        self.config = ordered
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)


"""
SubtitleRenderer draws parsed subtitle segments onto the overlay canvas.

Design:
- Draw each subtitle segment as a whole base string instead of per-character canvas items.
- Draw ruby as a whole string above the segment.
- Keep hover-ruby behavior by storing per-segment hover regions.
- Cache wrapping, measurements, and layout.
"""

from __future__ import annotations

import logging
import re
import time
import tkinter as tk
from tkinter import font as tkFont
from typing import Dict, List, Optional, Tuple

from model.config_manager import ConfigManager
from view.subtitle_overlay import SubtitleOverlayUI

logger = logging.getLogger(__name__)

Segment = Tuple[str, Optional[str]]
FrozenSegments = Tuple[Segment, ...]
WrappedLines = Tuple[Tuple[Segment, ...], ...]


class SubtitleRenderer:
    def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
        self.config = config
        self.canvas = canvas

        self._hover_regions: List[dict] = []
        self._hover_active_region = None

        self.font: Optional[tkFont.Font] = None
        self.ruby_font: Optional[tkFont.Font] = None
        self._font_settings = None

        self.hover_ruby_enabled = False
        self.color = "white"
        self.glow_color = "black"
        self.glow_radius = 4
        self.ruby_glow_radius = 3
        self.line_height = 0
        self.ruby_height = 0

        self._measure_cache: Dict[Tuple[int, str], int] = {}
        self._outline_offset_cache: Dict[int, List[Tuple[int, int]]] = {}
        self._split_cache: Dict[Tuple[str, int, int], Tuple[str, ...]] = {}
        self._wrap_cache: Dict[
            Tuple[FrozenSegments, int, int, int, int, int],
            WrappedLines,
        ] = {}
        self._layout_cache: Dict[Tuple, Dict[str, WrappedLines]] = {}
        self._last_overlay_width: Optional[int] = None
        self._layout_cache_hits = 0
        self._layout_cache_misses = 0

        self._timing_enabled = False
        self._timing_data = {
            "render_subtitle_time": 0.0,
            "font_creation_time": 0.0,
            "wrap_segments_time": 0.0,
            "split_text_to_fit_time": 0.0,
            "font_measure_time": 0.0,
            "draw_outlined_text_time": 0.0,
            "render_count": 0,
        }

    def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
        start = time.perf_counter()
        if self._timing_enabled:
            self._timing_data["render_count"] += 1

        try:
            self._refresh_fonts_if_needed()

            self._hover_regions = []
            self._hover_active_region = None

            base_height = int(self.ruby_height * 2 + self.line_height * 2)
            y_ruby_top = self.ruby_height // 2
            y_base1 = self.ruby_height + self.line_height // 2

            wrap_limit_px = self._get_wrap_limit_px()

            if int(overlay.max_w) != int(self._last_overlay_width or 0):
                self._layout_cache.clear()
                self._last_overlay_width = int(overlay.max_w)

            layout_cache_key = (
                self._freeze_segments(top_segments),
                self._freeze_segments(bottom_segments),
                int(overlay.max_w),
                self._font_settings,
                int(wrap_limit_px) if wrap_limit_px else -1,
            )

            cached_layout = self._layout_cache.get(layout_cache_key)
            if cached_layout is not None:
                self._layout_cache_hits += 1
                wrapped_top = cached_layout["wrapped_top"]
                wrapped_bottom = cached_layout["wrapped_bottom"]
            else:
                self._layout_cache_misses += 1
                wrapped_top = self._wrap_segments(
                    top_segments, overlay.max_w, line_limit_px=wrap_limit_px
                ) if top_segments else ()
                wrapped_bottom = self._wrap_segments(
                    bottom_segments, overlay.max_w, line_limit_px=wrap_limit_px
                ) if bottom_segments else ()
                self._layout_cache[layout_cache_key] = {
                    "wrapped_top": wrapped_top,
                    "wrapped_bottom": wrapped_bottom,
                }

            lines = wrapped_top + wrapped_bottom

            if not lines:
                self.canvas.delete("all")
                self._finish_hover_bindings()
                return

            if len(lines) <= 2:
                try:
                    if int(overlay.max_h) != int(base_height):
                        overlay.update_geometry(int(overlay.max_w), int(base_height))
                except Exception:
                    pass
            else:
                block_h = self.line_height + self.ruby_height
                needed_h = int(block_h * len(lines))

                try:
                    sh = overlay.root.winfo_vrootheight()
                    max_h_allowed = max(80, int(sh) - 40)
                except Exception:
                    max_h_allowed = overlay.max_h

                target_h = max(int(base_height), needed_h)
                target_h = min(target_h, max_h_allowed)

                if int(overlay.max_h) != int(target_h):
                    overlay.update_geometry(int(overlay.max_w), int(target_h))

            self.canvas.delete("all")
            self._render_subtitle_lines(lines, y_ruby_top, y_base1, overlay)
            self._finish_hover_bindings()

        finally:
            if self._timing_enabled:
                self._timing_data["render_subtitle_time"] += time.perf_counter() - start

    def _freeze_segments(self, segments) -> FrozenSegments:
        if not segments:
            return ()
        frozen: List[Segment] = []
        for base, ruby in segments:
            frozen.append((base or "", ruby))
        return tuple(frozen)

    def _get_wrap_limit_px(self) -> Optional[int]:
        wrap_limit_px = None
        try:
            v = self.config.get("SUBTITLE_WRAP_LIMIT_PX")
            if v is not None:
                v = int(v)
                if v > 0:
                    wrap_limit_px = v
        except Exception:
            wrap_limit_px = None
        return wrap_limit_px

    def _render_subtitle_lines(self, lines, y_ruby_top, y_base1, overlay: SubtitleOverlayUI) -> None:
        if len(lines) == 1:
            self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
            return

        if len(lines) == 2:
            block_h = self.line_height + self.ruby_height
            base2_start = block_h
            y_base2 = base2_start + self.line_height // 2
            y_ruby_bot = base2_start + self.line_height + self.ruby_height // 2
            self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
            self._render_line(lines[1], y_ruby_bot, y_base2, overlay.max_w)
            return

        block_h = self.line_height + self.ruby_height
        avail_h = int(overlay.max_h)
        total_h = min(int(block_h * len(lines)), avail_h)
        top_offset = max(0, int((avail_h - total_h) / 2))

        for i, segs in enumerate(lines):
            block_y = top_offset + int(i * block_h)
            if i == len(lines) - 1:
                base_y = block_y + self.line_height // 2
                ruby_y = block_y + self.line_height + self.ruby_height // 2
            else:
                ruby_y = block_y + self.ruby_height // 2
                base_y = block_y + self.ruby_height + self.line_height // 2
            self._render_line(segs, ruby_y, base_y, overlay.max_w)

    def _render_line(self, segments, ruby_y, base_y, max_width):
        if not segments:
            return

        seg_meta = []
        total_w = 0

        for base, ruby in segments:
            base_w = self._measure_text(self.font, base)
            if ruby:
                ruby_w = self._measure_text(self.ruby_font, ruby)
                seg_w = max(base_w, ruby_w)
            else:
                ruby_w = 0
                seg_w = base_w
            seg_meta.append((base, ruby, base_w, ruby_w, seg_w))
            total_w += seg_w

        cur_x = (max_width - total_w) / 2

        for base, ruby, base_w, ruby_w, seg_w in seg_meta:
            cx = cur_x + seg_w / 2

            if ruby and not self.hover_ruby_enabled:
                self._draw_ruby_text(ruby, base_w, ruby_w, cx, ruby_y)
            elif ruby and self.hover_ruby_enabled and self._contains_kanji(base):
                self._hover_regions.append(
                    {
                        "bbox": (
                            cur_x,
                            base_y - (self.line_height / 2),
                            cur_x + seg_w,
                            base_y + (self.line_height / 2),
                        ),
                        "ruby": ruby,
                        "base_w": base_w,
                        "ruby_w": ruby_w,
                        "cx": cx,
                        "ruby_y": ruby_y,
                    }
                )

            self._draw_outlined_text(
                self.canvas,
                cx,
                base_y,
                base,
                self.font,
                fill=self.color,
                outline=self.glow_color,
                thickness=self.glow_radius,
            )
            cur_x += seg_w

    def _refresh_fonts_if_needed(self) -> None:
        font_family = self.config.get("SUBTITLE_FONT")
        font_size = int(self.config.get("SUBTITLE_FONT_SIZE") or 12)
        hover_ruby_enabled = self.config.get("SUBTITLE_HOVER_RUBY")
        color = self.config.get("SUBTITLE_COLOR")
        glow_color = str(self.config.get("GLOW_COLOR") or "black")

        try:
            glow_radius = int(float(self.config.get("GLOW_RADIUS") or 10))
        except Exception:
            glow_radius = 10
        glow_radius = max(0, min(glow_radius, 20))

        settings = (font_family, font_size, hover_ruby_enabled, color, glow_color, glow_radius)

        if settings != self._font_settings or self.font is None or self.ruby_font is None:
            font_start = time.perf_counter()
            self.font = tkFont.Font(family=font_family, size=font_size, weight="bold")
            ruby_size = max(1, int(round(int(self.font.actual("size")) * 0.6)))
            self.ruby_font = tkFont.Font(
                family=self.font.actual("family"),
                size=ruby_size,
                weight="bold",
            )

            self.hover_ruby_enabled = hover_ruby_enabled
            self.color = str(color or "white")
            self.glow_color = glow_color
            self.glow_radius = glow_radius
            self.ruby_glow_radius = max(0, int(round(self.glow_radius * 0.6667)))

            self.line_height = int(self.font.metrics("linespace"))
            self.ruby_height = int(self.line_height * 0.6)

            self._font_settings = settings
            self._invalidate_measure_caches()

            if self._timing_enabled:
                self._timing_data["font_creation_time"] += time.perf_counter() - font_start

    def _invalidate_measure_caches(self) -> None:
        self._measure_cache.clear()
        self._outline_offset_cache.clear()
        self._split_cache.clear()
        self._wrap_cache.clear()
        self._layout_cache.clear()
        self._last_overlay_width = None

    def _wrap_segments(
        self,
        segments: List[Tuple[str, Optional[str]]],
        max_width: int,
        padding: int = 40,
        line_limit_px: Optional[int] = None,
    ) -> WrappedLines:
        cache_key = (
            self._freeze_segments(segments),
            int(max_width),
            int(padding),
            int(line_limit_px) if line_limit_px is not None else -1,
            id(self.font),
            id(self.ruby_font),
        )
        cached = self._wrap_cache.get(cache_key)
        if cached is not None:
            return cached

        start = time.perf_counter()
        try:
            if not segments:
                return ()

            limit = max(60, int(max_width) - int(padding) * 2)
            if line_limit_px is not None:
                try:
                    limit = max(60, min(limit, int(line_limit_px)))
                except Exception:
                    pass

            lines: List[List[Segment]] = []
            cur: List[Segment] = []
            cur_w = 0

            def _flush():
                nonlocal cur, cur_w
                if cur:
                    lines.append(cur)
                cur = []
                cur_w = 0

            for base, ruby in segments:
                if not base:
                    continue

                base_w = self._measure_text(self.font, base)
                if ruby:
                    ruby_w = self._measure_text(self.ruby_font, ruby)
                    seg_w = max(base_w, ruby_w)
                else:
                    seg_w = base_w

                if cur and (cur_w + seg_w) <= limit:
                    cur.append((base, ruby))
                    cur_w += seg_w
                    continue

                if cur and (cur_w + seg_w) > limit:
                    _flush()

                if seg_w <= limit:
                    cur.append((base, ruby))
                    cur_w += seg_w
                    continue

                if ruby:
                    cur.append((base, ruby))
                    _flush()
                    continue

                for chunk in self._split_text_to_fit(base, limit):
                    chunk_w = self._measure_text(self.font, chunk)
                    if cur and (cur_w + chunk_w) > limit:
                        _flush()
                    cur.append((chunk, None))
                    cur_w += chunk_w

            _flush()
            wrapped: WrappedLines = tuple(tuple(line) for line in lines)
            self._wrap_cache[cache_key] = wrapped
            return wrapped
        finally:
            if self._timing_enabled:
                self._timing_data["wrap_segments_time"] += time.perf_counter() - start

    def _measure_text(self, font_obj: Optional[tkFont.Font], text: str) -> int:
        if font_obj is None:
            return 0
        s = text or ""
        if not s:
            return 0

        key = (id(font_obj), s)
        cached = self._measure_cache.get(key)
        if cached is not None:
            return cached

        start = time.perf_counter()
        value = int(font_obj.measure(s))
        self._measure_cache[key] = value
        if self._timing_enabled:
            self._timing_data["font_measure_time"] += time.perf_counter() - start
        return value

    def _get_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
        thickness = max(0, int(thickness))
        cached = self._outline_offset_cache.get(thickness)
        if cached is not None:
            return cached

        if thickness == 0:
            offsets: List[Tuple[int, int]] = []
        elif thickness == 1:
            offsets = [
                (-1, 0), (1, 0), (0, -1), (0, 1),
                (-1, -1), (-1, 1), (1, -1), (1, 1),
            ]
        else:
            mid = max(1, thickness // 2)
            offsets = [
                (-thickness, 0), (thickness, 0), (0, -thickness), (0, thickness),
                (-thickness, -thickness), (-thickness, thickness),
                (thickness, -thickness), (thickness, thickness),
                (-mid, 0), (mid, 0), (0, -mid), (0, mid),
            ]

        self._outline_offset_cache[thickness] = offsets
        return offsets

    def _draw_outlined_text(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        text: str,
        font: tkFont.Font,
        fill: str,
        outline: str,
        thickness: int,
        anchor: str = "center",
        tags=(),
    ) -> None:
        start = time.perf_counter()

        thickness = max(0, int(thickness))
        text = text or ""

        if thickness == 0:
            canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)
            if self._timing_enabled:
                self._timing_data["draw_outlined_text_time"] += time.perf_counter() - start
            return

        create_text = canvas.create_text
        offsets = self._get_outline_offsets(thickness)

        for dx, dy in offsets:
            create_text(
                x + dx,
                y + dy,
                text=text,
                fill=outline,
                font=font,
                anchor=anchor,
                tags=tags,
            )

        create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)

        if self._timing_enabled:
            self._timing_data["draw_outlined_text_time"] += time.perf_counter() - start

    def _draw_ruby_text(
        self,
        ruby: str,
        base_w: int,
        ruby_w: int,
        center_x: float,
        y: float,
        tags=(),
    ) -> None:
        if not ruby:
            return

        self._draw_outlined_text(
            self.canvas,
            center_x,
            y,
            ruby,
            self.ruby_font,
            fill=self.color,
            outline=self.glow_color,
            thickness=self.ruby_glow_radius,
            tags=tags,
        )

    def _split_text_to_fit(self, text: str, max_width: int) -> List[str]:
        start = time.perf_counter()
        try:
            out: List[str] = []
            s = (text or "").replace("\t", " ")
            cache_key = (s, int(max_width), id(self.font))
            cached = self._split_cache.get(cache_key)
            if cached is not None:
                return list(cached)

            while s:
                if self._measure_text(self.font, s) <= max_width:
                    out.append(s)
                    break

                lo, hi = 1, len(s)
                best = 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if self._measure_text(self.font, s[:mid]) <= max_width:
                        best = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1

                cut = best
                prefix = s[:best]

                m = re.search(r"[ \u3000、。，,.!?！？:：;；)\]】」』]\s*$", prefix)
                if m:
                    cut = m.end()
                    if cut < max(1, int(best * 0.5)):
                        cut = best

                chunk = s[:cut].rstrip()
                if chunk:
                    out.append(chunk)
                s = s[cut:].lstrip()

            self._split_cache[cache_key] = tuple(out)
            return out
        finally:
            if self._timing_enabled:
                self._timing_data["split_text_to_fit_time"] += time.perf_counter() - start

    def _finish_hover_bindings(self) -> None:
        if self.hover_ruby_enabled and self._hover_regions:
            self.canvas.bind("<Motion>", self._on_hover_motion)
            self.canvas.bind("<Leave>", self._clear_hover_ruby)
        else:
            self._clear_hover_ruby()
            try:
                self.canvas.unbind("<Motion>")
                self.canvas.unbind("<Leave>")
            except Exception:
                pass

    def _on_hover_motion(self, event) -> None:
        hit = None
        x = float(getattr(event, "x", 0))
        y = float(getattr(event, "y", 0))

        for region in self._hover_regions:
            x1, y1, x2, y2 = region["bbox"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                hit = region
                break

        if hit is self._hover_active_region:
            return

        self._clear_hover_ruby()
        self._hover_active_region = hit

        if hit is None:
            return

        self._draw_ruby_text(
            hit["ruby"],
            hit["base_w"],
            hit["ruby_w"],
            hit["cx"],
            hit["ruby_y"],
            tags=("hover_ruby",),
        )

    def _clear_hover_ruby(self, _event=None) -> None:
        try:
            self.canvas.delete("hover_ruby")
        except Exception:
            pass
        self._hover_active_region = None

    @staticmethod
    def _contains_kanji(text: str) -> bool:
        for ch in text or "":
            code = ord(ch)
            if 0x4E00 <= code <= 0x9FFF or code == 0x3005:
                return True
        return False

    def update_canvas(self, canvas: tk.Canvas):
        """
        Switch the renderer to a different canvas (after overlay update).
        No canvas-item pool is kept, so no cache reset is required here.
        """
        self.canvas = canvas








# """
# SubtitleRenderer draws parsed subtitle segments onto the overlay canvas.

# Responsibilities:
# - Text/ruby layout
# - Pixel-based wrapping (optional)
# - Window height adjustments for multi-line subtitles
# - Hover ruby display
# """

# from __future__ import annotations

# import logging
# import re
# import tkinter as tk
# from tkinter import font as tkFont
# from typing import Dict, List, Optional, Tuple

# from model.config_manager import ConfigManager
# from view.subtitle_overlay import SubtitleOverlayUI

# logger = logging.getLogger(__name__)


# class SubtitleRenderer:
#     def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
#         self.config = config
#         self.canvas = canvas

#         self._hover_regions: List[dict] = []
#         self._hover_active_region = None

#         self.font: Optional[tkFont.Font] = None
#         self.ruby_font: Optional[tkFont.Font] = None
#         self._font_settings = None

#         self.hover_ruby_enabled = False
#         self.color = "white"
#         self.glow_color = "black"
#         self.glow_radius = 4
#         self.ruby_glow_radius = 3
#         self.line_height = 0
#         self.ruby_height = 0

#         self._measure_cache: Dict[Tuple[int, str], int] = {}
#         self._outline_offset_cache: Dict[int, List[Tuple[int, int]]] = {}
#         self._split_cache: Dict[Tuple[str, int, int], Tuple[str, ...]] = {}
#         self._wrap_cache: Dict[
#             Tuple[Tuple[Tuple[str, Optional[str]], ...], int, int, int, int, int],
#             Tuple[Tuple[Tuple[str, Optional[str]], ...], ...],
#         ] = {}
#         self._layout_cache: Dict[Tuple, Dict] = {}
#         self._last_overlay_width = None
#         self._layout_cache_hits = 0
#         self._layout_cache_misses = 0

#     def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
#         self._refresh_fonts_if_needed()

#         self._hover_regions = []
#         self._hover_active_region = None

#         base_height = int(self.ruby_height * 2 + self.line_height * 2)
#         y_ruby_top = self.ruby_height // 2
#         y_base1 = self.ruby_height + self.line_height // 2

#         wrap_limit_px = self._get_wrap_limit_px()

#         if int(overlay.max_w) != int(self._last_overlay_width or 0):
#             self._layout_cache.clear()
#             self._last_overlay_width = overlay.max_w

#         layout_cache_key = (
#             repr(top_segments),
#             repr(bottom_segments),
#             int(overlay.max_w),
#             self._font_settings,
#             int(wrap_limit_px) if wrap_limit_px else -1,
#         )
#         cached_layout = self._layout_cache.get(layout_cache_key)
#         if cached_layout is not None:
#             self._layout_cache_hits += 1
#             wrapped_top = cached_layout["wrapped_top"]
#             wrapped_bottom = cached_layout["wrapped_bottom"]
#         else:
#             self._layout_cache_misses += 1
#             wrapped_top = self._wrap_segments(top_segments, overlay.max_w, line_limit_px=wrap_limit_px) if top_segments else []
#             wrapped_bottom = self._wrap_segments(bottom_segments, overlay.max_w, line_limit_px=wrap_limit_px) if bottom_segments else []
#             self._layout_cache[layout_cache_key] = {
#                 "wrapped_top": wrapped_top,
#                 "wrapped_bottom": wrapped_bottom,
#             }

#         lines = wrapped_top + wrapped_bottom

#         self.canvas.delete("hover_ruby")

#         if not lines:
#             self._finish_hover_bindings()
#             return

#         if len(lines) <= 2:
#             try:
#                 if int(overlay.max_h) != int(base_height):
#                     overlay.update_geometry(int(overlay.max_w), int(base_height))
#             except Exception:
#                 pass
#         else:
#             block_h = self.line_height + self.ruby_height
#             needed_h = int(block_h * len(lines))

#             try:
#                 sh = overlay.root.winfo_vrootheight()
#                 max_h_allowed = max(80, int(sh) - 40)
#             except Exception:
#                 max_h_allowed = overlay.max_h

#             target_h = max(int(base_height), needed_h)
#             target_h = min(target_h, max_h_allowed)

#             if int(overlay.max_h) != int(target_h):
#                 overlay.update_geometry(int(overlay.max_w), int(target_h))

#         self._render_subtitle_lines(lines, y_ruby_top, y_base1, overlay)
#         self._finish_hover_bindings()

#     def _get_wrap_limit_px(self) -> Optional[int]:
#         wrap_limit_px = None
#         try:
#             v = self.config.get("SUBTITLE_WRAP_LIMIT_PX")
#             if v is not None:
#                 v = int(v)
#                 if v > 0:
#                     wrap_limit_px = v
#         except Exception:
#             wrap_limit_px = None
#         return wrap_limit_px

#     def _render_subtitle_lines(self, lines, y_ruby_top, y_base1, overlay: SubtitleOverlayUI) -> None:
#         if len(lines) == 1:
#             self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
#             return

#         if len(lines) == 2:
#             block_h = self.line_height + self.ruby_height
#             base2_start = block_h
#             y_base2 = base2_start + self.line_height // 2
#             y_ruby_bot = base2_start + self.line_height + self.ruby_height // 2
#             self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
#             self._render_line(lines[1], y_ruby_bot, y_base2, overlay.max_w)
#             return

#         block_h = self.line_height + self.ruby_height
#         avail_h = int(overlay.max_h)
#         total_h = min(int(block_h * len(lines)), avail_h)
#         top_offset = max(0, int((avail_h - total_h) / 2))

#         for i, segs in enumerate(lines):
#             block_y = top_offset + int(i * block_h)
#             if i == len(lines) - 1:
#                 base_y = block_y + self.line_height // 2
#                 ruby_y = block_y + self.line_height + self.ruby_height // 2
#             else:
#                 ruby_y = block_y + self.ruby_height // 2
#                 base_y = block_y + self.ruby_height + self.line_height // 2
#             self._render_line(segs, ruby_y, base_y, overlay.max_w)

#     def _render_line(self, segments, ruby_y, base_y, max_width):
#         if not segments:
#             return

#         seg_meta = []
#         total_w = 0

#         for base, ruby in segments:
#             base_w = self._measure_text(self.font, base)
#             if ruby:
#                 ruby_w = self._measure_text(self.ruby_font, ruby)
#                 seg_w = max(base_w, ruby_w)
#             else:
#                 ruby_w = 0
#                 seg_w = base_w
#             seg_meta.append((base, ruby, base_w, ruby_w, seg_w))
#             total_w += seg_w

#         cur_x = (max_width - total_w) / 2

#         for base, ruby, base_w, ruby_w, seg_w in seg_meta:
#             cx = cur_x + seg_w / 2

#             if ruby and not self.hover_ruby_enabled:
#                 self._draw_ruby_text(ruby, base_w, ruby_w, cx, ruby_y)
#             elif ruby and self.hover_ruby_enabled and self._contains_kanji(base):
#                 self._hover_regions.append(
#                     {
#                         "bbox": (
#                             cur_x,
#                             base_y - (self.line_height / 2),
#                             cur_x + seg_w,
#                             base_y + (self.line_height / 2),
#                         ),
#                         "ruby": ruby,
#                         "base_w": base_w,
#                         "ruby_w": ruby_w,
#                         "cx": cx,
#                         "ruby_y": ruby_y,
#                     }
#                 )

#             self._draw_outlined_text(
#                 self.canvas,
#                 cx,
#                 base_y,
#                 base,
#                 self.font,
#                 fill=self.color,
#                 outline=self.glow_color,
#                 thickness=self.glow_radius,
#             )
#             cur_x += seg_w

#     def _refresh_fonts_if_needed(self) -> None:
#         font_family = self.config.get("SUBTITLE_FONT")
#         font_size = int(self.config.get("SUBTITLE_FONT_SIZE") or 12)
#         hover_ruby_enabled = self.config.get("SUBTITLE_HOVER_RUBY")
#         color = self.config.get("SUBTITLE_COLOR")
#         glow_color = str(self.config.get("GLOW_COLOR") or "black")

#         try:
#             glow_radius = int(float(self.config.get("GLOW_RADIUS") or 10))
#         except Exception:
#             glow_radius = 10
#         glow_radius = max(0, min(glow_radius, 20))

#         settings = (font_family, font_size, hover_ruby_enabled, color, glow_color, glow_radius)

#         if settings != self._font_settings or self.font is None or self.ruby_font is None:
#             self.font = tkFont.Font(family=font_family, size=font_size, weight="bold")
#             ruby_size = max(1, int(round(int(self.font.actual("size")) * 0.6)))
#             self.ruby_font = tkFont.Font(family=self.font.actual("family"), size=ruby_size, weight="bold")

#             self.hover_ruby_enabled = hover_ruby_enabled
#             self.color = str(color or "white")
#             self.glow_color = glow_color
#             self.glow_radius = glow_radius
#             self.ruby_glow_radius = max(0, int(round(self.glow_radius * 0.6667)))

#             self.line_height = int(self.font.metrics("linespace"))
#             self.ruby_height = int(self.line_height * 0.6)

#             self._font_settings = settings
#             self._invalidate_measure_caches()

#     def _invalidate_measure_caches(self) -> None:
#         self._measure_cache.clear()
#         self._outline_offset_cache.clear()
#         self._split_cache.clear()
#         self._wrap_cache.clear()
#         self._layout_cache.clear()

#     def _wrap_segments(
#         self,
#         segments: List[Tuple[str, Optional[str]]],
#         max_width: int,
#         padding: int = 40,
#         line_limit_px: Optional[int] = None,
#     ) -> List[List[Tuple[str, Optional[str]]]]:
#         cache_key = (
#             tuple(segments),
#             int(max_width),
#             int(padding),
#             int(line_limit_px) if line_limit_px is not None else -1,
#             id(self.font),
#             id(self.ruby_font),
#         )
#         cached = self._wrap_cache.get(cache_key)
#         if cached is not None:
#             return [list(line) for line in cached]

#         try:
#             if not segments:
#                 return []

#             limit = max(60, int(max_width) - int(padding) * 2)
#             if line_limit_px is not None:
#                 try:
#                     limit = max(60, min(limit, int(line_limit_px)))
#                 except Exception:
#                     pass

#             lines: List[List[Tuple[str, Optional[str]]]] = []
#             cur: List[Tuple[str, Optional[str]]] = []
#             cur_w = 0

#             def _flush():
#                 nonlocal cur, cur_w
#                 if cur:
#                     lines.append(cur)
#                 cur = []
#                 cur_w = 0

#             for base, ruby in segments:
#                 if not base:
#                     continue

#                 base_w = self._measure_text(self.font, base)
#                 if ruby:
#                     ruby_w = self._measure_text(self.ruby_font, ruby)
#                     seg_w = max(base_w, ruby_w)
#                 else:
#                     seg_w = base_w

#                 if cur and (cur_w + seg_w) <= limit:
#                     cur.append((base, ruby))
#                     cur_w += seg_w
#                     continue

#                 if cur and (cur_w + seg_w) > limit:
#                     _flush()

#                 if seg_w <= limit:
#                     cur.append((base, ruby))
#                     cur_w += seg_w
#                     continue

#                 if ruby:
#                     cur.append((base, ruby))
#                     _flush()
#                     continue

#                 for chunk in self._split_text_to_fit(base, limit):
#                     chunk_w = self._measure_text(self.font, chunk)
#                     if cur and (cur_w + chunk_w) > limit:
#                         _flush()
#                     cur.append((chunk, None))
#                     cur_w += chunk_w

#             _flush()
#             wrapped = [tuple(line) for line in lines]
#             self._wrap_cache[cache_key] = tuple(wrapped)
#             return lines
#         finally:
#             pass

#     def _measure_text(self, font_obj: Optional[tkFont.Font], text: str) -> int:
#         if font_obj is None:
#             return 0
#         s = text or ""
#         if not s:
#             return 0

#         key = (id(font_obj), s)
#         cached = self._measure_cache.get(key)
#         if cached is not None:
#             return cached

#         value = int(font_obj.measure(s))
#         self._measure_cache[key] = value
#         return value

#     def _get_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
#         thickness = max(0, int(thickness))
#         cached = self._outline_offset_cache.get(thickness)
#         if cached is not None:
#             return cached

#         offsets = [
#             (dx, dy)
#             for dx in range(-thickness, thickness + 1)
#             for dy in range(-thickness, thickness + 1)
#             if dx or dy
#         ]
#         self._outline_offset_cache[thickness] = offsets
#         return offsets

#     def _draw_outlined_text(
#         self,
#         canvas: tk.Canvas,
#         x: float,
#         y: float,
#         text: str,
#         font: tkFont.Font,
#         fill: str,
#         outline: str,
#         thickness: int,
#         anchor: str = "center",
#         tags=(),
#     ) -> None:
#         thickness = max(0, int(thickness))
#         text = text or ""

#         if thickness == 0:
#             canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)
#             return

#         create_text = canvas.create_text
#         offsets = self._get_outline_offsets(thickness)
#         if len(offsets) > 16:
#             offsets = self._get_approximate_outline_offsets(thickness)

#         for dx, dy in offsets:
#             create_text(
#                 x + dx,
#                 y + dy,
#                 text=text,
#                 fill=outline,
#                 font=font,
#                 anchor=anchor,
#                 tags=tags,
#             )

#         create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)

#     def _get_approximate_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
#         thickness = max(0, int(thickness))
#         if thickness <= 1:
#             return [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]

#         offsets = [
#             (-thickness, 0),
#             (thickness, 0),
#             (0, -thickness),
#             (0, thickness),
#             (-thickness, -thickness),
#             (-thickness, thickness),
#             (thickness, -thickness),
#             (thickness, thickness),
#         ]

#         mid = max(1, thickness // 2)
#         offsets.extend([
#             (-mid, -thickness),
#             (mid, -thickness),
#             (-mid, thickness),
#             (mid, thickness),
#             (-thickness, -mid),
#             (-thickness, mid),
#             (thickness, -mid),
#             (thickness, mid),
#         ])
#         return offsets

#     def _draw_ruby_text(
#         self,
#         ruby: str,
#         base_w: int,
#         ruby_w: int,
#         center_x: float,
#         y: float,
#         tags=(),
#     ) -> None:
#         if not ruby:
#             return

#         if base_w <= 0 or ruby_w >= base_w or len(ruby) <= 1:
#             self._draw_outlined_text(
#                 self.canvas,
#                 center_x,
#                 y,
#                 ruby,
#                 self.ruby_font,
#                 fill=self.color,
#                 outline=self.glow_color,
#                 thickness=self.ruby_glow_radius,
#                 tags=tags,
#             )
#             return

#         slot = base_w / max(1, len(ruby))
#         start_x = center_x - base_w / 2

#         for i, ch in enumerate(ruby):
#             ch_x = start_x + slot * (i + 0.5)
#             self._draw_outlined_text(
#                 self.canvas,
#                 ch_x,
#                 y,
#                 ch,
#                 self.ruby_font,
#                 fill=self.color,
#                 outline=self.glow_color,
#                 thickness=self.ruby_glow_radius,
#                 tags=tags,
#             )

#     def _split_text_to_fit(self, text: str, max_width: int) -> List[str]:
#         out: List[str] = []
#         s = (text or "").replace("\t", " ")
#         cache_key = (s, int(max_width), id(self.font))
#         cached = self._split_cache.get(cache_key)
#         if cached is not None:
#             return list(cached)

#         while s:
#             if self._measure_text(self.font, s) <= max_width:
#                 out.append(s)
#                 break

#             lo, hi = 1, len(s)
#             best = 1
#             while lo <= hi:
#                 mid = (lo + hi) // 2
#                 if self._measure_text(self.font, s[:mid]) <= max_width:
#                     best = mid
#                     lo = mid + 1
#                 else:
#                     hi = mid - 1

#             cut = best
#             prefix = s[:best]

#             m = re.search(r"[ \u3000、。，,.!?！？:：;；)\]】」』]\s*$", prefix)
#             if m:
#                 cut = m.end()
#                 if cut < max(1, int(best * 0.5)):
#                     cut = best

#             chunk = s[:cut].rstrip()
#             if chunk:
#                 out.append(chunk)
#             s = s[cut:].lstrip()

#         self._split_cache[cache_key] = tuple(out)
#         return out

#     def _finish_hover_bindings(self) -> None:
#         if self.hover_ruby_enabled and self._hover_regions:
#             self.canvas.bind("<Motion>", self._on_hover_motion)
#             self.canvas.bind("<Leave>", self._clear_hover_ruby)
#         else:
#             self._clear_hover_ruby()
#             try:
#                 self.canvas.unbind("<Motion>")
#                 self.canvas.unbind("<Leave>")
#             except Exception:
#                 pass

#     def _on_hover_motion(self, event) -> None:
#         hit = None
#         x = float(getattr(event, "x", 0))
#         y = float(getattr(event, "y", 0))

#         for region in self._hover_regions:
#             x1, y1, x2, y2 = region["bbox"]
#             if x1 <= x <= x2 and y1 <= y <= y2:
#                 hit = region
#                 break

#         if hit is self._hover_active_region:
#             return

#         self._clear_hover_ruby()
#         self._hover_active_region = hit

#         if hit is None:
#             return

#         self._draw_ruby_text(
#             hit["ruby"],
#             hit["base_w"],
#             hit["ruby_w"],
#             hit["cx"],
#             hit["ruby_y"],
#             tags=("hover_ruby",),
#         )

#     def _clear_hover_ruby(self, _event=None) -> None:
#         try:
#             self.canvas.delete("hover_ruby")
#         except Exception:
#             pass
#         self._hover_active_region = None

#     @staticmethod
#     def _contains_kanji(text: str) -> bool:
#         for ch in text or "":
#             code = ord(ch)
#             if 0x4E00 <= code <= 0x9FFF or code == 0x3005:
#                 return True
#         return False

#     def update_canvas(self, canvas: tk.Canvas):
#         """
#         Switch the renderer to a different canvas (after overlay update).
#         This renderer is stateless with respect to canvas items, so no cache reset is needed here.
#         """
#         self.canvas = canvas"""
SubtitleManager (model) handles:
- Loading local or cached subtitle files (.srt / .ass / .ssa)
- Parsing and building display data used by the renderer/controller
- Building episode maps (local directory + remote GitHub search results)
- Downloading missing remote episodes into a runtime cache
"""

import os
import re
import shutil
import requests
import json
import time
import datetime
import atexit
import bisect
import queue
from urllib.parse import urlparse, unquote
from typing import List, Optional, Tuple, Dict
import threading
from collections import defaultdict, Counter
import heapq

import regex
import srt
import chardet
import tkinter as tk
from tkinter import font as tkFont
from tkinter import filedialog, messagebox

from model.anki_ruby import AddonRubyGenerator

from model.config_manager import ConfigManager
from utils import format_time, get_monitor_rects
from view.overlays import LoadingOverlay, get_startup_overlay, hide_startup_overlay, show_startup_overlay

import logging
logger = logging.getLogger(__name__)
logging.Formatter.converter = time.gmtime

class SubtitleManager:

    CLEAN_PATTERN = re.compile(r'\{\\an\d+\}')
    TAG_PATTERN = re.compile(r'<[^>]*>')
    TAG_NAME_PATTERN = re.compile(r'<\s*/?\s*([A-Za-z0-9:_-]+)')
    SPEAKER_PATTERN = re.compile(
        r'^\s*(?P<dash>[-\u2010-\u2015\u2212\uff0d]\s*)?'
        r'[\uFF08(]\s*(?P<name>[^)\uFF09]{1,60})\s*[\uFF09)]\s*'
        r'[:\uFF1A]?\s*(?P<rest>.*)$'
    )
    NON_SPEAKER_HINTS = (
        "音",
        "物音",
        "声",
        "効果音",
        "足音",
        "息",
        "拍手",
        "ざわめき",
        "雑音",
        "心の声",
        "モノローグ",
    )
    SEASON_PATTERN = re.compile(r'S(\d+)', re.IGNORECASE)
    EPISODE_PATTERN = re.compile(r'E(\d+)', re.IGNORECASE)
    RUBY_PATTERN = regex.compile(r'(\p{Han}+)[(\uFF08]([^\)\uFF09]+)[)\uFF09]')
    PAREN_NOTE_PATTERN = regex.compile(r'[(\uFF08][^)\uFF09]*[)\uFF09]')

    RESOLUTION_RE = re.compile(r'^\d{3,4}p$', re.IGNORECASE)
    RESOLUTION_X_RE = re.compile(r'^\d{3,4}x\d{3,4}$', re.IGNORECASE)
    VIDEO_CODEC_RE = re.compile(r'^(x265|h264|av1|hevc|x264)$', re.IGNORECASE)
    NOISE_TOKENS = {'bd','web','webrip','bluray','bdrip','dvd','x264','x265','av1','hevc',
                    'aac','flac','hdtv','bdrip','bs8','netflix','amazon', 'fansub','group','copy','complete','ja[cc]'}
    AUTO_RUBY_CACHE_LIMIT = 20000
    _AUTO_RUBY_CACHE_MISS = object()

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.github_token = os.environ.get("GITHUB_TOKEN")
        self.remote_flag = self.config.get("REMOTE_FLAG")
        self._ruby_generator = None
        self._ruby_generator_failed = False
        self._auto_ruby_cache: Dict[str, object] = {}
        # Profiling stats
        self._ruby_stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "generator_calls": 0,
            "generator_time": 0.0,
            "episode_load_times": [],
        }
        self._search_dialog_open_count = 0
        self._font_cache_key = None
        self._cached_font = None
        self._cached_ruby_font = None
        self._search_dialog_lock = threading.Lock()

        if self.remote_flag:
            url = self.config.get("LAST_GITHUB_URL")
            # If we don't have a URL yet, fall back to the same "choose source" dialog used by set_new_file().
            # This gives the user local/remote-url/remote-search options at startup too.
            local_srt_path = self._initialize_remote_path(url) if url else None
            self._register_cache_cleanup()
        else:
            local_srt_path = self.config.get("LAST_LOCAL_SRT_FILE")
        self._load_local_and_process(local_srt_path)
        # self._trying_search_queries()#debugging
        # self._load_local_and_process(self.config.get("DEBUGGING_SRT_FILE"))
    
    def _register_cache_cleanup(self) -> None:
        def _cleanup():
            try:
                base = self._get_cache_base_dir()
                if os.path.exists(base):
                    shutil.rmtree(base)
                    logger.debug("Removed runtime cache: %s", base)
            except Exception:
                logger.exception("Failed to cleanup cache on exit")
        atexit.register(_cleanup)

    def save_state(self):
        #save all the variables to config on close:
        #LAST_LOCAL_SRT_FILE, LAST_ANIME_NAME, LAST_GITHUB_URL, 
        if getattr(self, "remote_flag", False):
            try:
                self._sync_remote_url_to_current_episode()
            except Exception:
                logger.exception("Failed to sync remote URL to current episode before save_state")

        if getattr(self, "srt_file", None) != self.config.get("LAST_LOCAL_SRT_FILE"):
            self.config.set("LAST_LOCAL_SRT_FILE", self.srt_file)
        if getattr(self, "anime_folder_name", None) != self.config.get("LAST_ANIME_NAME"):
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
        remote_url = getattr(self, "remote_url", None)
        if remote_url and remote_url != self.config.get("LAST_GITHUB_URL"):
            self.config.set("LAST_GITHUB_URL", remote_url)
            
#region --------------------------------local handling-----------------------------------
    def _load_local_and_process(self, local_srt_path: str) -> bool:
        if not (local_srt_path and os.path.isfile(local_srt_path)):
            logger.error("Local SRT path not found: \n%s\n -> Manual selection", local_srt_path)
            local_srt_path = self.choose_new_file()  # ask for local or remote
            if local_srt_path is None:
                return False
        # Keep the currently loaded file path in sync so helpers like get_current_global()
        # can always parse the active filename.
        self.srt_file = local_srt_path
        self._extract_and_set_local_episode_metadata(local_srt_path)
        self.set_subtitle_display_data(local_srt_path)
        if self.is_movie:
            logger.info(f"Loaded subtitle: Movie | {local_srt_path}")
        else:
            # Prefer global numbering if that's all we have (common for long-running shows).
            g = self.get_current_global()
            if self.current_season is not None and self.current_episode is not None:
                logger.info(f"Loaded subtitle: S{self.current_season}E{self.current_episode} | {local_srt_path}")
            elif g is not None:
                logger.info(f"Loaded subtitle: G{g} | {local_srt_path}")
            else:
                logger.info(f"Loaded subtitle: Episode | {local_srt_path}")

# -------------------------helpers-----------------------------
    def _extract_and_set_local_episode_metadata(self, local_path):
        if not self.remote_flag: #hardcoded certain local folder when not using cached files
            self.anime_folder_name = local_path.replace("\\", "/").split("/")[local_path.replace("\\", "/").split("/").index("subs")+1]
        self.config.set("LAST_LOCAL_SRT_FILE", local_path)

        # Always parse from the filename (works for both fixed subs folder and runtime cache).
        s, e, g = self.extract_season_episode_global(os.path.basename(local_path))
        # If the file only has global numbering, treat that as the current "episode"
        # so the UI/controller doesn't label it as "Movie".
        if s is None and e is None and g is not None:
            self.current_season, self.current_episode = None, int(g)
        else:
            self.current_season, self.current_episode = s, e

        # Only treat as "movie" if we couldn't parse *any* episodic identifier.
        # (Global-only numbering like "- 123" is still episodic.)
        self.is_movie = (s is None and e is None and g is None)
        self._build_local_episode_map(local_path)

    def _build_local_episode_map(self, local_path=None):
        if local_path is None:
            # Allow callers to refresh the map without explicitly passing a path.
            local_path = getattr(self, "srt_file", None)
            if not local_path:
                return

        self.local_srt_dir = os.path.dirname(local_path)
        self.local_srt_files = []
        for fn in os.listdir(self.local_srt_dir):
            if not fn.lower().endswith(('.srt', '.ass', '.ssa')):
                continue
            path = os.path.join(self.local_srt_dir, fn)
            s, e, g = self.extract_season_episode_global(fn)
            if s is None and e is None and g is None:
                self._log_unparsed_filename(name=fn, path=path, reason="local_dir_scan")
            # Normalize global-only files so they behave like episodes in the UI.
            if s is None and e is None and g is not None:
                e = int(g)
            rec = {"name": fn, "path": path, "season": s, "episode": e, "global": g}
            self.local_srt_files.append(rec)
        def _sort_key(rec):
            g = rec.get("global")
            if g is not None:
                return (0, int(g), (rec.get("name") or ""))
            return (1, int(rec.get("season") or 0), int(rec.get("episode") or 0), rec.get("name") or "")
        self.local_srt_files.sort(key=_sort_key)

    def set_subtitle_display_data(self, local_path):
        with open(local_path, 'rb') as f:
            raw = f.read()
        detected = chardet.detect(raw)
        text = raw.decode(detected['encoding'] or 'utf-8', errors='replace')

        ext = os.path.splitext(local_path)[1].lower()
        if ext in (".ass", ".ssa"):
            self.subtitles = self._parse_ass_subtitles(text)
        else:
            self.subtitles = list(srt.parse(text))

        auto_ruby_enabled = self._auto_ruby_enabled()
        # Cache repeated line parses inside one file load.
        # Keyed by (line_text, allow_auto) so source-only and auto-ruby variants can coexist.
        line_segment_cache: Dict[Tuple[str, bool], List[tuple[str, Optional[str]]]] = {}

        def _segments_for_line(line_text: str, allow_auto: bool) -> List[tuple[str, Optional[str]]]:
            cache_key = (line_text, bool(allow_auto))
            cached = line_segment_cache.get(cache_key)
            if cached is not None:
                return cached
            result = self._parse_ruby_segments(line_text, allow_auto=allow_auto)
            line_segment_cache[cache_key] = result if result else []
            return line_segment_cache[cache_key]

        self.display_data = []
        self.display_start_times = []
        self._auto_ruby_ready_indices = set()
        
        # Use start time as a warm area for eager auto-ruby, lazy-load everything else on demand.
        try:
            start_time_threshold = float(self.config.get("DEFAULT_START_TIME") or 120.0)
        except Exception:
            start_time_threshold = 120.0
        try:
            eager_window_sec = float(self.config.get("AUTO_RUBY_EAGER_WINDOW_SEC") or 180.0)
        except Exception:
            eager_window_sec = 180.0
        eager_start = max(0.0, float(start_time_threshold) - 30.0)
        eager_end = float(start_time_threshold) + max(0.0, float(eager_window_sec))

        episode_start = time.time()

        for idx, sub in enumerate(self.subtitles):
            clean = self._clean_text(sub.content)
            start_times = sub.start.total_seconds()
            lines = [l for l in clean.splitlines() if l.strip()]
            self.display_start_times.append(start_times)
            allow_auto_for_idx = (not auto_ruby_enabled) or (eager_start <= start_times <= eager_end)
            if not lines:
                top, bottom = [], []
            elif len(lines) == 1:
                top, bottom = [], _segments_for_line(lines[0], allow_auto=allow_auto_for_idx)
            else:
                top = _segments_for_line(lines[0], allow_auto=allow_auto_for_idx)
                bottom = _segments_for_line(lines[1], allow_auto=allow_auto_for_idx)
            self.display_data.append((clean, start_times, top, bottom))
            if allow_auto_for_idx:
                self._auto_ruby_ready_indices.add(int(idx))
        
        episode_time = time.time() - episode_start
        if hasattr(self, '_ruby_stats'):
            self._ruby_stats["episode_load_times"].append(episode_time)
            # Reset stats for next episode
            self._ruby_stats["cache_hits"] = 0
            self._ruby_stats["cache_misses"] = 0
            self._ruby_stats["generator_calls"] = 0
            self._ruby_stats["generator_time"] = 0.0

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN.sub('', text)
        cleaned = self._clean_html_tags(cleaned)
        use_source_ruby = not self._auto_ruby_enabled()
        if not use_source_ruby:
            cleaned = self.RUBY_PATTERN.sub(r'\1', cleaned)

        speaker_mode = str(self.config.get("SUBTITLE_SPEAKER_MODE") or "").strip().lower()
        if not speaker_mode:
            speaker_mode = "template" if bool(self.config.get("SUBTITLE_KEEP_SPEAKER_NAMES") or False) else "hide"
        elif speaker_mode in {"off", "none", "false", "0"}:
            speaker_mode = "hide"
        elif speaker_mode in {"source", "original"}:
            speaker_mode = "anime"
        elif speaker_mode not in {"hide", "anime", "template"}:
            speaker_mode = "hide"
        strip_paren_notes = self.config.get("SUBTITLE_STRIP_PAREN_NOTES")
        strip_paren_notes = True if strip_paren_notes is None else bool(strip_paren_notes)
        speaker_template = str(self.config.get("SUBTITLE_SPEAKER_TEMPLATE") or "{name}: ")

        out_lines = []
        for raw_line in cleaned.splitlines():
            line = (raw_line or "").strip()
            if not line:
                continue
            name, rest, anime_prefix = self._find_leading_speaker_label(line)
            if name is not None:
                if speaker_mode == "template" and name:
                    prefix = self._format_speaker_template(speaker_template, name).rstrip()
                    line = f"{prefix} {rest}".strip() if rest else prefix
                elif speaker_mode == "anime" and anime_prefix:
                    line = f"{anime_prefix}{rest}".strip() if rest else anime_prefix
                else:
                    line = rest
            if strip_paren_notes and line:
                line = self._strip_parenthetical_notes(line, keep_ruby=use_source_ruby).strip()
            if line:
                out_lines.append(line)

        cleaned = "\n".join(out_lines)
        cleaned = cleaned.replace('«', '(').replace('»', ')')
        return cleaned.replace('&lrm;', '').replace('\u200e', '').strip()

    def _strip_parenthetical_notes(self, line: str, keep_ruby: bool) -> str:
        if not line:
            return ""
        parts = []
        cursor = 0
        for match in self.PAREN_NOTE_PATTERN.finditer(line):
            start, end = match.span()
            if start > cursor:
                parts.append(line[cursor:start])
            keep_group = False
            if keep_ruby and start > 0:
                prev_char = line[start - 1]
                if regex.match(r"\p{Han}", prev_char):
                    keep_group = True
            if keep_group:
                parts.append(match.group(0))
            cursor = end
        if cursor < len(line):
            parts.append(line[cursor:])
        return "".join(parts)

    def _clean_html_tags(self, text: str) -> str:
        raw = self.config.get("SUBTITLE_CUSTOM_HTML_TAGS")
        if raw is None:
            raw = ""
        if isinstance(raw, (list, tuple, set)):
            parts = [str(p).strip() for p in raw]
        else:
            parts = re.split(r"[\s,;|]+", str(raw))

        allow = set()
        for p in parts:
            tag = str(p or "").strip().lower().strip("<>/")
            if tag:
                allow.add(tag)

        if not allow:
            return self.TAG_PATTERN.sub('', text)

        def _replace(match):
            token = match.group(0)
            m = self.TAG_NAME_PATTERN.search(token)
            if not m:
                return ""
            tag_name = (m.group(1) or "").strip().lower()
            return token if tag_name in allow else ""

        return self.TAG_PATTERN.sub(_replace, text)

    @staticmethod
    def _format_speaker_template(template: str, name: str) -> str:
        text = str(template or "{name}: ")
        if "{name}" not in text:
            text = text + "{name}"
        try:
            return text.format(name=name)
        except Exception:
            return f"{name}: "

    def _find_leading_speaker_label(self, line: str) -> Tuple[Optional[str], str, str]:
        current = (line or "").strip()
        if not current:
            return None, "", ""

        # Some subtitle lines have multiple leading (...) tags; we only treat tags
        # that look like an actual speaker label as speaker names.
        for _ in range(6):
            m = self.SPEAKER_PATTERN.match(current)
            if not m:
                break
            candidate = (m.group("name") or "").strip()
            rest = (m.group("rest") or "").strip()
            if self._looks_like_speaker_name(candidate):
                dash = "-" if (m.group("dash") or "").strip() else ""
                return candidate, rest, f"{dash}\uFF08{candidate}\uFF09"
            if not rest or rest == current:
                break
            current = rest
        return None, (line or "").strip(), ""

    @classmethod
    def _looks_like_speaker_name(cls, text: str) -> bool:
        value = (text or "").strip()
        if not value:
            return False

        # Remove ruby placeholders from the name check (e.g. 刃牙«バキ»).
        normalized = regex.sub(r'«[^»]*»', '', value)
        normalized = re.sub(r'\s+', '', normalized)
        if not normalized:
            return False

        if len(normalized) > 30:
            return False

        lower = normalized.lower()
        if any(token in lower for token in ("sfx", "se", "bgm", "voice", "sound", "noise")):
            return False

        if any(hint in normalized for hint in cls.NON_SPEAKER_HINTS):
            return False

        return bool(regex.search(r'[\p{Han}\p{Hiragana}\p{Katakana}A-Za-z]', normalized))

    # ---------------------- ASS parsing (minimal) ----------------------
    ASS_OVERRIDE_TAG_RE = re.compile(r'\{[^}]*\}')

    def _ass_time_to_timedelta(self, ts: str) -> datetime.timedelta:
        """
        ASS timestamps are usually H:MM:SS.CS (centiseconds), but some files use 3-digit fractions.
        """
        ts = (ts or "").strip()
        # e.g. 0:01:23.45
        hms, dot, frac = ts.partition(".")
        parts = hms.split(":")
        if len(parts) != 3:
            raise ValueError(f"Invalid ASS timestamp: {ts!r}")
        h = int(parts[0]); m = int(parts[1]); s = int(parts[2])
        frac_val = 0.0
        if dot:
            frac = (frac or "").strip()
            if frac.isdigit():
                if len(frac) <= 2:
                    frac_val = int(frac) / 100.0
                else:
                    frac_val = int(frac) / 1000.0
        return datetime.timedelta(hours=h, minutes=m, seconds=s + frac_val)

    def _clean_ass_dialogue_text(self, text: str) -> str:
        """
        Strip ASS override tags and convert common escape sequences to plain text.
        """
        t = text or ""
        # Remove override tags like "{\\i1}" / "{\\pos(...)}"
        t = self.ASS_OVERRIDE_TAG_RE.sub("", t)
        # Line breaks and non-breaking spaces
        t = t.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
        return t.strip()

    def _parse_ass_subtitles(self, text: str) -> List[srt.Subtitle]:
        """
        Parse an .ass/.ssa file into a list of srt.Subtitle objects.
        We only extract Start/End/Text from [Events] -> Dialogue lines.
        """
        if not text:
            return []

        in_events = False
        fmt_cols: List[str] = []
        idx_map: Dict[str, int] = {}
        subs: List[srt.Subtitle] = []
        out_idx = 1

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith(";"):
                continue
            low = line.lower()
            if low.startswith("[events]"):
                in_events = True
                fmt_cols = []
                idx_map = {}
                continue
            if not in_events:
                continue

            if low.startswith("format:"):
                fmt = line.split(":", 1)[1]
                fmt_cols = [c.strip().lower() for c in fmt.split(",") if c.strip()]
                idx_map = {name: i for i, name in enumerate(fmt_cols)}
                continue

            if low.startswith("dialogue:") or low.startswith("comment:"):
                # Ignore Comment lines (same shape as Dialogue in many files).
                if low.startswith("comment:"):
                    continue

                payload = line.split(":", 1)[1].lstrip()

                # If the file didn't provide a Format line, fall back to the common V4+ format.
                if not idx_map:
                    fmt_cols = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
                    idx_map = {name: i for i, name in enumerate(fmt_cols)}

                maxsplit = max(len(fmt_cols) - 1, 1)
                parts = payload.split(",", maxsplit=maxsplit)
                if len(parts) < len(fmt_cols):
                    # Malformed line; skip
                    continue

                try:
                    start_s = parts[idx_map["start"]].strip()
                    end_s = parts[idx_map["end"]].strip()
                    txt = parts[idx_map["text"]]
                except Exception:
                    continue

                try:
                    start = self._ass_time_to_timedelta(start_s)
                    end = self._ass_time_to_timedelta(end_s)
                except Exception:
                    continue

                content = self._clean_ass_dialogue_text(txt)
                if not content:
                    continue

                # Ensure ordering for duration computations; skip inverted cues.
                if end <= start:
                    continue

                subs.append(srt.Subtitle(index=out_idx, start=start, end=end, content=content))
                out_idx += 1

        subs.sort(key=lambda s: (s.start, s.end, s.index))
        return subs
     
    def _parse_ruby_segments(self, text: str, allow_auto: bool = True) -> List[tuple[str, Optional[str]]]:
        segments: List[tuple[str, Optional[str]]] = []
        last = 0
        found_ruby = False
        for m in self.RUBY_PATTERN.finditer(text):
            found_ruby = True
            plain = text[last:m.start()]
            if plain:
                segments.append((plain, None))
            segments.append((m.group(1), m.group(2)))
            last = m.end()
        tail = text[last:]
        if tail:
            segments.append((tail, None))
        if found_ruby:
            return segments

        auto = self._auto_ruby_segments(text) if allow_auto else None
        if auto:
            return auto
        return segments

    def _auto_ruby_enabled(self) -> bool:
        try:
            return bool(self.config.get("SUBTITLE_AUTO_RUBY") or False)
        except Exception:
            return False

    def _get_ruby_generator(self) -> Optional[AddonRubyGenerator]:
        if self._ruby_generator is not None or self._ruby_generator_failed:
            return self._ruby_generator
        try:
            self._ruby_generator = AddonRubyGenerator()
        except Exception:
            self._ruby_generator_failed = True
        return self._ruby_generator

    def _auto_ruby_segments(self, text: str) -> Optional[List[tuple[str, Optional[str]]]]:
        if not self._auto_ruby_enabled():
            return None
        if not text or ("[" in text and "]" in text):
            return None
        if not regex.search(r"\p{Han}", text or ""):
            return None

        cache = getattr(self, "_auto_ruby_cache", None)
        if cache is None:
            cache = {}
            self._auto_ruby_cache = cache
        cached = cache.get(text)
        if cached is self._AUTO_RUBY_CACHE_MISS:
            self._ruby_stats["cache_misses"] += 1
            return None
        if cached is not None:
            self._ruby_stats["cache_hits"] += 1
            return [tuple(seg) for seg in cached]

        generator = self._get_ruby_generator()
        if generator is None:
            return None
        
        # Time the generator call
        gen_start = time.time()
        try:
            segments = generator.segments(text)
        except Exception:
            return None
        gen_end = time.time()
        
        self._ruby_stats["generator_calls"] += 1
        self._ruby_stats["generator_time"] += (gen_end - gen_start)
        
        if not segments:
            cache[text] = self._AUTO_RUBY_CACHE_MISS
            return None
        if any(ruby for _base, ruby in segments):
            cached_segments = tuple((base, ruby) for base, ruby in segments)
            cache[text] = cached_segments
            while len(cache) > int(self.AUTO_RUBY_CACHE_LIMIT):
                try:
                    cache.pop(next(iter(cache)))
                except Exception:
                    break
            return [tuple(seg) for seg in cached_segments]
        cache[text] = self._AUTO_RUBY_CACHE_MISS
        return None

    def ensure_auto_ruby_for_index(self, idx: int) -> None:
        """
        Lazily enrich one subtitle cue with auto-ruby.
        Used by the controller right before rendering to avoid full-episode MeCab work on episode switch.
        """
        if not self._auto_ruby_enabled():
            return
        if idx is None:
            return
        try:
            i = int(idx)
        except Exception:
            return
        if i < 0 or i >= len(getattr(self, "display_data", [])):
            return

        ready = getattr(self, "_auto_ruby_ready_indices", None)
        if ready is None:
            ready = set()
            self._auto_ruby_ready_indices = ready
        if i in ready:
            return

        clean, start_times, top, bottom = self.display_data[i]
        lines = [l for l in (clean or "").splitlines() if l.strip()]
        if not lines:
            ready.add(i)
            return

        if len(lines) == 1:
            new_top, new_bottom = [], self._parse_ruby_segments(lines[0], allow_auto=True)
        else:
            new_top = self._parse_ruby_segments(lines[0], allow_auto=True)
            new_bottom = self._parse_ruby_segments(lines[1], allow_auto=True)

        self.display_data[i] = (clean, start_times, new_top, new_bottom)
        ready.add(i)

    def _process_ruby_batch(self, line_texts: List[str], line_segment_cache: Dict[str, List[tuple[str, Optional[str]]]]) -> List[List[tuple[str, Optional[str]]]]:
        """
        Process multiple lines with caching. This is NOT used with threading anymore
        but kept for any potential future use.
        """
        results = []
        for line_text in line_texts:
            # Check cache first
            cached = line_segment_cache.get(line_text)
            if cached is not None:
                results.append(cached)
                continue
            
            # Parse ruby
            parsed = self._parse_ruby_segments(line_text)
            line_segment_cache[line_text] = parsed
            results.append(parsed)
        
        return results

    def _process_all_ruby_async(self, sub_metadata: List[Dict], 
                                 line_segment_cache: Dict[str, List[tuple[str, Optional[str]]]],
                                 start_time_threshold: float,
                                 load_id: int) -> None:
        """
        Process ruby for all subtitles asynchronously in background.
        Prioritizes lines >= start_time_threshold, then processes others.
        This runs in a separate thread so it doesn't block the main thread.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        # Exit early if outdated
        if load_id != self._ruby_load_id:
            return
        
        def _process_line(line_text: str) -> List[tuple[str, Optional[str]]]:
            """Process a single line through ruby generator."""
            # Check if we're still the current load
            if load_id != self._ruby_load_id:
                return []
            
            cached = line_segment_cache.get(line_text)
            if cached is not None:
                return cached
            
            try:
                parsed = self._parse_ruby_segments(line_text)
                line_segment_cache[line_text] = parsed
                return parsed
            except Exception:
                return []
        
        # Collect all lines with priority markers
        priority_tasks = []  # (line_text, sub_idx, position) for lines >= start_time
        background_tasks = []  # (line_text, sub_idx, position) for lines < start_time
        
        for idx, metadata in enumerate(sub_metadata):
            if load_id != self._ruby_load_id:
                return
            
            lines = metadata['lines']
            start_times = metadata['start_times']
            
            if not lines:
                continue
            
            # Split by priority based on start time
            is_priority = start_times >= start_time_threshold
            task_list = priority_tasks if is_priority else background_tasks
            
            if len(lines) >= 1:
                task_list.append((lines[0], idx, 'top'))
            if len(lines) >= 2:
                task_list.append((lines[1], idx, 'bottom'))
        
        # Use thread pool (4 workers) - process priority first, then background
        with ThreadPoolExecutor(max_workers=4) as executor:
            all_tasks = priority_tasks + background_tasks
            future_map = {
                executor.submit(_process_line, line_text): (sub_idx, position)
                for line_text, sub_idx, position in all_tasks
            }
            
            for future in as_completed(future_map):
                # Check if load is outdated
                if load_id != self._ruby_load_id:
                    return
                
                try:
                    sub_idx, position = future_map[future]
                    segments = future.result()
                    # Update display_data directly
                    if 0 <= sub_idx < len(self.display_data):
                        clean, start_times, top, bottom = self.display_data[sub_idx]
                        if position == 'top':
                            self.display_data[sub_idx] = (clean, start_times, segments, bottom)
                        else:
                            self.display_data[sub_idx] = (clean, start_times, top, segments)
                except Exception:
                    pass  # Silently handle errors in background thread
# -------------------------helpers-----------------------------

# ---------------------- get data -------------------------
    def get_anime_name(self)-> Optional[str]: return self.anime_folder_name
    def get_subtitle_display_data(self): return self.display_data
    def get_subtitle_geometry(self): return self.calculate_geometry()
    def calculate_geometry_for_longest_lines(self, top_n: int = 10):

        def line_score(segments):
            return sum(len(base or "") + len(ruby or "") for base, ruby in segments)

        lines = []
        for clean, start_time, top, bottom in self.display_data:
            for segments in (top, bottom):
                if segments:
                    lines.append((line_score(segments), start_time, segments))

        if not lines:
            return self.calculate_geometry()

        top_lines = heapq.nlargest(top_n, lines, key=lambda item: item[0])
        font, ruby_font = self._get_subtitle_fonts()
        max_width = 0
        for _, _, segments in top_lines:
            if self._auto_ruby_enabled() and not any(ruby for _, ruby in segments):
                line_text = "".join(base for base, _ in segments)
                parsed = self._parse_ruby_segments(line_text, allow_auto=True)
                if parsed:
                    segments = parsed
            width = self._measure_line_width(segments, font, ruby_font)
            max_width = max(max_width, width)

        return self._geometry_from_measured_width(max_width)

    def _get_subtitle_fonts(self):
        family = self.config.get("SUBTITLE_FONT") or "Arial"
        size = self.config.get("SUBTITLE_FONT_SIZE") or 18
        try:
            size = int(size)
        except Exception:
            size = 18
        key = (family, size, "bold")
        if self._font_cache_key != key or self._cached_font is None or self._cached_ruby_font is None:
            self._cached_font = tkFont.Font(family=family, size=size, weight="bold")
            self._cached_ruby_font = tkFont.Font(
                family=self._cached_font.actual("family"),
                size=int(self._cached_font.actual("size") * 0.6),
                weight="bold",
            )
            self._font_cache_key = key
        return self._cached_font, self._cached_ruby_font

    def _measure_line_width(self, segments, font, ruby_font):
        width = 0
        for base, ruby in segments:
            base_w = font.measure(base)
            ruby_w = ruby_font.measure(ruby) if ruby else 0
            width += max(base_w, ruby_w)
        return width

    def _geometry_from_measured_width(self, max_width):
        font, _ = self._get_subtitle_fonts()
        line_height = font.metrics("linespace")
        ruby_height = int(line_height * 0.6)
        pad_x = 5
        total_height = ruby_height * 2 + line_height * 2

        try:
            wrap_limit_px = int(self.config.get("SUBTITLE_WRAP_LIMIT_PX") or 0)
        except Exception:
            wrap_limit_px = 0

        if wrap_limit_px > 0:
            renderer_padding = 40
            max_width = min(max_width, wrap_limit_px)
            total_width = max_width + 2 * renderer_padding
        else:
            total_width  = max_width + 2 * pad_x

        return (total_width, total_height)

    def _calculate_geometry_from_line_segments(self, segments):
        font, ruby_font = self._get_subtitle_fonts()
        max_width = self._measure_line_width(segments, font, ruby_font)
        return self._geometry_from_measured_width(max_width)

    def get_episode_metadata(self): return (self.github_owner, self.github_repo, self.remote_path,
                                            self.anime_folder_name, self.file_name, self.current_season, self.current_episode)
    def get_total_duration(self) -> float: return self.subtitles[-1].end.total_seconds()
    def get_current_season(self) -> int: return self.current_season
    def get_current_episode(self) -> int: return self.current_episode
# ---------------------- get data -------------------------
#endregion ------------------------------local handling-----------------------------------

#region -------------------------episode / season switching-----------------------------
    def _load_local_record(self, rec: dict) -> bool:
        """
        Given a rec from self.local_srt_files, load it and update current state.
        Returns True on success.
        """
        if not rec or not rec.get("path") or not os.path.isfile(rec["path"]):
            return False
        try:
            self._load_local_and_process(rec["path"])
            # _load_local_and_process already parses the filename and sets current_season/current_episode.
            # Only override if the record provides concrete metadata (and normalize global-only files).
            if rec.get("season") is not None:
                self.current_season = rec.get("season")
            if rec.get("episode") is not None:
                self.current_episode = rec.get("episode")
            elif rec.get("global") is not None and self.current_episode is None:
                self.current_episode = int(rec.get("global"))
            self.srt_file = rec["path"]
            if getattr(self, "remote_flag", False):
                try:
                    self._sync_remote_url_to_current_episode(rec)
                except Exception:
                    logger.exception("Failed to sync remote URL after loading local record")
            return True
        except Exception:
            logger.exception("Failed to load local subtitle: %s", rec.get("path"))
            return False

    def _sync_remote_url_to_current_episode(self, rec: Optional[Dict] = None) -> None:
        """
        Keep remote_url/remote_path aligned with the currently loaded episode.
        This allows startup to resume the same episode via LAST_GITHUB_URL even when cache is cleared on exit.
        """
        if not getattr(self, "remote_flag", False):
            return

        owner = getattr(self, "github_owner", None)
        repo = getattr(self, "github_repo", None)
        ref = getattr(self, "github_ref", None)
        if not (owner and repo and ref):
            return

        item = None
        remote_map = getattr(self, "remote_episode_map_global", None) or {}

        g = None
        if isinstance(rec, dict):
            try:
                if rec.get("global") is not None:
                    g = int(rec.get("global"))
            except Exception:
                g = None
        if g is None:
            try:
                g = self.get_current_global()
            except Exception:
                g = None
        if g is not None:
            item = remote_map.get(int(g))

        if item is None:
            s = None
            e = None
            if isinstance(rec, dict):
                s = rec.get("season")
                e = rec.get("episode")
            if s is None:
                s = getattr(self, "current_season", None)
            if e is None:
                e = getattr(self, "current_episode", None)
            season_map = getattr(self, "remote_episode_map_season", None) or {}
            if s is not None and e is not None:
                for it in season_map.get(int(s), []):
                    if it.get("episode") == int(e):
                        item = it
                        break

        remote_path = None
        if item and item.get("path"):
            remote_path = item.get("path")
        elif getattr(self, "remote_path", None):
            remote_path = self.remote_path
        if not remote_path:
            return

        self.remote_path = remote_path
        self.remote_url = f"https://github.com/{owner}/{repo}/blob/{ref}/{remote_path}"

    def change_episode(
        self,
        action: str,
        raw: Optional[int] = None,
        target_season: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        if self.remote_flag:
            return self.change_episode_remote(action, raw, target_season)
        return self.change_episode_local(action, raw, target_season)

    def change_episode_local(
        self,
        action: str,
        raw: Optional[int] = None,
        target_season: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        if not getattr(self, "local_srt_files", None):
            self._build_local_episode_map()

        cur_s = getattr(self, "current_season", None)
        cur_e = getattr(self, "current_episode", None)
        cur_g = None
        try:
            cur_g = self.get_current_global()
        except Exception:
            # fallback parsing from filename
            if getattr(self, "srt_file", None):
                _, _, cur_g = self.extract_season_episode_global(os.path.basename(self.srt_file))

        # helper to find by global or by (s,e)
        def find_by_global(g):
            if g is None:
                return None
            for rec in self.local_srt_files:
                if rec.get("global") == g:
                    return rec
            return None

        def find_by_local(s, e):
            for rec in self.local_srt_files:
                if rec.get("season") == s and rec.get("episode") == e:
                    return rec
            return None

        target_rec = None
        if action == "inc":
            # prefer global step if available
            if cur_g is not None:
                globals_sorted = sorted({int(r.get("global")) for r in self.local_srt_files if r.get("global") is not None})
                _, next_g = self._prev_next_in_sorted(globals_sorted, int(cur_g))
                if next_g is not None:
                    target_rec = find_by_global(next_g)
            if target_rec is None and cur_s is not None and cur_e is not None:
                season_eps = sorted({int(r.get("episode")) for r in self.local_srt_files if r.get("season") == cur_s and r.get("episode") is not None})
                _, next_e = self._prev_next_in_sorted(season_eps, int(cur_e))
                if next_e is not None:
                    target_rec = find_by_local(cur_s, next_e)

        elif action == "dec":
            if cur_g is not None:
                globals_sorted = sorted({int(r.get("global")) for r in self.local_srt_files if r.get("global") is not None})
                prev_g, _ = self._prev_next_in_sorted(globals_sorted, int(cur_g))
                if prev_g is not None:
                    target_rec = find_by_global(prev_g)
            if target_rec is None and cur_s is not None and cur_e is not None and cur_e > 1:
                season_eps = sorted({int(r.get("episode")) for r in self.local_srt_files if r.get("season") == cur_s and r.get("episode") is not None})
                prev_e, _ = self._prev_next_in_sorted(season_eps, int(cur_e))
                if prev_e is not None:
                    target_rec = find_by_local(cur_s, prev_e)

        elif action == "set":
            if not (isinstance(raw, int) and raw > 0):
                return self.current_season, self.current_episode
            if target_season is not None:
                target_rec = find_by_local(int(target_season), raw)
            # interpret as local episode in current season first
            if target_rec is None:
                if cur_s is not None:
                    target_rec = find_by_local(cur_s, raw)
            # if that failed, also check whether raw matches a global index
            if target_rec is None:
                target_rec = find_by_global(raw)

        else:
            return self.current_season, self.current_episode

        if target_rec:
            if self._load_local_record(target_rec):
                return self.current_season, self.current_episode
            # if load fails, fall through to warn

        # not found locally -> warn user (no downloads in local mode)
        message = f"Episode not found in local folder: action={action}, value={raw}"
        logger.info(message)

        return self.current_season, self.current_episode

    def _prev_next_in_sorted(self, sorted_values: List[int], current: int) -> Tuple[Optional[int], Optional[int]]:
        """
        Given a sorted list of ints and a current value, return (prev, next) values.
        If current is not in the list, we return neighbors around the insertion point.
        """
        if not sorted_values:
            return None, None
        cur = int(current)
        idx = bisect.bisect_left(sorted_values, cur)
        prev_val = sorted_values[idx - 1] if idx > 0 else None

        # If current exists in the list, "next" should be the element after it.
        if idx < len(sorted_values) and sorted_values[idx] == cur:
            idx += 1
        next_val = sorted_values[idx] if idx < len(sorted_values) else None
        return prev_val, next_val

    def get_episode_nav_state(self) -> Tuple[bool, bool, bool]:
        """
        Returns (can_dec, can_inc, is_movie) for UI.
        We only disable buttons when we *know* there is no prev/next episode from the current maps/index.
        """
        if getattr(self, "is_movie", False):
            return False, False, True

        # Remote mode: prefer the remote global map if available.
        if getattr(self, "remote_flag", False):
            m = getattr(self, "remote_episode_map_global", None)
            if isinstance(m, dict) and m:
                cur_g = None
                try:
                    cur_g = self.get_current_global()
                except Exception:
                    cur_g = None
                if cur_g is not None:
                    keys = sorted(int(k) for k in m.keys())
                    prev_g, next_g = self._prev_next_in_sorted(keys, int(cur_g))
                    return (prev_g is not None), (next_g is not None), False
            # Unknown -> don't block UI.
            return True, True, False

        # Local mode: base decision on the local index if available.
        if not getattr(self, "local_srt_files", None):
            try:
                self._build_local_episode_map()
            except Exception:
                return True, True, False

        cur_g = None
        try:
            cur_g = self.get_current_global()
        except Exception:
            cur_g = None

        if cur_g is not None:
            globals_sorted = sorted({int(r.get("global")) for r in self.local_srt_files if r.get("global") is not None})
            prev_g, next_g = self._prev_next_in_sorted(globals_sorted, int(cur_g))
            return (prev_g is not None), (next_g is not None), False

        cur_s = getattr(self, "current_season", None)
        cur_e = getattr(self, "current_episode", None)
        if cur_s is not None and cur_e is not None:
            season_eps = sorted({int(r.get("episode")) for r in self.local_srt_files if r.get("season") == cur_s and r.get("episode") is not None})
            prev_e, next_e = self._prev_next_in_sorted(season_eps, int(cur_e))
            return (prev_e is not None), (next_e is not None), False

        return True, True, False

    def get_episode_dropdown_values(self) -> List[int]:
        """
        Values for the episode dropdown (combobox).

        Remote mode: show *all* global episodes from the remote episode map (even if not downloaded yet).
        Local mode: show global episodes if available, otherwise local episodes (prefer current season).
        """
        # Remote: prefer complete global map.
        if getattr(self, "remote_flag", False):
            m = getattr(self, "remote_episode_map_global", None)
            if not isinstance(m, dict) or not m:
                try:
                    self.build_remote_episode_maps()
                except Exception:
                    pass
                m = getattr(self, "remote_episode_map_global", None)
            if isinstance(m, dict) and m:
                try:
                    return sorted(int(k) for k in m.keys())
                except Exception:
                    pass

        # Local fallback: index available local/cache files.
        if not getattr(self, "local_srt_files", None):
            try:
                if getattr(self, "remote_flag", False):
                    self.update_local_srt_files()
                else:
                    self._build_local_episode_map()
            except Exception:
                pass

        lst = getattr(self, "local_srt_files", None) or []
        try:
            globals_sorted = sorted({int(r.get("global")) for r in lst if r.get("global") is not None})
        except Exception:
            globals_sorted = []
        if globals_sorted:
            return globals_sorted

        cur_s = getattr(self, "current_season", None)
        if cur_s is not None:
            try:
                eps = sorted({int(r.get("episode")) for r in lst if r.get("season") == cur_s and r.get("episode") is not None})
            except Exception:
                eps = []
            if eps:
                return eps

        try:
            return sorted({int(r.get("episode")) for r in lst if r.get("episode") is not None})
        except Exception:
            return []

    def change_episode_remote(
        self,
        action: str,
        raw: Optional[int] = None,
        target_season: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Remote switching: first try to find the file in the local index. If missing, trigger a
        focused windowed download around the target/global (synchronously), refresh local index,
        then load if available. If still missing, show a warning.
        """
        # local_srt_files is optional in remote mode; avoid expensive full-cache scans here.
        if not isinstance(getattr(self, "local_srt_files", None), list):
            self.local_srt_files = []

        # ensure remote maps exist (for global<->local mapping and download lists)
        if not getattr(self, "remote_episode_map_global", None):
            overlay = None
            try:
                root = getattr(tk, "_default_root", None)
                if root is not None:
                    overlay = LoadingOverlay(
                        root,
                        text="Searching GitHub...",
                        anchor_window=root,
                        y_offset=0,
                    )
                if not getattr(self, "all_results_items", None):
                    self._create_remote_episode_map_per_season()
                self.build_remote_episode_maps()
            except Exception:
                logger.exception("Failed to build remote maps in change_episode_remote")
            finally:
                if overlay:
                    overlay.close()

        cur_s = getattr(self, "current_season", None)
        cur_e = getattr(self, "current_episode", None)
        cur_g = self.get_current_global()

        # helpers to locate rec
        def find_by_global(g):
            for rec in self.local_srt_files:
                if rec.get("global") == g:
                    return rec
            return None

        def find_by_local(s, e):
            for rec in self.local_srt_files:
                if rec.get("season") == s and rec.get("episode") == e:
                    return rec
            return None

        # resolve intended target global/season/episode similar to local function
        target_global = None
        target_s = None
        target_e = None

        if action == "inc":
            if cur_g is not None:
                # Prefer stepping to the next *available* global episode in the remote map.
                target_global = None
                try:
                    keys = sorted(int(k) for k in getattr(self, "remote_episode_map_global", {}).keys())
                    _, next_g = self._prev_next_in_sorted(keys, int(cur_g))
                    target_global = next_g
                except Exception:
                    target_global = int(cur_g) + 1

                if target_global is None:
                    logger.info("No next episode available (global %s).", cur_g)
                    return self.current_season, self.current_episode

                target_rec = find_by_global(target_global)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                # not found locally: compute target season/episode from remote map
                ts, te = self.global_to_local(target_global)
                target_s, target_e = ts, te
            else:
                if cur_s is None or cur_e is None:
                    return None, None
                # attempt local step
                target_rec = find_by_local(cur_s, cur_e + 1)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                # map local->global if possible and fall through to download
                target_global = self.local_to_global(cur_s, cur_e + 1)
                target_s, target_e = cur_s, cur_e + 1

        elif action == "dec":
            if cur_g is not None:
                # Prefer stepping to the previous *available* global episode in the remote map.
                target_global = None
                try:
                    keys = sorted(int(k) for k in getattr(self, "remote_episode_map_global", {}).keys())
                    prev_g, _ = self._prev_next_in_sorted(keys, int(cur_g))
                    target_global = prev_g
                except Exception:
                    target_global = int(cur_g) - 1

                if target_global is None:
                    logger.info("No previous episode available (global %s).", cur_g)
                    return self.current_season, self.current_episode

                target_rec = find_by_global(target_global)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                ts, te = self.global_to_local(target_global)
                target_s, target_e = ts, te
            else:
                if cur_s is None or cur_e is None:
                    return None, None
                target_rec = find_by_local(cur_s, cur_e - 1) if cur_e > 1 else None
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                target_global = self.local_to_global(cur_s, cur_e - 1) if cur_e > 1 else None
                target_s, target_e = cur_s, cur_e - 1

        elif action == "set":
            if not (isinstance(raw, int) and raw > 0):
                return self.current_season, self.current_episode
            if target_season is not None:
                ts = int(target_season)
                target_rec = find_by_local(ts, raw)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode

                g = self.local_to_global(ts, raw)
                if g is not None:
                    target_global = int(g)
                    target_s, target_e = ts, raw
                else:
                    try:
                        if not getattr(self, "remote_episode_map_global", None):
                            if not getattr(self, "all_results_items", None):
                                self._create_remote_episode_map_per_season()
                            self.build_remote_episode_maps()
                    except Exception:
                        logger.exception("Failed to build remote maps for season-episode set")

                    season_items = getattr(self, "remote_episode_map_season", {}).get(ts, [])
                    chosen = None
                    for item in season_items:
                        if item.get("episode") == int(raw):
                            chosen = item
                            break
                    if chosen and chosen.get("global") is not None:
                        target_global = int(chosen.get("global"))
                        target_s, target_e = ts, raw
                    else:
                        logger.info("Episode not found for season set: S%sE%s", ts, raw)
                        return self.current_season, self.current_episode

            # prefer local interpretation: current season + episode raw
            elif cur_s is not None:
                target_rec = find_by_local(cur_s, raw)
                if target_rec:
                    if self._load_local_record(target_rec):
                        return self.current_season, self.current_episode
                # try to map to global if possible
                g = self.local_to_global(cur_s, raw)
                if g:
                    target_global = g
                    target_s, target_e = cur_s, raw
                else:
                    # treat raw as global if present in remote map
                    if raw in getattr(self, "remote_episode_map_global", {}):
                        target_global = raw
                        target_s, target_e = self.global_to_local(raw)
                    else:
                        # build remote maps and retry
                        try:
                            if not getattr(self, "all_results_items", None):
                                self._create_remote_episode_map_per_season()
                            self.build_remote_episode_maps()
                        except Exception:
                            logger.exception("Failed to build remote maps for 'set'")
                        if raw in getattr(self, "remote_episode_map_global", {}):
                            target_global = raw
                            target_s, target_e = self.global_to_local(raw)
                        else:
                            # fall back to trying a local file with that episode number
                            for rec in self.local_srt_files:
                                if rec.get("season") == cur_s and rec.get("episode") == raw:
                                    if self._load_local_record(rec):
                                        return self.current_season, self.current_episode
                            logger.info("Episode not found locally or remotely: %s", raw)
                            return self.current_season, self.current_episode
            else:
                # no cur season known -> try treat raw as global
                if raw in getattr(self, "remote_episode_map_global", {}):
                    target_global = raw
                    target_s, target_e = self.global_to_local(raw)
                else:
                    logger.info("Episode not found: %s", raw)
                    return self.current_season, self.current_episode
        else:
            return self.current_season, self.current_episode

        # At this point we have target_global (maybe None) and/or target_s/target_e
        # If the file is still not local, request windowed download around target_global (or current global)
        if target_global is None and target_s is not None and target_e is not None:
            target_global = self.local_to_global(target_s, target_e)

        if target_global is None:
            # If we still cannot derive a global index, warn user
            logger.info("Could not determine global index for requested episode.")
            return self.current_season, self.current_episode

        # Resolve the expected local cache path for this global episode.
        item = getattr(self, "remote_episode_map_global", {}).get(int(target_global))
        if not item or not item.get("path"):
            logger.info("Requested episode not available: no remote path for global %s.", target_global)
            return self.current_season, self.current_episode

        season = item.get("season") or self.current_season
        season_dir = self._season_cache_dir(season)
        filename = self.sanitize_filename(os.path.basename(item["path"]))
        local_path = os.path.join(season_dir, filename)

        # Always download the *requested* episode first (sync) if missing.
        if not os.path.exists(local_path):
            overlay = None
            try:
                root = getattr(tk, "_default_root", None)
                if root is not None:
                    overlay = LoadingOverlay(
                        root,
                        text=f"Downloading episode {target_global}...",
                        anchor_window=root,
                    )
                raw_url = self._get_raw_url(item["path"])
                self._download_file(raw_url, local_path)
            except Exception:
                logger.exception("Failed to download requested episode (global %s)", target_global)
            finally:
                if overlay:
                    overlay.close()

        # After download attempt, load directly from the expected cache path (no full cache scan).
        if os.path.exists(local_path):
            rec = {
                "season": item.get("season"),
                "episode": item.get("episode"),
                "global": int(target_global),
                "path": local_path,
                "name": os.path.basename(local_path),
            }
            # Normalize global-only episodes so they don't behave like "movies".
            if rec.get("season") is None and rec.get("episode") is None:
                rec["episode"] = int(target_global)

            if self._load_local_record(rec):
                # Now that the requested episode is loaded, download the rest of the window asynchronously.
                try:
                    self._schedule_prefetch_window(target_global)
                except Exception:
                    logger.exception("Failed to start background window download around global %s", target_global)
                return self.current_season, self.current_episode

        logger.info("Requested episode not available after download attempt (global %s).", target_global)
        return self.current_season, self.current_episode

    def set_new_file(self):
        """
        User action: prompt for a new subtitle source and immediately load it.

        Returns the selected local file path (including downloaded cache files) or None.
        """
        path = self.choose_new_file()
        if not path:
            return None
        try:
            self._load_local_and_process(path)
        except Exception:
            logger.exception("Failed to load selected subtitle: %s", path)
            return None
        return path

    @staticmethod
    def _fit_dialog_to_screen(
        dialog: tk.Toplevel,
        parent=None,
        min_w: int = 360,
        min_h: int = 180,
        max_w_ratio: float = 0.96,
        max_h_ratio: float = 0.94,
    ) -> None:
        if dialog is None:
            return
        try:
            dialog.update_idletasks()
        except Exception:
            pass

        try:
            req_w = int(dialog.winfo_reqwidth())
            req_h = int(dialog.winfo_reqheight())
        except Exception:
            req_w, req_h = min_w, min_h

        mon_x = 0
        mon_y = 0
        mon_w = 1920
        mon_h = 1080
        try:
            probe_x = int(dialog.winfo_pointerx())
            probe_y = int(dialog.winfo_pointery())
            if parent is not None:
                parent.update_idletasks()
                probe_x = int(parent.winfo_rootx() + (parent.winfo_width() // 2))
                probe_y = int(parent.winfo_rooty() + (parent.winfo_height() // 2))
            rects = list(get_monitor_rects(parent or dialog) or [])
            for rx, ry, rw, rh in rects:
                if rx <= probe_x < rx + rw and ry <= probe_y < ry + rh:
                    mon_x, mon_y, mon_w, mon_h = int(rx), int(ry), int(rw), int(rh)
                    break
            else:
                if rects:
                    rx, ry, rw, rh = min(
                        rects,
                        key=lambda r: abs(probe_x - (r[0] + (r[2] // 2))) + abs(probe_y - (r[1] + (r[3] // 2))),
                    )
                    mon_x, mon_y, mon_w, mon_h = int(rx), int(ry), int(rw), int(rh)
        except Exception:
            try:
                mon_w = int(dialog.winfo_screenwidth() or 1920)
                mon_h = int(dialog.winfo_screenheight() or 1080)
            except Exception:
                mon_w, mon_h = 1920, 1080
            mon_x, mon_y = 0, 0

        max_w = max(min_w, int(mon_w * max_w_ratio))
        max_h = max(min_h, int(mon_h * max_h_ratio))
        width = max(min_w, min(req_w, max_w))
        height = max(min_h, min(req_h, max_h))

        x = mon_x + max(0, (mon_w - width) // 2)
        y = mon_y + max(0, (mon_h - height) // 2)

        try:
            dialog.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            pass

    def _set_search_dialog_active(self, active: bool) -> None:
        try:
            with self._search_dialog_lock:
                if active:
                    self._search_dialog_open_count += 1
                else:
                    self._search_dialog_open_count = max(0, self._search_dialog_open_count - 1)
        except Exception:
            pass

    def is_search_dialog_active(self) -> bool:
        try:
            with self._search_dialog_lock:
                return bool(self._search_dialog_open_count > 0)
        except Exception:
            return False

    @staticmethod
    def _normalize_search_query_text(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip())

    def _safe_search_query_name(self, query: str) -> str:
        normalized = self._normalize_search_query_text(query)
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in normalized)
        safe = re.sub(r"_+", "_", safe).strip(" _.-")
        return (safe[:200] or "result")

    def choose_new_file(self) -> Optional[str]:
        """
        Prompt the user to select a new subtitle source.

        Returns a local path:
        - Local mode: chosen file path
        - Remote mode: downloaded cache path for the chosen episode
        """
        result = {"path": None}

        # If a startup splash is visible, hide it while the user interacts with dialogs.
        hide_startup_overlay()
        try:
            popup = tk.Toplevel()
            popup.title("Choose Source")
            popup.attributes("-topmost", True)
            popup.grab_set()
            popup.resizable(True, True)

            tk.Label(popup, text="Select source for subtitle file:", font=("Arial", 12)).pack(pady=(12, 8))
            button_frame = tk.Frame(popup)
            button_frame.pack(pady=8)

            def _done(path: Optional[str]):
                result["path"] = path
                try:
                    popup.destroy()
                except Exception:
                    pass

            def choose_local():
                path = self.ask_local_srt_file()
                if not path:
                    return
                # Bring the startup splash back while we parse/process the chosen file.
                show_startup_overlay()
                _done(path)

            def choose_remote_url():
                url, s, e, is_movie_mode = self.ask_remote_srt_with_hint()
                if not url:
                    return
                hint = (s, e) if (s is not None or e is not None) else None

                # Close chooser. Show startup splash again while we do network work.
                try:
                    popup.destroy()
                except Exception:
                    pass
                show_startup_overlay()

                overlay = None
                try:
                    # If we already have the startup splash, don't stack another overlay.
                    root = getattr(tk, "_default_root", None)
                    if root is not None and get_startup_overlay() is None:
                        overlay = LoadingOverlay(
                            root,
                            text="Searching and downloading...",
                            anchor_window=root,
                            y_offset=0,
                        )
                    result["path"] = self._initialize_remote_path(url, hint=hint, movie_mode=is_movie_mode)
                except Exception as exc:
                    logger.exception("Remote URL initialization failed")
                    try:
                        messagebox.showerror("Remote URL Error", str(exc))
                    except Exception:
                        pass
                finally:
                    if overlay:
                        overlay.close()

            def choose_remote_search():
                anime_query, s, e, is_movie_mode = self.ask_remote_search_query()
                if not anime_query:
                    return
                hint = (s, e) if (s is not None or e is not None) else None

                try:
                    popup.destroy()
                except Exception:
                    pass
                show_startup_overlay()

                overlay = None
                try:
                    root = getattr(tk, "_default_root", None)
                    if root is not None and get_startup_overlay() is None:
                        overlay = LoadingOverlay(
                            root,
                            text="Searching and downloading...",
                            anchor_window=root,
                            y_offset=0,
                        )
                    result["path"] = self._initialize_remote_from_search_query(
                        anime_query,
                        hint=hint,
                        movie_mode=is_movie_mode,
                    )
                except Exception as exc:
                    logger.exception("Remote search initialization failed")
                    try:
                        messagebox.showerror("Remote Search Error", str(exc))
                    except Exception:
                        pass
                finally:
                    if overlay:
                        overlay.close()

            tk.Button(button_frame, text="Local File", width=15, command=choose_local).grid(row=0, column=0, padx=10)
            tk.Button(button_frame, text="Remote URL", width=15, command=choose_remote_url).grid(row=0, column=1, padx=10)
            tk.Button(button_frame, text="Remote Search", width=15, command=choose_remote_search).grid(row=0, column=2, padx=10)

            self._fit_dialog_to_screen(popup, min_w=460, min_h=130)
            popup.wait_window(popup)
            return result["path"]
        finally:
            # Ensure the splash returns even if the user cancels/closes the dialog.
            show_startup_overlay()

    def ask_remote_srt_with_hint(self) -> Tuple[Optional[str], Optional[int], Optional[int], bool]:
        result = {"url": None, "season": None, "episode": None, "is_movie": False}
        hide_startup_overlay()
        self._set_search_dialog_active(True)
        try:
            dlg = tk.Toplevel()
            dlg.title("Remote subtitle (URL + sXeY)")
            dlg.attributes("-topmost", True)
            dlg.grab_set()
            dlg.resizable(True, True)

            tk.Label(dlg, text="GitHub subtitle URL:", anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=(8,2))
            url_entry = tk.Entry(dlg, width=60)
            url_entry.grid(row=1, column=0, padx=8)

            tk.Label(dlg, text="Season/Episode (e.g. s2e1 or s02e01):", anchor="w").grid(row=2, column=0, sticky="w", padx=8, pady=(8,2))
            se_entry = tk.Entry(dlg, width=30)
            se_entry.grid(row=3, column=0, padx=8)
            movie_var = tk.BooleanVar(value=False)
            tk.Checkbutton(
                dlg,
                text="Movie mode (no season/episode constraints)",
                variable=movie_var,
                anchor="w",
            ).grid(row=4, column=0, sticky="w", padx=8, pady=(6, 0))

            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=5, column=0, pady=10)

            def on_ok():
                u = url_entry.get().strip()
                s, e, global_e = self.extract_season_episode_global(se_entry.get().strip().lower())
                result["url"], result["season"], result["episode"] = (u or None, s, e)
                result["is_movie"] = bool(movie_var.get())
                dlg.destroy()
            def on_cancel():
                dlg.destroy()
            tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=6)
            tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

            self._fit_dialog_to_screen(dlg, min_w=620, min_h=260)
            dlg.wait_window(dlg)
            return result["url"], result["season"], result["episode"], bool(result.get("is_movie"))
        finally:
            self._set_search_dialog_active(False)
            show_startup_overlay()

    def _cached_github_search_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "github_search")

    def _list_cached_github_search_queries(self) -> List[str]:
        folder_dir = self._cached_github_search_dir()
        if not os.path.isdir(folder_dir):
            return []

        prefix = "github_search_"
        suffix = ".json"
        queries: List[str] = []
        for fn in os.listdir(folder_dir):
            if not (fn.startswith(prefix) and fn.endswith(suffix)):
                continue
            safe_name = fn[len(prefix):-len(suffix)]
            if not safe_name:
                continue
            query_text = safe_name.replace("_", " ")
            try:
                path = os.path.join(folder_dir, fn)
                with open(path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                candidate = payload.get("anime_query")
                if isinstance(candidate, str) and candidate.strip():
                    query_text = candidate
            except Exception:
                pass
            normalized = self._normalize_search_query_text(query_text)
            if normalized:
                queries.append(normalized)
        return sorted(set(queries), key=str.casefold)

    def _ask_cached_github_search_query(self, parent) -> Optional[str]:
        queries = self._list_cached_github_search_queries()
        if not queries:
            try:
                parent.bell()
            except Exception:
                pass
            return None

        chosen = {"query": None}
        self._set_search_dialog_active(True)
        try:
            chooser = tk.Toplevel(parent)
            chooser.title("Choose cached search")
            chooser.attributes("-topmost", True)
            chooser.transient(parent)
            chooser.grab_set()
            chooser.resizable(True, True)

            tk.Label(chooser, text="Select a cached anime query:", anchor="w").pack(padx=8, pady=(8, 4), fill="x")

            filter_row = tk.Frame(chooser)
            filter_row.pack(padx=8, pady=(0, 6), fill="x")
            tk.Label(filter_row, text="Filter:", anchor="w").pack(side="left")
            filter_var = tk.StringVar()
            filter_entry = tk.Entry(filter_row, textvariable=filter_var)
            filter_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))

            list_frame = tk.Frame(chooser)
            list_frame.pack(padx=8, pady=(0, 8), fill="both", expand=True)
            scrollbar = tk.Scrollbar(list_frame, orient="vertical")
            listbox = tk.Listbox(
                list_frame,
                width=56,
                height=min(12, len(queries)),
                yscrollcommand=scrollbar.set,
                exportselection=False,
            )
            scrollbar.config(command=listbox.yview)
            listbox.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            all_queries = list(queries)

            def _refresh_list(filtered):
                listbox.delete(0, tk.END)
                for q in filtered:
                    listbox.insert(tk.END, q)
                if filtered:
                    listbox.selection_set(0)
                    listbox.activate(0)

            def _apply_filter(_event=None):
                term = (filter_var.get() or "").strip().lower()
                if not term:
                    filtered = all_queries
                else:
                    filtered = [q for q in all_queries if term in q.lower()]
                _refresh_list(filtered)

            _refresh_list(all_queries)
            try:
                filter_entry.focus_set()
            except Exception:
                listbox.focus_set()

            filter_entry.bind("<KeyRelease>", _apply_filter)

            btn_frame = tk.Frame(chooser)
            btn_frame.pack(pady=(0, 8))

            def on_ok(event=None):
                selection = listbox.curselection()
                if not selection:
                    try:
                        chooser.bell()
                    except Exception:
                        pass
                    return "break"
                chosen["query"] = self._normalize_search_query_text((listbox.get(selection[0]) or "").strip()) or None
                chooser.destroy()
                return "break"

            def on_cancel(event=None):
                chooser.destroy()
                return "break"

            tk.Button(btn_frame, text="Use Selected", width=12, command=on_ok).pack(side="left", padx=6)
            tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

            listbox.bind("<Double-Button-1>", on_ok)
            listbox.bind("<Return>", on_ok)
            listbox.bind("<KP_Enter>", on_ok)
            chooser.bind("<Escape>", on_cancel)

            self._fit_dialog_to_screen(chooser, parent=parent, min_w=520, min_h=320)

            chooser.wait_window(chooser)
            return chosen["query"]
        finally:
            self._set_search_dialog_active(False)

    def ask_remote_search_query(self) -> Tuple[Optional[str], Optional[int], Optional[int], bool]:
        """
        Ask the user for a GitHub search query (anime name) and an optional episode hint.

        Hint parsing uses extract_season_episode_global() so the user can enter:
        - "s2e1" (season+episode)
        - "130" (global episode)
        - "e254" (global episode)
        """
        result = {"query": None, "season": None, "episode": None, "is_movie": False}

        hide_startup_overlay()
        self._set_search_dialog_active(True)
        try:
            root = getattr(tk, "_default_root", None)
            dlg = tk.Toplevel(root) if root is not None else tk.Toplevel()
            dlg.title("Remote Subtitle Search")
            dlg.attributes("-topmost", True)
            if root is not None:
                try:
                    dlg.transient(root)
                except Exception:
                    pass
            dlg.grab_set()
            dlg.resizable(True, True)
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(dlg, text="Anime search query (folder name / season 1 base):", anchor="w").grid(
                row=0, column=0, sticky="w", padx=8, pady=(8, 2)
            )
            query_row = tk.Frame(dlg)
            query_row.grid(row=1, column=0, sticky="ew", padx=8)
            query_row.grid_columnconfigure(0, weight=1)
            query_entry = tk.Entry(query_row, width=25)
            query_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))

            def choose_cached():
                cached_query = self._ask_cached_github_search_query(dlg)
                if not cached_query:
                    return
                query_entry.delete(0, tk.END)
                query_entry.insert(0, cached_query)
                query_entry.icursor(tk.END)

            tk.Button(query_row, text="Use Saved...", width=12, command=choose_cached).grid(row=0, column=1)
            try:
                dlg.after(0, lambda: query_entry.focus_set())
            except Exception:
                try:
                    query_entry.focus_set()
                except Exception:
                    pass

            tk.Label(dlg, text="Episode hint (optional: s2e1 or 130):", anchor="w").grid(
                row=2, column=0, sticky="w", padx=8, pady=(8, 2)
            )
            hint_entry = tk.Entry(dlg, width=30)
            hint_entry.grid(row=3, column=0, sticky="w", padx=8)
            movie_var = tk.BooleanVar(value=False)
            tk.Checkbutton(
                dlg,
                text="Movie search (anime/drama movie folders first)",
                variable=movie_var,
                anchor="w",
            ).grid(row=4, column=0, sticky="w", padx=8, pady=(6, 0))

            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=5, column=0, pady=10)

            def on_ok():
                q = self._normalize_search_query_text(query_entry.get() or "")
                hint_raw = (hint_entry.get() or "").strip().lower()
                s = e = g = None
                if hint_raw:
                    s, e, g = self.extract_season_episode_global(hint_raw)
                # We only store (season, episode) here; when only a global is present we store it in "episode"
                # so remote init can resolve it via remote_episode_map_global.
                if s is None and e is None and g is not None:
                    e = int(g)
                result["query"], result["season"], result["episode"] = (q or None, s, e)
                result["is_movie"] = bool(movie_var.get())
                dlg.destroy()

            def on_cancel():
                dlg.destroy()

            tk.Button(btn_frame, text="OK", width=10, command=on_ok).pack(side="left", padx=6)
            tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)

            def on_enter(event=None):
                on_ok()
                return "break"

            query_entry.bind("<Return>", on_enter)
            query_entry.bind("<KP_Enter>", on_enter)
            hint_entry.bind("<Return>", on_enter)
            hint_entry.bind("<KP_Enter>", on_enter)
            dlg.bind("<Return>", on_enter)
            dlg.bind("<KP_Enter>", on_enter)

            self._fit_dialog_to_screen(dlg, parent=root, min_w=620, min_h=300)
            dlg.wait_window(dlg)
            return result["query"], result["season"], result["episode"], bool(result.get("is_movie"))
        finally:
            self._set_search_dialog_active(False)
            show_startup_overlay()
    
    def ask_local_srt_file(self) -> Optional[str]:
        hide_startup_overlay()
        try:
            window = tk.Tk(); window.withdraw(); window.attributes("-topmost", True)
            path = filedialog.askopenfilename(
                parent=window,
                title="Select Subtitle File",
                initialdir=self.local_srt_dir,
                filetypes=[
                    ("Subtitle files", "*.srt *.ass *.ssa"),
                    ("SubRip files", "*.srt"),
                    ("ASS/SSA files", "*.ass *.ssa"),
                    ("All Files", "*.*"),
                ]
            )
            window.destroy()
            if not path:
                return None
            return path
        except Exception:
            logger.exception("Subtitle file selection failed")
            return None
        finally:
            show_startup_overlay()

    def calculate_geometry(self):
        font, ruby_font = self._get_subtitle_fonts()

        def _measure_line_width(segments):
            width = 0
            for base, ruby in segments:
                base_w = font.measure(base)
                ruby_w = ruby_font.measure(ruby) if ruby else 0
                width += max(base_w, ruby_w)
            return width

        max_width = 0
        for _clean, _time, top, bottom in self.display_data:
            for segments in (top, bottom):
                if not segments:
                    continue
                width = _measure_line_width(segments)
                if width > max_width:
                    max_width = width

        line_height = font.metrics("linespace")
        ruby_height = int(line_height * 0.6)
        pad_x = 5
        total_height = ruby_height * 2 + line_height * 2

        try:
            wrap_limit_px = int(self.config.get("SUBTITLE_WRAP_LIMIT_PX") or 0)
        except Exception:
            wrap_limit_px = 0

        if wrap_limit_px > 0:
            renderer_padding = 40
            max_width = min(max_width, wrap_limit_px)
            total_width = max_width + 2 * renderer_padding
        else:
            total_width  = max_width + 2 * pad_x

        return (total_width, total_height)
#endregion -------------------------episode / season switching-----------------------------


################ TODO: figure out the anime name of first season #################
##### workaround user gives always s1 when pasting URL##########
#look for same string in folder name and file name? Could work but not everytime


#region -------------------------remote handling-----------------------------
    def _schedule_prefetch_window(self, center_global: Optional[int]) -> None:
        """
        Schedule a background prefetch of the configured window around center_global.

        This never expands beyond one window. It can be delayed via DOWNLOAD_PREFETCH_DELAY_MS.
        """
        if center_global is None:
            return
        try:
            window = self.config.get("DOWNLOAD_WINDOW")
        except Exception:
            window = 0
        if window <= 0:
            return

        # Cancel any pending prefetch (e.g. user quickly changed episodes).
        t = getattr(self, "_prefetch_timer", None)
        if t is not None:
            try:
                t.cancel()
            except Exception:
                pass
            self._prefetch_timer = None

        delay_ms = self.config.get("DOWNLOAD_PREFETCH_DELAY_MS")
        if delay_ms <= 0:
            self.download_window_around_global(int(center_global), window=int(window), async_download=True)
            return

        def _run():
            try:
                self.download_window_around_global(int(center_global), window=int(window), async_download=True)
            except Exception:
                logger.exception("Prefetch window download failed (global %s)", center_global)

        timer = threading.Timer(delay_ms / 1000.0, _run)
        timer.daemon = True
        self._prefetch_timer = timer
        timer.start()

    def _ensure_download_workers(self) -> None:
        """
        Start a small fixed set of background downloader threads.

        This avoids spawning one thread per file (which can cause short CPU spikes).
        """
        if getattr(self, "_dl_workers_started", False):
            return
        self._dl_workers_started = True

        self._dl_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._dl_seen_lock = threading.Lock()
        self._dl_seen: set[str] = set()  # local_path values currently queued/in-progress

        max_workers = self.config.get("DOWNLOAD_MAX_WORKERS")
        self._dl_workers: list[threading.Thread] = []
        for i in range(int(max_workers)):
            t = threading.Thread(target=self._download_worker_loop, daemon=True, name=f"subtitle-dl-{i+1}")
            t.start()
            self._dl_workers.append(t)

    def _download_worker_loop(self) -> None:
        # Keep a session per worker to reuse TLS connections (less CPU than requests.get per file).
        try:
            session = requests.Session()
        except Exception:
            session = None
        while True:
            job = self._dl_q.get()
            try:
                raw_url, local_path = job
                try:
                    if not os.path.exists(local_path):
                        self._download_file(raw_url, local_path, session=session)
                except Exception:
                    logger.exception("Background download failed for %s", raw_url)
                finally:
                    try:
                        with self._dl_seen_lock:
                            self._dl_seen.discard(local_path)
                    except Exception:
                        pass

                try:
                    throttle_ms = self.config.get("DOWNLOAD_THROTTLE_MS")
                    if throttle_ms:
                        time.sleep(throttle_ms / 1000.0)
                except Exception:
                    pass
            finally:
                try:
                    self._dl_q.task_done()
                except Exception:
                    pass

    def _enqueue_download(self, raw_url: str, local_path: str) -> bool:
        if not raw_url or not local_path:
            return False
        if os.path.exists(local_path):
            return False

        self._ensure_download_workers()
        try:
            with self._dl_seen_lock:
                if local_path in self._dl_seen:
                    return False
                self._dl_seen.add(local_path)
        except Exception:
            # If we can't de-dupe, still enqueue (worst case: duplicates).
            pass
        try:
            self._dl_q.put((raw_url, local_path))
            return True
        except Exception:
            try:
                with self._dl_seen_lock:
                    self._dl_seen.discard(local_path)
            except Exception:
                pass
            return False

    def _initialize_remote_path(
        self,
        init_url: Optional[str] = None,
        hint: Optional[Tuple[Optional[int], Optional[int]]] = None,
        movie_mode: bool = False,
    ) -> Optional[str]:
        url = init_url
        prompt_hint = None
        prompt_movie_mode = bool(movie_mode)
        if not url:
            url, h_s, h_e, prompt_movie_mode = self.ask_remote_srt_with_hint()
            prompt_hint = (h_s, h_e) if (h_s is not None or h_e is not None) else None
            if not url:
                return None

        use_movie_mode = bool(movie_mode or prompt_movie_mode)

        # download/gather metadata for the chosen URL, then build maps once
        self._extract_and_set_remote_episode_metadata(url)

        remote_norm = (self.remote_path or "").replace("\\", "/")
        is_movie_url = (
            "/anime_movie/" in remote_norm
            or "/drama_movie/" in remote_norm
            or remote_norm.startswith("subtitles/anime_movie/")
            or remote_norm.startswith("subtitles/drama_movie/")
        )

        # For direct movie file URLs, load exactly that file (avoid season map rebuilds that can pick old anime state).
        try:
            parsed = self._parse_github_url(url)
        except Exception:
            parsed = {}
        if (use_movie_mode or is_movie_url) and parsed.get("is_file") and self.remote_path:
            try:
                season_dir = self._season_cache_dir(None)
                filename = self.sanitize_filename(os.path.basename(self.remote_path))
                local_path = os.path.join(season_dir, filename)
                raw_url = self._get_raw_url(self.remote_path)
                self._download_file(raw_url, local_path)
            except Exception:
                logger.exception("Failed to download direct movie URL: %s", self.remote_path)
                return None
            try:
                self.remote_url = url
                self.config.set("LAST_GITHUB_URL", url)
                if getattr(self, "anime_folder_name", None):
                    self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
            except Exception:
                pass
            self.all_results_items = [{
                "name": os.path.basename(self.remote_path),
                "path": self.remote_path,
                "season": None,
                "episode": None,
                "global": None,
            }]
            self.build_remote_episode_maps()
            return local_path

        try:
            self._create_remote_episode_map_per_season()
        except Exception:
            logger.exception("Failed to build remote episode map from URL: %s", url)
            return None
        self.build_remote_episode_maps()

        # allow caller-provided hint (highest priority), otherwise prefer prompt hint
        chosen_item = None
        use_hint = hint if hint is not None else prompt_hint
        if use_hint:
            hint_season, hint_episode = use_hint
            if hint_season is not None and hint_episode is not None:
                lst = getattr(self, "remote_episode_map_season", {}).get(int(hint_season), [])
                for it in lst:
                    if it.get("episode") == int(hint_episode):
                        chosen_item = it
                        break
            if chosen_item is None and hint_episode is not None:
                chosen_item = getattr(self, "remote_episode_map_global", {}).get(int(hint_episode))
            if chosen_item:
                logger.info("Using explicit hint: season=%s episode=%s -> global=%s", hint_season, hint_episode, chosen_item.get("global"))

        # only try URL/filename matching if hint did not resolve
        if not chosen_item:
            basename = os.path.basename(self.remote_path or "")
            if getattr(self, "all_results_items", None):
                for it in self.all_results_items:
                    if it.get("path") == self.remote_path or it.get("name") == basename:
                        chosen_item = it
                        break

        # if still not chosen, try to infer from the parsed filename (global or season/episode)
        if not chosen_item:
            basename = os.path.basename(self.remote_path or "")
            s_parsed, e_parsed, g_parsed = self.extract_season_episode_global(basename)
            if g_parsed is not None:
                chosen_item = getattr(self, "remote_episode_map_global", {}).get(int(g_parsed))
            elif s_parsed is not None and e_parsed is not None:
                lst = getattr(self, "remote_episode_map_season", {}).get(int(s_parsed), [])
                for it in lst:
                    if it.get("episode") == int(e_parsed):
                        chosen_item = it
                        break

        # fallback: prefer first episode of current season
        if not chosen_item and getattr(self, "remote_episode_map_season", None) and getattr(self, "current_season", None) is not None:
            lst = self.remote_episode_map_season.get(self.current_season, [])
            if lst:
                chosen_item = lst[0]

        # final fallback: lowest global available
        if not chosen_item and getattr(self, "remote_episode_map_global", None):
            keys = sorted(self.remote_episode_map_global.keys())
            if keys:
                chosen_item = self.remote_episode_map_global[keys[0]]

        # If nothing has episode numbering (movies / standalone files), pick the best candidate by score.
        if not chosen_item and getattr(self, "all_results_items", None):
            try:
                chosen_item = max(
                    self.all_results_items,
                    key=lambda it: self._subtitle_candidate_score(it.get("name"), it.get("path")),
                )
                logger.info("No episode numbering found; treating as movie/standalone. Selected: %s", chosen_item.get("path") or chosen_item.get("name"))
            except Exception:
                chosen_item = None

        if not chosen_item:
            logger.error("No remote subtitle candidates found for URL: %s", url)
            return None

        # extract target properties and attempt to resolve season/episode if missing
        target_remote_path = chosen_item.get("path")
        target_season = chosen_item.get("season")
        target_episode = chosen_item.get("episode")
        target_global = chosen_item.get("global")
        if (target_season is None or target_episode is None) and target_global is not None:
            mapped = self.global_to_local(int(target_global))
            if mapped != (None, None):
                target_season, target_episode = mapped

        if not target_remote_path:
            logger.error("Chosen item has no path: %s", chosen_item)
            return None

        # synchronous download of chosen item so UI can load it immediately
        try:
            season_dir = self._season_cache_dir(target_season) if target_season is not None else self._season_cache_dir()
            filename = self.sanitize_filename(os.path.basename(target_remote_path))
            local_path = os.path.join(season_dir, filename)
            raw_url = self._get_raw_url(target_remote_path)
            self._download_file(raw_url, local_path)
        except Exception:
            logger.exception("Failed to download chosen remote episode: %s", target_remote_path)
            return None

        # Persist remote selection (do not load here; __init__ loads exactly once)
        try:
            self.remote_url = url
            try:
                self.config.set("LAST_GITHUB_URL", url)
                if getattr(self, "anime_folder_name", None):
                    self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
            except Exception:
                logger.debug("Failed to persist LAST_GITHUB_URL/LAST_ANIME_NAME")
        except Exception:
            logger.exception("Failed to persist remote selection metadata for: %s", local_path)
            return None

        # kick off async windowed download around the chosen global (Â±20)
        try:
            center_global = target_global
            if center_global is None and target_season is not None and target_episode is not None:
                center_global = self.local_to_global(target_season, target_episode)
            if center_global is not None:
                self._schedule_prefetch_window(center_global)
        except Exception:
            logger.exception("Failed to start windowed background downloads")

        return local_path


     
    def _extract_and_set_remote_episode_metadata(self, remote_url):
        self.config.set("LAST_GITHUB_URL", remote_url)
        github_dict = self._parse_github_url(remote_url)
        self.github_owner = github_dict["owner"]
        self.github_repo  = github_dict["repo"]
        self.github_ref   = github_dict["ref"]
        self.remote_path = github_dict["path"]

        s, e, global_e = self.extract_season_episode_global(os.path.basename(self.remote_path))
        if s is None and e is None and global_e is not None:
            self.current_season, self.current_episode = None, int(global_e)
        else:
            self.current_season, self.current_episode = s, e
        self.anime_folder_name = self.config.get("LAST_ANIME_NAME")
        url_anime_name = self._extract_anime_name_from_url(self.remote_path)

        remote_norm = (self.remote_path or "").replace("\\", "/")
        is_movie_url = (
            "/anime_movie/" in remote_norm
            or "/drama_movie/" in remote_norm
            or remote_norm.startswith("subtitles/anime_movie/")
            or remote_norm.startswith("subtitles/drama_movie/")
        )

        # TV shows: keep the name of season 1 (user preference), since later seasons can have
        # slightly different folder names. Movies: always use the movie folder name.
        if url_anime_name and (is_movie_url or self.current_season == 1 or not self.anime_folder_name):
            self.anime_folder_name = url_anime_name
            self.config.set("LAST_ANIME_NAME", url_anime_name)
        return

    def _initialize_remote_from_search_query(
        self,
        anime_query: str,
        hint: Optional[Tuple[Optional[int], Optional[int]]] = None,
        movie_mode: bool = False,
    ) -> Optional[str]:
        """
        Remote "init" without a URL: use a user-provided anime query to build the episode map,
        pick an initial episode (hint > first global), download it to cache, then start window downloads.

        This stores a synthesized LAST_GITHUB_URL pointing to the chosen file so the next startup
        can resume in URL-based remote init.
        """
        anime_query = self._normalize_search_query_text(anime_query or "")
        if not anime_query:
            return None

        # Default repo/ref for kitsunekko mirror if nothing else is known yet.
        self.github_owner = getattr(self, "github_owner", None) or self.config.get("GITHUB_OWNER") or "Ajatt-Tools"
        self.github_repo = getattr(self, "github_repo", None) or self.config.get("GITHUB_REPO") or "kitsunekko-mirror"
        self.github_ref = getattr(self, "github_ref", None) or self.config.get("GITHUB_REF") or "main"

        self.anime_folder_name = anime_query
        try:
            self.config.set("LAST_ANIME_NAME", self.anime_folder_name)
        except Exception:
            pass

        if movie_mode:
            search_paths = (
                "subtitles/anime_movie",
                "subtitles/drama_movie",
                "subtitles/anime_tv",
                "subtitles/drama_tv",
            )
            merged: List[Dict] = []
            seen_paths: set[str] = set()
            for sub_path in search_paths:
                for it in self._search_remote_candidates_in_path(anime_query, sub_path):
                    p = it.get("path")
                    if not p or p in seen_paths:
                        continue
                    seen_paths.add(p)
                    merged.append(it)
            self.all_results_items = merged
            if not self.all_results_items:
                logger.info("No movie-mode remote subtitle candidates found for search query: %s", anime_query)
                return None
            try:
                self.all_results_items.sort(key=self.sort_key_per_season)
            except Exception:
                pass
        else:
            # Build / load the remote episode listing and maps.
            try:
                self._create_remote_episode_map_per_season()
            except Exception:
                logger.exception("Failed to build remote episode map from search query: %s", anime_query)
                return None
        self.build_remote_episode_maps()

        chosen_item = None
        if hint:
            hint_season, hint_episode = hint
            if hint_season is not None and hint_episode is not None:
                lst = getattr(self, "remote_episode_map_season", {}).get(int(hint_season), [])
                for it in lst:
                    if it.get("episode") == int(hint_episode):
                        chosen_item = it
                        break
            if chosen_item is None and hint_episode is not None:
                chosen_item = getattr(self, "remote_episode_map_global", {}).get(int(hint_episode))

        if not chosen_item and getattr(self, "remote_episode_map_global", None):
            keys = sorted(self.remote_episode_map_global.keys())
            if keys:
                chosen_item = self.remote_episode_map_global[keys[0]]

        if not chosen_item and getattr(self, "all_results_items", None):
            try:
                chosen_item = max(
                    self.all_results_items,
                    key=lambda it: self._subtitle_candidate_score(it.get("name"), it.get("path")),
                )
            except Exception:
                chosen_item = None

        if not chosen_item:
            logger.info("No remote subtitle candidates found for search query: %s", anime_query)
            return None

        target_remote_path = chosen_item.get("path")
        target_season = chosen_item.get("season")
        target_episode = chosen_item.get("episode")
        target_global = chosen_item.get("global")

        if movie_mode and target_season is None and target_episode is None and target_global is None:
            self.current_season, self.current_episode = None, None
        elif target_season is None and target_episode is None and target_global is not None:
            # If the chosen item only has global numbering, keep current_episode set so UI doesn't treat it as a movie.
            self.current_season, self.current_episode = None, int(target_global)
        else:
            self.current_season, self.current_episode = target_season, target_episode

        if not target_remote_path:
            return None

        # Download synchronously so we can load immediately.
        try:
            season_dir = self._season_cache_dir(target_season) if target_season is not None else self._season_cache_dir()
            filename = self.sanitize_filename(os.path.basename(target_remote_path))
            local_path = os.path.join(season_dir, filename)
            raw_url = self._get_raw_url(target_remote_path)
            self._download_file(raw_url, local_path)
        except Exception:
            logger.exception("Failed to download chosen remote episode for search query: %s", target_remote_path)
            return None

        # Persist a concrete URL for restart-resume behavior.
        try:
            blob_url = f"https://github.com/{self.github_owner}/{self.github_repo}/blob/{self.github_ref}/{target_remote_path}"
            self.remote_url = blob_url
            self.config.set("LAST_GITHUB_URL", blob_url)
        except Exception:
            pass

        # Start background download window around the chosen global if available.
        if not movie_mode:
            try:
                center_global = target_global
                if center_global is None and target_season is not None and target_episode is not None:
                    center_global = self.local_to_global(target_season, target_episode)
                if center_global is not None:
                    self._schedule_prefetch_window(center_global)
            except Exception:
                logger.exception("Failed to start windowed background downloads for search-query init")

        return local_path
    
    def _search_remote_candidates_in_path(self, anime_query: str, repo_sub_path: str) -> List[Dict]:
        """
        Search GitHub code API for subtitle files under a specific path.
        Returns normalized items with parsed season/episode/global metadata.
        """
        anime_query = self._normalize_search_query_text(anime_query or "")
        repo_sub_path = (repo_sub_path or "").strip().strip("/")
        if not anime_query or not repo_sub_path:
            return []

        owner = getattr(self, "github_owner", None) or self.config.get("GITHUB_OWNER") or "Ajatt-Tools"
        repo = getattr(self, "github_repo", None) or self.config.get("GITHUB_REPO") or "kitsunekko-mirror"
        if not owner or not repo:
            return []

        api_url = "https://api.github.com/search/code"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "subtitle-searcher",
        }
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"

        per_page = 100
        out: List[Dict] = []
        seen_paths: set[str] = set()

        session = requests.Session()
        session.headers.update(headers)

        for ext in ("srt", "ass"):
            q = (f'repo:{owner}/{repo} '
                 f'path:{repo_sub_path} extension:{ext} in:path "{anime_query}"')
            page = 1
            while True:
                params = {"q": q, "per_page": per_page, "page": page}
                try:
                    resp = session.get(api_url, params=params, timeout=15)
                except requests.RequestException:
                    logger.exception("GitHub path search failed: query=%s", q)
                    break

                if resp.status_code != 200:
                    logger.error("GitHub path search failed (%s): %s", resp.status_code, resp.text)
                    break

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break

                for it in items:
                    path = it.get("path")
                    if not path or path in seen_paths:
                        continue
                    seen_paths.add(path)
                    name = os.path.basename(path or it.get("name") or "")
                    s, e, g = self.extract_season_episode_global(name)
                    if s is None and e is None and g is not None:
                        e = int(g)
                    out.append({
                        "name": name or it.get("name"),
                        "path": path,
                        "season": s,
                        "episode": e,
                        "global": g,
                    })

                if len(items) < per_page:
                    break
                page += 1
                time.sleep(0.1)

        try:
            out.sort(key=self.sort_key_per_season)
        except Exception:
            pass
        return out

    def search_remote_movie_candidates(self, anime_query: str) -> List[Dict]:
        """
        Search order for movie subtitles:
        1) subtitles/anime_movie
        2) subtitles/drama_movie (fallback only if #1 has zero results)
        """
        anime_query = self._normalize_search_query_text(anime_query or "")
        if not anime_query:
            return []

        results = self._search_remote_candidates_in_path(anime_query, "subtitles/anime_movie")
        if results:
            return results
        logger.info("No results in subtitles/anime_movie for '%s'; falling back to subtitles/drama_movie", anime_query)
        return self._search_remote_candidates_in_path(anime_query, "subtitles/drama_movie")

    def search_remote_drama_tv_candidates(self, anime_query: str) -> List[Dict]:
        """
        Search drama TV subtitles under subtitles/drama_tv.
        """
        anime_query = self._normalize_search_query_text(anime_query or "")
        if not anime_query:
            return []
        return self._search_remote_candidates_in_path(anime_query, "subtitles/drama_tv")
    

    def _trying_search_queries(self):
        #season and episode must be in the format of the github s02e0001 or e01 and so on
        url = self.config.get("LAST_GITHUB_URL")
        github_dict = self._parse_github_url(url)
        self.github_owner = github_dict["owner"]
        self.github_repo  = github_dict["repo"]
        self.anime_folder_name = self.config.get("LAST_ANIME_NAME")
        api_url = "https://api.github.com/search/code"
        headers = {"Accept": "application/vnd.github.v3+json",
                   "Authorization": f"token {self.github_token}",
                   "User-Agent": "subtitle-searcher"}
        per_page = 100
        search_query = f"{self.anime_folder_name}"
        q = (f'repo:{self.github_owner}/{self.github_repo}'
             f' path:subtitles/anime_tv extension:ass extension:srt in:path "{search_query}"')
        params = {"q": q, "per_page": per_page}
        all_results_items: List[Dict] = []
        page = 1
        logger.info(f"Building comprehensive episode map for {q}...")
        while True:
            params["page"] = page
            resp = requests.get(api_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if not items and page == 1:
                    print(f"GitHub search returned 0 items for query: {q}")
                for it in items:
                    name = os.path.basename(it.get("path") or it.get("name") or "")
                    s, e, global_e = self.extract_season_episode_global(name)
                    if s is None and e is None and global_e is None:
                        self._log_unparsed_filename(name=name, path=it.get("path"), reason="github_search_debug")
                    all_results_items.append({
                        "name": name,
                        "path": it.get("path"),
                        "season": s,
                        "episode": e,
                        "global": global_e,
                    })
                if len(items) < per_page:
                    break
                page += 1
                time.sleep(0.1)
                continue
            else:
                logger.error("GitHub search failed: %s", resp.text)
                break
        season_offset, local_numbering, season_len_est, season_len_density = self.compute_season_offsets_per_season(all_results_items)
        self.assign_globals_per_season(all_results_items, season_offset, local_numbering, season_len_est, season_len_density)
        all_results_items.sort(key=self.sort_key_per_season)
        sxexx_to_gxx = {}
        for it in all_results_items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            if s is not None and e is not None and g is not None:
                key = f"S{int(s):02d}E{int(e):02d}"
                # prefer lowest conflict-free mapping (but if duplicates exist we keep the first seen)
                if key not in sxexx_to_gxx:
                    sxexx_to_gxx[key] = int(g)

        if all_results_items:
            safe_name = self._safe_search_query_name(self.anime_folder_name or "")
            folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"github_search")
            os.makedirs(folder_dir, exist_ok=True)
            json_path = os.path.join(folder_dir, f"github_search_{safe_name}.json")
            payload = {
                "anime_query": self._normalize_search_query_text(self.anime_folder_name or ""),
                "last search": q,
                "repo": f"{self.github_owner}/{self.github_repo}",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "result_count": len(all_results_items),
                "items": all_results_items,
                "sxexx_to_gxx": sxexx_to_gxx
            }
            try:
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                print(f"Wrote diagnostics to {json_path}")
            except Exception as e:
                print("Failed to write diagnostics JSON:", e)
        self.all_results_items = all_results_items
        return

    def _specific_episode_search(self,s,e):
        #season and episode must be in the format of the github s02e0001 or e01 and so on
        api_url = "https://api.github.com/search/code"
        headers = {"Accept": "application/vnd.github.v3+json",
                   "Authorization": f"token {self.github_token}",
                   "User-Agent": "subtitle-searcher"}
        per_page = 100
        if s is not None and e is not None:
            logger.info(f"Searching [S{s}E{e}] for {self.anime_folder_name}...")
            search_query = f"{self.anime_folder_name} s{s}e{e}"
        elif e is not None:
            search_query = f"{self.anime_folder_name} e{e}"
        else: search_query = f"{self.anime_folder_name}"
        q = (f'repo:{self.github_owner}/{self.github_repo}'
             f' path:subtitles/anime_tv extension:srt in:path "{search_query}"')
        params = {"q": q, "per_page": per_page}
        results: List[Dict] = []
        page = 1
        while True:
            params["page"] = page
            resp = requests.get(api_url, headers=headers, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if not items and page == 1:
                    print(f"GitHub search returned 0 items for query: {q}")
                for it in items:
                    results.append({
                        "name": it.get("name"),
                        "path": it.get("path")
                    })
                if len(items) < per_page:
                    break
                page += 1
                time.sleep(0.1)
                continue
            else:
                logger.error("GitHub search failed: %s", resp.text)
                break

    def _create_remote_episode_map_per_season(self):
        #add end_season and anime name searches in the gui
        end_season = 50
        # self.anime_folder_name = "Daini no Shokugyo"
        logger.info(f"Building comprehensive episode map for {self.anime_folder_name}...")

        # If we already have a cached episode map JSON for this anime, prefer it over
        # hitting the GitHub Search API again (avoids rate limits / repeat work).
        safe_name = self._safe_search_query_name(self.anime_folder_name or "")
        folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "github_search")
        cache_path = os.path.join(folder_dir, f"github_search_{safe_name}.json")
        if not os.path.isfile(cache_path) and os.path.isdir(folder_dir):
            normalized_query = self._normalize_search_query_text(self.anime_folder_name or "")
            prefix = "github_search_"
            suffix = ".json"
            for fn in os.listdir(folder_dir):
                if not (fn.startswith(prefix) and fn.endswith(suffix)):
                    continue
                legacy_safe = fn[len(prefix):-len(suffix)]
                if not legacy_safe:
                    continue
                legacy_query = self._normalize_search_query_text(legacy_safe.replace("_", " "))
                if legacy_query == normalized_query:
                    cache_path = os.path.join(folder_dir, fn)
                    break
        if os.path.isfile(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
                repo = payload.get("repo")
                expected_repo = f"{getattr(self, 'github_owner', None)}/{getattr(self, 'github_repo', None)}"
                if repo and expected_repo and repo != expected_repo:
                    logger.info("Cached search exists but repo mismatch (%s != %s); ignoring: %s", repo, expected_repo, cache_path)
                else:
                    cached_items = payload.get("items")
                    if isinstance(cached_items, list) and cached_items:

                        # Rewriting a large JSON and re-running regex parsing can be noticeably CPU-heavy.
                        out_items: List[Dict] = []
                        for it in cached_items:
                            if not isinstance(it, dict):
                                continue
                            if not it.get("name") and it.get("path"):
                                it = dict(it)
                                it["name"] = os.path.basename(it["path"])
                            out_items.append(it)
                        self.all_results_items = out_items
                        return
            except Exception:
                logger.exception("Failed to load cached GitHub search results from: %s", cache_path)

        all_results_items: List[Dict] = []
        stop_reason = None
        last_rate_info = {}
        api_url = "https://api.github.com/search/code"
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "Authorization": f"token {self.github_token}",
            "User-Agent": "subtitle-searcher",
        }
        per_page = 100  
        
        def _print_rate_info(hdr):
            limit = hdr.get("X-RateLimit-Limit")
            remaining = hdr.get("X-RateLimit-Remaining")
            reset = hdr.get("X-RateLimit-Reset")
            retry_after = hdr.get("Retry-After")
            reset_time = None
            if reset:
                try:
                    reset_time = datetime.datetime.utcfromtimestamp(int(reset)).isoformat() + "Z"
                except Exception:
                    reset_time = reset
            print(f"Rate: limit={limit} remaining={remaining} reset={reset_time}")
            return {"limit": limit, "remaining": remaining, "reset": reset, "retry_after": retry_after}

        def _wait_until_reset(hdr_info):
            # honor Retry-After first
            ra = hdr_info.get("retry_after")
            if ra:
                try:
                    wait = int(ra) + 1
                except Exception:
                    wait = 60
                print(f"Server requested Retry-After {ra}s; sleeping {wait}s...")
                time.sleep(wait)
                return
            # otherwise use X-RateLimit-Reset
            reset = hdr_info.get("reset")
            if reset:
                try:
                    reset_ts = int(reset)
                    now_ts = int(time.time())
                    wait = max(reset_ts - now_ts + 3, 3)
                    reset_time = datetime.datetime.utcfromtimestamp(reset_ts).isoformat() + "Z"
                    print(f"Sleeping {wait}s until rate reset at {reset_time}...")
                    time.sleep(wait)
                    return
                except Exception:
                    pass
            # fallback
            print("No reset info available; sleeping 60s as fallback...")
            time.sleep(60)
            return
        
        session = requests.Session()
        session.headers.update(headers)

        provider_early_stop_enabled = bool(self.config.get("SEASON_PROVIDER_EARLY_STOP_ENABLED") or False)
        try:
            raw_min_found = self.config.get("SEASON_PROVIDER_EARLY_STOP_MIN_FOUND_SEASONS")
            provider_early_stop_min_found_seasons = int(raw_min_found) if raw_min_found is not None else 1
        except Exception:
            provider_early_stop_min_found_seasons = 1
        provider_early_stop_min_found_seasons = max(1, provider_early_stop_min_found_seasons)

        # Provider "try" that succeeded for season 1 (1=Netflix, 2=Amazon).
        primary_provider_try = None
        primary_provider_found_seasons = 0

        season = 1
        while True: #search season until none found
            logger.info(f"\nSearching season {season:02d}")
            found_any_for_season = False
            old_length = len(all_results_items)
            season_searching = True
            stop_season_loop = False

            strict_provider_only = (
                provider_early_stop_enabled
                and primary_provider_try in (1, 2)
                and season > 1
                and primary_provider_found_seasons >= provider_early_stop_min_found_seasons
            )

            # Tries: 1=Netflix, 2=Amazon, 5=unseasoned fallback (only for season 1).
            if strict_provider_only:
                tries_to_run = [primary_provider_try]
            else:
                if primary_provider_try == 2:
                    tries_to_run = [2, 1]
                else:
                    tries_to_run = [1, 2]
                if season == 1:
                    tries_to_run.append(5)

            for tries in tries_to_run:  # if 0 hits try amazon instead of netflix then without both and so on can add more fallbacks later
                if tries == 1:
                    search_query = f"{self.anime_folder_name} s{season:02d} Netflix"
                elif tries == 2:
                    search_query = f"{self.anime_folder_name} s{season:02d} Amazon"
                elif tries == 3:
                    continue #skip for now
                    search_query = f"{self.anime_folder_name} s{season:02d} Hulu"
                elif tries == 4:
                    continue #skip for now
                    search_query = f"{self.anime_folder_name} s{season:02d}"
                elif tries == 5:
                    # continue #skip for now
                    search_query = f"{self.anime_folder_name}"
                    season_searching = False
                provider_found = False

                # Prefer .srt, but if nothing is found (ass-only anime) try .ass as a fallback.
                for ext in ("srt", "ass"):
                    q = (f'repo:{self.github_owner}/{self.github_repo}'
                         f' path:subtitles extension:{ext} in:path {search_query}')
                    params = {"q": q, "per_page": per_page}
                    page = 1
                    while True:
                        params["page"] = page
                        try: resp = session.get(api_url, params=params, timeout=15)
                        except requests.RequestException as e:# network error: stop and return what we have
                            logger.error(f"network error: {e}")
                            return
                        hdr = resp.headers
                        last_rate_info = _print_rate_info(hdr)
                        if resp.status_code == 200:
                            data = resp.json()
                            items = data.get("items", [])
                            if not items:
                                if page == 1:
                                    print(f"GitHub search returned 0 items for query: {q}")
                                break

                            provider_found = True
                            found_any_for_season = True
                            for it in items:
                                name = os.path.basename(it.get("path") or it.get("name") or "")
                                s, e, global_e = self.extract_season_episode_global(name)
                                if s is None and e is None and global_e is None:
                                    self._log_unparsed_filename(name=name, path=it.get("path"), reason="github_search")
                                all_results_items.append({
                                    "name": name,
                                    "path": it.get("path"),
                                    "season": s,
                                    "episode": e,
                                    "global": global_e,
                                })

                            # stop when fewer than per_page items returned (no more pages)
                            if len(items) < per_page:
                                break

                            page += 1

                            # Only wait when we actually need another request.
                            rem = last_rate_info.get("remaining")
                            try:
                                rem_i = int(rem) if rem is not None else None
                            except Exception:
                                rem_i = None
                            if rem_i is not None and rem_i <= 0:
                                _wait_until_reset(last_rate_info)

                            time.sleep(0.1)
                            continue
                        # Rate-limited or retryable responses: 403 / 429
                        if resp.status_code == 403 or resp.status_code == 429:
                            # try to parse message
                            try:
                                msg = resp.json().get("message", "")
                            except Exception:
                                msg = resp.text or ""
                            # honor Retry-After header if provided
                            if hdr.get("Retry-After"):
                                print("Retry-After header present; waiting as requested...")
                                _wait_until_reset(last_rate_info)
                                continue
                            # if remaining==0 or message mentions rate limit -> wait until reset
                            rem = last_rate_info.get("remaining")
                            if rem == "0" or (rem is not None and int(rem) == 0) or "rate limit" in msg.lower():
                                print("Rate limit reached; will wait until reset and then continue...")
                                _wait_until_reset(last_rate_info)
                                continue
                            # abuse detection -> wait a longer time then retry
                            if "abuse" in msg.lower():
                                print(f"Abuse detection triggered: {msg}. Sleeping 120s then retrying...")
                                time.sleep(120)
                                continue
                            logger.error("GitHub search failed: %s, %s", resp.status_code, resp.text)
                            stop_reason = f"http_{resp.status_code}"
                            stop_season_loop = True
                            break
                        # Search API 1000-results cap
                        if resp.status_code == 422:
                            print("Search API 422 (cannot access beyond the first 1000 results). Stopping and returning partial results.")
                            stop_reason = "search_api_1000_cap"
                            if not season_searching:
                                stop_season_loop = True
                            break
                        logger.error("GitHub search failed: %s, %s", resp.status_code, resp.text)
                        stop_reason = f"http_{resp.status_code}"
                        stop_season_loop = True
                        break

                    if provider_found or stop_season_loop:
                        break
                if provider_found:
                    # Remember which provider succeeded for season 1, and count how many seasons
                    # have results on that same provider (used for optional early stopping).
                    if season == 1 and tries in (1, 2) and primary_provider_try is None:
                        primary_provider_try = tries
                        primary_provider_found_seasons = 1
                        if provider_early_stop_enabled:
                            logger.info(
                                "Provider early stop enabled: season 01 succeeded with %s",
                                "Netflix" if tries == 1 else "Amazon",
                            )
                    elif tries == primary_provider_try:
                        primary_provider_found_seasons += 1
                    break
                if stop_season_loop:
                    break
            if stop_season_loop:
                break

            if strict_provider_only and not found_any_for_season and primary_provider_try in (1, 2):
                provider_label = "Netflix" if primary_provider_try == 1 else "Amazon"
                stop_reason = f"provider_early_stop:{provider_label}:S{season:02d}"
                logger.info(
                    "Provider early stop: no results for %s season %02d (after %d seasons found on that provider). Stopping season search.",
                    provider_label,
                    season,
                    primary_provider_found_seasons,
                )
                break
            if not found_any_for_season:
                logger.info(f"No providers found results for season {season:02d}, stopping.")
                break  # stop season loop entirely
            print("For season:",season, " we found ",len(all_results_items)-old_length,"files")
            if not season_searching:
                if stop_reason is None:
                    stop_reason = "fallback_unseasoned_search"
                break
            season += 1
            if season == end_season:
                break
            if self.anime_folder_name == "HUNTERÃ—HUNTER" and season == 7:
                break
            if self.anime_folder_name == "Shingeki no Kyojin" and season == 8:
                break
            if self.anime_folder_name == "One Piece" and season == 40:
                break
        season_offset, local_numbering, season_len_est, season_len_density = self.compute_season_offsets_per_season(all_results_items)
        self.assign_globals_per_season(all_results_items, season_offset, local_numbering, season_len_est, season_len_density)
        all_results_items.sort(key=self.sort_key_per_season)
        sxexx_to_gxx = {}
        for it in all_results_items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            if s is not None and e is not None and g is not None:
                key = f"S{int(s):02d}E{int(e):02d}"
                # prefer lowest conflict-free mapping (but if duplicates exist we keep the first seen)
                if key not in sxexx_to_gxx:
                    sxexx_to_gxx[key] = int(g)

        if all_results_items:
            safe_name = self._safe_search_query_name(self.anime_folder_name or "")
            folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),"github_search")
            os.makedirs(folder_dir, exist_ok=True)
            json_path = os.path.join(folder_dir, f"github_search_{safe_name}.json")
            payload = {
                "anime_query": self._normalize_search_query_text(self.anime_folder_name or ""),
                "last search": q,
                "repo": f"{self.github_owner}/{self.github_repo}",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "stop_reason": stop_reason,
                "rate_info": last_rate_info,
                "result_count": len(all_results_items),
                "items": all_results_items,
                "sxexx_to_gxx": sxexx_to_gxx
            }
            try:
                with open(json_path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                print(f"Wrote diagnostics to {json_path}")
            except Exception as e:
                print("Failed to write diagnostics JSON:", e)
        self.all_results_items = all_results_items
        return
    
    def sort_key_per_season(self, it):
        # 1) items with global first
        if it.get("global") is not None:
            return (0, it["global"])
        # 2) then season+episode
        if it.get("season") is not None and it.get("episode") is not None:
            return (1, it["season"], it["episode"])
        # 3) unknowns last
        return (2, it.get("name", ""))

    def compute_season_offsets_per_season(self, items):
        """
        Returns (season_offset, local_numbering, season_len_est, season_len_density)
        - season_offset: dict season -> offset (used when season uses local numbering)
        - local_numbering: dict season -> bool (True if season's episode numbers are local i.e. include E1)
        - season_len_est: dict season -> estimated local season length (robust against outliers like S02E074)
        - season_len_density: dict season -> density of observed episodes in [1..season_len_est]
        """
        season_eps = defaultdict(set)     # season -> set(episode numbers)
        season_eps_by_provider = defaultdict(lambda: defaultdict(set))  # season -> provider -> set(episode numbers)
        pairs = defaultdict(lambda: defaultdict(set))  # season -> episode -> set(globals)

        def _provider_token(s: str) -> str:
            s = (s or "").lower()
            if "netflix" in s:
                return "netflix"
            if "amazon" in s:
                return "amazon"
            if "hulu" in s:
                return "hulu"
            if "bandai" in s:
                return "bandai"
            if "TV" in s:
                return "TV"
            return "other"

        for it in items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            if s is None or e is None:
                continue
            season_eps[s].add(int(e))
            prov = _provider_token(it.get("name") or it.get("path") or "")
            season_eps_by_provider[s][prov].add(int(e))
            if g is not None:
                pairs[s][int(e)].add(int(g))

        # Decide whether seasons use local numbering (contain an episode 1) or not.
        local_numbering = {s: (1 in eps) for s, eps in season_eps.items()}

        # Estimate local season length (used for robust running offsets and per-item heuristics).
        # Some sources mix global numbering into "seasoned" files (e.g. S02E074 meaning global 74),
        # so using max(eps) overestimates the true local season length and breaks later season offsets.
        season_len_est = {}
        season_len_density = {}

        def _estimate_local_len(eps: set[int]) -> tuple[int, float]:
            eps = {int(x) for x in eps if x is not None and int(x) > 0}
            if not eps:
                return 0, 0.0
            mx = max(eps)
            # Not enough samples -> don't guess; treat as full span.
            if len(eps) < 10 or 1 not in eps:
                return mx, (len(eps) / mx) if mx else 0.0

            # Choose the largest k where observed density in [1..k] stays high.
            threshold = 0.80
            seen = 0
            best_k = 1
            best_density = 1.0
            for k in sorted(eps):
                seen += 1
                density = seen / k
                if density >= threshold:
                    best_k = k
                    best_density = density
            return best_k, float(best_density)

        preferred_provider_order = ("netflix", "amazon", "hulu")
        for s, eps in season_eps.items():
            if not local_numbering.get(s, False):
                continue

            eps_for_len = eps
            for prov in preferred_provider_order:
                cand = season_eps_by_provider.get(s, {}).get(prov)
                if cand and 1 in cand:
                    eps_for_len = cand
                    break

            k, dens = _estimate_local_len(eps_for_len)
            season_len_est[int(s)] = int(k)
            season_len_density[int(s)] = float(dens)

        # Compute offsets from explicit (season,episode)->global pairs (one vote per episode)
        offsets_votes = defaultdict(list)
        for s, ep_map in pairs.items():
            # For each episode in this season that has explicit global(s), create votes
            for e, gs in ep_map.items():
                # If multiple different global values exist for same (s,e), take them all once
                for gg in gs:
                    offsets_votes[s].append(int(gg) - int(e))

        season_offset = {}
        # Accept majority offsets for seasons that use local numbering
        for s, votes in offsets_votes.items():
            if not votes:
                continue
            cnt = Counter(votes)
            most_common, count = cnt.most_common(1)[0]
            # require at least one clear vote and either >=2 votes or >50% agreement
            if count >= max(1, len(votes) * 0.5):
                season_offset[s] = most_common

        # Fallback: compute cumulative running global using max episode (deduped) for previous seasons
        ordered = sorted(season_eps.keys())
        running = 0
        for s in ordered:
            if local_numbering.get(s, True):
                # If no computed offset, use running as fallback
                if s not in season_offset:
                    season_offset[s] = running
                # Use robust local season length estimate rather than max(eps) to avoid outliers
                # inflating later season offsets.
                est_len = season_len_est.get(int(s))
                if est_len is None:
                    est_len = max(season_eps[s]) if season_eps[s] else 0
                running = max(running, season_offset[s] + int(est_len))
            else:
                # season uses global numbering: update running to be at least the max global seen
                running = max(running, max(season_eps[s]) if season_eps[s] else running)

        return season_offset, local_numbering, season_len_est, season_len_density

    def assign_globals_per_season(self, items, season_offset, local_numbering, season_len_est, season_len_density):
        used_globals = set(it['global'] for it in items if it.get('global') is not None)
        season_item_counts = Counter()
        for it in items:
            s = it.get('season')
            e = it.get('episode')
            if s is None or e is None:
                continue
            season_item_counts[int(s)] += 1

        for it in items:
            if it.get('global') is not None:
                continue
            s = it.get('season')
            e = it.get('episode')
            if s is None or e is None:
                continue
            # If season uses global numbering already -> use episode value as global
            if not local_numbering.get(s, True):
                it['global'] = int(e)
            else:
                # Default mapping assumes local numbering within a season.
                # If we have strong evidence that this season has a dense low-episode range
                # (e.g. 1..50) and this particular item has an episode number far above that,
                # it's likely using global numbering despite having an Sxx prefix.
                local_len = season_len_est.get(int(s))
                dens = season_len_density.get(int(s), 0.0)
                if (local_len is not None
                        and season_item_counts.get(int(s), 0) >= 10
                        and int(local_len) >= 10
                        and float(dens) >= 0.85
                        and int(e) > int(local_len) + 2):
                    it['global'] = int(e)
                    it.setdefault('conflicts', []).append('season_mixed_numbering')
                else:
                    it['global'] = int(season_offset.get(s, 0)) + int(e)

            if it['global'] in used_globals:
                it.setdefault('conflicts', []).append('global_collision')
            used_globals.add(it['global'])

    def build_remote_episode_maps(self):
        """
        Build convenient lookup maps from self.all_results_items:
        - self.remote_episode_map_global: global_index -> item
        - self.remote_episode_map_season: season -> list[item]
        Each item contains: season, episode, global, path, name.
        """
        self.remote_episode_map_global = {}
        self.remote_episode_map_season = defaultdict(list)
        if not getattr(self, "all_results_items", None):
            return
        for it in self.all_results_items:
            s = it.get("season")
            e = it.get("episode")
            g = it.get("global")
            path = it.get("path") or it.get("name")
            name = it.get("name")
            entry = {"season": s, "episode": e, "global": g, "path": path, "name": name}
            entry["_score"] = self._subtitle_candidate_score(name, path)
            if g is not None:
                try:
                    gi = int(g)
                    prev = self.remote_episode_map_global.get(gi)
                    # Prefer higher-scored variants (JP, SxxExx) instead of "last one wins".
                    if prev is None or int(entry["_score"]) > int(prev.get("_score") or 0):
                        self.remote_episode_map_global[gi] = entry
                except Exception:
                    pass
            if s is not None:
                try:
                    self.remote_episode_map_season[int(s)].append(entry)
                except Exception:
                    pass
        # sort season lists by episode, but keep the "best" variant first for each episode
        for s, lst in self.remote_episode_map_season.items():
            lst.sort(key=lambda x: (x.get("episode") or 0, -(x.get("_score") or 0), x.get("global") or 0, x.get("name") or ""))

    def global_to_local(self, global_idx: int) -> Tuple[Optional[int], Optional[int]]:
        """Return (season, episode) for a given global index, or (None, None)."""
        if global_idx is None:
            return None, None
        if not hasattr(self, "remote_episode_map_global"):
            self.build_remote_episode_maps()
        it = self.remote_episode_map_global.get(int(global_idx))
        if not it:
            return None, None
        return (int(it["season"]) if it.get("season") is not None else None,
                int(it["episode"]) if it.get("episode") is not None else None)

    def local_to_global(self, season: Optional[int], episode: Optional[int]) -> Optional[int]:
        """Return global index for given local season/episode if known, else None."""
        if season is None or episode is None:
            return None
        if not hasattr(self, "remote_episode_map_season"):
            self.build_remote_episode_maps()
        for it in self.remote_episode_map_season.get(int(season), []):
            if it.get("episode") == int(episode) and it.get("global") is not None:
                return int(it["global"])
        return None

    def get_current_global(self) -> Optional[int]:
        """Try to determine a global index for the currently loaded subtitle."""
        # 1) try to parse from the current filename
        if getattr(self, "srt_file", None):
            _, _, g = self.extract_season_episode_global(os.path.basename(self.srt_file))
            if g:
                return int(g)
        # 2) try mapping from current season/episode
        if getattr(self, "current_season", None) and getattr(self, "current_episode", None):
            g = self.local_to_global(self.current_season, self.current_episode)
            if g:
                return g
        # 3) last-resort: if remote path corresponds to an item with global
        if getattr(self, "remote_path", None) and getattr(self, "all_results_items", None):
            basename = os.path.basename(self.remote_path)
            for it in self.all_results_items:
                if it.get("name") == basename or it.get("path") == self.remote_path:
                    if it.get("global") is not None:
                        return int(it["global"])
        return None

    def download_window_around_global(self, center_global: int, window: int, async_download: bool = True):
        """
        Download files with global indices in [center_global - window, center_global + window].
        Creates season cache dirs and downloads missing subtitle files.

        If async_download is True, downloads are queued onto a small fixed set of daemon
        worker threads (smears CPU usage and avoids spawning one thread per file).
        """
        if center_global is None:
            return []
        if not hasattr(self, "remote_episode_map_global"):
            self.build_remote_episode_maps()

        got: List[str] = []
        lo = max(1, int(center_global) - int(window))
        hi = int(center_global) + int(window)

        # Prefer downloading "closest to the center" first (helps next/prev navigation).
        ordered_globals: List[int] = [int(center_global)]
        for d in range(1, int(window) + 1):
            ordered_globals.append(int(center_global) - d)
            ordered_globals.append(int(center_global) + d)
        # Clamp + de-dup while keeping order.
        seen_g: set[int] = set()
        ordered_globals = [g for g in ordered_globals if lo <= g <= hi and (g not in seen_g and not seen_g.add(g))]

        for g in ordered_globals:
            item = self.remote_episode_map_global.get(int(g))
            if not item or not item.get("path"):
                continue
            season = item.get("season") or self.current_season
            season_dir = self._season_cache_dir(season)
            filename = self.sanitize_filename(os.path.basename(item["path"]))
            local_path = os.path.join(season_dir, filename)
            if os.path.exists(local_path):
                got.append(local_path)
                continue
            raw_url = self._get_raw_url(item["path"])
            if not async_download:
                try:
                    self._download_file(raw_url, local_path)
                    got.append(local_path)
                except Exception:
                    pass
            else:
                self._enqueue_download(raw_url, local_path)
                got.append(local_path)

        # Only refresh local index for synchronous downloads where we know files exist now.
        if not async_download:
            try:
                self.update_local_srt_files()
            except Exception:
                logger.exception("Failed to refresh local_srt_files after synchronous window download")
        return got

    def update_local_srt_files(self):
        """
        Scan cache and build self.local_srt_files: list of dicts with keys:
        'season','episode','global','path','name'
        """
        self.local_srt_files = []
        base = self._get_cache_base_dir()
        anime_dir = os.path.join(base, self.anime_folder_name) if self.anime_folder_name else base
        if not os.path.isdir(anime_dir):
            return self.local_srt_files
        for root, _dirs, files in os.walk(anime_dir):
            for fn in files:
                if not fn.lower().endswith((".srt", ".ass", ".ssa")):
                    continue
                full = os.path.join(root, fn)
                s, e, g = self.extract_season_episode_global(fn)
                if s is None and e is None and g is None:
                    self._log_unparsed_filename(name=fn, path=full, reason="local_cache_scan")
                # Normalize global-only files so they behave like episodes in the UI/navigation.
                if s is None and e is None and g is not None:
                    e = int(g)
                # if file name had no explicit global, try to map via remote maps
                if g is None and s is not None and e is not None:
                    g = self.local_to_global(s, e)
                rec = {"season": s, "episode": e, "global": g, "path": full, "name": fn}
                # Used only for deterministic selection when multiple variants exist for one episode.
                rec["_score"] = self._subtitle_candidate_score(fn, full)
                self.local_srt_files.append(rec)
        # Keep selection deterministic: prefer higher-scored (JP, SxxExx) variants when duplicates exist.
        def _sort_key(x):
            g = x.get("global")
            s = x.get("season") or 0
            e = x.get("episode") or 0
            sc = x.get("_score") or 0
            if g is not None:
                return (0, int(g), -int(sc), x.get("name") or "")
            return (1, int(s), int(e), -int(sc), x.get("name") or "")
        self.local_srt_files.sort(key=_sort_key)
        return self.local_srt_files



    def _parse_github_url(self, url: str) -> Dict[str, Optional[str]]:  
        p = urlparse(url)
        path = unquote(p.path)
        parts = path.strip('/').split('/')
        out = {'owner': None, 'repo': None, 'ref': None, 'path': None, 'is_file': False, 'filename': None}
        if p.netloc.endswith('github.com'):
            if len(parts) >= 2:
                out['owner'] = parts[0]
                out['repo'] = parts[1]
            if len(parts) >= 3 and parts[2] in ('blob', 'tree'):
                mode = parts[2]
                if len(parts) >= 4:
                    out['ref'] = parts[3]
                    internal = parts[4:]
                    out['path'] = '/'.join(internal) if internal else ''
                    if mode == 'blob' and out['path']:
                        out['is_file'] = True
                        out['filename'] = os.path.basename(out['path'])
            else:
                out['path'] = '/'.join(parts[2:]) if len(parts) > 2 else ''
        elif p.netloc == 'raw.githubusercontent.com':
            if len(parts) >= 4:
                out['owner'], out['repo'], out['ref'] = parts[0], parts[1], parts[2]
                out['path'] = '/'.join(parts[3:])
                out['is_file'] = True
                out['filename'] = os.path.basename(out['path'])
        return out

    # ---------------------- Diagnostics: unparsed filenames ----------------------
    def _get_unparsed_log_path(self) -> str:
        folder_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "github_search")
        os.makedirs(folder_dir, exist_ok=True)
        return os.path.join(folder_dir, "unparsed_episode_names.tsv")

    def _ensure_unparsed_seen_loaded(self) -> None:
        if getattr(self, "_unparsed_seen_loaded", False):
            return
        self._unparsed_seen_loaded = True
        self._unparsed_seen = set()
        log_path = self._get_unparsed_log_path()
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    line = raw.rstrip("\n")
                    if not line:
                        continue
                    parts = line.split("\t")
                    # expected: ts, anime, name, path, reason
                    key = None
                    if len(parts) >= 4:
                        key = parts[3] or parts[2]
                    elif len(parts) >= 3:
                        key = parts[2]
                    if key:
                        self._unparsed_seen.add(key)
        except FileNotFoundError:
            pass
        except Exception:
            logger.exception("Failed to read unparsed log file: %s", log_path)

    def _log_unparsed_filename(self, name: str, path: Optional[str] = None, reason: str = "") -> None:
        if not name:
            return
        lock = getattr(self, "_unparsed_log_lock", None)
        if lock is None:
            self._unparsed_log_lock = threading.Lock()
            lock = self._unparsed_log_lock

        with lock:
            self._ensure_unparsed_seen_loaded()
            log_path = self._get_unparsed_log_path()

            key = path or name
            if key in self._unparsed_seen:
                return
            self._unparsed_seen.add(key)

            def _clean(s: Optional[str]) -> str:
                s = s or ""
                return s.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()

            ts = datetime.datetime.utcnow().isoformat() + "Z"
            anime = getattr(self, "anime_folder_name", None) or ""
            line = f"{_clean(ts)}\t{_clean(anime)}\t{_clean(name)}\t{_clean(path)}\t{_clean(reason)}\n"
            try:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(line)
            except Exception:
                logger.exception("Failed to append to unparsed log file: %s", log_path)

# ---------------------- helpers: cache dirs ----------------------base
    def _season_cache_dir(self, season: Optional[int] = None) -> str:
        base = self._get_cache_base_dir()
        season_num = season if season is not None else self.current_season
        # Avoid creating "SeasonNone" folders; use Season0 for global-only numbering.
        if season_num is None:
            season_num = 0
        season_dir = os.path.join(base, self.anime_folder_name,f"Season{season_num}")
        if not os.path.exists(season_dir):
            os.makedirs(season_dir, exist_ok=True)
        return season_dir
    
    def _get_cache_base_dir(self) -> str: #get current base directory
        project_root = os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))
        base = os.path.join(project_root, "cache_github")
        os.makedirs(base, exist_ok=True)
        return base
# ---------------------- helpers: cache dirs ----------------------

# ---------------------- Helpers: parsing ----------------------
    def normalize_name(self, name: str) -> str:
        s = name.replace('\\','/').split('/')[-1]
        s = re.sub(r'\[.*?\]', '', s)
        s = re.sub(r'\{.*?\}', '', s)
        s = re.sub(r'\.(mkv|mp4|srt|ass|avi)$', '', s, flags=re.IGNORECASE)
        s = s.replace('â€“','-').replace('â€”','-')
        s = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '', s)
        tokens = re.split(r'([.\s_\-()\[\]]+)', s)
        filtered = []
        for t in tokens:
            if re.match(r'[.\s_\-()\[\]]+', t):
                filtered.append(t)
            else:
                if not self.is_noise_token(t):
                    filtered.append(t)
        s2 = ''.join(filtered).strip()
        s2 = re.sub(r'[._]+', ' ', s2)
        s2 = re.sub(r'\s+', ' ', s2).strip()
        return s2

    def _subtitle_candidate_score(self, name: Optional[str], path: Optional[str] = None) -> int:
        """
        Heuristic score to prefer "better" subtitle variants when multiple files map to the same episode/global.

        Goals (user preference):
        - Prefer Japanese subs (ja/jpn/ja[cc]) over English.
        - Prefer filenames containing SxxExx when available.
        - Keep behavior deterministic (avoid "last one wins" overwrites).
        """
        text = f"{name or ''} {path or ''}"
        t = text.lower()
        score = 0

        # Language preference (strong)
        if re.search(r'(?i)(?:^|[^a-z0-9])(jpn|ja)(?:$|[^a-z0-9])', text) or "ja[cc]" in t:
            score += 200
        if re.search(r'(?i)(?:^|[^a-z0-9])(eng|english)(?:$|[^a-z0-9])', text):
            score -= 300
        # Common compact token " en" / ".en." / "_en_" etc.
        if re.search(r'(?i)(?:^|[^a-z0-9])en(?:$|[^a-z0-9])', text):
            score -= 150

        # Prefer explicit season/episode tags in filename.
        if re.search(r'(?i)\bS\d{1,2}[ ._\-]*E\d{1,4}\b', text):
            score += 120
        # Weak positive: other episode markers (still better than nothing)
        if re.search(r'(?i)\b(?:ep|episode)\s*\.?\s*\d{1,4}\b', text):
            score += 25
        if re.search(r'(?i)\bE\d{1,4}\b', text):
            score += 15
        if re.search(r'ç¬¬\s*\d{1,4}\s*è©±', text):
            score += 20
        if re.search(r'(?:ã‚·ãƒ¼ã‚ºãƒ³|ï½¼ï½°ï½½ï¾žï¾)\s*\d{1,2}\s*[-â€â€‘â€“â€”ãƒ¼]\s*\d{1,4}', text):
            score += 30

        # Small provider preference (tie-breaker)
        if "netflix" in t:
            score += 10
        if "amazon" in t:
            score += 5

        return int(score)
    
    def is_noise_token(self, token: str) -> bool:
        t = token.lower().strip(" ._-()[]{}")
        if not t:
            return True
        if re.match(r'^\d{3,4}p$', t): return True
        if re.match(r'^\d{3,4}x\d{3,4}$', t): return True
        if t in self.NOISE_TOKENS: return True
        if re.match(r'^(x26[45]|av1|hevc|h264|aac|ac3|flac)$', t):
            return True
        if re.match(r'^(19|20)\d{2}$', t):
            return True
        return False

    def extract_season_episode_global(self, filename: str, folder_path: Optional[str] = None) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        name = filename or ""
        s = e = g = None
        n = name.replace('\u2013', '-').replace('\u2014', '-')
        def is_probable_episode_number(num: int) -> bool:
            if 1 <= num <= 1600:
                return True
        # Regexes
        # Episode numbers can be up to 4 digits for long-running shows (e.g. One Piece E1135).
        #
        # IMPORTANT: We can't rely on \b boundaries for SxxEyy because many Japanese filenames are like:
        # "åæŽ¢åµã‚³ãƒŠãƒ³S10 E1 - ç¬¬384è©±..." (no separator before "S").
        # Use an ASCII-only "not preceded by [A-Za-z0-9]" guard instead.
        re_s_e_paren = re.compile(
            r'(?xi)(?<![A-Za-z0-9])S(?P<s>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})(?!\d)'
            r'[^()\[\]]*[\(\[]\s*(?P<g>\d{1,4})\s*[\)\]]'
        )
        re_s_e = re.compile(r'(?xi)(?<![A-Za-z0-9])S(?P<s>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})(?!\d)')
        re_s_paren = re.compile(r'(?xi)(?<![A-Za-z0-9])S(?P<s>\d{1,2})(?!\d)[ ._\-]*[\(\[]\s*(?P<g>\d{1,4})\s*[\)\]]')
        re_episode_number = re.compile(r'(?xi)\b(?:ep|episode|ep\.)[ ._\-#]*(?P<num>\d{1,4})\b')
        # Similar to SxxEyy: don't rely on \b because filenames can contain "_E60_" etc.
        re_e_token = re.compile(r'(?xi)(?<![A-Za-z0-9])E(?P<num>\d{1,4})(?!\d)')
        # Japanese "ç¬¬384è©±" style global episode markers.
        # Use explicit unicode escapes to avoid source-encoding / mojibake issues.
        re_jp_episode = re.compile(r'(?x)\u7b2c\s*(?P<num>\d{1,4})\s*\u8a71')
        re_jp_season_dash = re.compile(r'(?x)(?:ã‚·ãƒ¼ã‚ºãƒ³|ï½¼ï½°ï½½ï¾žï¾)\s*(?P<s>\d{1,2})\s*[-â€â€‘â€“â€”ãƒ¼]\s*(?P<e>\d{1,4})')
        re_bracket_number = re.compile(r'[\(\[]\s*(\d{1,4})\s*[\)\]]')
        re_trailing_number = re.compile(r'(?xi)(?:[_\-. ]|^)(?P<num>\d{1,4})(?:\.[a-z0-9]{1,6})?$')
        # Common fansub pattern: "Show Name - 123 [720p].srt" (no season info -> treat as global)
        re_dash_number = re.compile(r'(?x)\s-\s(?P<num>\d{1,4})\b')
        # Compact variant: "Kyojin-19[...].ass" / "Show-76(...).srt"
        # Avoid matching resolutions like "-1080p" by rejecting letters right after the digits.
        re_dash_number_compact = re.compile(r'(?x)-(?P<num>\d{1,4})(?![A-Za-z])')

        # 1) SxxEyy (GGG) -> explicit local + bracketed global
        m = re_s_e_paren.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e')); gnum = int(m.group('g'))
                if is_probable_episode_number(gnum):
                    g = gnum
                else:
                    g = None
            except Exception:
                pass
            if s == 1 and e is not None:
                g = e
            return s, e, g

        # 2) SxxEyy -> local episode (conservative: do not treat as global unless we have
        # an explicit global marker like "ç¬¬384è©±" in the same filename).
        m = re_s_e.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
            except Exception:
                return None, None, None
            # If the filename contains an explicit global episode marker, prefer it.
            # Example: "åæŽ¢åµã‚³ãƒŠãƒ³S10 E1 - ç¬¬384è©±...srt" -> s=10,e=1,g=384.
            m_g = re_jp_episode.search(n)
            if m_g:
                try:
                    gnum = int(m_g.group("num"))
                    if is_probable_episode_number(gnum):
                        return s, e, gnum
                except Exception:
                    pass
            if s == 1:
                return s, e, e
            return s, e, None

        # 3) Sxx (GGG) -> season present, bracket likely a global index (no E present)
        m = re_s_paren.search(n)
        if m:
            try:
                s = int(m.group('s')); gnum = int(m.group('g'))
                if is_probable_episode_number(gnum):
                    g = gnum
            except Exception:
                pass
            if s == 1 and g is not None:
                return s, g, g
            return s, None, g

        # 4) textual "Season X Episode Y"
        re_season_episode_words = re.compile(r'(?xi)\bseason[ ._\-]*(?P<s>\d{1,2})[^\d]{0,12}episode[ ._\-]*(?P<e>\d{1,4})\b')
        m = re_season_episode_words.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
            except Exception:
                return None, None, None
            if s == 1:
                return s, e, e
            return s, e, None

        # 4b) Japanese "ã‚·ãƒ¼ã‚ºãƒ³X-Y" (common on some subtitle sources)
        m = re_jp_season_dash.search(n)
        if m:
            try:
                s = int(m.group('s')); e = int(m.group('e'))
            except Exception:
                return None, None, None
            if s == 1:
                return s, e, e
            return s, e, None

        # 5) "Ep 38" or "Episode 38" - if a season exists elsewhere treat as local episode; else treat as global
        m = re_episode_number.search(n)
        if m:
            try:
                num = int(m.group('num'))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None

            return None, None, num

        # 5b) standalone "E254" token - if a season exists elsewhere treat as local; else treat as global
        m = re_e_token.search(n)
        if m:
            try:
                num = int(m.group('num'))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None
            return None, None, num

        # 5c) Japanese "ç¬¬255è©±" -> global episode number
        m = re_jp_episode.search(n)
        if m:
            try:
                num = int(m.group('num'))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None
            return None, None, num

        # 6) bracketed numbers (general). If season present and no E present: bracket likely global.
        #    If season+E present we would have returned earlier; if both S and E exist earlier we prefer E as local and bracket as global.
        for br in re_bracket_number.findall(n):
            try:
                num = int(br)
                if not is_probable_episode_number(num):
                    continue
            except Exception:
                continue
            s_m = re.search(r'(?xi)\bS(?P<s>\d{1,2})\b', n)
            e_m = re.search(r'(?xi)\bS(?P<s2>\d{1,2})[ ._\-]*E(?P<e>\d{1,4})\b', n)
            if s_m and e_m:
                # filename contains SxxEyy and also bracket: treat bracket as global and return both
                s = int(s_m.group('s')); e = int(e_m.group('e')); g = num
                if s == 1:
                    return s, e, e
                return s, e, num
            if s_m:
                # season present but no explicit E -> bracket is probably the global index
                s = int(s_m.group('s'))
                if s == 1:
                    return s, num, num
                return s, None, num
            return None, None, num

        # 7) dash-number patterns (e.g. " - 123 " or "Kyojin-19[...]") as global index
        #    (or local if season is present)
        m = re_dash_number.search(n) or re_dash_number_compact.search(n)
        if m:
            try:
                num = int(m.group("num"))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None

            return None, None, num

        # 8) trailing number heuristics:
        m = re_trailing_number.search(n)
        if m:
            try:
                num = int(m.group("num"))
                if not is_probable_episode_number(num):
                    raise ValueError
            except Exception:
                return None, None, None

            s_m = re.search(r"(?xi)\bS(?P<s>\d{1,2})\b", n)
            if s_m:
                s = int(s_m.group("s"))
                if s == 1:
                    return s, num, num
                return s, num, None

            return None, None, num

        # nothing confident
        return None, None, None

    def _extract_anime_name_from_url(self, remote_path: str) -> Optional[str]:
        parts = remote_path.split("/")
        try:
            idx = parts.index("subtitles")
            return parts[idx + 2]  # folder 2 under /subtitles/
        except ValueError:
            return None

    def _get_raw_url(self, filename: str) -> str:
        return f"https://raw.githubusercontent.com/{self.github_owner}/{self.github_repo}/{self.github_ref}/{filename}"
# ---------------------- Helpers: parsing ----------------------

# ---------------------- GitHub searching / downloading ----------------------
    def download_current_episode(self, remote_path):
        file_name = self.sanitize_filename(os.path.basename(remote_path))
        season_dir = self._season_cache_dir()  # Create directory when actually downloading
        local_path = os.path.join(season_dir, file_name)
        raw_url = self._get_raw_url(remote_path)
        self._download_file(raw_url, local_path)
        return local_path

    def sanitize_filename(self,filename: str) -> str:
        # Replace invalid Windows characters with underscore
        return re.sub(r'[<>:"/\\|?*]', '_', filename)

    def download_remaining_season_async(self, season_files: List[str], current_file: str, season_dir: str, window: int = 20):
        entries_map: Dict[int, Tuple[int, str, str]] = {}
        unknowns: List[Tuple[str, str]] = []  # (fname, remote_path) for files without episode number

        for file in season_files:
            fname = self.sanitize_filename(os.path.basename(file))
            s, e, global_e = self.extract_season_episode_global(os.path.basename(file))
            if e is None:
                unknowns.append((fname, file))
                continue
            if e in entries_map:
                # keep first seen for this episode (avoid downloading multiple variants for same epi)
                continue
            entries_map[e] = (e, fname, file)

        # create a sorted list of episodes
        entries = sorted(entries_map.values(), key=lambda x: x[0])

        # locate current index by matching filename
        idx = next((i for i, (_e, fname, _rp) in enumerate(entries) if fname == current_file), None)
        if idx is None:
            # fallback: find index by episode number of current_file
            cur_e = None
            for e, fname, rp in entries:
                if fname == current_file:
                    cur_e = e
                    idx = entries.index((e, fname, rp))
                    break

        if idx is None:
            start, end = 0, min(len(entries)-1, window-1)
        else:
            start = max(0, idx - window)
            end = min(len(entries)-1, idx + window)

        to_download = entries[start:end+1]
        keep_filenames = {fname for _e, fname, _rp in to_download}

        # For safety, also include the current_file in keep_filenames
        keep_filenames.add(current_file)
        # spawn download threads for the window
        for e, fname, remote_path in to_download:
            local_path = os.path.join(season_dir, fname)
            if os.path.exists(local_path):
                continue
            threading.Thread(
                target=self._download_file,
                args=(self._get_raw_url(remote_path), local_path),
                daemon=True
            ).start()

        # optionally consider unknowns (files without parsed episode) only if season_dir is empty
        if not entries and unknowns:
            for fname, remote_path in unknowns[:window]:
                local_path = os.path.join(season_dir, fname)
                if os.path.exists(local_path):
                    continue
                threading.Thread(
                    target=self._download_file,
                    args=(self._get_raw_url(remote_path), local_path),
                    daemon=True
                ).start()

        # optional: evict files outside keep_filenames to limit disk usage
        try:
            self._evict_outside_window(season_dir, keep_filenames)
        except Exception:
            logger.exception("Failed to evict old episode files")

    def _evict_outside_window(self, season_dir: str, keep_filenames: set):
        """
        Remove files in season_dir that are not in keep_filenames.
        Safe-guards: only remove .srt and only when season_dir exists.
        """
        if not season_dir or not os.path.isdir(season_dir):
            return
        for fn in os.listdir(season_dir):
            if not fn.lower().endswith(".srt"):
                continue
            if fn in keep_filenames:
                continue
            try:
                path = os.path.join(season_dir, fn)
                os.remove(path)
                # logger.debug("Evicted old episode file: %s", path)
            except Exception:
                print("fail")
                # logger.exception("Failed to remove cached file: %s", fn)

    def _download_file(self,remote_path, local_path, session=None):
        if os.path.exists(local_path):
            return
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        try:
            getter = session.get if session is not None else requests.get
            r = getter(remote_path, timeout=15)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            if "text/html" in content_type.lower():
                raise RuntimeError(f"Downloaded HTML instead of SRT from {remote_path}")
            with open(local_path, "wb") as f:
                f.write(r.content)
        except Exception as e:
            logger.error(f"Download failed for {remote_path}: {e}")
# ---------------------- GitHub searching / downloading ----------------------
#endregion -------------------------remote handling-----------------------------

















"""
UI overlays used by SubtitlePlayer.

This module currently provides LoadingOverlay: a small runtime "please wait" overlay.
"""
import tkinter as tk
from tkinter import ttk
from typing import Optional

class LoadingOverlay:
    """
    Simple modal "loading" overlay that blocks UI interaction (grab_set).
    """
    def __init__(self, root: tk.Tk, text: str = "Loading...", modal: bool = True, anchor_window: Optional[tk.Misc] = None, y_offset: int = 0) -> None:
        self.root = root
        self.anchor_window = anchor_window
        self.y_offset = int(y_offset or 0)
        self._anchor_prev_topmost = None

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        if modal:
            self._win.grab_set()

        if self.anchor_window is not None:
            try:
                self._anchor_prev_topmost = bool(self.anchor_window.attributes("-topmost"))
            except Exception:
                self._anchor_prev_topmost = None
            try:
                self.anchor_window.attributes("-topmost", True)
                self.anchor_window.lift()
            except Exception:
                pass

        frame = tk.Frame(self._win, bg="#111111", bd=2, relief="solid")
        frame.pack(fill="both", expand=True)

        lbl = tk.Label(frame, text=text, fg="white", bg="#111111", font=("Arial", 12, "bold"))
        lbl.pack(padx=20, pady=(16, 10))

        # A looping 0..100 progress bar (not tied to actual progress).
        pb = ttk.Progressbar(frame, mode="determinate", maximum=15, value=0, length=240)
        pb.pack(padx=20, pady=(0, 16))
        try:
            pb.start(50)
        except Exception:
            pass

        _center_toplevel(self._win, root=self.root, anchor_window=self.anchor_window, y_offset=self.y_offset)

        # Force at least one paint before the caller blocks the UI thread.
        try:
            self._win.update_idletasks()
            self._win.update()
        except Exception:
            pass
        try:
            # Ensure overlay is above the anchor window.
            self._win.lift()
            self._win.attributes("-topmost", True)
        except Exception:
            pass

    def close(self) -> None:
        if not getattr(self, "_win", None):
            return
        try:
            self._win.grab_release()
        except Exception:
            pass
        try:
            self._win.destroy()
        except Exception:
            pass
        self._win = None
        # Restore anchor window topmost state.
        if self.anchor_window is not None and self._anchor_prev_topmost is not None:
            try:
                self.anchor_window.attributes("-topmost", bool(self._anchor_prev_topmost))
            except Exception:
                pass

    def hide(self) -> None:
        """Temporarily hide the overlay (e.g. while a modal dialog is shown)."""
        if not getattr(self, "_win", None):
            return
        try:
            self._win.withdraw()
        except Exception:
            pass

    def show(self) -> None:
        """Show the overlay again after hide()."""
        if not getattr(self, "_win", None):
            return
        try:
            self._win.deiconify()
            self._win.lift()
            self._win.attributes("-topmost", True)
        except Exception:
            pass

def _center_toplevel(
    win: tk.Toplevel,
    root: tk.Tk,
    anchor_window: Optional[tk.Misc] = None,
    y_offset: int = 0,
    margin: int = 20,
) -> None:
    """Center a toplevel on screen or relative to an anchor window (clamped to desktop)."""
    win.update_idletasks()
    w = int(win.winfo_reqwidth())
    h = int(win.winfo_reqheight())

    # Virtual desktop bounds (handles multi-monitor + negative origins on Windows).
    try:
        vx = int(root.winfo_vrootx())
        vy = int(root.winfo_vrooty())
        vw = int(root.winfo_vrootwidth())
        vh = int(root.winfo_vrootheight())
    except Exception:
        vx = 0
        vy = 0
        vw = int(win.winfo_screenwidth())
        vh = int(win.winfo_screenheight())

    def _primary_screen_size() -> tuple[int, int]:
        try:
            import ctypes
            user32 = ctypes.windll.user32  # pyright: ignore[reportAttributeAccessIssue]
            sw = int(user32.GetSystemMetrics(0))
            sh = int(user32.GetSystemMetrics(1))
            if sw > 0 and sh > 0:
                return sw, sh
        except Exception:
            pass
        return int(win.winfo_screenwidth()), int(win.winfo_screenheight())

    x: int
    y: int
    if anchor_window is not None:
        try:
            anchor_window.update_idletasks()
        except Exception:
            pass
        try:
            # rootx/rooty are always screen coords even for nested widgets.
            ax = int(anchor_window.winfo_rootx())
            ay = int(anchor_window.winfo_rooty())
            aw = int(anchor_window.winfo_width()) or int(anchor_window.winfo_reqwidth())
            ah = int(anchor_window.winfo_height()) or int(anchor_window.winfo_reqheight())
            cx = ax + aw / 2
            cy = ay + ah / 2
            x = int(cx - w / 2)
            y = int(cy - h / 2) + int(y_offset)
        except Exception:
            sw, sh = _primary_screen_size()
            x = int((sw - w) / 2)
            y = int((sh - h) / 2) + int(y_offset)
    else:
        sw, sh = _primary_screen_size()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2) + int(y_offset)

    x = max(vx + margin, min(x, vx + vw - margin - w))
    y = max(vy + margin, min(y, vy + vh - margin - h))
    win.geometry(f"{w}x{h}+{x}+{y}")

# ---------------------- Startup Splash Control ----------------------
# The app keeps a single "startup splash" overlay alive while background initialization runs.
# Some workflows must show Tk dialogs; while those are visible we hide the splash temporarily.
_STARTUP_OVERLAY: Optional[LoadingOverlay] = None
_STARTUP_HIDE_COUNT: int = 0


def set_startup_overlay(overlay: Optional[LoadingOverlay]) -> None:
    """
    Register the current startup overlay.
    When set to None, the hidden-counter is reset as well.
    """
    global _STARTUP_OVERLAY, _STARTUP_HIDE_COUNT
    _STARTUP_OVERLAY = overlay
    if overlay is None:
        _STARTUP_HIDE_COUNT = 0


def get_startup_overlay() -> Optional[LoadingOverlay]:
    return _STARTUP_OVERLAY


def hide_startup_overlay() -> None:
    """Re-entrant hide: nested calls are reference-counted."""
    global _STARTUP_HIDE_COUNT
    ov = _STARTUP_OVERLAY
    if ov is None:
        return
    _STARTUP_HIDE_COUNT += 1
    try:
        ov.root.after(0, ov.hide)
    except Exception:
        try:
            ov.hide()
        except Exception:
            pass


def show_startup_overlay() -> None:
    """Re-entrant show: only shows once the hide counter returns to 0."""
    global _STARTUP_HIDE_COUNT
    ov = _STARTUP_OVERLAY
    if ov is None:
        return

    _STARTUP_HIDE_COUNT = max(0, _STARTUP_HIDE_COUNT - 1)
    if _STARTUP_HIDE_COUNT != 0:
        return
    try:
        ov.root.after(0, ov.show)
    except Exception:
        try:
            ov.show()
        except Exception:
            pass
"""
Copy popup window shown on right-click.

Displays subtitle text and provides a simple context menu for mouse-only copy.
"""

import tkinter as tk
from tkinter import font as tkFont
from utils import make_draggable, get_monitor_rects, make_nonactivating_tool_window, show_window_no_activate

class CopyPopup:
    
    def __init__(self, root: tk.Tk, config) -> None:
        self.root = root
        self.config = config

        self._popup: tk.Toplevel | None = None
        self._close_job: str | None = None
        self._pinned = False
        self._menu_open = False
        self._entry_widget: tk.Text | None = None
        self._drag_grip: tk.Label | None = None
        self._on_add_anki = None
        self._dragging = False
        self.root.bind("<Destroy>", lambda e: self._cancel_close())

        self.bg_color = self.config.get("POPUP_BG_COLOR")
        self.font_name = self.config.get("POPUP_FONT")
        self.font_color = self.config.get("POPUP_FONT_COLOR")
        self.font_size = self.config.get("POPUP_FONT_SIZE")
        self.close_delay = int(self.config.get("POPUP_CLOSE_TIMER") or 1000)

    def open_copy_popup(self, subtitle_text = None) -> None:
        if self._popup: #if already popup, close it and make a new one
            self._cancel_close()
            self._popup.destroy()
            self._popup = None
        
        popup = tk.Toplevel(self.root)
        self._popup = popup
        self._menu_open = False
        self._dragging = False
        self._entry_widget = None
        self._drag_grip = None
        popup.withdraw()
        popup.overrideredirect(True)
        # popup.configure(bg=self.bg_color)
        popup.attributes("-topmost", True)
        make_nonactivating_tool_window(popup)

        #calulate size of popup based on text
        font = tkFont.Font(family=self.font_name, size=self.font_size, weight="bold")
        lines = [l for l in (subtitle_text or "").splitlines() if l.strip()]
        pixel_widths = [font.measure(line) for line in lines]
        text_width = max(pixel_widths) if pixel_widths else font.measure((subtitle_text or "").strip() or " ")
        line_height = font.metrics("linespace")
        line_count = max(1, len(lines))
        text_height = line_height * line_count
        pad_x, pad_y = 10,5
        total_width  = text_width  + 2 * pad_x
        total_height = text_height + 2 * pad_y

        entry = tk.Text(popup, font=font, wrap="word",padx=8, pady=4,
                        bg=self.bg_color,fg= self.font_color,
                        cursor="xterm", height=line_count)
        entry.insert("1.0", subtitle_text)
        entry.tag_configure("center", justify="center")
        entry.tag_add("center", "1.0", "end")
        entry.config(state="disabled")
        entry.pack(fill="both", expand=True)
        self._entry_widget = entry

        # Drag grip (no title bar) to move popup without interfering with text selection.
        drag_grip = tk.Label(
            popup,
            text=":::",
            font=(self.font_name, max(8, int(self.font_size * 0.45))),
            fg=self.font_color,
            bg=self.bg_color,
            cursor="fleur",
            bd=0,
            padx=2,
            pady=0,
        )
        drag_grip.place(relx=1.0, rely=1.0, x=-2, y=-2, anchor="se")
        self._drag_grip = drag_grip
        make_draggable(drag_grip, popup, on_release=self._on_popup_drag_end)
        drag_grip.bind("<ButtonPress-1>", self._on_popup_drag_start, add="+")
        drag_grip.bind("<ButtonRelease-1>", self._on_popup_drag_end, add="+")

        # Right-click context menu to copy selected text using only the mouse.
        menu = tk.Menu(popup, tearoff=0)

        def _get_selection() -> str:
            try:
                return (entry.get("sel.first", "sel.last") or "").strip()
            except tk.TclError:
                return ""

        def _copy_selection():
            selected = _get_selection()
            if not selected:
                return
            try:
                popup.clipboard_clear()
                popup.clipboard_append(selected)
            except Exception:
                pass

        def _add_selection_to_anki():
            selected = _get_selection()
            if not selected:
                print("Add Selection To Anki: no text selected.")
                return
            if not callable(self._on_add_anki):
                print("Add Selection To Anki: callback not bound.")
                return
            def _run():
                try:
                    self._on_add_anki(selected_text=selected, subtitle_text=subtitle_text or "")
                except Exception as e:
                    print(f"Add Selection To Anki failed: {e}")
            # Run on next tick so the context menu can close and cursor change is visible.
            try:
                popup.after(1, _run)
            except Exception:
                _run()

        def _copy_all():
            try:
                popup.clipboard_clear()
                popup.clipboard_append(subtitle_text or "")
            except Exception:
                pass

        menu.add_command(label="Copy", command=_copy_selection)
        menu.add_command(label="Copy All", command=_copy_all)
        menu.add_command(label="Add Selection To Anki", command=_add_selection_to_anki)
        menu.add_separator()
        menu.add_command(label="Pin", command=lambda: self._pin(popup))

        def _show_menu(event):
            self._menu_open = True
            self._cancel_close()
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass
                self._menu_open = False
                if not self._pinned:
                    self._restart_close()
            return "break"

        entry.bind("<Button-3>", _show_menu)

        try:
            pointer_x = int(self.root.winfo_pointerx())
            pointer_y = int(self.root.winfo_pointery())
        except Exception:
            pointer_x, pointer_y = 0, 0

        monitor_rect = None
        try:
            rects = list(get_monitor_rects(self.root) or [])
            for rx, ry, rw, rh in rects:
                if rx <= pointer_x < rx + rw and ry <= pointer_y < ry + rh:
                    monitor_rect = (rx, ry, rw, rh)
                    break
            if monitor_rect is None and rects:
                monitor_rect = min(
                    rects,
                    key=lambda r: (abs(pointer_x - (r[0] + (r[2] // 2))) + abs(pointer_y - (r[1] + (r[3] // 2)))),
                )
        except Exception:
            monitor_rect = None

        if monitor_rect is not None:
            screen_x, screen_y, screen_w, screen_h = monitor_rect
        else:
            try:
                screen_x, screen_y = 0, 0
                screen_w = int(popup.winfo_screenwidth() or 1920)
                screen_h = int(popup.winfo_screenheight() or 1080)
            except Exception:
                screen_x, screen_y, screen_w, screen_h = 0, 0, 1920, 1080

        max_w = max(240, int(screen_w * 0.90))
        max_h = max(120, int(screen_h * 0.60))
        total_width = min(max_w, int(total_width))
        total_height = min(max_h, int(total_height))

        x = int(pointer_x - (total_width // 2))
        y = int(pointer_y - total_height - 16)
        max_x = screen_x + max(0, screen_w - total_width)
        max_y = screen_y + max(0, screen_h - total_height)
        x = max(screen_x, min(x, max_x))
        y = max(screen_y, min(y, max_y))
        popup.geometry(f"{total_width}x{total_height}+{x}+{y}")
        show_window_no_activate(popup)
        
        self._pinned  = False
        popup.bind("<Enter>", lambda e: self._cancel_close())
        popup.bind("<Leave>", lambda e: self._on_popup_leave())
        popup.bind("<Destroy>", lambda e: self._on_popup_destroy())
        self._restart_close()

    def bind_add_to_anki(self, callback) -> None:
        self._on_add_anki = callback

    def ensure_on_top(self) -> None:
        """
        Keep popup above the other always-on-top windows in this app.

        Note: other windows (overlay/control) also set -topmost, so z-order depends on who is lifted last.
        """
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        try:
            show_window_no_activate(popup)
        except Exception:
            pass

    def set_busy_cursor(self, cursor: str) -> None:
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        if cursor:
            self._cancel_close()
        try:
            popup.configure(cursor=cursor)
        except Exception:
            pass
        entry = getattr(self, "_entry_widget", None)
        if entry is not None:
            try:
                entry.configure(cursor=cursor or "xterm")
            except Exception:
                pass

    def mark_anki_success(self, duration_ms: int = 1200) -> None:
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        try:
            if not popup.winfo_exists():
                return
        except Exception:
            return

        success_bg = "#168a3a"
        self._cancel_close()
        try:
            popup.configure(bg=success_bg, cursor="")
        except Exception:
            pass
        entry = getattr(self, "_entry_widget", None)
        if entry is not None:
            try:
                entry.configure(bg=success_bg, cursor="xterm")
            except Exception:
                pass
        grip = getattr(self, "_drag_grip", None)
        if grip is not None:
            try:
                grip.configure(bg=success_bg)
            except Exception:
                pass
        try:
            self._close_job = popup.after(max(300, int(duration_ms)), self._close)
        except Exception:
            self._restart_close()

    def _close(self) -> None:
        if self._popup: self._popup.destroy()
        self._popup = None
        self._entry_widget = None
        self._drag_grip = None
        self._close_job = None

    def _cancel_close(self) -> None:
        if self._popup and self._close_job:
            self._popup.after_cancel(self._close_job)
        self._close_job = None

    def _restart_close(self) -> None: #restart close timer
        self._cancel_close()
        if self._popup:
            self._close_job = self._popup.after(self.close_delay, self._close)

    def _on_popup_leave(self) -> None:
        if self._pinned or self._menu_open or self._dragging:
            return
        self._restart_close()

    def _on_popup_drag_start(self, event=None) -> None:
        self._dragging = True
        self._cancel_close()

    def _on_popup_drag_end(self, *args, **kwargs) -> None:
        self._dragging = False
        if not self._pinned and not self._menu_open:
            self._restart_close()

    def _pin(self, popup: tk.Toplevel) -> None:
        self._cancel_close()
        self._pinned = True
        popup.overrideredirect(False)
        popup.lift()

    def _on_popup_destroy(self) -> None:
        self._popup = None
        self._entry_widget = None
        self._drag_grip = None
        self._dragging = False
"""Advanced settings window helpers for SettingsUI."""

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from utils import (
    get_monitor_rects,
    make_nonactivating_window,
    show_window_no_activate_minimizable,
)


class _SettingsUIProxy:
    def __init__(self, settings_ui):
        object.__setattr__(self, "settings_ui", settings_ui)

    def __getattr__(self, name):
        return getattr(self.settings_ui, name)

    def __setattr__(self, name, value):
        if name == "settings_ui":
            object.__setattr__(self, name, value)
        else:
            setattr(self.settings_ui, name, value)  


class SettingsAdvancedUI(_SettingsUIProxy):
    """Advanced settings window logic extracted from SettingsUI."""

    def __init__(self, settings_ui: Any) -> None:
        super().__init__(settings_ui)

    def _sync_advanced_startup_vars_from_runtime(self) -> None:
        vars_map = getattr(self, "_advanced_vars", None)
        if not isinstance(vars_map, dict):
            return
        offset_var = vars_map.get("EXTRA_OFFSET")
        if offset_var is not None:
            try:
                offset_var.set(self._format_number(float(self._last_offset_value)))
            except Exception:
                pass
        skip_var = vars_map.get("DEFAULT_SKIP")
        if skip_var is not None:
            try:
                skip_var.set(self._format_number(float(self._last_skip_value)))
            except Exception:
                pass
    def _flush_pending_entry_changes(self):
        """Force any pending changes in offset/skip entry fields to be saved to config."""
        for entry, attr_name, apply_method in [
            (self.offset_entry, "_last_offset_value", self._apply_offset_change),
            (self.skip_entry, "_last_skip_value", self._apply_skip_change),
        ]:
            try:
                text = entry.get().replace(",", ".").strip()
                parsed = self._parse_number(text)
                if parsed is not None and hasattr(self, attr_name):
                    current_value = getattr(self, attr_name)
                    if abs(parsed - current_value) > 0.001:  # Value has changed
                        setattr(self, attr_name, parsed)
                        if entry is self.offset_entry:
                            apply_method(parsed, persist=True, previous_value=current_value)
                        elif entry is self.skip_entry:
                            apply_method(parsed, persist=True)
            except Exception:
                pass
        self._sync_advanced_startup_vars_from_runtime()

    def _open_advanced_settings_window(self):
        # Flush any pending changes in the main UI before opening the advanced window
        self._flush_pending_entry_changes()
        self._keep_main_settings_clickable_with_advanced()
        
        if self.advanced_window is not None and self.advanced_window.winfo_exists():
            show_window_no_activate_minimizable(self.advanced_window)
            self._load_advanced_values_into_vars()
            self._prepare_advanced_tab_sizes()
            self.root.after(0, self._fit_advanced_window_to_selected_tab)
            self.root.after(80, self._fit_advanced_window_to_selected_tab)
            self.root.after(0, self._reset_advanced_tab_focus)
            return

        win = tk.Toplevel(self.root)
        win.withdraw()
        self.advanced_window = win
        win.title("Advanced Settings")
        win.attributes("-topmost", True)
        make_nonactivating_window(win)
        win.resizable(True, True)
        try:
            win.grab_release()
        except Exception:
            pass
        self._restore_advanced_window_geometry(win)

        body = tk.Frame(win, padx=12, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text="Tune runtime behavior, subtitle style, Anki integration, and shortcuts.",
            font=("Arial", 11, "bold"),
            anchor="w",
            justify="left",
        ).pack(fill="x", pady=(0, 8))

        self._advanced_vars = {}
        self._advanced_meta = {}
        self._advanced_status_var = tk.StringVar(value="")
        self._advanced_tab_key_map = {}
        self._ocr_region_count_trace_var = None
        self._ocr_region_count_refresh_job = None

        notebook = ttk.Notebook(body)
        notebook.pack(fill="both", expand=True, anchor="n", pady=(0, 8))
        self._advanced_notebook = notebook

        general_tab = tk.Frame(notebook)
        anki_tab = tk.Frame(notebook)
        shortcuts_tab = tk.Frame(notebook)
        ocr_tab = tk.Frame(notebook)
        notebook.add(general_tab, text="General")
        notebook.add(anki_tab, text="Anki")
        notebook.add(shortcuts_tab, text="Shortcuts")
        notebook.add(ocr_tab, text="OCR")
        notebook.bind("<<NotebookTabChanged>>", self._on_advanced_tab_changed, add="+")

        self._build_advanced_tab(general_tab, self._advanced_general_columns())
        self._build_advanced_tab(anki_tab, self._advanced_anki_columns())
        self._build_advanced_tab(shortcuts_tab, self._advanced_shortcut_columns())
        self._build_advanced_tab(ocr_tab, self._advanced_ocr_columns())

        self._build_general_actions(general_tab)
        self._build_ocr_actions(ocr_tab)

        self._load_advanced_values_into_vars()

        status_row = tk.Frame(body)
        status_row.pack(fill="x", pady=(0, 6))
        tk.Label(
            status_row,
            textvariable=self._advanced_status_var,
            fg="#1a4d1a",
            anchor="w",
            justify="left",
        ).pack(fill="x")

        btn_row = tk.Frame(body)
        btn_row.pack(fill="x", pady=(4, 0))
        tk.Button(
            btn_row,
            text="Apply Now",
            width=12,
            command=lambda: self._apply_advanced_settings(persist=True),
        ).pack(side="left")
        tk.Button(
            btn_row,
            text="Reload from Config",
            width=16,
            command=self._load_advanced_values_into_vars,
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            btn_row,
            text="Reset to Defaults",
            width=14,
            command=self._reset_selected_advanced_tab_to_defaults,
        ).pack(side="left", padx=(6, 0))
        tk.Button(btn_row, text="Close", width=10, command=win.destroy).pack(side="right")

        win.bind("<Return>", self._on_advanced_apply_now_key, add="+")
        win.bind("<KP_Enter>", self._on_advanced_apply_now_key, add="+")

        self._prepare_advanced_tab_sizes()
        win.after(0, self._fit_advanced_window_to_selected_tab)
        win.after(80, self._fit_advanced_window_to_selected_tab)
        win.after(0, self._reset_advanced_tab_focus)
        show_window_no_activate_minimizable(win)

        def _on_destroy(_event):
            if _event.widget is not win:
                return
            if self._advanced_resize_job is not None:
                try:
                    win.after_cancel(self._advanced_resize_job)
                except Exception:
                    pass
                self._advanced_resize_job = None
            if self._ocr_region_count_refresh_job is not None:
                try:
                    win.after_cancel(self._ocr_region_count_refresh_job)
                except Exception:
                    pass
                self._ocr_region_count_refresh_job = None
            self._save_advanced_window_geometry(win)
            self.advanced_window = None
            self._advanced_notebook = None
            self._advanced_tab_sizes = {}
            self._advanced_tab_key_map = {}
            self._phone_mode_toggle_btn = None
            self._ocr_region_count_trace_var = None
            self._restore_main_settings_topmost_after_advanced()

        win.bind("<Destroy>", _on_destroy)

    def _keep_main_settings_clickable_with_advanced(self) -> None:
        if self._root_topmost_before_advanced is None:
            try:
                self._root_topmost_before_advanced = bool(self.root.attributes("-topmost"))
            except Exception:
                self._root_topmost_before_advanced = False
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass

    def _restore_main_settings_topmost_after_advanced(self) -> None:
        previous = self._root_topmost_before_advanced
        self._root_topmost_before_advanced = None
        if previous is None:
            return
        try:
            self.root.attributes("-topmost", bool(previous))
        except Exception:
            pass

    def _restore_advanced_window_geometry(self, win):
        try:
            self.root.update_idletasks()
            sw = int(self.root.winfo_vrootwidth() or self.root.winfo_screenwidth())
            sh = int(self.root.winfo_vrootheight() or self.root.winfo_screenheight())
        except Exception:
            sw, sh = 1920, 1080

        saved_w = self.config.get("LAST_ADV_SETTINGS_WINDOW_WIDTH")
        saved_h = self.config.get("LAST_ADV_SETTINGS_WINDOW_HEIGHT")
        saved_x = self.config.get("LAST_ADV_SETTINGS_WINDOW_X")
        saved_y = self.config.get("LAST_ADV_SETTINGS_WINDOW_Y")

        default_w, default_h = 760, 540
        w = int(saved_w) if isinstance(saved_w, int) and saved_w > 0 else default_w
        h = int(saved_h) if isinstance(saved_h, int) and saved_h > 0 else default_h
        w = max(420, min(w, sw))
        h = max(340, min(h, sh))

        if isinstance(saved_x, int) and isinstance(saved_y, int):
            x = max(0, min(saved_x, sw - w))
            y = max(0, min(saved_y, sh - h))
        else:
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
        win.geometry(f"{w}x{h}+{x}+{y}")

    def _save_advanced_window_geometry(self, win):
        try:
            geo = win.winfo_geometry()
            size, pos = geo.split("+", 1)
            w_s, h_s = size.split("x", 1)
            x_s, y_s = pos.split("+", 1)
            x, y = int(x_s), int(y_s)
            w, h = int(w_s), int(h_s)
        except Exception:
            try:
                x = int(win.winfo_x())
                y = int(win.winfo_y())
                w = int(win.winfo_width())
                h = int(win.winfo_height())
            except Exception:
                return

        if (x, y) != (
            self.config.get("LAST_ADV_SETTINGS_WINDOW_X"),
            self.config.get("LAST_ADV_SETTINGS_WINDOW_Y"),
        ):
            self.config.set("LAST_ADV_SETTINGS_WINDOW_X", x)
            self.config.set("LAST_ADV_SETTINGS_WINDOW_Y", y)
        if (w, h) != (
            self.config.get("LAST_ADV_SETTINGS_WINDOW_WIDTH"),
            self.config.get("LAST_ADV_SETTINGS_WINDOW_HEIGHT"),
        ):
            self.config.set("LAST_ADV_SETTINGS_WINDOW_WIDTH", w)
            self.config.set("LAST_ADV_SETTINGS_WINDOW_HEIGHT", h)

    def _on_advanced_tab_changed(self, _event=None):
        win = self.advanced_window
        if win is None:
            return
        try:
            notebook = getattr(self, "_advanced_notebook", None)
            if notebook is not None and notebook.winfo_exists():
                tab_id = notebook.select()
                if tab_id:
                    self._prepare_advanced_tab_size(tab_id)
        except Exception:
            pass
        if self._advanced_resize_job is not None:
            try:
                win.after_cancel(self._advanced_resize_job)
            except Exception:
                pass
        self._advanced_resize_job = win.after(1, self._fit_advanced_window_to_selected_tab)
        win.after(0, self._reset_advanced_tab_focus)

    def _on_advanced_apply_now_key(self, _event=None):
        self._apply_advanced_settings(persist=True)
        return "break"

    def _clear_advanced_entry_selection(self, parent):
        try:
            children = parent.winfo_children()
        except Exception:
            return
        for child in children:
            try:
                if isinstance(child, (tk.Entry, ttk.Entry, ttk.Combobox)):
                    child.selection_clear()
                self._clear_advanced_entry_selection(child)
            except Exception:
                pass

    def _reset_advanced_tab_focus(self):
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        try:
            if not notebook.winfo_exists():
                return
            tab_id = notebook.select()
            if tab_id:
                tab_widget = notebook.nametowidget(tab_id)
                self._clear_advanced_entry_selection(tab_widget)
            notebook.focus_set()
        except Exception:
            pass

    def _fit_advanced_window_to_selected_tab(self):
        win = self.advanced_window
        notebook = getattr(self, "_advanced_notebook", None)
        if win is None or notebook is None:
            return
        self._advanced_resize_job = None
        try:
            if not (win.winfo_exists() and notebook.winfo_exists()):
                return
            tab_id = notebook.select()
            if not tab_id:
                return
            sizes = self._advanced_tab_sizes.get(tab_id)
            if sizes is None:
                self._prepare_advanced_tab_sizes()
                sizes = self._advanced_tab_sizes.get(tab_id)
                if sizes is None:
                    return
            nb_w, nb_h, req_w, req_h = sizes
            notebook.configure(width=int(nb_w), height=int(nb_h))
            try:
                sw = int(self.root.winfo_vrootwidth() or self.root.winfo_screenwidth())
                sh = int(self.root.winfo_vrootheight() or self.root.winfo_screenheight())
            except Exception:
                sw, sh = 1920, 1080
            req_w = max(360, min(int(req_w), max(360, sw - 20)))
            req_h = max(220, min(int(req_h), max(220, sh - 40)))
            x = max(0, min(int(win.winfo_x()), max(0, sw - req_w)))
            y = max(0, min(int(win.winfo_y()), max(0, sh - req_h)))
            if int(win.winfo_width()) != int(req_w) or int(win.winfo_height()) != int(req_h):
                win.geometry(f"{req_w}x{req_h}+{x}+{y}")
        except Exception:
            pass

    def _prepare_advanced_tab_sizes(self):
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        try:
            tab_id = notebook.select()
        except Exception:
            return
        if tab_id:
            self._prepare_advanced_tab_size(tab_id)

    def _prepare_advanced_tab_size(self, tab_id: str):
        win = self.advanced_window
        notebook = getattr(self, "_advanced_notebook", None)
        if win is None or notebook is None:
            return
        try:
            if not tab_id:
                return
            win.update_idletasks()
            tab = notebook.nametowidget(tab_id)
            nb_w = max(280, int(tab.winfo_reqwidth()) + 14)
            nb_h = max(80, int(tab.winfo_reqheight()) + 8)
            notebook.configure(width=nb_w, height=nb_h)
            win.update_idletasks()
            w = max(360, int(win.winfo_reqwidth()))
            h = max(180, int(win.winfo_reqheight()))
            self._advanced_tab_sizes[tab_id] = (nb_w, nb_h, w, h)
        except Exception:
            pass

    def _build_advanced_tab(self, tab_parent, column_sections):
        tab_id = str(tab_parent)
        if not hasattr(self, "_advanced_tab_key_map"):
            self._advanced_tab_key_map = {}
        if tab_id not in self._advanced_tab_key_map:
            self._advanced_tab_key_map[tab_id] = []

        content = tk.Frame(tab_parent, padx=8, pady=8)
        content.pack(fill="both", expand=True, anchor="n")
        for col_idx in range(len(column_sections)):
            content.grid_columnconfigure(col_idx, weight=1)

        for col_idx, sections in enumerate(column_sections):
            col = tk.Frame(content)
            padx = (0, 6) if col_idx == 0 else (6, 0)
            col.grid(row=0, column=col_idx, sticky="nsew", padx=padx)
            for section_idx, (section_name, specs) in enumerate(sections):
                self._build_advanced_section(
                    col,
                    section_name,
                    specs,
                    tab_id=tab_id,
                    is_last=(section_idx == len(sections) - 1),
                )

    def _build_advanced_section(self, parent, section_name, specs, tab_id: str, is_last: bool = False):
        visible_specs = [spec for spec in specs if not spec.get("hidden")]

        def _register_var(spec):
            key = spec["key"]
            if key in self._advanced_vars:
                keys = self._advanced_tab_key_map.setdefault(tab_id, [])
                if key not in keys:
                    keys.append(key)
                return
            self._advanced_meta[key] = spec
            if spec["type"] == "bool":
                self._advanced_vars[key] = tk.BooleanVar(value=False)
            else:
                self._advanced_vars[key] = tk.StringVar(value="")
            if key == "OCR_REGION_COUNT":
                self._install_ocr_region_count_trace(self._advanced_vars[key])
            keys = self._advanced_tab_key_map.setdefault(tab_id, [])
            if key not in keys:
                keys.append(key)

        # If everything is hidden, just register vars without rendering a UI section.
        if not visible_specs:
            for spec in specs:
                _register_var(spec)
            return

        section = tk.LabelFrame(parent, text=section_name, padx=10, pady=8)
        section.pack(fill="x", pady=(0, 0 if is_last else 10))
        section.grid_columnconfigure(1, weight=1)

        row = 0
        for spec in specs:
            _register_var(spec)
            if spec.get("hidden"):
                continue
            key = spec["key"]
            var = self._advanced_vars.get(key)
            if spec["type"] == "bool":
                chk_frame = tk.Frame(section)
                chk_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
                chk_frame.grid_columnconfigure(0, weight=0)
                chk_frame.grid_columnconfigure(1, weight=1)
                
                chk = tk.Checkbutton(chk_frame, text=spec["label"], variable=var, anchor="w")
                chk.grid(row=0, column=0, sticky="w")
                
                # Add button if spec has button_text
                if spec.get("button_text"):
                    btn_text = spec["button_text"]
                    btn_callback = spec.get("button_callback")
                    btn = tk.Button(
                        chk_frame,
                        text=btn_text,
                        width=13,
                        command=btn_callback if btn_callback else self._handle_anki_check
                    )
                    btn.grid(row=0, column=1, sticky="w", padx=(8, 0))
                    if key == "ANKI_ENABLED":
                        self._anki_check_btn = btn
                        self._remember_anki_check_defaults(self._anki_check_btn)
                        self._set_anki_check_button_state(None)
            else:
                tk.Label(section, text=spec["label"]).grid(row=row, column=0, sticky="w", pady=2)
                # Create a frame for entry and optional button
                entry_frame = tk.Frame(section)
                entry_frame.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
                entry_frame.grid_columnconfigure(0, weight=1)
                
                entry = tk.Entry(entry_frame, textvariable=var, width=20)
                entry.grid(row=0, column=0, sticky="ew")
                
                # Add button if spec has button_text
                if spec.get("button_text"):
                    btn_text = spec["button_text"]
                    btn_callback = spec.get("button_callback")
                    btn = tk.Button(
                        entry_frame,
                        text=btn_text,
                        width=6,
                        command=btn_callback if btn_callback else self._toggle_phone_mode
                    )
                    btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))
                    if key == "PHONEMODE_WINDOWS_HIDE_DELAY_MS":
                        self._phone_mode_toggle_btn = btn
                        self._refresh_phone_toggle_button()
            row += 1

    def _install_ocr_region_count_trace(self, var) -> None:
        if var is None or self._ocr_region_count_trace_var is var:
            return
        self._ocr_region_count_trace_var = var
        try:
            var.trace_add("write", self._on_ocr_region_count_changed)
        except Exception:
            pass

    def _on_ocr_region_count_changed(self, *_args) -> None:
        root = getattr(self, "root", None)
        if root is None:
            return
        if self._ocr_region_count_refresh_job is not None:
            try:
                root.after_cancel(self._ocr_region_count_refresh_job)
            except Exception:
                pass
        try:
            self._ocr_region_count_refresh_job = root.after(80, self._refresh_ocr_count_runtime)
        except Exception:
            self._refresh_ocr_count_runtime()

    def _refresh_ocr_count_runtime(self) -> None:
        self._ocr_region_count_refresh_job = None
        try:
            self._refresh_ocr_area_buttons()
            self._apply_ocr_values_runtime()
        except Exception:
            pass

    def _advanced_general_columns(self):
        left = [
            (
                "Playback / Overlay",
                [
                    {"key": "UPDATE_INTERVAL_MS", "label": "Update interval (ms)", "type": "int", "default": 100, "min": 15, "max": 5000},
                    {"key": "SUBTITLE_TIMEOUT_MS", "label": "Subtitle timeout (ms)", "type": "int", "default": 7000, "min": 100, "max": 120000},
                    {"key": "WINDOWS_HIDE_DELAY_MS", "label": "Control hide delay desktop (ms)", "type": "int", "default": 7000, "min": 100, "max": 120000},
                    {"key": "PHONEMODE_WINDOWS_HIDE_DELAY_MS", "label": "Control hide delay phone (ms)", "type": "int", "default": 6000, "min": 100, "max": 120000, "button_text": "Phone"},
                    {"key": "VIDEO_CLICK", "label": "Auto-click video after control actions", "type": "bool", "default": False},
                ],
            ),
            (
                "Download / Search",
                [
                    {"key": "DOWNLOAD_WINDOW", "label": "Prefetch window (episodes)", "type": "int", "default": 5, "min": 1, "max": 50},
                    {"key": "DOWNLOAD_MAX_WORKERS", "label": "Max parallel downloads", "type": "int", "default": 2, "min": 1, "max": 10},
                    {"key": "DOWNLOAD_PREFETCH_DELAY_MS", "label": "Prefetch delay (ms)", "type": "int", "default": 1000, "min": 0, "max": 600000},
                    {"key": "DOWNLOAD_THROTTLE_MS", "label": "Download throttle (ms)", "type": "int", "default": 0, "min": 0, "max": 60000},
                    {"key": "SEASON_PROVIDER_EARLY_STOP_ENABLED", "label": "Provider early-stop enabled", "type": "bool", "default": False},
                    {"key": "SEASON_PROVIDER_EARLY_STOP_MIN_FOUND_SEASONS", "label": "Early-stop min found seasons", "type": "int", "default": 1, "min": 1, "max": 20},
                ],
            ),
            (
                "Subtitle Cleaning",
                [
                    {"key": "SUBTITLE_CUSTOM_HTML_TAGS", "label": "Custom HTML tags to keep", "type": "str", "default": ""},
                    {"key": "SUBTITLE_SPEAKER_MODE", "label": "Speaker mode: hide, anime, template", "type": "str", "default": "hide"},
                    {"key": "SUBTITLE_KEEP_SPEAKER_NAMES", "label": "Legacy: keep leading speaker labels", "type": "bool", "default": False},
                    {"key": "SUBTITLE_SPEAKER_TEMPLATE", "label": "Custom speaker template ({name})", "type": "str", "default": "{name}: "},
                    {"key": "SUBTITLE_STRIP_PAREN_NOTES", "label": "Remove remaining non-speaker (...) notes", "type": "bool", "default": False},
                    {"key": "SUBTITLE_AUTO_RUBY", "label": "Auto-add ruby for kanji-only lines", "type": "bool", "default": False},
                    {"key": "SUBTITLE_HOVER_RUBY", "label": "Show ruby only on kanji hover", "type": "bool", "default": False},
                ],
            ),
        ]
        right = [
            (
                "Subtitle / Popup Style",
                [
                    {"key": "SUBTITLE_FONT", "label": "Subtitle font", "type": "str", "default": "meiryo.ttc"},
                    {"key": "SUBTITLE_FONT_SIZE", "label": "Subtitle font size", "type": "int", "default": 50, "min": 10, "max": 140},
                    {"key": "SUBTITLE_COLOR", "label": "Subtitle color", "type": "str", "default": "white"},
                    {"key": "SUBTITLE_WRAP_LIMIT_PX", "label": "Subtitle wrap limit px", "type": "int", "default": 1500, "min": 0, "max": 5000},
                    {"key": "GLOW_COLOR", "label": "Glow color", "type": "str", "default": "black"},
                    {"key": "GLOW_RADIUS", "label": "Glow radius", "type": "int", "default": 5, "min": 0, "max": 20},
                    {"key": "POPUP_FONT", "label": "Popup font", "type": "str", "default": "Arial"},
                    {"key": "POPUP_FONT_SIZE", "label": "Popup font size", "type": "int", "default": 20, "min": 8, "max": 96},
                    {"key": "POPUP_FONT_COLOR", "label": "Popup font color", "type": "str", "default": "white"},
                    {"key": "POPUP_BG_COLOR", "label": "Popup background color", "type": "str", "default": "black"},
                    {"key": "POPUP_CLOSE_TIMER", "label": "Popup close delay (ms)", "type": "int", "default": 1000, "min": 100, "max": 60000},
                ],
            ),
            (
                "Startup Defaults",
                [
                    {"key": "DEFAULT_START_TIME", "label": "Start time (s)", "type": "float", "default": 120.0, "min": 0.0, "max": 604800.0},
                    {"key": "EXTRA_OFFSET", "label": "Default offset (s)", "type": "float", "default": 0.0, "min": -600.0, "max": 600.0},
                    {"key": "DEFAULT_SKIP", "label": "Default skip (s)", "type": "float", "default": 1.0, "min": 0.01, "max": 600.0},
                ],
            ),
        ]
        return [left, right]

    def _advanced_anki_columns(self):
        left = [
            (
                "Anki Connection",
                [
                    {"key": "ANKI_ENABLED", "label": "Enable Anki integration", "type": "bool", "default": True, "button_text": "Check Connection"},
                    {"key": "ANKI_CONNECT_URL", "label": "AnkiConnect URL", "type": "str", "default": "http://127.0.0.1:8765"},
                    {"key": "ANKI_HTTP_TIMEOUT_SEC", "label": "HTTP timeout (sec)", "type": "float", "default": 4.0, "min": 0.5, "max": 120.0},
                ],
            ),
            (
                "Deck / Model",
                [
                    {"key": "ANKI_DECK", "label": "Main deck", "type": "str", "default": "Japanese"},
                    {"key": "ANKI_READING_DECK", "label": "Reading deck", "type": "str", "default": "Japanese::Reading"},
                    {"key": "ANKI_REVERSE_DECK", "label": "Reverse deck", "type": "str", "default": "Japanese::DE -> JA"},
                    {"key": "ANKI_MODEL", "label": "Note type", "type": "str", "default": "Standard (und umgekehrte Karte) Japanese"},
                    {"key": "ANKI_TAGS", "label": "Tags (comma-separated)", "type": "str", "default": "subtitleplayer", "allow_empty": True},
                ],
            ),
        ]
        right = [
            (
                "Audio Clip Timing",
                [
                    {"key": "AUDIO_PADDING", "label": "Subtitle-end audio padding (s)", "type": "float", "default": 0.1, "min": -10.0, "max": 10.0},
                ],
            ),
            (
                "Language",
                [
                    {"key": "ANKI_WORD_TARGET_LANG", "label": "Word target language", "type": "str", "default": "de"},
                    {"key": "ANKI_SENTENCE_TARGET_LANG", "label": "Sentence target language", "type": "str", "default": "de"},
                ],
            ),
            (
                "Anki Fields",
                [
                    {"key": "ANKI_FIELD_ADD_RUBIES_FRONT", "label": "Add rubies front field", "type": "str", "default": "AddRubiesToFront"},
                    {"key": "ANKI_FIELD_FRONT", "label": "Front field", "type": "str", "default": "Front"},
                    {"key": "ANKI_FIELD_BACK", "label": "Back field", "type": "str", "default": "Back"},
                    {"key": "ANKI_FIELD_SENTENCE_JA", "label": "Sentence JA field", "type": "str", "default": "SentenceJA"},
                    {"key": "ANKI_FIELD_SENTENCE_DE", "label": "Sentence translation field", "type": "str", "default": "SentenceDE"},
                    {"key": "ANKI_FIELD_SOUND", "label": "Sound field", "type": "str", "default": "Sound"},
                    {"key": "ANKI_FIELD_IMAGE", "label": "Image field", "type": "str", "default": "Image"},
                    {"key": "ANKI_FIELD_ADD_RUBIES_SENTENCE_JA", "label": "Add rubies sentence field", "type": "str", "default": "AddRubiesToSentenceJA"},
                    {"key": "ANKI_FIELD_DEFINITION", "label": "Definition field", "type": "str", "default": "Definition"},
                ],
            ),
        ]
        return [left, right]

    def _build_general_actions(self, general_tab: tk.Frame) -> None:
        # Actions section is now integrated with PHONEMODE_WINDOWS_HIDE_DELAY_MS as a button
        # This method now only exists for compatibility but doesn't create the Actions button
        pass

    def _remember_anki_check_defaults(self, btn: tk.Button) -> None:
        if btn is None:
            return
        if hasattr(self, "_anki_check_defaults"):
            return
        try:
            self._anki_check_defaults = {
                "bg": btn.cget("bg"),
                "fg": btn.cget("fg"),
                "activebackground": btn.cget("activebackground"),
                "activeforeground": btn.cget("activeforeground"),
            }
        except Exception:
            self._anki_check_defaults = None

    def _set_anki_check_button_state(self, connected):
        btn = getattr(self, "_anki_check_btn", None)
        if btn is None:
            return
        if connected is True:
            try:
                btn.configure(bg="#2f8f4e", fg="white", activebackground="#2f8f4e", activeforeground="white")
            except Exception:
                pass
            return
        if connected is False:
            try:
                btn.configure(bg="#b33939", fg="white", activebackground="#b33939", activeforeground="white")
            except Exception:
                pass
            return
        defaults = getattr(self, "_anki_check_defaults", None)
        if not defaults:
            return
        try:
            btn.configure(
                bg=defaults.get("bg"),
                fg=defaults.get("fg"),
                activebackground=defaults.get("activebackground"),
                activeforeground=defaults.get("activeforeground"),
            )
        except Exception:
            pass

    def _handle_anki_check(self) -> None:
        btn = getattr(self, "_anki_check_btn", None)
        if btn is not None:
            try:
                btn.configure(state=tk.DISABLED, text="Checking...")
            except Exception:
                pass

        def worker():
            connected = False
            try:
                connected = bool(self._on_anki_check())
            except Exception:
                connected = False

            def _finish():
                target = getattr(self, "_anki_check_btn", None)
                if target is None or not target.winfo_exists():
                    return
                try:
                    target.configure(state=tk.NORMAL, text="Check Connection")
                except Exception:
                    pass
                self._set_anki_check_button_state(connected)
                if hasattr(self, "_advanced_status_var"):
                    self._advanced_status_var.set(
                        "AnkiConnect reachable." if connected else "AnkiConnect not reachable."
                    )

            try:
                self.root.after(0, _finish)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _advanced_shortcut_columns(self):
        left = [
            (
                "Mode 1 (Arrows)",
                [
                    {"key": "SHORTCUT_TOGGLE_PLAY", "label": "Play/Pause", "type": "str", "default": "space"},
                    {"key": "SHORTCUT_GO_BACK", "label": "Back (seconds)", "type": "str", "default": "left"},
                    {"key": "SHORTCUT_GO_FORWARD", "label": "Forward (seconds)", "type": "str", "default": "right"},
                    {"key": "SHORTCUT_SUBTITLE_BACK", "label": "Back (subtitle segment)", "type": "str", "default": "shift+left"},
                    {"key": "SHORTCUT_SUBTITLE_FORWARD", "label": "Forward (subtitle segment)", "type": "str", "default": "shift+right"},
                ],
            ),
            (
                "Mode 2 (Numpad)",
                [
                    {"key": "SHORTCUT_MODE2_TOGGLE_PLAY", "label": "Play/Pause", "type": "str", "default": "numpad0"},
                    {"key": "SHORTCUT_MODE2_GO_BACK", "label": "Back (seconds)", "type": "str", "default": "4"},
                    {"key": "SHORTCUT_MODE2_GO_FORWARD", "label": "Forward (seconds)", "type": "str", "default": "6"},
                    {"key": "SHORTCUT_MODE2_SUBTITLE_BACK", "label": "Back (subtitle segment)", "type": "str", "default": "alt+4"},
                    {"key": "SHORTCUT_MODE2_SUBTITLE_FORWARD", "label": "Forward (subtitle segment)", "type": "str", "default": "alt+6"},
                ],
            ),
            (
                "Other Global",
                [
                    {"key": "SHORTCUT_BRING_TO_FRONT", "label": "Bring app to front", "type": "str", "default": "alt+x"},
                    {"key": "SHORTCUT_EPISODE_INC", "label": "Episode +", "type": "str", "default": "alt+c"},
                    {"key": "SHORTCUT_EPISODE_DEC", "label": "Episode -", "type": "str", "default": "alt+y"},
                ],
            ),
        ]
        right = [
            (
                "Skip Behavior",
                [
                    {"key": "SKIP_BUTTONS_USE_SUBTITLE_SEGMENTS", "label": "Back/forward use subtitle segments", "type": "bool", "default": False},
                ],
            ),
            (
                "Hotkeys",
                [
                    {"key": "SHORTCUTS_DISABLED", "label": "Disable all hotkeys", "type": "bool", "default": False},
                    {"key": "DISABLE_SPACE_HOTKEY", "label": "Disable space play/pause", "type": "bool", "default": False},
                    {"key": "DISABLE_HOTKEY_TOGGLE_PLAY", "label": "Disable play/pause hotkey", "type": "bool", "default": False},
                    {"key": "DISABLE_HOTKEY_GO_BACK", "label": "Disable back hotkey", "type": "bool", "default": False},
                    {"key": "DISABLE_HOTKEY_GO_FORWARD", "label": "Disable forward hotkey", "type": "bool", "default": False},
                    {"key": "DISABLE_HOTKEY_SUBTITLE_BACK", "label": "Disable subtitle-back hotkey", "type": "bool", "default": False},
                    {"key": "DISABLE_HOTKEY_SUBTITLE_FORWARD", "label": "Disable subtitle-forward hotkey", "type": "bool", "default": False},
                    {"key": "DISABLE_HOTKEY_JUMP_SUB_END", "label": "Disable jump-sub-end hotkey", "type": "bool", "default": False},
                ],
            ),
        ]
        return [left, right]

    def _advanced_ocr_columns(self):
        left = [
            (
                "OCR Settings",
                [
                    {"key": "OCR_ENABLED", "label": "Enable startup/episode OCR sync", "type": "bool", "default": True},
                    {"key": "OCR_SYNC_AFTER_ANKI", "label": "OCR sync after Anki add", "type": "bool", "default": False},
                    {"key": "OCR_TESSERACT_CMD", "label": "Tesseract path (exe or folder)", "type": "str", "default": "", "allow_empty": True},
                    {"key": "OCR_TESSERACT_PSM", "label": "Tesseract PSM", "type": "int", "default": 6, "min": 0, "max": 13},
                    {"key": "OCR_TESSERACT_OEM", "label": "Tesseract OEM", "type": "int", "default": 3, "min": 0, "max": 3},
                    {"key": "OCR_CHAR_WHITELIST", "label": "Char whitelist", "type": "str", "default": "0123456789:/", "allow_empty": True},
                    {"key": "OCR_REGION_COUNT", "label": "OCR box count", "type": "int", "default": 2, "min": 1, "max": self.OCR_MAX_REGIONS},
                    {"key": "OCR_SCREEN_INDEX", "label": "Screen index", "type": "int", "default": 1, "min": 1, "max": 16, "hidden": True},
                ],
            ),
        ]
        # Hidden region coordinate fields for all supported boxes.
        for idx in range(1, self.OCR_MAX_REGIONS + 1):
            suffix = "" if idx == 1 else str(idx)
            left[0][1].extend([
                {"key": f"OCR_REGION{suffix}_SCREEN", "label": f"Region {idx} Screen", "type": "int", "default": 1, "min": 1, "max": 64, "hidden": True},
                {"key": f"OCR_REGION{suffix}_X", "label": f"Region {idx} X", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
                {"key": f"OCR_REGION{suffix}_Y", "label": f"Region {idx} Y", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
                {"key": f"OCR_REGION{suffix}_W", "label": f"Region {idx} W", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
                {"key": f"OCR_REGION{suffix}_H", "label": f"Region {idx} H", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
            ])
        return [left]
    @staticmethod
    def _coerce_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        return text in ("1", "true", "yes", "on")
    
    @staticmethod
    def _coerce_int(value, default: int = 0, min_v=None, max_v=None) -> int:
        try:
            num = int(float(str(value).strip().replace(",", ".")))
        except Exception:
            num = int(default)
        if min_v is not None and num < int(min_v):
            num = int(min_v)
        if max_v is not None and num > int(max_v):
            num = int(max_v)
        return int(num)

    def _build_ocr_actions(self, ocr_tab: tk.Frame) -> None:
        actions = tk.LabelFrame(ocr_tab, text="Actions", padx=10, pady=8)
        actions.pack(fill="x", padx=8, pady=(0, 8), anchor="n")

        top = tk.Frame(actions)
        top.pack(fill="x", expand=True)
        left = tk.Frame(top)
        left.pack(side="left", fill="x", expand=True)
        right = tk.Frame(top)
        right.pack(side="right")

        self._ocr_area_select_btn = tk.Button(left, text="Select OCR Area", command=self._handle_select_ocr_area)
        self._ocr_area_select_btn.pack(side="left")
        self._ocr_area_select_var = tk.StringVar(value="1")
        try:
            self._ocr_area_select_var.trace_add("write", self._on_ocr_area_selection_changed)
        except Exception:
            pass
        self._ocr_area_select_menu = tk.OptionMenu(left, self._ocr_area_select_var, "1")
        self._ocr_area_select_menu.pack(side="left", padx=(4, 0))

        self._refresh_ocr_area_buttons()
        tk.Button(right, text="Read Now (Set Time)", command=self._handle_ocr_read_now).pack(side="right")
        tk.Button(right, text="Sync Now (5s)", command=self._handle_ocr_sync_now).pack(side="right", padx=(6, 0))

        screen_row = tk.Frame(actions)
        screen_row.pack(fill="x", pady=(8, 0))
        tk.Label(screen_row, text="Screen:").pack(side="left")
        self._build_ocr_screen_buttons(screen_row)

        self._update_ocr_screen_button_styles()

    def _on_ocr_area_selection_changed(self, *_args):
        try:
            values = self._get_ocr_values_from_vars()
            self._ocr_selected_screen = self._get_ocr_screen_for_region(
                values,
                self._get_selected_ocr_area_index(),
            )
        except Exception:
            pass
        self._update_ocr_screen_button_styles()

    def _get_ocr_region_count_from_vars(self) -> int:
        default = int(self.config.get("OCR_REGION_COUNT") or 2)
        try:
            var = getattr(self, "_advanced_vars", {}).get("OCR_REGION_COUNT")
            raw = var.get() if var is not None else default
        except Exception:
            raw = default
        return self._coerce_int(raw, default=default, min_v=1, max_v=self.OCR_MAX_REGIONS)

    def _get_selected_ocr_area_index(self) -> int:
        count = self._get_ocr_region_count_from_vars()
        var = getattr(self, "_ocr_area_select_var", None)
        try:
            raw = var.get() if var is not None else "1"
        except Exception:
            raw = "1"
        return self._coerce_int(raw, default=1, min_v=1, max_v=count)

    @staticmethod
    def _get_ocr_region_screen_key(index: int) -> str:
        suffix = "" if int(index) == 1 else str(int(index))
        return f"OCR_REGION{suffix}_SCREEN"

    def _get_ocr_screen_for_region(self, values: dict, index: int) -> int:
        default_screen = self._coerce_int(values.get("OCR_SCREEN_INDEX", 1), default=1, min_v=1, max_v=64)
        screen_key = self._get_ocr_region_screen_key(index)
        raw = values.get(screen_key, default_screen)
        return self._coerce_int(raw, default=default_screen, min_v=1, max_v=64)

    def _refresh_ocr_area_buttons(self) -> None:
        select_var = getattr(self, "_ocr_area_select_var", None)
        select_menu = getattr(self, "_ocr_area_select_menu", None)
        if select_var is None:
            return
        count = self._get_ocr_region_count_from_vars()
        options = [str(i) for i in range(1, count + 1)]

        def _refresh_menu(menu_widget, var):
            if menu_widget is None or var is None:
                return
            try:
                menu = menu_widget["menu"]
                menu.delete(0, "end")
                for opt in options:
                    menu.add_command(label=opt, command=lambda v=opt, vv=var: vv.set(v))
            except Exception:
                pass

        if select_var.get() not in options:
            select_var.set(options[0])
        _refresh_menu(select_menu, select_var)

    def _build_ocr_screen_buttons(self, parent: tk.Frame) -> None:
        for child in parent.winfo_children():
            if isinstance(child, tk.Button):
                child.destroy()
        monitors = get_monitor_rects(self.root)
        count = max(1, len(monitors))
        self._ocr_screen_buttons = {}

        for idx in range(1, count + 1):
            btn = tk.Button(parent, text=f"Screen {idx}", command=lambda i=idx: self._set_ocr_screen_index(i))
            btn.pack(side="left", padx=(6, 0))
            self._ocr_screen_buttons[idx] = btn
            self._remember_ocr_button_defaults(btn)

        if hasattr(self, "_ocr_area_select_btn"):
            self._remember_ocr_button_defaults(self._ocr_area_select_btn)

    def _remember_ocr_button_defaults(self, btn: tk.Button) -> None:
        if btn is None:
            return
        if hasattr(self, "_ocr_button_defaults"):
            return
        try:
            self._ocr_button_defaults = {
                "bg": btn.cget("bg"),
                "fg": btn.cget("fg"),
                "activebackground": btn.cget("activebackground"),
                "activeforeground": btn.cget("activeforeground"),
            }
        except Exception:
            self._ocr_button_defaults = None

    def _apply_ocr_button_style(self, btn: tk.Button, active: bool) -> None:
        if btn is None:
            return
        if active:
            try:
                btn.configure(bg="#2f8f4e", fg="white", activebackground="#2f8f4e", activeforeground="white")
            except Exception:
                pass
            return
        defaults = getattr(self, "_ocr_button_defaults", None)
        if not defaults:
            return
        try:
            btn.configure(
                bg=defaults.get("bg"),
                fg=defaults.get("fg"),
                activebackground=defaults.get("activebackground"),
                activeforeground=defaults.get("activeforeground"),
            )
        except Exception:
            pass

    def _update_ocr_screen_button_styles(self) -> None:
        values = {}
        try:
            values = self._get_ocr_values_from_vars()
        except Exception:
            values = {}

        region_count = self._get_ocr_region_count_from_vars()
        select_idx = self._get_selected_ocr_area_index()
        suffix = "" if select_idx == 1 else str(select_idx)
        rw = self._coerce_int(values.get(f"OCR_REGION{suffix}_W", 0), default=0)
        rh = self._coerce_int(values.get(f"OCR_REGION{suffix}_H", 0), default=0)
        area_selected = rw > 0 and rh > 0
        screen_idx = self._get_ocr_screen_for_region(values, select_idx)

        select_btn = getattr(self, "_ocr_area_select_btn", None)
        self._apply_ocr_button_style(select_btn, area_selected)

        for idx, btn in getattr(self, "_ocr_screen_buttons", {}).items():
            active = (idx == screen_idx)
            self._apply_ocr_button_style(btn, active)

    def _set_ocr_screen_index(self, index: int) -> None:
        self._ocr_selected_screen = int(index)
        vars_map = getattr(self, "_advanced_vars", {})
        global_var = vars_map.get("OCR_SCREEN_INDEX")
        if global_var is not None:
            global_var.set(str(int(index)))
        selected_idx = self._get_selected_ocr_area_index()
        region_screen_var = vars_map.get(self._get_ocr_region_screen_key(selected_idx))
        if region_screen_var is not None:
            region_screen_var.set(str(int(index)))
        if hasattr(self, "_advanced_status_var"):
            self._advanced_status_var.set(f"OCR region {selected_idx} screen set to {int(index)}.")
        self._apply_ocr_values_runtime()
        self._update_ocr_screen_button_styles()

    def _set_ocr_region_vars(
        self,
        x: int,
        y: int,
        w: int,
        h: int,
        index: int = 1,
        screen=None,
    ) -> None:
        suffix = "" if index == 1 else str(index)
        label = "OCR region" if index == 1 else f"OCR region {index}"
        mapping = {
            f"OCR_REGION{suffix}_X": x,
            f"OCR_REGION{suffix}_Y": y,
            f"OCR_REGION{suffix}_W": w,
            f"OCR_REGION{suffix}_H": h,
        }
        if screen is not None:
            mapping[self._get_ocr_region_screen_key(index)] = int(screen)
        for key, val in mapping.items():
            var = getattr(self, "_advanced_vars", {}).get(key)
            if var is not None:
                var.set(str(int(val)))
        if hasattr(self, "_advanced_status_var"):
            if w > 0 and h > 0:
                self._advanced_status_var.set(f"{label} set (area mode).")
            else:
                cleared_msg = f"{label} cleared (default bottom half)." if index == 1 else f"{label} cleared."
                self._advanced_status_var.set(cleared_msg)
        self._update_ocr_screen_button_styles()

    def _get_ocr_values_from_vars(self) -> dict:
        values = {}
        meta = getattr(self, "_advanced_meta", {})
        vars_map = getattr(self, "_advanced_vars", {})
        for key in self.OCR_KEYS:
            spec = meta.get(key)
            var = vars_map.get(key)
            if spec is None or var is None:
                continue
            if spec["type"] == "bool":
                values[key] = bool(var.get())
                continue
            if spec["type"] == "int":
                values[key] = self._coerce_int(var.get(), default=spec.get("default", 0),
                                               min_v=spec.get("min"), max_v=spec.get("max"))
                continue
            if spec["type"] == "float":
                try:
                    values[key] = float(str(var.get()).strip().replace(",", "."))
                except Exception:
                    values[key] = float(spec.get("default", 0.0))
                continue
            text = str(var.get()).strip()
            allow_empty = bool(spec.get("allow_empty", False))
            if not text and not allow_empty:
                text = str(spec.get("default", ""))
            values[key] = text
        return values

    def _apply_ocr_values_runtime(self):
        try:
            values = self._get_ocr_values_from_vars()
        except Exception:
            return
        if not values:
            return
        try:
            self._on_advanced_apply(dict(values), False)
        except Exception:
            pass
        self._refresh_ocr_area_buttons()
        self._update_ocr_screen_button_styles()

    def _handle_ocr_read_now(self):
        values = self._get_ocr_values_from_vars()
        try:
            self._on_ocr_read_now(dict(values))
        except Exception:
            pass

    def _handle_ocr_sync_now(self):
        values = self._get_ocr_values_from_vars()
        try:
            self._on_ocr_sync_now(dict(values))
        except Exception:
            pass

    def _handle_select_ocr_area(self) -> None:
        var = getattr(self, "_ocr_area_select_var", None)
        try:
            index = int(var.get()) if var is not None else 1
        except Exception:
            index = 1
        self._select_ocr_region(index)

    def _select_ocr_region(self, index: int = 1):
        values = self._get_ocr_values_from_vars()
        screen_idx = self._get_ocr_screen_for_region(values, index)
        self._ocr_selected_screen = int(screen_idx)
        monitors = get_monitor_rects(self.root)
        if not monitors:
            monitors = [(0, 0, 1920, 1080)]

        if screen_idx <= 0:
            min_x = min(r[0] for r in monitors)
            min_y = min(r[1] for r in monitors)
            max_x = max(r[0] + r[2] for r in monitors)
            max_y = max(r[1] + r[3] for r in monitors)
            base_x, base_y = int(min_x), int(min_y)
            sw, sh = int(max_x - min_x), int(max_y - min_y)
        else:
            if screen_idx > len(monitors):
                screen_idx = 1
            base_x, base_y, sw, sh = monitors[screen_idx - 1]

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.25)
        except Exception:
            pass
        win.configure(bg="black")
        win.geometry(f"{int(sw)}x{int(sh)}+{int(base_x)}+{int(base_y)}")
        win.focus_set()
        win.grab_set()

        canvas = tk.Canvas(win, width=sw, height=sh, bg="black", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)

        state = {"x0": 0, "y0": 0, "rect": None}

        def _on_press(event):
            state["x0"] = event.x
            state["y0"] = event.y
            if state["rect"] is not None:
                canvas.delete(state["rect"])
                state["rect"] = None
            state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                    outline="#00ff66", width=2)

        def _on_drag(event):
            if state["rect"] is None:
                return
            canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

        def _finish(x1, y1, x2, y2):
            win.grab_release()
            win.destroy()
            x = int(min(x1, x2))
            y = int(min(y1, y2))
            w = int(abs(x2 - x1))
            h = int(abs(y2 - y1))
            if w < 5 or h < 5:
                self._set_ocr_region_vars(0, 0, 0, 0, index=index, screen=screen_idx)
            else:
                self._set_ocr_region_vars(x, y, w, h, index=index, screen=screen_idx)
            self._apply_ocr_values_runtime()

        def _on_release(event):
            _finish(state["x0"], state["y0"], event.x, event.y)

        def _on_cancel(_event=None):
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        canvas.bind("<ButtonPress-1>", _on_press)
        canvas.bind("<B1-Motion>", _on_drag)
        canvas.bind("<ButtonRelease-1>", _on_release)
        win.bind("<Escape>", _on_cancel)

    def _load_advanced_values_into_vars(self):
        if not hasattr(self, "_advanced_vars") or not hasattr(self, "_advanced_meta"):
            return
        for key, spec in self._advanced_meta.items():
            if key not in self._advanced_vars:
                continue
            cfg_val = self.config.get(key)
            if cfg_val is None:
                cfg_val = spec.get("default")
            var = self._advanced_vars[key]
            if spec["type"] == "bool":
                var.set(self._coerce_bool(cfg_val))
            elif spec["type"] == "int":
                try:
                    val = int(float(str(cfg_val)))
                except Exception:
                    val = int(spec.get("default", 0))
                var.set(str(val))
            elif spec["type"] == "float":
                try:
                    val = float(str(cfg_val).replace(",", "."))
                except Exception:
                    val = float(spec.get("default", 0.0))
                var.set(self._format_number(val))
            else:
                if isinstance(cfg_val, list):
                    text = ", ".join(str(v).strip() for v in cfg_val if str(v).strip())
                else:
                    text = str(cfg_val).strip() if cfg_val is not None else ""
                allow_empty = bool(spec.get("allow_empty", False))
                if (not text) and ((cfg_val is None) or (not allow_empty)):
                    text = str(spec.get("default", ""))
                var.set(text)
        self._sync_advanced_startup_vars_from_runtime()
        try:
            values = self._get_ocr_values_from_vars()
            self._ocr_selected_screen = self._get_ocr_screen_for_region(
                values,
                self._get_selected_ocr_area_index(),
            )
        except Exception:
            self._ocr_selected_screen = None
        if hasattr(self, "_advanced_status_var"):
            self._advanced_status_var.set("Loaded values from config.")
        self._refresh_phone_toggle_button()
        self._refresh_ocr_area_buttons()
        self._update_ocr_screen_button_styles()

    def _collect_advanced_values(self):
        if not hasattr(self, "_advanced_vars") or not hasattr(self, "_advanced_meta"):
            return None
        values = {}
        errors = []
        for key, spec in self._advanced_meta.items():
            var = self._advanced_vars.get(key)
            if var is None:
                continue
            if spec["type"] == "bool":
                values[key] = bool(var.get())
                continue
            if spec["type"] == "str":
                text = str(var.get()).strip()
                allow_empty = bool(spec.get("allow_empty", False))
                if not text and not allow_empty:
                    text = str(spec.get("default", ""))
                var.set(text)
                values[key] = text
                continue
            if spec["type"] == "int":
                raw = str(var.get()).strip().replace(",", ".")
                try:
                    num = int(float(raw))
                except Exception:
                    errors.append(spec["label"])
                    continue
                min_v = spec.get("min")
                max_v = spec.get("max")
                if min_v is not None and num < int(min_v):
                    num = int(min_v)
                if max_v is not None and num > int(max_v):
                    num = int(max_v)
                values[key] = num
                var.set(str(num))
                continue
            if spec["type"] == "float":
                raw = str(var.get()).strip().replace(",", ".")
                try:
                    num = float(raw)
                except Exception:
                    errors.append(spec["label"])
                    continue
                min_v = spec.get("min")
                max_v = spec.get("max")
                if min_v is not None and num < float(min_v):
                    num = float(min_v)
                if max_v is not None and num > float(max_v):
                    num = float(max_v)
                values[key] = float(num)
                var.set(self._format_number(num))
                continue
        if errors:
            return {"errors": errors}
        return {"values": values}

    def _get_selected_advanced_tab_keys(self):
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return []
        try:
            tab_id = notebook.select()
        except Exception:
            tab_id = ""
        if not tab_id:
            return []
        keys = getattr(self, "_advanced_tab_key_map", {}).get(str(tab_id), [])
        return list(keys or [])

    def _reset_selected_advanced_tab_to_defaults(self):
        keys = self._get_selected_advanced_tab_keys()
        if not keys:
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("No tab selected to reset.")
            return
        self._reset_advanced_values_to_defaults(keys=keys)
        self._apply_advanced_settings(persist=True)
        if hasattr(self, "_advanced_status_var"):
            self._advanced_status_var.set("Reset current tab to defaults and saved.")

    def _reset_advanced_values_to_defaults(self, keys=None):
        if not hasattr(self, "_advanced_vars") or not hasattr(self, "_advanced_meta"):
            return
        if keys:
            target_keys = [k for k in keys if k in self._advanced_meta]
        else:
            target_keys = list(self._advanced_meta.keys())
        for key in target_keys:
            spec = self._advanced_meta.get(key)
            if spec is None:
                continue
            if key not in self._advanced_vars:
                continue
            default = spec.get("default")
            var = self._advanced_vars[key]
            if spec["type"] == "bool":
                var.set(bool(default))
            elif spec["type"] == "int":
                try:
                    var.set(str(int(default)))
                except Exception:
                    var.set("0")
            elif spec["type"] == "float":
                try:
                    var.set(self._format_number(float(default)))
                except Exception:
                    var.set("0")
            else:
                var.set(str(default or ""))

    def _apply_advanced_settings(self, persist: bool):
        result = self._collect_advanced_values()
        if not result:
            return
        if "errors" in result:
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Invalid values: " + ", ".join(result["errors"]))
            try:
                self.root.bell()
            except Exception:
                pass
            return

        values = result["values"]
        try:
            self._on_advanced_apply(dict(values), bool(persist))
        except Exception:
            pass

        if persist:
            try:
                if hasattr(self.config, "set_many"):
                    self.config.set_many(values)
                else:
                    for key, value in values.items():
                        self.config.set(key, value)
            except Exception:
                for key, value in values.items():
                    try:
                        self.config.set(key, value)
                    except Exception:
                        pass
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Saved and applied.")
        else:
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Applied for current session (not saved).")
        
"""
Settings window UI (root) and control window (floating playback controls).

This module is the main user-facing UI for controlling time, offsets, episodes, and mode.
"""

import tkinter as tk
from tkinter import ttk
import threading
from re import fullmatch
from model.config_manager import ConfigManager
from view.settings_advanced_ui import SettingsAdvancedUI
from utils import (make_draggable,format_time,get_monitor_rects,make_nonactivating_window,show_window_no_activate_minimizable)

class SettingsUI:
    NUMBER_PATTERN = r"\s*([-+]?\d+(?:[.,]\d+)?)\s*(?:s|sec|secs|second|seconds)?\s*"
    OCR_MAX_REGIONS = 8
    _OCR_REGION_KEYS = []
    _OCR_REGION_SCREEN_KEYS = []
    for _idx in range(1, OCR_MAX_REGIONS + 1):
        _suffix = "" if _idx == 1 else str(_idx)
        for _axis in ("X", "Y", "W", "H"):
            _OCR_REGION_KEYS.append(f"OCR_REGION{_suffix}_{_axis}")
        _OCR_REGION_SCREEN_KEYS.append(f"OCR_REGION{_suffix}_SCREEN")
    OCR_KEYS = (
        "OCR_ENABLED",
        "OCR_DEBUG",
        "OCR_TESSERACT_CMD",
        "OCR_TESSERACT_PSM",
        "OCR_TESSERACT_OEM",
        "OCR_CHAR_WHITELIST",
        "OCR_SCREEN_INDEX",
        "OCR_REGION_COUNT",
        "OCR_SYNC_AFTER_ANKI",
        *_OCR_REGION_KEYS,
        *_OCR_REGION_SCREEN_KEYS,
    )

    def __init__(
        self,
        root: tk.Tk,
        config: ConfigManager,
        total_duration: float,
        initial_episode=None,
        start_hidden: bool = False,
    ):
        self.root = root
        self.config = config
        self.total_duration = total_duration
        self.initial_episode = initial_episode
        self._start_hidden = bool(start_hidden)

        self._init_defaults()
        self._init_vars()
        self._init_callbacks()

        self.episode_inc_btn = None
        self.episode_dec_btn = None
        self.input_mode_btn = None
        self.mode_toggle_btn = None
        self.advanced_settings_btn = None
        self._phone_mode_toggle_btn = None
        self.play_pause_btn = None
        self.slider = None
        self.advanced_window = None
        self._advanced_notebook = None
        self._advanced_tab_sizes = {}
        self._advanced_tab_key_map = {}
        self._advanced_resize_job = None
        self._root_topmost_before_advanced = None
        self._ocr_region_count_trace_var = None
        self._ocr_region_count_refresh_job = None
        self.adv_settings = SettingsAdvancedUI(self)
        self._build_settings_frame()
        self._build_control_window()
        try:
            make_nonactivating_window(self.root)
            self.root.after(0, lambda: make_nonactivating_window(self.root))
        except Exception:
            pass
        if self._start_hidden:
            try:
                self.control_window.withdraw()
            except Exception:
                pass

    def _init_defaults(self):
        get = self.config.get
        self.default_offset = get('EXTRA_OFFSET')        
        self._last_offset_value = float(self.default_offset)
        self.default_skip = get('DEFAULT_SKIP')
        self._last_skip_value   = float(self.default_skip)
        self.default_start = get('DEFAULT_START_TIME')
        self.default_phone_mode = get("PHONEMODE_DEFAULT")
        self.input_mode = self._resolve_input_mode()
        self._last_active_input_mode = self.input_mode if self.input_mode in (1, 2) else 1
        self.numpad_mode_enabled = (self.input_mode == 2)
        self._sync_input_mode_runtime_flags()

        self.default_x = self.config.get("LAST_SETTINGS_WINDOW_X")
        self.default_y = self.config.get("LAST_SETTINGS_WINDOW_Y")
        self.win_x = get('LAST_CONTROL_WINDOW_X')
        self.win_y = get('LAST_CONTROL_WINDOW_Y')
        self._control_win_x = int(self.win_x) if isinstance(self.win_x, int) else 30
        self._control_win_y = int(self.win_y) if isinstance(self.win_y, int) else 30

    def _resolve_input_mode(self) -> int:
        """Return input mode 1/2/3 with backward compatibility for old config keys."""
        mode = self.config.get("INPUT_MODE")
        parsed = None
        try:
            parsed = int(mode)
        except Exception:
            parsed = None
        if parsed in (1, 2):
            return parsed
        if parsed == 3:
            last_active = self.config.get("LAST_ACTIVE_INPUT_MODE")
            try:
                last_active = int(last_active)
            except Exception:
                last_active = None
            if last_active in (1, 2):
                return last_active
            return 1
        parsed = 2 if bool(self.config.get("INPUT_MODE_NUMPAD") or False) else 1
        if bool(self.config.get("SHORTCUTS_DISABLED") or False):
            last_active = self.config.get("LAST_ACTIVE_INPUT_MODE")
            try:
                last_active = int(last_active)
            except Exception:
                last_active = None
            if last_active in (1, 2):
                return last_active
            return 1
        return parsed

    def _init_vars(self):
        val = float(self.default_offset)
        self.offset_var = tk.StringVar(value=self._format_seconds(val))

        val = float(self.default_skip)
        self.skip_var = tk.StringVar(value=self._format_seconds(val))

        self.episode_var = tk.StringVar(value="Movie" if self.initial_episode is None else str(self.initial_episode))
        self.setto_var = tk.StringVar(value="")
        self._last_episode_value = self.episode_var.get()

        self.control_time_seconds = tk.DoubleVar(value=self.default_start)
        self.control_time_str     = tk.StringVar(value=format_time(self.default_start))
        self.control_time_str.trace_add("write", self._adjust_time_entry_width)
        max_secs = self.total_duration + self._last_offset_value
        max_str  = format_time(max_secs)
        self._max_time_width = len(max_str)# + 1


    def _noop(self, *args, **kwargs):
        pass

    def _init_callbacks(self):
        for name in ("ep_change", "ep_inc", "ep_dec",
                     "slider_change", "slider_press", "slider_release",
                     "set_to", "open_srt", "show_handle",
                     #Control window:
                     "back", "forward", "play_pause",
                     "time_entry_return", "time_entry_clear",
                     "advanced_apply",
                     "ocr_read_now", "ocr_sync_now",
                     "anki_check", "settings_open"):
            setattr(self, f"_on_{name}", self._noop)

    # --------- SETTINGS FRAME ------------------------------------------------------------------------------------
    def _build_settings_frame(self):
        self.settings_frame = tk.LabelFrame(self.root)
        self.settings_frame.pack(fill="both", expand=True, padx=5, pady=5)
        self.settings_frame.grid_rowconfigure(1, weight=1)
        self.settings_frame.grid_columnconfigure(0, weight=1)

        # Frame for all except slider
        options_frame = tk.Frame(self.settings_frame)
        options_frame.grid(row=0, column=0, sticky="news", pady=0, padx=0)
        options_frame.grid_rowconfigure(0, weight=1)
        options_frame.grid_rowconfigure(1, weight=1)
        for col in range(6):
            options_frame.grid_columnconfigure(col, weight=1, uniform="settings_row2")

        # Row 0: compact search + episode controls with tools on the right.
        top_row = tk.Frame(options_frame, bg="#f0f0f0")
        top_row.grid(row=0, column=0, columnspan=6, padx=0, pady=0, sticky="ew")
        top_row.grid_columnconfigure(0, weight=0)
        top_row.grid_columnconfigure(1, weight=0)
        top_row.grid_columnconfigure(2, weight=1)
        top_row.grid_columnconfigure(3, weight=0)

        self.srt_button = tk.Button(
            top_row,
            text="\N{LEFT-POINTING MAGNIFYING GLASS}",
            width=2,
            height=1,
            relief="raised",
            command=lambda: self._on_open_srt(),
        )
        self.srt_button.grid(row=0, column=0, padx=(5, 2), pady=(5, 2), sticky="w")

        tk.Label(top_row, text="Episode", font=("Arial", 12), bg="#f0f0f0").grid(
            row=0, column=1, padx=(0, 2), pady=(5, 2), sticky="w"
        )

        episode_frame = tk.Frame(top_row, bg="#f0f0f0")
        episode_frame.grid(row=0, column=2, padx=(0, 2), pady=(5, 2), sticky="ew")
        episode_frame.grid_columnconfigure(0, weight=1)
        episode_frame.grid_columnconfigure((1, 2), weight=0)

        self.episode_entry = ttk.Combobox(
            episode_frame,
            textvariable=self.episode_var,
            font=("Arial", 12),
            width=4,
        )
        self.episode_entry.grid(row=0, column=0, sticky="ew")
        self.episode_entry.bind("<Return>", lambda e: (self._on_ep_entry_change(), self.root.focus()))
        self.episode_entry.bind("<<ComboboxSelected>>", lambda e: (self._on_ep_entry_change(), self.root.focus()))
        self.episode_entry.bind("<Button-1>", self._on_episode_entry_click, add="+")
        self.episode_entry.bind("<FocusOut>", self._on_episode_entry_focus_out, add="+")

        self.episode_dec_btn = tk.Button(
            episode_frame,
            text="-",
            font=("Arial", 8, "bold"),
            width=1,
            height=1,
            command=lambda: self._on_ep_dec(),
        )
        self.episode_dec_btn.grid(row=0, column=1, sticky="e")
        self.episode_inc_btn = tk.Button(
            episode_frame,
            text="+",
            font=("Arial", 8, "bold"),
            width=1,
            height=1,
            command=lambda: self._on_ep_inc(),
        )
        self.episode_inc_btn.grid(row=0, column=2, sticky="e")

        mode_tools_frame = tk.Frame(top_row, bg="#f0f0f0")
        mode_tools_frame.grid(row=0, column=3, padx=(2, 5), pady=(5, 2), sticky="e")

        self.input_mode_btn = tk.Button(
            mode_tools_frame,
            text="M1",
            width=2,
            height=1,
            relief="raised",
            command=self._toggle_input_mode,
        )
        self.input_mode_btn.pack(side="left", padx=(0, 2))

        self.mode_toggle_btn = None
        self.advanced_settings_btn = tk.Button(
            mode_tools_frame,
            text="\N{GEAR}",
            width=2,
            height=1,
            relief="raised",
            command=self._open_advanced_settings_window,
        )
        self.advanced_settings_btn.pack(side="left")
        self._refresh_input_mode_button()

        # Row 1: six equal parts (label/value pairs).
        setto_pair = tk.Frame(options_frame, bg="#f0f0f0")
        setto_pair.grid(row=1, column=0, columnspan=2, padx=(5, 2), pady=(2, 5), sticky="ew")
        setto_pair.grid_columnconfigure(1, weight=1)
        tk.Label(setto_pair, text="Set to", font=("Arial", 12), bg="#f0f0f0").grid(row=0, column=0, sticky="w", padx=(0, 2))
        self.setto_entry = tk.Entry(setto_pair, textvariable=self.setto_var, font=("Arial", 12), width=4)
        self.setto_entry.grid(row=0, column=1, sticky="ew")
        self.setto_entry.bind("<Return>", lambda e: self._on_set_to_return(self.setto_var.get()))

        offset_pair = tk.Frame(options_frame, bg="#f0f0f0")
        offset_pair.grid(row=1, column=2, columnspan=2, padx=(2, 2), pady=(2, 5), sticky="ew")
        offset_pair.grid_columnconfigure(1, weight=1)
        tk.Label(offset_pair, text="Offset", font=("Arial", 12), bg="#f0f0f0").grid(row=0, column=0, sticky="w", padx=(0, 2))
        self.offset_entry = tk.Entry(offset_pair, textvariable=self.offset_var, font=("Arial", 12), width=4)
        self.offset_entry.grid(row=0, column=1, sticky="ew")
        self.offset_entry.bind("<Button-1>", self._clear_entry)
        self.offset_entry.bind("<FocusOut>", self._on_entry_focus_out)
        self.offset_entry.bind("<Return>", self._on_entry_focus_out)

        skip_pair = tk.Frame(options_frame, bg="#f0f0f0")
        skip_pair.grid(row=1, column=4, columnspan=2, padx=(2, 5), pady=(2, 5), sticky="ew")
        skip_pair.grid_columnconfigure(1, weight=1)
        tk.Label(skip_pair, text="Skip", font=("Arial", 12), bg="#f0f0f0").grid(row=0, column=0, sticky="w", padx=(0, 2))
        self.skip_entry = tk.Entry(skip_pair, textvariable=self.skip_var, font=("Arial", 12), width=4)
        self.skip_entry.grid(row=0, column=1, sticky="ew")
        self.skip_entry.bind("<Button-1>", self._clear_entry)
        self.skip_entry.bind("<FocusOut>", self._on_entry_focus_out)
        self.skip_entry.bind("<Return>", self._on_entry_focus_out)

        self.slider_frame = tk.Frame(self.settings_frame)
        self.slider_frame.grid(row=1, column=0, sticky="ew", padx=(0,0), pady=(0,0))
        self.slider_frame.grid_columnconfigure(0, weight=1)
        self.slider_frame.grid_rowconfigure((0,1), weight=1)

        # Time overlay for slider
        self.time_overlay_frame = tk.Frame(self.slider_frame,height = 20)
        self.time_overlay_frame.grid(row=0, column=0, sticky="ew")
        self.time_overlay_frame.grid_columnconfigure(0, weight=1)

        self.time_overlay = tk.Canvas(self.time_overlay_frame, height=18, highlightthickness=0)
        self.time_overlay.grid(row=0, column=0, sticky="ew") 

        self.time_overlay_text = self.time_overlay.create_text(
            0, 0,
            text=self.control_time_str.get(),
            font=("Arial", 10)
        )

        # Slider
        self.slider  = tk.Scale(
            self.slider_frame,
            from_=0, to=(self.total_duration + self.default_offset),
            orient="horizontal",
            resolution=00.1,
            showvalue=False,
            sliderlength=32,
            command=lambda v: self._on_slider_change(v)
        )
        self.slider.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        self.slider.set(float(self.default_start))
        self.slider.bind("<ButtonPress-1>", self._on_click_or_drag)
        self.slider.bind("<B1-Motion>",      self._on_click_or_drag)
        self.slider.bind("<ButtonRelease-1>", lambda e: self._on_slider_release(e))
        self.update_time_overlay_position()

    # --------- CONTROL WINDOW ------------------------------------------------------------------------------------------------------------------
    def _build_control_window(self):
        self.control_window = tk.Toplevel(self.root)
        self.control_window.overrideredirect(True)
        self.control_window.attributes("-topmost", True)
        self.control_window.minsize(200, 40)
        
        main_frame = tk.Frame(self.control_window, bg="black")
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure(1, weight=0)
        main_frame.columnconfigure((0,2), weight=1)
        main_frame.rowconfigure((0,1), weight=1)

        self.back_button = tk.Button(main_frame, text="<< Skip", font=("Arial", 12, "bold"),
                                      width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")

        self.time_entry = tk.Entry(main_frame, textvariable=self.control_time_str,
                                        font=("Arial", 14, "bold"), bd=0,
                                        bg="black", fg="white", width=self._max_time_width, justify="center")

        self.play_pause_btn = tk.Button(main_frame, text="Play", bg="green",
                                            activebackground="green", font=("Arial", 12, "bold"), height=1, relief="flat")
        
        self.forward_button = tk.Button(main_frame, text="Skip >>", font=("Arial", 12, "bold"),
                                        width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")

        self.back_button.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.play_pause_btn.grid(row=1, column=1,pady=0, sticky="nsew")
        self.time_entry.grid(row=0, column=1, sticky="nsew", ipady=5)
        self.forward_button.grid(row=0, column=2, rowspan=2, sticky="nsew")

        self.handle_settings_frame = tk.Frame(self.control_window, width=30, height=10)
        self.handle_settings_frame.place(x=0, y=0)
        self.settings_btn = tk.Button(self.handle_settings_frame,
                                      relief="raised", bg= "grey")
        self.refresh_btn = tk.Button(self.handle_settings_frame,
                                     relief="raised", bg= "grey")

        self.settings_btn.place(x=10, y=0, width=10, height=10)
        self.refresh_btn.place(x=20, y=0, width=10, height=10)

        self.control_drag_handle = tk.Frame(self.handle_settings_frame, bg="gray", width=10, height=10)
        self.control_drag_handle.place(x=0, y=0)
        self.control_drag_handle.lift()
        self._set_phone_mode_styles(self.default_phone_mode)
        self._refresh_phone_toggle_button()

        make_draggable(
            self.control_drag_handle,
            self.control_window,
            on_release=self._save_control_window_pos
        )

        self.forward_button.bind("<ButtonPress>", lambda event: self._on_forward())
        self.back_button.bind("<ButtonPress>", lambda event: self._on_back())
        self.play_pause_btn.bind("<ButtonPress>", lambda event: (self._on_play_pause()))
        self.settings_btn.bind("<ButtonPress>", self._on_settings)
        self.refresh_btn.bind("<ButtonPress>", lambda ev: self.on_refresh_subtitles(ev))
        self.time_entry.bind("<Button-1>", lambda ev: self._on_time_entry_clear(ev))
        self.time_entry.bind("<FocusOut>", lambda ev: self._on_time_entry_return(ev))
        self.time_entry.bind("<Return>", lambda ev:   self._on_time_entry_return(ev))
        self.control_window.bind("<ButtonPress-1>", self._on_control_window_click, add="+")

        self.control_window.bind("<Enter>", lambda ev: self.bind_control_window_enter(ev))
        self.control_window.bind("<Leave>", lambda ev: self.bind_control_window_leave(ev))

    def show(self) -> None:
        """Show the floating control window (used after startup splash)."""
        try:
            self.control_window.deiconify()
            # Re-apply geometry after withdraw/deiconify (overrideredirect windows can reset to 0,0).
            try:
                self._set_phone_mode_styles(self.default_phone_mode)
            except Exception:
                pass
            self.control_window.lift()
            self.control_window.attributes("-topmost", True)
        except Exception:
            pass
        
    def _save_control_window_pos(self, x, y, w, h):
        self._control_win_x = x
        self._control_win_y = y
        
    def save_state(self):
        if (self._control_win_x, self._control_win_y) != (self.config.get("LAST_CONTROL_WINDOW_X"), self.config.get("LAST_CONTROL_WINDOW_Y")):
            self.config.set("LAST_CONTROL_WINDOW_X", self._control_win_x)
            self.config.set("LAST_CONTROL_WINDOW_Y", self._control_win_y)
        if self.default_phone_mode != self.config.get("PHONEMODE_DEFAULT"):
            self.config.set("PHONEMODE_DEFAULT", bool(self.default_phone_mode))
        try:
            saved_mode = int(self.config.get("INPUT_MODE") or 1)
        except Exception:
            saved_mode = 1
        if int(self.input_mode) != saved_mode:
            self.config.set("INPUT_MODE", int(self.input_mode))
        if bool(self.numpad_mode_enabled) != bool(self.config.get("INPUT_MODE_NUMPAD") or False):
            self.config.set("INPUT_MODE_NUMPAD", bool(self.numpad_mode_enabled))
        hotkeys_disabled = bool(self.input_mode == 3)
        if hotkeys_disabled != bool(self.config.get("SHORTCUTS_DISABLED") or False):
            self.config.set("SHORTCUTS_DISABLED", hotkeys_disabled)
        if self._last_active_input_mode in (1, 2):
            try:
                saved_last = int(self.config.get("LAST_ACTIVE_INPUT_MODE") or 0)
            except Exception:
                saved_last = 0
            if self._last_active_input_mode != saved_last:
                self.config.set("LAST_ACTIVE_INPUT_MODE", int(self._last_active_input_mode))
        # Persist offset and skip values
        try:
            saved_offset = float(self.config.get("EXTRA_OFFSET") or 0.0)
        except Exception:
            saved_offset = 0.0
        if abs(self._last_offset_value - saved_offset) > 0.001:
            self.config.set("EXTRA_OFFSET", self._last_offset_value)
        try:
            saved_skip = float(self.config.get("DEFAULT_SKIP") or 1.0)
        except Exception:
            saved_skip = 1.0
        if abs(self._last_skip_value - saved_skip) > 0.001:
            self.config.set("DEFAULT_SKIP", self._last_skip_value)
            
    # --------- PUBLIC binders ------------------------------------------------------------------------------------------------------------------
    # Settings window
    def bind_episode_change(self, on_ent, on_inc, on_dec):
        self._on_ep_entry_change = on_ent
        self._on_ep_inc          = on_inc
        self._on_ep_dec          = on_dec

    def set_episode_nav_state(self, can_dec: bool, can_inc: bool, is_movie: bool = False) -> None:
        try:
            self.episode_dec_btn.configure(state=(tk.NORMAL if can_dec else tk.DISABLED))
            self.episode_inc_btn.configure(state=(tk.NORMAL if can_inc else tk.DISABLED))
            self.episode_entry.configure(state=(tk.DISABLED if is_movie else tk.NORMAL))
        except Exception:
            pass

    def set_episode_values(self, values) -> None:
        """
        Update the dropdown list for the episode combobox.
        Values should be an iterable of ints/strings (will be converted to strings).
        """
        try:
            self.episode_entry.configure(values=[str(v) for v in (values or [])])
        except Exception:
            pass

    def _on_episode_entry_click(self, event):
        try:
            elem = event.widget.identify(event.x, event.y)
            if elem and "downarrow" in str(elem).lower():
                return
        except Exception:
            pass
        try:
            self._last_episode_value = self.episode_var.get()
        except Exception:
            self._last_episode_value = ""
        try:
            self.episode_var.set("")
        except Exception:
            pass

    def _on_episode_entry_focus_out(self, event):
        """
        Restore last value if the entry is left empty (or invalid) without pressing Enter.
        This must NOT trigger subtitle loading.
        """
        try:
            text = (self.episode_var.get() or "").strip()
        except Exception:
            text = ""
        if not text:
            try:
                self.episode_var.set(self._last_episode_value)
            except Exception:
                pass
            return
        if text.lower() == "movie":
            try:
                self.episode_var.set(self._last_episode_value)
            except Exception:
                pass
            return
        try:
            n = int(text)
            if n <= 0:
                raise ValueError()
        except Exception:
            try:
                self.episode_var.set(self._last_episode_value)
            except Exception:
                pass
    def bind_slider(self,   on_chg, on_pr, on_rl):
        self._on_slider_change   = on_chg
        self._on_slider_press    = on_pr
        self._on_slider_release  = on_rl
    def bind_set_to_return(self, cb):        self._on_set_to_return = cb
    def bind_open_srt(self, cb):             self._on_open_srt = cb
    def bind_show_subtitle_handle(self, cb): self._on_show_handle = cb

    # Control window
    def bind_back(self,      cb):            self._on_back       = cb
    def bind_forward(self,   cb):            self._on_forward    = cb
    def bind_play_pause(self,cb):            self._on_play_pause = cb
    def bind_time_entry_return(self, cb):    self._on_time_entry_return = cb
    def bind_time_entry_clear(self,  cb):    self._on_time_entry_clear = cb
    def bind_control_window_enter(self, cb): self._on_control_window_enter = cb
    def bind_control_window_leave(self, cb): self._on_control_window_leave = cb
    def bind_refresh_subtitles(self, cb):    self.on_refresh_subtitles = cb

    def bind_update_display(self, cb):       self.update_time_and_subtitle_displays = cb
    def bind_advanced_apply(self, cb):       self._on_advanced_apply = cb
    def bind_ocr_read_now(self, cb):         self._on_ocr_read_now = cb
    def bind_ocr_sync_now(self, cb):         self._on_ocr_sync_now = cb
    def bind_anki_check(self, cb):           self._on_anki_check = cb
    def bind_settings_open(self, cb):        self._on_settings_open = cb

    def update_time_overlay_position(self):
        self.root.update_idletasks()
        root_width = self.root.winfo_width()
        diff = root_width - 320
        min_x = 1+19
        max_x = 268 + diff + 19
        min_val = float(self.slider.cget('from'))
        max_val = float(self.slider.cget('to'))
        value = float(self.slider.get())
        rel = (value - min_val) / (max_val - min_val) if max_val != min_val else 0.0
        x = int(min_x + rel * (max_x - min_x))
        self.time_overlay.coords(self.time_overlay_text, x, 9+3)

    def _on_click_or_drag(self, event):
        self._on_slider_press(event)
        w      = self.slider.winfo_width() - self.slider["sliderlength"]
        x_off  = event.x - (self.slider["sliderlength"] / 2)
        frac   = max(0.0, min(1.0, x_off / w))
        start  = float(self.slider.cget("from"))
        end    = float(self.slider.cget("to"))
        new_val = start + frac * (end - start)
        self.slider.set(new_val)
        self._on_slider_change(str(new_val))
        return "break"

    # --------- PHONE MODE UI ADJUSTMENT ------------------------------------------------------------------------------------

    def _toggle_phone_mode(self):
        phone_mode = not self.default_phone_mode
        self.default_phone_mode = phone_mode
        self._set_phone_mode_styles(phone_mode)
        self._refresh_phone_toggle_button()
        self.control_window.attributes("-topmost", True)
        self._on_show_handle(self.default_phone_mode)

    def _refresh_phone_toggle_button(self):
        btn = getattr(self, "_phone_mode_toggle_btn", None)
        if btn is None:
            return
        try:
            active = bool(self.default_phone_mode)
            if active:
                btn.configure(
                    bg="#2f8f4e",
                    fg="white",
                    activebackground="#2f8f4e",
                    activeforeground="white",
                )
            else:
                btn.configure(
                    bg="SystemButtonFace",
                    fg="black",
                    activebackground="SystemButtonFace",
                    activeforeground="black",
                )
        except Exception:
            pass

    def _toggle_input_mode(self):
        if self.input_mode == 1:
            self.input_mode = 2
        elif self.input_mode == 2:
            self.input_mode = 3
        else:
            self.input_mode = 1
        if self.input_mode in (1, 2):
            self._last_active_input_mode = self.input_mode
        self._sync_input_mode_runtime_flags()
        self._refresh_input_mode_button()

    def set_hotkeys_disabled(self, disabled: bool):
        disabled = bool(disabled)
        if disabled:
            if self.input_mode in (1, 2):
                self._last_active_input_mode = self.input_mode
            self.input_mode = 3
        elif self.input_mode == 3:
            self.input_mode = self._last_active_input_mode if self._last_active_input_mode in (1, 2) else 1
        if self.input_mode in (1, 2):
            self._last_active_input_mode = self.input_mode
        self._sync_input_mode_runtime_flags()
        self._refresh_input_mode_button()

    def _sync_input_mode_runtime_flags(self):
        self.numpad_mode_enabled = (self.input_mode == 2)
        cfg = getattr(self.config, "config", None)
        if isinstance(cfg, dict):
            cfg["INPUT_MODE"] = int(self.input_mode)
            cfg["INPUT_MODE_NUMPAD"] = bool(self.numpad_mode_enabled)
            cfg["SHORTCUTS_DISABLED"] = bool(self.input_mode == 3)
            if self.input_mode in (1, 2):
                cfg["LAST_ACTIVE_INPUT_MODE"] = int(self.input_mode)

    def _refresh_input_mode_button(self):
        if not self.input_mode_btn:
            return
        if self.input_mode == 2:
            text, bg = "M2", "green"
        elif self.input_mode == 3:
            text, bg = "M3", "#d46a6a"
        else:
            text, bg = "M1", "SystemButtonFace"
        self.input_mode_btn.configure(text=text, bg=bg)
        
    def _set_phone_mode_styles(self, phone_mode: bool):
        if phone_mode:
            self.handle_settings_frame.configure(width=120, height=40)
            self.control_drag_handle.config(width=40, height=40)
            f_large = ("Arial", 30, "bold")
            f_btn = ("Arial", 22, "bold")
            self.settings_btn.place_configure(x=40, y=0, width=40, height=40)
            self.refresh_btn .place_configure(x= 80, y=0, width=40, height=40)
            h = 160
        else:
            self.handle_settings_frame.configure(width=30, height=10)
            self.control_drag_handle.config(width=10, height=10)
            f_large = ("Arial", 14, "bold")
            f_btn = ("Arial", 12, "bold")
            self.settings_btn.place_configure(x=10, y=0, width=10, height=10)
            self.refresh_btn .place_configure(x= 20, y=0, width=10, height=10)
            h = 40

        self.time_entry.config(font=f_large)
        self.play_pause_btn.config(font=f_btn)
        self.back_button.config(font=f_btn)
        self.forward_button.config(font=f_btn)

        self.time_entry.config(width=len(self.control_time_str.get()))
        self.control_window.update_idletasks()
        reqw = self.control_window.winfo_reqwidth()
        sw, sh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        x = self.win_x if 0 <= self.win_x <= sw - reqw else 30
        y = self.win_y if 0 <= self.win_y <= sh - h else sh - 100 - h
        self.control_window.geometry(f"{reqw}x{h}+{x}+{y}")

    def _adjust_time_entry_width(self, *args):
        self.time_entry.config(width=len(self.control_time_str.get()))
        self.control_window.update_idletasks()
        reqw = self.control_window.winfo_reqwidth()
        x = self.control_window.winfo_x()
        y = self.control_window.winfo_y()
        self.control_window.geometry(f"{reqw}x{self.control_window.winfo_height()}+{x}+{y}")

    def _on_control_window_click(self, event):
        if event.widget is self.time_entry:
            return
        try:
            self.control_window.focus_force()
        except Exception:
            pass
        try:
            self.control_window.after_idle(lambda: self.control_window.tk.call("focus", ""))
        except Exception:
            try:
                self.control_window.focus_set()
            except Exception:
                pass


    #HELPERS
    def _on_settings(self, event):#button to lift the root window
        show_window_no_activate_minimizable(self.root, topmost=True)
        try:
            self._on_settings_open()
        except Exception:
            pass

    def _sync_advanced_startup_vars_from_runtime(self) -> None:
        return self.adv_settings._sync_advanced_startup_vars_from_runtime()

    def _open_advanced_settings_window(self):
        return self.adv_settings._open_advanced_settings_window()

    def _format_number(self, value: float) -> str:
        value = float(value)
        if value.is_integer():
            return str(int(value))
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def _format_seconds(self, value: float) -> str:
        return f"{self._format_number(value)} s"

    def _parse_number(self, text: str):
        match = fullmatch(self.NUMBER_PATTERN, (text or "").strip())
        if not match:
            return None
        try:
            return float(match.group(1).replace(",", "."))
        except Exception:
            return None

    def _set_entry_value(self, entry, value: float):
        formatted = self._format_seconds(value)
        # Update both the StringVar and the entry widget to keep them in sync
        if entry is self.offset_entry:
            self.offset_var.set(formatted)
        elif entry is self.skip_entry:
            self.skip_var.set(formatted)
        entry.delete(0, tk.END)
        entry.insert(0, formatted)

    def _get_last_value(self, entry):
        if entry is self.offset_entry:
            return "_last_offset_value", self._last_offset_value
        if entry is self.skip_entry:
            return "_last_skip_value", self._last_skip_value
        return None, None

    def _clear_entry(self, event):
        entry = event.widget
        attr, _ = self._get_last_value(entry)
        if not attr:
            return
        parsed = self._parse_number(entry.get().replace(",", "."))
        if parsed is not None:
            setattr(self, attr, parsed)
        entry.delete(0, tk.END)

    def _on_entry_focus_out(self, event):
        entry = event.widget
        attr, last_val = self._get_last_value(entry)
        if not attr:
            return
        text = entry.get().replace(",", ".").strip()
        parsed = self._parse_number(text)
        if parsed is None:
            self._set_entry_value(entry, last_val)
        else:
            value = parsed
            previous = last_val
            setattr(self, attr, value)
            self._set_entry_value(entry, value)
            if entry is self.offset_entry:
                self._apply_offset_change(value, persist=True, previous_value=previous)
            elif entry is self.skip_entry:
                self._apply_skip_change(value, persist=True)
        entry.master.focus_set()

    def _apply_offset_change(self, value_seconds: float, persist: bool, previous_value=None):
        try:
            previous = float(previous_value)
            delta = float(value_seconds) - previous
        except Exception:
            delta = 0.0
        self.slider.config(to=self.total_duration + value_seconds)
        if abs(delta) >= 0.001:
            try:
                self.slider.set(float(self.slider.get()) + delta)
            except Exception:
                pass
        self.update_time_and_subtitle_displays()
        self._on_slider_release(None)
        self._sync_advanced_startup_vars_from_runtime()
        if persist:
            try:
                self.config.set("EXTRA_OFFSET", value_seconds)
            except Exception:
                pass

    def _apply_skip_change(self, value_seconds: float, persist: bool):
        """Update skip value and optionally persist to config."""
        self._sync_advanced_startup_vars_from_runtime()
        if persist:
            try:
                self.config.set("DEFAULT_SKIP", value_seconds)
            except Exception:
                pass

    def set_total_duration(self, total_duration: float):
        self.total_duration = total_duration
        self.slider.config(to=total_duration + self._last_offset_value)
"""
Subtitle overlay window (transparent canvas) that renders the current subtitle text.

This is a separate always-on-top, borderless toplevel window that can be dragged.
"""

import tkinter as tk
from typing import List, Optional

from model.config_manager import ConfigManager
from utils import make_draggable, make_nonactivating_tool_window, show_window_no_activate

class SubtitleOverlayUI:

    def __init__(
        self,
        root: tk.Tk,
        config: ConfigManager,
        cleaned_subs: Optional[List[str]] = None,
        overlay_geometry=None,
        start_hidden: bool = False,
    ) -> None:
        self.root = root
        self.config = config
        self.cleaned_subs = cleaned_subs
        self._start_hidden = bool(start_hidden)

        self.sub_window: tk.Toplevel = None 
        self.subtitle_canvas: tk.Canvas = None
        self.subtitle_handle = None
        self._handle_width = 80
        self.max_w, self.max_h = overlay_geometry
        self.center_x = self.config.get("LAST_SUB_CENTER_X")
        self.center_y = self.config.get("LAST_SUB_CENTER_Y")
        self.on_sub_window_enter = lambda _ev=None: None
        self.on_sub_window_leave = lambda _ev=None: None
        self.on_handle_enter = lambda _ev=None: None

        self.build_overlay()

    def build_overlay(self) -> None:
        self.sub_window = tk.Toplevel(self.root)
        self.sub_window.overrideredirect(True)
        self.sub_window.attributes("-topmost", True)
        self.sub_window.attributes("-transparentcolor", "grey")

        # Clamp overlay width to the visible desktop to avoid off-screen windows.
        sw = self.root.winfo_vrootwidth()
        margin = 20  # keep at least 20px visible margin on left+right
        max_w_allowed = max(100, int(sw) - margin * 2)
        self.max_w = max(100, min(int(self.max_w), max_w_allowed))
        self.max_h = max(80, int(self.max_h))

        x = int(self.center_x - self.max_w / 2)
        y = int(self.center_y - self.max_h / 2)
        sw, sh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        x = max(margin, min(x, sw - margin - self.max_w))
        y = max(0, min(y, sh - self.max_h))
        self.sub_window.geometry(f"{self.max_w}x{self.max_h}+{x}+{y}")
        self.sub_window.update_idletasks()

        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(
            self.border_frame,
            bg="grey",
            highlightthickness=0,
            width=self.max_w,
            height=self.max_h
        )
        self.subtitle_canvas.pack(fill="both", expand=True)
        if self.config.get("PHONEMODE_DEFAULT"):
            self.show_handle()
        else:
            self.hide_handle()
        
        self._bind_subtitle_drag()

        self.sub_window.bind("<Enter>", lambda ev: self.on_sub_window_enter(ev))
        self.sub_window.bind("<Leave>", lambda ev: self.on_sub_window_leave(ev))

        if self._start_hidden:
            try:
                self.sub_window.withdraw()
            except Exception:
                pass
            try:
                if self.subtitle_handle:
                    self.subtitle_handle.withdraw()
            except Exception:
                pass

    # Subtitle overlay
    def bind_sub_window_enter(self, cb): self.on_sub_window_enter = cb
    def bind_sub_window_leave(self, cb): self.on_sub_window_leave = cb
    def bind_sub_handel_enter(self, cb): self.on_handle_enter = cb

    def update_geometry(self, new_w, new_h):
        """Resize overlay window and internal canvas to the new width/height (integers)."""
        sw = self.root.winfo_vrootwidth()
        margin = 20  # keep at least 20px visible margin on left+right
        max_w_allowed = max(100, int(sw) - margin * 2)

        self.max_w = max(100, min(int(new_w), max_w_allowed))
        self.max_h = max(80, int(new_h))

        # Recenter around stored center_x/center_y
        x = int(self.center_x - self.max_w / 2)
        y = int(self.center_y - self.max_h / 2)

        # Clamp to screen
        sw = self.root.winfo_vrootwidth()
        sh = self.root.winfo_vrootheight()
        x = max(margin, min(x, sw - margin - self.max_w))
        y = max(0, min(y, sh - self.max_h))

        # Apply geometry
        self.sub_window.geometry(f"{self.max_w}x{self.max_h}+{x}+{y}")
        self._sync_handle_to_subtitle()
        # Resize canvas to match coordinate system the renderer expects
        self.subtitle_canvas.config(width=self.max_w, height=self.max_h)
        self.subtitle_canvas.update_idletasks()

    def _sync_handle_to_subtitle(self):
        if not self.subtitle_handle:
            return
        try:
            if not self.subtitle_handle.winfo_exists():
                return
            self.sub_window.update_idletasks()
            sub_x = self.sub_window.winfo_x()
            sub_y = self.sub_window.winfo_y()
            drag_w = int(self._handle_width)
            drag_h = self.sub_window.winfo_height()
            self.subtitle_handle.geometry(f"{drag_w}x{drag_h}+{sub_x}+{sub_y}")
        except Exception:
            pass

    def _bind_subtitle_drag(self):
        sync_windows = None
        try:
            if (
                self.subtitle_handle
                and self.subtitle_handle.winfo_exists()
                and str(self.subtitle_handle.state()) != "withdrawn"
            ):
                sync_windows = [self.subtitle_handle]
        except Exception:
            sync_windows = None
        make_draggable(
            self.sub_window,
            self.sub_window,
            sync_windows=sync_windows,
            on_release=self._save_center_position,
        )

    def show_handle(self):
        if self.subtitle_handle:
            try:
                if self.subtitle_handle.winfo_exists():
                    self._sync_handle_to_subtitle()
                    self.subtitle_handle.attributes("-alpha", 0.05)
                    show_window_no_activate(self.subtitle_handle)
                    self._bind_subtitle_drag()
                    return
            except Exception:
                self.subtitle_handle = None

        self.subtitle_handle = tk.Toplevel(self.root)
        self.subtitle_handle.withdraw()
        self.subtitle_handle.overrideredirect(True)
        self.subtitle_handle.attributes("-topmost", True)
        make_nonactivating_tool_window(self.subtitle_handle)
        self._sync_handle_to_subtitle()
        self.subtitle_handle.attributes("-alpha", 0.05)

        self.subtitle_handle.bind("<Enter>", lambda ev: self.on_handle_enter(ev))
        make_draggable(self.subtitle_handle, self.sub_window,
                       sync_windows=[self.subtitle_handle],
                       on_release=self._save_center_position)
        make_draggable(self.sub_window, self.sub_window,
                       sync_windows=[self.subtitle_handle], 
                       on_release=self._save_center_position)
        if self._start_hidden:
            try:
                self.subtitle_handle.withdraw()
            except Exception:
                pass
        else:
            show_window_no_activate(self.subtitle_handle)
        self._bind_subtitle_drag()

    def hide_handle(self):
        if self.subtitle_handle:
            try:
                self.subtitle_handle.withdraw()
            except Exception:
                try:
                    self.subtitle_handle.attributes("-alpha", 0.0)
                except Exception:
                    pass
        self._bind_subtitle_drag()

    def _save_center_position(self, x, y, w, h):
        self.center_x = x + w / 2
        self.center_y = y + h / 2
        
    def save_state(self):
        if (self.center_x, self.center_y) != (self.config.get("LAST_SUB_CENTER_X"), self.config.get("LAST_SUB_CENTER_Y")):
            self.config.set("LAST_SUB_CENTER_X", self.center_x)
            self.config.set("LAST_SUB_CENTER_Y", self.center_y)

    def show(self) -> None:
        """Show overlay (and handle if enabled). Used after startup splash."""
        self._start_hidden = False
        try:
            self.sub_window.deiconify()
            self.sub_window.lift()
            self.sub_window.attributes("-topmost", True)
        except Exception:
            pass
        try:
            if self.config.get("PHONEMODE_DEFAULT"):
                self.show_handle()
        except Exception:
            pass
# app.py
import tkinter as tk
import logging
import threading

from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from view.popup import CopyPopup
from view.overlays import LoadingOverlay, set_startup_overlay

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer

from controller.controller import SubtitleController

# from video_sync_server import start_server, get_video_time

logging.basicConfig(
    level=logging.INFO,
    format="%(name)s: %(message)s",
)
logger = logging.getLogger("SubtitlePlayer.App")


class SubtitlePlayerApp:
    def __init__(self):
        self.root = None
        self.config = None

        self._startup_done = threading.Event()
        self._startup_error = self._startup_result = self._startup_thread = self._startup_overlay = None

        self.sub_manager = None
        self.renderer = None
        self.controller = None
        self.settings_ui = None
        self.sub_overlay_ui = None
        self.popup = None
        self.total_duration = None

    def run(self):
        logger.info("Starting SubtitlePlayerApp")

        self._load_config()
        self._build_root()
        self._show_startup_overlay()
        self._start_startup_worker()

        self.root.after(50, self._check_startup_worker)
        self.root.mainloop()

    def _load_config(self):
        try:
            self.config = ConfigManager("config.json")
        except Exception:
            logger.exception("Failed to load config.json")
            raise SystemExit(1)
    def _build_root(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("SubtitlePlayer")
        self.root.geometry("280x115")

        self._restore_window_position()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _show_startup_overlay(self):
        self._startup_overlay = LoadingOverlay(self.root,text="Starting SubtitlePlayer...",modal=False)
        set_startup_overlay(self._startup_overlay)

    def _start_startup_worker(self):
        self._startup_thread = threading.Thread(target=self._startup_worker,daemon=True)
        self._startup_thread.start()

    def _startup_worker(self):
        try:
            sub_manager = SubtitleManager(self.config)
            total_duration = sub_manager.get_total_duration()
            self._startup_result = (sub_manager, total_duration)
        except Exception as exc:
            self._startup_error = exc
        finally:
            self._startup_done.set()

    def _check_startup_worker(self):
        if not self._startup_done.is_set():
            self.root.after(50, self._check_startup_worker)
            return
        self.root.after(0, self._finish_startup)

    def _finish_startup(self):
        if self._startup_error or not self._startup_result:
            self._close_startup_overlay()
            logger.exception("Startup failed", exc_info=self._startup_error)
            self.root.destroy()
            return

        self.sub_manager, self.total_duration = self._startup_result
        self.root.after(0, self._finish_startup_ui)

    def _finish_startup_ui(self):
        self._build_ui()
        self._build_renderer()
        self._build_controller()

        self._update_title()
        self.root.deiconify()
        self._close_startup_overlay()
        self.sub_overlay_ui.show()
        self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

    def _close_startup_overlay(self):
        if self._startup_overlay:
            try:
                self._startup_overlay.close()
            except Exception as e:
                print("ERROR:", e)
                pass
            self._startup_overlay = None
            set_startup_overlay(None)

    # =========================
    # Build components
    # =========================
    def _build_ui(self):
        self.popup = CopyPopup(root=self.root, config=self.config)

        overlay_geometry = self.sub_manager.calculate_geometry_for_longest_lines(5)
        cleaned_subs = [item[0] for item in self.sub_manager.display_data]

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=self.config,
            cleaned_subs=cleaned_subs,
            overlay_geometry=overlay_geometry,
            start_hidden=True,
        )

        self.settings_ui = SettingsUI(
            root=self.root,
            config=self.config,
            total_duration=self.total_duration,
            initial_episode=self.sub_manager.get_current_episode(),
        )

    def _build_renderer(self):
        self.renderer = SubtitleRenderer(
            config=self.config,
            canvas=self.sub_overlay_ui.subtitle_canvas,
        )

    def _build_controller(self):
        self.controller = SubtitleController(
            manager=self.sub_manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=self.config,
            total_duration=self.total_duration,
        )

    # =========================
    # Window / UI helpers
    # =========================
    def _update_title(self):
        s = self.sub_manager.get_current_season()
        e = self.sub_manager.get_current_episode()
        n = self.sub_manager.get_anime_name() or "SubtitlePlayer"

        if s is None and e is None:
            title = n
        elif s is None:
            title = f"E{e} {n}"
        else:
            title = f"S{s}E{e} {n}"

        self.root.title(title)

    def _get_screen_size(self):
        sw = int(self.root.winfo_vrootwidth() or 0)
        sh = int(self.root.winfo_vrootheight() or 0)

        if sw <= 1 or sh <= 1:
            sw = int(self.root.winfo_screenwidth() or 1920)
            sh = int(self.root.winfo_screenheight() or 1080)

        return sw, sh

    def _restore_window_position(self):
        x = self.config.get("LAST_SETTINGS_WINDOW_X")
        y = self.config.get("LAST_SETTINGS_WINDOW_Y")
        w = self.config.get("LAST_SETTINGS_WINDOW_WIDTH") or 280
        h = self.config.get("LAST_SETTINGS_WINDOW_HEIGHT") or 115

        sw, sh = self._get_screen_size()

        w = max(120, min(int(w), sw))
        h = max(80, min(int(h), sh))

        if not isinstance(x, int) or not isinstance(y, int):
            x = int((sw - w) / 2)
            y = int((sh - h) / 2)
        else:
            x = max(0, min(x, sw - w))
            y = max(0, min(y, sh - h))

        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _read_geometry(self):
        try:
            geo = self.root.winfo_geometry()
            size, pos = geo.split("+", 1)
            w, h = map(int, size.split("x"))
            x, y = map(int, pos.split("+"))
            return w, h, x, y
        except Exception as e:
            print("ERROR:", e)
            return None

    # =========================
    # Shutdown
    # =========================
    def _on_close(self):
        geom = self._read_geometry()

        if geom:
            w, h, x, y = geom
            self.config.set("LAST_SETTINGS_WINDOW_X", x)
            self.config.set("LAST_SETTINGS_WINDOW_Y", y)
            self.config.set("LAST_SETTINGS_WINDOW_WIDTH", w)
            self.config.set("LAST_SETTINGS_WINDOW_HEIGHT", h)

        for comp in (self.settings_ui, self.sub_overlay_ui, self.sub_manager):
            try:
                comp.save_state()
            except Exception as e:
                print("ERROR:", e)
                pass

        self.root.destroy()"""
Small shared helpers used across the UI/controller.

- Window dragging (make_draggable)
- Parsing and formatting time values
"""

import tkinter as tk


def _get_windows_hwnd(win: tk.Misc):
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(win.winfo_id())
        try:
            root_hwnd = int(user32.GetAncestor(hwnd, 2))  # GA_ROOT
            if root_hwnd:
                hwnd = root_hwnd
        except Exception:
            parent_hwnd = int(user32.GetParent(hwnd))
            if parent_hwnd:
                hwnd = parent_hwnd
        return hwnd
    except Exception:
        return None


def make_nonactivating_tool_window(win: tk.Toplevel, topmost: bool = True) -> bool:
    """
    Mark a passive utility window so clicking/showing it does not steal foreground focus
    on Windows. This helps keep fullscreen video from revealing the taskbar.
    """
    try:
        import ctypes
        import sys

        if not sys.platform.startswith("win"):
            return False
        try:
            win.update_idletasks()
        except Exception:
            pass
        hwnd = _get_windows_hwnd(win)
        if not hwnd:
            return False

        user32 = ctypes.windll.user32
        gwl_exstyle = -20
        ws_ex_toolwindow = 0x00000080
        ws_ex_appwindow = 0x00040000
        ws_ex_noactivate = 0x08000000
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        hwnd_topmost = -1
        hwnd_notopmost = -2

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_style(hwnd, gwl_exstyle))
        style |= ws_ex_toolwindow | ws_ex_noactivate
        style &= ~ws_ex_appwindow
        set_style(hwnd, gwl_exstyle, style)
        user32.SetWindowPos(
            hwnd,
            hwnd_topmost if topmost else hwnd_notopmost,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_noactivate | swp_framechanged,
        )
        return True
    except Exception:
        return False


def make_nonactivating_window(win: tk.Toplevel, topmost: bool = True) -> bool:
    """
    Mark a normal window as no-activate while keeping its regular window chrome.
    Unlike make_nonactivating_tool_window, this preserves normal minimize behavior.
    """
    try:
        import ctypes
        import sys

        if not sys.platform.startswith("win"):
            return False
        try:
            win.update_idletasks()
        except Exception:
            pass
        hwnd = _get_windows_hwnd(win)
        if not hwnd:
            return False

        user32 = ctypes.windll.user32
        gwl_exstyle = -20
        ws_ex_noactivate = 0x08000000
        swp_nosize = 0x0001
        swp_nomove = 0x0002
        swp_noactivate = 0x0010
        swp_framechanged = 0x0020
        hwnd_topmost = -1
        hwnd_notopmost = -2

        get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        style = int(get_style(hwnd, gwl_exstyle))
        style |= ws_ex_noactivate
        set_style(hwnd, gwl_exstyle, style)
        user32.SetWindowPos(
            hwnd,
            hwnd_topmost if topmost else hwnd_notopmost,
            0,
            0,
            0,
            0,
            swp_nomove | swp_nosize | swp_noactivate | swp_framechanged,
        )
        return True
    except Exception:
        return False


def show_window_no_activate_minimizable(win: tk.Toplevel, topmost: bool = True) -> None:
    """Show a normal minimizable window without asking Windows to foreground this process."""
    if make_nonactivating_window(win, topmost=topmost):
        try:
            import ctypes

            try:
                win.deiconify()
            except Exception:
                pass
            hwnd = _get_windows_hwnd(win)
            if hwnd:
                user32 = ctypes.windll.user32
                hwnd_topmost = -1
                hwnd_top = 0
                swp_nosize = 0x0001
                swp_nomove = 0x0002
                swp_noactivate = 0x0010
                swp_showwindow = 0x0040
                user32.SetWindowPos(
                    hwnd,
                    hwnd_topmost if topmost else hwnd_top,
                    0,
                    0,
                    0,
                    0,
                    swp_nomove | swp_nosize | swp_noactivate | swp_showwindow,
                )
                return
        except Exception:
            pass
    try:
        win.deiconify()
        if topmost:
            win.attributes("-topmost", True)
        win.lift()
    except Exception:
        pass


def show_window_no_activate(win: tk.Toplevel, topmost: bool = True) -> None:
    """Show a passive window without asking Windows to foreground this process."""
    if make_nonactivating_tool_window(win, topmost=topmost):
        try:
            import ctypes

            try:
                win.deiconify()
            except Exception:
                pass
            hwnd = _get_windows_hwnd(win)
            if hwnd:
                user32 = ctypes.windll.user32
                hwnd_topmost = -1
                hwnd_top = 0
                swp_nosize = 0x0001
                swp_nomove = 0x0002
                swp_noactivate = 0x0010
                swp_showwindow = 0x0040
                user32.SetWindowPos(
                    hwnd,
                    hwnd_topmost if topmost else hwnd_top,
                    0,
                    0,
                    0,
                    0,
                    swp_nomove | swp_nosize | swp_noactivate | swp_showwindow,
                )
                return
        except Exception:
            pass
    try:
        win.deiconify()
        if topmost:
            win.attributes("-topmost", True)
        win.lift()
    except Exception:
        pass

def get_monitor_rects(root: tk.Tk | None = None):
    """
    Return a list of monitor rectangles as (x, y, w, h).
    On Windows, uses EnumDisplayMonitors; otherwise falls back to the primary screen.
    """
    try:
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG)]

        monitors = []

        def _callback(hMonitor, hdc, lprcMonitor, dwData):
            r = lprcMonitor.contents
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
            monitors.append((int(r.left), int(r.top), w, h))
            return 1

        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                           ctypes.POINTER(RECT), wintypes.LPARAM)
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, callback_type(_callback), 0)
        if monitors:
            monitors.sort(key=lambda r: (r[0], r[1]))
            return monitors
    except Exception:
        pass

    # Fallback: use primary screen size
    try:
        if root is not None:
            sw = int(root.winfo_vrootwidth() or root.winfo_screenwidth())
            sh = int(root.winfo_vrootheight() or root.winfo_screenheight())
        else:
            sw, sh = 1920, 1080
    except Exception:
        sw, sh = 1920, 1080
    return [(0, 0, int(sw), int(sh))]

def make_draggable(drag_handle: tk.Widget,target: tk.Toplevel,sync_windows: list[tk.Toplevel] = None, on_release=None):

    drag_state = {}

    def start_drag(event):
        drag_state['start_x'] = event.x_root
        drag_state['start_y'] = event.y_root

    def do_drag(event):
        dx = event.x_root - drag_state.get('start_x', event.x_root)
        dy = event.y_root - drag_state.get('start_y', event.y_root)
        new_x = target.winfo_x() + dx
        new_y = target.winfo_y() + dy
        try:
            target.geometry(f"+{new_x}+{new_y}")
        except tk.TclError:
            return
        if sync_windows:
            for win in sync_windows:
                if win.winfo_exists():
                    try:
                        win.geometry(f"+{new_x}+{new_y}")
                    except tk.TclError:
                        pass

        drag_state['start_x'] = event.x_root
        drag_state['start_y'] = event.y_root

    def end_drag(event):
        if on_release:
            on_release(target.winfo_x(), target.winfo_y(),
                       target.winfo_width(), target.winfo_height())


    drag_handle.bind("<ButtonPress-1>", start_drag)
    drag_handle.bind("<B1-Motion>", do_drag)
    drag_handle.bind("<ButtonRelease-1>", end_drag)


def parse_time_value(time: str, last_subtitle = None) -> float:
    text = str(time or "").strip().lower()
    text = text.replace(" ", "").replace("s", "").replace(",", ".")
    if not text:
        return 0.0

    def _digits(s: str) -> str:
        return "".join(ch for ch in s if ch.isdigit())

    if ":" in text:
        parts = [p or "0" for p in text.split(":")]
        if len(parts) > 3:
            parts = parts[-3:]
        while len(parts) < 3:
            parts.insert(0, "0")
        h_s, m_s, s_s = parts
        h = int(_digits(h_s) or 0)
        m = int(_digits(m_s) or 0)
        if "." in s_s:
            sec_int, frac = s_s.split(".", 1)
        else:
            sec_int, frac = s_s, ""
        sec = int(_digits(sec_int) or 0)
        frac_digits = _digits(frac)
        frac_secs = float("0." + frac_digits) if frac_digits else 0.0
    else:
        if "." in text:
            int_part, frac = text.split(".", 1)
        else:
            int_part, frac = text, ""
        int_digits = _digits(int_part)
        if not int_digits:
            return 0.0
        if len(int_digits) <= 4:
            padded = int_digits.zfill(4)
            h = 0
            m = int(padded[:-2])
            sec = int(padded[-2:])
        else:
            h = int(int_digits[:-4] or 0)
            m = int(int_digits[-4:-2])
            sec = int(int_digits[-2:])
        frac_digits = _digits(frac)
        frac_secs = float("0." + frac_digits) if frac_digits else 0.0

    m += sec // 60
    sec %= 60
    h += m // 60
    m %= 60

    return h * 3600 + m * 60 + sec + frac_secs
   
def format_time(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{m:02d}:{s:02d}"
    else:
        return f"{m:02d}:{s:02d}"

"""
Minimal entry point for launching the SubtitlePlayer app.
#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Restricted
# keep this for later information: to download subtitles uses mirror on github of:
# https://kitsunekko.net/dirlist.php?dir=subtitles/japanese/One_Piece/&sort=date&order=asc
"""

from app import SubtitlePlayerApp

if __name__ == "__main__":
    app = SubtitlePlayerApp()
    app.run()
