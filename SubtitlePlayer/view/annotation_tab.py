"""Annotation tab for local/Anki/WaniKani word databases."""

from __future__ import annotations

import json
import logging
import re
import threading
import tkinter as tk
from datetime import datetime
from tkinter import colorchooser, filedialog, messagebox, ttk
from typing import Any

from model.annotation_styles import DEFAULT_STYLES, STATUS_LABELS, STATUS_ORDER, dump_style_config, load_style_config
from model.word_database import normalize_word, search_query_variants
from utils import get_monitor_rects

logger = logging.getLogger(__name__)


class _Tooltip:
    def __init__(self, widget, text: str) -> None:
        self.widget = widget
        self.text = str(text or "")
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.window is not None or not self.text:
            return
        try:
            x = int(self.widget.winfo_rootx()) + 18
            y = int(self.widget.winfo_rooty()) + int(self.widget.winfo_height()) + 6
            win = tk.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            try:
                win.transient(self.widget.winfo_toplevel())
                win.attributes("-topmost", True)
            except Exception:
                pass
            win.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                win,
                text=self.text,
                justify="left",
                background="#ffffe0",
                relief="solid",
                borderwidth=1,
                padx=6,
                pady=3,
                wraplength=360,
            )
            label.pack()
            self.window = win
            try:
                win.lift()
            except Exception:
                pass
        except Exception:
            logger.debug("Failed to show annotation tooltip", exc_info=True)

    def _hide(self, _event=None) -> None:
        win = self.window
        self.window = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass


