"""Advanced Settings tab for offline voice commands."""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from model.voice_commands import (
    DEFAULT_WAKE_PREFIXES,
    VOICE_ACTION_GROUPS,
    VOICE_ACTIONS,
    default_voice_commands,
    merge_voice_commands,
    merge_wake_prefixes,
    normalize_voice_phrase,
    split_voice_aliases,
    voice_alias_conflicts,
)
from utils import dispatch_to_tk

logger = logging.getLogger(__name__)


VOICE_CONFIG_KEYS = [
    "VOICE_ENABLED",
    "VOICE_LANGUAGE",
    "VOICE_INPUT_DEVICE",
    "VOICE_REQUIRE_WAKE_PREFIX",
    "VOICE_WAKE_PREFIXES",
    "VOICE_COMMAND_COOLDOWN_MS",
    "VOICE_COLLAPSED_SECTIONS",
    "VOICE_HIGHLIGHT_AUTO_DICTIONARY",
    "VOICE_HIGHLIGHT_AUTO_TRANSLATION",
    "VOICE_HIGHLIGHT_AUTO_STATUS",
    "VOICE_ACTION_INDICATOR_ENABLED",
    "VOICE_COMMANDS",
]


class _VoiceTooltip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = str(text or "").strip()
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.window is not None or not self.text:
            return
        try:
            win = tk.Toplevel(self.widget)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            x = int(self.widget.winfo_rootx()) + 18
            y = int(self.widget.winfo_rooty()) + int(self.widget.winfo_height()) + 5
            tk.Label(
                win,
                text=self.text,
                justify="left",
                background="#ffffe0",
                relief="solid",
                borderwidth=1,
                padx=6,
                pady=4,
                wraplength=460,
            ).pack()
            win.geometry(f"+{x}+{y}")
            self.window = win
        except Exception:
            self.window = None

    def _hide(self, _event=None) -> None:
        win, self.window = self.window, None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass


