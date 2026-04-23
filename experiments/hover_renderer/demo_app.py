"""Hover subtitle rendering experiment (isolated from main SubtitlePlayer runtime)."""

from __future__ import annotations

import argparse
import tkinter as tk
from typing import Dict, List, Optional, Sequence, Tuple

from experiments.hover_renderer.render_backends import (
    BaseBackend,
    CanvasImageBackend,
    CanvasTextBackend,
    HoverBox,
    Line,
    Style,
)


_SAMPLES: List[Sequence[Line]] = [
    [
        [("久々", "ひさびさ"), ("の", None), ("ベッド", None), ("だ", None), ("~", None)],
    ],
    [
        [("異世界", "いせかい"), ("攻略", "こうりゃく"), ("って", None), ("難しい", "むずかしい"), ("?", None)],
        [("でも", None), ("面白い", "おもしろい"), ("!", None)],
    ],
    [
        [("Terraformars", None), ("映画", "えいが"), ("版", "ばん"), ("も", None), ("見た", "みた")],
    ],
]

_MOCK_DICT: Dict[str, str] = {
    "久々": "after a long time",
    "異世界": "another world",
    "攻略": "strategy / conquest",
    "難しい": "difficult",
    "面白い": "interesting",
    "映画": "movie",
    "見た": "saw (past tense of to see)",
}


class HoverDemo:
    def __init__(self, backend_name: str = "image") -> None:
        self.root = tk.Tk()
        self.root.title("Hover Renderer Experiment")
        self.root.geometry("1280x360")
        self._transparent_key = "#01fe03"
        self.root.configure(bg="#161a1d")
        self.canvas = tk.Canvas(
            self.root,
            width=1240,
            height=260,
            bg=self._transparent_key,
            bd=0,
            highlightthickness=0,
        )
        self.canvas.pack(fill="x", padx=20, pady=(14, 8))
        try:
            self.root.attributes("-transparentcolor", self._transparent_key)
        except Exception:
            pass

        self.info_var = tk.StringVar(value="")
        self.backend_var = tk.StringVar(value=backend_name)
        self.sample_idx = 0
        self.style = Style(font_family="Meiryo", font_size=44, text_color="white", outline_color="black", outline_radius=3)
        self.hover_boxes: List[HoverBox] = []
        self.hover_token: Optional[str] = None
        self.shift_down = False
        self.last_render_cache_hit = False

        self.text_backend = CanvasTextBackend()
        self.image_backend = CanvasImageBackend()
        self.backend: BaseBackend = self.image_backend if backend_name == "image" else self.text_backend

        controls = tk.Frame(self.root, bg="#161a1d")
        controls.pack(fill="x", padx=20, pady=(0, 8))
        tk.Button(controls, text="Prev Sample", command=self.prev_sample).pack(side="left")
        tk.Button(controls, text="Next Sample", command=self.next_sample).pack(side="left", padx=(8, 0))
        tk.OptionMenu(controls, self.backend_var, "image", "text", command=self.set_backend).pack(side="left", padx=(14, 0))
        tk.Label(controls, textvariable=self.info_var, fg="#f1f3f5", bg="#161a1d", anchor="w").pack(side="left", padx=(16, 0))

        self.lookup_var = tk.StringVar(value="Hover a token to inspect lookup output.")
        tk.Label(
            self.root,
            textvariable=self.lookup_var,
            fg="#e9ecef",
            bg="#161a1d",
            anchor="w",
            justify="left",
            wraplength=1220,
        ).pack(fill="x", padx=20, pady=(0, 14))

        self.canvas.bind("<Motion>", self.on_hover)
        self.canvas.bind("<Leave>", self.on_leave)
        self.root.bind_all("<KeyPress-Shift_L>", self.on_shift_down, add="+")
        self.root.bind_all("<KeyPress-Shift_R>", self.on_shift_down, add="+")
        self.root.bind_all("<KeyRelease-Shift_L>", self.on_shift_up, add="+")
        self.root.bind_all("<KeyRelease-Shift_R>", self.on_shift_up, add="+")
        self.root.bind("<Left>", lambda _e: self.prev_sample())
        self.root.bind("<Right>", lambda _e: self.next_sample())
        self.root.after(100, self.render)

    def set_backend(self, selected: str) -> None:
        self.backend = self.image_backend if str(selected) == "image" else self.text_backend
        self.render()

    def current_lines(self) -> Sequence[Line]:
        return _SAMPLES[self.sample_idx]

    def prev_sample(self) -> None:
        self.sample_idx = (self.sample_idx - 1) % len(_SAMPLES)
        self.render()

    def next_sample(self) -> None:
        self.sample_idx = (self.sample_idx + 1) % len(_SAMPLES)
        self.render()

    def render(self) -> None:
        result = self.backend.render(self.canvas, self.current_lines(), self.style)
        self.hover_boxes = result.hover_boxes
        self.last_render_cache_hit = bool(result.cache_hit)
        cache_text = "cache_hit" if result.cache_hit else "cache_miss"
        if isinstance(self.backend, CanvasImageBackend):
            cache_text = (
                f"{cache_text} | image_hits={self.backend.cache_hits} image_misses={self.backend.cache_misses}"
            )
        self.info_var.set(f"backend={self.backend_var.get()} | sample={self.sample_idx + 1}/{len(_SAMPLES)} | {cache_text}")
        self.lookup_var.set("Hold Shift and hover a token to inspect lookup output.")
        self.hover_token = None
        self.canvas.delete("hover_box")

    def on_shift_down(self, _event=None) -> None:
        self.shift_down = True

    def on_shift_up(self, _event=None) -> None:
        self.shift_down = False
        self.hover_token = None
        self.canvas.delete("hover_box")
        self.lookup_var.set("Hold Shift and hover a token to inspect lookup output.")

    def on_hover(self, event) -> None:
        if not self.shift_down:
            if self.hover_token is not None:
                self.hover_token = None
                self.canvas.delete("hover_box")
                self.lookup_var.set("Hold Shift and hover a token to inspect lookup output.")
            return
        token = self._token_at(x=float(event.x), y=float(event.y))
        if token == self.hover_token:
            return
        self.hover_token = token
        self.canvas.delete("hover_box")
        if not token:
            self.lookup_var.set("Hold Shift and hover a token to inspect lookup output.")
            return
        for box in self.hover_boxes:
            if box.token == token and (box.x0 <= event.x <= box.x1) and (box.y0 <= event.y <= box.y1):
                self.canvas.create_rectangle(
                    box.x0,
                    box.y0,
                    box.x1,
                    box.y1,
                    outline="#80ed99",
                    width=2,
                    tags=("hover_box",),
                )
                break
        definition = _MOCK_DICT.get(token, "(no mock entry)")
        self.lookup_var.set(f"{token}: {definition}")

    def on_leave(self, _event) -> None:
        self.hover_token = None
        self.canvas.delete("hover_box")
        self.lookup_var.set("Hold Shift and hover a token to inspect lookup output.")

    def _token_at(self, x: float, y: float) -> Optional[str]:
        for box in self.hover_boxes:
            if box.x0 <= x <= box.x1 and box.y0 <= y <= box.y1:
                return box.token
        return None

    def run(self) -> None:
        self.root.mainloop()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Hover subtitle rendering experiment.")
    p.add_argument("--backend", choices=("image", "text"), default="image")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    app = HoverDemo(backend_name=str(args.backend))
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
