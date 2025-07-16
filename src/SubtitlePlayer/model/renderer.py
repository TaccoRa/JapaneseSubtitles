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


    def render_subtitle(self, top_segments, bottom_segments, content_w: int, overlay: SubtitleOverlayUI) -> None:
        pad_x = overlay.pad_x

        # for calculating dynamix window height
        # h = 0
        # if top_segments:
        #     if any(r for _, r in top_segments): h += self.max_ruby_h
        #     h += self.line_height
        # if bottom_segments:
        #     h += self.line_height
        #     if any(r for _, r in bottom_segments): h += self.max_ruby_h
        # content_h = h
        self.canvas.config(width=overlay.max_w, height=overlay.max_h)
        self.canvas.delete("all")    
        
        y = 0
        if top_segments:
            if any(r for _,r in top_segments):
                y_ruby_top = y + self.max_ruby_h // 2
                y += self.max_ruby_h
            y_base1 = y + self.line_height//2
            y += self.line_height
        else:
            y_base1 = None

        if bottom_segments:
            y_base2 = y + self.line_height//2
            if any(r for _,r in bottom_segments):
                y_ruby_bot = y + self.line_height + self.max_ruby_h//2
            y += self.line_height
            if any(r for _,r in bottom_segments):
                y += self.max_ruby_h
        else:
            y_base2 = None

        top_w = sum(self.font.measure(b) for b, _ in top_segments)
        bot_w = sum(self.font.measure(b) for b, _ in bottom_segments)
        # x_top = (content_w - top_w) / 2
        # x_bot = (content_w - bot_w) / 2
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

        cx = overlay.center_x
        cy = overlay.center_y

        win_w = overlay.max_w
        win_h = overlay.max_h

        new_x = int(cx - win_w / 2)
        new_y = int(cy - win_h / 2)

        # new_x = overlay.sub_window.winfo_x()
        # canvas above line2 is (max_ruby_h +  self.line_height*2)
        # new_y = overlay.bottom - (self.max_ruby_h*2 + self.line_height*2)

        ## resizible window size
        # win_w = content_w + 2 * pad_x
        # win_h = content_h
        
        # new_x = int(cx - win_w / 2)
        # new_y = int(cy - win_h / 2)
    
        # has_top      = bool(top_segments)
        # has_bot      = bool(bottom_segments)
        # has_top_ruby = has_top and any(r for _,r in top_segments)
        # has_bot_ruby = has_bot and any(r for _,r in bottom_segments)
        # num_lines    = int(has_top) + int(has_bot)
        # shift = 0.0

        # if num_lines == 1:# One line only: shift down by half a line
            
        #     shift += self.line_height / 2
        #     # If there’s also a ruby, shift additionally by half a ruby
        #     if has_bot_ruby:
        #         shift += self.max_ruby_h / 2

        # elif num_lines == 2:# Two lines: only shift if exactly one ruby present
            
        #     if has_top_ruby and not has_bot_ruby:
        #         shift -= self.max_ruby_h / 2
        #     elif has_bot_ruby and not has_top_ruby:
        #         shift += self.max_ruby_h / 2

        # new_y = int(new_y + shift)

        sw = self.canvas.winfo_screenwidth()
        sh = self.canvas.winfo_screenheight()
        new_x = max(0, min(new_x, sw - win_w))
        new_y = max(0, min(new_y, sh - win_h))

        overlay.sub_window.geometry(f"{win_w}x{win_h}+{new_x}+{new_y}")

    @staticmethod
    def draw_outlined_text(canvas: tk.Canvas, x: int, y: int, text: str,
                           font: tkFont.Font, fill: str, outline: str, thickness: int,
                           anchor: str = "center") -> None:
        for dx in range(-thickness, thickness + 1):
            for dy in range(-thickness, thickness + 1):
                if dx or dy:
                    canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)
