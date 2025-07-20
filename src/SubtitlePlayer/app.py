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
    DEBOUNCE_MS = 100
    def __init__(self):

        # Initialize config and subtitle manager
        self.config = ConfigManager("config.json")
        self.sub_manager = SubtitleManager(self.config)
        self.total_duration = self.sub_manager.get_total_duration()
        # App window
        self.root = tk.Tk()
        self.root.title("Subtitle Player Settings") 
        self.root.geometry("320x123")
        self.root.minsize(320, 123)
        self._save_after_id = None
        self._restore_window_position()
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.deiconify()

        # UI (View)
        self.popup = CopyPopup(root=self.root, config=self.config)

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.root, config=self.config,
            cleaned_subs=self.sub_manager.cleaned_subtitles)
        
        self.settings_ui = SettingsUI(
            root=self.root, config=self.config,
            total_duration=self.total_duration,
            initial_episode=self.sub_manager.current_episode)

        # Model
        self.renderer = SubtitleRenderer(
            canvas=self.sub_overlay_ui.subtitle_canvas,
            font=self.sub_overlay_ui.font,
            color=self.config.get("SUBTITLE_COLOR"),
            line_height=self.sub_overlay_ui.line_height
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

        self.root.after(self.config.get("UPDATE_INTERVAL_MS"), self.controller.update_loop)

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
