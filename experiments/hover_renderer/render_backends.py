"""Renderer backends for subtitle experiments (text primitives vs cached image)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
import hashlib
import tkinter as tk
from tkinter import font as tk_font

from PIL import Image, ImageDraw, ImageFont, ImageTk

from experiments.hover_renderer.font_utils import find_japanese_font_path
from experiments.hover_renderer.tokenizer import TokenSpan, tokenize_for_hover

Segment = Tuple[str, Optional[str]]
Line = Sequence[Segment]


@dataclass
class Style:
    font_family: str = "Meiryo"
    font_size: int = 46
    ruby_scale: float = 0.6
    text_color: str = "white"
    outline_color: str = "black"
    outline_radius: int = 3


@dataclass
class HoverBox:
    token: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class RenderResult:
    hover_boxes: List[HoverBox]
    cache_hit: bool = False


class _Layout:
    def __init__(self, style: Style) -> None:
        self.style = style
        self.base_font = tk_font.Font(
            family=style.font_family,
            size=max(8, int(style.font_size)),
            weight="bold",
        )
        self.ruby_font = tk_font.Font(
            family=style.font_family,
            size=max(7, int(style.font_size * style.ruby_scale)),
            weight="bold",
        )
        self.base_h = int(self.base_font.metrics("linespace"))
        self.ruby_h = int(self.ruby_font.metrics("linespace"))
        self.block_h = int(self.base_h + self.ruby_h + 6)

    def segment_width(self, base: str, ruby: Optional[str]) -> int:
        b = int(self.base_font.measure(base or ""))
        r = int(self.ruby_font.measure(ruby or "")) if ruby else 0
        return max(b, r)

    def line_width(self, segments: Line) -> int:
        return sum(self.segment_width(base, ruby) for base, ruby in segments)

    def line_positions(self, segments: Line, canvas_w: int) -> List[Tuple[Segment, float, int]]:
        total = self.line_width(segments)
        x = float((canvas_w - total) / 2.0)
        out: List[Tuple[Segment, float, int]] = []
        for seg in segments:
            w = self.segment_width(seg[0], seg[1])
            out.append((seg, x, w))
            x += w
        return out

    def token_boxes_for_segment(self, base: str, seg_x: float, seg_w: int, y0: float, y1: float) -> List[HoverBox]:
        if not base:
            return []
        base_w = float(self.base_font.measure(base))
        base_x = float(seg_x + max(0.0, (float(seg_w) - base_w) / 2.0))
        spans: List[TokenSpan] = tokenize_for_hover(base)
        out: List[HoverBox] = []
        for span in spans:
            left = base_x + float(self.base_font.measure(base[: span.start]))
            right = base_x + float(self.base_font.measure(base[: span.end]))
            if right <= left:
                continue
            out.append(HoverBox(token=span.text, x0=left, y0=y0, x1=right, y1=y1))
        return out


class BaseBackend:
    def render(self, canvas: tk.Canvas, lines: Sequence[Line], style: Style) -> RenderResult:
        raise NotImplementedError


class CanvasTextBackend(BaseBackend):
    def render(self, canvas: tk.Canvas, lines: Sequence[Line], style: Style) -> RenderResult:
        layout = _Layout(style)
        canvas_w = int(canvas.winfo_width() or canvas.winfo_reqwidth() or 1280)
        canvas_h = int(canvas.winfo_height() or canvas.winfo_reqheight() or 300)
        top = max(0, int((canvas_h - (len(lines) * layout.block_h)) / 2))
        hover_boxes: List[HoverBox] = []
        canvas.delete("all")

        for line_idx, segments in enumerate(lines):
            y_top = float(top + line_idx * layout.block_h)
            ruby_y = y_top + float(layout.ruby_h / 2)
            base_y = y_top + float(layout.ruby_h + (layout.base_h / 2))
            base_box_top = y_top + float(layout.ruby_h)
            base_box_bottom = base_box_top + float(layout.base_h)

            for (base, ruby), x, seg_w in layout.line_positions(segments, canvas_w):
                center_x = x + float(seg_w / 2.0)
                if ruby:
                    _draw_outlined_text(
                        canvas,
                        center_x,
                        ruby_y,
                        ruby,
                        layout.ruby_font,
                        style.text_color,
                        style.outline_color,
                        max(1, int(round(style.outline_radius * 0.7))),
                    )
                _draw_outlined_text(
                    canvas,
                    center_x,
                    base_y,
                    base,
                    layout.base_font,
                    style.text_color,
                    style.outline_color,
                    max(0, int(style.outline_radius)),
                )
                hover_boxes.extend(
                    layout.token_boxes_for_segment(
                        base=base,
                        seg_x=x,
                        seg_w=seg_w,
                        y0=base_box_top,
                        y1=base_box_bottom,
                    )
                )

        return RenderResult(hover_boxes=hover_boxes, cache_hit=False)


class CanvasImageBackend(BaseBackend):
    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[ImageTk.PhotoImage, List[HoverBox]]] = {}
        self.cache_hits = 0
        self.cache_misses = 0

    def render(self, canvas: tk.Canvas, lines: Sequence[Line], style: Style) -> RenderResult:
        layout = _Layout(style)
        canvas_w = int(canvas.winfo_width() or canvas.winfo_reqwidth() or 1280)
        canvas_h = int(canvas.winfo_height() or canvas.winfo_reqheight() or 300)
        key = self._cache_key(lines=lines, style=style, canvas_w=canvas_w, canvas_h=canvas_h)

        if key in self._cache:
            self.cache_hits += 1
            photo, hover_boxes = self._cache[key]
            canvas.delete("all")
            canvas.create_image(0, 0, image=photo, anchor="nw", tags=("subtitle_image",))
            canvas._subtitle_image_ref = photo  # keep ref
            return RenderResult(hover_boxes=list(hover_boxes), cache_hit=True)

        self.cache_misses += 1
        img = Image.new("RGBA", (max(2, canvas_w), max(2, canvas_h)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        base_font, ruby_font = _load_pillow_fonts(style=style)
        top = max(0, int((canvas_h - (len(lines) * layout.block_h)) / 2))
        hover_boxes: List[HoverBox] = []

        for line_idx, segments in enumerate(lines):
            y_top = float(top + line_idx * layout.block_h)
            ruby_y = y_top + float(layout.ruby_h / 2)
            base_y = y_top + float(layout.ruby_h + (layout.base_h / 2))
            base_box_top = y_top + float(layout.ruby_h)
            base_box_bottom = base_box_top + float(layout.base_h)

            for (base, ruby), x, seg_w in layout.line_positions(segments, canvas_w):
                center_x = x + float(seg_w / 2.0)
                if ruby:
                    _draw_outlined_text_pil(
                        draw=draw,
                        x=center_x,
                        y=ruby_y,
                        text=ruby,
                        font=ruby_font,
                        fill=style.text_color,
                        outline=style.outline_color,
                        thickness=max(1, int(round(style.outline_radius * 0.7))),
                    )
                _draw_outlined_text_pil(
                    draw=draw,
                    x=center_x,
                    y=base_y,
                    text=base,
                    font=base_font,
                    fill=style.text_color,
                    outline=style.outline_color,
                    thickness=max(0, int(style.outline_radius)),
                )
                hover_boxes.extend(
                    layout.token_boxes_for_segment(
                        base=base,
                        seg_x=x,
                        seg_w=seg_w,
                        y0=base_box_top,
                        y1=base_box_bottom,
                    )
                )

        photo = ImageTk.PhotoImage(img)
        self._cache[key] = (photo, list(hover_boxes))
        while len(self._cache) > 256:
            self._cache.pop(next(iter(self._cache)))

        canvas.delete("all")
        canvas.create_image(0, 0, image=photo, anchor="nw", tags=("subtitle_image",))
        canvas._subtitle_image_ref = photo
        return RenderResult(hover_boxes=hover_boxes, cache_hit=False)

    @staticmethod
    def _cache_key(lines: Sequence[Line], style: Style, canvas_w: int, canvas_h: int) -> str:
        payload = repr((list(lines), style, canvas_w, canvas_h)).encode("utf-8", "replace")
        return hashlib.sha1(payload).hexdigest()


def _draw_outlined_text(
    canvas: tk.Canvas,
    x: float,
    y: float,
    text: str,
    font: tk_font.Font,
    fill: str,
    outline: str,
    thickness: int,
) -> None:
    for dx in range(-thickness, thickness + 1):
        for dy in range(-thickness, thickness + 1):
            if dx == 0 and dy == 0:
                continue
            canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor="center")
    canvas.create_text(x, y, text=text, fill=fill, font=font, anchor="center")


def _draw_outlined_text_pil(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str,
    outline: str,
    thickness: int,
) -> None:
    for dx in range(-thickness, thickness + 1):
        for dy in range(-thickness, thickness + 1):
            if dx == 0 and dy == 0:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=outline, anchor="mm")
    draw.text((x, y), text, font=font, fill=fill, anchor="mm")


def _load_pillow_fonts(style: Style) -> Tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    base_size = max(8, int(style.font_size))
    ruby_size = max(7, int(style.font_size * style.ruby_scale))
    font_path = find_japanese_font_path(preferred_family=style.font_family)
    if font_path:
        try:
            return (
                ImageFont.truetype(font_path, size=base_size),
                ImageFont.truetype(font_path, size=ruby_size),
            )
        except Exception:
            pass
    return ImageFont.load_default(), ImageFont.load_default()
