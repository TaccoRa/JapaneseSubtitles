import tkinter as tk
from tkinter import font as tkFont

class SubtitleRenderer:
    def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
        self.canvas = canvas
        self.font = font
        self.color = color
        self.line_height = line_height

    def render(self, text: str, max_width: int, bottom_anchor: int, sub_window: tk.Toplevel) -> None:
        self.canvas.delete("all")
        if not text: return

        lines = text.splitlines() or [""]
        if len(lines) == 1: lines.insert(0, "")

        total_height = self.line_height * len(lines)
        self.canvas.config(width=max_width, height=total_height)

        y = 0
        for line in lines:
            self.draw_outlined_text(
                canvas=self.canvas,
                x=max_width // 2,
                y=y + (self.line_height // 2),
                text=line,
                font=self.font,
                fill=self.color,
                outline="black",
                thickness=3,
                anchor="center"
            )
            y += self.line_height

        current_x = sub_window.winfo_x()
        new_y = bottom_anchor - total_height
        sub_window.geometry(f"{max_width}x{total_height}+{current_x}+{new_y}")
    
    @staticmethod
    def draw_outlined_text(canvas: tk.Canvas, x: int, y: int, text: str,
                           font: tkFont.Font, fill: str, outline: str, thickness: int,
                           anchor: str = "center") -> None:
        for dx in range(-thickness, thickness + 1):
            for dy in range(-thickness, thickness + 1):
                if dx or dy:
                    canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)



