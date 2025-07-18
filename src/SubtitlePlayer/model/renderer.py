import tkinter as tk
from tkinter import font as tkFont
from view.subtitle_overlay import SubtitleOverlayUI

class SubtitleRenderer:
    def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
        self.canvas = canvas
        self.font = font
        self.color = color
        self.line_height = line_height
        self.max_ruby_h = int(self.line_height * 0.6)


    def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
        pad_x = overlay.pad_x
        self.canvas.delete("all")    
        y_ruby_top = self.max_ruby_h // 2
        y_base1   = self.max_ruby_h + self.line_height//2
        y_base2   = self.max_ruby_h + self.line_height + self.line_height//2
        y_ruby_bot = self.max_ruby_h + self.line_height*2 + self.max_ruby_h//2


        top_w = sum(self.font.measure(b) for b, _ in top_segments)
        bot_w = sum(self.font.measure(b) for b, _ in bottom_segments)
        x_top = (overlay.max_w - top_w) / 2
        x_bot = (overlay.max_w - bot_w) / 2

        # draw top line, if present
        cur_x = x_top
        if top_segments:
            for base, ruby in top_segments:
                w = self.font.measure(base)
                cx = pad_x + cur_x + w/2
                if ruby:
                    rf = tkFont.Font(
                        family=self.font.actual('family'),
                        size=int(self.font.actual('size')*0.6),
                        weight="bold"
                    )
                    self.draw_outlined_text(
                        self.canvas, cx, y_ruby_top,
                        ruby, rf, fill=self.color,
                        outline="black", thickness=2
                    )
                self.draw_outlined_text(
                    self.canvas, cx, y_base1,
                    base, self.font,
                    fill=self.color, outline="black", thickness=3
                )
                cur_x += w

        # draw bottom line
        cur_x = x_bot
        for base, ruby in bottom_segments:
            w = self.font.measure(base)
            cx = pad_x + cur_x + w/2
            if ruby:
                rf = tkFont.Font(
                    family=self.font.actual('family'),
                    size=int(self.font.actual('size')*0.6),
                    weight="bold"
                )
                self.draw_outlined_text(
                    self.canvas, cx, y_ruby_bot,
                    ruby, rf, fill=self.color,
                    outline="black", thickness=2
                )
            self.draw_outlined_text(
                self.canvas, cx, y_base2,
                base, self.font,
                fill=self.color, outline="black", thickness=3
            )
            cur_x += w

    @staticmethod
    def draw_outlined_text(canvas: tk.Canvas, x: int, y: int, text: str,
                           font: tkFont.Font, fill: str, outline: str, thickness: int,
                           anchor: str = "center") -> None:
        for dx in range(-thickness, thickness + 1):
            for dy in range(-thickness, thickness + 1):
                if dx or dy:
                    canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)
