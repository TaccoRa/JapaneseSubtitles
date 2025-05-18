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
#         if not text: return

#         lines = text.splitlines() or [""]
#         if len(lines) == 1: lines.insert(0, "")

#         total_height = self.line_height * len(lines)
#         self.canvas.config(width=max_width, height=total_height)

#         y = 0
#         for line in lines:
#             self.draw_outlined_text(
#                 canvas=self.canvas,
#                 x=max_width // 2,
#                 y=y + (self.line_height // 2),
#                 text=line,
#                 font=self.font,
#                 fill=self.color,
#                 outline="black",
#                 thickness=3,
#                 anchor="center"
#             )
#             y += self.line_height

#         current_x = sub_window.winfo_x()
#         new_y = bottom_anchor - total_height
#         sub_window.geometry(f"{max_width}x{total_height}+{current_x}+{new_y}")
    
#     @staticmethod
#     def draw_outlined_text(canvas: tk.Canvas, x: int, y: int, text: str,
#                            font: tkFont.Font, fill: str, outline: str, thickness: int,
#                            anchor: str = "center") -> None:
#         for dx in range(-thickness, thickness + 1):
#             for dy in range(-thickness, thickness + 1):
#                 if dx or dy:
#                     canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
#         canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)
#     @staticmethod
#     def _format_time(seconds: float) -> str:
#         minutes, secs = divmod(int(seconds), 60)
#         return f"{minutes:02d}:{secs:02d}"
    

# import tkinter as tk
# from tkinter import font as tkFont

# class SubtitleRenderer:
#     def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
#         self.canvas = canvas
#         self.font = font
#         self.color = color
#         self.line_height = line_height
#         self._text_ids = []

#     def render(self, text: str, max_width: int, bottom_anchor: int, sub_window: tk.Toplevel) -> None:
#         if not text or max_width <= 0:
#             return
        
#         self.canvas.delete("all")

#         lines = (text or "").splitlines()
#         if len(lines) == 1: lines.insert(0, "")

#         total_height = self.line_height * len(lines)
#         self.canvas.config(width=max_width, height=total_height)
#         thickness = 3
#         for i, line in enumerate(lines):
#             y = i * self.line_height + self.line_height // 2
#             # draw outline first
#             offsets = [(-thickness, 0), (thickness, 0), (0, -thickness), (0, thickness),
#                        (-thickness, -thickness), (-thickness, thickness), (thickness, -thickness), (thickness, thickness)]
#             for dx, dy in offsets:
#                 self.canvas.create_text(
#                     max_width // 2 + dx,
#                     y + dy,
#                     text=line,
#                     fill="black",
#                     font=self.font,
#                     anchor="center"
#                 )
#             # draw main text
#             self.canvas.create_text(
#                 max_width // 2,
#                 y,
#                 text=line,
#                 fill=self.color,
#                 font=self.font,
#                 anchor="center"
#             )

#         # Reposition window based on new height
#         x = sub_window.winfo_x()
#         new_y = bottom_anchor - total_height
#         sub_window.geometry(f"{max_width}x{total_height}+{x}+{new_y}")
#         sub_window.update_idletasks()

#     def _layout_window(self,
#                        sub_window: tk.Toplevel,
#                        width: int,
#                        height: int,
#                        bottom_anchor: int) -> None:
#         x = sub_window.winfo_x()
#         y = bottom_anchor - height
#         sub_window.geometry(f"{width}x{height}+{x}+{y}")
#         sub_window.update_idletasks()

#     def _draw_outlined_text(self, x: int, y: int, text: str) -> int:
#         # Draw outline in 8 directions only
#         thickness = 2
#         offsets = [(-thickness, 0),(thickness, 0),
#                    (0, -thickness),(0, thickness),
#                    (-thickness, -thickness),(-thickness, thickness),
#                    (thickness, -thickness),(thickness, thickness)]

#         for dx, dy in offsets:
#             self.canvas.create_text(x + dx, y + dy,
#                                      text=text,
#                                      fill="black",
#                                      font=self.font,
#                                      anchor="center")
#         return self.canvas.create_text(x, y,
#                                        text=text,
#                                        fill=self.color,
#                                        font=self.font,
#                                        anchor="center")
    
#     def _update_outlined_text(self, text_id: int, text: str, x: int, y: int) -> None:
#         self.canvas.itemconfig(text_id, text=text, fill=self.color, font=self.font)
#         self.canvas.coords(text_id, x, y)

# import tkinter as tk
# from tkinter import font as tkFont

# class SubtitleRenderer:
#     def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
#         self.canvas = canvas
#         self.font = font
#         self.color = color
#         self.line_height = line_height
#         self._line_ids = []  # list of lists: [[outline1, ..., main], ...]

#     def render(self, text: str, max_width: int, bottom_anchor: int, sub_window: tk.Toplevel) -> None:
#         if not text or max_width <= 0:
#             return

#         lines = (text or "").splitlines()
#         if len(lines) == 1: lines.insert(0, "")

#         total_height = self.line_height * len(lines)
#         self.canvas.config(width=max_width, height=total_height)

