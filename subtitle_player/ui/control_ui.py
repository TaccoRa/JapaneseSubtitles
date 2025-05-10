import tkinter as tk
from config_manager import ConfigManager

class ControlUI:
    def __init__(self, config: ConfigManager):
        self.config = config
        self.control_window_x = config.get('CONTROL_WINDOW_X')
        self.control_window_y = config.get('CONTROL_WINDOW_Y')
        self.ratio = config.get('RATIO')
        self.control_w_width = config.get('CONTROL_WINDOW_WIDTH')
        self.control_w_height = config.get('CONTROL_WINDOW_HEIGHT')
        self.control_w_x = config.get('CONTROL_WINDOW_X')
        self.control_w_y = config.get('CONTROL_WINDOW_Y')
        self.default_start_time = config.get('DEFAULT_START_TIME')

    def init_settings_window(self) -> None:
        settings_frame = tk.LabelFrame(self.root)
        settings_frame.pack(fill="both", expand=True, padx=5, pady=5)

        options_frame = tk.Frame(settings_frame, bg="#f0f0f0")
        options_frame.grid(row=0, column=0, sticky="nw")

        # Offset entry.
        tk.Label(options_frame, text="Offset (sec):", font=("Arial", 12), bg="#f0f0f0")\
            .grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.offset_var = tk.StringVar(value="0.0")
        self.offset_entry = tk.Entry(options_frame, textvariable=self.offset_var, font=("Arial", 12), width=7)
        self.offset_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        # Skip entry.
        tk.Label(options_frame, text="Skip:", font=("Arial", 12), bg="#f0f0f0")\
            .grid(row=0, column=2, padx=5, pady=5, sticky="e")
        self.skip_entry = tk.Entry(options_frame, font=("Arial", 12), width=7)
        self.skip_entry.insert(0, str(config['DEFAULT_SKIP']))
        self.skip_entry.grid(row=0, column=3, padx=5, pady=5, sticky="w")
        self.skip_entry.bind("<FocusOut>", lambda e: self.format_skip_entry())

        phone_episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        phone_episode_frame.grid(row=1, column=0, padx=5, pady=5, sticky="w")

        # Phone mode toggle button.
        self.mode_toggle_button = tk.Button(
            phone_episode_frame,
            text="📞",
            #font=("bold"),
            width=1, height=1,
            relief="raised",
            command=self.toggle_phone_mode
        )
        self.mode_toggle_button.pack(side="left", padx=(0,6))
                                     
        # Episode entry.
        tk.Label(
            phone_episode_frame,
            text="Episode:",
            font=("Arial", 12),
            bg="#f0f0f0"
        ).pack(side="left")

        episode_frame = tk.Frame(options_frame, bg="#f0f0f0")
        episode_frame.grid(row=1, column=1, padx=5, pady=0, sticky="w")
        # Episode entry inside the frame
        self.episode_var = tk.StringVar()
        match = re.search(r'E(\d+)', self.srt_path) # type: ignore
        self.episode_var.set(match.group(1) if match else "1")
        self.episode_entry = tk.Entry(episode_frame, textvariable=self.episode_var, font=("Arial", 12), width=4)
        self.episode_entry.pack(side="left")
        self.episode_entry.bind("<Return>", lambda e: self.on_episode_change())
        
        # Minus and Plus button next to the entry
        minus_button = tk.Button(episode_frame, text="-", font=("Arial", 8, "bold"), width=1,height=1, command=self.decline_episode)
        minus_button.pack(side="left")
        plus_button = tk.Button(episode_frame, text="+", font=("Arial", 8, "bold"), width=1,height=1, command=self.increment_episode)
        plus_button.pack(side="left")

        # "Set to" entry.
        tk.Label(options_frame, text="Set to:", font=("Arial", 12), bg="#f0f0f0")\
            .grid(row=1, column=2, padx=5, pady=0, sticky="e")
        self.setto_entry = tk.Entry(options_frame, font=("Arial", 12), width=7)
        self.setto_entry.grid(row=1, column=3, padx=5, pady=5, sticky="w")
        self.setto_entry.bind("<Return>", lambda e: self.set_to_time())
        
        # # Time slider.
        self.slider = tk.Scale(
            settings_frame,
            from_=0,
            to=self.total_duration,
            orient="horizontal",
            length=self.total_duration // 4.6,
            resolution=0.1,
            showvalue=1,
            command=self.on_slider_change,
            bg="#f0f0f0"
        )
        self.slider.grid(row=1, column=0, padx=0, pady=0, sticky="ew")
        self.slider.bind("<ButtonPress-1>", self.on_slider_press)
        self.slider.bind("<ButtonRelease-1>", self.on_slider_release)
        self.slider.set(config['DEFAULT_START_TIME'])

        formatted = self.format_time(config['DEFAULT_START_TIME'])
        self.time_overlay = tk.Label(settings_frame, text=formatted, font=("Arial", 9), bg="#f0f0f0")
        relx = config['RATIO'] + (1 - 2 * config['RATIO']) * (config['DEFAULT_START_TIME'] / self.total_duration)
        self.time_overlay.place(in_=self.slider, relx=relx, rely=0.2, anchor="center")
    
    def toggle_phone_mode(self):
        self.use_phone_mode.set(not self.use_phone_mode.get())
        self.update_phone_mode_ui()

    def set_to_time(self) -> None:
        new_time = self.parse_time_value(self.setto_entry.get())
        self.set_current_time(new_time)
        self.setto_entry.delete(0, tk.END)
        self.root.focus_set()

    def lower_controls(self) -> None:
        self.control_window.lower()
        self.hide_controls_job = None





    def build_controls(self):
        self.control_window = tk.Toplevel(self.root)
        self.control_window.overrideredirect(True)

        is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
        cw = 180 if not is_phone_mode else self.control_w_width
        ch = 40 if not is_phone_mode else self.control_w_height

        sh = self.root.winfo_screenheight()
        pos_x = self.control_window_x  #110
        base_y = self.control_window_y #1000
        offset = 40 - ch  # Move up if ch is larger
        pos_y = base_y + offset if ch != 40 else base_y  
        self.control_window.geometry(f"{cw}x{ch}+{pos_x}+{pos_y}")

        main_frame = tk.Frame(self.control_window, bg="black")
        main_frame.pack(fill="both", expand=True)
        main_frame.columnconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=2)
        main_frame.columnconfigure(2, weight=1)
        main_frame.rowconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)

        self.back_button = tk.Button(main_frame, text="<< Skip", font=("Arial", 12, "bold"),
                                      command=self.go_back, width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")
        self.back_button.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.forward_button = tk.Button(main_frame, text="Skip >>", font=("Arial", 12, "bold"),
                                        command=self.go_forward, width=6, height=2, bg="#3582B5", activebackground="#42A1E0", relief="flat")
        self.forward_button.grid(row=0, column=2, rowspan=2, sticky="nsew")

        formatted = self.format_time(self.default_start_time)
        self.play_time_var = tk.StringVar(value=formatted)
        self.play_time_entry = tk.Entry(main_frame, textvariable=self.play_time_var,
                                        font=("Arial", 14, "bold"),
                                        bg="black", fg="white", width=6, justify="center")
        self.play_time_entry.grid(row=0, column=1, sticky="nsew")

        # self.play_time_entry.bind("<FocusIn>", self.on_time_entry_focus_in)
        self.play_time_entry.bind("<Return>", self.on_time_entry_return)
        # self.play_time_entry.bind("<FocusOut>", self.on_time_entry_focus_out)
        self.play_time_entry.bind("<Button-1>", self.clear_time_entry)

        self.play_pause_button = tk.Button(main_frame, text="Play", font=("Arial", 12),
                                           command=self.toggle_play, height=1, relief="flat")
        self.play_pause_button.grid(row=1, column=1, sticky="nsew")

        self.back_button.bind("<ButtonPress>", self.force_update_entry)
        self.forward_button.bind("<ButtonPress>", self.force_update_entry)
        self.play_pause_button.bind("<ButtonPress>", self.force_update_entry)

        self.control_drag_handle = tk.Frame(self.control_window, bg="gray", width=10, height=10)
        self.control_drag_handle.place(x=0, y=0)
        self.control_drag_handle.lift()
        self.make_draggable(self.control_drag_handle, self.control_window)

    def on_slider_press(self, event: tk.Event) -> None:
        self.slider_dragging = True
        self.force_update_entry()

    def on_slider_release(self, event: tk.Event) -> None:
        self.slider_dragging = False
        self.set_current_time(float(self.slider.get()))

    def on_slider_change(self, value: str) -> None:
        if self.slider_dragging:
            self.set_current_time(float(value))

    def update_time_displays(self) -> None:
        formatted = self.format_time(self.current_time)
        relx = self.ratio + (1 - 2 * self.ratio) * (self.current_time / self.total_duration)
        self.time_overlay.config(text=formatted)
        self.time_overlay.place(in_=self.slider, relx=relx, rely=0.2)
        if not self.time_editing:
            self.play_time_var.set(formatted)

    def on_time_entry_focus_in(self, event) -> None:
        self.time_editing = True
        self.play_time_entry.delete(0, tk.END)
    def on_time_entry_return(self, event) -> None:
        self.commit_time_entry_change()
        self.time_editing = False
        self.force_update_entry()
    def on_time_entry_focus_out(self, event) -> None:
        if self.time_editing:
            self.play_time_var.set(self.format_time(self.current_time))
            self.time_editing = False
    def clear_time_entry(self, event) ->None:
        self.time_editing = True
        event.widget.delete(0, tk.END)
    def commit_time_entry_change(self) -> None:
        content = self.play_time_var.get().strip()
        if content == "":
            self.play_time_var.set(self.format_time(self.current_time))
            return
        try:
            new_time = self.parse_time_value(content)
            self.set_current_time(new_time)
        except Exception:
            pass
        self.play_time_var.set(self.format_time(self.current_time))

    def force_update_entry(self, event=None) -> None:
        self.play_time_var.set(self.format_time(self.current_time))


    def update_phone_mode_ui(self):
        is_phone_mode = self.use_phone_mode.get()
        self.mode_toggle_button.configure(bg="green" if is_phone_mode else "SystemButtonFace")
        self.control_window.attributes("-topmost", True)
        if hasattr(self, 'control_window') and self.control_window.winfo_exists():
            new_width = 180 if not is_phone_mode else self.control_w_width
            new_height = 40 if not is_phone_mode else self.control_w_height
            pos_x = self.control_w_x
            base_y = self.control_w_y
            offset = 40 - new_height
            pos_y = base_y + offset if new_height != 40 else base_y
            self.control_window.geometry(f"{new_width}x{new_height}+{pos_x}+{pos_y}")
        if hasattr(self, 'handle_win') and self.handle_win.winfo_exists():
            self.handle_win.attributes("-alpha", 0.05 if is_phone_mode else 0.0)
