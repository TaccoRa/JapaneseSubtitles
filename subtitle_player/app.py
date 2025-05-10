from subtitle.manager import SubtitleManager
from subtitle.renderer import SubtitleRenderer
from controller import SubtitleController
from ui.control_ui import ControlUI
from ui.subtitle_overlay import SubtitleOverlayUI
from subtitle_player.mouse import MouseManager
from popup import CopyPopup
from config_manager import ConfigManager

import tkinter as tk

class SubtitlePlayerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.subtitle_manager = SubtitleManager()
        self.renderer = SubtitleRenderer()
        self.controller = SubtitleController()
        self.control_ui = ControlUI()
        self.overlay = SubtitleOverlayUI()
        self.mouse = MouseManager()
        self.popup = CopyPopup()
        self.config = ConfigManager()
        
    def run(self):
        self.root.mainloop()
