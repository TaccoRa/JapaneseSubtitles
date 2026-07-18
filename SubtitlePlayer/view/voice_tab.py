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
    "VOICE_PLAYBACK_TARGET",
    "VOICE_PLAYBACK_HOTKEY",
    "VOICE_PLAYBACK_BACK_HOTKEY",
    "VOICE_PLAYBACK_FORWARD_HOTKEY",
    "VOICE_REPEAT_HOTKEY_INTERVAL_MS",
    "VOICE_COMMANDS",
]


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
        self._destroyed = False
        self._register_tab_keys()
        self._build()
        self.parent.bind("<Destroy>", self._on_parent_destroy, add="+")
        self.load_values()
        self.refresh_devices()
        self.refresh_windows()

    def _on_parent_destroy(self, event) -> None:
        if event.widget is self.parent:
            if self._mic_test_active:
                try:
                    self.ui._on_voice_stop_microphone_test()
                except Exception:
                    pass
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

    def _section(self, parent: tk.Widget, title: str) -> tuple[tk.LabelFrame, dict]:
        section = tk.LabelFrame(parent, text=title, padx=10, pady=8)
        pack_info = {"fill": "x", "pady": (0, 10)}
        section.pack(**pack_info)
        info = {
            "parent": parent,
            "tab_id": self.tab_id,
            "section": section,
            "section_text": title.casefold(),
            "pack": pack_info,
            "rows": [],
        }
        self.ui._advanced_filter_sections.append(info)
        return section, info

    @staticmethod
    def _add_filter_row(info: dict, widgets: list[tk.Widget], text: str) -> None:
        info["rows"].append({"widgets": widgets, "text": str(text or "").casefold()})

    def _build(self) -> None:
        content = self.ui.adv_settings._create_scrollable_advanced_tab(self.parent)
        body = tk.Frame(content)
        body.pack(fill="x", expand=True, anchor="n")
        body.grid_columnconfigure(0, weight=1)

        recognition, recognition_info = self._section(body, "Recognition")
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

        playback, playback_info = self._section(body, "Video Playback")
        playback.grid_columnconfigure(1, weight=1)
        window_label = tk.Label(playback, text="Available video window (current)")
        window_label.grid(row=0, column=0, sticky="w", pady=2)
        window_combo = ttk.Combobox(playback, textvariable=self.window_var, state="readonly", width=58)
        window_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)
        refresh_windows = tk.Button(playback, text="Refresh", width=10, command=self.refresh_windows)
        refresh_windows.grid(row=0, column=2, sticky="e", padx=(6, 0), pady=2)
        self._window_combo = window_combo
        self._add_filter_row(
            playback_info,
            [window_label, window_combo, refresh_windows],
            "available current open video window target refresh",
        )

        title_label = tk.Label(playback, text="Saved title contains")
        title_label.grid(row=1, column=0, sticky="w", pady=2)
        title = tk.Entry(playback, textvariable=self.target_title_var, width=48)
        title.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=2)
        select = tk.Button(playback, text="Use Selected", width=10, command=self.select_window)
        select.grid(row=1, column=2, sticky="e", padx=(6, 0), pady=2)
        self._add_filter_row(playback_info, [title_label, title, select], "target window title contains select")

        process_label = tk.Label(playback, text="Target process")
        process_label.grid(row=2, column=0, sticky="w", pady=2)
        process = ttk.Combobox(playback, textvariable=self.target_process_var, state="normal", width=24)
        process.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=2)
        self._process_combo = process
        self._add_filter_row(playback_info, [process_label, process], "target process executable selectable optional")

        hotkey_label = tk.Label(playback, text="Play / pause hotkey")
        hotkey_label.grid(row=3, column=0, sticky="w", pady=2)
        hotkey = tk.Entry(playback, textvariable=self.playback_hotkey_var, width=18)
        hotkey.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=2)
        self._add_filter_row(playback_info, [hotkey_label, hotkey], "playback hotkey space video")

        back_hotkey_label = tk.Label(playback, text="Back hotkey")
        back_hotkey_label.grid(row=4, column=0, sticky="w", pady=2)
        back_hotkey = tk.Entry(playback, textvariable=self.playback_back_hotkey_var, width=18)
        back_hotkey.grid(row=4, column=1, sticky="w", padx=(8, 0), pady=2)
        self._add_filter_row(playback_info, [back_hotkey_label, back_hotkey], "back hotkey left video")

        forward_hotkey_label = tk.Label(playback, text="Forward hotkey")
        forward_hotkey_label.grid(row=5, column=0, sticky="w", pady=2)
        forward_hotkey = tk.Entry(playback, textvariable=self.playback_forward_hotkey_var, width=18)
        forward_hotkey.grid(row=5, column=1, sticky="w", padx=(8, 0), pady=2)
        self._add_filter_row(
            playback_info,
            [forward_hotkey_label, forward_hotkey],
            "forward hotkey right video",
        )

        repeat_interval_label = tk.Label(playback, text="Repeated hotkey interval (ms)")
        repeat_interval_label.grid(row=6, column=0, sticky="w", pady=2)
        repeat_interval = tk.Entry(playback, textvariable=self.repeat_hotkey_interval_var, width=18)
        repeat_interval.grid(row=6, column=1, sticky="w", padx=(8, 0), pady=2)
        self._add_filter_row(
            playback_info,
            [repeat_interval_label, repeat_interval],
            "repeated hotkey interval delay pacing back forward milliseconds",
        )

        commands, commands_info = self._section(body, "Commands")
        commands.grid_columnconfigure(2, weight=1)
        commands.grid_columnconfigure(3, weight=1)
        tk.Label(commands, text="Enabled", font=("Arial", 9, "bold")).grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Label(commands, text="Action", font=("Arial", 9, "bold")).grid(row=0, column=1, sticky="w", padx=(0, 8))
        tk.Label(commands, text="English aliases (; separated)", font=("Arial", 9, "bold")).grid(
            row=0, column=2, sticky="w", padx=(0, 8)
        )
        tk.Label(commands, text="German aliases (; separated)", font=("Arial", 9, "bold")).grid(
            row=0, column=3, sticky="w"
        )
        for row, (action, spec) in enumerate(VOICE_ACTIONS.items(), start=1):
            enabled_var = tk.BooleanVar(value=True)
            en_var = tk.StringVar(value="; ".join(spec["en"]))
            de_var = tk.StringVar(value="; ".join(spec["de"]))
            self._command_vars[action] = {"enabled": enabled_var, "en": en_var, "de": de_var}
            check = tk.Checkbutton(commands, variable=enabled_var)
            check.grid(row=row, column=0, sticky="w", pady=2)
            label = tk.Label(commands, text=str(spec["label"]), anchor="w", width=24)
            label.grid(row=row, column=1, sticky="w", padx=(0, 8), pady=2)
            en_entry = tk.Entry(commands, textvariable=en_var, width=32)
            en_entry.grid(row=row, column=2, sticky="ew", padx=(0, 8), pady=2)
            de_entry = tk.Entry(commands, textvariable=de_var, width=32)
            de_entry.grid(row=row, column=3, sticky="ew", pady=2)
            widgets = [check, label, en_entry, de_entry]
            self._command_widgets[action] = widgets
            self._command_entry_backgrounds[en_entry] = str(en_entry.cget("background"))
            self._command_entry_backgrounds[de_entry] = str(de_entry.cget("background"))
            search_text = f"{spec['label']} {action} {' '.join(spec['en'])} {' '.join(spec['de'])}"
            self._add_filter_row(commands_info, widgets, search_text)

        status, status_info = self._section(body, "Status")
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

    @staticmethod
    def _window_label(window: dict) -> str:
        title = str(window.get("title") or "").strip()
        process = str(window.get("process") or "").strip()
        return f"{title} - {process}" if process else title

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
        self._saved_target = self.ui.config.get("VOICE_PLAYBACK_TARGET") or {}
        self.set_playback_target(self._saved_target)
        self.playback_hotkey_var.set(str(self.ui.config.get("VOICE_PLAYBACK_HOTKEY") or "space"))
        self.playback_back_hotkey_var.set(str(self.ui.config.get("VOICE_PLAYBACK_BACK_HOTKEY") or "left"))
        self.playback_forward_hotkey_var.set(str(self.ui.config.get("VOICE_PLAYBACK_FORWARD_HOTKEY") or "right"))
        try:
            repeat_interval = int(self.ui.config.get("VOICE_REPEAT_HOTKEY_INTERVAL_MS") or 120)
        except Exception:
            repeat_interval = 120
        self.repeat_hotkey_interval_var.set(str(max(40, min(1000, repeat_interval))))
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
        try:
            repeat_interval = int(float(str(self.repeat_hotkey_interval_var.get()).strip().replace(",", ".")))
        except Exception:
            repeat_interval = 120
            errors.append("Repeated hotkey interval")
        repeat_interval = max(40, min(1000, repeat_interval))
        self.repeat_hotkey_interval_var.set(str(repeat_interval))
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
        target = {
            "title_filter": str(self.target_title_var.get() or "").strip(),
            "process": str(self.target_process_var.get() or "").strip(),
        }
        values = {
            "VOICE_ENABLED": bool(self.enabled_var.get()),
            "VOICE_LANGUAGE": self._language_code(self.language_var.get()),
            "VOICE_INPUT_DEVICE": selected_device,
            "VOICE_REQUIRE_WAKE_PREFIX": require_prefix,
            "VOICE_WAKE_PREFIXES": prefixes,
            "VOICE_COMMAND_COOLDOWN_MS": cooldown,
            "VOICE_PLAYBACK_TARGET": target,
            "VOICE_PLAYBACK_HOTKEY": str(self.playback_hotkey_var.get() or "").strip(),
            "VOICE_PLAYBACK_BACK_HOTKEY": str(self.playback_back_hotkey_var.get() or "").strip(),
            "VOICE_PLAYBACK_FORWARD_HOTKEY": str(self.playback_forward_hotkey_var.get() or "").strip(),
            "VOICE_REPEAT_HOTKEY_INTERVAL_MS": repeat_interval,
            "VOICE_COMMANDS": commands,
        }
        if not values["VOICE_PLAYBACK_HOTKEY"]:
            errors.append("Play / pause hotkey")
        if not values["VOICE_PLAYBACK_BACK_HOTKEY"]:
            errors.append("Back hotkey")
        if not values["VOICE_PLAYBACK_FORWARD_HOTKEY"]:
            errors.append("Forward hotkey")
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
        self._saved_target = {"title_filter": "", "process": ""}
        self.set_playback_target(self._saved_target)
        self.playback_hotkey_var.set("space")
        self.playback_back_hotkey_var.set("left")
        self.playback_forward_hotkey_var.set("right")
        self.repeat_hotkey_interval_var.set("120")
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

    def set_playback_target(self, target: dict) -> None:
        target = target if isinstance(target, dict) else {}
        self._saved_target = dict(target)
        self.target_title_var.set(str(target.get("title_filter") or target.get("title") or ""))
        self.target_process_var.set(str(target.get("process") or ""))

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

    def refresh_windows(self) -> None:
        def _worker() -> None:
            try:
                windows = list(self.ui._on_voice_list_windows() or [])
                error = ""
            except Exception as exc:
                windows = []
                error = str(exc)

            def _finish() -> None:
                if not self._alive():
                    return
                self._window_mapping = {}
                for window in windows:
                    label = self._window_label(window)
                    unique = label
                    suffix = 2
                    while unique in self._window_mapping:
                        unique = f"{label} ({suffix})"
                        suffix += 1
                    self._window_mapping[unique] = window
                self._window_combo.configure(values=list(self._window_mapping))
                processes = sorted(
                    {
                        str(window.get("process") or "").strip()
                        for window in windows
                        if str(window.get("process") or "").strip()
                    },
                    key=str.casefold,
                )
                self._process_combo.configure(values=["", *processes])
                saved_title = str(self.target_title_var.get() or "").strip().casefold()
                saved_process = str(self.target_process_var.get() or "").strip().casefold()
                selected_label = ""
                for label, window in self._window_mapping.items():
                    title_matches = not saved_title or saved_title in str(window.get("title") or "").casefold()
                    process_matches = not saved_process or saved_process == str(window.get("process") or "").casefold()
                    if title_matches and process_matches and (saved_title or saved_process):
                        selected_label = label
                        break
                if not selected_label:
                    selected_label = next(
                        (
                            label
                            for label, window in self._window_mapping.items()
                            if bool(window.get("anime_match"))
                        ),
                        "",
                    )
                self.window_var.set(selected_label)
                if error:
                    self._append_status(f"Window refresh failed: {error}")

            dispatch_to_tk(self.ui.root, _finish)

        threading.Thread(target=_worker, daemon=True, name="voice-window-list").start()

    def select_window(self) -> None:
        selected = self._window_mapping.get(self.window_var.get())
        if not selected:
            self.ui.root.bell()
            return
        target = {
            "title_filter": str(selected.get("title") or ""),
            "process": str(selected.get("process") or ""),
        }
        self.set_playback_target(target)

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
