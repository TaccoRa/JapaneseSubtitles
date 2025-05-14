import re
import tkinter as tk
from model.config_manager import ConfigManager
from utils import reformat_time_entry, parse_time_value, make_draggable

class ControlUI:
    def __init__(self, root: tk.Tk, config: ConfigManager, total_duration: float, initial_episode=None):
        self.root = root
        self.config = config
        self.total_duration = total_duration


        self.extra_offset= config.get('EXTRA_OFFSET')
        self.skip_default = config.get('DEFAULT_SKIP')
        self.default_start = config.get('DEFAULT_START_TIME')
        self.ratio = config.get('RATIO')

        self.win_x = config.get('CONTROL_WINDOW_X')
        self.win_y = config.get('CONTROL_WINDOW_Y')
        self.win_w = config.get('CONTROL_WINDOW_WIDTH')
        self.win_h = config.get('CONTROL_WINDOW_HEIGHT')
        self.win_w_phone = config.get('CONTROL_WINDOW_PHONE_MODE_WIDTH')
        self.win_h_phone = config.get('CONTROL_WINDOW_PHONE_MODE_HEIGHT')


        self.offset_var = tk.StringVar(value=self.extra_offset)
        self.skip_var   = tk.StringVar(value=str(self.skip_default))
        self.episode_var = tk.StringVar(value=str(initial_episode))
        self.setto_var = tk.StringVar(value="")
        self.phone_mode = tk.BooleanVar(value=False)
    
        self.play_time_var = tk.StringVar(value=self._format_time(self.default_start))

        self._on_ep_change  = lambda: None
        self._on_ep_inc     = lambda: None
        self._on_ep_dec     = lambda: None
        self._on_slider_change = lambda v: None
        self._on_slider_press  = lambda e: None
        self._on_slider_release  = lambda e: None
        self._on_set_to     = lambda text: None
        self._on_open_srt   = lambda: None

        self._on_back       = lambda: None
        self._on_forward    = lambda: None
        self._on_play_pause = lambda: None
        self._on_time_entry_return = lambda event: None
        self._on_time_entry_clear  = lambda event: None

        self.episode_inc_btn = None
        self.episode_dec_btn = None
        self.mode_toggle_button = None
        self.play_pause_button = None
        self.slider = None

        self._build_settings_frame()
        self._build_control_window()

    # ——— SETTINGS FRAME ————————————————————————————
    def _build_settings_frame(self):
        settings_frame = tk.LabelFrame(self.root)
        settings_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Frame for all except slider
        options_frame = tk.Frame(settings_frame, bg="#f0f0f0")
        options_frame.grid(row=0, column=0, sticky="news", pady=0)
        
        # rame for phone-mode toggle and offset
        phone_offset_frame = tk.Frame(options_frame, bg="#f0f0f0")
        phone_offset_frame.grid(row=0, column=0, padx=(5,0), pady=5, sticky="we")

        self.mode_toggle_button = tk.Button(
            phone_offset_frame, text="📞", width=2, height=1,
            relief="raised", command=self._toggle_phone_mode
        )
        self.mode_toggle_button.pack(side="left", padx=(0,5))

        # Offset entry.
        tk.Label(phone_offset_frame, text="Offset (sec):", font=("Arial",12), bg="#f0f0f0")\
            .pack(side="right")
        tk.Entry(options_frame, textvariable=self.offset_var,font=("Arial",12), width=7)\
            .grid(row=0, column=1, padx=(0,5) , pady=5, sticky="we")
        
        # Skip entry.
        tk.Label(options_frame, text="Skip:", font=("Arial",12), bg="#f0f0f0")\
            .grid(row=0, column=2, padx=0, pady=5, sticky="e")
        self.skip_entry = tk.Entry(options_frame, textvariable=self.skip_var, font=("Arial",12), width=7)
        self.skip_entry.grid(row=0, column=3, padx=(0,5), pady=5, sticky="ew")
        self.skip_entry.bind("<FocusOut>", lambda e: (reformat_time_entry(self.skip_entry,lambda txt: parse_time_value(txt, self.skip_default)), self.root.focus()))
        self.skip_entry.bind("<Return>", lambda e: (reformat_time_entry(self.skip_entry,lambda txt: parse_time_value(txt, self.skip_default)), self.root.focus()))

        # Frame for SRT button and episode
        srt_episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        srt_episode_frame.grid(row=1, column=0, padx=(5,0), pady=(5,0), sticky="we")

        self.srt_button = tk.Button(
            srt_episode_frame, text="SRT", width=2, height=1,
            relief="raised", command=lambda: self._on_open_srt())
        self.srt_button.pack(side="left")
        tk.Label(srt_episode_frame, text="Episode:",font=("Arial", 12), bg="#f0f0f0")\
            .pack(side="right")
        
        # Frame for Episode entry and plus/minus buttons
        episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        episode_frame.grid(row=1, column=1, padx=(0,5), pady=(5,0), sticky="ew")
        # Episode entry
        self.episode_entry = tk.Entry(episode_frame,textvariable=self.episode_var,font=("Arial",12), width=4)
        self.episode_entry.pack(side="left")
        self.episode_entry.bind("<FocusOut>",lambda e: (self._on_ep_change(), self.root.focus()))
        self.episode_entry.bind("<Return>",lambda e: (self._on_ep_change(), self.root.focus()))
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
            .grid(row=1, column=2, padx=0, pady=(5,0), sticky="e") ##################
        self.setto_entry = tk.Entry(options_frame,textvariable=self.setto_var, font=("Arial", 12), width=7)
        self.setto_entry.grid(row=1, column=3, padx=(0,5), pady=(5,0), sticky="we")
        self.setto_entry.bind("<Return>", lambda e: self._on_set_to(self.setto_var.get()))

        # Slider
        self.slider  = tk.Scale(
            settings_frame,
            from_=0, to=self.total_duration,
            orient="horizontal",
            #length=int(self.total_duration//4.6),
            resolution=0.1,
            showvalue=1,
            bg="#f0f0f0",
            command=lambda v: self._on_slider_change(v)
        )

        self.slider.grid(row=2, column=0, padx=0, pady=0, sticky="ew")
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
        # self.root.update_idletasks()
        # print("Geometry:", self.root.winfo_geometry())


    # ——— CONTROL WINDOW ——————————————————————————————————————
    def _build_control_window(self):
        w = self.win_w_phone if self.phone_mode.get() else self.win_w
        h = self.win_h_phone if self.phone_mode.get() else self.win_h
        self.control_window = tk.Toplevel(self.root)
        self.control_window.overrideredirect(True)
        self.control_window.attributes("-topmost", True)
        self.control_window.geometry(f"{w}x{h}+{self.win_x}+{self.win_y}")

        main_frame = tk.Frame(self.control_window, bg="black")
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure((0,2), weight=1)
        main_frame.columnconfigure(1, weight=2)
        main_frame.rowconfigure((0,1), weight=1)

        self.back_button = tk.Button(main_frame, text="<< Skip", font=("Arial", 12, "bold"),
                                      width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")
        self.back_button.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.back_button.bind("<ButtonPress>", lambda event: self._on_back())

        self.forward_button = tk.Button(main_frame, text="Skip >>", font=("Arial", 12, "bold"),
                                        width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")
        self.forward_button.grid(row=0, column=2, rowspan=2, sticky="nsew")
        self.forward_button.bind("<ButtonPress>", lambda event: self._on_forward())

        self.play_time_entry = tk.Entry(main_frame, textvariable=self.play_time_var,
                                        font=("Arial", 14, "bold"),
                                        bg="black", fg="white", width=6, justify="center")
        self.play_time_entry.grid(row=0, column=1, sticky="nsew")
        self.play_time_entry.bind("<Return>", self._on_time_entry_return)
        self.play_time_entry.bind("<Button-1>", self._on_time_entry_clear)


        self.play_pause_button = tk.Button(main_frame, text="Play", bg="green",
                                            activebackground="green", font=("Arial", 12), height=1, relief="flat")
        self.play_pause_button.grid(row=1, column=1, sticky="nsew")
        self.play_pause_button.bind("<ButtonPress>", lambda event: self._on_play_pause())

        self.control_drag_handle = tk.Frame(self.control_window, bg="gray", width=10, height=10)
        self.control_drag_handle.place(x=0, y=0)
        self.control_drag_handle.lift()
        make_draggable(self.control_drag_handle, self.control_window)





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
    def bind_set_to_time(self, cb):
        self._on_set_to = cb
    def bind_open_srt(self, cb):
        self._on_open_srt = cb
    def bind_show_subtitle_handle(self, cb):
        self._on_show_handle = cb

    # Control window
    def bind_back(self,      cb): self._on_back       = cb
    def bind_forward(self,   cb): self._on_forward    = cb
    def bind_play_pause(self,cb): self._on_play_pause = cb
    def bind_time_entry_return(self, cb):
        self._on_time_entry_return = cb
    def bind_time_entry_clear(self, cb):
        self._on_time_entry_clear = cb


    # ——— PHONE MODE UI ADJUSTMENT ————————————————————————————
    def _toggle_phone_mode(self):
        phone_mode = not self.phone_mode.get()
        self.phone_mode.set(phone_mode)

        new_width = self.win_w_phone if phone_mode else self.win_w
        new_height = self.win_h_phone if phone_mode else self.win_h

        height_diff = abs(self.win_h_phone - self.win_h)
        x = self.control_window.winfo_x()
        if phone_mode:
            y = self.control_window.winfo_y() - height_diff
        else:
            y = self.control_window.winfo_y() + height_diff
        self.control_window.geometry(f"{new_width}x{new_height}+{x}+{y}")
        self.control_window.update_idletasks()
        self.mode_toggle_button.configure(bg="green" if phone_mode else "SystemButtonFace")
        self.control_window.attributes("-topmost", True)
        self._on_show_handle(phone_mode)

    # ——— HELPERS ————————————————————————————————————————————
    @staticmethod
    def _format_time(seconds: float) -> str:
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"
