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
        
        pad_x     = overlay.pad_x

        h = 0
        if top_segments:
            if any(r for _, r in top_segments): h += self.max_ruby_h
            h += self.line_height
        if bottom_segments:
            h += self.line_height
            if any(r for _, r in bottom_segments): h += self.max_ruby_h
        content_h = h
        
        win_w = content_w + 2*pad_x
        win_h = content_h

        ref_w = overlay.max_width + 2*overlay.pad_x
        ref_h = overlay.max_total_height 
        new_x = overlay.ref_x + (ref_w - win_w)//2
        new_y = overlay.ref_y + (ref_h - win_h)//2
        
        self.canvas.config(width=content_w, height=content_h)
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
        x_top = (content_w - top_w) / 2
        x_bot = (content_w - bot_w) / 2

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
