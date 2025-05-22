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
        config = ConfigManager("config.json")
        self.manager = SubtitleManager(config)

        # App window
        self.root = tk.Tk()
        self.root.title("Subtitle Player Settings")
        self.root.geometry("320x123")
        self.root.minsize(320, 123)

        # UI (View)       
        self.popup = CopyPopup(root=self.root, config=config)

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=config,
            cleaned_subs=self.manager.cleaned_subtitles)
        
        self.settings_ui = SettingsUI(
            root=self.root,
            config=config,
            total_duration=self.manager.get_total_duration(),
            initial_episode=self.manager.current_episode)

        # Model
        self.renderer = SubtitleRenderer(
            canvas=self.sub_overlay_ui.subtitle_canvas,
            font=self.sub_overlay_ui.font,
            # font_size=config.get("SUBTITLE_FONT_SIZE"),
            color=config.get("SUBTITLE_COLOR"),
            line_height=self.sub_overlay_ui.line_height#,
            # glow_radius=config.get("GLOW_RADIUS"),
            # glow_alpha=config.get("GLOW_ALPHA")
            )


        # Controller
        self.controller = SubtitleController(
            manager=self.manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=config)

        self.root.after(config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

    def run(self):
        self.root.mainloop()