#         ascent = self.font.metrics("ascent")

#         # Clear and redraw every time for accuracy
#         for ids in self._line_ids:
#             for tid in ids:
#                 self.canvas.delete(tid)
#         self._line_ids.clear()

#         for i, line in enumerate(lines):
#             y = i * self.line_height + (self.line_height // 2)
#             ids = self._draw_outlined_text(x=max_width // 2, y=y, text=line)
#             self._line_ids.append(ids)

#         self._layout_window(sub_window, max_width, total_height, bottom_anchor)

#     def _layout_window(self,
#                        sub_window: tk.Toplevel,
#                        width: int,
#                        height: int,
#                        bottom_anchor: int) -> None:
#         x = sub_window.winfo_x()
#         y = bottom_anchor - height
#         sub_window.geometry(f"{width}x{height}+{x}+{y}")
#         sub_window.update_idletasks()

#     def _draw_outlined_text(self, x: int, y: int, text: str) -> list[int]:
#         thickness = 3
#         offsets = [(-thickness, 0),(thickness, 0),
#                    (0, -thickness),(0, thickness),
#                    (-thickness, -thickness),(-thickness, thickness),
#                    (thickness, -thickness),(thickness, thickness)]

#         ids = []
#         for dx, dy in offsets:
#             tid = self.canvas.create_text(x + dx, y + dy,
#                                           text=text,
#                                           fill="white",
#                                           font=self.font,
#                                           anchor="center")
#             ids.append(tid)

#         main_id = self.canvas.create_text(x, y,
#                                           text=text,
#                                           fill=self.color,
#                                           font=self.font,
#                                           anchor="center")
#         ids.append(main_id)
#         return ids


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

# #Okayisch
# import tkinter as tk
# from tkinter import font as tkFont

# class SubtitleRenderer:
#     def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
#         """
#         canvas:       your tk.Canvas
#         font:         a block‐style tkFont.Font (e.g. Arial Black, Impact, etc)
#         color:        e.g. "white"
#         line_height:  vertical spacing between lines
#         """
#         self.canvas = canvas
#         self.font = font
#         self.color = color
#         self.line_height = line_height

#     def render(self, text: str, max_width: int, bottom_anchor: int, sub_window: tk.Toplevel) -> None:
#         self.canvas.delete("all")
#         if not text:
#             return

#         # split and guarantee at least 2 lines so there's a little padding
#         lines = text.splitlines() or [""]
#         if len(lines) == 1:
#             lines.insert(0, "")

#         total_height = self.line_height * len(lines)
#         self.canvas.config(width=max_width, height=total_height)

#         y = 0
#         for line in lines:
#             self._draw_drop_shadow(
#                 x = max_width // 2,
#                 y = y + (self.line_height // 2),
#                 text = line,
#                 font = self.font,
#                 fill = self.color,
#                 shadow_color = "black",
#                 offset = (2, 2),
#                 anchor = "center"
#             )
#             y += self.line_height

#         # reposition the subtitle window
#         current_x = sub_window.winfo_x()
#         new_y = bottom_anchor - total_height
#         sub_window.geometry(f"{max_width}x{total_height}+{current_x}+{new_y}")

#     def _draw_drop_shadow(self,
#                           x: int,
#                           y: int,
#                           text: str,
#                           font: tkFont.Font,
#                           fill: str,
#                           shadow_color: str,
#                           offset: tuple[int, int],
#                           anchor: str = "center"
#                           ) -> None:
#         """
#         Draws a single drop shadow, then the text.
#         offset: (dx,dy) shadow offset in pixels
#         """
#         dx, dy = offset
#         # shadow
#         self.canvas.create_text(
#             x + dx, y + dy,
#             text = text,
#             fill = shadow_color,
#             font = font,
#             anchor = anchor
#         )
#         # main text
#         self.canvas.create_text(
#             x, y,
#             text = text,
#             fill = fill,
#             font = font,
#             anchor = anchor
#         )

#     @staticmethod
#     def _format_time(seconds: float) -> str:
#         minutes, secs = divmod(int(seconds), 60)
#         return f"{minutes:02d}:{secs:02d}"
# model/renderer.py


# # Using PIL to render text with stroke
# import tkinter as tk
# from tkinter import font as tkFont
# from PIL import Image, ImageDraw, ImageFont, ImageTk


# class SubtitleRenderer:
#     def __init__(
#         self,
#         canvas: tk.Canvas,
#         font: tkFont.Font,
#         color: str,
#         line_height: int,
#         pil_font_path: str = "C:\Windows\Fonts\meiryo.ttc",
#     ) -> None:
#         self.canvas = canvas
#         self.color = color
#         self.line_height = line_height

#         # extract integer point‑size from the tkFont.Font
#         tk_size = abs(int(font.cget("size")))
#         self.pil_font = ImageFont.truetype(pil_font_path, tk_size)

#         # keep a reference so Tk doesn't garbage‑collect the image
#         self._photoimage = None

