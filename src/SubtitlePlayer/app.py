#app.py
import os
import tkinter as tk
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
    DEBOUNCE_MS = 100
    def __init__(self):
        logger.info("Starting SubtitlePlayerApp")

        self._load_config()
        self._load_subtitle_metadata()



        # self._build_app_window()
        # self._bind_events()

        # self._build_model()
        # self._build_ui()
        # self._build_controller()



    def _load_config(self):
        try:
            self.config = ConfigManager("config.json")
        except Exception:
            logger.exception("Failed to load config.json")
            raise SystemExit(1)
        


    def _load_subtitle_metadata(self):
        self.sub_manager = SubtitleManager(self.config)
        self.total_duration = self.sub_manager.get_total_duration()
        #Get subtitle metadata...


















        self.config = ConfigManager("config.json")
        self.sub_manager = SubtitleManager(self.config)
        self.total_duration = self.sub_manager.get_total_duration()

        # App window
        self.root = tk.Tk()
        self.root.title("Subtitle Player Settings") 
        self.root.geometry("320x123")
        self.root.minsize(320, 123)
        self._restore_window_position()
        self._save_after_id = None
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.deiconify()

        # UI (View)
        self.popup = CopyPopup(root=self.root, config=self.config)

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root, config=self.config,
            cleaned_subs=[item[0] for item in self.sub_manager.display_data],
            overlay_geometry=self.sub_manager.calculate_geometry()) #height, width
        
        self.settings_ui = SettingsUI(
            root=self.root, config=self.config,
            total_duration=self.total_duration,
            initial_episode=self.sub_manager.get_current_episode())

        # Model
        self.renderer = SubtitleRenderer(
            config=self.config,
            canvas=self.sub_overlay_ui.subtitle_canvas
            )

        # Controller
        self.controller = SubtitleController(
            manager=self.sub_manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=self.config,
            total_duration=self.total_duration)
        

        self.download_season_asynch()
        self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)


    def download_season_asynch(self):
        init_url = self.sub_manager._parse_github_url(self.config.get("LAST_GITHUB_URL"))
        (owner, repo, ref, path,
         file_name, season_num, episode_num,
         anime_name, remote_folder) = self.sub_manager.extract_episode_metadata(init_url)
        season_dir = self.sub_manager._season_cache_dir(self.sub_manager.anime_folder_name, season_num, create=True)
        season_files = self.sub_manager._search_srt_files_in_folders(owner, repo, [remote_folder], season_num)
        self.sub_manager.download_remaining_season_async(owner, repo, ref, season_files, file_name, season_dir)

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
        
    def run(self):
        self.root.mainloop()


# import tkinter as tk
# from threading import Thread
# import logging


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


# class SubtitlePlayerApp:
#     DEBOUNCE_MS = 100

#     def __init__(self):
#         logger.info("Starting SubtitlePlayerApp")

#         # 1️⃣ Create root early but hide main window
#         self.root = tk.Tk()
#         self.root.withdraw()  # hide main window for now

#         # 2️⃣ Show splash
#         self.splash = SplashScreen(self.root, text="Loading subtitles…")

#         # 3️⃣ Run heavy loading in background
#         Thread(target=self._load_app, daemon=True).start()

#         # 4️⃣ Start Tkinter loop
#         self.root.mainloop()

#     def _load_app(self):
#         """Load config, subtitles, UI, and controller"""
#         try:
#             # Load config
#             self.config = ConfigManager("config.json")

#             # Load subtitle metadata
#             self.sub_manager = SubtitleManager(self.config)
#             self.total_duration = self.sub_manager.get_total_duration()

#             # Build UI (View)
#             self.popup = CopyPopup(root=self.root, config=self.config)
#             self.sub_overlay_ui = SubtitleOverlayUI(
#                 root=self.root,
#                 config=self.config,
#                 cleaned_subs=[item[0] for item in self.sub_manager.display_data],
#                 overlay_geometry=self.sub_manager.calculate_geometry()
#             )
#             self.settings_ui = SettingsUI(
#                 root=self.root,
#                 config=self.config,
#                 total_duration=self.total_duration,
#                 initial_episode=self.sub_manager.current_episode
#             )

