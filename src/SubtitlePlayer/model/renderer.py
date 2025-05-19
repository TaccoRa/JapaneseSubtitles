import tkinter as tk
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageTk, ImageColor

class SubtitleRenderer:
    def __init__(self,canvas: tk.Canvas,font_path: str,font_size: int,color: str,line_height: int, glow_radius:int, glow_alpha:int) -> None:
        self.canvas = canvas
        self.font_path = font_path      # e.g. "meiryo.ttc"
        self.font_size = font_size      # e.g. 50
        self.color = color              # e.g. "white" or "#FFFFFF"
        
        self.line_height = line_height
        self.glow_radius = glow_radius  # px blur radius
        self.glow_alpha  = glow_alpha   # 0–255 max opacity
        self.shadow_color  = (0, 0, 0)  # RGB tuple for shadow

    def render(
        self,text: str,max_width: int,bottom_anchor: int,sub_window: tk.Toplevel) -> None:
        # Clear previous
        self.canvas.delete("all")
        if not text: return

        W = self.canvas.winfo_width()
        H = self.canvas.winfo_height()

        # Prepare lines
        lines = text.splitlines() or [""]
        pil_font = ImageFont.truetype(self.font_path, self.font_size)
        ascent, descent = pil_font.getmetrics()
        lh = ascent + descent

        mask = Image.new("L", (W, H), 0)
        draw = ImageDraw.Draw(mask)

        total_height = lh * len(lines)
        y_start = (H - total_height) // 2
        y = y_start
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=pil_font)
            text_w = bbox[2] - bbox[0]
            x = (W - text_w) // 2
            draw.text((x, y), line, font=pil_font, fill=255)
            y += lh

        # 2) Blur mask for shadow
        glow_mask = mask.filter(ImageFilter.GaussianBlur(self.glow_radius))
        shadow = Image.new("RGBA", (W, H), self.shadow_color + (0,))
        shadow.putalpha(glow_mask.point(lambda v: v * (self.glow_radius / 255.0)))

        # 3) Text layer
        rgb = ImageColor.getrgb(self.color)
        text_img = Image.new("RGBA", (W, H), rgb + (0,))
        text_img.putalpha(mask)

        # 4) Composite on transparent
        frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        frame.alpha_composite(shadow)
        frame.alpha_composite(text_img)

        # 5) Display
        photo = ImageTk.PhotoImage(frame)
        self.canvas.create_image(0, 0, anchor="nw", image=photo)
        self.canvas.image = photo

        # 6) Position window
        x = sub_window.winfo_x()
        sub_window.geometry(f"{W}x{H}+{x}+{bottom_anchor - H}")

    @staticmethod
    def _format_time(seconds: float) -> str:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes:02d}:{secs:02d}"





# 3D shadow effect:
# import tkinter as tk
# from tkinter import font as tkFont

# class SubtitleRenderer:
#     def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
#         self.canvas = canvas
#         self.font = font
#         self.color = color
#         self.line_height = line_height

#     def render(self, text: str, max_width: int, bottom_anchor: int, sub_window: tk.Toplevel) -> None:
#         self.canvas.delete("all")
#         if not text:
#             return

#         # split into at least two lines so there's always a little padding above
#         lines = text.splitlines() or [""]
#         if len(lines) == 1:
#             lines.insert(0, "")

#         total_height = self.line_height * len(lines)
#         self.canvas.config(width=max_width, height=total_height)

#         y = 0
#         for line in lines:
#             self._draw_with_shadow(
#                 x=max_width // 2,
#                 y=y + (self.line_height // 2),
#                 text=line,
#                 font=self.font,
#                 fill=self.color,
#                 shadow_offset=(3, 3),
#                 anchor="center"
#             )
#             y += self.line_height

#         # reposition the subtitle window
#         current_x = sub_window.winfo_x()
#         new_y = bottom_anchor - total_height
#         sub_window.geometry(f"{max_width}x{total_height}+{current_x}+{new_y}")

#     def _draw_with_shadow(self, x: int, y: int, text: str, font: tkFont.Font,
#                           fill: str, shadow_offset: tuple[int, int],
#                           anchor: str = "center") -> None:
#         """
#         Draws text with a single drop‐shadow behind it.
#         """
#         ox, oy = shadow_offset
#         # shadow
#         self.canvas.create_text(
#             x + ox, y + oy,
#             text=text,
#             fill="black",
#             font=font,
#             anchor=anchor
#         )
#         # foreground
#         self.canvas.create_text(
#             x, y,
#             text=text,
#             fill=fill,
#             font=font,
#             anchor=anchor
#         )

#     @staticmethod
#     def _format_time(seconds: float) -> str:
#         minutes, secs = divmod(int(seconds), 60)
#         return f"{minutes:02d}:{secs:02d}"
