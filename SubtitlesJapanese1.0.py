import os
import re
import sys
import time
import json
import bisect
from typing import List, Optional

import srt
import pyautogui
from pynput.mouse import Button, Listener
from pynput.keyboard import Key, Listener as KeyboardListener
import chardet

import tkinter as tk
from tkinter import filedialog, font as tkFont

#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Restricted
# keep this for later information: to download subtitles:
# https://kitsunekko.net/dirlist.php?dir=subtitles/japanese/One_Piece/&sort=date&order=asc



# class SubtitlePlayer:
# class SubtitleController:
# class SubtitleRenderer:
# class SubtitleManager:
# class ControlUI:
# class SubtitleOverlayUI:
# class InteractionManager:
# class CopyPopup:

def load_config(config_path: str) -> dict:
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

config = load_config('config.json')

def update_debug_srt_file_in_config(new_season: int, new_episode: int, config_path: str = "config.json") -> None:
    # Read the current configuration
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    
    debug_srt_file = config_data.get("DEBUG_SRT_FILE", "")

    def replace_season_episode(match):
        # Preserve the zero-padding based on the original file name parts
        season_str = str(new_season).zfill(len(match.group(1)))
        episode_str = str(new_episode).zfill(len(match.group(2)))
        return f"S{season_str}E{episode_str}"
    
    # Look for a pattern like S01E05 in the file name
    new_debug_srt_file = re.sub(r'S(\d+)E(\d+)', replace_season_episode, debug_srt_file, count=1)
    config_data["DEBUG_SRT_FILE"] = new_debug_srt_file

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4, ensure_ascii=False)


