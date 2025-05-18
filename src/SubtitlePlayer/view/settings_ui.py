import tkinter as tk
from model.config_manager import ConfigManager
from utils import parse_time_value, make_draggable, format_time

class SettingsUI:
    def __init__(self, root: tk.Tk, config: ConfigManager, total_duration: float, initial_episode=None):
        self.root = root
        self.config = config
        self.initial_episode = initial_episode
        self.total_duration = total_duration
        self._load_config()

        for name in ("ep_change", "ep_inc", "ep_dec",
                     "slider_change", "slider_press", "slider_release",
                     "set_to", "open_srt", "show_handle",
                     #Control window:
                     "back", "forward", "play_pause",
                     "time_entry_return", "time_entry_clear"):
            setattr(self, f"_on_{name}", lambda *a, **k: None)

        self.episode_inc_btn = None
        self.episode_dec_btn = None
        self.mode_toggle_btn = None
        self.play_pause_btn = None
        self.slider = None

        self._last_offset_value = None
        self._last_skip_value = None

        self._build_settings_frame()
        self._build_control_window()
        self._last_offset_value = self.offset_var.get()
        self._last_skip_value = self.skip_var.get()

    def _load_config(self):
        get = self.config.get
        self.offset_default = get('EXTRA_OFFSET')
        self.skip_default =   get('DEFAULT_SKIP')
        self.default_start =  get('DEFAULT_START_TIME')
        self.ratio =          get('RATIO')
 
        self.win_x, self.win_y = get('CONTROL_WINDOW_X'),     get('CONTROL_WINDOW_Y')
        self.win_w, self.win_h = get('CONTROL_WINDOW_WIDTH'), get('CONTROL_WINDOW_HEIGHT')
        self.win_w_phone, self.win_h_phone = get('CONTROL_WINDOW_PHONE_MODE_WIDTH'), get('CONTROL_WINDOW_PHONE_MODE_HEIGHT')

        self.offset_var = tk.StringVar(value=f"{float(self.offset_default):.1f} s")
        self.skip_var   = tk.StringVar(value=f"{float(self.skip_default):.1f} s")

        if self.initial_episode is None:
            episode_display = "Movie"
        else:
            episode_display = str(self.initial_episode)
        self.episode_var = tk.StringVar(value=episode_display)
        self.setto_var = tk.StringVar(value="")
        self.phone_mode = tk.BooleanVar(value=False)
    
        self.play_time_var = tk.StringVar(value=format_time(self.default_start))


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
        self.offset_entry.bind("<FocusOut>", self._on_offset_focus_out)
        self.offset_entry.bind("<Return>", self._on_offset_focus_out)
        self.offset_entry.bind("<Button-1>", lambda e : self._on_setting_clear_offset_entry(e))

        
        # Skip entry.
        tk.Label(options_frame, text="Skip:", font=("Arial",12), bg="#f0f0f0")\
            .grid(row=0, column=2, padx=0, pady=5, sticky="e")
        self.skip_entry = tk.Entry(options_frame, textvariable=self.skip_var, font=("Arial",12), width=7)
        self.skip_entry.grid(row=0, column=3, padx=(0,5), pady=5, sticky="ew")
        self.skip_entry.bind("<FocusOut>", self._on_skip_focus_out)
        self.skip_entry.bind("<Return>", self._on_skip_focus_out)        
        self.skip_entry.bind("<Button-1>", lambda e: self._on_setting_clear_skip_entry(e))

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
        self.episode_entry.bind("<FocusOut>", lambda e: (self._on_ep_change(), self.root.focus()))
        self.episode_entry.bind("<Return>", lambda e: (self._on_ep_change(), self.root.focus()))
        
        self.episode_inc_btn = tk.Button(episode_frame, text="-", font=("Arial", 8, "bold"), width=1, height=1,
                                         command=lambda: self._on_ep_dec())
        self.episode_inc_btn.grid(row=0, column=1, sticky="e")
        self.episode_dec_btn = tk.Button(episode_frame, text="+", font=("Arial", 8, "bold"), width=1, height=1,
                                         command=lambda: self._on_ep_inc())
        self.episode_dec_btn.grid(row=0, column=2, sticky="e")

        # Set to
        tk.Label(options_frame, text="Set to:", font=("Arial", 12), bg="#f0f0f0")\
            .grid(row=1, column=2, padx=0, pady=(5,0), sticky="e") ##################
        self.setto_entry = tk.Entry(options_frame,textvariable=self.setto_var, font=("Arial", 12), width=7)
        self.setto_entry.grid(row=1, column=3, padx=(0,5), pady=(5,0), sticky="we")
        self.setto_entry.bind("<Return>", lambda e: self._on_set_to(self.setto_var.get()))

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
            text=self.play_time_var.get(),
            font=("Arial", 10)
        )

        # Slider
        self.slider  = tk.Scale(
            self.slider_frame,
            from_=0, to=self.total_duration,
            orient="horizontal",
            #length=int(self.total_duration//4.6),
            resolution=0.1,
            showvalue=False,
            sliderlength=32,
            command=lambda v: self._on_slider_change(v)
        )
        self.slider.grid(row=1, column=0, sticky="ew", padx=0, pady=0)
        self.slider.bind("<ButtonPress-1>", lambda e: self._on_slider_press(e))
        self.slider.bind("<ButtonRelease-1>", lambda e: self._on_slider_release(e))
        self.slider.bind("<Configure>", lambda e: self.update_time_overlay_position())
        self.slider.set(float(self.default_start))
        self.update_time_overlay_position()
        
        # self.root.update_idletasks()
        # print(self.root.geometry())

    # ——— CONTROL WINDOW ——————————————————————————————————————
    def _build_control_window(self):
        self.control_window = tk.Toplevel(self.root)
        self.control_window.overrideredirect(True)
        self.control_window.attributes("-topmost", True)
        w = self.win_w_phone if self.phone_mode.get() else self.win_w
        h = self.win_h_phone if self.phone_mode.get() else self.win_h
        self.control_window.geometry(f"{w}x{h}+{self.win_x}+{self.win_y}")

        self.control_drag_handle = tk.Frame(self.control_window, bg="gray", width=10, height=10)
        self.control_drag_handle.place(x=0, y=0)
        self.control_drag_handle.lift()
        make_draggable(self.control_drag_handle, self.control_window)

        main_frame = tk.Frame(self.control_window, bg="black")
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure((0,1,2), weight=1, minsize=60)
        main_frame.rowconfigure((0,1), weight=1, minsize=20)

        self.back_button = tk.Button(main_frame, text="<< Skip", font=("Arial", 12, "bold"),
                                      width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")

        self.play_time_entry = tk.Entry(main_frame, textvariable=self.play_time_var,
                                        font=("Arial", 14, "bold"), bd=0,
                                        bg="black", fg="white", width=6, justify="center")

        self.play_pause_btn = tk.Button(main_frame, text="Play", bg="green",
                                            activebackground="green", font=("Arial", 12, "bold"), height=1, relief="flat")
        
        self.forward_button = tk.Button(main_frame, text="Skip >>", font=("Arial", 12, "bold"),
                                        width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")

        self.back_button.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.play_pause_btn.grid(row=1, column=1,pady=0, sticky="nsew")
        self.play_time_entry.grid(row=0, column=1, sticky="nsew", ipady=5)
        self.forward_button.grid(row=0, column=2, rowspan=2, sticky="nsew")

        self.forward_button.bind("<ButtonPress>", lambda event: (self._on_time_entry_return(event), self._on_forward()))
        self.back_button.bind("<ButtonPress>", lambda event: (self._on_time_entry_return(event), self._on_back()))
        self.play_pause_btn.bind("<ButtonPress>", lambda event: (self._on_time_entry_return(event), self._on_play_pause()))
        self.play_time_entry.bind("<Return>", lambda ev:   self._on_time_entry_return(ev))
        self.play_time_entry.bind("<FocusOut>", lambda ev: self._on_time_entry_return(ev))
        self.play_time_entry.bind("<Button-1>", lambda ev: self._on_time_entry_clear(ev))

    # ——— PUBLIC binders ——————————————————————————————————————
    # Settings window
    def bind_episode_change(self, on_ent, on_inc, on_dec):
        self._on_ep_change = on_ent
        self._on_ep_inc    = on_inc
        self._on_ep_dec    = on_dec
    def bind_slider(self,   on_chg, on_pr, on_rl):
        self._on_slider_change = on_chg
        self._on_slider_press  = on_pr
        self._on_slider_release  = on_rl
    def bind_set_to_time(self, cb): self._on_set_to = cb
    def bind_open_srt(self, cb):  self._on_open_srt = cb
    def bind_show_subtitle_handle(self, cb): self._on_show_handle = cb

    # Control window
    def bind_back(self,      cb): self._on_back       = cb
    def bind_forward(self,   cb): self._on_forward    = cb
    def bind_play_pause(self,cb): self._on_play_pause = cb
    def bind_time_entry_return(self, cb): self._on_time_entry_return = cb
    def bind_time_entry_clear(self, cb): self._on_time_entry_clear = cb
    def bind_setting_clear_offset_entry(self, cb): self._on_setting_clear_offset_entry = cb
    def bind_setting_clear_skip_entry(self, cb): self._on_setting_clear_skip_entry = cb

    # ——— PHONE MODE UI ADJUSTMENT ————————————————————————————
    def _toggle_phone_mode(self):
        phone_mode = not self.phone_mode.get()
        self.phone_mode.set(phone_mode)

        new_width = self.win_w_phone if phone_mode else self.win_w
        new_height = self.win_h_phone if phone_mode else self.win_h

        x = self.control_window.winfo_x()
        x, y = self.control_window.winfo_x(), self.control_window.winfo_y() + self.control_window.winfo_height() - new_height
        self._set_phone_mode_styles(phone_mode)

        self.control_window.geometry(f"{new_width}x{new_height}+{x}+{y}")
        self.control_window.update_idletasks()
        self.mode_toggle_btn.configure(bg="green" if phone_mode else "SystemButtonFace")
        self.control_window.attributes("-topmost", True)

        self._on_show_handle(phone_mode)

    def _set_phone_mode_styles(self, phone_mode: bool):
        if phone_mode:
            self.control_drag_handle.config(width=40, height=40)
            f_large = ("Arial", 30, "bold")
            f_btn = ("Arial", 18, "bold")
        else:
            self.control_drag_handle.config(width=10, height=10)
            f_large = ("Arial", 14, "bold")
            f_btn = ("Arial", 12, "bold")

        self.play_time_entry.config(font=f_large)
        self.play_pause_btn.config(font=f_btn)
        self.back_button.config(font=f_btn)
        self.forward_button.config(font=f_btn)


    # ——— HELPERS ————————————————————————————————————————————


    def _on_setting_clear_offset_entry(self, event):
        val = self.offset_entry.get()
        if val.strip():
            self._last_offset_value = val
        self.offset_entry.delete(0, tk.END)
    
    def _on_setting_clear_skip_entry(self, event):
        val = self.skip_entry.get()
        if val.strip():
            self._last_skip_value = val
        self.skip_entry.delete(0, tk.END)

    def _on_offset_focus_out(self, event):
        val = self.offset_entry.get()
        try:
            parsed = parse_time_value(val, default_skip=None)
            if val.strip() == "" or parsed is None:
                self.offset_entry.delete(0, tk.END)
                value = self._last_offset_value if self._last_offset_value is not None else ""
                self.offset_entry.insert(0, str(value))
            else:
                self._last_offset_value = f"{parsed:.1f} s"
                self.offset_entry.delete(0, tk.END)
                self.offset_entry.insert(0, self._last_offset_value)
        except Exception:
            self.offset_entry.delete(0, tk.END)
            value = self._last_offset_value if self._last_offset_value is not None else ""
            self.offset_entry.insert(0, str(value))

    def _on_skip_focus_out(self, event):
        val = self.skip_entry.get()
        try:
            parsed = parse_time_value(val, default_skip=None)
            if val.strip() == "" or parsed is None:
                self.skip_entry.delete(0, tk.END)
                value = self._last_skip_value if self._last_skip_value is not None else ""
                self.skip_entry.insert(0, str(value))
            else:
                self._last_skip_value = f"{parsed:.1f} s"
                self.skip_entry.delete(0, tk.END)
                self.skip_entry.insert(0, self._last_skip_value)
        except Exception:
            self.skip_entry.delete(0, tk.END)
            value = self._last_skip_value if self._last_skip_value is not None else ""
            self.skip_entry.insert(0, str(value))

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
        self.time_overlay.move(self.time_overlay_text, x, 9+3)
        # # update the displayed text too
        self.time_overlay.coords(self.time_overlay_text, x, 9+3)
        self.time_overlay.itemconfig(self.time_overlay_text, text=self.play_time_var.get())