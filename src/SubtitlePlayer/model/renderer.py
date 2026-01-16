#renderer.py
import tkinter as tk
from tkinter import font as tkFont
from view.subtitle_overlay import SubtitleOverlayUI
from model.config_manager import ConfigManager

class SubtitleRenderer:
    def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
        self.config = config
        self.canvas = canvas

    def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
        self.font = tkFont.Font(family=self.config.get("SUBTITLE_FONT"),size=self.config.get("SUBTITLE_FONT_SIZE"),weight="bold")
        self.ruby_font = tkFont.Font(family=self.font.actual("family"), size=int(self.font.actual("size") * 0.6), weight="bold")

        self.color=self.config.get("SUBTITLE_COLOR")
        self.line_height = self.font.metrics("linespace")
        self.ruby_height = int(self.line_height * 0.6)
   
        y_ruby_top = self.ruby_height // 2
        y_base1   = self.ruby_height + self.line_height//2
        y_ruby_bot = self.ruby_height + self.line_height*2 + self.ruby_height//2
        y_base2   = self.ruby_height + self.line_height + self.line_height//2
        self.canvas.delete("all") 
        self._render_line(top_segments, y_ruby_top, y_base1, overlay.max_w)
        self._render_line(bottom_segments, y_ruby_bot, y_base2, overlay.max_w)

    def _render_line(self, segments, ruby_y, base_y, max_width):
        if not segments:
            return
        total_w = sum(self.font.measure(b) for b, _ in segments)
        cur_x = (max_width - total_w) / 2

        for base, ruby in segments:
            base_w = self.font.measure(base)
            cx = cur_x + base_w / 2
            if ruby:
                self.draw_outlined_text(
                    self.canvas, cx, ruby_y,
                    ruby, self.ruby_font, fill=self.color,
                    outline="black", thickness=2
                )
            self.draw_outlined_text(
                self.canvas, cx, base_y,
                base, self.font, fill=self.color,
                outline="black", thickness=3
            )
            cur_x += base_w

    @staticmethod
    def draw_outlined_text(canvas: tk.Canvas, x: int, y: int, text: str,
                           font: tkFont.Font, fill: str, outline: str, thickness: int,
                           anchor: str = "center") -> None:
        for dx in range(-thickness, thickness + 1):
            for dy in range(-thickness, thickness + 1):
                if dx or dy:
                    canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)

    def update_canvas(self, canvas: tk.Canvas):
        """Switch the renderer to a different canvas (after overlay update)."""
        self.canvas = canvas
