from PyQt5 import QtCore, QtWidgets


from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.settings_ui import SettingsUI
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
        self.settings_window = QtWidgets.QWidget()
        self.settings_window.setWindowTitle("Subtitle Player Settings")
        self.settings_window.resize(320, 123)
        self.settings_window.setMinimumSize(320, 123)

        # UI (View)       
        self.popup = CopyPopup(root=self.root, config=config)

        self.sub_overlay_ui = SubtitleOverlayUI(
            root=self.settings_window,
            config=config,
            cleaned_subs=self.manager.cleaned_subtitles
        )
        self.settings_ui = SettingsUI(
            root=self.settings_window,
            config=config,
            total_duration=self.manager.get_total_duration(),
            initial_episode=self.manager.current_episode
        )

        # Model
        self.renderer = SubtitleRenderer(
            canvas=self.sub_overlay_ui.subtitle_canvas,
            font_path=config.get("SUBTITLE_FONT"),
            font_size=config.get("SUBTITLE_FONT_SIZE"),
            color=config.get("SUBTITLE_COLOR"),
            line_height=self.sub_overlay_ui.line_height,
            glow_radius=config.get("GLOW_RADIUS"),
            glow_alpha=config.get("GLOW_ALPHA")
        )


        # Controller
        self.controller = SubtitleController(
            manager=self.manager,
            renderer=self.renderer,
            settings_ui=self.settings_ui,
            overlay_ui=self.sub_overlay_ui,
            popup=self.popup,
            config=config)

        self.mouse = MouseManager(
            overlay_ui=self.sub_overlay_ui,
            settings_ui=self.settings_ui,
            config=config)

        self.timer = QtCore.QTimer(self.settings_window)
        self.timer.setInterval(config.get("UPDATE_INTERVAL_MS"))
        self.timer.timeout.connect(self.controller.update_loop)

    def run(self):
        self.settings_ui.show()
        self.sub_overlay_ui.show()
        self.timer.start()
