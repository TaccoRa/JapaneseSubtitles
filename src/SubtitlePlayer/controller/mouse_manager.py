import pyautogui

from model.config_manager import ConfigManager
from view.control_ui import ControlUI
from view.subtitle_overlay import SubtitleOverlayUI


class MouseManager:

    def __init__(self, overlay_ui: SubtitleOverlayUI, control_ui: ControlUI, config: ConfigManager):
        self.overlay = overlay_ui
        self.control = control_ui
        self.config = config

        self.control_window_x = self.config.get('CONTROL_WINDOW_X')
        self.control_window_y = self.config.get('CONTROL_WINDOW_Y')

        self.mouse_over_controls = False
        self.mouse_over_subtitles = False
        
        self.root = control_ui.root
        self.control_window = control_ui.control_window
        self.hide_controls_job = None
        self.hide_delay = self.config.get("PHONEMODE_CONTROL_HIDE_DELAY_MS")
        self.use_phone_mode = control_ui.phone_mode

    def set_mouse_over(self, area: str, flag: bool) -> None:
        if area == "controls":
            self.mouse_over_controls = flag
        elif area == "subtitles":
            self.mouse_over_subtitles = flag

        if self.mouse_over_controls or self.mouse_over_subtitles:
            if self.hide_controls_job is not None:
                self.root.after_cancel(self.hide_controls_job)
                self.hide_controls_job = None
            self.control_window.lift()
        else:
            if self.use_phone_mode.get():
                if self.hide_controls_job is None:
                    self.hide_controls_job = self.root.after(
                        self.hide_delay,
                        self.lower_controls)
                    
    def lower_controls(self) -> None:
        self.control_window.lower()
        self.hide_controls_job = None

    def simulate_video_click(self):
        original_pos = pyautogui.position()
        target_x = self.control_window_x + 50  #110
        target_y = self.control_window_y - 50 #1000
        pyautogui.click(target_x, target_y)
        self.control_window.attributes("-topmost", True)
        pyautogui.moveTo(original_pos)
