"""
Settings window UI (root) and control window (floating playback controls).

This module is the main user-facing UI for controlling time, offsets, episodes, and mode.
"""

import tkinter as tk
import logging
from tkinter import ttk
from re import fullmatch
from model.config_manager import ConfigManager
from view.settings_advanced_ui import SettingsAdvancedUI
from utils import (
    make_draggable,
    format_time,
    get_monitor_rects,
    make_nonactivating_window,
    is_any_window_foreground,
    set_window_topmost_no_activate,
)

logger = logging.getLogger(__name__)

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
        self.fast_forward_btn = None
        self._phone_mode_toggle_btn = None
        self.play_pause_btn = None
        self.slider = None
        self.advanced_window = None
        self._advanced_notebook = None
        self._advanced_tab_key_map = {}
        self._performance_tab = None
        self._performance_text = None
        self._root_topmost_before_advanced = None
        self._settings_window_from_control = False
        self._ocr_region_count_trace_var = None
        self._ocr_region_count_refresh_job = None
        self._anki_add_session_count = 0
        self._anki_add_today_count = 0
        self.adv_settings = SettingsAdvancedUI(self)
        self._build_settings_frame()
        self._build_control_window()
        # make_nonactivating_window(self.root)
        # self.root.after(0, lambda: make_nonactivating_window(self.root))
        if self._start_hidden:
            self.control_window.withdraw()

    def _init_defaults(self):
        get = self.config.get
        self.default_offset = self._config_float("EXTRA_OFFSET", 0.0)
        self._last_offset_value = float(self.default_offset)
        self.default_skip = self._config_float("DEFAULT_SKIP", 1.0)
        self._last_skip_value = float(self.default_skip)
        self.default_start = self._config_float("DEFAULT_START_TIME", 0.0)
        self.default_phone_mode = bool(get("PHONEMODE_DEFAULT") or False)
        self.fast_forward_display_enabled = not bool(get("FAST_FORWARD_DISABLED") or False)
        self.input_mode = self._resolve_input_mode()
        self._last_active_input_mode = self.input_mode if self.input_mode in (1, 2) else 1
        self.numpad_mode_enabled = (self.input_mode == 2)
        self._sync_input_mode_runtime_flags()

        self.default_x = self.config.get("LAST_SETTINGS_WINDOW_X")
        self.default_y = self.config.get("LAST_SETTINGS_WINDOW_Y")
        self.win_x = self._config_int("LAST_CONTROL_WINDOW_X", 30)
        self.win_y = self._config_int("LAST_CONTROL_WINDOW_Y", 30)
        self._control_win_x = int(self.win_x)
        self._control_win_y = int(self.win_y)

    def _config_float(self, key: str, default: float) -> float:
        try:
            value = self.config.get(key)
            if value is None:
                return float(default)
            return float(value)
        except Exception:
            return float(default)

    def _config_int(self, key: str, default: int) -> int:
        try:
            value = self.config.get(key)
            if value is None:
                return int(default)
            return int(value)
        except Exception:
            return int(default)

    def _resolve_input_mode(self) -> int:
        """Return input mode 1/2/3 with backward compatibility for old config keys."""
        mode = self.config.get("INPUT_MODE")
        parsed = None
        try:
            parsed = int(mode) or None
        except Exception:
            parsed = None
        if parsed in (1, 2, 3):
            return parsed
        parsed = 2 if bool(self.config.get("INPUT_MODE_NUMPAD") or False) else 1
        if bool(self.config.get("SHORTCUTS_DISABLED") or False):
            return 3
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
        self._last_time_entry_width = len(self.control_time_str.get())


    def _noop(self, *args, **kwargs):
        pass

    def _init_callbacks(self):
        for name in ("ep_change", "ep_inc", "ep_dec", "refresh_episodes",
                     "slider_change", "slider_press", "slider_release",
                     "set_to", "open_srt", "show_handle",
                     #Control window:
                     "back", "forward", "play_pause",
                     "toggle_subtitles",
                     "time_entry_return", "time_entry_clear",
                     "fast_forward_toggle", "fast_forward_speed_delta",
                     "advanced_apply",
                     "offset_change",
                     "ocr_read_now", "ocr_sync_now", "ocr_show_boxes",
                     "anki_check", "performance_snapshot", "performance_reset",
                     "settings_open",
                     "annotation_list_words", "annotation_add_word", "annotation_delete_word",
                     "annotation_import_words", "annotation_export_words", "annotation_refresh_words",
                     "annotation_anki_refresh", "annotation_anki_model_fields", "annotation_anki_sync",
                     "annotation_wanikani_test", "annotation_wanikani_sync", "annotation_wanikani_clear"):
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
        self.setto_entry.bind("<Return>", self._on_set_to_commit)
        self.setto_entry.bind("<FocusOut>", self._on_set_to_commit)

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
        self._time_overlay_text_value = None
        self._time_overlay_half_width = 0
        self._time_overlay_position_job = None
        self.root.after_idle(lambda: self.set_time_overlay_text(self.control_time_str.get()))

        # Slider
        self.slider  = tk.Scale(
            self.slider_frame,
            from_=0, to=(self.total_duration + self.default_offset),
            orient="horizontal",
            resolution=00.1,
            showvalue=False,
            sliderlength=32,
            command=self._on_slider_command
        )
        self.slider.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        self.slider.set(float(self.default_start))
        self.slider.bind("<ButtonPress-1>", self._on_click_or_drag)
        self.slider.bind("<B1-Motion>",      self._on_click_or_drag)
        self.slider.bind("<ButtonRelease-1>", lambda e: self._on_slider_release(e))
        self.slider.bind("<Configure>", lambda _e: self._schedule_time_overlay_position_update(), add="+")
        self.time_overlay.bind("<Configure>", lambda _e: self._schedule_time_overlay_position_update(), add="+")
        self._schedule_time_overlay_position_update()
        self.root.after(50, self.update_time_overlay_position)
        self.root.after(250, self.update_time_overlay_position)

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
        self.fast_forward_btn = tk.Label(
            self.control_window,
            text="\N{STOPWATCH}1.5x",
            font=("Arial", 7, "bold"),
            width=4,
            height=1,
            relief="flat",
            bd=0,
            bg="#303030",
            fg="white",
        )

        self.back_button.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.play_pause_btn.grid(row=1, column=1,pady=0, sticky="nsew")
        self.time_entry.grid(row=0, column=1, sticky="nsew", ipady=5)
        self.forward_button.grid(row=0, column=2, rowspan=2, sticky="nsew")

        self.handle_settings_frame = tk.Frame(self.control_window, width=30, height=10)
        self.handle_settings_frame.place(x=0, y=0)
        self.settings_btn = tk.Button(self.handle_settings_frame, relief="raised", bg= "grey")
        self.refresh_btn = tk.Button(self.handle_settings_frame, relief="raised", bg= "grey")
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
        self.refresh_btn.bind("<ButtonPress>", lambda ev: self._on_toggle_subtitles(ev))
        self.fast_forward_btn.bind("<MouseWheel>", self._on_fast_forward_wheel)
        self.fast_forward_btn.bind("<Button-4>", self._on_fast_forward_wheel)
        self.fast_forward_btn.bind("<Button-5>", self._on_fast_forward_wheel)
        self.time_entry.bind("<Button-1>", self._on_time_entry_click)
        self.time_entry.bind("<FocusOut>", lambda ev: self._on_time_entry_return(ev))
        self.time_entry.bind("<Return>", lambda ev:   self._on_time_entry_return(ev))
        self.control_window.bind("<ButtonPress-1>", self._on_control_window_click, add="+")

        self.control_window.bind("<Enter>", lambda ev: self.bind_control_window_enter(ev))
        self.control_window.bind("<Leave>", lambda ev: self.bind_control_window_leave(ev))
        self.set_fast_forward_state(
            self.fast_forward_display_enabled,
            self._config_float("FAST_FORWARD_SPEED", 1.5),
        )

    def show(self) -> None:
        """Show the floating control window (used after startup splash)."""
        self.control_window.deiconify()
        # Re-apply geometry after withdraw/deiconify (overrideredirect windows can reset to 0,0).
        self._set_phone_mode_styles(self.default_phone_mode)
        self.control_window.lift()
        self.control_window.attributes("-topmost", True)

    def _settings_window_is_topmost(self) -> bool:
        try:
            return bool(self.root.attributes("-topmost"))
        except Exception:
            return False

    def _settings_window_is_visible(self) -> bool:
        try:
            state = str(self.root.state() or "").lower()
            if state in {"iconic", "withdrawn"}:
                return False
        except Exception:
            pass
        try:
            return bool(self.root.winfo_viewable())
        except Exception:
            return False

    def _advanced_window_is_open(self) -> bool:
        win = getattr(self, "advanced_window", None)
        try:
            return bool(win is not None and win.winfo_exists())
        except Exception:
            return False

    @staticmethod
    def _window_is_visible(win) -> bool:
        try:
            if win is None or not win.winfo_exists():
                return False
            state = str(win.state() or "").lower()
            if state in {"iconic", "withdrawn"}:
                return False
        except Exception:
            pass
        try:
            return bool(win.winfo_viewable())
        except Exception:
            return True

    def _advanced_window_is_visible(self) -> bool:
        return self._window_is_visible(getattr(self, "advanced_window", None))

    def _advanced_window_is_topmost(self) -> bool:
        win = getattr(self, "advanced_window", None)
        try:
            return bool(win is not None and win.winfo_exists() and win.attributes("-topmost"))
        except Exception:
            return False

    def _settings_group_is_foreground(self) -> bool | None:
        try:
            return is_any_window_foreground(
                [
                    getattr(self, "root", None),
                    getattr(self, "advanced_window", None),
                    getattr(self, "control_window", None),
                ]
            )
        except Exception:
            return None

    def show_settings_window(self) -> None:
        """Show the main settings window through the normal reliable Tk path."""
        shown = False
        try:
            self.root.deiconify()
            self.root.state("normal")
            self.root.attributes("-topmost", True)
            self.root.lift()
            shown = True
        except Exception as e:
            logger.debug("Failed to show settings window normally: %s", e, exc_info=True)
        if not shown:
            try:
                self.root.lift()
                self.root.focus_force()
            except Exception:
                pass
        self._settings_window_from_control = True

    def demote_settings_window(self) -> None:
        """Send the settings window behind fullscreen video without minimizing it."""
        if not set_window_topmost_no_activate(self.root, False):
            try:
                self.root.attributes("-topmost", False)
            except Exception:
                pass
        try:
            self.root.lower()
        except Exception:
            pass
        self._settings_window_from_control = False

    def _show_advanced_window_from_control(self) -> None:
        win = getattr(self, "advanced_window", None)
        try:
            if win is not None and win.winfo_exists():
                self.adv_settings._show_advanced_window(win)
        except Exception:
            logger.debug("Failed to show advanced settings window from control button", exc_info=True)

    def _hide_advanced_window_from_control(self) -> None:
        win = getattr(self, "advanced_window", None)
        try:
            if win is not None and win.winfo_exists():
                try:
                    self.adv_settings._save_advanced_window_position(win)
                except Exception:
                    pass
                win.withdraw()
        except Exception:
            logger.debug("Failed to hide advanced settings window from control button", exc_info=True)

    def toggle_settings_window_from_control(self) -> bool:
        advanced_visible = self._advanced_window_is_visible()
        group_visible = (
            self._settings_window_from_control
            and self._settings_window_is_visible()
            and self._settings_window_is_topmost()
        )
        if advanced_visible:
            foreground = self._settings_group_is_foreground()
            if foreground is False:
                group_visible = False
            else:
                group_visible = group_visible or self._advanced_window_is_topmost()
        if group_visible:
            self._hide_advanced_window_from_control()
            self.demote_settings_window()
            return False
        self.show_settings_window()
        self._show_advanced_window_from_control()
        return True
        
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
        except Exception as e:
            logger.debug("Saved input mode is invalid: %s", e, exc_info=True)
            saved_mode = 1
        if int(self.input_mode) != saved_mode:
            self.config.set("INPUT_MODE", int(self.input_mode))
        if bool(self.numpad_mode_enabled) != bool(self.config.get("INPUT_MODE_NUMPAD") or False):
            self.config.set("INPUT_MODE_NUMPAD", bool(self.numpad_mode_enabled))
        hotkeys_disabled = bool(self.input_mode == 3)
        if hotkeys_disabled != bool(self.config.get("SHORTCUTS_DISABLED") or False):
            self.config.set("SHORTCUTS_DISABLED", hotkeys_disabled)
        if self._last_active_input_mode in (1, 2):
            saved_last = int(self.config.get("LAST_ACTIVE_INPUT_MODE") or 0)
            if self._last_active_input_mode != saved_last:
                self.config.set("LAST_ACTIVE_INPUT_MODE", int(self._last_active_input_mode))
        # Persist offset and skip values
        saved_offset = float(self.config.get("EXTRA_OFFSET") or 0.0)
        if abs(self._last_offset_value - saved_offset) > 0.001:
            self.config.set("EXTRA_OFFSET", self._last_offset_value)
        saved_skip = float(self.config.get("DEFAULT_SKIP") or 1.0)
        if abs(self._last_skip_value - saved_skip) > 0.001:
            self.config.set("DEFAULT_SKIP", self._last_skip_value)

    def bind_slider(self,   on_chg, on_pr, on_rl):
        self._on_slider_change   = on_chg
        self._on_slider_press    = on_pr
        self._on_slider_release  = on_rl
    def bind_set_to_return(self, cb):        self._on_set_to_return = cb
    def bind_open_srt(self, cb):             self._on_open_srt = cb
    def bind_refresh_episodes(self, cb):     self._on_refresh_episodes = cb

    def open_anki_activity_window(self) -> None:
        self.adv_settings._open_anki_activity_window()
    def bind_show_subtitle_handle(self, cb): self._on_show_handle = cb

    # Control window
    def bind_back(self,      cb):            self._on_back       = cb
    def bind_forward(self,   cb):            self._on_forward    = cb
    def bind_play_pause(self,cb):            self._on_play_pause = cb
    def bind_fast_forward_controls(self, toggle=None, speed_delta=None):
        if callable(toggle):
            self._on_fast_forward_toggle = toggle
        if callable(speed_delta):
            self._on_fast_forward_speed_delta = speed_delta
    def bind_toggle_subtitles(self, cb):     self._on_toggle_subtitles = cb
    def bind_time_entry_return(self, cb):    self._on_time_entry_return = cb
    def bind_time_entry_clear(self,  cb):    self._on_time_entry_clear = cb
    def bind_control_window_enter(self, cb): self._on_control_window_enter = cb
    def bind_control_window_leave(self, cb): self._on_control_window_leave = cb
    def bind_refresh_subtitles(self, cb):    self.on_refresh_subtitles = cb

    def bind_update_display(self, cb):       self.update_time_and_subtitle_displays = cb
    def bind_advanced_apply(self, cb):       self._on_advanced_apply = cb
    def bind_offset_change(self, cb):        self._on_offset_change = cb
    def bind_ocr_read_now(self, cb):         self._on_ocr_read_now = cb
    def bind_ocr_sync_now(self, cb):         self._on_ocr_sync_now = cb
    def bind_ocr_show_boxes(self, cb):       self._on_ocr_show_boxes = cb
    def bind_anki_check(self, cb):           self._on_anki_check = cb
    def bind_performance_snapshot(self, cb): self._on_performance_snapshot = cb
    def bind_performance_reset(self, cb):    self._on_performance_reset = cb
    def bind_settings_open(self, cb):        self._on_settings_open = cb
    def bind_annotation_callbacks(self, **callbacks):
        mapping = {
            "list_words": "annotation_list_words",
            "add_word": "annotation_add_word",
            "delete_word": "annotation_delete_word",
            "import_words": "annotation_import_words",
            "export_words": "annotation_export_words",
            "refresh_words": "annotation_refresh_words",
            "anki_refresh": "annotation_anki_refresh",
            "anki_model_fields": "annotation_anki_model_fields",
            "anki_sync": "annotation_anki_sync",
            "wanikani_test": "annotation_wanikani_test",
            "wanikani_sync": "annotation_wanikani_sync",
            "wanikani_clear": "annotation_wanikani_clear",
        }
        for source, target in mapping.items():
            callback = callbacks.get(source)
            if callable(callback):
                setattr(self, f"_on_{target}", callback)

    def set_anki_add_counts(self, session_count: int, today_count: int) -> None:
        try:
            self._anki_add_session_count = max(0, int(session_count or 0))
        except Exception:
            self._anki_add_session_count = 0
        try:
            self._anki_add_today_count = max(0, int(today_count or 0))
        except Exception:
            self._anki_add_today_count = 0
        annotation_tab = getattr(self, "_annotation_tab_ui", None)
        if annotation_tab is not None:
            try:
                annotation_tab.set_add_counts(
                    self._anki_add_session_count,
                    self._anki_add_today_count,
                )
            except Exception:
                logger.debug("Failed to update Anki add counters in Annotation tab", exc_info=True)

    def persist_anki_add_history(self, history: dict, today: str, today_count: int) -> None:
        self.adv_settings.persist_anki_add_history(history, today, today_count)

    def refresh_anki_activity_chart(self) -> None:
        self.adv_settings.refresh_anki_activity_chart()

    def refresh_debugging_visibility(self):  self.adv_settings._sync_performance_tab_visibility()

    def set_time_overlay_text(self, text: str) -> None:
        text = str(text or "")
        if text != getattr(self, "_time_overlay_text_value", None):
            self.time_overlay.itemconfig(self.time_overlay_text, text=text)
            self._time_overlay_text_value = text
            try:
                bbox = self.time_overlay.bbox(self.time_overlay_text)
                self._time_overlay_half_width = max(0, int((bbox[2] - bbox[0]) / 2)) if bbox else 0
            except Exception:
                self._time_overlay_half_width = 0
        self._schedule_time_overlay_position_update()

    def _schedule_time_overlay_position_update(self, delay_ms: int = 0) -> None:
        if getattr(self, "_time_overlay_position_job", None) is not None:
            return

        def _run() -> None:
            self._time_overlay_position_job = None
            self.update_time_overlay_position()

        try:
            if int(delay_ms) > 0:
                self._time_overlay_position_job = self.root.after(int(delay_ms), _run)
            else:
                self._time_overlay_position_job = self.root.after_idle(_run)
        except Exception:
            self._time_overlay_position_job = None

    def update_time_overlay_position(self):
        if self.slider is None or self.time_overlay is None:
            return

        value = float(self.slider.get())
        slider_width = int(self.slider.winfo_width() or 0)
        canvas_width = int(self.time_overlay.winfo_width() or 0)
        if slider_width <= 1 or canvas_width <= 1:
            self._schedule_time_overlay_position_update(delay_ms=30)
            return

        slider_length = int(self.slider["sliderlength"])
        min_x = max(1, slider_length // 2)
        max_x = max(min_x, slider_width - max(1, slider_length // 2))
        min_val = float(self.slider.cget('from'))
        max_val = float(self.slider.cget('to'))
        rel = (value - min_val) / (max_val - min_val) if max_val != min_val else 0.0
        rel = max(0.0, min(1.0, rel))

        try:
            x, _y = self.slider.coords(value)
            x = int(x)
            if x <= 0 or x > slider_width:
                raise ValueError("slider coordinate is not ready")
        except Exception:
            x = int(min_x + rel * (max_x - min_x))

        if abs(canvas_width - slider_width) > 1:
            x = int(round(x * (canvas_width / max(1, slider_width))))

        half_text = int(getattr(self, "_time_overlay_half_width", 0) or 0)
        if half_text:
            x = max(half_text, min(max(half_text, canvas_width - half_text), x))
        self.time_overlay.coords(self.time_overlay_text, x, 12)

    def _on_slider_command(self, value):
        self._slider_command_seen = True
        self._on_slider_change(value)

    def _on_click_or_drag(self, event):
        event_type = getattr(event, "type", None)
        if event_type == tk.EventType.ButtonPress or str(event_type) == "4":
            self._on_slider_press(event)
        slider_length = int(self.slider["sliderlength"])
        w = max(1, int(self.slider.winfo_width()) - slider_length)
        x_off = int(event.x) - (slider_length / 2)
        frac   = max(0.0, min(1.0, x_off / w))
        start  = float(self.slider.cget("from"))
        end    = float(self.slider.cget("to"))
        new_val = start + frac * (end - start)
        self._slider_command_seen = False
        self.slider.set(new_val)
        if not getattr(self, "_slider_command_seen", False):
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
        self._persist_input_mode_state()
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
        self._persist_input_mode_state()
        self._refresh_input_mode_button()

    def _sync_input_mode_runtime_flags(self):
        self.numpad_mode_enabled = (self.input_mode == 2)
        cfg = getattr(self.config, "config", None)
        is_m3 = bool(self.input_mode == 3)
        if isinstance(cfg, dict):
            cfg["INPUT_MODE"] = int(self.input_mode)
            cfg["INPUT_MODE_NUMPAD"] = bool(self.numpad_mode_enabled)
            cfg["SHORTCUTS_DISABLED"] = is_m3
            if self.input_mode in (1, 2):
                cfg["LAST_ACTIVE_INPUT_MODE"] = int(self.input_mode)

        # Keep the advanced window checkbox in sync with runtime mode changes.
        adv_vars = getattr(self, "_advanced_vars", None)
        if isinstance(adv_vars, dict):
            hotkey_var = adv_vars.get("SHORTCUTS_DISABLED")
            if hotkey_var is not None:
                try:
                    hotkey_var.set(is_m3)
                except Exception:
                    pass

    def _persist_input_mode_state(self) -> None:
        updates = {
            "INPUT_MODE": int(self.input_mode),
            "INPUT_MODE_NUMPAD": bool(self.input_mode == 2),
            "SHORTCUTS_DISABLED": bool(self.input_mode == 3),
        }
        if self._last_active_input_mode in (1, 2):
            updates["LAST_ACTIVE_INPUT_MODE"] = int(self._last_active_input_mode)
        try:
            if hasattr(self.config, "set_many"):
                self.config.set_many(updates)
            else:
                for key, value in updates.items():
                    if self.config.get(key) != value:
                        self.config.set(key, value)
        except Exception as e:
            logger.debug("Failed to persist input mode state: %s", e, exc_info=True)

    def _refresh_input_mode_button(self):
        if not (getattr(self, "input_mode_btn", None) or getattr(self, "mode_toggle_btn", None)):
            return
        if self.input_mode == 2:
            text, bg = "M2", "green"
        elif self.input_mode == 3:
            text, bg = "M3", "#d46a6a"
        else:
            text, bg = "M1", "SystemButtonFace"
        for btn in (self.input_mode_btn, getattr(self, "mode_toggle_btn", None)):
            if btn is None:
                continue
            try:
                btn.configure(
                    text=text,
                    bg=bg,
                    state=tk.NORMAL,
                    activebackground=bg,
                    activeforeground="white" if self.input_mode in (2, 3) else "black",
                )
            except Exception:
                try:
                    btn.configure(text=text, bg=bg)
                except Exception:
                    pass
        
    def _set_phone_mode_styles(self, phone_mode: bool):
        if phone_mode:
            self.handle_settings_frame.configure(width=120, height=40)
            self.control_drag_handle.config(width=40, height=40)
            f_large = ("Arial", 30, "bold")
            f_btn = ("Arial", 22, "bold")
            self.settings_btn.place_configure(x=40, y=0, width=40, height=40)
            self.refresh_btn .place_configure(x= 80, y=0, width=40, height=40)
            self._place_fast_forward_display(phone_mode=True)
            h = 160
        else:
            self.handle_settings_frame.configure(width=30, height=10)
            self.control_drag_handle.config(width=10, height=10)
            f_large = ("Arial", 14, "bold")
            f_btn = ("Arial", 12, "bold")
            self.settings_btn.place_configure(x=10, y=0, width=10, height=10)
            self.refresh_btn .place_configure(x= 20, y=0, width=10, height=10)
            self._place_fast_forward_display(phone_mode=False)
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
        width = len(self.control_time_str.get())
        if width == getattr(self, "_last_time_entry_width", None):
            return
        self._last_time_entry_width = width
        if self.time_entry is None or self.control_window is None:
            return
        self.time_entry.config(width=width)
        self.control_window.update_idletasks()
        reqw = self.control_window.winfo_reqwidth()
        x = self.control_window.winfo_x()
        y = self.control_window.winfo_y()
        self.control_window.geometry(f"{reqw}x{self.control_window.winfo_height()}+{x}+{y}")

    def _on_control_window_click(self, event):
        if event.widget is self.time_entry:
            return
        try:
            self.control_window.after_idle(lambda: self.control_window.tk.call("focus", ""))
        except Exception as e:
            logger.debug("Failed to clear control-window focus: %s", e, exc_info=True)

    def _on_time_entry_click(self, event):
        try:
            self.control_window.deiconify()
            self.control_window.lift()
            self.control_window.attributes("-topmost", True)
            self.time_entry.focus_force()
            self.time_entry.icursor(tk.END)
        except Exception as e:
            logger.debug("Failed to focus control time entry: %s", e, exc_info=True)
        self._on_time_entry_clear(event)
        return None

    def _on_fast_forward_wheel(self, event):
        if not bool(getattr(self, "fast_forward_display_enabled", True)):
            return "break"
        delta = 0.1
        try:
            if getattr(event, "num", None) == 5 or int(getattr(event, "delta", 0) or 0) < 0:
                delta = -0.1
        except Exception:
            delta = 0.1
        try:
            self._on_fast_forward_speed_delta(delta)
        except Exception as e:
            logger.debug("Failed to adjust fast-forward speed from wheel: %s", e, exc_info=True)
        return "break"

    def set_fast_forward_state(self, active: bool, speed: float) -> None:
        btn = getattr(self, "fast_forward_btn", None)
        if btn is None:
            return
        self.fast_forward_display_enabled = bool(active)
        if not self.fast_forward_display_enabled:
            try:
                btn.place_forget()
            except Exception:
                pass
            return
        try:
            speed = float(speed)
        except Exception:
            speed = 1.0
        label = f"\N{STOPWATCH}{speed:.1f}x"
        try:
            btn.configure(
                text=label,
                bg="#303030",
                fg="white",
                relief=tk.FLAT,
            )
            self._place_fast_forward_display(bool(self.default_phone_mode))
        except Exception:
            pass

    def _place_fast_forward_display(self, phone_mode: bool) -> None:
        btn = getattr(self, "fast_forward_btn", None)
        if btn is None:
            return
        if not bool(getattr(self, "fast_forward_display_enabled", True)):
            try:
                btn.place_forget()
            except Exception:
                pass
            return
        if phone_mode:
            btn.place_configure(relx=1.0, x=-58, y=0, width=58, height=40)
            btn.configure(font=("Arial", 10, "bold"))
        else:
            btn.place_configure(relx=1.0, x=-48, y=0, width=48, height=18)
            btn.configure(font=("Arial", 7, "bold"))

    #HELPERS
    def _on_settings(self, event):#button to lift the root window
        opened = self.toggle_settings_window_from_control()
        if opened:
            self._on_settings_open()
        return "break"

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
        return float(match.group(1).replace(",", "."))

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
            
    # --------- PUBLIC binders ------------------------------------------------------------------------------------------------------------------
    # Settings window
    def bind_episode_change(self, on_ent, on_inc, on_dec):
        self._on_ep_entry_change = on_ent
        self._on_ep_inc          = on_inc
        self._on_ep_dec          = on_dec

    def set_episode_nav_state(self, can_dec: bool, can_inc: bool, is_movie: bool = False) -> None:
        self.episode_dec_btn.configure(state=(tk.NORMAL if can_dec else tk.DISABLED))
        self.episode_inc_btn.configure(state=(tk.NORMAL if can_inc else tk.DISABLED))
        self.episode_entry.configure(state=(tk.DISABLED if is_movie else tk.NORMAL))

    def set_episode_values(self, values) -> None:
        """
        Update the dropdown list for the episode combobox.
        Values may be ints/strings or dicts with a display "label".
        """
        labels = []
        for value in values or []:
            if isinstance(value, dict):
                labels.append(str(value.get("label") or value.get("value") or ""))
            else:
                labels.append(str(value))
        self.episode_entry.configure(values=[label for label in labels if label])

    def _on_episode_entry_click(self, event):
        elem = event.widget.identify(event.x, event.y)
        if elem and "downarrow" in str(elem).lower():
            return
        self._last_episode_value = self.episode_var.get() or ""
        self.episode_var.set("")

    def _on_episode_entry_focus_out(self, event):
        """
        Restore last value if the entry is left empty (or invalid) without pressing Enter.
        This must NOT trigger subtitle loading.
        """
        text = (self.episode_var.get() or "").strip() or ""
        if not text:
            self.episode_var.set(self._last_episode_value)
            return
        if text.lower() == "movie":
            self.episode_var.set(self._last_episode_value)
            return
        if fullmatch(r"\d+", text):
            try:
                if int(text) <= 0:
                    raise ValueError()
                return
            except Exception as e:
                logger.debug("Invalid episode entry on focus out: %s", e, exc_info=True)
                self.episode_var.set(self._last_episode_value)
                return
        if fullmatch(r"(?i)\d+\s*\(\s*S\d{1,2}E\d{1,4}\s*\)", text):
            return
        if fullmatch(r"(?i)S\d{1,2}\s*E\d{1,4}", text):
            return
        self.episode_var.set(self._last_episode_value)

    def _on_set_to_commit(self, event=None) -> str:
        text = (self.setto_var.get() or "").strip()

        if not text or not fullmatch(r"[\d:.]+", text):
            self.setto_entry.delete(0, tk.END)
            return "break"

        if callable(self._on_set_to_return):
            self._on_set_to_return(text)

        return "break"
    
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

    def _apply_offset_change(self, value_seconds: float, persist: bool, previous_value=None, adjust_current: bool = True):
        try:
            previous = float(previous_value)
            delta = float(value_seconds) - previous
        except Exception as e:
            logger.debug("Invalid previous offset value: %s", e, exc_info=True)
            delta = 0.0
        self.slider.config(to=self.total_duration + value_seconds)
        if adjust_current and abs(delta) >= 0.001:
            self.slider.set(float(self.slider.get()) + delta)
        self._schedule_time_overlay_position_update()
        self._on_slider_release(None)
        self._sync_advanced_startup_vars_from_runtime()
        if persist:
            self.config.set("EXTRA_OFFSET", value_seconds)
            try:
                self._on_offset_change(float(value_seconds))
            except Exception:
                logger.debug("Offset change callback failed", exc_info=True)

    def _apply_skip_change(self, value_seconds: float, persist: bool):
        """Update skip value and optionally persist to config."""
        self._sync_advanced_startup_vars_from_runtime()
        if persist:
            self.config.set("DEFAULT_SKIP", value_seconds)

    def set_total_duration(self, total_duration: float):
        self.total_duration = total_duration
        self.slider.config(to=total_duration + self._last_offset_value)
        self._schedule_time_overlay_position_update()
