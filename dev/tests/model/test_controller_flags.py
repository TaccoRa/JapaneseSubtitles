from SubtitlePlayer.controller.controller import SubtitleController
from SubtitlePlayer.controller.hotkey_controller import HotkeyController
from SubtitlePlayer.model.config_manager import ConfigManager


def test_video_click_flags(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"VIDEO_CLICK": true, "VIDEO_CLICK_PLAY": false, "VIDEO_CLICK_WINDOW": true}'
    )
    cfg = ConfigManager(str(cfg_path))

    ctrl = object.__new__(SubtitleController)
    ctrl.config = cfg
    ctrl._init_runtime_state()

    assert ctrl.video_click is True
    assert ctrl.video_click_play is False
    assert ctrl.video_click_window is True


def test_destroyed_text_widget_is_not_focused_input():
    class DestroyedWidget:
        def winfo_exists(self):
            raise Exception('bad window path name ".!toplevel6"')

        def winfo_class(self):
            raise AssertionError("winfo_class should not be called after winfo_exists fails")

    assert HotkeyController._is_text_input_widget(DestroyedWidget()) is False
