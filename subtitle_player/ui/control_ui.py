import tkinter as tk
from config_manager import ConfigManager
from utils import reformat_time_entry

class ControlUI:
    def __init__(self, root: tk.Tk, config: ConfigManager, total_duration: float):
        self.root = root
        self.config = config
        self.total_duration = total_duration

        self.ratio = config.get('RATIO')
        self.default_start = config.get('DEFAULT_START_TIME')
        self.skip_default = config.get('DEFAULT_SKIP')
        self.win_x = config.get('CONTROL_WINDOW_X')
        self.win_y = config.get('CONTROL_WINDOW_Y')
        self.win_w = config.get('CONTROL_WINDOW_WIDTH')
        self.win_h = config.get('CONTROL_WINDOW_HEIGHT')
        
        self.offset_var = tk.StringVar(value="0.0")
        self.skip_var   = tk.StringVar(value=str(self.skip_default))
        self.episode_var= tk.StringVar(value="614")
        self.setto_var = tk.StringVar(value="")
        self.play_time_var = tk.StringVar(value=self._format_time(self.default_start))
        self.use_phone_mode = tk.BooleanVar(value=False)
        self.episode_inc_btn = None
        self.episode_dec_btn = None

        self._on_back       = lambda: None
        self._on_forward    = lambda: None
        self._on_play_pause = lambda: None
        self._on_slider_change = lambda v: None
        self._on_slider_press  = lambda e: None
        self._on_slider_release  = lambda e: None
        self._on_ep_change  = lambda: None
        self._on_ep_inc     = lambda: None
        self._on_ep_dec     = lambda: None
        self._on_set_to     = lambda text: None

        self._build_settings_frame()
        self._build_control_window()

    # ——— SETTINGS FRAME - ————————————————————————————
    def _build_settings_frame(self):
        settings_frame = tk.LabelFrame(self.root)
        settings_frame.pack(fill="both", expand=True, padx=5, pady=5)

        options_frame = tk.Frame(settings_frame, bg="#f0f0f0")
        options_frame.grid(row=0, column=0, sticky="nw")

        # Offset entry.
        tk.Label(options_frame, text="Offset (sec):", font=("Arial",12), bg="#f0f0f0")\
            .grid(row=0, column=0, padx=5, pady=5, sticky="e")
        tk.Entry(options_frame, textvariable=self.offset_var,font=("Arial",12), width=7)\
            .grid(row=0, column=1, padx=5, pady=5, sticky="w")
        
        # Skip entry.
        tk.Label(options_frame, text="Skip:", font=("Arial",12), bg="#f0f0f0")\
            .grid(row=0, column=2, padx=5, pady=5, sticky="e")
        self.skip_entry = tk.Entry(options_frame, textvariable=self.skip_var, font=("Arial",12), width=7)
        self.skip_entry.grid(row=0, column=3, padx=5, pady=5, sticky="w")
        self.skip_entry.bind("<FocusOut>", lambda e: self.reformat_time_entry())

        # Phone-mode toggle and episode entry.
        phone_episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        phone_episode_frame.grid(row=1, column=0, padx=5, pady=5, sticky="w")

        self.mode_toggle_button = tk.Button(
            phone_episode_frame, text="📞", width=1, height=1,
            relief="raised", command=self._toggle_phone_mode
        )
        self.mode_toggle_button.pack(side="left", padx=(0,6))

        tk.Label(phone_episode_frame, text="Episode:",font=("Arial", 12), bg="#f0f0f0")\
            .pack(side="left")
        
        episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        episode_frame.grid(row=1, column=1, padx=5, pady=0, sticky="w")

        # Episode entry
        self.episode_entry = tk.Entry(episode_frame,textvariable=self.episode_var,font=("Arial",12), width=4)
        self.episode_entry.pack(side="left")
        self.episode_entry.bind("<Return>",lambda e: self._on_ep_change())

        #Plus and minus buttons
        self.episode_inc_btn = tk.Button(
            episode_frame, text="+", font=("Arial",8,"bold"),width=1, height=1,
            command=lambda: self._on_ep_inc())
        self.episode_inc_btn.pack(side="left")

        self.episode_dec_btn = tk.Button(
            episode_frame, text="-", font=("Arial",8,"bold"),width=1, height=1,
            command=lambda: self._on_ep_dec())
        self.episode_dec_btn.pack(side="left")

        # Set to
        tk.Label(options_frame, text="Set to:", font=("Arial", 12), bg="#f0f0f0")\
            .grid(row=1, column=2, padx=5, pady=0, sticky="e")
        self.setto_entry = tk.Entry(options_frame, font=("Arial", 12), width=7)
        self.setto_entry.grid(row=1, column=3, padx=5, pady=5, sticky="w")
        self.setto_entry.bind("<Return>", lambda e: self._on_set_to(self.setto_var.get()))

        # Slider
        self.slider  = tk.Scale(
            settings_frame,
            from_=0, to=self.total_duration,
            orient="horizontal",
            length=int(self.total_duration//4.6),
            resolution=0.1,
            showvalue=1,
            bg="#f0f0f0",
            command=lambda v: self._on_slider_change(v)
        )

        self.slider.grid(row=2, column=0, padx=0, pady=(0,5), sticky="ew")
        self.slider.bind("<ButtonPress-1>", lambda e: self._on_slider_press(e))
        self.slider.bind("<ButtonRelease-1>", lambda e: self._on_slider_release(e))
        self.slider.set(self.default_start)

        # Time overlay
        self.time_overlay = tk.Label(settings_frame,
            text=self.play_time_var.get(),
            font=("Arial",9),
            bg="#f0f0f0"
        )
        relx = self.ratio + (1 - 2*self.ratio)*(self.default_start/self.total_duration)
        self.time_overlay.place(in_=self.slider, relx=relx, rely=0.2, anchor="center")


    # ——— CONTROL WINDOW ——————————————————————————————————————
    def _build_control_window(self):
        w = self.win_w if self.use_phone_mode.get() else 180
        h = self.win_h if self.use_phone_mode.get() else 40
        cw = tk.Toplevel(self.root)
        cw.overrideredirect(True)
        cw.geometry(f"{w}x{h}+{self.win_x}+{self.win_y+(40-h)}")
        main = tk.Frame(cw, bg="black")
        main.pack(fill="both", expand=True)
        main.columnconfigure((0,2), weight=1)
        main.columnconfigure(1, weight=2)
        main.rowconfigure((0,1), weight=1)

        tk.Button(main, text="<< Skip", command=lambda: self._on_back())\
          .grid(row=0, column=0, rowspan=2, sticky="nsew")
        tk.Button(main, text="Skip >>", command=lambda: self._on_forward())\
          .grid(row=0, column=2, rowspan=2, sticky="nsew")

        tk.Button(main, text="Play", command=lambda: self._on_play_pause())\
          .grid(row=1, column=1, sticky="nsew")
        tk.Entry(
            main,
            textvariable=self.play_time_var,
            font=("Arial",14,"bold"),
            bg="black", fg="white", width=6,
            justify="center", relief="flat", state="readonly"
        ).grid(row=0, column=1, sticky="nsew")

        self.control_window = cw


    # ——— PUBLIC binders ——————————————————————————————————————
    def bind_back(self,     cb): self._on_back       = cb
    def bind_forward(self,  cb): self._on_forward    = cb
    def bind_play_pause(self,cb): self._on_play_pause = cb
    def bind_slider(self,   on_chg, on_pr, on_rl):
        self._on_slider_change = on_chg
        self._on_slider_press  = on_pr
        self._on_slider_release  = on_rl
    def bind_episode_change(self, on_ent, on_inc, on_dec):
        self._on_ep_change = on_ent
        self._on_ep_inc    = on_inc
        self._on_ep_dec    = on_dec
        self.episode_entry.bind("<Return>", lambda e: self._on_ep_change())
    def bind_set_to_time(self, cb):
        self._on_set_to = cb


    # ——— PHONE MODE UI ADJUSTMENT ————————————————————————————
    def _toggle_phone_mode(self):
        self.use_phone_mode.set(not self.use_phone_mode.get())
        # reposition control window:
        w = self.win_w if self.use_phone_mode.get() else 180
        h = self.win_h if self.use_phone_mode.get() else 40
        self.control_window.geometry(f"{w}x{h}+{self.win_x}+{self.win_y+(40-h)}")


    def _position_control_window(self):
        is_phone = self.use_phone_mode.get()
        w = self.win_w if is_phone else 180
        h = self.win_h if is_phone else 40
        x = self.win_x
        y = self.win_y + (40 - h)
        self.control_window.geometry(f"{w}x{h}+{x}+{y}")

    # ——— HELPERS ————————————————————————————————————————————
    @staticmethod
    def _format_time(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"
