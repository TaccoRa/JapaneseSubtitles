import tkinter as tk
from tkinter import font as tkFont

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.control_ui import ControlUI
from view.subtitle_overlay import SubtitleOverlayUI
from controller.controller import SubtitleController
from controller.mouse_manager import MouseManager
from view.popup import CopyPopup

class SubtitlePlayerApp:
    def __init__(self):

        # Initialize config and subtitle manager
        config = ConfigManager("config.json")
        self.manager = SubtitleManager(config)

        # App window
        self.root = tk.Tk()
        self.root.title("Subtitle Player Settings")
        self.root.geometry("340x128")
        self.root.minsize(340, 128)

        # UI
        self.control_ui = ControlUI(
            root=self.root,
            config=config,
            total_duration=self.manager.get_total_duration(),
            initial_episode=self.manager.current_episode)
        
        self.overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=config,
            cleaned_subs=self.manager.cleaned_subtitles,
            control_ui=self.control_ui)

        self.popup = CopyPopup(root=self.root, config=config)

        self.renderer = SubtitleRenderer(
            canvas=self.overlay_ui.subtitle_canvas,
            font=self.overlay_ui.font,
            color=config.get("SUBTITLE_COLOR"),
            line_height=self.overlay_ui.line_height)

        # Controller
        self.controller = SubtitleController(
            manager=self.manager,
            renderer=self.renderer,
            control_ui=self.control_ui,
            overlay_ui=self.overlay_ui,
            popup=self.popup,
            config=config)

        self.mouse = MouseManager(
            overlay_ui=self.overlay_ui,
            control_ui=self.control_ui,
            config=config)

        self.root.after(config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

    def run(self):
        self.root.mainloop()