#     def render(
#         self,
#         text: str,
#         max_width: int,
#         bottom_anchor: int,
#         sub_window: tk.Toplevel,
#     ) -> None:
#         # clear previous
#         self.canvas.delete("all")
#         if not text:
#             return

#         lines = text.splitlines() or [""]
#         if len(lines) == 1:
#             lines.insert(0, "")

#         total_height = self.line_height * len(lines)

#         # new transparent image
#         img = Image.new("RGBA", (max_width, total_height), (0, 0, 0, 0))
#         draw = ImageDraw.Draw(img)

#         y = 0
#         for line in lines:
#             # MEASURE using textbbox (supports stroke_width)
#             bbox = draw.textbbox((0, 0), line,
#                                  font=self.pil_font,
#                                  stroke_width=1)
#             w = bbox[2] - bbox[0]
#             x = (max_width - w) // 2

#             # DRAW with a 1px stroke + fill
#             draw.text(
#                 (x, y),
#                 line,
#                 font=self.pil_font,
#                 fill=self.color,
#                 stroke_width=1,
#                 stroke_fill="black",
#             )
#             y += self.line_height

#         # HARD‑THRESHOLD the alpha to eliminate antialias fringes
#         alpha = img.split()[3].point(lambda a: 255 if a > 0 else 0)
#         img.putalpha(alpha)

#         # BLIT to Tk
#         self._photoimage = ImageTk.PhotoImage(img)
#         self.canvas.config(width=max_width, height=total_height)
#         self.canvas.create_image(0, 0, image=self._photoimage, anchor="nw")

#         # reposition the window
#         current_x = sub_window.winfo_x()
#         new_y = bottom_anchor - total_height
#         sub_window.geometry(f"{max_width}x{total_height}+{current_x}+{new_y}")

#     @staticmethod
#     def _format_time(seconds: float) -> str:
#         minutes, secs = divmod(int(seconds), 60)
#         return f"{minutes:02d}:{secs:02d}"

import tkinter as tk
from tkinter import font as tkFont
from PIL import Image, ImageDraw, ImageFont, ImageTk, ImageFilter


class SubtitleRenderer:
    def __init__(
        self,
        canvas: tk.Canvas,
        font: tkFont.Font,
        color: str,
        line_height: int,
        pil_font_path: str = r"C:\Windows\Fonts\meiryo.ttc",
        shadow_offset: tuple[int, int] = (1, 1),
        shadow_blur_radius: int = 1,
    ) -> None:
        self.canvas = canvas
        self.color = "white"
        self.line_height = line_height
        self.shadow_offset = shadow_offset
        self.shadow_blur_radius = shadow_blur_radius

        # Extract integer point-size from the tkFont.Font
        tk_size = abs(int(font.cget("size")))
        self.pil_font = ImageFont.truetype(pil_font_path, tk_size)

        # Keep a reference so Tk doesn't garbage‑collect the image
        self._photoimage = None

    def render(
        self,
        text: str,
        max_width: int,
        bottom_anchor: int,
        sub_window: tk.Toplevel,
    ) -> None:
        from PIL import ImageFilter

        self.canvas.delete("all")
        if not text:
            return

        lines = text.splitlines() or [""]
        if len(lines) == 1:
            lines.insert(0, "")

        total_height = self.line_height * len(lines)
        shadow_offset = (2, 2)
        shadow_blur_radius = 4

        # Base transparent canvas
        img = Image.new("RGBA", (max_width, total_height), (0,0,0,0))

        # 1) Draw solid-black shadow
        shadow_img = Image.new("RGBA", (max_width, total_height), (0,0,0,0))
        draw_shadow = ImageDraw.Draw(shadow_img)
        y = 0
        for line in lines:
            bbox = draw_shadow.textbbox((0,0), line, font=self.pil_font)
            w = bbox[2] - bbox[0]
            x = (max_width - w)//2
            draw_shadow.text(
                (x + shadow_offset[0], y + shadow_offset[1]),
                line,
                font=self.pil_font,
                fill=(0, 0, 0, 255),  # <- fully opaque black
            )
            y += self.line_height

        # Blur it
        blurred_shadow = shadow_img.filter(ImageFilter.GaussianBlur(radius=shadow_blur_radius))
        img = Image.alpha_composite(img, blurred_shadow)

        # 2) Draw white text on top
        draw = ImageDraw.Draw(img)
        y = 0
        for line in lines:
            bbox = draw.textbbox((0,0), line, font=self.pil_font)
            w = bbox[2] - bbox[0]
            x = (max_width - w)//2
            draw.text(
                (x, y),
                line,
                font=self.pil_font,
                fill=(255, 255, 255, 255)  # <- fully opaque white
            )
            y += self.line_height

        # Blit to Tk
        self._photoimage = ImageTk.PhotoImage(img)
        self.canvas.config(width=max_width, height=total_height)
        self.canvas.create_image(0, 0, image=self._photoimage, anchor="nw")

        # Reposition window
        current_x = sub_window.winfo_x()
        new_y = bottom_anchor - total_height
        sub_window.geometry(f"{max_width}x{total_height}+{current_x}+{new_y}")
