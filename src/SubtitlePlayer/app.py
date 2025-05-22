import tkinter as tk
from tkinter import font as tkFont

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.settings_ui import SettingsUI
from view.subtitle_overlay import SubtitleOverlayUI
from controller.controller import SubtitleController
from view.popup import CopyPopup

class SubtitlePlayerApp:
    def __init__(self):

        # Initialize config and subtitle manager
        self.config = ConfigManager("config.json")
        self.manager = SubtitleManager(self.config)

        # App window
        self.root = tk.Tk()
        self.root.title("Subtitle Player Settings")
        self.root.geometry("320x123")
        self.root.minsize(320, 123)
        self.root.withdraw()               
        self._restore_window_position()    # now calls geometry("+x+y")
        self.root.deiconify()

        # UI (View)       
        self.popup = CopyPopup(root=self.root, config=self.config)

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=self.config,
            cleaned_subs=self.manager.cleaned_subtitles)
        
        self.settings_ui = SettingsUI(
            root=self.root,
            config=self.config,
            total_duration=self.manager.get_total_duration(),
            initial_episode=self.manager.current_episode)

        # Model
        self.renderer = SubtitleRenderer(
            canvas=self.sub_overlay_ui.subtitle_canvas,
            font=self.sub_overlay_ui.font,
            color=self.config.get("SUBTITLE_COLOR"),
            line_height=self.sub_overlay_ui.line_height
            )

        # Controller
        self.controller = SubtitleController(
            manager=self.manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=self.config)

        self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

    def _restore_window_position(self):
        x = self.config.get("LAST_SETTINGS_WINDOW_X", 100)
        y = self.config.get("LAST_SETTINGS_WINDOW_Y", 100)
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, min(x, sw - w))
        y = max(0, min(y, sh - h))
        self.root.geometry(f"+{x}+{y}")
        
    def run(self):
        self.root.mainloop()
