class SubtitleRenderer:
    def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int):
        self.canvas = canvas
        self.font = font
        self.color = color
        self.line_height = line_height

    def _render_subtitle(self, text: str) -> None:
        self.subtitle_canvas.delete("all")
        if not text: return
        lines = text.splitlines() or [""]
        if len(lines) == 1: lines.insert(0, "")
        total_height = self.line_height * len(lines)
        self.subtitle_canvas.config(width=self.max_width, height=total_height)
        y = 0
        for line in lines:
            self.draw_outlined_text(
                self.subtitle_canvas,
                self.max_width // 2,
                y + (self.line_height // 2),
                line,
                self.subtitle_font,
                fill=self.subtitle_color,
                outline="black",
                thickness=3,
            )
            y += self.line_height

        current_x = self.sub_window.winfo_x()
        new_y = self.sub_window_bottom_anchor - total_height
        self.sub_window.geometry(f"{self.max_width}x{total_height}+{current_x}+{new_y}")
    
    def hide_subtitles_temporarily(self):
        if not self.playing:
            self.subtitle_timeout_job = None
            return
        if not self.user_hidden:
            self.subtitle_canvas.delete("all")
            self.user_hidden = True
        self.subtitle_timeout_job = None


    def draw_outlined_text(self, canvas: tk.Canvas, x: int, y: int, text: str,
                           font: tkFont.Font, fill: str, outline: str, thickness: int,
                           anchor: str = "center") -> None:
        for dx in range(-thickness, thickness + 1):
            for dy in range(-thickness, thickness + 1):
                if dx or dy:
                    canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor) # type: ignore