class SubtitlePlayer:

    CLEAN_PATTERN_1 = re.compile(r'\{\\an\d+\}')
    CLEAN_PATTERN_2 = re.compile(r'[（(].*?[）)]')

    def __init__(self) -> None:
        srt_path_debug = config['DEBUGGING']
        self.srt_path = config['DEBUG_SRT_FILE'] if srt_path_debug else self.prompt_srt_file()
        if not self.srt_path: sys.exit(0)

        self.srt_dir = os.path.dirname(self.srt_path)
        self._srt_file_list = [
            f for f in os.listdir(self.srt_dir)
            if f.lower().endswith('.srt')]
        self.subtitles: List[srt.Subtitle] = self.load_subtitles(self.srt_path)
        self.cleaned_subtitles = [self._clean_text(sub.content) for sub in self.subtitles]
        self.total_duration: float = self.get_total_duration()
        self.start_times: List[float] = [sub.start.total_seconds() for sub in self.subtitles]
        self.subtitle_color = config['SUBTITLE_COLOR']

        self.root = tk.Tk()
        self.root.title("Subtitle Player Settings")
        self.root.geometry("330x130")
        self.root.minsize(340, 130)
        self.root.configure(bg="#f0f0f0")
        self.subtitle_font = tkFont.Font(family=config['SUBTITLE_FONT'], size=config['SUBTITLE_FONT_SIZE'], weight="bold")
        self.line_height = self.subtitle_font.metrics("linespace")
        self.max_width = self.measure_max_subtitle_width()

        self.current_time: float = config['DEFAULT_START_TIME']
        self.user_offset: float = 0.0
        self.last_update: float = time.time()
        self.hide_delay: int = config['WINDOWS_HIDE_DELAY_MS']
        self.disappear_timer: int = config['PHONEMODE_WINDOWS_HIDE_DELAY_MS']
        self.last_subtitle_text: str = ""
        self.subtitle_timeout_job: Optional[str] = None
        self.hide_controls_job: Optional[str] = None
        self.control_hide_timer_job: Optional[str] = None
        self.alt_reset_done: bool = False
        self.user_hidden: bool = False
        self.slider_dragging: bool = False
        self.user_hidden: bool = False
        self.mouse_over_controls: bool = False
        self.mouse_over_subtitles: bool = False
        self.playing: bool = False
        self.time_editing: bool = False
        self.video_click: bool = config['VIDEO_CLICK']
        self.use_phone_mode = tk.BooleanVar(value=False)
        self.init_settings_window()
        self.init_subtitle_window()
        self.init_control_window()
        self.bind_mouse_events()
        self.update_phone_mode_ui()
        self.control_window.attributes("-topmost", True)
        self.play_pause_button.config(text="Play", bg="green", font = "bold", activebackground="green")

        self.global_mouse_listener = Listener(on_click=self.on_global_click)
        self.global_mouse_listener.start()
        self.alt_pressed = False
        self.keyboard_listener = KeyboardListener(
            on_press=self._on_key_press,
            on_release=self._on_key_release)
        self.keyboard_listener.start()

        # self.root.after(0, self.toggle_play)
        self.root.after(config['UPDATE_INTERVAL_MS'], self.update_loop)
    
    # ---------- File & Subtitles Loading ----------
    def _on_key_press(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = True
        elif hasattr(key, "char") and key.char and key.char.lower() == "x" and self.alt_pressed:
            self.on_alt_x()
        elif hasattr(key, "char") and key.char and key.char.lower() == "c" and self.alt_pressed:
            self.on_alt_c()
        elif hasattr(key, "char") and key.char and key.char.lower() == "y" and self.alt_pressed:
            self.on_alt_y()

    def _on_key_release(self, key):
        if key in (Key.alt_l, Key.alt_r):
            self.alt_pressed = False


    def on_alt_x(self, event=None):
        self.control_window.attributes("-topmost", True)
    def on_alt_c(self, event=None):
        self.increment_episode()
    def on_alt_y(self, event=None):
        self.decline_episode()

    def prompt_srt_file(self) -> Optional[str]:
        window = tk.Tk()
        window.attributes("-topmost", True)
        window.withdraw()
        file = filedialog.askopenfilename(parent=window,title="Select SRT File",filetypes=[("SubRip files", "*.srt"), ("All Files", "*.*")])
        window.destroy()
        return file


    def load_subtitles(self, srt_path):
        with open(srt_path, 'rb') as f:
            raw_data = f.read()
        detected = chardet.detect(raw_data)
        encoding = detected['encoding']

        srt_content = raw_data.decode(encoding)
        return list(srt.parse(srt_content))
    
    def _find_srt_for(self, season: int, episode: int) -> Optional[str]:
        pat1 = re.compile(rf'S0*{season}E0*{episode}\b', re.IGNORECASE)
        for fn in self._srt_file_list:
            if pat1.search(fn):
                return fn
        pat2 = re.compile(rf'E0*{episode}\b', re.IGNORECASE)
        for fn in self._srt_file_list:
            if pat2.search(fn):
                return fn
        return None

    def _load_new_srt(self, filename: str, season: int, episode: int) -> None:
        new_path = os.path.join(self.srt_dir, filename)
        self.srt_path = new_path
        self.subtitles = self.load_subtitles(new_path)
        self.cleaned_subtitles = [self._clean_text(s.content) for s in self.subtitles]
        self.total_duration = self.get_total_duration()
        self.start_times = [s.start.total_seconds() for s in self.subtitles]

        # reset slider/time
        self.slider.config(to=self.total_duration)
        self.current_time = config['DEFAULT_START_TIME']
        self.slider.set(config['DEFAULT_START_TIME'])
        self.update_time_displays()
        self.update_subtitle_display()

        # update episode entry and debug config
        self.episode_var.set(str(episode))
        update_debug_srt_file_in_config(new_season=season, new_episode=episode + 1)
        self.sub_window.focus_force()

    def get_total_duration(self) -> float:
        return max(sub.end.total_seconds() for sub in self.subtitles)

    def _clean_text(self, text: str) -> str:
        cleaned = self.CLEAN_PATTERN_1.sub('', text)
        cleaned = self.CLEAN_PATTERN_2.sub('', cleaned)
        cleaned = cleaned.replace('&lrm;', '').replace('\u200e', '')
        return cleaned.strip()

    def measure_max_subtitle_width(self) -> int:
        return max(
            self.subtitle_font.measure(line)
            for text in self.cleaned_subtitles
            for line in text.splitlines())

    # ----- UI Initialization -----

    ############# SETTINGS Window ###############
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
        self.skip_entry.bind("<FocusOut>", lambda e: self.reformat_time_entry(self.skip_entry))

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
        match = re.search(r'E(\d+)', self.srt_path)
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
        
        # Time slider.
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
    
    
    ############# SUBTITLES Window ###############
    
    def init_subtitle_window(self) -> None:
        self.sub_window = tk.Toplevel(self.root)
        self.sub_window.overrideredirect(True)
        self.sub_window.attributes("-topmost", True)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        init_height = self.line_height * 2
        pos_x = (sw - self.max_width) // 2
        pos_y = sh - init_height - 215
        self.sub_window.geometry(f"{self.max_width}x{init_height}+{pos_x}+{pos_y}")
        self.sub_window.attributes("-transparentcolor", "grey")
        self.sub_window_bottom_anchor = pos_y + init_height

        self.border_frame = tk.Frame(self.sub_window, bg="grey")
        self.border_frame.pack(fill="both", expand=True)
        self.subtitle_canvas = tk.Canvas(self.border_frame, bg="grey", highlightthickness=0)
        self.subtitle_canvas.pack(fill="both", expand=True)
        self.subtitle_canvas.bind("<Button-3>", self.open_copy_popup)  

        drag_w, drag_h = 60, init_height
        self.handle_win = tk.Toplevel(self.root)
        self.handle_win.attributes("-topmost", True)
        self.handle_win.overrideredirect(True)
        is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
        self.handle_win.attributes("-alpha", 0.05 if is_phone_mode else 0.0)
        self.handle_win.geometry(f"{drag_w}x{drag_h}+{pos_x}+{pos_y}")
        self.make_draggable(self.handle_win, self.sub_window, sync_windows=[self.handle_win]) # type: ignore

        self.make_draggable(self.sub_window, self.sub_window, sync_windows=[self.handle_win])


    ############# CONTROL Window ###############

    def init_control_window(self) -> None:
        self.control_window = tk.Toplevel(self.root)
        self.control_window.overrideredirect(True)

        is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
        cw = config.get('CONTROL_WINDOW_WIDTH') if not is_phone_mode else config.get('CONTROL_WINDOW_PHONE_MODE_WIDTH')
        ch = config.get('CONTROL_WINDOW_HEIGHT') if not is_phone_mode else config.get('CONTROL_WINDOW_PHONE_MODE_HEIGHT')

        sh = self.root.winfo_screenheight()
        pos_x = config['CONTROL_WINDOW_X']  #110
        base_y = config['CONTROL_WINDOW_Y'] #1000
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



        formatted = self.format_time(config['DEFAULT_START_TIME'])
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

    # ---------- Event Handlers & Utility Methods ----------

    def toggle_phone_mode(self):
        self.use_phone_mode.set(not self.use_phone_mode.get())
        self.update_phone_mode_ui()

    def update_phone_mode_ui(self):
        is_phone_mode = self.use_phone_mode.get()
        self.mode_toggle_button.configure(bg="green" if is_phone_mode else "SystemButtonFace")
        self.control_window.attributes("-topmost", True)
        if hasattr(self, 'control_window') and self.control_window.winfo_exists():
            new_width = config.get('CONTROL_WINDOW_WIDTH') if not is_phone_mode else config.get('CONTROL_WINDOW_PHONE_MODE_WIDTH')
            new_height = config.get('CONTROL_WINDOW_HEIGHT') if not is_phone_mode else config.get('CONTROL_WINDOW_PHONE_MODE_HEIGHT')

            pos_x = config['CONTROL_WINDOW_X']
            base_y = config['CONTROL_WINDOW_Y']
            offset = config.get('CONTROL_WINDOW_HEIGHT')  - new_height
            pos_y = base_y + offset if new_height != config.get('CONTROL_WINDOW_HEIGHT') else base_y
            self.control_window.geometry(f"{new_width}x{new_height}+{pos_x}+{pos_y}")

        if hasattr(self, 'handle_win') and self.handle_win.winfo_exists():
            self.handle_win.attributes("-alpha", 0.05 if is_phone_mode else 0.0)

    def on_time_entry_focus_in(self, event) -> None:
        self.time_editing = True
        self.play_time_entry.delete(0, tk.END)
    def on_time_entry_focus_out(self, event) -> None:
        if self.time_editing:
            self.play_time_var.set(self.format_time(self.current_time))
            self.time_editing = False

    def on_time_entry_return(self, event) -> None:
        self.commit_time_entry_change()
        self.time_editing = False
        self.force_update_entry()
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



        
    def set_to_time(self) -> None:
        new_time = self.parse_time_value(self.setto_entry.get())
        self.set_current_time(new_time)
        self.setto_entry.delete(0, tk.END)
        self.root.focus_set()

    def increment_episode(self) -> None:
        current_ep = int(self.episode_var.get())
        self.episode_var.set(str(current_ep + 1))
        self.on_episode_change()
        if self.playing:
            self.toggle_play()
        self.control_window.attributes("-topmost", True)
    def decline_episode(self) -> None:
        current_ep = int(self.episode_var.get())
        self.episode_var.set(str(current_ep - 1))
        self.on_episode_change()
        if self.playing:
            self.toggle_play()
        self.control_window.attributes("-topmost", True)

    def _render_subtitle(self, text: str) -> None:
        self.subtitle_canvas.delete("all")
        if not text: return
        lines = text.splitlines() or [""]
        if len(lines) == 1: lines.insert(0, "")
        total_height = self.line_height * len(lines)
        self.subtitle_canvas.config(width=self.max_width, height=total_height)
        y = 0
        for line in lines:
            self.draw_outlined_text(
                self.subtitle_canvas,
                self.max_width // 2,
                y + (self.line_height // 2),
                line,
                self.subtitle_font,
                fill=self.subtitle_color,
                outline="black",
                thickness=3,
            )
            y += self.line_height

        current_x = self.sub_window.winfo_x()
        new_y = self.sub_window_bottom_anchor - total_height
        self.sub_window.geometry(f"{self.max_width}x{total_height}+{current_x}+{new_y}")
    
    def go_forward(self) -> None:
        self.set_current_time(self.current_time + self.get_skip_value())
        self.schedule_hide_controls()
    def go_back(self) -> None:
        self.set_current_time(self.current_time - self.get_skip_value())
        self.schedule_hide_controls()
    def toggle_play(self) -> None:
        self.playing = not self.playing
        if self.playing:
            self.play_pause_button.config(text="Stop", bg="red", font = "bold", activebackground="red")
            self.last_update = time.time()
        else:
            self.play_pause_button.config(text="Play", bg="green", font = "bold", activebackground="green")
            if self.subtitle_timeout_job is not None:
                self.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            if self.user_hidden and self.last_subtitle_text:
                self.user_hidden = False
                self._render_subtitle(self.last_subtitle_text)
        
        if self.video_click: self.simulate_video_click()
        self.update_time_displays()
        self.schedule_hide_controls()


    # ----- Mouse and Drag Handlers -----

    def bind_mouse_events(self) -> None:
        for widget in [self.sub_window, self.border_frame]:
            widget.bind("<Enter>", self.on_sub_enter)
            widget.bind("<Leave>", self.on_sub_leave)
        self.control_window.bind("<Enter>", self.on_controls_enter)
        self.control_window.bind("<Leave>", self.on_controls_leave)
        self.control_window.bind("<Motion>", self.on_controls_enter)
        self.sub_window.bind("<Enter>", self.subtitle_hover_enter)
        self.sub_window.bind("<Leave>", self.subtitle_hover_leave)

    def on_controls_enter(self, event):
        self.set_mouse_over("controls", True)
    def on_controls_leave(self, event):
        self.set_mouse_over("controls", False)

    def on_sub_enter(self, event):
        self.set_mouse_over("subtitles", True)
    def on_sub_leave(self, event):
        self.set_mouse_over("subtitles", False)

    def make_draggable(self, drag_handle: tk.Widget, target: tk.Toplevel, sync_windows: list[tk.Toplevel] = None) -> None:
        def start_drag(event):
            drag_handle._drag_start_x = event.x_root
            drag_handle._drag_start_y = event.y_root
        def do_drag(event):
            dx = event.x_root - drag_handle._drag_start_x
            dy = event.y_root - drag_handle._drag_start_y
            new_x = target.winfo_x() + dx
            new_y = target.winfo_y() + dy
            target.geometry(f"+{new_x}+{new_y}")

            if sync_windows:
                for win in sync_windows:
                    win.geometry(f"+{new_x}+{new_y}")

            drag_handle._drag_start_x = event.x_root
            drag_handle._drag_start_y = event.y_root

            if target == self.sub_window:
                self.sub_window_bottom_anchor = new_y + target.winfo_height()
        drag_handle.bind("<ButtonPress-1>", start_drag)
        drag_handle.bind("<B1-Motion>", do_drag)

    def open_copy_popup(self, event: tk.Event) -> None:
        if hasattr(self, "copy_popup") and self.copy_popup is not None:
            try:
                if self.copy_popup.close_timer is not None:
                    self.copy_popup.after_cancel(self.copy_popup.close_timer)
            except Exception:
                pass
            self.copy_popup.destroy()
            self.copy_popup = None
    
        popup = tk.Toplevel(self.root)
        self.copy_popup = popup
        popup.overrideredirect(True)
        popup.configure(bg="white")
        popup.geometry("+0+0")
    
        copy_font = tkFont.Font(family="Arial", size=14, weight="bold")
        lines = self.last_subtitle_text.splitlines() or [""]
        num_lines = len(lines) + 1
        line_height = copy_font.metrics("linespace")
        req_height = line_height * num_lines
        req_width = max(copy_font.measure(line) for line in lines) if lines else 100
    
        text_widget = tk.Text(popup, wrap="none", font=copy_font, borderwidth=0,
                              highlightthickness=0, padx=0, pady=0, bg="white")
        text_widget.insert("1.0", self.last_subtitle_text)
        text_widget.config(state="disabled")
        text_widget.place(x=0, y=0, width=req_width, height=req_height)
        popup.geometry(f"{req_width}x{req_height}+0+0")
    
        popup.close_timer = popup.after(
            config['CLOSE_TIMER'], lambda: (popup.destroy(), setattr(self, "copy_popup", None)))

        def on_enter(e):
            if popup.close_timer is not None:
                popup.after_cancel(popup.close_timer)
                popup.close_timer = None
        def on_leave(e):
            if popup.close_timer is None:
                popup.close_timer = popup.after(
                    config['CLOSE_TIMER'], lambda: (popup.destroy(), setattr(self, "copy_popup", None)))

        popup.bind("<Enter>", on_enter)
        popup.bind("<Leave>", on_leave)
        popup.bind("<Destroy>", lambda e: setattr(self, "copy_popup", None))

    def subtitle_hover_enter(self, event) -> None:
        self.sub_window.attributes("-transparentcolor", "")
        self.control_window.attributes("-topmost", True)
    def subtitle_hover_leave(self, event) -> None:
        self.sub_window.attributes("-transparentcolor", "grey")
        self.control_window.attributes("-topmost", False)

    def set_mouse_over(self, area: str, flag: bool) -> None:
        if area == "controls":
            self.mouse_over_controls = flag
        elif area == "subtitles":
            self.mouse_over_subtitles = flag

        if self.mouse_over_controls or self.mouse_over_subtitles:
            if self.hide_controls_job is not None:
                self.root.after_cancel(self.hide_controls_job)
                self.hide_controls_job = None
            self.control_window.lift()
        else:
            is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
            if not is_phone_mode:
                if self.hide_controls_job is None:
                    self.hide_controls_job = self.root.after(self.hide_delay, self.lower_controls)

    def lower_controls(self) -> None:
        self.control_window.lower()
        self.hide_controls_job = None

    def draw_outlined_text(self, canvas: tk.Canvas, x: int, y: int, text: str,
                           font: tkFont.Font, fill: str, outline: str, thickness: int,
                           anchor: str = "center") -> None:
        for dx in range(-thickness, thickness + 1):
            for dy in range(-thickness, thickness + 1):
                if dx or dy:
                    canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor) # type: ignore

    # ----- Time & Subtitle Updates -----

    def on_slider_press(self, event: tk.Event) -> None:
        self.slider_dragging = True
        self.force_update_entry()

    def on_slider_release(self, event: tk.Event) -> None:
        self.slider_dragging = False
        self.set_current_time(float(self.slider.get()))

    def on_slider_change(self, value: str) -> None:
        if self.slider_dragging:
            self.set_current_time(float(value))

    def set_current_time(self, new_time: float) -> None:
        self.current_time = max(0.0, min(new_time, self.total_duration))
        self.last_update = time.time()
        self.slider.set(self.current_time)
        self.update_time_displays()
        self.update_subtitle_display()

    @staticmethod
    def format_time(seconds: float) -> str:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes:02d}:{secs:02d}"

    def update_time_displays(self) -> None:
        formatted = self.format_time(self.current_time)
        relx = config['RATIO'] + (1 - 2 * config['RATIO']) * (self.current_time / self.total_duration)
        self.time_overlay.config(text=formatted)
        self.time_overlay.place(in_=self.slider, relx=relx, rely=0.2)
        if not self.time_editing:
            self.play_time_var.set(formatted)

    def update_subtitle_display(self) -> None:
        self.user_offset = float(self.offset_var.get())
        effective_time = self.current_time - (self.user_offset + config['EXTRA_OFFSET'])
        index = bisect.bisect_right(self.start_times, effective_time) - 1
        new_text = self.cleaned_subtitles[index] if index >= 0 else ""
        if new_text == self.last_subtitle_text and self.user_hidden:
            return
        if new_text != self.last_subtitle_text:
            if self.subtitle_timeout_job is not None:
                self.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None
            self.last_subtitle_text = new_text                
            self.user_hidden = False
            self._render_subtitle(new_text)

        # schedule hide
        if self.playing and not self.subtitle_timeout_job:
            self.subtitle_timeout_job = self.root.after(
                config['SUBTITLE_TIMEOUT_MS'],
                self.hide_subtitles_temporarily
            )

    def hide_subtitles_temporarily(self):
        if not self.playing:
            self.subtitle_timeout_job = None
            return
        if not self.user_hidden:
            self.subtitle_canvas.delete("all")
            self.user_hidden = True
        self.subtitle_timeout_job = None

    @staticmethod
    def parse_time_value(text: str) -> float:
        text = text.strip()
        if ":" in text:
            parts = text.split(":")
            if len(parts) == 2:
                try:
                    minutes = int(parts[0])
                    seconds = int(parts[1])
                    return minutes * 60 + seconds
                except ValueError:
                    return config['DEFAULT_SKIP']
            else:
                return config['DEFAULT_SKIP']
        else:
            if text.isdigit():
                return float(text) if len(text) <= 2 else int(text[:-2]) * 60 + int(text[-2:])
            try:
                return float(text)
            except ValueError:
                return config['DEFAULT_SKIP']

    def reformat_time_entry(self, entry: tk.Entry) -> None:
        seconds_val = self.parse_time_value(entry.get())
        minutes, seconds = divmod(int(seconds_val), 60)
        formatted = f"{minutes:02d}:{seconds:02d}"
        entry.delete(0, tk.END)
        entry.insert(0, formatted)

    def get_skip_value(self) -> float:
        return self.parse_time_value(self.skip_entry.get())

    def on_episode_change(self) -> None:
        try:
            episode = int(self.episode_var.get().strip())
        except ValueError:
            return

        match = re.search(r'S(\d+)', self.srt_path, re.IGNORECASE)
        season = int(match.group(1)) if match else 1

        subtitle_file = self._find_srt_for(season, episode)
        if not subtitle_file:
            print(f"No .srt found for S{season}E{episode}")
            return

        full_path = os.path.join(self.srt_dir, subtitle_file)
        if full_path == self.srt_path:
            return

        self._load_new_srt(subtitle_file, season, episode)



    def simulate_video_click(self):
        original_pos = pyautogui.position()
        target_x = config["CONTROL_WINDOW_X"] + 50  #110
        target_y = config["CONTROL_WINDOW_Y"] - 50 #1000
        pyautogui.click(target_x, target_y)
        self.control_window.attributes("-topmost", True)
        pyautogui.moveTo(original_pos.x, original_pos.y)

    def on_global_click(self, x, y, button, pressed) -> None:
        if button == Button.x2 and pressed:
            self.on_subtitle_click(None)        

    def on_subtitle_click(self, event: tk.Event) -> None:
        self.user_hidden = not self.user_hidden
        if self.user_hidden:
            self.subtitle_canvas.delete("all")
    def schedule_hide_controls(self) -> None:
        is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
        if is_phone_mode:
            if self.control_hide_timer_job is not None:
                self.root.after_cancel(self.control_hide_timer_job)
            self.control_hide_timer_job = self.root.after(self.disappear_timer, self.lower_controls)

    def update_loop(self) -> None:
        if self.playing and not self.slider_dragging:
            now = time.time()
            delta = now - self.last_update
            self.current_time += delta
            self.last_update = now
            if self.current_time >= self.total_duration:
                self.current_time = self.total_duration
                self.playing = False
            self.slider.set(self.current_time)
            self.update_time_displays()
            self.update_subtitle_display()
        try:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
            sub_x, sub_y = self.sub_window.winfo_rootx(), self.sub_window.winfo_rooty()
            sub_w, sub_h = self.sub_window.winfo_width(), self.sub_window.winfo_height()
            inside = sub_x <= x <= sub_x + sub_w and sub_y <= y <= sub_y + sub_h
            if inside != self.mouse_over_subtitles:
                self.set_mouse_over("subtitles", inside)
        except Exception:
            pass
        self.root.after(config['UPDATE_INTERVAL_MS'], self.update_loop)

    def run(self) -> None:
        self.root.mainloop()

if __name__ == "__main__":
    player = SubtitlePlayer()
    player.run()
