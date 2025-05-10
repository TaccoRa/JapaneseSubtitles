import pyautogui
from config_manager import ConfigManager
from pynput.mouse import Button, Listener


class MouseManager:

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.control_window_x = self.config.get('CONTROL_WINDOW_X')
        self.control_window_y = self.config.get('CONTROL_WINDOW_Y')
    def on_global_click(self, x, y, button, pressed) -> None:
        if button == Button.x2 and pressed:
            self.on_subtitle_click(None)        

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
            is_phone_mode = self.use_phone_mode.get() if hasattr(self, 'use_phone_mode') else False
            if not is_phone_mode:
                if self.hide_controls_job is None:
                    self.hide_controls_job = self.root.after(self.hide_delay, self.lower_controls)
    def simulate_video_click(self):
        original_pos = pyautogui.position()
        target_x = self.control_window_x + 50  #110
        target_y = self.control_window_y - 50 #1000
        pyautogui.click(target_x, target_y)
        self.control_window.attributes("-topmost", True)
        pyautogui.moveTo(original_pos.x, original_pos.y)
