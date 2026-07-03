"""Advanced settings window helpers for SettingsUI."""

import logging
import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from view.annotation_tab import AnnotationTab
from utils import get_monitor_rects, show_normal_window_no_activate

logger = logging.getLogger(__name__)


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

    _GENERAL_FILL_ENTRY_SECTIONS = {
        "Playback / Overlay",
        "Download / Search",
        "Subtitle / Popup Style",
        "Startup Defaults",
    }
    _GENERAL_NO_ENTRY_PAD_SECTIONS = {
        "Subtitle / Popup Style",
        "Startup Defaults",
    }
    _GENERAL_RIGHT_LABEL_ALIGN_SECTIONS = {
        "Subtitle / Popup Style",
        "Startup Defaults",
    }
    _GENERAL_LABEL_WIDTHS = {}
    _GENERAL_DOUBLE_WIDTH_EXCLUDED_KEYS = {
        "SUBTITLE_FONT",
        "SUBTITLE_COLOR",
        "GLOW_COLOR",
        "GLOW_RADIUS",
        "POPUP_FONT",
        "POPUP_FONT_COLOR",
        "POPUP_BG_COLOR",
    }
    _GENERAL_DOUBLE_WIDTH_EXCLUDED_SECTIONS = {"Subtitle Cleaning"}
    _GENERAL_DOUBLE_WIDTH_SECTIONS = {
        "Playback / Overlay",
        "Download / Search",
        "Kanji / Ruby",
        "Subtitle / Popup Style",
        "Startup Defaults",
    }
    _ANKI_DOUBLE_WIDTH_SECTIONS = {
        "Anki Connection",
        "Deck / Model",
        "Tags",
        "Audio Clip Timing",
        "Language",
        "Anki Fields",
    }
    _ANKI_FILL_ENTRY_SECTIONS = {
        "Anki Connection",
        "Deck / Model",
        "Tags",
        "Audio Clip Timing",
        "Language",
        "Anki Fields",
    }
    _OCR_FILL_ENTRY_SECTIONS = {
        "OCR Settings",
    }

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
        
        if self.advanced_window is not None and self.advanced_window.winfo_exists():
            try:
                self._save_advanced_window_position(self.advanced_window)
                self.advanced_window.destroy()
            except Exception:
                logger.debug("Failed to close advanced settings window", exc_info=True)
            return

        win = tk.Toplevel(self.root)
        win.withdraw()
        self.advanced_window = win
        win.title("Advanced Settings")
        try:
            win.transient(self.root)
        except Exception:
            pass
        win.resizable(True, True)
        win.grab_release()

        body = tk.Frame(win, padx=12, pady=12)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(2, weight=1)

        title_label = tk.Label(
            body,
            text="Tune runtime behavior, subtitle style, Anki integration, and shortcuts.",
            font=("Arial", 11, "bold"),
            anchor="w",
            justify="left",
        )
        title_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        self._advanced_vars = {}
        self._advanced_meta = {}
        self._advanced_status_var = tk.StringVar(value="")
        self._advanced_filter_var = tk.StringVar(value="")
        self._advanced_filter_sections = []
        self._advanced_scroll_canvases = {}
        self._advanced_scroll_contents = {}
        self._advanced_tab_min_widths = {}
        self._advanced_tab_original_text = {}
        self._advanced_tab_key_map = {}
        self._advanced_label_align_groups = {}
        self._ocr_region_count_trace_var = None
        self._ocr_region_count_refresh_job = None

        filter_row = tk.Frame(body)
        filter_row.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        filter_row.grid_columnconfigure(1, weight=1)
        tk.Label(filter_row, text="Search").pack(side="left")
        filter_entry = tk.Entry(filter_row, textvariable=self._advanced_filter_var)
        filter_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))
        filter_entry.bind("<Escape>", self._clear_advanced_filter)
        self._advanced_filter_var.trace_add("write", self._on_advanced_filter_changed)

        notebook = ttk.Notebook(body)
        notebook.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        self._advanced_notebook = notebook

        general_tab = tk.Frame(notebook)
        anki_tab = tk.Frame(notebook)
        annotation_tab = tk.Frame(notebook)
        shortcuts_tab = tk.Frame(notebook)
        ocr_tab = tk.Frame(notebook)
        notebook.add(general_tab, text="General")
        notebook.add(anki_tab, text="Anki")
        notebook.add(annotation_tab, text="Annotation")
        notebook.add(shortcuts_tab, text="Shortcuts")
        notebook.add(ocr_tab, text="OCR")
        self._performance_tab = None
        if bool(self.config.get("DEBUGGING") or False):
            self._add_performance_tab()
        self._remember_advanced_tab_labels()
        notebook.bind("<<NotebookTabChanged>>", self._on_advanced_tab_changed, add="+")

        general_content = self._build_advanced_tab(general_tab, self._advanced_general_columns())
        self._advanced_general_min_width = int(getattr(general_content, "_advanced_min_width", 0) or 0)
        self._build_advanced_tab(anki_tab, self._advanced_anki_columns())
        self._annotation_tab_ui = AnnotationTab(self.settings_ui, annotation_tab, str(annotation_tab))
        self._build_advanced_tab(shortcuts_tab, self._advanced_shortcut_columns())
        ocr_content = self._build_advanced_tab(ocr_tab, self._advanced_ocr_columns())

        self._build_general_actions(general_content)
        self._build_ocr_actions(ocr_content)
        self._sync_performance_tab_visibility()

        self._load_advanced_values_into_vars()

        footer = tk.Frame(body)
        footer.grid(row=3, column=0, sticky="ew")
        footer.grid_columnconfigure(0, weight=1)

        status_row = tk.Frame(footer)
        status_row.pack(fill="x", pady=(0, 6))
        tk.Label(
            status_row,
            textvariable=self._advanced_status_var,
            fg="#1a4d1a",
            anchor="w",
            justify="left",
        ).pack(fill="x")

        btn_row = tk.Frame(footer)
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

        self._set_advanced_footer_minsize(win, title_label, filter_row, footer)
        self._restore_advanced_window_position(win)
        self._show_advanced_window(win)
        win.bind("<Configure>", self._on_advanced_window_configure, add="+")

        def _on_destroy(_event):
            if _event.widget is not win:
                return
            self._save_advanced_window_position(win)
            job = getattr(self, "_advanced_position_save_job", None)
            if job is not None:
                try:
                    win.after_cancel(job)
                except Exception:
                    pass
                self._advanced_position_save_job = None
            if self._ocr_region_count_refresh_job is not None:
                win.after_cancel(self._ocr_region_count_refresh_job)
                self._ocr_region_count_refresh_job = None
            self.advanced_window = None
            self._advanced_notebook = None
            self._advanced_tab_key_map = {}
            self._advanced_filter_sections = []
            self._advanced_scroll_canvases = {}
            self._advanced_scroll_contents = {}
            self._advanced_tab_min_widths = {}
            self._advanced_tab_original_text = {}
            self._phone_mode_toggle_btn = None
            self._performance_tab = None
            self._performance_text = None
            self._annotation_tab_ui = None
            self._advanced_label_align_groups = {}
            self._ocr_region_count_trace_var = None

        win.bind("<Destroy>", _on_destroy)

    def _show_advanced_window(self, win) -> None:
        try:
            if not show_normal_window_no_activate(win, topmost=True):
                win.deiconify()
                win.attributes("-topmost", True)
                win.lift()
        except Exception:
            logger.debug("Failed to show advanced settings window", exc_info=True)

    def _set_advanced_footer_minsize(self, win, title_label, filter_row, footer) -> None:
        try:
            win.update_idletasks()
            fixed_h = (
                int(title_label.winfo_reqheight())
                + int(filter_row.winfo_reqheight())
                + int(footer.winfo_reqheight())
                + 56
            )
            self._advanced_footer_min_width = int(footer.winfo_reqwidth())
            self._advanced_fixed_window_height = int(fixed_h)
            min_h = max(240, fixed_h + 100)
            self._advanced_footer_min_height = min_h
            self._apply_advanced_selected_tab_width(resize=True)
        except Exception:
            logger.debug("Failed to set advanced settings footer minimum size", exc_info=True)

    def _selected_advanced_tab_min_width(self) -> int:
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return int(getattr(self, "_advanced_general_min_width", 0) or 0)
        try:
            selected = str(notebook.select() or "")
        except Exception:
            selected = ""
        widths = getattr(self, "_advanced_tab_min_widths", {}) or {}
        return int(widths.get(selected) or getattr(self, "_advanced_general_min_width", 0) or 0)

    def _apply_advanced_selected_tab_width(self, resize: bool = False) -> None:
        win = getattr(self, "advanced_window", None)
        if win is None:
            return
        try:
            if not win.winfo_exists():
                return
            content_min_w = self._selected_advanced_tab_min_width()
            min_w = max(int(getattr(self, "_advanced_footer_min_width", 420) or 420), content_min_w + 60)
            max_h = self._advanced_max_window_height()
            min_h = min(int(getattr(self, "_advanced_footer_min_height", 240) or 240), max_h)
            win.minsize(min_w, min_h)
            if resize:
                win.update_idletasks()
                cur_w = max(1, int(win.winfo_width()))
                target_h = self._selected_advanced_tab_window_height(min_h, max_h=max_h)
                if abs(cur_w - min_w) > 2 or abs(int(win.winfo_height() or 0) - target_h) > 2:
                    win.geometry(f"{min_w}x{target_h}+{int(win.winfo_x())}+{int(win.winfo_y())}")
        except Exception:
            logger.debug("Failed to apply advanced tab width", exc_info=True)

    def _selected_advanced_tab_window_height(self, min_h: int, max_h: int | None = None) -> int:
        fixed_h = int(getattr(self, "_advanced_fixed_window_height", 0) or 0)
        content_h = self._selected_advanced_tab_content_height()
        desired_h = max(int(min_h), fixed_h + content_h + 36)
        if max_h is None:
            max_h = self._advanced_max_window_height()
        return max(int(min_h), min(int(desired_h), int(max_h)))

    def _selected_advanced_tab_content_height(self) -> int:
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return 100
        try:
            selected = str(notebook.select() or "")
        except Exception:
            selected = ""
        content = (getattr(self, "_advanced_scroll_contents", {}) or {}).get(selected)
        try:
            if content is not None and content.winfo_exists():
                content.update_idletasks()
                return max(100, int(content.winfo_reqheight()))
        except Exception:
            logger.debug("Failed to measure advanced tab content height", exc_info=True)
        try:
            tab = notebook.nametowidget(selected)
            tab.update_idletasks()
            return max(100, int(tab.winfo_reqheight()))
        except Exception:
            return 100

    def _advanced_max_window_height(self) -> int:
        win = getattr(self, "advanced_window", None)
        try:
            monitors = list(get_monitor_rects(self.root) or [])
        except Exception:
            monitors = []
        if not monitors:
            try:
                return max(240, int(self.root.winfo_screenheight() or 1080) - 200)
            except Exception:
                return 880

        try:
            target = win if win is not None and win.winfo_exists() else self.root
            target.update_idletasks()
            px = int(target.winfo_x() + max(1, target.winfo_width()) // 2)
            py = int(target.winfo_y() + max(1, target.winfo_height()) // 2)
        except Exception:
            px = py = 0

        for mx, my, mw, mh in monitors:
            if mx <= px < mx + mw and my <= py < my + mh:
                return max(240, int(mh) - 200)
        _mx, _my, _mw, mh = monitors[0]
        return max(240, int(mh) - 200)

    def _restore_advanced_window_position(self, win) -> None:
        try:
            x = self.config.get("LAST_ADV_SETTINGS_WINDOW_X")
            y = self.config.get("LAST_ADV_SETTINGS_WINDOW_Y")
            if not isinstance(x, int) or not isinstance(y, int):
                return
            win.update_idletasks()
            width = int(win.winfo_reqwidth())
            height = int(win.winfo_reqheight())
            x, y = self._clamp_advanced_window_position(int(x), int(y), width, height)
            win.geometry(f"+{x}+{y}")
        except Exception:
            logger.debug("Failed to restore advanced settings window position", exc_info=True)

    def _clamp_advanced_window_position(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        try:
            monitors = list(get_monitor_rects(self.root) or [])
        except Exception:
            monitors = []
        if not monitors:
            try:
                monitors = [(0, 0, int(self.root.winfo_screenwidth() or 1920), int(self.root.winfo_screenheight() or 1080))]
            except Exception:
                monitors = [(0, 0, 1920, 1080)]

        for mx, my, mw, mh in monitors:
            if x < mx + mw and x + width > mx and y < my + mh and y + height > my:
                return x, y

        def _distance(rect):
            mx, my, mw, mh = rect
            cx = mx + mw // 2
            cy = my + mh // 2
            wx = x + width // 2
            wy = y + height // 2
            return abs(wx - cx) + abs(wy - cy)

        mx, my, mw, mh = min(monitors, key=_distance)
        return max(mx, min(x, mx + max(0, mw - width))), max(my, min(y, my + max(0, mh - height)))

    def _on_advanced_window_configure(self, event=None) -> None:
        win = getattr(self, "advanced_window", None)
        try:
            if win is None or event is None or event.widget is not win or not win.winfo_exists():
                return
            if str(win.state()) == "withdrawn":
                return
            self._last_advanced_window_position = (int(win.winfo_x()), int(win.winfo_y()))
            job = getattr(self, "_advanced_position_save_job", None)
            if job is not None:
                win.after_cancel(job)

            def _save_later(w=win):
                self._advanced_position_save_job = None
                self._save_advanced_window_position(w)

            self._advanced_position_save_job = win.after(500, _save_later)
        except Exception:
            logger.debug("Failed to track advanced settings window position", exc_info=True)

    def _save_advanced_window_position(self, win=None) -> None:
        try:
            if win is None:
                win = getattr(self, "advanced_window", None)
            if win is not None and win.winfo_exists():
                win.update_idletasks()
                x, y = int(win.winfo_x()), int(win.winfo_y())
            else:
                x, y = getattr(self, "_last_advanced_window_position", (None, None))
            if not isinstance(x, int) or not isinstance(y, int):
                return
            self._last_advanced_window_position = (x, y)
            updates = {}
            if self.config.get("LAST_ADV_SETTINGS_WINDOW_X") != x:
                updates["LAST_ADV_SETTINGS_WINDOW_X"] = x
            if self.config.get("LAST_ADV_SETTINGS_WINDOW_Y") != y:
                updates["LAST_ADV_SETTINGS_WINDOW_Y"] = y
            if updates:
                if hasattr(self.config, "set_many"):
                    self.config.set_many(updates)
                else:
                    for key, value in updates.items():
                        self.config.set(key, value)
        except Exception:
            logger.debug("Failed to save advanced settings window position", exc_info=True)

    def _remember_advanced_tab_labels(self) -> None:
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        labels = getattr(self, "_advanced_tab_original_text", None)
        if not isinstance(labels, dict):
            labels = {}
            self._advanced_tab_original_text = labels
        try:
            for tab_id in notebook.tabs():
                labels.setdefault(str(tab_id), str(notebook.tab(tab_id, "text") or ""))
        except Exception:
            logger.debug("Failed to remember advanced tab labels", exc_info=True)

    def _on_advanced_tab_changed(self, _event=None):
        win = self.advanced_window
        if win is None:
            return
        self._apply_advanced_filter()
        self._apply_advanced_selected_tab_width(resize=True)
        self._refresh_advanced_scroll_regions()

    def _create_scrollable_advanced_tab(self, tab_parent):
        tab_id = str(tab_parent)
        outer = tk.Frame(tab_parent)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, padx=0, pady=8)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _refresh_region(_event=None):
            try:
                min_width = int(getattr(content, "_advanced_min_width", 0) or content.winfo_reqwidth())
                window_width = max(min_width, canvas.winfo_width())
                canvas.itemconfigure(content_id, width=window_width)
                canvas.configure(scrollregion=(0, 0, window_width, max(int(content.winfo_reqheight()), int(canvas.winfo_height()))))
                if int(content.winfo_reqheight()) <= int(canvas.winfo_height()):
                    canvas.yview_moveto(0)
            except Exception:
                logger.debug("Failed to refresh advanced scroll region", exc_info=True)

        def _on_mousewheel(event):
            try:
                delta = int(-1 * (event.delta / 120))
            except Exception:
                delta = 0
            if delta:
                canvas.yview_scroll(delta, "units")
            return "break"

        def _on_button4(_event):
            canvas.yview_scroll(-1, "units")
            return "break"

        def _on_button5(_event):
            canvas.yview_scroll(1, "units")
            return "break"

        content.bind("<Configure>", _refresh_region)
        canvas.bind("<Configure>", _refresh_region)
        canvas.after_idle(lambda: (canvas.yview_moveto(0), _refresh_region()))
        canvas.bind("<Enter>", lambda _event: (
            canvas.bind_all("<MouseWheel>", _on_mousewheel),
            canvas.bind_all("<Button-4>", _on_button4),
            canvas.bind_all("<Button-5>", _on_button5),
        ))
        canvas.bind("<Leave>", lambda _event: (
            canvas.unbind_all("<MouseWheel>"),
            canvas.unbind_all("<Button-4>"),
            canvas.unbind_all("<Button-5>"),
        ))

        self._advanced_scroll_canvases[tab_id] = canvas
        self._advanced_scroll_contents[tab_id] = content
        return content

    def _refresh_advanced_scroll_regions(self) -> None:
        for canvas in list(getattr(self, "_advanced_scroll_canvases", {}).values()):
            try:
                if canvas.winfo_exists():
                    canvas.configure(scrollregion=canvas.bbox("all"))
                    canvas.yview_moveto(0)
            except Exception:
                logger.debug("Failed to update advanced scroll region", exc_info=True)

    def _build_advanced_tab(self, tab_parent, column_sections):
        tab_id = str(tab_parent)
        if not hasattr(self, "_advanced_tab_key_map"):
            self._advanced_tab_key_map = {}
        if tab_id not in self._advanced_tab_key_map:
            self._advanced_tab_key_map[tab_id] = []

        content = self._create_scrollable_advanced_tab(tab_parent)
        columns_frame = tk.Frame(content)
        columns_frame.pack(fill="x", expand=True, anchor="n")
        single_column = len(column_sections) == 1
        for col_idx in range(len(column_sections)):
            columns_frame.grid_columnconfigure(col_idx, weight=1 if single_column else 0)

        for col_idx, sections in enumerate(column_sections):
            col = tk.Frame(columns_frame)
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
        try:
            self._align_advanced_label_groups()
            columns_frame.update_idletasks()
            measured_width = int(columns_frame.winfo_reqwidth())
            content._advanced_min_width = measured_width
            self._advanced_tab_min_widths[str(tab_parent)] = int(content._advanced_min_width)
        except Exception:
            logger.debug("Failed to measure advanced tab minimum width", exc_info=True)
        return content

    def _refresh_advanced_content_min_width(self, content: tk.Frame) -> None:
        try:
            content.update_idletasks()
            min_width = max(int(getattr(content, "_advanced_min_width", 0) or 0), int(content.winfo_reqwidth()))
            content._advanced_min_width = min_width
            for tab_id, registered_content in dict(getattr(self, "_advanced_scroll_contents", {}) or {}).items():
                if registered_content is content:
                    self._advanced_tab_min_widths[str(tab_id)] = min_width
                    break
        except Exception:
            logger.debug("Failed to refresh advanced content minimum width", exc_info=True)

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
        pack_info = {"fill": "x", "pady": (0, 0 if is_last else 10)}
        section.pack(**pack_info)
        section.grid_columnconfigure(1, weight=1)
        filter_info = {
            "parent": parent,
            "tab_id": str(tab_id),
            "section": section,
            "section_text": str(section_name or "").casefold(),
            "pack": pack_info,
            "rows": [],
        }
        self._advanced_filter_sections.append(filter_info)

        row = 0
        for spec in specs:
            _register_var(spec)
            if spec.get("hidden"):
                continue
            key = spec["key"]
            var = self._advanced_vars.get(key)
            if spec["type"] == "bool":
                fill_entry = self._advanced_section_fills_entry(section_name)
                chk_frame = tk.Frame(section)
                chk_frame.grid(row=row, column=0, columnspan=2, sticky=("ew" if fill_entry else "w"), pady=2)
                chk_frame.grid_columnconfigure(0, weight=1 if fill_entry and spec.get("button_text") else 0)
                chk_frame.grid_columnconfigure(1, weight=0)
                
                chk = tk.Checkbutton(chk_frame, text=spec["label"], variable=var, anchor="w")
                chk.grid(row=0, column=0, sticky="w")
                inline_spec = spec.get("inline_entry")
                inline_widgets = []
                inline_text = ""
                next_inline_column = 1
                if isinstance(inline_spec, dict):
                    _register_var(inline_spec)
                    inline_key = inline_spec["key"]
                    inline_var = self._advanced_vars.get(inline_key)
                    inline_entry = tk.Entry(
                        chk_frame,
                        textvariable=inline_var,
                        width=self._advanced_entry_width(section_name, inline_spec),
                    )
                    inline_entry.grid(row=0, column=next_inline_column, sticky="w", padx=(8, 0))
                    inline_widgets.append(inline_entry)
                    inline_text = f" {inline_spec.get('label', '')} {inline_key}"
                    next_inline_column += 1
                
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
                    btn.grid(row=0, column=next_inline_column, sticky=("e" if fill_entry else "w"), padx=(8, 0))
                    if key == "ANKI_ENABLED":
                        self._anki_check_btn = btn
                        self._remember_anki_check_defaults(self._anki_check_btn)
                        self._set_anki_check_button_state(None)
                filter_info["rows"].append({
                    "widgets": [chk_frame, *inline_widgets],
                    "text": f"{section_name} {spec.get('label', '')} {key}{inline_text}".casefold(),
                })
            else:
                label_width = self._advanced_label_width(section_name)
                label_kwargs = {"text": spec["label"], "anchor": "w"}
                if label_width:
                    label_kwargs["width"] = label_width
                label = tk.Label(section, **label_kwargs)
                label.grid(row=row, column=0, sticky="w", pady=2)
                self._register_advanced_label_alignment(section_name, section, label)
                # Create a frame for entry and optional button
                entry_frame = tk.Frame(section)
                fill_entry = self._advanced_section_fills_entry(section_name)
                entry_padx = self._advanced_entry_frame_padx(section_name)
                entry_frame.grid(row=row, column=1, sticky=("ew" if fill_entry else "w"), padx=entry_padx, pady=2)
                entry_frame.grid_columnconfigure(0, weight=1 if fill_entry else 0)
                
                entry_width = self._advanced_entry_width(section_name, spec)
                entry = tk.Entry(entry_frame, textvariable=var, width=entry_width)
                entry.grid(row=0, column=0, sticky=("ew" if fill_entry else "w"))
                
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
                filter_info["rows"].append({
                    "widgets": [label, entry_frame],
                    "text": f"{section_name} {spec.get('label', '')} {key}".casefold(),
                })
            row += 1

    def _advanced_entry_width(self, section_name: str, spec: dict) -> int:
        base = int(spec.get("width") or (8 if spec["type"] in {"int", "float"} else 14))
        key = str(spec.get("key") or "")
        if key == "OCR_CHAR_WHITELIST":
            return base * 2
        if "entry_width" in spec:
            return int(spec["entry_width"])
        layout_width = self._general_section_entry_width(section_name, key)
        if layout_width is not None:
            return layout_width
        if section_name in self._ANKI_DOUBLE_WIDTH_SECTIONS:
            return base * 2
        if (
            section_name in self._GENERAL_DOUBLE_WIDTH_SECTIONS
            and section_name not in self._GENERAL_DOUBLE_WIDTH_EXCLUDED_SECTIONS
            and key not in self._GENERAL_DOUBLE_WIDTH_EXCLUDED_KEYS
        ):
            return base * 2
        return base

    def _advanced_label_width(self, section_name: str) -> int | None:
        width = self._GENERAL_LABEL_WIDTHS.get(section_name)
        return int(width) if width else None

    def _general_section_entry_width(self, section_name: str, key: str) -> int | None:
        if section_name == "Playback / Overlay":
            if key == "PHONEMODE_WINDOWS_HIDE_DELAY_MS":
                return 8
            return 14
        if section_name == "Download / Search":
            return 14
        if section_name in {"Subtitle / Popup Style", "Startup Defaults"}:
            return 14
        return None

    def _advanced_section_fills_entry(self, section_name: str) -> bool:
        return (
            section_name in self._GENERAL_FILL_ENTRY_SECTIONS
            or section_name in self._ANKI_FILL_ENTRY_SECTIONS
            or section_name in self._OCR_FILL_ENTRY_SECTIONS
        )

    def _advanced_entry_frame_padx(self, section_name: str) -> tuple[int, int]:
        if section_name in self._GENERAL_NO_ENTRY_PAD_SECTIONS:
            return (0, 0)
        return (8, 0)

    def _register_advanced_label_alignment(self, section_name: str, section, label) -> None:
        if section_name not in self._GENERAL_RIGHT_LABEL_ALIGN_SECTIONS:
            return
        groups = getattr(self, "_advanced_label_align_groups", None)
        if not isinstance(groups, dict):
            return
        bucket = groups.setdefault("general_right", {"sections": set(), "labels": []})
        bucket["sections"].add(section)
        bucket["labels"].append(label)

    def _align_advanced_label_groups(self) -> None:
        groups = getattr(self, "_advanced_label_align_groups", None)
        if not isinstance(groups, dict):
            return
        for group in groups.values():
            labels = [label for label in group.get("labels", []) if label is not None]
            sections = [section for section in group.get("sections", set()) if section is not None]
            max_width = 0
            for label in labels:
                try:
                    if label.winfo_exists():
                        label.update_idletasks()
                        max_width = max(max_width, int(label.winfo_reqwidth()))
                except Exception:
                    continue
            if max_width <= 0:
                continue
            for section in sections:
                try:
                    if section.winfo_exists():
                        section.grid_columnconfigure(0, minsize=max_width)
                except Exception:
                    continue

    def _on_advanced_filter_changed(self, *_args) -> None:
        self._apply_advanced_filter()

    def _clear_advanced_filter(self, _event=None):
        var = getattr(self, "_advanced_filter_var", None)
        if var is not None:
            var.set("")
        return "break"

    def _advanced_filter_terms(self) -> list[str]:
        var = getattr(self, "_advanced_filter_var", None)
        try:
            text = str(var.get() if var is not None else "")
        except Exception:
            text = ""
        return [term for term in text.casefold().split() if term]

    @staticmethod
    def _advanced_filter_matches(text: str, terms: list[str]) -> bool:
        if not terms:
            return True
        return all(term in text for term in terms)

    def _set_advanced_row_visible(self, row_info: dict, visible: bool) -> None:
        for widget in row_info.get("widgets", []):
            try:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()
            except Exception:
                logger.debug("Failed to update advanced filter row visibility", exc_info=True)

    def _apply_advanced_filter(self) -> None:
        sections = list(getattr(self, "_advanced_filter_sections", []) or [])
        if not sections:
            return
        terms = self._advanced_filter_terms()
        by_parent = {}
        notebook = getattr(self, "_advanced_notebook", None)
        try:
            active_tab = str(notebook.select() or "") if notebook is not None else ""
        except Exception:
            active_tab = ""
        for info in sections:
            by_parent.setdefault(info.get("parent"), []).append(info)

        for infos in by_parent.values():
            for info in infos:
                section = info.get("section")
                try:
                    section.pack_forget()
                except Exception:
                    pass

            for info in infos:
                tab_id = str(info.get("tab_id") or "")
                filter_this_tab = not active_tab or tab_id == active_tab
                section = info.get("section")
                rows = list(info.get("rows", []) or [])
                section_match = filter_this_tab and self._advanced_filter_matches(str(info.get("section_text") or ""), terms)
                visible_rows = 0
                for row_info in rows:
                    row_visible = (not terms) or not filter_this_tab or section_match or self._advanced_filter_matches(
                        str(row_info.get("text") or ""),
                        terms,
                    )
                    self._set_advanced_row_visible(row_info, row_visible)
                    if row_visible:
                        visible_rows += 1

                section_visible = (not terms) or not filter_this_tab or section_match or visible_rows > 0
                if section_visible:
                    try:
                        section.pack(**dict(info.get("pack") or {}))
                    except Exception:
                        logger.debug("Failed to update advanced section visibility", exc_info=True)

        self._update_advanced_filter_tab_labels({}, [])
        self._refresh_advanced_scroll_regions()

    def _update_advanced_filter_tab_labels(self, tab_counts: dict[str, int], terms: list[str]) -> None:
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        self._remember_advanced_tab_labels()
        labels = dict(getattr(self, "_advanced_tab_original_text", {}) or {})
        try:
            tabs = list(notebook.tabs())
        except Exception:
            return

        first_match = ""
        for tab_id in tabs:
            original = labels.get(str(tab_id), str(notebook.tab(tab_id, "text") or ""))
            count = int(tab_counts.get(str(tab_id), 0) or 0)
            text = original
            if terms and count > 0:
                text = f"{original} ({count})"
                if not first_match:
                    first_match = str(tab_id)
            try:
                notebook.tab(tab_id, text=text)
            except Exception:
                logger.debug("Failed to update advanced search tab label", exc_info=True)

        if terms and first_match:
            try:
                selected = str(notebook.select() or "")
                if int(tab_counts.get(selected, 0) or 0) <= 0:
                    notebook.select(first_match)
            except Exception:
                logger.debug("Failed to select first advanced search result tab", exc_info=True)

        if terms and hasattr(self, "_advanced_status_var"):
            summary = []
            for tab_id in tabs:
                count = int(tab_counts.get(str(tab_id), 0) or 0)
                if count > 0:
                    summary.append(f"{labels.get(str(tab_id), 'Tab')} {count}")
            self._advanced_status_var.set("Search matches: " + (", ".join(summary) if summary else "none"))
        elif hasattr(self, "_advanced_status_var"):
            try:
                if str(self._advanced_status_var.get() or "").startswith("Search matches:"):
                    self._advanced_status_var.set("")
            except Exception:
                pass

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
            logger.debug("Failed to schedule OCR count refresh", exc_info=True)
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
                    {"key": "SUBTITLE_HOVER_PAUSE_VIDEO", "label": "Pause background video while subtitle hovered", "type": "bool", "default": False},
                    {"key": "SUBTITLE_CENTER_SNAP_ENABLED", "label": "Snap subtitle window to screen center", "type": "bool", "default": False},
                    {"key": "SUBTITLE_CENTER_SNAP_THRESHOLD_PX", "label": "Center snap distance (px)", "type": "int", "default": 32, "min": 1, "max": 500},
                    {"key": "FAST_FORWARD_SPEED", "label": "Fast-forward speed", "type": "float", "default": 1.5, "min": 1.0, "max": 8.0, "round": 1},
                ],
            ),
            (
                "Download / Search",
                [
                    {"key": "REMOTE_CACHE_CLEANUP_ON_EXIT", "label": "Delete remote cache on exit", "type": "bool", "default": False},
                    {"key": "DOWNLOAD_WINDOW", "label": "Prefetch window (episodes)", "type": "int", "default": 5, "min": 1, "max": 50},
                    {"key": "DOWNLOAD_MAX_WORKERS", "label": "Max parallel downloads", "type": "int", "default": 2, "min": 1, "max": 10},
                    {"key": "DOWNLOAD_PREFETCH_DELAY_MS", "label": "Prefetch delay (ms)", "type": "int", "default": 1000, "min": 0, "max": 600000},
                    {"key": "DOWNLOAD_THROTTLE_MS", "label": "Download throttle (ms)", "type": "int", "default": 0, "min": 0, "max": 60000},
                    {"key": "EPISODE_PRELOAD_RADIUS", "label": "Prepared episode radius", "type": "int", "default": 1, "min": 0, "max": 5},
                    {"key": "EPISODE_PRELOAD_THROTTLE_MS", "label": "Prepared episode throttle (ms)", "type": "int", "default": 1000, "min": 0, "max": 60000},
                    {"key": "SUBTITLE_GEOMETRY_CANDIDATE_LINES", "label": "Geometry candidate lines", "type": "int", "default": 32, "min": 0, "max": 500},
                    {"key": "SEASON_PROVIDER_EARLY_STOP_ENABLED", "label": "Provider early-stop enabled", "type": "bool", "default": False},
                    {"key": "SEASON_PROVIDER_EARLY_STOP_MIN_FOUND_SEASONS", "label": "Early-stop seasons", "type": "int", "default": 1, "min": 1, "max": 20, "entry_width": 6},
                ],
            ),
            (
                "Kanji / Ruby",
                [
                    {"key": "SUBTITLE_AUTO_RUBY", "label": "Auto-add ruby for kanji-only lines", "type": "bool", "default": False},
                    {"key": "SUBTITLE_HOVER_RUBY", "label": "Show ruby only on kanji hover", "type": "bool", "default": False},
                    {"key": "SHIFT_HOVER_KANJI_DICTIONARY", "label": "Enable plain Shift-hover word definition window", "type": "bool", "default": False},
                    {"key": "ANKI_SPLIT_KANJI_MORAS", "label": "Split all-kanji ruby per kanji", "type": "bool", "default": False},
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
                    {"key": "START_EPISODES_AT_DEFAULT_TIME", "label": "Always use default start time", "type": "bool", "default": False},
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
                ],
            ),
            (
                "Tags",
                [
                    {"key": "ANKI_TAGS", "label": "Custom tags", "type": "str", "default": "", "allow_empty": True},
                    {"key": "ANKI_TAG_SURU_VERBS", "label": "Add する-Verb marker tag", "type": "bool", "default": True},
                    {"key": "ANKI_TAG_I_ADJECTIVES", "label": "Add い-Adj marker tag", "type": "bool", "default": True},
                    {"key": "ANKI_TAG_NA_ADJECTIVES", "label": "Add な-Adj marker tag", "type": "bool", "default": True},
                ],
            ),
        ]
        right = [
            (
                "Audio Clip Timing",
                [
                    {"key": "AUDIO_PADDING", "label": "Subtitle-end audio padding (ms)", "type": "float", "default": 100},
                    {"key": "ANKI_AUTO_JUMP_AFTER_ADD", "label": "Automatically jump after Anki card was added", "type": "bool", "default": False},
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
        except Exception as e:
            logger.debug("Failed to remember Anki check button defaults: %s", e, exc_info=True)
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
                logger.debug("Anki connection worker failed: %s", e, exc_info=True)
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
                    {"key": "SHORTCUT_TOGGLE_PLAY", "label": "Play/Pause", "type": "str", "default": "space", "allow_empty": True},
                    {"key": "SHORTCUT_GO_BACK", "label": "Back (seconds)", "type": "str", "default": "left", "allow_empty": True},
                    {"key": "SHORTCUT_GO_FORWARD", "label": "Forward (seconds)", "type": "str", "default": "right", "allow_empty": True},
                    {"key": "SHORTCUT_SUBTITLE_BACK", "label": "Back (subtitle segment)", "type": "str", "default": "shift+left", "allow_empty": True},
                    {"key": "SHORTCUT_SUBTITLE_FORWARD", "label": "Forward (subtitle segment)", "type": "str", "default": "shift+right", "allow_empty": True},
                    {"key": "SHORTCUT_TOGGLE_SUBTITLES", "label": "Show/Hide subtitles", "type": "str", "default": "s", "allow_empty": True},
                ],
            ),
            (
                "Mode 2 (Numpad)",
                [
                    {"key": "SHORTCUT_MODE2_TOGGLE_PLAY", "label": "Play/Pause", "type": "str", "default": "numpad0", "allow_empty": True},
                    {"key": "SHORTCUT_MODE2_GO_BACK", "label": "Back (seconds)", "type": "str", "default": "4", "allow_empty": True},
                    {"key": "SHORTCUT_MODE2_GO_FORWARD", "label": "Forward (seconds)", "type": "str", "default": "6", "allow_empty": True},
                    {"key": "SHORTCUT_MODE2_SUBTITLE_BACK", "label": "Back (subtitle segment)", "type": "str", "default": "alt+4", "allow_empty": True},
                    {"key": "SHORTCUT_MODE2_SUBTITLE_FORWARD", "label": "Forward (subtitle segment)", "type": "str", "default": "alt+6", "allow_empty": True},
                ],
            ),
            (
                "Other Global",
                [
                    {"key": "SHORTCUT_BRING_TO_FRONT", "label": "Bring app to front", "type": "str", "default": "alt+x", "allow_empty": True},
                    {"key": "SHORTCUT_EPISODE_INC", "label": "Episode +", "type": "str", "default": "alt+c", "allow_empty": True},
                    {"key": "SHORTCUT_EPISODE_DEC", "label": "Episode -", "type": "str", "default": "alt+y", "allow_empty": True},
                    {"key": "SHORTCUT_JUMP_SUB_END", "label": "Jump to subtitle end / capture", "type": "str", "default": "ctrl+shift+y", "allow_empty": True},
                    {"key": "SHORTCUT_TOGGLE_FAST_FORWARD", "label": "Toggle fast-forward", "type": "str", "default": "f", "allow_empty": True},
                    {"key": "SHORTCUT_TOGGLE_DEBUGGING", "label": "Toggle debugging", "type": "str", "default": "ctrl+shift+d", "allow_empty": True},
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
                    {"key": "DISABLE_HOTKEY_TOGGLE_FAST_FORWARD", "label": "Disable fast-forward hotkey", "type": "bool", "default": False},
                    {"key": "DISABLE_HOTKEY_TOGGLE_SUBTITLES", "label": "Disable subtitle-toggle hotkeys", "type": "bool", "default": False},
                ],
            ),
            (
                "Popup Translation",
                [
                    {"key": "SHORTCUT_POPUP_DEEPL_TRANSLATE", "label": "DeepL selection translation", "type": "str", "default": "t", "allow_empty": True},
                    {"key": "SHORTCUT_POPUP_GOOGLE_TRANSLATE", "label": "Google selection translation", "type": "str", "default": "g", "allow_empty": True},
                    {"key": "SHORTCUT_POPUP_ADD_ANKI", "label": "Add selected popup text to Anki", "type": "str", "default": "a", "allow_empty": True},
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
                    {"key": "OCR_TESSERACT_CMD", "label": "Tesseract path (exe or folder)", "type": "str", "default": "", "allow_empty": True, "width": 22},
                    {"key": "OCR_TESSERACT_PSM", "label": "Tesseract PSM", "type": "int", "default": 6, "min": 0, "max": 13, "width": 5},
                    {"key": "OCR_TESSERACT_OEM", "label": "Tesseract OEM", "type": "int", "default": 3, "min": 0, "max": 3, "width": 5},
                    {"key": "OCR_CHAR_WHITELIST", "label": "Char whitelist", "type": "str", "default": "0123456789:/", "allow_empty": True, "width": 12},
                    {"key": "OCR_REGION_COUNT", "label": "OCR box count", "type": "int", "default": 2, "min": 1, "max": self.OCR_MAX_REGIONS, "width": 5},
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
                logger.debug("coerce_int failed: %r", value, stack_info=True)
                num = int(default)

        if min_v is not None and num < int(min_v):
            num = int(min_v)
        if max_v is not None and num > int(max_v):
            num = int(max_v)
        return int(num)


    def _build_ocr_actions(self, ocr_tab: tk.Frame) -> None:
        actions = tk.LabelFrame(ocr_tab, text="Actions", padx=10, pady=8)
        actions.pack(fill="x", pady=(0, 8), anchor="n")

        action_grid = tk.Frame(actions)
        action_grid.pack(fill="x", expand=True)
        action_grid.grid_columnconfigure(0, weight=1, uniform="ocr_action_cols")
        action_grid.grid_columnconfigure(1, weight=1, uniform="ocr_action_cols")

        area_cell = tk.Frame(action_grid)
        area_cell.grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 4))
        area_cell.grid_columnconfigure(0, weight=1)
        area_cell.grid_columnconfigure(1, weight=0)

        self._ocr_area_select_btn = tk.Button(area_cell, text="Select OCR Area", command=self._handle_select_ocr_area)
        self._ocr_area_select_btn.grid(row=0, column=0, sticky="ew")
        self._ocr_area_select_var = tk.StringVar(value="1")
        self._ocr_area_select_var.trace_add("write", self._on_ocr_area_selection_changed)
        self._ocr_area_select_menu = tk.OptionMenu(area_cell, self._ocr_area_select_var, "1")
        self._ocr_area_select_menu.grid(row=0, column=1, sticky="e", padx=(4, 0))

        self._refresh_ocr_area_buttons()
        tk.Button(action_grid, text="Read Now (Set Time)", command=self._handle_ocr_read_now).grid(
            row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 4)
        )
        tk.Button(action_grid, text="Sync Now (5s)", command=self._handle_ocr_sync_now).grid(
            row=1, column=0, sticky="ew", padx=(0, 6)
        )
        tk.Button(action_grid, text="Show Boxes", command=self._handle_ocr_show_boxes).grid(
            row=1, column=1, sticky="ew", padx=(6, 0)
        )

        screen_row = tk.Frame(actions)
        screen_row.pack(fill="x", pady=(8, 0))
        tk.Label(screen_row, text="Screen:").pack(side="left")
        self._build_ocr_screen_buttons(screen_row)

        self._update_ocr_screen_button_styles()
        self._refresh_advanced_content_min_width(ocr_tab)

    def _sync_performance_tab_visibility(self) -> None:
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        try:
            if not notebook.winfo_exists():
                return
        except Exception:
            return

        if bool(self.config.get("DEBUGGING") or False):
            self._add_performance_tab()
        else:
            self._remove_performance_tab()

        self._refresh_advanced_scroll_regions()

    def _add_performance_tab(self) -> None:
        notebook = getattr(self, "_advanced_notebook", None)
        if notebook is None:
            return
        existing = getattr(self, "_performance_tab", None)
        try:
            if existing is not None and existing.winfo_exists():
                return
        except Exception:
            pass

        performance_tab = tk.Frame(notebook)
        self._performance_tab = performance_tab
        notebook.add(performance_tab, text="Performance")
        self._remember_advanced_tab_labels()
        self._build_performance_tab(performance_tab)

    def _remove_performance_tab(self) -> None:
        notebook = getattr(self, "_advanced_notebook", None)
        performance_tab = getattr(self, "_performance_tab", None)
        if notebook is None or performance_tab is None:
            self._performance_text = None
            self._performance_tab = None
            return

        tab_id = str(performance_tab)
        try:
            if notebook.select() == tab_id:
                tabs = [tab for tab in notebook.tabs() if tab != tab_id]
                if tabs:
                    notebook.select(tabs[0])
            notebook.forget(performance_tab)
        except Exception as e:
            logger.debug("Failed to remove Performance tab: %s", e, exc_info=True)
        try:
            performance_tab.destroy()
        except Exception:
            pass
        try:
            self._advanced_tab_original_text.pop(tab_id, None)
        except Exception:
            pass
        self._performance_tab = None
        self._performance_text = None

    def _build_performance_tab(self, performance_tab: tk.Frame) -> None:
        content = tk.Frame(performance_tab, padx=10, pady=10)
        content.pack(fill="both", expand=True)

        actions = tk.Frame(content)
        actions.pack(fill="x", pady=(0, 8))
        tk.Button(actions, text="Refresh", command=self._refresh_performance_text).pack(side="left")
        tk.Button(actions, text="Reset", command=self._reset_performance_stats).pack(side="left", padx=(6, 0))
        tk.Button(actions, text="Copy", command=self._copy_performance_text).pack(side="left", padx=(6, 0))

        text = tk.Text(content, width=78, height=18, wrap="none", font=("Consolas", 9))
        text.pack(fill="both", expand=True)
        self._performance_text = text
        self._refresh_performance_text()

    def _refresh_performance_text(self) -> None:
        text = getattr(self, "_performance_text", None)
        if text is None:
            return
        try:
            snapshot = str(self._on_performance_snapshot() or "")
        except Exception as e:
            logger.debug("Failed to read performance snapshot: %s", e, exc_info=True)
            snapshot = f"Failed to read performance snapshot: {e}"
        try:
            text.configure(state="normal")
            text.delete("1.0", tk.END)
            text.insert("1.0", snapshot)
            text.configure(state="disabled")
        except Exception as e:
            logger.debug("Failed to refresh performance text: %s", e, exc_info=True)

    def _reset_performance_stats(self) -> None:
        try:
            self._on_performance_reset()
        except Exception as e:
            logger.debug("Failed to reset performance stats: %s", e, exc_info=True)
        self._refresh_performance_text()

    def _copy_performance_text(self) -> None:
        text = getattr(self, "_performance_text", None)
        if text is None:
            return
        try:
            snapshot = text.get("1.0", "end-1c")
            self.root.clipboard_clear()
            self.root.clipboard_append(snapshot)
        except Exception as e:
            logger.debug("Failed to copy performance snapshot: %s", e, exc_info=True)

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
            logger.debug("Failed to remember OCR button defaults: %s", e, exc_info=True)
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
            logger.debug("Failed to read OCR values for screen buttons: %s", e, exc_info=True)
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
                    logger.debug("Invalid OCR float value for %s: %s", key, e, exc_info=True)
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
            logger.debug("Failed to read OCR values before runtime apply: %s", e, exc_info=True)
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

    def _handle_ocr_show_boxes(self):
        values = self._get_ocr_values_from_vars()
        try:
            count = int(self._on_ocr_show_boxes(dict(values)) or 0)
        except Exception:
            count = 0
        if hasattr(self, "_advanced_status_var"):
            if count > 0:
                self._advanced_status_var.set(f"Showing {count} OCR box(es) briefly.")
            else:
                self._advanced_status_var.set("No OCR boxes to show.")

    def _handle_select_ocr_area(self) -> None:
        var = getattr(self, "_ocr_area_select_var", None)
        try:
            index = int(var.get()) if var is not None else 1
        except Exception as e:
            logger.debug("Invalid OCR selected area index: %s", e, exc_info=True)
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
                    logger.debug("Invalid advanced int value for %s: %s", key, e, exc_info=True)
                    val = int(spec.get("default", 0))
                var.set(str(val))
            elif spec["type"] == "float":
                try:
                    val = float(str(cfg_val).replace(",", "."))
                except Exception as e:
                    logger.debug("Invalid advanced float value for %s: %s", key, e, exc_info=True)
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
            logger.debug("Failed to update selected OCR screen: %s", e, exc_info=True)
            self._ocr_selected_screen = None
        if hasattr(self, "_advanced_status_var"):
            self._advanced_status_var.set("Loaded values from config.")
        self._refresh_phone_toggle_button()
        self._refresh_ocr_area_buttons()
        self._update_ocr_screen_button_styles()
        annotation_tab = getattr(self, "_annotation_tab_ui", None)
        if annotation_tab is not None:
            try:
                annotation_tab.load_style_vars()
                annotation_tab.refresh_sync_status_labels()
            except Exception:
                logger.debug("Failed to refresh annotation tab after config load", exc_info=True)

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
                if spec.get("round") is not None:
                    try:
                        num = round(num, int(spec.get("round")))
                    except Exception:
                        pass
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
            logger.debug("Failed to read selected advanced tab: %s", e, exc_info=True)
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
                    logger.debug("Invalid default int for %s: %s", key, e, exc_info=True)
                    var.set("0")
            elif spec["type"] == "float":
                try:
                    var.set(self._format_number(float(default)))
                except Exception as e:
                    logger.debug("Invalid default float for %s: %s", key, e, exc_info=True)
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
                logger.debug("set_many failed; falling back to individual config writes: %s", e, exc_info=True)
                for key, value in values.items():
                    self.config.set(key, value)
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Saved and applied.")
        else:
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Applied for current session (not saved).")
