#app.py
import os
import tkinter as tk
import tkinter.font as tkFont
from threading import Thread
import time
import logging

from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from view.popup import CopyPopup
from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from controller.controller import SubtitleController


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

class SubtitlePlayerApp:
    DEBOUNCE_MS = 100 #for saving current window position not all the time
    def __init__(self):
        logger.info("Starting SubtitlePlayerApp")

        self._load_config()
        self._load_subtitle_metadata()



        self._build_app_window()
        self._build_ui()
        self._build_model()
        self._build_controller()
        if self.config.get("REMOTE_FLAG"):
            self._download_init_season_asynch()
        self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)


    def _load_config(self):
        try:
            self.config = ConfigManager("config.json")
        except Exception:
            logger.exception("Failed to load config.json")
            raise SystemExit(1)
        
    def _load_subtitle_metadata(self):
        self.sub_manager = SubtitleManager(self.config)
        self.total_duration = self.sub_manager.get_total_duration()

    def _build_app_window(self):
        self.root = tk.Tk()
        self._restore_window_position()
        s = self.sub_manager.get_current_season()
        e = self.sub_manager.get_current_episode()
        n = self.sub_manager.get_anime_name()

        title = (n if (s and e) is None else
                 f"E{e} {n}" if s is None else
                 f"S{s}E{e} {n}")
        self.root.title(title)
        self.root.geometry(f"320x123")
        self.root.minsize(320, 123)
        self._save_after_id = None #save after id really needed?
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.deiconify()

    def _restore_window_position(self): #gets last saved position of settings window or centers it on the screen if out of bounds
        x = self.config.get("LAST_SETTINGS_WINDOW_X")
        y = self.config.get("LAST_SETTINGS_WINDOW_Y")
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        self.root.geometry(f"+{x}+{y}")

    def _on_root_configure(self, event):
        if self._save_after_id is not None:
            self.root.after_cancel(self._save_after_id)
        self._save_after_id = self.root.after(self.DEBOUNCE_MS, self._save_settings_window_pos)

    def _save_settings_window_pos(self):
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        self.config.set("LAST_SETTINGS_WINDOW_X", x)
        self.config.set("LAST_SETTINGS_WINDOW_Y", y)
        self._save_after_id = None
       

    def _build_ui(self):
        # UI (View)
        self.popup = CopyPopup(root=self.root, config=self.config)

        overlay_geometry = self.sub_manager.calculate_geometry()
        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root, config=self.config,
            cleaned_subs=[item[0] for item in self.sub_manager.display_data],
            overlay_geometry=overlay_geometry) #width, height
        
        self.settings_ui = SettingsUI(
            root=self.root, config=self.config,
            total_duration=self.total_duration,
            initial_episode=self.sub_manager.get_current_episode()
            )

    def _build_model(self):
        self.renderer = SubtitleRenderer(
            config=self.config,
            canvas=self.sub_overlay_ui.subtitle_canvas
            )

    def _build_controller(self):
        self.controller = SubtitleController(
            manager=self.sub_manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=self.config,
            total_duration=self.total_duration)

    def _download_init_season_asynch(self):
        init_url = self.sub_manager._parse_github_url(self.config.get("LAST_GITHUB_URL"))
        (owner, repo, ref, path, remote_folder, anime_folder_name,
        file_name, season_num, episode_num) = self.sub_manager.get_episode_metadata()
        season_dir = self.sub_manager._season_cache_dir()
        season_files = self.sub_manager._search_srt_files_in_folders([remote_folder], season_num)
        self.sub_manager.download_remaining_season_async(season_files, file_name, season_dir, window = 15)
 
    def run(self):
        self.root.mainloop()


# class SplashScreen:
#     def __init__(self, root, text="Loading…", width=300, height=150):
#         self.splash = tk.Toplevel(root)
#         self.splash.overrideredirect(True)
#         self.splash.attributes("-topmost", True)

#         # Center on screen
#         sw, sh = self.splash.winfo_screenwidth(), self.splash.winfo_screenheight()
#         x = (sw - width) // 2
#         y = (sh - height) // 2
#         self.splash.geometry(f"{width}x{height}+{x}+{y}")

#         label = tk.Label(self.splash, text=text, font=("Arial", 14))
#         label.pack(expand=True, fill="both", padx=20, pady=20)
#         self.splash.update_idletasks()

#     def close(self):
#         self.splash.destroy()
