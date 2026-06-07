"""
Subtitle window demo runner.

Renders a few sample lines (with ruby) using the real overlay + renderer.
No production code is modified.
"""

import os
import sys
import tkinter as tk

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "SubtitlePlayer"))
sys.path.insert(0, ROOT)

from model.config_manager import ConfigManager  # noqa: E402
from model.renderer import SubtitleRenderer  # noqa: E402
from view.subtitle_overlay import SubtitleOverlayUI  # noqa: E402


def _segments(pairs):
    return [(base, ruby) for base, ruby in pairs]


def main() -> None:
    root = tk.Tk()
    root.withdraw()

    config = ConfigManager()
    overlay = SubtitleOverlayUI(root, config, overlay_geometry=(900, 220))
    renderer = SubtitleRenderer(overlay.subtitle_canvas, config)

    samples = [
        (
            _segments([("そんな ", None), ("残", "のこ"), ("り", None), ("物", "もの"), ("のスキル", None)]),
            _segments([("僕", "ぼく"), ("は ", None), ("認", "みと"), ("めないぞ！", None)]),
        ),
        (
            _segments([("当", "あ"), ("たり", None), ("前", "まえ")]),
            _segments([("へえ~ ", None), ("意外", "いがい"), ("な ", None), ("観察力", "かんさつりょく")]),
        ),
        (
            _segments([("攻撃", "こうげき"), ("パターンが ", None), ("多", "おお"), ("すぎて", None)]),
            _segments([("動", "うご"), ("きが ", None), ("読", "よ"), ("みきれない", None)]),
        ),
        (
            _segments([("その ", None), ("汚", "きたな"), ("い ", None), ("木", "き"), ("の ", None), ("棒", "ぼう")]),
            [],
        ),
    ]

    idx = {"value": 0}

    def render_current():
        top, bottom = samples[idx["value"]]
        renderer.render_subtitle(top, bottom, overlay)

    def next_sample(_event=None):
        idx["value"] = (idx["value"] + 1) % len(samples)
        render_current()

    def prev_sample(_event=None):
        idx["value"] = (idx["value"] - 1) % len(samples)
        render_current()

    def quit_app(_event=None):
        try:
            overlay.save_state()
        except Exception:
            pass
        root.destroy()

    overlay.sub_window.bind("<Right>", next_sample)
    overlay.sub_window.bind("<Left>", prev_sample)
    overlay.sub_window.bind("<Escape>", quit_app)
    root.bind("<Escape>", quit_app)
    overlay.sub_window.focus_set()

    render_current()
    root.mainloop()


if __name__ == "__main__":
    main()