#             # Model
#             self.renderer = SubtitleRenderer(
#                 config=self.config,
#                 canvas=self.sub_overlay_ui.subtitle_canvas
#             )

#             # Controller
#             self.controller = SubtitleController(
#                 manager=self.sub_manager,
#                 renderer=self.renderer,
#                 settings_ui=self.settings_ui,
#                 overlay_ui=self.sub_overlay_ui,
#                 popup=self.popup,
#                 config=self.config,
#                 total_duration=self.total_duration
#             )

#             # Schedule loop
#             self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

#             # Finalize UI on main thread
#             self.root.after(0, self._finalize_ui)
#         except Exception:
#             logger.exception("Failed to initialize app")
#             self.root.after(0, self.root.destroy)

#     def _finalize_ui(self):
#         """Close splash and show main window"""
#         self.splash.close()
#         self.root.deiconify()
#         self.root.title("Subtitle Player Settings")
#         self._restore_window_position()
#         self.root.bind("<Configure>", self._on_root_configure)

#     # Existing window position handling
#     def _restore_window_position(self):
#         x = self.config.get("LAST_SETTINGS_WINDOW_X")
#         y = self.config.get("LAST_SETTINGS_WINDOW_Y")
#         self.root.update_idletasks()
#         w, h = self.root.winfo_width(), self.root.winfo_height()
#         sw, sh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
#         x = max(0, min(x, sw - w))
#         y = max(0, min(y, sh - h))
#         self.root.geometry(f"+{x}+{y}")

#     def _on_root_configure(self, event):
#         if hasattr(self, "_save_after_id") and self._save_after_id is not None:
#             self.root.after_cancel(self._save_after_id)
#         self._save_after_id = self.root.after(self.DEBOUNCE_MS, self._save_settings_window_pos)

#     def _save_settings_window_pos(self):
#         x = self.root.winfo_x()
#         y = self.root.winfo_y()
#         self.config.set("LAST_SETTINGS_WINDOW_X", x)
#         self.config.set("LAST_SETTINGS_WINDOW_Y", y)
#         self._save_after_id = None













    # def _load_subtitle_metadata(self):
    #     # create the root early so the OS can paint a window immediately
    #     self.root = tk.Tk()
    #     self.root.title("Subtitle Player (loading...)")
    #     self.root.geometry("320x123")
    #     self.root.minsize(320, 123)
    #     self._restore_window_position()

    #     # show a minimal "loading" indicator before heavy init
    #     loading_label = tk.Label(self.root, text="Loading subtitles…", font=("Arial", 12))
    #     loading_label.pack(fill="both", expand=True, padx=20, pady=20)

    #     # force the window to be drawn right away
    #     self.root.update_idletasks()
    #     self.root.update()

    #     # now do the heavy work (blocks, but window is visible)
    #     self.sub_manager = SubtitleManager(self.config)
    #     self.total_duration = self.sub_manager.get_total_duration()

    #     # remove the loading indicator and continue building the UI
    #     loading_label.destroy()
    #     self.root.title("Subtitle Player Settings")

    #     # proceed to build the UI that depends on sub_manager
    #     self._save_after_id = None
    #     self.root.bind("<Configure>", self._on_root_configure)
    #     self.root.deiconify()

    #     # build rest of UI (same as your code)
    #     self.popup = CopyPopup(root=self.root, config=self.config)

    #     self.sub_overlay_ui = SubtitleOverlayUI(
    #         root=self.root, config=self.config,
    #         cleaned_subs=[item[0] for item in self.sub_manager.display_data],
    #         overlay_geometry=self.sub_manager.calculate_geometry())

    #     self.settings_ui = SettingsUI(
    #         root=self.root, config=self.config,
    #         total_duration=self.total_duration,
    #         initial_episode=self.sub_manager.current_episode)

    #     # Model
    #     self.renderer = SubtitleRenderer(
    #         config=self.config,
    #         canvas=self.sub_overlay_ui.subtitle_canvas
    #         )

    #     # Controller
    #     self.controller = SubtitleController(
    #         manager=self.sub_manager,
    #         renderer=self.renderer,
    #         settings_ui=self.settings_ui,
    #         overlay_ui=self.sub_overlay_ui,
    #         popup=self.popup,
    #         config=self.config,
    #         total_duration=self.total_duration)

    #     self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)