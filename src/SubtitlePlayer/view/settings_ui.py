import tkinter as tk
from re import fullmatch
from model.config_manager import ConfigManager
from utils import make_draggable, format_time

class SettingsUI:
    OFFSET_PATTERN = r"\s*([-+]?\d+(?:\.\d+)?)\s*s?"

    def __init__(self, root: tk.Tk, config: ConfigManager, total_duration: float, initial_episode=None):
        self.root = root
        self.config = config
        self.total_duration = total_duration
        self.initial_episode = initial_episode

        self._init_defaults()
        self._init_vars()
        self._init_callbacks()

        self.episode_inc_btn = None
        self.episode_dec_btn = None
        self.mode_toggle_btn = None
        self.play_pause_btn = None
        self.slider = None

        self._build_settings_frame()
        self._build_control_window()

    def _init_defaults(self):
        get = self.config.get
        self.default_offset = get('EXTRA_OFFSET')        
        self._last_offset_value = float(self.default_offset)
        self.default_skip = get('DEFAULT_SKIP')
        self._last_skip_value   = float(self.default_skip)
        self.default_start = get('DEFAULT_START_TIME')
        self.default_phone_mode = get("PHONEMODE_DEFAULT")
        self.ratio = get('RATIO')

        self.default_x = self.config.get("LAST_SETTINGS_WINDOW_X")
        self.default_y = self.config.get("LAST_SETTINGS_WINDOW_Y")
        self.win_x = get('LAST_CONTROL_WINDOW_X')
        self.win_y = get('LAST_CONTROL_WINDOW_Y')

    def _init_vars(self):
        val = float(self.default_offset)
        self.offset_var = tk.StringVar(value=f"{int(val) if val.is_integer() else val} s")
        
        val = float(self.default_skip)
        self.skip_var = tk.StringVar(value=f"{int(val) if val.is_integer() else val} s")

        self.episode_var = tk.StringVar(value="Movie" if self.initial_episode is None else str(self.initial_episode))
        self.setto_var = tk.StringVar(value="")

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
                     "time_entry_return", "time_entry_clear"):
            setattr(self, f"_on_{name}", self._noop)

    # ——— SETTINGS FRAME ————————————————————————————
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
        options_frame.grid_columnconfigure(0, weight=1)
        options_frame.grid_columnconfigure(1, weight=1)
        options_frame.grid_columnconfigure(2, weight=1)
        options_frame.grid_columnconfigure(3, weight=1)
       
        # Row 0
        # rame for phone-mode toggle and offset
        phone_offset_frame = tk.Frame(options_frame, bg="#f0f0f0")
        phone_offset_frame.grid(row=0, column=0, padx=(5,0), pady=5, sticky="we")

        self.mode_toggle_btn = tk.Button(
            phone_offset_frame, text="📞", width=2, height=1,
            relief="raised", command=self._toggle_phone_mode
        )
        self.mode_toggle_btn.pack(side="left", padx=(0,5))

        # Offset entry.
        tk.Label(phone_offset_frame, text="Offset:", font=("Arial",12), bg="#f0f0f0")\
            .pack(side="right")
        self.offset_entry = tk.Entry(options_frame, textvariable=self.offset_var,font=("Arial",12), width=7)
        self.offset_entry.grid(row=0, column=1, padx=(0,5) , pady=5, sticky="we")
        self.offset_entry.bind("<Button-1>", self._clear_entry)
        self.offset_entry.bind("<FocusOut>", self._on_entry_focus_out)
        self.offset_entry.bind("<Return>",   self._on_entry_focus_out)
 
        
        # Skip entry.
        tk.Label(options_frame, text="Skip:", font=("Arial",12), bg="#f0f0f0")\
            .grid(row=0, column=2, padx=0, pady=5, sticky="e")
        self.skip_entry = tk.Entry(options_frame, textvariable=self.skip_var, font=("Arial",12), width=7)
        self.skip_entry.grid(row=0, column=3, padx=(0,5), pady=5, sticky="ew")
        self.skip_entry.bind("<Button-1>",   self._clear_entry)
        self.skip_entry.bind("<FocusOut>",   self._on_entry_focus_out)
        self.skip_entry.bind("<Return>",     self._on_entry_focus_out) 

        # Row 1
        # Frame for SRT button and episode
        srt_episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        srt_episode_frame.grid(row=1, column=0, padx=(5,0), pady=(5,0), sticky="we")

        self.srt_button = tk.Button(
            srt_episode_frame, text="SRT", width=2, height=1,
            relief="raised", command=lambda: self._on_open_srt())
        self.srt_button.pack(side="left", padx=(0,5))
        tk.Label(srt_episode_frame, text="Episode:",font=("Arial", 12), bg="#f0f0f0")\
            .pack(side="right")
        
        # Frame for Episode entry and plus/minus buttons
        episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        episode_frame.grid(row=1, column=1, padx=(0,5), pady=(5,0), sticky="ew")
        episode_frame.grid_columnconfigure(0, weight=1)
        episode_frame.grid_columnconfigure((1,2), weight=0)

        # Episode entry
        self.episode_entry = tk.Entry(episode_frame, textvariable=self.episode_var, font=("Arial", 12), width=7)
        self.episode_entry.grid(row=0, column=0, sticky="ew")
        self.episode_entry.bind("<Return>", lambda e: (self._on_ep_entry_change(), self.root.focus()))
        
        self.episode_dec_btn = tk.Button(episode_frame, text="-", font=("Arial", 8, "bold"), width=1, height=1,
                                         command=lambda: self._on_ep_dec())
        self.episode_dec_btn.grid(row=0, column=1, sticky="e")
        self.episode_inc_btn = tk.Button(episode_frame, text="+", font=("Arial", 8, "bold"), width=1, height=1,
                                         command=lambda: self._on_ep_inc())
        self.episode_inc_btn.grid(row=0, column=2, sticky="e")

        # Set to
        tk.Label(options_frame, text="Set to:", font=("Arial", 12), bg="#f0f0f0")\
            .grid(row=1, column=2, padx=0, pady=(5,0), sticky="e") ##################
        self.setto_entry = tk.Entry(options_frame,textvariable=self.setto_var, font=("Arial", 12), width=7)
        self.setto_entry.grid(row=1, column=3, padx=(0,5), pady=(5,0), sticky="we")
        self.setto_entry.bind("<Return>", lambda e: self._on_set_to_return(self.setto_var.get()))

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
            resolution=0.1,
            showvalue=False,
            sliderlength=32,
            command=lambda v: self._on_slider_change(v)
        )
        self.slider.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        self.slider.set(float(self.default_start))
        self.slider.bind("<ButtonPress-1>", lambda e: self._on_slider_press(e))
        self.slider.bind("<ButtonRelease-1>", lambda e: self._on_slider_release(e))
        self.update_time_overlay_position()


    # ——— CONTROL WINDOW ——————————————————————————————————————
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

        self.forward_button.bind("<ButtonPress>", lambda event: (self._on_time_entry_return(event), self._on_forward()))
        self.back_button.bind("<ButtonPress>", lambda event: (self._on_time_entry_return(event), self._on_back()))
        self.play_pause_btn.bind("<ButtonPress>", lambda event: (self._on_play_pause()))
        self.settings_btn.bind("<ButtonPress>", self._on_settings)
        self.refresh_btn.bind("<ButtonPress>", lambda ev: self.on_refresh_subtitles(ev))
        self.time_entry.bind("<Button-1>", lambda ev: self._on_time_entry_clear(ev))
        self.time_entry.bind("<FocusOut>", lambda ev: self._on_time_entry_return(ev))
        self.time_entry.bind("<Return>", lambda ev:   self._on_time_entry_return(ev))

        self.control_window.bind("<Enter>", lambda ev: self._on_control_window_enter(ev))
        self.control_window.bind("<Leave>", lambda ev: self._on_control_window_leave(ev))
        
    def _save_control_window_pos(self, x, y, w, h):
        self.config.set("LAST_CONTROL_WINDOW_X", x)
        self.config.set("LAST_CONTROL_WINDOW_Y", y)

    # ——— PUBLIC binders ——————————————————————————————————————
    # Settings window
    def bind_episode_change(self, on_ent, on_inc, on_dec):
        self._on_ep_entry_change = on_ent
        self._on_ep_inc          = on_inc
        self._on_ep_dec          = on_dec
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

    # ——— PHONE MODE UI ADJUSTMENT ————————————————————————————


    
    def _toggle_phone_mode(self):
        phone_mode = not self.default_phone_mode
        self.default_phone_mode = phone_mode
        self._set_phone_mode_styles(phone_mode)
        self.mode_toggle_btn.configure(bg="green" if phone_mode else "SystemButtonFace")
        self.control_window.attributes("-topmost", True)
        self._on_show_handle(self.default_phone_mode)
        
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



    # ——— HELPERS ————————————————————————————————————————————
    def _on_settings(self, event):#button to lift the root window
        self.root.deiconify()
        self.root.lift()
        
    def _format_offset(self, value: float) -> str:
        return f"{int(value) if value.is_integer() else value} s"
    
    def _get_last_value(self, entry):
        if entry is self.offset_entry: return "_last_offset_value", self._last_offset_value
        elif entry is self.skip_entry: return "_last_skip_value", self._last_skip_value

    def _clear_entry(self, event):
        entry = event.widget
        attr, _ = self._get_last_value(entry)
        match = fullmatch(self.OFFSET_PATTERN, entry.get().replace(",", ".").strip())
        if match:
            setattr(self, attr, float(match.group(1)))
        entry.delete(0, tk.END)

    def _on_entry_focus_out(self, event):
        entry = event.widget
        attr, last_val = self._get_last_value(entry)
        text = entry.get().replace(",", ".").strip()
        formatted = self._format_offset(last_val)

        if text != formatted:
            match = fullmatch(self.OFFSET_PATTERN, text)
            if match:
                number = float(match.group(1))
                setattr(self, attr, number)
                entry.delete(0, tk.END)
                entry.insert(0, self._format_offset(number))
                if entry is self.offset_entry:
                    self.slider.config(to=self.total_duration + number)
                    self.update_time_and_subtitle_displays()
                    self._on_slider_release(None)
            else:
                entry.delete(0, tk.END)
                entry.insert(0, formatted)
        entry.master.focus_set()

