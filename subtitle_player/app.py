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
        self.root.geometry("330x130")
        self.root.minsize(340, 130)

        initial_srt = self.config.get("DEBUG_SRT_FILE")
        self.manager = SubtitleManager(initial_srt, self.config)

        subtitle_font = tkFont.Font(
            family=self.config.get("SUBTITLE_FONT"),
            size=self.config.get("SUBTITLE_FONT_SIZE"),
            weight="bold"
        )
        line_height = subtitle_font.metrics("linespace")

        self.overlay_ui = SubtitleOverlayUI(
            root=self.root,
            config=self.config,
            font=subtitle_font,
            line_height=line_height,
            cleaned_subs=self.manager.cleaned_subtitles
        )

        self.renderer = SubtitleRenderer(
            canvas=self.overlay_ui.subtitle_canvas,
            font=self.overlay_ui.font,
            color=self.config.get("SUBTITLE_COLOR"),
            line_height=self.overlay_ui.line_height
        )

        self.control_ui = ControlUI(root=self.root,config=self.config,total_duration=self.manager.get_total_duration())
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
