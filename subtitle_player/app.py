import os
import tkinter as tk
from tkinter import font as tkFont

from config_manager import ConfigManager
from subtitle.manager import SubtitleManager
from subtitle.renderer import SubtitleRenderer
from ui.control_ui import ControlUI
from ui.subtitle_overlay import SubtitleOverlayUI
from controller import SubtitleController
from mouse import MouseManager
from popup import CopyPopup

class SubtitlePlayerApp:
    def __init__(self):
        self.config = ConfigManager("config.json")

        self.root = tk.Tk()
        self.root.title("Subtitle Player Settings")

        initial_srt = self.config.get("DEBUG_SRT_FILE")
        self.manager = SubtitleManager(initial_srt)

        self.overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=self.config,
            font=tkFont.Font(
                family=self.config.get("SUBTITLE_FONT"),
                size=self.config.get("SUBTITLE_FONT_SIZE"),
                weight="bold"
            ),
            line_height=None  # SubtitleOverlayUI can compute this after setting its font
        )

        self.renderer = SubtitleRenderer(
            canvas=self.overlay_ui.subtitle_canvas,
            font=self.overlay_ui.font,
            color=self.config.get("SUBTITLE_COLOR"),
            line_height=self.overlay_ui.line_height
        )

        self.control_ui = ControlUI(root=self.root, config=self.config)
        self.popup = CopyPopup(root=self.root, config=self.config)
        
        self.controller = SubtitleController(
            manager=self.manager,
            renderer=self.renderer,
            control_ui=self.control_ui,
            overlay_ui=self.overlay_ui,
            popup=self.popup,
            config=self.config
        )

        self.mouse = MouseManager(
            overlay_ui=self.overlay_ui,
            control_ui=self.control_ui,
            config=self.config
        )

        self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

    def run(self):
        self.root.mainloop()
