from SubtitlePlayer.model.config_manager import ConfigManager
from SubtitlePlayer.controller.controller import SubtitleController
from SubtitlePlayer.model.subtitle_manager import SubtitleManager
from SubtitlePlayer.model.renderer import SubtitleRenderer
from SubtitlePlayer.view.settings_ui import SettingsUI
from SubtitlePlayer.view.subtitle_overlay import SubtitleOverlayUI
from SubtitlePlayer.view.popup import CopyPopup
import tkinter as tk


def make_dummy_controller(tmp_config):
    # minimal objects to satisfy controller signature; most methods are not used
    root = tk.Tk()
    settings = SettingsUI(root, tmp_config, total_duration=0.0)
    overlay = SubtitleOverlayUI(root, tmp_config, cleaned_subs=[], overlay_geometry=(100,100))
    manager = SubtitleManager(tmp_config)
    renderer = SubtitleRenderer(config=tmp_config, canvas=overlay.subtitle_canvas)
    popup = CopyPopup(root=root, config=tmp_config)
    ctrl = SubtitleController(
        manager=manager,
        renderer=renderer,
        settings_ui=settings,
        overlay_ui=overlay,
        popup=popup,
        config=tmp_config,
        total_duration=0.0,
    )
    root.destroy()
    return ctrl


def test_video_click_flags(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        '{"VIDEO_CLICK": true, "VIDEO_CLICK_PLAY": false, "VIDEO_CLICK_WINDOW": true}'
    )
    cfg = ConfigManager(str(cfg_path))
    ctrl = make_dummy_controller(cfg)
    assert ctrl.video_click is True
    # explicit overrides
    assert ctrl.video_click_play is False
    assert ctrl.video_click_window is True
