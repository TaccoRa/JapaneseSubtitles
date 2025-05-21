import tkinter as tk
from tkinter import font as tkFont

class SubtitleRenderer:
    def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
        self.canvas = canvas
        self.font = font
        self.color = color
        self.line_height = line_height
        self.max_ruby_h = int(self.font.metrics('linespace') * 0.6)
        self.max_total_height = self.max_ruby_h * 2 + self.line_height * 2

    def render_subtitle(self, top_segments, bottom_segments, max_width: int,bottom_anchor: int, sub_window: tk.Toplevel) -> None:
        # measure max ruby height across both lines
        for segs in (top_segments, bottom_segments):
            for base, ruby in segs:
                if ruby:
                    rf = tkFont.Font(
                        family=self.font.actual('family'),
                        size=int(self.font.actual('size')*0.6),
                        weight="bold"
                    )
                    
        # total: ruby_above + line1 + line2 + ruby_below
        total_height = self.max_total_height

        # resize canvas
        self.canvas.delete("all")
        self.canvas.config(width=max_width, height=total_height)

        # Y positions
        y_ruby_top = self.max_ruby_h // 2
        y_base1   = self.max_ruby_h + self.line_height//2
        y_base2   = self.max_ruby_h + self.line_height + self.line_height//2
        y_ruby_bot = self.max_ruby_h + self.line_height*2 + self.max_ruby_h//2

        top_width = sum(self.font.measure(b) for b,_ in top_segments)
        bottom_width = sum(self.font.measure(b) for b,_ in bottom_segments)

        # starting X positions
        x_top = (max_width - top_width) / 2
        x_bot = (max_width - bottom_width) / 2

        # draw top line, if present
        cur_x = x_top
        if top_segments:
            for base, ruby in top_segments:
                w = self.font.measure(base)
                center = cur_x + w/2
                if ruby:
                    rf = tkFont.Font(
                        family=self.font.actual('family'),
                        size=int(self.font.actual('size')*0.6),
                        weight="bold"
                    )
                    self.draw_outlined_text(
                        self.canvas,
                        center, y_ruby_top,
                        ruby, rf,
                        fill=self.color,
                        outline="black",
                        thickness=2
                    )
                self.draw_outlined_text(
                    self.canvas,
                    center, y_base1,
                    base, self.font,
                    fill=self.color, outline="black", thickness=3
                )
                cur_x += w

        # draw bottom line
        cur_x = x_bot
        for base, ruby in bottom_segments:
            w = self.font.measure(base)
            center = cur_x + w/2
            if ruby:
                rf = tkFont.Font(
                    family=self.font.actual('family'),
                    size=int(self.font.actual('size')*0.6),
                    weight="bold"
                )
                self.draw_outlined_text(
                    self.canvas,
                    center, y_ruby_bot,
                    ruby, rf,
                    fill=self.color,
                    outline="black",
                    thickness=2
                )

            self.draw_outlined_text(
                self.canvas,
                center, y_base2,
                base, self.font,
                fill=self.color, outline="black", thickness=3
            )
            cur_x += w

        # reposition window
        # reposition window so that the second‐line baseline (y_base2) stays fixed on screen
        current_x = sub_window.winfo_x()
        # canvas above line2 is (max_ruby_h +  self.line_height*2)
        new_y = bottom_anchor - (self.max_ruby_h + self.line_height*2)
        # print(max_width, max_ruby_h)
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

    @staticmethod
    def _format_time(seconds: float) -> str:
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes:02d}:{secs:02d}"
    
    
    
# import tkinter as tk
# from tkinter import font as tkFont

# class SubtitleRenderer:
#     def __init__(self, canvas: tk.Canvas, font: tkFont.Font, color: str, line_height: int) -> None:
#         self.canvas = canvas
#         self.font = font
#         self.color = color
#         self.line_height = line_height
#         self._text_ids = []

#     def render(self, segments: list[tuple[str, str]], max_width: int, bottom_anchor: int, sub_window: tk.Toplevel) -> None:
#         self.canvas.delete("all")
#         if not segments or max_width <= 0:
#             return
        
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




# # 3D shadow effect:
# import tkinter as tk
# from tkinter import font as tkFont
# from PIL import Image, ImageDraw, ImageFont, ImageTk

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
#                 # font=self.font,
#                 fill=self.color,
#                 shadow_offset=(3, 3),
#                 # anchor="center"
#             )
#             y += self.line_height

#         # reposition the subtitle window
#         current_x = sub_window.winfo_x()
#         new_y = bottom_anchor - total_height
#         sub_window.geometry(f"{max_width}x{total_height}+{current_x}+{new_y}")

    # def _draw_with_shadow(self, x: int, y: int, text: str, font: tkFont.Font,
    #                       fill: str, shadow_offset: tuple[int, int],
    #                       anchor: str = "center") -> None:
    #     ox, oy = shadow_offset
    #     # shadow
    #     self.canvas.create_text(
    #         x + ox, y + oy,
    #         text=text,
    #         fill="black",
    #         font=font,
    #         anchor=anchor
    #     )
    #     # foreground
    #     self.canvas.create_text(
    #         x, y,
    #         text=text,
    #         fill=fill,
    #         font=font,
    #         anchor=anchor)

    # @staticmethod
    # def _format_time(seconds: float) -> str:
    #     minutes, secs = divmod(int(seconds), 60)
    #     return f"{minutes:02d}:{secs:02d}"

    # def _draw_with_shadow(self, x: int, y: int, text: str, fill: str,
    #                     shadow_offset: tuple[int, int], anchor: str = "center") -> None:
    #     # Convert tkinter font to a PIL ImageFont
    #     font_path = "meiryo.ttc"
    #     pil_font = ImageFont.truetype(font_path, 70)

    #     # Estimate image size
    #     bbox = pil_font.getbbox(text)
    #     text_width = bbox[2] - bbox[0]
    #     text_height = bbox[3] - bbox[1]

    #     img_width = text_width + max(shadow_offset[0], 0) + 10
    #     img_height = text_height + max(shadow_offset[1], 0) + 10

    #     # Create image with alpha channel
    #     image = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
    #     draw = ImageDraw.Draw(image)

    #     # Draw shadow
    #     draw.text((shadow_offset[0], shadow_offset[1]), text, font=pil_font, fill="black")

    #     # Draw text
    #     draw.text((0, 0), text, font=pil_font, fill=fill)

    #     # Convert to Tk image
    #     tk_image = ImageTk.PhotoImage(image)
    #     self.canvas.create_image(x, y, image=tk_image, anchor=anchor)

    #     # Prevent garbage collection
    #     if not hasattr(self, "_image_refs"):
    #         self._image_refs = []
    #     self._image_refs.append(tk_image)

# # Not that good
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
#         pil_font_path: str = "meiryo.ttc",
#     ) -> None:
#         self.canvas = canvas
#         self.color = color
#         self.line_height = line_height

#         # extract integer point‑size from the tkFont.Font
#         tk_size = 80
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
