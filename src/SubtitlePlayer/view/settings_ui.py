"""
Settings window UI (root) and control window (floating playback controls).

This module is the main user-facing UI for controlling time, offsets, episodes, and mode.
"""

import tkinter as tk
from tkinter import ttk
from re import fullmatch
from model.config_manager import ConfigManager
from utils import make_draggable, format_time, get_monitor_rects

class SettingsUI:
    NUMBER_PATTERN = r"\s*([-+]?\d+(?:[.,]\d+)?)\s*(?:s|sec|secs|second|seconds)?\s*"
    OCR_MAX_REGIONS = 8
    _OCR_REGION_KEYS = []
    for _idx in range(1, OCR_MAX_REGIONS + 1):
        _suffix = "" if _idx == 1 else str(_idx)
        for _axis in ("X", "Y", "W", "H"):
            _OCR_REGION_KEYS.append(f"OCR_REGION{_suffix}_{_axis}")
    OCR_KEYS = (
        "OCR_ENABLED",
        "OCR_DEBUG",
        "OCR_TESSERACT_CMD",
        "OCR_TESSERACT_PSM",
        "OCR_TESSERACT_OEM",
        "OCR_CHAR_WHITELIST",
        "OCR_SCREEN_INDEX",
        "OCR_REGION_COUNT",
        *_OCR_REGION_KEYS,
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
        self.play_pause_btn = None
        self.slider = None
        self.advanced_window = None
        self._advanced_notebook = None
        self._advanced_tab_sizes = {}
        self._advanced_resize_job = None

        self._build_settings_frame()
        self._build_control_window()
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
        if parsed not in (1, 2, 3):
            parsed = 2 if bool(self.config.get("INPUT_MODE_NUMPAD") or False) else 1
        if bool(self.config.get("SHORTCUTS_DISABLED") or False):
            parsed = 3
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
                     "ocr_read_now"):
            setattr(self, f"_on_{name}", self._noop)

    # â€”â€”â€” SETTINGS FRAME â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
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

        self.mode_toggle_btn = tk.Button(
            mode_tools_frame,
            text="\N{TELEPHONE RECEIVER}",
            width=2,
            height=1,
            relief="raised",
            command=self._toggle_phone_mode,
        )
        self.advanced_settings_btn = tk.Button(
            mode_tools_frame,
            text="\N{GEAR}",
            width=2,
            height=1,
            relief="raised",
            command=self._open_advanced_settings_window,
        )
        self.advanced_settings_btn.pack(side="left", padx=(0, 2))
        self.mode_toggle_btn.pack(side="left")
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

    # â€”â€”â€” CONTROL WINDOW â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
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
        if self.default_phone_mode:
            self._set_phone_mode_styles(self.default_phone_mode)
            self.mode_toggle_btn.configure(bg="green")
        else:
            self._set_phone_mode_styles(self.default_phone_mode)
            self.mode_toggle_btn.configure(bg="SystemButtonFace")

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

        self.control_window.bind("<Enter>", lambda ev: self._on_control_window_enter(ev))
        self.control_window.bind("<Leave>", lambda ev: self._on_control_window_leave(ev))

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
            
    # â€”â€”â€” PUBLIC binders â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
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

    # â€”â€”â€” PHONE MODE UI ADJUSTMENT â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”

    def _toggle_phone_mode(self):
        phone_mode = not self.default_phone_mode
        self.default_phone_mode = phone_mode
        self._set_phone_mode_styles(phone_mode)
        self.mode_toggle_btn.configure(bg="green" if phone_mode else "SystemButtonFace")
        self.control_window.attributes("-topmost", True)
        self._on_show_handle(self.default_phone_mode)

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



    # â€”â€”â€” HELPERS â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”â€”
    def _on_settings(self, event):#button to lift the root window
        self.root.deiconify()
        self.root.lift()

    def _open_advanced_settings_window(self):
        if self.advanced_window is not None and self.advanced_window.winfo_exists():
            self.advanced_window.deiconify()
            self.advanced_window.lift()
            self.advanced_window.attributes("-topmost", True)
            self._load_advanced_values_into_vars()
            self._prepare_advanced_tab_sizes()
            self.root.after(0, self._fit_advanced_window_to_selected_tab)
            self.root.after(80, self._fit_advanced_window_to_selected_tab)
            self.root.after(0, self._reset_advanced_tab_focus)
            return

        win = tk.Toplevel(self.root)
        self.advanced_window = win
        win.title("Advanced Settings")
        win.attributes("-topmost", True)
        win.resizable(True, True)
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

        notebook = ttk.Notebook(body)
        notebook.pack(fill="x", expand=False, anchor="n", pady=(0, 8))
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
            command=lambda: self._apply_advanced_settings(persist=False),
        ).pack(side="left")
        tk.Button(
            btn_row,
            text="Save Default",
            width=12,
            command=lambda: self._apply_advanced_settings(persist=True),
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            btn_row,
            text="Reload",
            width=10,
            command=self._load_advanced_values_into_vars,
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            btn_row,
            text="Reset Defaults",
            width=13,
            command=self._reset_advanced_values_to_defaults,
        ).pack(side="left", padx=(6, 0))
        tk.Button(btn_row, text="Close", width=10, command=win.destroy).pack(side="right")

        win.bind("<Return>", self._on_advanced_apply_now_key, add="+")
        win.bind("<KP_Enter>", self._on_advanced_apply_now_key, add="+")

        self._prepare_advanced_tab_sizes()
        win.after(0, self._fit_advanced_window_to_selected_tab)
        win.after(80, self._fit_advanced_window_to_selected_tab)
        win.after(0, self._reset_advanced_tab_focus)

        def _on_destroy(_event):
            if _event.widget is not win:
                return
            if self._advanced_resize_job is not None:
                try:
                    win.after_cancel(self._advanced_resize_job)
                except Exception:
                    pass
                self._advanced_resize_job = None
            self._save_advanced_window_geometry(win)
            self.advanced_window = None
            self._advanced_notebook = None
            self._advanced_tab_sizes = {}

        win.bind("<Destroy>", _on_destroy)

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
            try:
                rw, rh = self.root.winfo_width(), self.root.winfo_height()
                rx, ry = self.root.winfo_rootx(), self.root.winfo_rooty()
                x = rx + max((rw - w) // 2, 0)
                y = ry + max((rh - h) // 2, 0)
            except Exception:
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
        if self._advanced_resize_job is not None:
            try:
                win.after_cancel(self._advanced_resize_job)
            except Exception:
                pass
        self._advanced_resize_job = win.after(1, self._fit_advanced_window_to_selected_tab)
        win.after(0, self._reset_advanced_tab_focus)

    def _on_advanced_apply_now_key(self, _event=None):
        self._apply_advanced_settings(persist=False)
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
            x = max(0, min(int(win.winfo_x()), max(0, sw - req_w)))
            y = max(0, min(int(win.winfo_y()), max(0, sh - req_h)))
            if int(win.winfo_width()) != int(req_w) or int(win.winfo_height()) != int(req_h):
                win.geometry(f"{req_w}x{req_h}+{x}+{y}")
        except Exception:
            pass

    def _prepare_advanced_tab_sizes(self):
        win = self.advanced_window
        notebook = getattr(self, "_advanced_notebook", None)
        if win is None or notebook is None:
            return
        try:
            tabs = list(notebook.tabs())
            if not tabs:
                return
            current = notebook.select()
            sizes = {}
            for tab_id in tabs:
                notebook.select(tab_id)
                win.update_idletasks()
                tab = notebook.nametowidget(tab_id)
                nb_w = max(280, int(tab.winfo_reqwidth()) + 14)
                nb_h = max(80, int(tab.winfo_reqheight()) + 8)
                notebook.configure(width=nb_w, height=nb_h)
                win.update_idletasks()
                w = max(360, int(win.winfo_reqwidth()))
                h = max(180, int(win.winfo_reqheight()))
                sizes[tab_id] = (nb_w, nb_h, w, h)
            if current:
                notebook.select(current)
            self._advanced_tab_sizes = sizes
        except Exception:
            pass

    def _build_advanced_tab(self, tab_parent, column_sections):
        content = tk.Frame(tab_parent, padx=8, pady=8)
        content.pack(fill="x", expand=False, anchor="n")
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
                    is_last=(section_idx == len(sections) - 1),
                )

    def _build_advanced_section(self, parent, section_name, specs, is_last: bool = False):
        visible_specs = [spec for spec in specs if not spec.get("hidden")]

        def _register_var(spec):
            key = spec["key"]
            if key in self._advanced_vars:
                return
            self._advanced_meta[key] = spec
            if spec["type"] == "bool":
                self._advanced_vars[key] = tk.BooleanVar(value=False)
            else:
                self._advanced_vars[key] = tk.StringVar(value="")

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
                chk = tk.Checkbutton(section, text=spec["label"], variable=var, anchor="w")
                chk.grid(row=row, column=0, columnspan=2, sticky="w", pady=2)
            else:
                tk.Label(section, text=spec["label"]).grid(row=row, column=0, sticky="w", pady=2)
                entry = tk.Entry(section, textvariable=var, width=20)
                entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=2)
            row += 1

    def _advanced_general_columns(self):
        left = [
            (
                "Playback / Overlay",
                [
                    {"key": "UPDATE_INTERVAL_MS", "label": "Update interval (ms)", "type": "int", "default": 100, "min": 15, "max": 5000},
                    {"key": "SUBTITLE_TIMEOUT_MS", "label": "Subtitle timeout (ms)", "type": "int", "default": 7000, "min": 100, "max": 120000},
                    {"key": "WINDOWS_HIDE_DELAY_MS", "label": "Control hide delay desktop (ms)", "type": "int", "default": 7000, "min": 100, "max": 120000},
                    {"key": "PHONEMODE_WINDOWS_HIDE_DELAY_MS", "label": "Control hide delay phone (ms)", "type": "int", "default": 6000, "min": 100, "max": 120000},
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
                    {"key": "SUBTITLE_KEEP_SPEAKER_NAMES", "label": "Keep speaker names from (Name)", "type": "bool", "default": False},
                    {"key": "SUBTITLE_SPEAKER_TEMPLATE", "label": "Speaker template ({name})", "type": "str", "default": "<speaker:{name}> "},
                    {"key": "SUBTITLE_STRIP_PAREN_NOTES", "label": "Strip remaining (...) notes", "type": "bool", "default": True},
                    {"key": "SUBTITLE_AUTO_RUBY", "label": "Auto-add ruby for kanji-only lines", "type": "bool", "default": False},
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
                    {"key": "ANKI_ENABLED", "label": "Enable Anki integration", "type": "bool", "default": True},
                    {"key": "ANKI_CONNECT_URL", "label": "AnkiConnect URL", "type": "str", "default": "http://127.0.0.1:8765"},
                    {"key": "ANKI_HTTP_TIMEOUT_SEC", "label": "HTTP timeout (sec)", "type": "float", "default": 4.0, "min": 0.5, "max": 120.0},
                    {"key": "ANKI_BUSY_CURSOR", "label": "Busy cursor", "type": "str", "default": "wait"},
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
        ]
        right = [
            (
                "Hotkeys",
                [
                    {"key": "SHORTCUTS_DISABLED", "label": "Disable all hotkeys", "type": "bool", "default": False},
                    {"key": "DISABLE_SPACE_HOTKEY", "label": "Disable space play/pause", "type": "bool", "default": False},
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
        return [left, right]

    def _advanced_ocr_columns(self):
        left = [
            (
                "OCR Settings",
                [
                    {"key": "OCR_ENABLED", "label": "Enable startup/episode OCR sync", "type": "bool", "default": True},
                    {"key": "OCR_DEBUG", "label": "Debug print OCR text", "type": "bool", "default": True},
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
                {"key": f"OCR_REGION{suffix}_X", "label": f"Region {idx} X", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
                {"key": f"OCR_REGION{suffix}_Y", "label": f"Region {idx} Y", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
                {"key": f"OCR_REGION{suffix}_W", "label": f"Region {idx} W", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
                {"key": f"OCR_REGION{suffix}_H", "label": f"Region {idx} H", "type": "int", "default": 0, "min": 0, "max": 100000, "hidden": True},
            ])
        return [left]

    def _coerce_bool(self, value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        text = str(value).strip().lower()
        return text in ("1", "true", "yes", "on")

    def _coerce_int(self, value, default: int = 0, min_v=None, max_v=None) -> int:
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
        self._ocr_area_select_menu = tk.OptionMenu(left, self._ocr_area_select_var, "1")
        self._ocr_area_select_menu.pack(side="left", padx=(4, 10))

        self._ocr_area_clear_btn = tk.Button(left, text="Clear OCR Area", command=self._handle_clear_ocr_area)
        self._ocr_area_clear_btn.pack(side="left")
        self._ocr_area_clear_var = tk.StringVar(value="1")
        self._ocr_area_clear_menu = tk.OptionMenu(left, self._ocr_area_clear_var, "1")
        self._ocr_area_clear_menu.pack(side="left", padx=(4, 0))

        self._refresh_ocr_area_buttons()
        tk.Button(right, text="Read Now (Set Time)", command=self._handle_ocr_read_now).pack(side="right")

        screen_row = tk.Frame(actions)
        screen_row.pack(fill="x", pady=(8, 0))
        tk.Label(screen_row, text="Screen:").pack(side="left")
        self._build_ocr_screen_buttons(screen_row)

        self._update_ocr_screen_button_styles()

    def _get_ocr_region_count_from_vars(self) -> int:
        default = int(self.config.get("OCR_REGION_COUNT") or 2)
        try:
            var = getattr(self, "_advanced_vars", {}).get("OCR_REGION_COUNT")
            raw = var.get() if var is not None else default
        except Exception:
            raw = default
        return self._coerce_int(raw, default=default, min_v=1, max_v=self.OCR_MAX_REGIONS)

    def _refresh_ocr_area_buttons(self) -> None:
        select_var = getattr(self, "_ocr_area_select_var", None)
        clear_var = getattr(self, "_ocr_area_clear_var", None)
        select_menu = getattr(self, "_ocr_area_select_menu", None)
        clear_menu = getattr(self, "_ocr_area_clear_menu", None)
        if select_var is None and clear_var is None:
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

        if select_var is not None and select_var.get() not in options:
            select_var.set(options[0])
        if clear_var is not None and clear_var.get() not in options:
            clear_var.set(options[0])
        _refresh_menu(select_menu, select_var)
        _refresh_menu(clear_menu, clear_var)

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

        screen_idx = self._coerce_int(values.get("OCR_SCREEN_INDEX", 1), default=1, min_v=1, max_v=64)
        region_count = self._get_ocr_region_count_from_vars()
        area_any = False
        area_selected = False
        select_var = getattr(self, "_ocr_area_select_var", None)
        select_idx = self._coerce_int(select_var.get(), default=1, min_v=1, max_v=region_count) if select_var else 1
        for idx in range(1, region_count + 1):
            suffix = "" if idx == 1 else str(idx)
            rw = self._coerce_int(values.get(f"OCR_REGION{suffix}_W", 0), default=0)
            rh = self._coerce_int(values.get(f"OCR_REGION{suffix}_H", 0), default=0)
            selected = rw > 0 and rh > 0
            if selected:
                area_any = True
                if idx == select_idx:
                    area_selected = True

        select_btn = getattr(self, "_ocr_area_select_btn", None)
        self._apply_ocr_button_style(select_btn, area_selected)

        for idx, btn in getattr(self, "_ocr_screen_buttons", {}).items():
            active = (not area_any) and (idx == screen_idx)
            self._apply_ocr_button_style(btn, active)

    def _set_ocr_screen_index(self, index: int) -> None:
        self._ocr_selected_screen = int(index)
        var = getattr(self, "_advanced_vars", {}).get("OCR_SCREEN_INDEX")
        if var is not None:
            var.set(str(int(index)))
        region_count = self._get_ocr_region_count_from_vars()
        for idx in range(1, region_count + 1):
            self._set_ocr_region_vars(0, 0, 0, 0, index=idx)
        self._apply_ocr_values_runtime()
        self._update_ocr_screen_button_styles()

    def _set_ocr_region_vars(self, x: int, y: int, w: int, h: int, index: int = 1) -> None:
        suffix = "" if index == 1 else str(index)
        label = "OCR region" if index == 1 else f"OCR region {index}"
        mapping = {
            f"OCR_REGION{suffix}_X": x,
            f"OCR_REGION{suffix}_Y": y,
            f"OCR_REGION{suffix}_W": w,
            f"OCR_REGION{suffix}_H": h,
        }
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

    def _handle_ocr_read_now(self):
        values = self._get_ocr_values_from_vars()
        try:
            self._on_ocr_read_now(dict(values))
        except Exception:
            pass

    def _handle_select_ocr_area(self) -> None:
        var = getattr(self, "_ocr_area_select_var", None)
        try:
            index = int(var.get()) if var is not None else 1
        except Exception:
            index = 1
        self._select_ocr_region(index)

    def _handle_clear_ocr_area(self) -> None:
        var = getattr(self, "_ocr_area_clear_var", None)
        try:
            index = int(var.get()) if var is not None else 1
        except Exception:
            index = 1
        self._clear_ocr_region_index(index)

    def _clear_ocr_region(self):
        self._set_ocr_region_vars(0, 0, 0, 0, index=1)
        self._apply_ocr_values_runtime()

    def _clear_ocr_region_index(self, index: int):
        self._set_ocr_region_vars(0, 0, 0, 0, index=index)
        self._apply_ocr_values_runtime()

    def _select_ocr_region(self, index: int = 1):
        values = self._get_ocr_values_from_vars()
        selected = getattr(self, "_ocr_selected_screen", None)
        screen_idx = self._coerce_int(
            selected if selected is not None else values.get("OCR_SCREEN_INDEX", 1),
            default=1,
            min_v=1,
            max_v=64,
        )
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
                self._set_ocr_region_vars(0, 0, 0, 0, index=index)
            else:
                self._set_ocr_region_vars(x, y, w, h, index=index)
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
        try:
            screen_var = self._advanced_vars.get("OCR_SCREEN_INDEX")
            if screen_var is not None:
                self._ocr_selected_screen = self._coerce_int(screen_var.get(), default=1, min_v=1, max_v=64)
        except Exception:
            self._ocr_selected_screen = None
        if hasattr(self, "_advanced_status_var"):
            self._advanced_status_var.set("Loaded values from config.")
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

    def _reset_advanced_values_to_defaults(self):
        if not hasattr(self, "_advanced_vars") or not hasattr(self, "_advanced_meta"):
            return
        for key, spec in self._advanced_meta.items():
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
        if hasattr(self, "_advanced_status_var"):
            self._advanced_status_var.set("Loaded built-in default values.")

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
            for key, value in values.items():
                try:
                    self.config.set(key, value)
                except Exception:
                    pass
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Saved to config and applied.")
        else:
            if hasattr(self, "_advanced_status_var"):
                self._advanced_status_var.set("Applied for current session.")
        
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
        entry.delete(0, tk.END)
        entry.insert(0, self._format_seconds(value))

    def _apply_offset_change(self, value_seconds: float, persist: bool):
        self.slider.config(to=self.total_duration + value_seconds)
        self.update_time_and_subtitle_displays()
        self._on_slider_release(None)
        if persist:
            try:
                self.config.set("EXTRA_OFFSET", value_seconds)
            except Exception:
                pass

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
            setattr(self, attr, value)
            self._set_entry_value(entry, value)
            if entry is self.offset_entry:
                self._apply_offset_change(value, persist=True)
        entry.master.focus_set()

    def set_total_duration(self, total_duration: float):
        self.total_duration = total_duration
        self.slider.config(to=total_duration + self._last_offset_value)