class VoiceTab:
    def __init__(self, settings_ui: Any, parent: tk.Frame, tab_id: str) -> None:
        self.ui = settings_ui
        self.parent = parent
        self.tab_id = str(tab_id)
        self.enabled_var = tk.BooleanVar(value=False)
        self.require_prefix_var = tk.BooleanVar(value=True)
        self.language_var = tk.StringVar(value="English")
        self.device_var = tk.StringVar(value="System default")
        self.prefix_en_var = tk.StringVar(value=DEFAULT_WAKE_PREFIXES["en"])
        self.prefix_de_var = tk.StringVar(value=DEFAULT_WAKE_PREFIXES["de"])
        self.cooldown_var = tk.StringVar(value="1000")
        self.window_var = tk.StringVar(value="")
        self.target_title_var = tk.StringVar(value="")
        self.target_process_var = tk.StringVar(value="")
        self.playback_hotkey_var = tk.StringVar(value="space")
        self.playback_back_hotkey_var = tk.StringVar(value="left")
        self.playback_forward_hotkey_var = tk.StringVar(value="right")
        self.repeat_hotkey_interval_var = tk.StringVar(value="120")
        self.highlight_dictionary_var = tk.BooleanVar(value=False)
        self.highlight_translation_var = tk.BooleanVar(value=False)
        self.highlight_status_var = tk.BooleanVar(value=False)
        self.action_indicator_var = tk.BooleanVar(value=True)
        self.model_status_var = tk.StringVar(value="")
        self.monitor_var = tk.BooleanVar(value=True)
        self._device_mapping: dict[str, dict] = {}
        self._window_mapping: dict[str, dict] = {}
        self._saved_device: dict = {"name": "", "host_api": ""}
        self._saved_target: dict = {"title_filter": "", "process": "", "class_name": ""}
        self._command_vars: dict[str, dict[str, tk.Variable]] = {}
        self._command_widgets: dict[str, list[tk.Widget]] = {}
        self._command_entry_backgrounds: dict[tk.Widget, str] = {}
        self._status_lines: list[str] = []
        self._status_text: tk.Text | None = None
        self._model_action_btn: tk.Button | None = None
        self._model_remove_btn: tk.Button | None = None
        self._model_download_active = False
        self._mic_test_active = False
        self._mic_test_button: tk.Button | None = None
        self._mic_monitor_check: tk.Checkbutton | None = None
        self._device_refresh_btn: tk.Button | None = None
        self._mic_meter: tk.Canvas | None = None
        self._mic_meter_items: list[int] = []
        self._mic_meter_level = 0.0
        self._collapsed_sections: dict[str, bool] = {}
        self._section_bodies: dict[str, tk.Frame] = {}
        self._section_toggles: dict[str, tk.Button] = {}
        self._section_titles: dict[str, str] = {}
        self._advanced_filter_trace_id: str | None = None
        self._destroyed = False
        self._register_tab_keys()
        self._build()
        self.parent.bind("<Destroy>", self._on_parent_destroy, add="+")
        self.load_values()
        self.refresh_devices()

    def _on_parent_destroy(self, event) -> None:
        if event.widget is self.parent:
            if self._mic_test_active:
                try:
                    self.ui._on_voice_stop_microphone_test()
                except Exception:
                    pass
            filter_var = getattr(self.ui, "_advanced_filter_var", None)
            if filter_var is not None and self._advanced_filter_trace_id:
                try:
                    filter_var.trace_remove("write", self._advanced_filter_trace_id)
                except Exception:
                    pass
                self._advanced_filter_trace_id = None
            self._destroyed = True

    def _alive(self) -> bool:
        if self._destroyed:
            return False
        try:
            return bool(self.parent.winfo_exists())
        except Exception:
            return False

    def _register_tab_keys(self) -> None:
        keys = self.ui._advanced_tab_key_map.setdefault(self.tab_id, [])
        for key in VOICE_CONFIG_KEYS:
            if key not in keys:
                keys.append(key)

    def _section(
        self,
        parent: tk.Widget,
        title: str,
        *,
        section_id: str,
    ) -> tuple[tk.Frame, dict]:
        section = tk.Frame(parent, borderwidth=1, relief="groove")
        pack_info = {"fill": "x", "pady": (0, 10)}
        section.pack(**pack_info)
        toggle = tk.Button(
            section,
            text=f"- {title}",
            anchor="w",
            relief="flat",
            borderwidth=0,
            padx=8,
            pady=5,
            command=lambda current_id=section_id: self._toggle_section(current_id),
        )
        toggle.pack(fill="x")
        body = tk.Frame(section, padx=10, pady=8)
        body.pack(fill="x")
        info = {
            "parent": parent,
            "tab_id": self.tab_id,
            "section": section,
            "section_text": title.casefold(),
            "pack": pack_info,
            "rows": [],
        }
        self.ui._advanced_filter_sections.append(info)
        self._collapsed_sections.setdefault(section_id, False)
        self._section_bodies[section_id] = body
        self._section_toggles[section_id] = toggle
        self._section_titles[section_id] = title
        return body, info

    def _toggle_section(self, section_id: str) -> None:
        if section_id not in self._section_bodies:
            return
        self._collapsed_sections[section_id] = not bool(self._collapsed_sections.get(section_id, False))
        try:
            self.ui.config.set("VOICE_COLLAPSED_SECTIONS", self._collapsed_section_values())
        except Exception:
            logger.debug("Failed to persist Voice collapsed-section state", exc_info=True)
        self._apply_collapsed_section_states()

    def _collapsed_section_values(self) -> dict[str, bool]:
        return {
            section_id: bool(self._collapsed_sections.get(section_id, False))
            for section_id in self._section_bodies
        }

    def _apply_collapsed_section_states(self, *_args) -> None:
        filter_var = getattr(self.ui, "_advanced_filter_var", None)
        try:
            filter_active = bool(str(filter_var.get() if filter_var is not None else "").strip())
        except Exception:
            filter_active = False
        for section_id, body in self._section_bodies.items():
            toggle = self._section_toggles.get(section_id)
            title = self._section_titles.get(section_id, section_id)
            if toggle is None:
                continue
            collapsed = bool(self._collapsed_sections.get(section_id, False)) and not filter_active
            try:
                toggle.configure(text=f"{'+' if collapsed else '-'} {title}")
                if collapsed:
                    body.pack_forget()
                elif not body.winfo_manager():
                    body.pack(fill="x")
            except Exception:
                continue
        try:
            self.ui.adv_settings._refresh_advanced_scroll_regions()
        except Exception:
            pass

    @staticmethod
    def _add_filter_row(info: dict, widgets: list[tk.Widget], text: str) -> None:
        info["rows"].append({"widgets": widgets, "text": str(text or "").casefold()})

    def _build(self) -> None:
        content = self.ui.adv_settings._create_scrollable_advanced_tab(self.parent)
        body = tk.Frame(content)
        body.pack(fill="x", expand=True, anchor="n")
        body.grid_columnconfigure(0, weight=1)

        recognition, recognition_info = self._section(body, "Recognition", section_id="recognition")
        recognition.grid_columnconfigure(1, weight=1)
        enabled = tk.Checkbutton(recognition, text="Enable offline voice commands", variable=self.enabled_var)
        enabled.grid(row=0, column=0, sticky="w", pady=2)
        require_prefix = tk.Checkbutton(
            recognition,
            text="Require wake prefix",
            variable=self.require_prefix_var,
        )
        require_prefix.grid(row=0, column=1, columnspan=2, sticky="w", padx=(8, 0), pady=2)
        self._add_filter_row(
            recognition_info,
            [enabled, require_prefix],
            "enable offline voice commands microphone listening require wake prefix optional",
        )

        language_label = tk.Label(recognition, text="Recognition language")
        language_label.grid(row=1, column=0, sticky="w", pady=2)
        language = ttk.Combobox(
            recognition,
            textvariable=self.language_var,
            values=("English", "German"),
            state="readonly",
            width=18,
        )
        language.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)
        language.bind("<<ComboboxSelected>>", lambda _event: self.refresh_model_status())
        self._add_filter_row(recognition_info, [language_label, language], "recognition language english german")

        device_label = tk.Label(recognition, text="Microphone")
        device_label.grid(row=2, column=0, sticky="w", pady=2)
        device = ttk.Combobox(recognition, textvariable=self.device_var, state="readonly", width=50)
        device.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=2)
        refresh_device = tk.Button(recognition, text="Refresh", width=10, command=self.refresh_devices)
        refresh_device.grid(row=2, column=2, sticky="e", padx=(6, 0), pady=2)
        self._device_combo = device
        self._device_refresh_btn = refresh_device
        self._add_filter_row(recognition_info, [device_label, device, refresh_device], "microphone input device refresh")

        prefix_en_label = tk.Label(recognition, text="English wake prefix")
        prefix_en_label.grid(row=3, column=0, sticky="w", pady=2)
        prefix_en = tk.Entry(recognition, textvariable=self.prefix_en_var, width=30)
        prefix_en.grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=2)
        self._add_filter_row(recognition_info, [prefix_en_label, prefix_en], "english wake prefix subtitles")

        prefix_de_label = tk.Label(recognition, text="German wake prefix")
        prefix_de_label.grid(row=4, column=0, sticky="w", pady=2)
        prefix_de = tk.Entry(recognition, textvariable=self.prefix_de_var, width=30)
        prefix_de.grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=2)
        self._add_filter_row(recognition_info, [prefix_de_label, prefix_de], "german wake prefix untertitel")

        cooldown_label = tk.Label(recognition, text="Duplicate cooldown (ms)")
        cooldown_label.grid(row=5, column=0, sticky="w", pady=2)
        cooldown = tk.Entry(recognition, textvariable=self.cooldown_var, width=12)
        cooldown.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=2)
        self._add_filter_row(recognition_info, [cooldown_label, cooldown], "duplicate cooldown milliseconds")

        model_label = tk.Label(recognition, text="Speech model")
        model_label.grid(row=6, column=0, sticky="nw", pady=(6, 2))
        model_status = tk.Label(recognition, textvariable=self.model_status_var, anchor="w", justify="left", wraplength=520)
        model_status.grid(row=6, column=1, sticky="ew", padx=(8, 0), pady=(6, 2))
        model_buttons = tk.Frame(recognition)
        model_buttons.grid(row=6, column=2, sticky="e", padx=(6, 0), pady=(6, 2))
        self._model_action_btn = tk.Button(model_buttons, text="Download", width=10, command=self._download_or_cancel_model)
        self._model_action_btn.pack(side="left")
        self._model_remove_btn = tk.Button(model_buttons, text="Remove", width=8, command=self.remove_model)
        self._model_remove_btn.pack(side="left", padx=(4, 0))
        self._add_filter_row(
            recognition_info,
            [model_label, model_status, model_buttons],
            "speech model download remove english german vosk offline",
        )

        test_label = tk.Label(recognition, text="Microphone test")
        test_label.grid(row=7, column=0, sticky="w", pady=(6, 2))
        test_controls = tk.Frame(recognition)
        test_controls.grid(row=7, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(6, 2))
        self._mic_test_button = tk.Button(
            test_controls,
            text="Start Test",
            width=12,
            command=self.test_microphone,
        )
        self._mic_test_button.pack(side="left")
        self._mic_monitor_check = tk.Checkbutton(
            test_controls,
            text="Hear microphone",
            variable=self.monitor_var,
            command=self._on_monitor_changed,
        )
        self._mic_monitor_check.pack(side="left", padx=(10, 0))
        self._add_filter_row(
            recognition_info,
            [test_label, test_controls],
            "start stop test microphone hear monitor playback input signal",
        )

        meter_label = tk.Label(recognition, text="Input level")
        meter_label.grid(row=8, column=0, sticky="w", pady=(2, 0))
        self._mic_meter = tk.Canvas(
            recognition,
            width=360,
            height=18,
            highlightthickness=0,
            borderwidth=0,
            background=str(recognition.cget("background")),
        )
        self._mic_meter.grid(row=8, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(2, 0))
        self._mic_meter_items = [self._mic_meter.create_rectangle(0, 0, 0, 0, outline="") for _ in range(24)]
        self._mic_meter.bind("<Configure>", self._layout_microphone_meter, add="+")
        self._add_filter_row(recognition_info, [meter_label, self._mic_meter], "microphone input level volume meter bars")

        highlighted, highlighted_info = self._section(
            body,
            "Highlighted Words",
            section_id="highlighted_words",
        )
        automatic_label = tk.Label(highlighted, text="Show automatically after highlighting:")
        automatic_label.grid(row=0, column=0, sticky="w", padx=(0, 12), pady=2)
        auto_dictionary = tk.Checkbutton(
            highlighted,
            text="Dictionary",
            variable=self.highlight_dictionary_var,
        )
        auto_dictionary.grid(row=0, column=1, sticky="w", padx=(0, 12), pady=2)
        auto_translation = tk.Checkbutton(
            highlighted,
            text="Translation",
            variable=self.highlight_translation_var,
        )
        auto_translation.grid(row=0, column=2, sticky="w", padx=(0, 12), pady=2)
        auto_status = tk.Checkbutton(
            highlighted,
            text="Anki/database Status",
            variable=self.highlight_status_var,
        )
        auto_status.grid(row=0, column=3, sticky="w", pady=2)
        self._add_filter_row(
            highlighted_info,
            [automatic_label, auto_dictionary, auto_translation, auto_status],
            "highlighted words automatic dictionary translation anki database status display",
        )
        action_indicator = tk.Checkbutton(
            highlighted,
            text="Show add/capture activity in subtitle window",
            variable=self.action_indicator_var,
        )
        action_indicator.grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 2))
        self._add_filter_row(
            highlighted_info,
            [action_indicator],
            "add capture activity spinner wait success check subtitle window indicator",
        )

        for group_name, group_actions in VOICE_ACTION_GROUPS.items():
            group_id = "_".join(
                part for part in "".join(
                    character if character.isalnum() else " "
                    for character in group_name.casefold()
                ).split()
            )
            commands, commands_info = self._section(
                body,
                f"{group_name} Commands",
                section_id=f"commands_{group_id}",
            )
            commands.grid_columnconfigure(2, weight=1)
            commands.grid_columnconfigure(3, weight=1)
            tk.Label(commands, text="Enabled", font=("Arial", 9, "bold")).grid(
                row=0, column=0, sticky="w", padx=(0, 8)
            )
            tk.Label(commands, text="Action", font=("Arial", 9, "bold")).grid(
                row=0, column=1, sticky="w", padx=(0, 8)
            )
            tk.Label(commands, text="English aliases (; separated)", font=("Arial", 9, "bold")).grid(
                row=0, column=2, sticky="w", padx=(0, 8)
            )
            tk.Label(commands, text="German aliases (; separated)", font=("Arial", 9, "bold")).grid(
                row=0, column=3, sticky="w"
            )
            row = 1
            for action in group_actions:
                spec = VOICE_ACTIONS.get(action)
                if not isinstance(spec, dict):
                    continue
                enabled_var = tk.BooleanVar(value=True)
                en_var = tk.StringVar(value="; ".join(spec["en"]))
                de_var = tk.StringVar(value="; ".join(spec["de"]))
                self._command_vars[action] = {"enabled": enabled_var, "en": en_var, "de": de_var}
                check = tk.Checkbutton(commands, variable=enabled_var)
                check.grid(row=row, column=0, sticky="w", pady=2)
                label = tk.Label(commands, text=str(spec["label"]), anchor="w", width=28)
                label.grid(row=row, column=1, sticky="w", padx=(0, 8), pady=2)
                en_entry = tk.Entry(commands, textvariable=en_var, width=32)
                en_entry.grid(row=row, column=2, sticky="ew", padx=(0, 8), pady=2)
                de_entry = tk.Entry(commands, textvariable=de_var, width=32)
                de_entry.grid(row=row, column=3, sticky="ew", pady=2)
                widgets = [check, label, en_entry, de_entry]
                self._command_widgets[action] = widgets
                self._command_entry_backgrounds[en_entry] = str(en_entry.cget("background"))
                self._command_entry_backgrounds[de_entry] = str(de_entry.cget("background"))
                tooltip = str(spec.get("tooltip") or "").strip()
                for widget in widgets:
                    _VoiceTooltip(widget, tooltip)
                search_text = (
                    f"{group_name} {spec['label']} {action} {' '.join(spec['en'])} "
                    f"{' '.join(spec['de'])} {tooltip}"
                )
                self._add_filter_row(commands_info, widgets, search_text)
                row += 1

        status, status_info = self._section(body, "Status", section_id="status")
        status_body = tk.Frame(status)
        status_body.pack(fill="both", expand=True)
        status_scroll = ttk.Scrollbar(status_body, orient="vertical")
        status_scroll.pack(side="right", fill="y")
        self._status_text = tk.Text(
            status_body,
            height=5,
            wrap="word",
            state="disabled",
            takefocus=False,
            yscrollcommand=status_scroll.set,
        )
        self._status_text.pack(side="left", fill="both", expand=True)
        status_scroll.configure(command=self._status_text.yview)

        def _scroll_status(event) -> str:
            units = -1 if int(getattr(event, "delta", 0) or 0) > 0 else 1
            self._status_text.yview_scroll(units * 3, "units")
            return "break"

        self._status_text.bind("<MouseWheel>", _scroll_status, add="+")
        self._add_filter_row(
            status_info,
            [status_body],
            "status listening recognized model microphone error scroll history",
        )

        filter_var = getattr(self.ui, "_advanced_filter_var", None)
        if filter_var is not None:
            try:
                self._advanced_filter_trace_id = filter_var.trace_add(
                    "write",
                    self._apply_collapsed_section_states,
                )
            except Exception:
                self._advanced_filter_trace_id = None

        try:
            body.update_idletasks()
            min_width = int(body.winfo_reqwidth())
            content._advanced_min_width = min_width
            self.ui._advanced_tab_min_widths[self.tab_id] = min_width
        except Exception:
            logger.debug("Failed to measure Voice tab", exc_info=True)

    @staticmethod
    def _language_code(value: str) -> str:
        return "de" if str(value).strip().casefold().startswith("german") else "en"

    @staticmethod
    def _device_label(device: dict) -> str:
        name = str(device.get("name") or "").strip()
        host = str(device.get("host_api") or "").strip()
        if not name:
            return "System default"
        return f"{name} - {host}" if host else name


    def load_values(self) -> None:
        self.enabled_var.set(bool(self.ui.config.get("VOICE_ENABLED") or False))
        self.require_prefix_var.set(self.ui.config.get("VOICE_REQUIRE_WAKE_PREFIX") is not False)
        language = "de" if str(self.ui.config.get("VOICE_LANGUAGE") or "en").lower() == "de" else "en"
        self.language_var.set("German" if language == "de" else "English")
        self._saved_device = self.ui.config.get("VOICE_INPUT_DEVICE") or {"name": "", "host_api": ""}
        self.device_var.set(self._device_label(self._saved_device))
        prefixes = merge_wake_prefixes(self.ui.config.get("VOICE_WAKE_PREFIXES"))
        self.prefix_en_var.set(prefixes["en"])
        self.prefix_de_var.set(prefixes["de"])
        try:
            cooldown = int(self.ui.config.get("VOICE_COMMAND_COOLDOWN_MS") or 1000)
        except Exception:
            cooldown = 1000
        self.cooldown_var.set(str(max(0, min(60000, cooldown))))
        configured_sections = self.ui.config.get("VOICE_COLLAPSED_SECTIONS")
        configured_sections = configured_sections if isinstance(configured_sections, dict) else {}
        self._collapsed_sections = {
            section_id: bool(configured_sections.get(section_id, False))
            for section_id in self._section_bodies
        }
        if "video_playback" not in configured_sections:
            self._collapsed_sections["video_playback"] = bool(
                self.ui.config.get("VOICE_VIDEO_PLAYBACK_COLLAPSED") or False
            )
        self._apply_collapsed_section_states()
        self.highlight_dictionary_var.set(bool(self.ui.config.get("VOICE_HIGHLIGHT_AUTO_DICTIONARY") or False))
        self.highlight_translation_var.set(bool(self.ui.config.get("VOICE_HIGHLIGHT_AUTO_TRANSLATION") or False))
        self.highlight_status_var.set(bool(self.ui.config.get("VOICE_HIGHLIGHT_AUTO_STATUS") or False))
        self.action_indicator_var.set(self.ui.config.get("VOICE_ACTION_INDICATOR_ENABLED") is not False)
        commands = merge_voice_commands(self.ui.config.get("VOICE_COMMANDS"))
        for action, values in commands.items():
            variables = self._command_vars[action]
            variables["enabled"].set(bool(values.get("enabled")))
            variables["en"].set("; ".join(values.get("en") or []))
            variables["de"].set("; ".join(values.get("de") or []))
        self._clear_conflict_highlights()
        self.refresh_model_status()
        current_status = getattr(self.ui, "_voice_status", None)
        if isinstance(current_status, dict):
            self.set_status(current_status)

    def collect_values(self) -> dict:
        errors: list[str] = []
        try:
            cooldown = int(float(str(self.cooldown_var.get()).strip().replace(",", ".")))
        except Exception:
            cooldown = 1000
            errors.append("Duplicate cooldown")
        cooldown = max(0, min(60000, cooldown))
        self.cooldown_var.set(str(cooldown))
        prefixes = {
            "en": normalize_voice_phrase(self.prefix_en_var.get()),
            "de": normalize_voice_phrase(self.prefix_de_var.get()),
        }
        commands = default_voice_commands()
        for action, variables in self._command_vars.items():
            commands[action] = {
                "enabled": bool(variables["enabled"].get()),
                "en": split_voice_aliases(variables["en"].get()),
                "de": split_voice_aliases(variables["de"].get()),
            }
        require_prefix = bool(self.require_prefix_var.get())
        conflicts = voice_alias_conflicts(commands, prefixes, require_prefix=require_prefix)
        self._show_conflicts(conflicts)
        if conflicts:
            descriptions = []
            for language, phrases in conflicts.items():
                for phrase, actions in phrases.items():
                    descriptions.append(f"{language}: {phrase} ({', '.join(actions)})")
            errors.append("Voice alias conflicts: " + "; ".join(descriptions[:3]))

        device = self._device_mapping.get(self.device_var.get())
        if device is None:
            device = dict(self._saved_device or {})
        selected_device = {
            "name": str(device.get("name") or ""),
            "host_api": str(device.get("host_api") or ""),
        }
        values = {
            "VOICE_ENABLED": bool(self.enabled_var.get()),
            "VOICE_LANGUAGE": self._language_code(self.language_var.get()),
            "VOICE_INPUT_DEVICE": selected_device,
            "VOICE_REQUIRE_WAKE_PREFIX": require_prefix,
            "VOICE_WAKE_PREFIXES": prefixes,
            "VOICE_COMMAND_COOLDOWN_MS": cooldown,
            "VOICE_COLLAPSED_SECTIONS": self._collapsed_section_values(),
            "VOICE_HIGHLIGHT_AUTO_DICTIONARY": bool(self.highlight_dictionary_var.get()),
            "VOICE_HIGHLIGHT_AUTO_TRANSLATION": bool(self.highlight_translation_var.get()),
            "VOICE_HIGHLIGHT_AUTO_STATUS": bool(self.highlight_status_var.get()),
            "VOICE_ACTION_INDICATOR_ENABLED": bool(self.action_indicator_var.get()),
            "VOICE_COMMANDS": commands,
        }
        return {"values": values, "errors": errors}

    def reset_defaults(self) -> None:
        self.enabled_var.set(False)
        self.require_prefix_var.set(True)
        self.language_var.set("English")
        self._saved_device = {"name": "", "host_api": ""}
        self.device_var.set("System default")
        self.prefix_en_var.set(DEFAULT_WAKE_PREFIXES["en"])
        self.prefix_de_var.set(DEFAULT_WAKE_PREFIXES["de"])
        self.cooldown_var.set("1000")
        self._collapsed_sections = {section_id: False for section_id in self._section_bodies}
        self._apply_collapsed_section_states()
        self.highlight_dictionary_var.set(False)
        self.highlight_translation_var.set(False)
        self.highlight_status_var.set(False)
        self.action_indicator_var.set(True)
        commands = default_voice_commands()
        for action, values in commands.items():
            variables = self._command_vars[action]
            variables["enabled"].set(True)
            variables["en"].set("; ".join(values["en"]))
            variables["de"].set("; ".join(values["de"]))
        self._clear_conflict_highlights()
        self.refresh_model_status()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled_var.set(bool(enabled))


    def _clear_conflict_highlights(self) -> None:
        for entry, background in self._command_entry_backgrounds.items():
            try:
                entry.configure(background=background)
            except Exception:
                pass

    def _show_conflicts(self, conflicts: dict) -> None:
        self._clear_conflict_highlights()
        for language, phrases in conflicts.items():
            index = 2 if language == "en" else 3
            actions = {action for action_list in phrases.values() for action in action_list}
            for action in actions:
                widgets = self._command_widgets.get(action) or []
                if len(widgets) > index:
                    try:
                        widgets[index].configure(background="#ffd6d6")
                    except Exception:
                        pass

    def refresh_devices(self) -> None:
        self._append_status("Refreshing microphone list...")

        def _worker() -> None:
            try:
                devices = list(self.ui._on_voice_list_devices() or [])
                error = ""
            except Exception as exc:
                devices = []
                error = str(exc)

            def _finish() -> None:
                if not self._alive():
                    return
                self._device_mapping = {"System default": {"name": "", "host_api": ""}}
                for device in devices:
                    label = self._device_label(device)
                    unique = label
                    suffix = 2
                    while unique in self._device_mapping:
                        unique = f"{label} ({suffix})"
                        suffix += 1
                    self._device_mapping[unique] = device
                self._device_combo.configure(values=list(self._device_mapping))
                desired = self._device_label(self._saved_device)
                if desired in self._device_mapping:
                    self.device_var.set(desired)
                elif str(self._saved_device.get("name") or "").strip():
                    self.device_var.set(desired)
                    self._append_status(f"Saved microphone is unavailable: {desired}")
                else:
                    self.device_var.set("System default")
                if error:
                    self._append_status(f"Microphone refresh failed: {error}")

            dispatch_to_tk(self.ui.root, _finish)

        threading.Thread(target=_worker, daemon=True, name="voice-device-list").start()



    def refresh_model_status(self) -> None:
        language = self._language_code(self.language_var.get())
        try:
            status = dict(self.ui._on_voice_model_status(language) or {})
        except Exception as exc:
            self.model_status_var.set(f"Model status unavailable: {exc}")
            return
        installed = bool(status.get("installed"))
        name = str(status.get("name") or language)
        if installed:
            self.model_status_var.set(f"{name} model installed.\n{status.get('path', '')}")
        else:
            self.model_status_var.set(
                f"{name} model not installed (approximately {status.get('download_size_mb', '?')} MB)."
            )
        if self._model_remove_btn is not None:
            self._model_remove_btn.configure(state=(tk.NORMAL if installed else tk.DISABLED))

    def _download_or_cancel_model(self) -> None:
        if self._model_download_active:
            self.ui._on_voice_cancel_download()
            return
        language = self._language_code(self.language_var.get())
        if not self.ui._on_voice_download_model(language):
            self._append_status("Another model download is already running.")

    def remove_model(self) -> None:
        language = self._language_code(self.language_var.get())
        label = "German" if language == "de" else "English"
        if not messagebox.askyesno("Remove Voice Model", f"Remove the downloaded {label} voice model?", parent=self.parent):
            return
        try:
            self.ui._on_voice_remove_model(language)
        except Exception as exc:
            self._append_status(f"Could not remove the {label} voice model: {exc}")
        finally:
            self.refresh_model_status()

    def _on_monitor_changed(self) -> None:
        try:
            self.ui._on_voice_set_microphone_monitor(bool(self.monitor_var.get()))
        except Exception as exc:
            self._append_status(f"Could not change microphone monitoring: {exc}")

    def test_microphone(self) -> None:
        device = self._device_mapping.get(self.device_var.get(), self._saved_device)
        selected = {"name": str(device.get("name") or ""), "host_api": str(device.get("host_api") or "")}
        was_active = bool(self._mic_test_active)
        started = bool(self.ui._on_voice_test_microphone(selected, bool(self.monitor_var.get())))
        if was_active:
            self.set_microphone_test_stopping()
        elif started:
            self.set_microphone_test_state(True, monitor_available=True)

    def _layout_microphone_meter(self, _event=None) -> None:
        meter = self._mic_meter
        if meter is None:
            return
        try:
            width = max(120, int(meter.winfo_width()))
            height = max(10, int(meter.winfo_height()))
            count = max(1, len(self._mic_meter_items))
            gap = 3
            bar_width = max(2.0, (width - gap * (count - 1)) / float(count))
            for index, item in enumerate(self._mic_meter_items):
                left = index * (bar_width + gap)
                meter.coords(item, left, 1, min(width, left + bar_width), height - 1)
            self.set_microphone_level(self._mic_meter_level, 0.0)
        except Exception:
            logger.debug("Failed to lay out microphone level meter", exc_info=True)

    def set_microphone_level(self, level: float, peak: float = 0.0) -> None:
        meter = self._mic_meter
        if meter is None or not self._alive():
            return
        try:
            level = max(0.0, min(1.0, float(level)))
            peak = max(0.0, min(1.0, float(peak)))
        except Exception:
            level = 0.0
            peak = 0.0
        self._mic_meter_level = level
        visible_level = max(level, peak * 0.85)
        active_count = int(round(visible_level * len(self._mic_meter_items)))
        for index, item in enumerate(self._mic_meter_items):
            if index >= active_count:
                color = "#c9cdd2"
            elif index >= int(len(self._mic_meter_items) * 0.88):
                color = "#d83c3e"
            elif index >= int(len(self._mic_meter_items) * 0.68):
                color = "#d89b18"
            else:
                color = "#3ba55d"
            try:
                meter.itemconfigure(item, fill=color)
            except Exception:
                pass

    def set_microphone_test_state(self, active: bool, *, monitor_available: bool = True) -> None:
        self._mic_test_active = bool(active)
        button = self._mic_test_button
        if button is not None:
            button.configure(
                text="Stop Test" if active else "Start Test",
                state=tk.NORMAL,
            )
        monitor = self._mic_monitor_check
        if monitor is not None:
            monitor.configure(state=(tk.NORMAL if monitor_available else tk.DISABLED))
        try:
            self._device_combo.configure(state=("disabled" if active else "readonly"))
        except Exception:
            pass
        if self._device_refresh_btn is not None:
            self._device_refresh_btn.configure(state=(tk.DISABLED if active else tk.NORMAL))
        if not monitor_available:
            self.monitor_var.set(False)
            self._on_monitor_changed()
        if not active:
            self.set_microphone_level(0.0, 0.0)

    def set_microphone_test_stopping(self) -> None:
        button = self._mic_test_button
        if button is not None:
            button.configure(text="Stopping...", state=tk.DISABLED)

    def set_status(self, status: dict) -> None:
        if not self._alive():
            return
        state = str((status or {}).get("state") or "")
        message = str((status or {}).get("message") or "").strip()
        if message:
            self._append_status(message)
        self._model_download_active = state == "downloading"
        if self._model_action_btn is not None:
            self._model_action_btn.configure(
                text="Cancel" if self._model_download_active else "Download",
                command=self._download_or_cancel_model,
            )
        if state in {"ready", "disabled", "error", "listening"}:
            self.refresh_model_status()

    def _append_status(self, message: str) -> None:
        message = str(message or "").strip()
        if not message:
            return
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        if self._status_lines and self._status_lines[-1].endswith(message):
            return
        self._status_lines.append(line)
        self._status_lines = self._status_lines[-100:]
        text = self._status_text
        if text is None:
            return
        try:
            text.configure(state="normal")
            text.delete("1.0", tk.END)
            text.insert("1.0", "\n".join(self._status_lines))
            text.configure(state="disabled")
            text.see(tk.END)
        except Exception:
            logger.debug("Failed to update Voice status text", exc_info=True)