ANNOTATION_SPECS = [
    {"key": "ANNOTATION_ENABLED", "type": "bool", "default": False},
    {"key": "ANNOTATION_LOCAL_DB_PATH", "type": "str", "default": "annotation_words.json", "allow_empty": False},
    {"key": "ANNOTATION_MARK_LOCAL", "type": "bool", "default": False},
    {"key": "ANNOTATION_COLORIZE_ANKI", "type": "bool", "default": False},
    {"key": "ANNOTATION_COLORIZE_WANIKANI", "type": "bool", "default": False},
    {"key": "ANNOTATION_INCLUDE_PARTICLES", "type": "bool", "default": False},
    {"key": "ANNOTATION_MIN_TOKEN_LENGTH", "type": "int", "default": 1, "min": 1, "max": 20},
    {"key": "ANNOTATION_ONLY_ON_HOVER", "type": "bool", "default": False},
    {"key": "ANNOTATION_HIGHLIGHT_ON_HOVER", "type": "bool", "default": True},
    {"key": "ANNOTATION_SHOW_RUBY_ON_HOVER", "type": "bool", "default": True},
    {"key": "ANNOTATION_SHOW_MEANING_ON_HOVER", "type": "bool", "default": False},
    {"key": "ANNOTATION_SHOW_CARD_STATUS_ON_HOVER", "type": "bool", "default": False},
    {"key": "ANNOTATION_RUBY_MODE", "type": "str", "default": "always", "allow_empty": False},
    {"key": "ANNOTATION_USE_ANKI_MATURE_THRESHOLD", "type": "bool", "default": True},
    {"key": "ANNOTATION_ANKI_MATURE_INTERVAL_DAYS", "type": "int", "default": 21, "min": 1, "max": 9999},
    {"key": "ANNOTATION_ANKI_SUSPENDED_AS", "type": "str", "default": "normal", "allow_empty": False},
    {"key": "ANNOTATION_ANKI_DECKS", "type": "str", "default": "", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_NOTE_TYPE", "type": "str", "default": "", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_WORD_FIELDS", "type": "str", "default": "Front", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_SENTENCE_FIELDS", "type": "str", "default": "", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_SENTENCE_TRANSLATED_FIELDS", "type": "str", "default": "", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_READING_FIELDS", "type": "str", "default": "", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_MEANING_FIELDS", "type": "str", "default": "Back", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_INCLUDE_SENTENCE_WORDS", "type": "bool", "default": False},
    {"key": "ANNOTATION_WANIKANI_API_TOKEN", "type": "str", "default": "", "allow_empty": True},
    {"key": "ANNOTATION_ANKI_LAST_SYNC", "type": "str", "default": "", "allow_empty": True, "hidden": True},
    {"key": "ANNOTATION_ANKI_LAST_COUNT", "type": "int", "default": 0, "min": 0, "hidden": True},
    {"key": "ANNOTATION_ANKI_LAST_SKIPPED", "type": "int", "default": 0, "min": 0, "hidden": True},
    {"key": "ANNOTATION_ANKI_LAST_FAILED", "type": "int", "default": 0, "min": 0, "hidden": True},
    {"key": "ANNOTATION_WANIKANI_LAST_SYNC", "type": "str", "default": "", "allow_empty": True, "hidden": True},
    {"key": "ANNOTATION_WANIKANI_VOCAB_COUNT", "type": "int", "default": 0, "min": 0, "hidden": True},
    {"key": "ANNOTATION_WANIKANI_KANJI_COUNT", "type": "int", "default": 0, "min": 0, "hidden": True},
]


class AnnotationTab:
    def __init__(self, settings_ui: Any, parent: tk.Frame, tab_id: str) -> None:
        self.ui = settings_ui
        self.parent = parent
        self.tab_id = tab_id
        self._style_vars: dict[str, tk.Variable] = {}
        self._word_search_var = tk.StringVar(value="")
        self._word_column_labels = {
            "word": "Word",
            "meaning": "Meaning",
            "status": "Status",
            "nid": "NID",
            "due_date": "Due Date",
            "reviews": "Reviews",
            "note_modified": "Note Modified",
            "sentence": "Sentence Field",
            "sentence_translated": "Sentence Field (Translated)",
        }
        self._word_column_order = self._load_word_column_order()
        self._word_drag_column = ""
        self._word_drag_original_order: list[str] = []
        self._word_drag_window: tk.Toplevel | None = None
        self._status_label_to_key = {STATUS_LABELS.get(key, key): key for key in STATUS_ORDER}
        self._style_status_var = tk.StringVar(value=STATUS_LABELS.get("local_known", "local_known"))
        self._word_tree: ttk.Treeview | None = None
        self._deck_listbox: tk.Listbox | None = None
        self._model_combo: ttk.Combobox | None = None
        self._anki_status_var = tk.StringVar(value="")
        self._wanikani_status_var = tk.StringVar(value="")
        self._tab_canvas: tk.Canvas | None = None
        self._tab_wheel_handlers = None
        self._register_settings()
        self._build()

    @staticmethod
    def _w(width: int) -> int:
        return max(1, int(round(int(width) * 4 / 3)))

    def _register_settings(self) -> None:
        if not hasattr(self.ui, "_advanced_vars"):
            self.ui._advanced_vars = {}
        if not hasattr(self.ui, "_advanced_meta"):
            self.ui._advanced_meta = {}
        keys = self.ui._advanced_tab_key_map.setdefault(self.tab_id, [])
        for spec in ANNOTATION_SPECS:
            key = spec["key"]
            self.ui._advanced_meta[key] = spec
            if key not in self.ui._advanced_vars:
                if spec["type"] == "bool":
                    self.ui._advanced_vars[key] = tk.BooleanVar(value=bool(spec.get("default")))
                else:
                    self.ui._advanced_vars[key] = tk.StringVar(value=str(spec.get("default", "")))
            if key not in keys:
                keys.append(key)

    def _var(self, key: str):
        return self.ui._advanced_vars[key]

    def _build(self) -> None:
        canvas = tk.Canvas(self.parent, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.parent, orient="vertical", command=canvas.yview)
        content = tk.Frame(canvas, padx=0, pady=8)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._tab_canvas = canvas
        try:
            self.ui._advanced_scroll_canvases[self.tab_id] = canvas
            self.ui._advanced_scroll_contents[self.tab_id] = content
        except Exception:
            logger.debug("Failed to register annotation tab sizing widgets", exc_info=True)

        def _on_configure(_event=None):
            min_width = int(getattr(content, "_advanced_min_width", 0) or content.winfo_reqwidth())
            window_width = max(min_width, int(canvas.winfo_width()) - 4)
            canvas.itemconfigure(content_id, width=window_width)
            canvas.configure(scrollregion=(0, 0, window_width, max(int(content.winfo_reqheight()), int(canvas.winfo_height()))))

        content.bind("<Configure>", _on_configure)
        canvas.bind("<Configure>", _on_configure)
        self._bind_mousewheel(canvas)

        columns = tk.Frame(content)
        columns.pack(fill="x", expand=True, anchor="n")
        columns.grid_columnconfigure(0, weight=1)
        columns.grid_columnconfigure(1, weight=0)
        left = tk.Frame(columns)
        right = tk.Frame(columns)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_top(left)
        self._build_local_database(left)
        self._build_anki(left)
        self._build_wanikani(left)
        self._build_display(right)
        self._build_styles(right)
        self._register_tab_min_width(content, columns)
        self.refresh_words()
        self.refresh_sync_status_labels()

    def _register_tab_min_width(self, content: tk.Frame, columns: tk.Frame) -> None:
        try:
            columns.update_idletasks()
            min_width = int(columns.winfo_reqwidth())
            content._advanced_min_width = min_width
            self.ui._advanced_tab_min_widths[str(self.tab_id)] = min_width
        except Exception:
            logger.debug("Failed to measure annotation tab minimum width", exc_info=True)

    def _load_word_column_order(self) -> list[str]:
        default_order = list(self._word_column_labels)
        try:
            raw_order = self.ui.config.get("ANNOTATION_WORD_COLUMN_ORDER")
        except Exception:
            raw_order = None
        if isinstance(raw_order, str):
            candidates = [part.strip() for part in raw_order.split(",")]
        elif isinstance(raw_order, (list, tuple)):
            candidates = [str(part).strip() for part in raw_order]
        else:
            candidates = []
        order = []
        for column in candidates:
            if column in self._word_column_labels and column not in order:
                order.append(column)
        for column in default_order:
            if column not in order:
                order.append(column)
        return order

    def _save_word_column_order(self) -> None:
        try:
            self.ui.config.set("ANNOTATION_WORD_COLUMN_ORDER", list(self._word_column_order))
        except Exception:
            logger.debug("Failed to save annotation word column order", exc_info=True)

    def _bind_word_column_drag(self, tree: ttk.Treeview) -> None:
        tree.bind("<ButtonPress-1>", self._on_word_column_press, add="+")
        tree.bind("<B1-Motion>", self._on_word_column_motion, add="+")
        tree.bind("<ButtonRelease-1>", self._on_word_column_release, add="+")

    def _on_word_column_press(self, event) -> None:
        tree = self._word_tree
        self._word_drag_column = ""
        if tree is None or tree.identify_region(event.x, event.y) != "heading":
            return
        self._word_drag_column = self._word_column_from_tree_x(tree, event.x)
        if self._word_drag_column:
            self._word_drag_original_order = self._current_word_display_order(tree)
            self._show_word_column_drag_header(event)

    def _on_word_column_motion(self, event) -> None:
        tree = self._word_tree
        if tree is None or not self._word_drag_column:
            return
        self._show_word_column_drag_header(event)
        order = self._preview_reordered_word_columns_at_x(tree, event.x)
        if not order:
            return
        if order and order != self._current_word_display_order(tree):
            tree.configure(displaycolumns=order)

    def _on_word_column_release(self, event) -> None:
        tree = self._word_tree
        dragged = self._word_drag_column
        self._word_drag_column = ""
        self._hide_word_column_drag_header()
        if tree is None or not dragged or tree.identify_region(event.x, event.y) != "heading":
            if tree is not None and self._word_drag_original_order:
                tree.configure(displaycolumns=self._word_drag_original_order)
            self._word_drag_original_order = []
            return
        self._word_column_order = self._preview_reordered_word_columns_at_x(tree, event.x) or self._current_word_display_order(tree)
        tree.configure(displaycolumns=self._word_column_order)
        self._save_word_column_order()
        self._word_drag_original_order = []

    def _preview_reordered_word_columns_at_x(self, tree: ttk.Treeview, x: int) -> list[str]:
        dragged = self._word_drag_column
        current = self._current_word_display_order(tree)
        if not dragged or dragged not in current:
            return []
        insert_idx = self._word_column_insert_index_at_x(tree, x, current)
        dragged_idx = current.index(dragged)
        order = [column for column in current if column != dragged]
        if dragged_idx < insert_idx:
            insert_idx -= 1
        insert_idx = max(0, min(len(order), insert_idx))
        order.insert(insert_idx, dragged)
        return order

    def _word_column_insert_index_at_x(self, tree: ttk.Treeview, x: int, order: list[str]) -> int:
        widths = []
        total_width = 0
        for column in order:
            try:
                width = max(1, int(tree.column(column, "width") or 1))
            except Exception:
                width = 1
            widths.append(width)
            total_width += width
        try:
            left_fraction = float(tree.xview()[0])
        except Exception:
            left_fraction = 0.0
        absolute_x = max(0.0, (left_fraction * max(1, total_width)) + float(x))
        cursor = 0.0
        for idx, width in enumerate(widths):
            midpoint = cursor + (width / 2.0)
            if absolute_x < midpoint:
                return idx
            cursor += width
        return len(order)

    def _show_word_column_drag_header(self, event) -> None:
        column = self._word_drag_column
        if not column:
            return
        label_text = self._word_column_labels.get(column, column)
        win = self._word_drag_window
        if win is None or not win.winfo_exists():
            try:
                parent = self._word_tree or self.parent
                win = tk.Toplevel(parent)
                win.wm_overrideredirect(True)
                try:
                    win.transient(parent.winfo_toplevel())
                    win.attributes("-topmost", True)
                except Exception:
                    pass
                tk.Label(
                    win,
                    text=label_text,
                    padx=8,
                    pady=3,
                    relief="raised",
                    borderwidth=1,
                    background="#f4f4f4",
                ).pack()
                self._word_drag_window = win
            except Exception:
                logger.debug("Failed to create annotation column drag header", exc_info=True)
                return
        else:
            try:
                child = win.winfo_children()[0]
                if isinstance(child, tk.Label):
                    child.configure(text=label_text)
            except Exception:
                pass
        try:
            win.geometry(f"+{int(event.x_root) + 10}+{int(event.y_root) + 10}")
            win.lift()
        except Exception:
            logger.debug("Failed to move annotation column drag header", exc_info=True)

    def _hide_word_column_drag_header(self) -> None:
        win = self._word_drag_window
        self._word_drag_window = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass

    def _word_column_from_tree_x(self, tree: ttk.Treeview, x: int) -> str:
        column_id = str(tree.identify_column(x) or "")
        if not column_id.startswith("#"):
            return ""
        try:
            display_idx = int(column_id[1:]) - 1
        except Exception:
            return ""
        display_columns = self._current_word_display_order(tree)
        if 0 <= display_idx < len(display_columns):
            column = str(display_columns[display_idx])
            return column if column in self._word_column_labels else ""
        return ""

    def _current_word_display_order(self, tree: ttk.Treeview) -> list[str]:
        try:
            raw = tree.cget("displaycolumns")
        except Exception:
            raw = ()
        if isinstance(raw, str):
            values = list(self._word_column_labels) if raw in {"", "#all"} else [part.strip() for part in raw.split()]
        else:
            values = list(raw)
        order = [str(column) for column in values if str(column) in self._word_column_labels]
        return order or list(self._word_column_order)

    def _bind_mousewheel(self, canvas: tk.Canvas) -> None:
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

        def _bind_tab_wheel():
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_button4)
            canvas.bind_all("<Button-5>", _on_button5)

        def _unbind_tab_wheel():
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        self._tab_wheel_handlers = (_bind_tab_wheel, _unbind_tab_wheel)
        canvas.bind("<Enter>", lambda _event: _bind_tab_wheel())
        canvas.bind("<Leave>", lambda _event: _unbind_tab_wheel())

    def _bind_tree_mousewheel(self, tree: ttk.Treeview) -> None:
        def _on_mousewheel(event):
            try:
                delta = int(-1 * (event.delta / 120))
            except Exception:
                delta = 0
            if delta:
                if int(getattr(event, "state", 0) or 0) & 0x0001:
                    tree.xview_scroll(delta * 120, "units")
                else:
                    tree.yview_scroll(delta, "units")
            return "break"

        def _on_button4(event):
            if int(getattr(event, "state", 0) or 0) & 0x0001:
                tree.xview_scroll(-12, "units")
            else:
                tree.yview_scroll(-1, "units")
            return "break"

        def _on_button5(event):
            if int(getattr(event, "state", 0) or 0) & 0x0001:
                tree.xview_scroll(12, "units")
            else:
                tree.yview_scroll(1, "units")
            return "break"

        def _bind_tree(_event=None):
            tree.bind_all("<MouseWheel>", _on_mousewheel)
            tree.bind_all("<Button-4>", _on_button4)
            tree.bind_all("<Button-5>", _on_button5)

        def _restore_tab(_event=None):
            handlers = self._tab_wheel_handlers
            if handlers:
                handlers[0]()

        tree.bind("<Enter>", _bind_tree)
        tree.bind("<Leave>", _restore_tab)

    def _section(self, parent, title: str) -> tk.LabelFrame:
        frame = tk.LabelFrame(parent, text=title, padx=10, pady=8)
        pack_info = {"fill": "x", "pady": (0, 10)}
        frame.pack(**pack_info)
        frame.grid_columnconfigure(1, weight=1)
        sections = getattr(self.ui, "_advanced_filter_sections", None)
        if isinstance(sections, list):
            sections.append(
                {
                    "parent": parent,
                    "tab_id": str(self.tab_id),
                    "section": frame,
                    "section_text": str(title or "").casefold(),
                    "pack": pack_info,
                    "rows": [],
                }
            )
        return frame

    def _build_top(self, parent) -> None:
        section = self._section(parent, "Annotation")
        section.grid_columnconfigure(0, weight=1)
        section.grid_columnconfigure(1, weight=1)
        tk.Checkbutton(section, text="Enable subtitle annotations", variable=self._var("ANNOTATION_ENABLED")).grid(
            row=0, column=0, sticky="w", padx=(0, 10)
        )
        tk.Checkbutton(section, text="Mark database words", variable=self._var("ANNOTATION_MARK_LOCAL")).grid(
            row=1, column=0, sticky="w", padx=(0, 10)
        )
        tk.Checkbutton(section, text="Colorize Anki words", variable=self._var("ANNOTATION_COLORIZE_ANKI")).grid(
            row=0, column=1, sticky="w"
        )
        tk.Checkbutton(section, text="Colorize WaniKani words", variable=self._var("ANNOTATION_COLORIZE_WANIKANI")).grid(
            row=1, column=1, sticky="w"
        )

    def _build_local_database(self, parent) -> None:
        section = self._section(parent, "My Word Database")
        tk.Label(section, text="Search").grid(row=0, column=0, sticky="w", pady=(6, 2))
        search = tk.Entry(section, textvariable=self._word_search_var, width=self._w(20))
        search.grid(row=0, column=1, sticky="ew", pady=(6, 2))
        self._word_search_var.trace_add("write", lambda *_args: self.refresh_words())

        columns = tuple(self._word_column_labels)
        table = tk.Frame(section, width=540, height=210)
        table.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 4))
        table.grid_propagate(False)
        table.grid_rowconfigure(0, weight=1)
        table.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(table, columns=columns, show="headings", height=8, selectmode="browse")
        widths = {
            "word": 140,
            "meaning": 170,
            "status": 120,
            "nid": 90,
            "due_date": 120,
            "reviews": 80,
            "note_modified": 140,
            "sentence": 220,
            "sentence_translated": 220,
        }
        for column in columns:
            tree.heading(column, text=self._word_column_labels[column])
            anchor = "w" if column in {"sentence", "sentence_translated"} else "center"
            tree.column(column, width=widths[column], stretch=False, anchor=anchor)
        tree.configure(displaycolumns=self._word_column_order)
        tree_scroll_y = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
        tree_scroll_x = ttk.Scrollbar(table, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        tree.bind("<Double-1>", lambda _event: self._edit_selected_word())
        self._bind_tree_mousewheel(tree)
        self._word_tree = tree
        self._bind_word_column_drag(tree)

        buttons = tk.Frame(section)
        buttons.grid(row=2, column=0, columnspan=3, sticky="ew")
        for text, command in (
            ("Import words", self.import_words),
            ("Export words", self.export_words),
            ("Add word", self.add_word),
            ("Delete selected", self.delete_selected_word),
            ("Ignore selected", self.ignore_selected_word),
            ("Refresh", self.refresh_word_database),
        ):
            tk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 6))

    def _build_anki(self, parent) -> None:
        section = self._section(parent, "Anki Word Database")
        tk.Button(section, text="Connect / Refresh Anki", command=self.refresh_anki_metadata).grid(row=0, column=0, sticky="w", pady=4)
        self._model_combo = ttk.Combobox(section, textvariable=self._var("ANNOTATION_ANKI_NOTE_TYPE"), values=[], width=self._w(28))
        self._model_combo.grid(row=0, column=1, sticky="ew", pady=4)
        self._model_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_anki_fields())

        tk.Label(section, text="Decks").grid(row=1, column=0, sticky="nw")
        self._deck_listbox = tk.Listbox(section, height=5, selectmode="extended", exportselection=False)
        self._deck_listbox.grid(row=1, column=1, sticky="ew")
        self._deck_listbox.bind("<<ListboxSelect>>", lambda _event: self._store_selected_decks())

        rows = [
            ("Word fields", "ANNOTATION_ANKI_WORD_FIELDS"),
            ("Sentence fields", "ANNOTATION_ANKI_SENTENCE_FIELDS"),
            ("Sentence fields (translated)", "ANNOTATION_ANKI_SENTENCE_TRANSLATED_FIELDS"),
            ("Reading fields", "ANNOTATION_ANKI_READING_FIELDS"),
            ("Meaning fields", "ANNOTATION_ANKI_MEANING_FIELDS"),
        ]
        start_row = 2
        for offset, (label, key) in enumerate(rows):
            tk.Label(section, text=label).grid(row=start_row + offset, column=0, sticky="w", pady=2)
            tk.Entry(section, textvariable=self._var(key), width=self._w(18)).grid(row=start_row + offset, column=1, sticky="w", pady=2)

        row = start_row + len(rows)
        tk.Checkbutton(section, text="Extract words from sentence fields", variable=self._var("ANNOTATION_ANKI_INCLUDE_SENTENCE_WORDS")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        tk.Label(section, text="Mature interval threshold days").grid(row=row, column=0, sticky="w")
        tk.Entry(section, textvariable=self._var("ANNOTATION_ANKI_MATURE_INTERVAL_DAYS"), width=self._w(8)).grid(row=row, column=1, sticky="w")
        row += 1
        tk.Checkbutton(section, text="Use review interval for mature status", variable=self._var("ANNOTATION_USE_ANKI_MATURE_THRESHOLD")).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        tk.Label(section, text="Treat suspended Anki cards as").grid(row=row, column=0, sticky="w")
        ttk.Combobox(
            section,
            textvariable=self._var("ANNOTATION_ANKI_SUSPENDED_AS"),
            values=["normal", "mature", "young", "graduated", "learning", "unknown", "ignored"],
            state="readonly",
            width=self._w(16),
        ).grid(row=row, column=1, sticky="w")
        row += 1
        sync_buttons = tk.Frame(section)
        sync_buttons.grid(row=row, column=0, sticky="w", pady=(6, 2))
        tk.Button(sync_buttons, text="Sync Anki words", command=self.sync_anki).pack(side="left")
        tk.Button(sync_buttons, text="Sync missing Anki words", command=lambda: self.sync_anki(missing_only=True)).pack(side="left", padx=(6, 0))
        self._build_wrapping_status_label(section, self._anki_status_var, row, 1)

    def _build_wanikani(self, parent) -> None:
        section = self._section(parent, "WaniKani Word Database")
        tk.Button(section, text="Set API Token", command=self.set_wanikani_token).grid(row=0, column=0, sticky="w", pady=4)
        tk.Button(section, text="Sync WaniKani", command=self.sync_wanikani).grid(row=0, column=1, sticky="w", padx=(6, 0), pady=4)
        tk.Button(section, text="Clear WaniKani Cache", command=self.clear_wanikani).grid(row=0, column=2, sticky="w", padx=(6, 0), pady=4)
        self._build_wrapping_status_label(section, self._wanikani_status_var, 1, 0, columnspan=4)

    def _build_wrapping_status_label(
        self,
        parent,
        textvariable: tk.StringVar,
        row: int,
        column: int,
        *,
        columnspan: int = 1,
    ) -> tk.Label:
        label = tk.Label(parent, textvariable=textvariable, anchor="w", justify="left")
        label.grid(row=row, column=column, columnspan=columnspan, sticky="ew", pady=(6, 2))

        def _sync_wrap(event=None):
            try:
                width = int(label.winfo_width() if event is None else event.width)
                label.configure(wraplength=max(180, width - 8))
            except Exception:
                pass

        label.bind("<Configure>", _sync_wrap, add="+")
        self.ui.root.after_idle(_sync_wrap)
        return label

    def _build_display(self, parent) -> None:
        section = self._section(parent, "Display / Ruby Behavior")
        modes = [
            ("Always show ruby", "always"),
            ("Hide ruby for mature known words", "hide_mature"),
            ("Hide ruby for all known words", "hide_known"),
            ("Show ruby only on hover", "hover_only"),
            ("Show ruby only for unknown/uncollected words", "unknown_only"),
        ]
        ruby_label = tk.Label(section, text="Ruby display mode")
        ruby_label.grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(section, textvariable=self._var("ANNOTATION_RUBY_MODE"), values=[value for _label, value in modes], state="readonly", width=self._w(18))
        combo.grid(row=0, column=1, sticky="w")
        ruby_help = (
            "always: show ruby for every subtitle word.\n"
            "hide_mature: hide ruby only for mature known words.\n"
            "hide_known: hide ruby for all known database words.\n"
            "hover_only: hide ruby for annotation matches until hover.\n"
            "unknown_only: show ruby only for words not known or ignored in the database."
        )
        _Tooltip(ruby_label, ruby_help)
        _Tooltip(combo, ruby_help)
        hover_only = tk.Checkbutton(section, text="Only show annotations on hover", variable=self._var("ANNOTATION_ONLY_ON_HOVER"))
        hover_only.grid(row=1, column=0, columnspan=2, sticky="w")
        _Tooltip(
            hover_only,
            "Only affects visible annotation styling. It is most useful when General -> Show ruby only on kanji hover is off, because hover-ruby mode already delays ruby display until hover.",
        )
        tk.Checkbutton(section, text="Highlight words on hover", variable=self._var("ANNOTATION_HIGHLIGHT_ON_HOVER")).grid(row=2, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(section, text="Show hidden ruby on hover", variable=self._var("ANNOTATION_SHOW_RUBY_ON_HOVER")).grid(row=3, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(section, text="Show meaning in hover popup", variable=self._var("ANNOTATION_SHOW_MEANING_ON_HOVER")).grid(row=4, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(section, text="Show Anki/local status in hover popup", variable=self._var("ANNOTATION_SHOW_CARD_STATUS_ON_HOVER")).grid(row=5, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(section, text="Include particles in annotation matching", variable=self._var("ANNOTATION_INCLUDE_PARTICLES")).grid(row=6, column=0, columnspan=2, sticky="w")
        min_len_label = tk.Label(section, text="Minimum token length for annotation")
        min_len_label.grid(row=7, column=0, sticky="w")
        min_len_entry = tk.Entry(section, textvariable=self._var("ANNOTATION_MIN_TOKEN_LENGTH"), width=self._w(8))
        min_len_entry.grid(row=7, column=1, sticky="w")
        min_len_help = "Minimum subtitle-token length to mark. Example: 2 skips one-character tokens like は or の, but still marks 重要."
        _Tooltip(min_len_label, min_len_help)
        _Tooltip(min_len_entry, min_len_help)

    def _build_styles(self, parent) -> None:
        section = self._section(parent, "Styles")
        section.grid_columnconfigure(1, weight=1, minsize=self._w(18) * 8)
        tk.Label(section, text="Status").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(
            section,
            textvariable=self._style_status_var,
            values=[STATUS_LABELS.get(status, status) for status in STATUS_ORDER],
            state="readonly",
            width=self._w(16),
        )
        combo.grid(row=0, column=1, sticky="ew")
        combo.bind("<<ComboboxSelected>>", lambda _event: self.load_style_vars())

        color_keys = {"text_color", "background_color", "underline_color", "overline_color", "outline_color"}
        controls = [
            ("Enable style", "enabled", "bool"),
            ("Text color", "text_color", "str"),
            ("Background color", "background_color", "str"),
            ("Background alpha", "background_alpha", "str"),
            ("Underline", "underline", "bool"),
            ("Underline color", "underline_color", "str"),
            ("Underline thickness", "underline_thickness", "str"),
            ("Overline", "overline", "bool"),
            ("Overline color", "overline_color", "str"),
            ("Overline thickness", "overline_thickness", "str"),
            ("Outline", "outline", "bool"),
            ("Outline color", "outline_color", "str"),
            ("Outline thickness", "outline_thickness", "str"),
            ("Show ruby for this status", "show_ruby", "bool_or_auto"),
        ]
        for idx, (label, key, kind) in enumerate(controls, start=1):
            tk.Label(section, text=label).grid(row=idx, column=0, sticky="w", pady=2)
            if kind == "bool":
                var = tk.BooleanVar(value=False)
                tk.Checkbutton(section, variable=var).grid(row=idx, column=1, sticky="w")
            elif kind == "bool_or_auto":
                var = tk.StringVar(value="auto")
                ttk.Combobox(section, textvariable=var, values=["auto", "true", "false"], state="readonly", width=self._w(12)).grid(row=idx, column=1, sticky="ew")
            elif key in color_keys:
                var = tk.StringVar(value="")
                self._build_color_control(section, idx, var, key)
            else:
                var = tk.StringVar(value="")
                tk.Entry(section, textvariable=var, width=self._w(12)).grid(row=idx, column=1, sticky="ew", pady=2)
            self._style_vars[key] = var
        tk.Button(section, text="Save style", command=self.save_current_style).grid(row=len(controls) + 1, column=0, sticky="w", pady=(6, 0))
        tk.Button(section, text="Reset style", command=self.reset_current_style).grid(row=len(controls) + 1, column=1, sticky="w", pady=(6, 0))
        self.load_style_vars()

    def _build_color_control(self, parent, row: int, var: tk.StringVar, key: str) -> None:
        frame = tk.Frame(parent)
        frame.grid(row=row, column=1, sticky="ew", pady=2)
        frame.grid_columnconfigure(0, weight=1)
        entry = tk.Entry(frame, textvariable=var, width=self._w(12))
        entry.grid(row=0, column=0, sticky="ew")
        swatch = tk.Label(frame, width=3, relief="sunken", borderwidth=1)
        swatch.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        picker = self._build_color_picker_icon(frame, key, var)
        picker.grid(row=0, column=2, padx=(4, 0))
        var.trace_add("write", lambda *_args: self._update_color_widgets(swatch, picker, var.get()))
        self._update_color_widgets(swatch, picker, var.get())

    def _build_color_picker_icon(self, parent, key: str, var: tk.StringVar):
        canvas = tk.Canvas(parent, width=30, height=22, highlightthickness=1, highlightbackground="#888", cursor="hand2")
        canvas.configure(background=parent.cget("background"))
        if key == "text_color":
            canvas.create_text(15, 8, text="A", font=("Arial", 10, "bold"), fill="#111", tags=("icon",))
            color_item = canvas.create_rectangle(8, 16, 22, 18, outline="", fill="#000000", tags=("color",))
            _Tooltip(canvas, "Text color")
        elif key == "background_color":
            canvas.create_polygon(8, 7, 17, 4, 22, 12, 13, 15, outline="#111", fill="#f8f8f8", tags=("icon",))
            color_item = canvas.create_rectangle(8, 16, 22, 18, outline="", fill="#000000", tags=("color",))
            _Tooltip(canvas, "Background color")
        else:
            canvas.create_rectangle(7, 5, 23, 15, outline="#111", fill="#f8f8f8", tags=("icon",))
            color_item = canvas.create_rectangle(7, 16, 23, 18, outline="", fill="#000000", tags=("color",))
            _Tooltip(canvas, "Choose color")
        canvas._annotation_color_item = color_item
        canvas.bind("<Button-1>", lambda _event: self._choose_style_color(var))
        return canvas

    def _choose_style_color(self, var: tk.StringVar) -> None:
        initial = str(var.get() or "").strip() or None
        try:
            _rgb, hex_color = colorchooser.askcolor(color=initial, parent=self.ui.advanced_window or self.ui.root)
        except Exception:
            logger.debug("Failed to open color chooser", exc_info=True)
            return
        if hex_color:
            var.set(str(hex_color))

    @staticmethod
    def _update_color_swatch(swatch: tk.Label, value: str) -> None:
        color = str(value or "").strip()
        try:
            swatch.configure(background=color if color else swatch.master.cget("background"))
        except Exception:
            try:
                swatch.configure(background=swatch.master.cget("background"))
            except Exception:
                pass

    def _update_color_widgets(self, swatch: tk.Label, picker, value: str) -> None:
        self._update_color_swatch(swatch, value)
        color = str(value or "").strip() or "#000000"
        try:
            picker.itemconfigure(getattr(picker, "_annotation_color_item"), fill=color)
        except Exception:
            pass

    def refresh_words(self) -> None:
        tree = self._word_tree
        if tree is None:
            return
        query = self._word_search_var.get()
        try:
            rows = self.ui._on_annotation_list_words("" if self._is_column_search(query) else query) or []
        except Exception:
            rows = []
        tree.delete(*tree.get_children())
        for row in rows:
            values = self._word_row_values(row)
            if not self._word_row_matches_query(values, query):
                continue
            key = row.get("key") or f"{row.get('source')}:{row.get('normalized')}"
            tree.insert(
                "",
                "end",
                iid=str(key),
                values=(
                    values["word"],
                    values["meaning"],
                    values["status"],
                    values["nid"],
                    values["due_date"],
                    values["reviews"],
                    values["note_modified"],
                    values["sentence"],
                    values["sentence_translated"],
                ),
            )

    def refresh_word_database(self) -> None:
        try:
            result = self.ui._on_annotation_refresh_words() or {}
            if result.get("ok"):
                self._set_status(f"Reloaded {result.get('count', 0)} word(s).")
            elif result.get("error"):
                self._set_status(str(result.get("error")))
        except Exception:
            logger.debug("Failed to refresh annotation word database", exc_info=True)
        self.refresh_words()

    def _word_row_values(self, row: dict) -> dict[str, str]:
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        reviews = self._display_extra_value(extra, "reviews")
        if reviews == "" and str(row.get("source") or "") == "anki":
            reviews = "0"
        return {
            "word": str(row.get("surface") or row.get("base") or ""),
            "meaning": str(row.get("meaning") or ""),
            "status": str(row.get("status") or ""),
            "nid": self._display_extra_value(extra, "note_id"),
            "due_date": self._display_extra_value(extra, "due_date"),
            "reviews": reviews,
            "note_modified": self._display_note_modified(self._display_extra_value(extra, "note_modified")),
            "sentence": self._display_extra_value(extra, "sentence"),
            "sentence_translated": self._display_extra_value(extra, "sentence_translated"),
        }

    @staticmethod
    def _display_extra_value(extra: dict, key: str) -> str:
        if key not in extra:
            return ""
        value = extra.get(key)
        return "" if value is None else str(value)

    @staticmethod
    def _display_note_modified(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if re.match(r"^\d{2}:\d{2} \d{2}-\d{2}-\d{4}$", text):
            return text
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M"):
            try:
                return datetime.strptime(text, fmt).strftime("%H:%M %d-%m-%Y")
            except Exception:
                continue
        return text

    def _is_column_search(self, query: str | None) -> bool:
        column, _needle = self._parse_word_column_query(query)
        return bool(column)

    def _word_row_matches_query(self, values: dict[str, str], query: str | None) -> bool:
        query_text = str(query or "").strip()
        if not query_text:
            return True
        column, needle = self._parse_word_column_query(query_text)
        if column:
            return self._search_text_matches(values.get(column, ""), needle)
        return any(self._search_text_matches(value, query_text) for value in values.values())

    def _parse_word_column_query(self, query: str | None) -> tuple[str, str]:
        text = str(query or "").strip()
        if ":" not in text:
            return "", ""
        raw_column, needle = text.split(":", 1)
        column = self._word_column_aliases().get(self._normalize_column_name(raw_column))
        if not column:
            return "", ""
        return column, needle.strip()

    def _word_column_aliases(self) -> dict[str, str]:
        aliases = {}
        for key, label in self._word_column_labels.items():
            aliases[self._normalize_column_name(key)] = key
            aliases[self._normalize_column_name(label)] = key
        aliases.update(
            {
                "front": "word",
                "surface": "word",
                "back": "meaning",
                "definition": "meaning",
                "review": "reviews",
                "reviews": "reviews",
                "reps": "reviews",
                "nid": "nid",
                "noteid": "nid",
                "note": "nid",
                "cardid": "nid",
                "cid": "nid",
                "due": "due_date",
                "duedate": "due_date",
                "reviewsdue": "due_date",
                "modified": "note_modified",
                "updated": "note_modified",
                "sentencefield": "sentence",
                "translated": "sentence_translated",
                "translation": "sentence_translated",
            }
        )
        return aliases

    @staticmethod
    def _normalize_column_name(text: str | None) -> str:
        return re.sub(r"[^0-9a-z]+", "", str(text or "").casefold())

    @staticmethod
    def _search_text_matches(value: str, needle: str) -> bool:
        normalized_value = normalize_word(value)
        needles = search_query_variants(needle)
        if not needles:
            return True
        return any(needle_value in normalized_value for needle_value in needles)

    def _selected_word_key(self) -> str:
        tree = self._word_tree
        if tree is None:
            return ""
        selected = tree.selection()
        return str(selected[0]) if selected else ""

    def add_word(self, initial: dict | None = None) -> None:
        self._word_dialog(initial or {})

    def _edit_selected_word(self) -> None:
        key = self._selected_word_key()
        if not key:
            return
        query = self._word_search_var.get()
        rows = self.ui._on_annotation_list_words("" if self._is_column_search(query) else query) or []
        current = next((row for row in rows if str(row.get("key")) == key), None)
        if current:
            self._word_dialog(current)

    def _word_dialog(self, initial: dict) -> None:
        old_key = str(initial.get("key") or "")
        initial_extra = dict(initial.get("extra") or {}) if isinstance(initial.get("extra"), dict) else {}
        initial_source = str(initial.get("source") or "local").strip() or "local"
        initial_normalized = str(initial.get("normalized") or "")
        initial_created_at = str(initial.get("created_at") or "")
        win = tk.Toplevel(self.ui.root)
        win.title("Annotation Word")
        win.transient(self.ui.advanced_window or self.ui.root)
        win.grab_set()
        vars_map = {
            "surface": tk.StringVar(value=str(initial.get("surface") or "")),
            "base": tk.StringVar(value=str(initial.get("base") or "")),
            "reading": tk.StringVar(value=str(initial.get("reading") or "")),
            "meaning": tk.StringVar(value=str(initial.get("meaning") or "")),
            "status": tk.StringVar(value=str(initial.get("status") or "local_known")),
            "notes": tk.StringVar(value=str(initial.get("notes") or "")),
        }
        body = tk.Frame(win, padx=10, pady=10)
        body.pack(fill="both", expand=True)
        fields = [("Word", "surface"), ("Base", "base"), ("Reading", "reading"), ("Meaning", "meaning"), ("Status", "status"), ("Notes", "notes")]
        for idx, (label, key) in enumerate(fields):
            tk.Label(body, text=label).grid(row=idx, column=0, sticky="w", pady=2)
            if key == "status":
                ttk.Combobox(body, textvariable=vars_map[key], values=STATUS_ORDER, state="readonly").grid(row=idx, column=1, sticky="ew", pady=2)
            else:
                tk.Entry(body, textvariable=vars_map[key], width=24).grid(row=idx, column=1, sticky="ew", pady=2)
        body.grid_columnconfigure(1, weight=1)

        def _save():
            payload = {key: var.get() for key, var in vars_map.items()}
            payload["source"] = initial_source
            payload["normalized"] = initial_normalized
            payload["created_at"] = initial_created_at
            payload["extra"] = dict(initial_extra)
            payload["old_key"] = old_key
            result = self.ui._on_annotation_add_word(payload) or {}
            if not result.get("ok"):
                messagebox.showerror("Annotation", result.get("error") or "Failed to save word.", parent=win)
                return
            new_key = str((result.get("entry") or {}).get("key") or "")
            if old_key and new_key and old_key != new_key:
                self.ui._on_annotation_delete_word(old_key)
            win.destroy()
            self.refresh_words()

        row = len(fields)
        tk.Button(body, text="Save", command=_save).grid(row=row, column=0, sticky="w", pady=(8, 0))
        tk.Button(body, text="Cancel", command=win.destroy).grid(row=row, column=1, sticky="e", pady=(8, 0))
        self._center_dialog(win)
        try:
            body.grid_slaves(row=0, column=1)[0].focus_set()
        except Exception:
            pass

    def delete_selected_word(self) -> None:
        key = self._selected_word_key()
        if not key:
            return
        if not messagebox.askyesno("Annotation", "Delete selected word?", parent=self.ui.advanced_window):
            return
        result = self.ui._on_annotation_delete_word(key) or {}
        if not result.get("ok"):
            messagebox.showerror("Annotation", result.get("error") or "Delete failed.", parent=self.ui.advanced_window)
        self.refresh_words()

    def ignore_selected_word(self) -> None:
        key = self._selected_word_key()
        if not key:
            return
        tree = self._word_tree
        values = tree.item(key, "values") if tree is not None else ()
        surface = values[0] if values else ""
        if not surface:
            return
        self.ui._on_annotation_add_word({"surface": surface, "source": "local", "status": "ignored"})
        self.refresh_words()

    def import_words(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.ui.advanced_window,
            title="Import words",
            filetypes=[("Word files", "*.txt *.csv *.json"), ("All files", "*.*")],
        )
        if not path:
            return
        result = self.ui._on_annotation_import_words(path) or {}
        if not result.get("ok"):
            messagebox.showerror("Annotation", result.get("error") or "Import failed.", parent=self.ui.advanced_window)
        else:
            self._set_status(f"Imported {result.get('imported', 0)} word(s).")
        self.refresh_words()

    def export_words(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.ui.advanced_window,
            title="Export words",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("JSON", "*.json")],
        )
        if not path:
            return
        result = self.ui._on_annotation_export_words(path) or {}
        if not result.get("ok"):
            messagebox.showerror("Annotation", result.get("error") or "Export failed.", parent=self.ui.advanced_window)
        else:
            self._set_status(f"Exported {result.get('exported', 0)} word(s).")

    def refresh_anki_metadata(self) -> None:
        self._anki_status_var.set("Refreshing Anki...")

        def _worker():
            result = self.ui._on_annotation_anki_refresh() or {}

            def _apply():
                if not result.get("ok"):
                    self._anki_status_var.set(result.get("error") or "AnkiConnect unavailable.")
                    return
                decks = result.get("decks") or []
                models = result.get("models") or []
                if self._deck_listbox is not None:
                    self._deck_listbox.delete(0, "end")
                    selected = {part.strip() for part in str(self._var("ANNOTATION_ANKI_DECKS").get()).split(",") if part.strip()}
                    for deck in decks:
                        self._deck_listbox.insert("end", deck)
                        if deck in selected:
                            self._deck_listbox.selection_set("end")
                if self._model_combo is not None:
                    self._model_combo.configure(values=models)
                self._anki_status_var.set(f"Loaded {len(decks)} deck(s), {len(models)} note type(s).")

            self.ui.root.after(0, _apply)

        threading.Thread(target=_worker, daemon=True, name="annotation-anki-refresh").start()

    def refresh_anki_fields(self) -> None:
        model = str(self._var("ANNOTATION_ANKI_NOTE_TYPE").get()).strip()
        if not model:
            return

        def _worker():
            result = self.ui._on_annotation_anki_model_fields(model) or {}

            def _apply():
                fields = result.get("fields") or []
                if result.get("ok") and fields and not str(self._var("ANNOTATION_ANKI_WORD_FIELDS").get()).strip():
                    self._var("ANNOTATION_ANKI_WORD_FIELDS").set(fields[0])
                self._anki_status_var.set(
                    f"Fields: {', '.join(fields[:8])}" if fields else result.get("error", "No fields found.")
                )

            self.ui.root.after(0, _apply)

        threading.Thread(target=_worker, daemon=True, name="annotation-anki-fields").start()

    def _store_selected_decks(self) -> None:
        lb = self._deck_listbox
        if lb is None:
            return
        decks = [lb.get(i) for i in lb.curselection()]
        self._var("ANNOTATION_ANKI_DECKS").set(", ".join(decks))

    def sync_anki(self, missing_only: bool = False) -> None:
        self._store_selected_decks()
        self._persist_annotation_values()
        self._anki_status_var.set("Syncing missing Anki words..." if missing_only else "Syncing Anki words...")
        settings = {
            "decks": self._var("ANNOTATION_ANKI_DECKS").get(),
            "word_fields": self._var("ANNOTATION_ANKI_WORD_FIELDS").get(),
            "sentence_fields": self._var("ANNOTATION_ANKI_SENTENCE_FIELDS").get(),
            "sentence_translated_fields": self._var("ANNOTATION_ANKI_SENTENCE_TRANSLATED_FIELDS").get(),
            "reading_fields": self._var("ANNOTATION_ANKI_READING_FIELDS").get(),
            "meaning_fields": self._var("ANNOTATION_ANKI_MEANING_FIELDS").get(),
            "include_sentence_words": bool(self._var("ANNOTATION_ANKI_INCLUDE_SENTENCE_WORDS").get()),
            "use_mature_threshold": bool(self._var("ANNOTATION_USE_ANKI_MATURE_THRESHOLD").get()),
            "mature_interval_days": self._var("ANNOTATION_ANKI_MATURE_INTERVAL_DAYS").get(),
            "suspended_as": self._var("ANNOTATION_ANKI_SUSPENDED_AS").get(),
            "missing_only": bool(missing_only),
        }

        def _worker():
            result = self.ui._on_annotation_anki_sync(settings) or {}

            def _apply():
                if not result.get("ok"):
                    self._anki_status_var.set(result.get("error") or "Anki sync failed.")
                    return
                reason_text = self._format_skip_reasons(result.get("skip_reasons"))
                skipped_text = f"{result.get('skipped', 0)} skipped"
                if reason_text:
                    skipped_text = f"{skipped_text} ({reason_text})"
                if result.get("missing_only"):
                    self._anki_status_var.set(
                        f"Last sync {result.get('last_sync')}: {result.get('added', 0)} missing added, "
                        f"{result.get('collected', 0)} scanned, {skipped_text}, {result.get('failed', 0)} failed."
                    )
                else:
                    self._anki_status_var.set(
                        f"Last sync {result.get('last_sync')}: {result.get('collected', 0)} collected, "
                        f"{skipped_text}, {result.get('failed', 0)} failed."
                    )
                self.refresh_words()

            self.ui.root.after(0, _apply)

        threading.Thread(target=_worker, daemon=True, name="annotation-anki-sync").start()

    @staticmethod
    def _format_skip_reasons(reasons) -> str:
        if not isinstance(reasons, dict):
            return ""
        parts = []
        for reason, count in sorted(reasons.items(), key=lambda item: (-int(item[1] or 0), str(item[0]))):
            try:
                count_i = int(count)
            except Exception:
                count_i = 0
            if count_i > 0:
                parts.append(f"{reason}: {count_i}")
        return ", ".join(parts)

    def set_wanikani_token(self) -> None:
        win = tk.Toplevel(self.ui.root)
        win.title("WaniKani API Token")
        win.transient(self.ui.advanced_window or self.ui.root)
        win.grab_set()
        body = tk.Frame(win, padx=10, pady=10)
        body.pack(fill="both", expand=True)
        token_var = tk.StringVar(value=str(self._var("ANNOTATION_WANIKANI_API_TOKEN").get()))
        status_var = tk.StringVar(value="Token is stored in local app settings.")
        tk.Label(body, text="API token").grid(row=0, column=0, sticky="w")
        entry = tk.Entry(body, textvariable=token_var, show="*", width=48)
        entry.grid(row=0, column=1, sticky="ew")
        tk.Label(body, textvariable=status_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        def _test():
            status_var.set("Testing token...")

            def _worker():
                result = self.ui._on_annotation_wanikani_test(token_var.get()) or {}
                self.ui.root.after(0, lambda: status_var.set(result.get("message") or result.get("error") or "Test failed."))

            threading.Thread(target=_worker, daemon=True, name="annotation-wanikani-test").start()

        def _save():
            self._var("ANNOTATION_WANIKANI_API_TOKEN").set(token_var.get().strip())
            self._persist_annotation_values()
            win.destroy()
            self.refresh_sync_status_labels()

        row = 2
        tk.Button(body, text="Save", command=_save).grid(row=row, column=0, sticky="w", pady=(8, 0))
        tk.Button(body, text="Test token", command=_test).grid(row=row, column=1, sticky="w", pady=(8, 0))
        tk.Button(body, text="Cancel", command=win.destroy).grid(row=row, column=1, sticky="e", pady=(8, 0))
        body.grid_columnconfigure(1, weight=1)
        self._center_dialog(win)
        entry.focus_set()

    def _center_dialog(self, win: tk.Toplevel) -> None:
        try:
            win.update_idletasks()
            anchor = self.ui.advanced_window or self.ui.root
            anchor.update_idletasks()
            px = int(anchor.winfo_x() + max(1, anchor.winfo_width()) // 2)
            py = int(anchor.winfo_y() + max(1, anchor.winfo_height()) // 2)
            monitors = list(get_monitor_rects(self.ui.root) or [])
            if not monitors:
                monitors = [(0, 0, int(win.winfo_screenwidth() or 1920), int(win.winfo_screenheight() or 1080))]
            mx, my, mw, mh = monitors[0]
            for rect in monitors:
                x, y, w, h = rect
                if x <= px < x + w and y <= py < y + h:
                    mx, my, mw, mh = rect
                    break
            ww = int(win.winfo_reqwidth())
            wh = int(win.winfo_reqheight())
            x = int(mx + max(0, (mw - ww) // 2))
            y = int(my + max(0, (mh - wh) // 2))
            win.geometry(f"{ww}x{wh}+{x}+{y}")
        except Exception:
            logger.debug("Failed to center annotation dialog", exc_info=True)

    def sync_wanikani(self) -> None:
        self._persist_annotation_values()
        self._wanikani_status_var.set("Syncing WaniKani...")
        token = str(self._var("ANNOTATION_WANIKANI_API_TOKEN").get())

        def _worker():
            result = self.ui._on_annotation_wanikani_sync(token) or {}

            def _apply():
                if not result.get("ok"):
                    self._wanikani_status_var.set(result.get("error") or "WaniKani sync failed.")
                    return
                self._wanikani_status_var.set(
                    f"Last sync {result.get('last_sync')}: {result.get('vocabulary_count', 0)} vocab, "
                    f"{result.get('kanji_count', 0)} kanji."
                )
                self.refresh_words()

            self.ui.root.after(0, _apply)

        threading.Thread(target=_worker, daemon=True, name="annotation-wanikani-sync").start()

    def clear_wanikani(self) -> None:
        result = self.ui._on_annotation_wanikani_clear() or {}
        if not result.get("ok"):
            self._wanikani_status_var.set(result.get("error") or "Clear failed.")
        else:
            self._wanikani_status_var.set(f"Removed {result.get('removed', 0)} WaniKani entries.")
            self.refresh_words()

    def refresh_sync_status_labels(self) -> None:
        self._anki_status_var.set(
            f"Last sync {self.ui.config.get('ANNOTATION_ANKI_LAST_SYNC') or 'never'}: "
            f"{self.ui.config.get('ANNOTATION_ANKI_LAST_COUNT') or 0} collected."
        )
        token_state = "configured" if str(self._var("ANNOTATION_WANIKANI_API_TOKEN").get()).strip() else "not configured"
        self._wanikani_status_var.set(
            f"Token {token_state}. Last sync {self.ui.config.get('ANNOTATION_WANIKANI_LAST_SYNC') or 'never'}: "
            f"{self.ui.config.get('ANNOTATION_WANIKANI_VOCAB_COUNT') or 0} vocab, "
            f"{self.ui.config.get('ANNOTATION_WANIKANI_KANJI_COUNT') or 0} kanji."
        )

    def _style_config(self) -> dict:
        return load_style_config(self.ui.config.get("ANNOTATION_STYLES"))

    def _selected_style_status(self) -> str:
        selected = str(self._style_status_var.get() or "")
        return self._status_label_to_key.get(selected, selected if selected in STATUS_ORDER else "local_known")

    def load_style_vars(self) -> None:
        status = self._selected_style_status()
        label = STATUS_LABELS.get(status, status)
        if self._style_status_var.get() != label:
            self._style_status_var.set(label)
        style = self._style_config().get(status, DEFAULT_STYLES.get(status, {}))
        for key, var in self._style_vars.items():
            value = style.get(key)
            if key == "show_ruby":
                var.set("auto" if value is None else ("true" if bool(value) else "false"))
            elif isinstance(var, tk.BooleanVar):
                var.set(bool(value))
            else:
                var.set("" if value is None else str(value))

    def save_current_style(self) -> None:
        status = self._selected_style_status()
        styles = self._style_config()
        style = dict(styles.get(status, DEFAULT_STYLES.get(status, {})))
        for key, var in self._style_vars.items():
            if key == "show_ruby":
                raw = str(var.get()).strip().lower()
                style[key] = None if raw == "auto" else raw == "true"
            elif isinstance(var, tk.BooleanVar):
                style[key] = bool(var.get())
            elif key.endswith("_thickness"):
                try:
                    style[key] = int(float(var.get()))
                except Exception:
                    style[key] = 1
            elif key == "background_alpha":
                try:
                    style[key] = float(var.get())
                except Exception:
                    style[key] = 0.35
            else:
                style[key] = str(var.get()).strip()
        styles[status] = style
        self.ui.config.set("ANNOTATION_STYLES", dump_style_config(styles))
        self.ui._on_advanced_apply({"ANNOTATION_STYLES": dump_style_config(styles)}, True)
        self._set_status(f"Saved style for {STATUS_LABELS.get(status, status)}.")

    def reset_current_style(self) -> None:
        status = self._selected_style_status()
        styles = self._style_config()
        styles[status] = dict(DEFAULT_STYLES.get(status, {}))
        self.ui.config.set("ANNOTATION_STYLES", dump_style_config(styles))
        self.load_style_vars()
        self.ui._on_advanced_apply({"ANNOTATION_STYLES": dump_style_config(styles)}, True)

    def _annotation_values_from_vars(self) -> dict:
        values = {}
        for spec in ANNOTATION_SPECS:
            if spec.get("hidden"):
                continue
            key = spec["key"]
            var = self.ui._advanced_vars.get(key)
            if var is None:
                continue
            if spec["type"] == "bool":
                values[key] = bool(var.get())
            elif spec["type"] == "int":
                try:
                    value = int(float(str(var.get()).strip() or spec.get("default", 0)))
                except Exception:
                    value = int(spec.get("default", 0))
                if spec.get("min") is not None:
                    value = max(int(spec["min"]), value)
                if spec.get("max") is not None:
                    value = min(int(spec["max"]), value)
                values[key] = value
                var.set(str(value))
            else:
                text = str(var.get()).strip()
                if not text and not bool(spec.get("allow_empty", False)):
                    text = str(spec.get("default", ""))
                    var.set(text)
                values[key] = text
        return values

    def _persist_annotation_values(self) -> None:
        values = self._annotation_values_from_vars()
        self.ui._on_advanced_apply(dict(values), True)
        self.ui.config.set_many(values)

    def _set_status(self, text: str) -> None:
        status = getattr(self.ui, "_advanced_status_var", None)
        if status is not None:
            status.set(text)
