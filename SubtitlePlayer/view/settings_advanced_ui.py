"""Advanced settings window helpers for SettingsUI."""

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any
import traceback

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
            offset_var.set(self._format_number(float(self._last_offset_value)))
        skip_var = vars_map.get("DEFAULT_SKIP")
        if skip_var is not None:
            skip_var.set(self._format_number(float(self._last_skip_value)))

    def _flush_pending_entry_changes(self):
        """Force any pending changes in offset/skip entry fields to be saved to config."""
        for entry, attr_name, apply_method in [
            (self.offset_entry, "_last_offset_value", self._apply_offset_change),
            (self.skip_entry, "_last_skip_value", self._apply_skip_change),
        ]:
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
        win.grab_release()
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
                win.after_cancel(self._advanced_resize_job)
                self._advanced_resize_job = None
            if self._ocr_region_count_refresh_job is not None:
                win.after_cancel(self._ocr_region_count_refresh_job)
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
            except Exception as e:
                print("keep main", e)
                self._root_topmost_before_advanced = False
        self.root.attributes("-topmost", True)

    def _restore_main_settings_topmost_after_advanced(self) -> None:
        previous = self._root_topmost_before_advanced
        self._root_topmost_before_advanced = None
        if previous is None:
            return
        self.root.attributes("-topmost", bool(previous))

    def _restore_advanced_window_geometry(self, win):
        try:
            self.root.update_idletasks()
            sw = int(self.root.winfo_vrootwidth() or self.root.winfo_screenwidth())
            sh = int(self.root.winfo_vrootheight() or self.root.winfo_screenheight())
        except Exception:#
            print("Advanced window geometry invalid")
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
        except Exception as e:
            print("save geom", e)
            try:
                x = int(win.winfo_x())
                y = int(win.winfo_y())
                w = int(win.winfo_width())
                h = int(win.winfo_height())
            except Exception as e:
                print("save geom", e)
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
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is not None and notebook.winfo_exists():
            tab_id = notebook.select()
            if tab_id:
                self._prepare_advanced_tab_size(tab_id)
        if self._advanced_resize_job is not None:
            win.after_cancel(self._advanced_resize_job)
        self._advanced_resize_job = win.after(1, self._fit_advanced_window_to_selected_tab)
        win.after(0, self._reset_advanced_tab_focus)

    def _on_advanced_apply_now_key(self, _event=None):
        self._apply_advanced_settings(persist=True)
        return "break"

    def _clear_advanced_entry_selection(self, parent):
        try:
            children = parent.winfo_children()
        except Exception as e:
            print("clear entry sel", e)
            return
        for child in children:
            if isinstance(child, (tk.Entry, ttk.Entry, ttk.Combobox)):
                child.selection_clear()
            self._clear_advanced_entry_selection(child)

    def _reset_advanced_tab_focus(self):
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        if not notebook.winfo_exists():
            return
        tab_id = notebook.select()
        if tab_id:
            tab_widget = notebook.nametowidget(tab_id)
            self._clear_advanced_entry_selection(tab_widget)
        notebook.focus_set()

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
            except Exception as e:
                print(e, "Invalid window size")
                sw, sh = 1920, 1080
            req_w = max(360, min(int(req_w), max(360, sw - 20)))
            req_h = max(220, min(int(req_h), max(220, sh - 40)))
            x = max(0, min(int(win.winfo_x()), max(0, sw - req_w)))
            y = max(0, min(int(win.winfo_y()), max(0, sh - req_h)))
            if int(win.winfo_width()) != int(req_w) or int(win.winfo_height()) != int(req_h):
                win.geometry(f"{req_w}x{req_h}+{x}+{y}")
        except Exception as e:
            print("fit window tab", e)
            pass

    def _prepare_advanced_tab_sizes(self):
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        try:
            tab_id = notebook.select()
        except Exception as e:
            print("tab sizes", e)
            return
        if tab_id:
            self._prepare_advanced_tab_size(tab_id)

    def _prepare_advanced_tab_size(self, tab_id: str):
        win = self.advanced_window
        notebook = getattr(self, "_advanced_notebook", None)
        if win is None or notebook is None:
            return
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
        var.trace_add("write", self._on_ocr_region_count_changed)

    def _on_ocr_region_count_changed(self, *_args) -> None:
        root = getattr(self, "root", None)
        if root is None:
            return
        if self._ocr_region_count_refresh_job is not None:
            root.after_cancel(self._ocr_region_count_refresh_job)
        try:
            self._ocr_region_count_refresh_job = root.after(80, self._refresh_ocr_count_runtime)
        except Exception:
            traceback.print_exc()
            self._refresh_ocr_count_runtime()

    def _refresh_ocr_count_runtime(self) -> None:
        self._ocr_region_count_refresh_job = None
        self._refresh_ocr_area_buttons()
        self._apply_ocr_values_runtime()

    def _advanced_general_columns(self):
        left = [
            (
                "Playback / Overlay",
                [
                    {"key": "UPDATE_INTERVAL_MS", "label": "Update interval (ms)", "type": "int", "default": 100, "min": 15, "max": 5000},
                    {"key": "SUBTITLE_TIMEOUT_MS", "label": "Subtitle timeout (ms)", "type": "int", "default": 7000, "min": 100, "max": 120000},
                    {"key": "WINDOWS_HIDE_DELAY_MS", "label": "Control hide delay desktop (ms)", "type": "int", "default": 7000, "min": 100, "max": 120000},
                    {"key": "PHONEMODE_WINDOWS_HIDE_DELAY_MS", "label": "Control hide delay phone (ms)", "type": "int", "default": 6000, "min": 100, "max": 120000, "button_text": "Phone"},
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
                "Kanji / Ruby",
                [
                    {"key": "SUBTITLE_AUTO_RUBY", "label": "Auto-add ruby for kanji-only lines", "type": "bool", "default": False},
                    {"key": "SUBTITLE_HOVER_RUBY", "label": "Show ruby only on kanji hover", "type": "bool", "default": False},
                    {"key": "SHIFT_HOVER_KANJI_DICTIONARY", "label": "Shift-hover word definition window", "type": "bool", "default": False},
                    {"key": "ANKI_SPLIT_KANJI_MORAS", "label": "Split kanji ruby by mora", "type": "bool", "default": False},
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
            (
                "Subtitle Cleaning",
                [
                    {"key": "SUBTITLE_SPEAKER_MODE", "label": "Speaker mode: hide, anime, template", "type": "str", "default": "hide"},
                    {"key": "SUBTITLE_SPEAKER_TEMPLATE", "label": "Speaker template ({name})", "type": "str", "default": "{name}: ", "preserve_whitespace": True},
                    {"key": "SUBTITLE_STRIP_PAREN_NOTES", "label": "Remove sound/action notes in (...) / （...）", "type": "bool", "default": False},
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
            (
                "Language",
                [
                    {"key": "ANKI_WORD_TARGET_LANG", "label": "Word target language", "type": "str", "default": "de"},
                    {"key": "ANKI_SENTENCE_TARGET_LANG", "label": "Sentence target language", "type": "str", "default": "de"},
                ],
            ),
        ]
        right = [
            (
                "Audio Clip Timing",
                [
                    {"key": "AUDIO_PADDING", "label": "Subtitle-end audio padding (ms)", "type": "float", "default": 100},
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
        except Exception as e:
            print("anki defaults", e)
            self._anki_check_defaults = None

    def _set_anki_check_button_state(self, connected):
        btn = getattr(self, "_anki_check_btn", None)
        if btn is None:
            return
        if connected is True:
            btn.configure(bg="#2f8f4e", fg="white", activebackground="#2f8f4e", activeforeground="white")
            return
        if connected is False:
            btn.configure(bg="#b33939", fg="white", activebackground="#b33939", activeforeground="white")
            return
        defaults = getattr(self, "_anki_check_defaults", None)
        if not defaults:
            return
        btn.configure(
            bg=defaults.get("bg"),
            fg=defaults.get("fg"),
            activebackground=defaults.get("activebackground"),
            activeforeground=defaults.get("activeforeground"),
        )

    def _handle_anki_check(self) -> None:
        btn = getattr(self, "_anki_check_btn", None)
        if btn is not None:
            btn.configure(state=tk.DISABLED, text="Checking...")

        def worker():
            connected = False
            try:
                connected = bool(self._on_anki_check())
            except Exception as e:
                print("anki worker connection", e)
                connected = False

            def _finish():
                target = getattr(self, "_anki_check_btn", None)
                if target is None or not target.winfo_exists():
                    return
                target.configure(state=tk.NORMAL, text="Check Connection")
                self._set_anki_check_button_state(connected)
                if hasattr(self, "_advanced_status_var"):
                    self._advanced_status_var.set(
                        "AnkiConnect reachable." if connected else "AnkiConnect not reachable."
                    )
            self.root.after(0, _finish)

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
                    {"key": "SHORTCUT_TOGGLE_SUBTITLES", "label": "Show/Hide subtitles", "type": "str", "default": "s"},
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
                    {"key": "DISABLE_HOTKEY_TOGGLE_SUBTITLES", "label": "Disable subtitle-toggle hotkeys", "type": "bool", "default": False},
                ],
            ),
            (
                "Popup Translation",
                [
                    {"key": "SHORTCUT_POPUP_DEEPL_TRANSLATE", "label": "DeepL selection translation", "type": "str", "default": "t"},
                    {"key": "SHORTCUT_POPUP_GOOGLE_TRANSLATE", "label": "Google selection translation", "type": "str", "default": "g"},
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
    
    # @staticmethod
    # def _coerce_int(value, default: int = 0, min_v=None, max_v=None) -> int:
    #     try:
    #         num = int(float(str(value).strip().replace(",", ".")))
    #     except Exception as e:
    #         print("coerce int", e)
    #         num = int(default)
    #     if min_v is not None and num < int(min_v):
    #         num = int(min_v)
    #     if max_v is not None and num > int(max_v):
    #         num = int(max_v)
    #     return int(num)

    @staticmethod
    def _coerce_int(value, default: int = 0, min_v=None, max_v=None) -> int:
        text = str(value).strip().replace(",", ".")
        if not text:
            num = int(default)
        else:
            try:
                num = int(float(text))
            except Exception:
                print("coerce_int failed:", repr(value))
                traceback.print_stack(limit=6)
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
        self._ocr_area_select_var.trace_add("write", self._on_ocr_area_selection_changed)
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
        values = self._get_ocr_values_from_vars()
        self._ocr_selected_screen = self._get_ocr_screen_for_region(
            values,
            self._get_selected_ocr_area_index(),
        )
        self._update_ocr_screen_button_styles()

    def _get_ocr_region_count_from_vars(self) -> int:
        default = int(self.config.get("OCR_REGION_COUNT") or 2)
        var = getattr(self, "_advanced_vars", {}).get("OCR_REGION_COUNT")
        raw = var.get() if var is not None else default
        return self._coerce_int(raw, default=default, min_v=1, max_v=self.OCR_MAX_REGIONS)

    def _get_selected_ocr_area_index(self) -> int:
        count = self._get_ocr_region_count_from_vars()
        var = getattr(self, "_ocr_area_select_var", None)
        raw = var.get() if var is not None else "1"
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
            menu = menu_widget["menu"]
            if menu is None:
                return
            menu.delete(0, "end")
            for opt in options:
                menu.add_command(label=opt, command=lambda v=opt, vv=var: vv.set(v))

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
        except Exception as e:
            print("rememver ocr buttons", e)
            self._ocr_button_defaults = None

    def _apply_ocr_button_style(self, btn: tk.Button, active: bool) -> None:
        if btn is None:
            return
        if active:
            btn.configure(bg="#2f8f4e", fg="white", activebackground="#2f8f4e", activeforeground="white")
            return
        defaults = getattr(self, "_ocr_button_defaults", None)
        if not defaults:
            return
        btn.configure(
            bg=defaults.get("bg"),
            fg=defaults.get("fg"),
            activebackground=defaults.get("activebackground"),
            activeforeground=defaults.get("activeforeground"),
        )

    def _update_ocr_screen_button_styles(self) -> None:
        values = {}
        try:
            values = self._get_ocr_values_from_vars()
        except Exception as e:
            print("ocs screen button", e)
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
                except Exception as e:
                    print("ocr values", e)
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
        except Exception as e:
            print(e)
            return
        if not values:
            return
        self._on_advanced_apply(dict(values), False)
        self._refresh_ocr_area_buttons()
        self._update_ocr_screen_button_styles()

    def _handle_ocr_read_now(self):
        values = self._get_ocr_values_from_vars()
        self._on_ocr_read_now(dict(values))

    def _handle_ocr_sync_now(self):
        values = self._get_ocr_values_from_vars()
        self._on_ocr_sync_now(dict(values))

    def _handle_select_ocr_area(self) -> None:
        var = getattr(self, "_ocr_area_select_var", None)
        try:
            index = int(var.get()) if var is not None else 1
        except Exception as e:
            print(e)
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
        win.attributes("-alpha", 0.25)
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
            win.grab_release()
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
                except Exception as e:
                    print(e)
                    val = int(spec.get("default", 0))
                var.set(str(val))
            elif spec["type"] == "float":
                try:
                    val = float(str(cfg_val).replace(",", "."))
                except Exception as e:
                    print(e)
                    val = float(spec.get("default", 0.0))
                var.set(self._format_number(val))
            else:
                if isinstance(cfg_val, list):
                    if bool(spec.get("preserve_whitespace", False)):
                        text = ", ".join(str(v) for v in cfg_val if str(v))
                    else:
                        text = ", ".join(str(v).strip() for v in cfg_val if str(v).strip())
                else:
                    text = str(cfg_val) if bool(spec.get("preserve_whitespace", False)) and cfg_val is not None else (
                        str(cfg_val).strip() if cfg_val is not None else ""
                    )
                allow_empty = bool(spec.get("allow_empty", False))
                if (not text) and ((cfg_val is None) or (not allow_empty)):
                    text = str(spec.get("default", ""))
                var.set(text)
        self._sync_advanced_startup_vars_from_runtime()
        self._sync_input_mode_runtime_flags()
        self._refresh_input_mode_button()
        try:
            values = self._get_ocr_values_from_vars()
            self._ocr_selected_screen = self._get_ocr_screen_for_region(
                values,
                self._get_selected_ocr_area_index(),
            )
        except Exception as e:
            print(e)
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
                text = str(var.get()) if bool(spec.get("preserve_whitespace", False)) else str(var.get()).strip()
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
                except Exception:#
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
                except Exception:#
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
        except Exception as e:
            print(e)
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
                except Exception as e:
                    print(e)
                    var.set("0")
            elif spec["type"] == "float":
                try:
                    var.set(self._format_number(float(default)))
                except Exception as e:
                    print(e)
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
            self.root.bell()
            return

        values = result["values"]
        self._on_advanced_apply(dict(values), bool(persist))

        if persist:
            try:
                if hasattr(self.config, "set_many"):
                    self.config.set_many(values)
                else:
                    for key, value in values.items():
                        self.config.set(key, value)
            except Exception as e:
                print(e)
                for key, value in values.items():
                    self.config.set(key, value)
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Saved and applied.")
        else:
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Applied for current session (not saved).")
