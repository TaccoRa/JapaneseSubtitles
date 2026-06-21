from SubtitlePlayer.controller.controller import SubtitleController
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
