

"""
SubtitleRenderer draws parsed subtitle segments onto the overlay canvas.

Design:
- Draw each subtitle segment as a whole base string instead of per-character canvas items.
- Draw ruby as a whole string above the segment.
- Keep hover-ruby behavior by storing per-segment hover regions.
- Cache wrapping, measurements, and layout.
"""

from __future__ import annotations

import logging
import re
import time
import tkinter as tk
from tkinter import font as tkFont
from typing import Any, Callable, Dict, List, Optional, Tuple

from model.config_manager import ConfigManager
from view.subtitle_overlay import SubtitleOverlayUI
from utils import get_monitor_rects, make_nonactivating_tool_window, show_window_no_activate

logger = logging.getLogger(__name__)

Segment = Tuple[str, Optional[str]]
FrozenSegments = Tuple[Segment, ...]
WrappedLines = Tuple[Tuple[Segment, ...], ...]


class SubtitleRenderer:
    def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
        self.config = config
        self.canvas = canvas

        self._hover_regions: List[dict] = []
        self._word_regions: List[dict] = []
        self._hover_active_region = None
        self._hover_active_mode: str | None = None
        self._hover_text_window: tk.Toplevel | None = None
        self._hover_text_label: tk.Label | None = None
        self._hover_text_current: str = ""
        self._dictionary_lookup: Callable[[str], str] | None = None
        self._word_tokenizer: Callable[[str], List[dict[str, Any]]] | None = None
        self._shift_state_callback: Callable[[], bool] | None = None

        self.font: Optional[tkFont.Font] = None
        self.ruby_font: Optional[tkFont.Font] = None
        self._font_settings = None

        self.hover_ruby_enabled = False
        self.color = "white"
        self.glow_color = "black"
        self.glow_radius = 4
        self.ruby_glow_radius = 3
        self.line_height = 0
        self.ruby_height = 0

        self._measure_cache: Dict[Tuple[int, str], int] = {}
        self._outline_offset_cache: Dict[int, List[Tuple[int, int]]] = {}
        self._split_cache: Dict[Tuple[str, int, int], Tuple[str, ...]] = {}
        self._wrap_cache: Dict[
            Tuple[FrozenSegments, int, int, int, int, int],
            WrappedLines,
        ] = {}
        self._layout_cache: Dict[Tuple, Dict[str, WrappedLines]] = {}
        self._last_overlay_width: Optional[int] = None
        self._layout_cache_hits = 0
        self._layout_cache_misses = 0

        self._timing_enabled = False
        self._timing_data = {
            "render_subtitle_time": 0.0,
            "font_creation_time": 0.0,
            "wrap_segments_time": 0.0,
            "split_text_to_fit_time": 0.0,
            "font_measure_time": 0.0,
            "draw_outlined_text_time": 0.0,
            "render_count": 0,
        }

    def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
        start = time.perf_counter()
        if self._timing_enabled:
            self._timing_data["render_count"] += 1

        try:
            self._refresh_fonts_if_needed()

            self._hover_regions = []
            self._word_regions = []
            self._hover_active_region = None
            self._hover_active_mode = None

            base_height = int(self.ruby_height * 2 + self.line_height * 2)
            y_ruby_top = self.ruby_height // 2
            y_base1 = self.ruby_height + self.line_height // 2

            wrap_limit_px = self._get_wrap_limit_px()

            if int(overlay.max_w) != int(self._last_overlay_width or 0):
                self._layout_cache.clear()
                self._last_overlay_width = int(overlay.max_w)

            layout_cache_key = (
                self._freeze_segments(top_segments),
                self._freeze_segments(bottom_segments),
                int(overlay.max_w),
                self._font_settings,
                int(wrap_limit_px) if wrap_limit_px else -1,
            )

            cached_layout = self._layout_cache.get(layout_cache_key)
            if cached_layout is not None:
                self._layout_cache_hits += 1
                wrapped_top = cached_layout["wrapped_top"]
                wrapped_bottom = cached_layout["wrapped_bottom"]
            else:
                self._layout_cache_misses += 1
                wrapped_top = self._wrap_segments(
                    top_segments, overlay.max_w, line_limit_px=wrap_limit_px
                ) if top_segments else ()
                wrapped_bottom = self._wrap_segments(
                    bottom_segments, overlay.max_w, line_limit_px=wrap_limit_px
                ) if bottom_segments else ()
                self._layout_cache[layout_cache_key] = {
                    "wrapped_top": wrapped_top,
                    "wrapped_bottom": wrapped_bottom,
                }

            lines = wrapped_top + wrapped_bottom

            if not lines:
                self.canvas.delete("all")
                self._finish_hover_bindings()
                return

            if len(lines) <= 2:
                try:
                    if int(overlay.max_h) != int(base_height):
                        overlay.update_geometry(int(overlay.max_w), int(base_height))
                except Exception:
                    pass
            else:
                block_h = self.line_height + self.ruby_height
                needed_h = int(block_h * len(lines))

                try:
                    sh = overlay.root.winfo_vrootheight()
                    max_h_allowed = max(80, int(sh) - 40)
                except Exception:
                    max_h_allowed = overlay.max_h

                target_h = max(int(base_height), needed_h)
                target_h = min(target_h, max_h_allowed)

                if int(overlay.max_h) != int(target_h):
                    overlay.update_geometry(int(overlay.max_w), int(target_h))

            self.canvas.delete("all")
            self._render_subtitle_lines(lines, y_ruby_top, y_base1, overlay)
            self._finish_hover_bindings()

        finally:
            if self._timing_enabled:
                self._timing_data["render_subtitle_time"] += time.perf_counter() - start

    def _freeze_segments(self, segments) -> FrozenSegments:
        if not segments:
            return ()
        frozen: List[Segment] = []
        for base, ruby in segments:
            frozen.append((base or "", ruby))
        return tuple(frozen)

    def _get_wrap_limit_px(self) -> Optional[int]:
        wrap_limit_px = None
        try:
            v = self.config.get("SUBTITLE_WRAP_LIMIT_PX")
            if v is not None:
                v = int(v)
                if v > 0:
                    wrap_limit_px = v
        except Exception:
            wrap_limit_px = None
        return wrap_limit_px

    def _render_subtitle_lines(self, lines, y_ruby_top, y_base1, overlay: SubtitleOverlayUI) -> None:
        if len(lines) == 1:
            self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
            return

        if len(lines) == 2:
            block_h = self.line_height + self.ruby_height
            base2_start = block_h
            y_base2 = base2_start + self.line_height // 2
            y_ruby_bot = base2_start + self.line_height + self.ruby_height // 2
            self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
            self._render_line(lines[1], y_ruby_bot, y_base2, overlay.max_w)
            return

        block_h = self.line_height + self.ruby_height
        avail_h = int(overlay.max_h)
        total_h = min(int(block_h * len(lines)), avail_h)
        top_offset = max(0, int((avail_h - total_h) / 2))

        for i, segs in enumerate(lines):
            block_y = top_offset + int(i * block_h)
            if i == len(lines) - 1:
                base_y = block_y + self.line_height // 2
                ruby_y = block_y + self.line_height + self.ruby_height // 2
            else:
                ruby_y = block_y + self.ruby_height // 2
                base_y = block_y + self.ruby_height + self.line_height // 2
            self._render_line(segs, ruby_y, base_y, overlay.max_w)

    def _render_line(self, segments, ruby_y, base_y, max_width):
        if not segments:
            return

        seg_meta = []
        total_w = 0
        line_text = "".join((base or "") for base, _ruby in segments)
        line_region_meta = []
        line_col = 0

        for base, ruby in segments:
            base_w = self._measure_text(self.font, base)
            if ruby:
                ruby_w = self._measure_text(self.ruby_font, ruby)
                seg_w = max(base_w, ruby_w)
            else:
                ruby_w = 0
                seg_w = base_w
            seg_meta.append((base, ruby, base_w, ruby_w, seg_w))
            total_w += seg_w

        cur_x = (max_width - total_w) / 2

        for base, ruby, base_w, ruby_w, seg_w in seg_meta:
            cx = cur_x + seg_w / 2
            base_left = cx - (base_w / 2)
            line_region_meta.append(
                {
                    "start": line_col,
                    "end": line_col + len(base or ""),
                    "base": base or "",
                    "base_left": base_left,
                }
            )
            line_col += len(base or "")

            if ruby and not self.hover_ruby_enabled:
                self._draw_ruby_text(ruby, base_w, ruby_w, cx, ruby_y)
            elif ruby and self.hover_ruby_enabled and self._contains_kanji(base):
                self._hover_regions.append(
                    {
                        "bbox": (
                            cur_x,
                            base_y - (self.line_height / 2),
                            cur_x + seg_w,
                            base_y + (self.line_height / 2),
                        ),
                        "ruby": ruby,
                        "base_w": base_w,
                        "ruby_w": ruby_w,
                        "cx": cx,
                        "ruby_y": ruby_y,
                        "base": base,
                        "lookup": base,
                    }
                )

            self._draw_outlined_text(
                self.canvas,
                cx,
                base_y,
                base,
                self.font,
                fill=self.color,
                outline=self.glow_color,
                thickness=self.glow_radius,
            )
            cur_x += seg_w

        self._add_word_regions_for_line(line_text, line_region_meta, base_y, ruby_y)

    def _refresh_fonts_if_needed(self) -> None:
        font_family = self.config.get("SUBTITLE_FONT")
        font_size = int(self.config.get("SUBTITLE_FONT_SIZE") or 12)
        hover_ruby_enabled = self.config.get("SUBTITLE_HOVER_RUBY")
        color = self.config.get("SUBTITLE_COLOR")
        glow_color = str(self.config.get("GLOW_COLOR") or "black")

        try:
            glow_radius = int(float(self.config.get("GLOW_RADIUS") or 10))
        except Exception:
            glow_radius = 10
        glow_radius = max(0, min(glow_radius, 20))

        settings = (font_family, font_size, hover_ruby_enabled, color, glow_color, glow_radius)

        if settings != self._font_settings or self.font is None or self.ruby_font is None:
            font_start = time.perf_counter()
            self.font = tkFont.Font(family=font_family, size=font_size, weight="bold")
            ruby_size = max(1, int(round(int(self.font.actual("size")) * 0.6)))
            self.ruby_font = tkFont.Font(
                family=self.font.actual("family"),
                size=ruby_size,
                weight="bold",
            )

            self.hover_ruby_enabled = hover_ruby_enabled
            self.color = str(color or "white")
            self.glow_color = glow_color
            self.glow_radius = glow_radius
            self.ruby_glow_radius = max(0, int(round(self.glow_radius * 0.6667)))

            self.line_height = int(self.font.metrics("linespace"))
            self.ruby_height = int(self.line_height * 0.6)

            self._font_settings = settings
            self._invalidate_measure_caches()

            if self._timing_enabled:
                self._timing_data["font_creation_time"] += time.perf_counter() - font_start

    def _invalidate_measure_caches(self) -> None:
        self._measure_cache.clear()
        self._outline_offset_cache.clear()
        self._split_cache.clear()
        self._wrap_cache.clear()
        self._layout_cache.clear()
        self._last_overlay_width = None

    def _wrap_segments(
        self,
        segments: List[Tuple[str, Optional[str]]],
        max_width: int,
        padding: int = 40,
        line_limit_px: Optional[int] = None,
    ) -> WrappedLines:
        cache_key = (
            self._freeze_segments(segments),
            int(max_width),
            int(padding),
            int(line_limit_px) if line_limit_px is not None else -1,
            id(self.font),
            id(self.ruby_font),
        )
        cached = self._wrap_cache.get(cache_key)
        if cached is not None:
            return cached

        start = time.perf_counter()
        try:
            if not segments:
                return ()

            limit = max(60, int(max_width) - int(padding) * 2)
            if line_limit_px is not None:
                try:
                    limit = max(60, min(limit, int(line_limit_px)))
                except Exception:
                    pass

            lines: List[List[Segment]] = []
            cur: List[Segment] = []
            cur_w = 0

            def _flush():
                nonlocal cur, cur_w
                if cur:
                    lines.append(cur)
                cur = []
                cur_w = 0

            for base, ruby in segments:
                if not base:
                    continue

                base_w = self._measure_text(self.font, base)
                if ruby:
                    ruby_w = self._measure_text(self.ruby_font, ruby)
                    seg_w = max(base_w, ruby_w)
                else:
                    seg_w = base_w

                if cur and (cur_w + seg_w) <= limit:
                    cur.append((base, ruby))
                    cur_w += seg_w
                    continue

                if cur and (cur_w + seg_w) > limit:
                    _flush()

                if seg_w <= limit:
                    cur.append((base, ruby))
                    cur_w += seg_w
                    continue

                if ruby:
                    cur.append((base, ruby))
                    _flush()
                    continue

                for chunk in self._split_text_to_fit(base, limit):
                    chunk_w = self._measure_text(self.font, chunk)
                    if cur and (cur_w + chunk_w) > limit:
                        _flush()
                    cur.append((chunk, None))
                    cur_w += chunk_w

            _flush()
            wrapped: WrappedLines = tuple(tuple(line) for line in lines)
            self._wrap_cache[cache_key] = wrapped
            return wrapped
        finally:
            if self._timing_enabled:
                self._timing_data["wrap_segments_time"] += time.perf_counter() - start

    def _measure_text(self, font_obj: Optional[tkFont.Font], text: str) -> int:
        if font_obj is None:
            return 0
        s = text or ""
        if not s:
            return 0

        key = (id(font_obj), s)
        cached = self._measure_cache.get(key)
        if cached is not None:
            return cached

        start = time.perf_counter()
        value = int(font_obj.measure(s))
        self._measure_cache[key] = value
        if self._timing_enabled:
            self._timing_data["font_measure_time"] += time.perf_counter() - start
        return value

    # def _get_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
    #     thickness = max(0, int(thickness))
    #     cached = self._outline_offset_cache.get(thickness)
    #     if cached is not None:
    #         return cached

    #     if thickness == 0:
    #         offsets: List[Tuple[int, int]] = []
    #     elif thickness == 1:
    #         offsets = [
    #             (-1, 0), (1, 0), (0, -1), (0, 1),
    #             (-1, -1), (-1, 1), (1, -1), (1, 1),
    #         ]
    #     else:
    #         mid = max(1, thickness // 2)
    #         offsets = [
    #             (-thickness, 0), (thickness, 0), (0, -thickness), (0, thickness),
    #             (-thickness, -thickness), (-thickness, thickness),
    #             (thickness, -thickness), (thickness, thickness),
    #             (-mid, 0), (mid, 0), (0, -mid), (0, mid),
    #         ]

    #     self._outline_offset_cache[thickness] = offsets
    #     return offsets
    
    def _get_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
        thickness = max(0, int(thickness))
        cached = self._outline_offset_cache.get(thickness)
        if cached is not None:
            return cached

        offsets = [
            (dx, dy)
            for dx in range(-thickness, thickness + 1)
            for dy in range(-thickness, thickness + 1)
            if dx or dy
        ]
        self._outline_offset_cache[thickness] = offsets
        return offsets
    
    def _draw_outlined_text(
        self,
        canvas: tk.Canvas,
        x: float,
        y: float,
        text: str,
        font: tkFont.Font,
        fill: str,
        outline: str,
        thickness: int,
        anchor: str = "center",
        tags=(),
    ) -> None:
        thickness = max(0, int(thickness))
        text = text or ""

        if thickness == 0:
            canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)
            return

        create_text = canvas.create_text
        offsets = self._get_outline_offsets(thickness)
        if len(offsets) > 16:
            offsets = self._get_approximate_outline_offsets(thickness)

        for dx, dy in offsets:
            create_text(
                x + dx,
                y + dy,
                text=text,
                fill=outline,
                font=font,
                anchor=anchor,
                tags=tags,
            )

        create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)

    def _get_approximate_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
        thickness = max(0, int(thickness))
        if thickness <= 1:
            return [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]

        offsets = [
            (-thickness, 0),
            (thickness, 0),
            (0, -thickness),
            (0, thickness),
            (-thickness, -thickness),
            (-thickness, thickness),
            (thickness, -thickness),
            (thickness, thickness),
        ]

        mid = max(1, thickness // 2)
        offsets.extend([
            (-mid, -thickness),
            (mid, -thickness),
            (-mid, thickness),
            (mid, thickness),
            (-thickness, -mid),
            (-thickness, mid),
            (thickness, -mid),
            (thickness, mid),
        ])
        return offsets





    # def _draw_outlined_text(
    #         self,
    #         canvas: tk.Canvas,
    #         x: float,
    #         y: float,
    #         text: str,
    #         font: tkFont.Font,
    #         fill: str,
    #         outline: str,
    #         thickness: int,
    #         anchor: str = "center",
    #         tags=(),
    #     ) -> None:
    #         start = time.perf_counter()

    #         thickness = max(0, int(thickness))
    #         text = text or ""

    #         if thickness == 0:
    #             canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)
    #             if self._timing_enabled:
    #                 self._timing_data["draw_outlined_text_time"] += time.perf_counter() - start
    #             return

    #         create_text = canvas.create_text
    #         offsets = self._get_outline_offsets(thickness)

    #         for dx, dy in offsets:
    #             create_text(
    #                 x + dx,
    #                 y + dy,
    #                 text=text,
    #                 fill=outline,
    #                 font=font,
    #                 anchor=anchor,
    #                 tags=tags,
    #             )

    #         create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)

    #         if self._timing_enabled:
    #             self._timing_data["draw_outlined_text_time"] += time.perf_counter() - start

    def _draw_ruby_text(
        self,
        ruby: str,
        base_w: int,
        ruby_w: int,
        center_x: float,
        y: float,
        tags=(),
    ) -> None:
        if not ruby:
            return

        self._draw_outlined_text(
            self.canvas,
            center_x,
            y,
            ruby,
            self.ruby_font,
            fill=self.color,
            outline=self.glow_color,
            thickness=self.ruby_glow_radius,
            tags=tags,
        )

    def _add_word_regions_for_line(self, line_text: str, segment_meta: list[dict[str, Any]], base_y: float, ruby_y: float) -> None:
        if not line_text:
            return
        tokens = self._tokenize_words(line_text)
        if not tokens:
            return

        for token in tokens:
            try:
                start = int(token.get("start"))
                end = int(token.get("end"))
            except Exception:
                continue
            if end <= start or start < 0 or end > len(line_text):
                continue
            surface = str(token.get("surface") or line_text[start:end]).strip()
            if not surface:
                continue

            x1 = None
            x2 = None
            for segment in segment_meta:
                seg_start = int(segment.get("start") or 0)
                seg_end = int(segment.get("end") or 0)
                if end <= seg_start or start >= seg_end:
                    continue
                base = str(segment.get("base") or "")
                local_start = max(0, start - seg_start)
                local_end = min(len(base), end - seg_start)
                if local_end <= local_start:
                    continue
                base_left = float(segment.get("base_left") or 0.0)
                part_x1 = base_left + self._measure_text(self.font, base[:local_start])
                part_x2 = base_left + self._measure_text(self.font, base[:local_end])
                x1 = part_x1 if x1 is None else min(x1, part_x1)
                x2 = part_x2 if x2 is None else max(x2, part_x2)

            if x1 is None or x2 is None or x2 <= x1:
                continue
            self._word_regions.append(
                {
                    "bbox": (
                        x1,
                        base_y - (self.line_height / 2),
                        x2,
                        base_y + (self.line_height / 2),
                    ),
                    "base": surface,
                    "lookup": str(token.get("lookup") or surface).strip(),
                    "ruby": str(token.get("reading") or "").strip(),
                    "cx": (x1 + x2) / 2,
                    "ruby_y": ruby_y,
                }
            )

    def _tokenize_words(self, text: str) -> List[dict[str, Any]]:
        callback = getattr(self, "_word_tokenizer", None)
        if callable(callback):
            try:
                spans = callback(text)
            except Exception:
                spans = []
            out: List[dict[str, Any]] = []
            for span in spans or []:
                try:
                    start = int(span.get("start"))
                    end = int(span.get("end"))
                except Exception:
                    continue
                if end <= start:
                    continue
                surface = str(span.get("surface") or text[start:end]).strip()
                if not surface:
                    continue
                out.append(
                    {
                        "surface": surface,
                        "lookup": str(span.get("lookup") or surface).strip(),
                        "reading": str(span.get("reading") or "").strip(),
                        "start": start,
                        "end": end,
                    }
                )
            if out:
                return out

        return [
            {
                "surface": match.group(0),
                "lookup": match.group(0),
                "reading": "",
                "start": match.start(),
                "end": match.end(),
            }
            for match in re.finditer(r"\S+", text or "")
        ]

    def _split_text_to_fit(self, text: str, max_width: int) -> List[str]:
        start = time.perf_counter()
        try:
            out: List[str] = []
            s = (text or "").replace("\t", " ")
            cache_key = (s, int(max_width), id(self.font))
            cached = self._split_cache.get(cache_key)
            if cached is not None:
                return list(cached)

            while s:
                if self._measure_text(self.font, s) <= max_width:
                    out.append(s)
                    break

                lo, hi = 1, len(s)
                best = 1
                while lo <= hi:
                    mid = (lo + hi) // 2
                    if self._measure_text(self.font, s[:mid]) <= max_width:
                        best = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1

                cut = best
                prefix = s[:best]

                m = re.search(r"[ \u3000、。，,.!?！？:：;；)\]】」』]\s*$", prefix)
                if m:
                    cut = m.end()
                    if cut < max(1, int(best * 0.5)):
                        cut = best

                chunk = s[:cut].rstrip()
                if chunk:
                    out.append(chunk)
                s = s[cut:].lstrip()

            self._split_cache[cache_key] = tuple(out)
            return out
        finally:
            if self._timing_enabled:
                self._timing_data["split_text_to_fit_time"] += time.perf_counter() - start

    def _finish_hover_bindings(self) -> None:
        has_ruby_hover = self.hover_ruby_enabled and self._hover_regions
        has_dictionary_hover = self._shift_hover_dictionary_enabled() and self._word_regions
        if has_ruby_hover or has_dictionary_hover:
            self.canvas.bind("<Motion>", self._on_hover_motion)
            self.canvas.bind("<Leave>", self._clear_hover_ruby)
        else:
            self._clear_hover_ruby()
            try:
                self.canvas.unbind("<Motion>")
                self.canvas.unbind("<Leave>")
            except Exception:
                pass

    def _on_hover_motion(self, event) -> None:
        dictionary_mode = self._shift_hover_dictionary_enabled() and self._is_shift_held()
        hit = None
        x = float(getattr(event, "x", 0))
        y = float(getattr(event, "y", 0))

        regions = self._word_regions if dictionary_mode else self._hover_regions
        for region in regions:
            x1, y1, x2, y2 = region["bbox"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                hit = region
                break

        mode = "dictionary" if dictionary_mode else "ruby"
        if hit is self._hover_active_region and mode == self._hover_active_mode:
            return

        self._clear_hover_ruby()
        self._hover_active_region = hit
        self._hover_active_mode = mode

        if hit is None:
            return

        if dictionary_mode:
            self._draw_word_highlight(hit)
            self._show_hover_dictionary(hit)
        else:
            self._draw_ruby_text(
                hit["ruby"],
                hit["base_w"],
                hit["ruby_w"],
                hit["cx"],
                hit["ruby_y"],
                tags=("hover_ruby",),
            )

    def _draw_word_highlight(self, region: dict) -> None:
        try:
            x1, y1, x2, y2 = region["bbox"]
            pad_x = 4
            pad_y = 2
            self.canvas.create_rectangle(
                x1 - pad_x,
                y1 + pad_y,
                x2 + pad_x,
                y2 - pad_y,
                fill="#303030",
                outline="#8a8a8a",
                width=1,
                tags=("hover_word_highlight",),
            )
            self.canvas.tag_lower("hover_word_highlight")
        except Exception:
            pass

    def _clear_hover_ruby(self, _event=None) -> None:
        try:
            self.canvas.delete("hover_ruby")
            self.canvas.delete("hover_word_highlight")
        except Exception:
            pass
        self._hide_hover_text_window()
        self._hover_active_region = None
        self._hover_active_mode = None

    def _shift_hover_dictionary_enabled(self) -> bool:
        try:
            return bool(self.config.get("SHIFT_HOVER_KANJI_DICTIONARY") or False)
        except Exception:
            return False

    def _is_shift_held(self) -> bool:
        callback = getattr(self, "_shift_state_callback", None)
        if not callable(callback):
            return False
        try:
            return bool(callback())
        except Exception:
            return False

    def _show_hover_dictionary(self, region: dict) -> None:
        lookup = getattr(self, "_dictionary_lookup", None)
        if not callable(lookup):
            return
        query = str(region.get("lookup") or region.get("base") or "").strip()
        if not query:
            return
        try:
            text = str(lookup(query) or "").strip()
        except Exception:
            text = ""
        if not text:
            return
        self._show_hover_text(region, text)

    def _show_hover_text(self, region: dict, text: str) -> None:
        label_text = str(text or "").strip()
        if not label_text:
            return

        parent = self.canvas.winfo_toplevel()
        if self._hover_text_window is None or not self._hover_text_window.winfo_exists():
            win = tk.Toplevel(parent)
            win.withdraw()
            win.overrideredirect(True)
            try:
                win.geometry("1x1+-32000+-32000")
            except Exception:
                pass
            win.attributes("-topmost", True)
            make_nonactivating_tool_window(win)
            win.configure(bg="black")
            label = tk.Label(
                win,
                text="",
                bg="black",
                fg=self.color,
                bd=0,
                padx=6,
                pady=3,
                justify="left",
                anchor="w",
                wraplength=520,
            )
            label.pack()
            self._hover_text_window = win
            self._hover_text_label = label

        try:
            font_size = max(8, int(float(self.config.get("SUBTITLE_FONT_SIZE") or 12) * 0.38))
            if label_text != self._hover_text_current:
                self._hover_text_label.configure(
                    text=label_text,
                    fg=self.color,
                    font=(self.font.actual("family") if self.font else "Arial", font_size, "bold"),
                )
                self._hover_text_current = label_text
        except Exception:
            try:
                self._hover_text_label.configure(text=label_text)
                self._hover_text_current = label_text
            except Exception:
                pass

        try:
            self._hover_text_window.update_idletasks()
        except Exception:
            pass

        try:
            x1, y1, x2, y2 = region["bbox"]
            hover_w = int(self._hover_text_window.winfo_reqwidth() or 1)
            hover_h = int(self._hover_text_window.winfo_reqheight() or 1)
            canvas_x = int(self.canvas.winfo_rootx())
            canvas_y = int(self.canvas.winfo_rooty())
            desired_x = int(canvas_x + ((x1 + x2) / 2) - (hover_w / 2))
            desired_y = int(canvas_y + y1 - hover_h - 5)
        except Exception:
            return

        desired_x, desired_y = self._clamp_hover_position(desired_x, desired_y, hover_w, hover_h)
        try:
            self._hover_text_window.geometry(f"{hover_w}x{hover_h}+{desired_x}+{desired_y}")
            show_window_no_activate(self._hover_text_window)
        except Exception:
            pass

    def _clamp_hover_position(self, x: int, y: int, w: int, h: int) -> tuple[int, int]:
        try:
            pointer_x = int(self.canvas.winfo_pointerx())
            pointer_y = int(self.canvas.winfo_pointery())
            rects = list(get_monitor_rects(self.canvas) or [])
        except Exception:
            pointer_x = pointer_y = 0
            rects = []

        monitor = None
        for rect in rects:
            rx, ry, rw, rh = rect
            if rx <= pointer_x < rx + rw and ry <= pointer_y < ry + rh:
                monitor = rect
                break
        if monitor is None and rects:
            monitor = rects[0]
        if monitor is None:
            try:
                monitor = (0, 0, int(self.canvas.winfo_screenwidth()), int(self.canvas.winfo_screenheight()))
            except Exception:
                monitor = (0, 0, 1920, 1080)

        rx, ry, rw, rh = monitor
        max_x = rx + max(0, rw - w)
        max_y = ry + max(0, rh - h)
        return max(rx, min(int(x), max_x)), max(ry, min(int(y), max_y))

    def _hide_hover_text_window(self) -> None:
        win = self._hover_text_window
        if win is None:
            return
        try:
            if win.winfo_exists():
                win.withdraw()
                self._hover_text_current = ""
        except Exception:
            pass

    def destroy_hover_windows(self) -> None:
        win = self._hover_text_window
        self._hover_text_window = None
        self._hover_text_label = None
        self._hover_text_current = ""
        if win is not None:
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass

    def refresh_hover_display(self) -> None:
        try:
            x = int(self.canvas.winfo_pointerx() - self.canvas.winfo_rootx())
            y = int(self.canvas.winfo_pointery() - self.canvas.winfo_rooty())
        except Exception:
            return
        try:
            width = int(self.canvas.winfo_width())
            height = int(self.canvas.winfo_height())
            if x < 0 or y < 0 or x >= width or y >= height:
                self._clear_hover_ruby()
                return
        except Exception:
            pass
        self._hover_active_region = None
        self._hover_active_mode = None
        self._on_hover_motion(type("_SubtitleHoverMotion", (), {"x": x, "y": y})())

    def bind_dictionary_lookup(self, callback) -> None:
        self._dictionary_lookup = callback

    def bind_word_tokenizer(self, callback) -> None:
        self._word_tokenizer = callback

    def bind_shift_state(self, callback) -> None:
        self._shift_state_callback = callback

    @staticmethod
    def _contains_kanji(text: str) -> bool:
        for ch in text or "":
            code = ord(ch)
            if 0x4E00 <= code <= 0x9FFF or code == 0x3005:
                return True
        return False

    def update_canvas(self, canvas: tk.Canvas):
        """
        Switch the renderer to a different canvas (after overlay update).
        No canvas-item pool is kept, so no cache reset is required here.
        """
        self.destroy_hover_windows()
        self.canvas = canvas






#old code:

# """
# SubtitleRenderer draws parsed subtitle segments onto the overlay canvas.

# Responsibilities:
# - Text/ruby layout
# - Pixel-based wrapping (optional)
# - Window height adjustments for multi-line subtitles
# - Hover ruby display
# """

# from __future__ import annotations

# import logging
# import re
# import tkinter as tk
# from tkinter import font as tkFont
# from typing import Dict, List, Optional, Tuple

# from model.config_manager import ConfigManager
# from view.subtitle_overlay import SubtitleOverlayUI

# logger = logging.getLogger(__name__)


# class SubtitleRenderer:
#     def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
#         self.config = config
#         self.canvas = canvas

#         self._hover_regions: List[dict] = []
#         self._hover_active_region = None

#         self.font: Optional[tkFont.Font] = None
#         self.ruby_font: Optional[tkFont.Font] = None
#         self._font_settings = None

#         self.hover_ruby_enabled = False
#         self.color = "white"
#         self.glow_color = "black"
#         self.glow_radius = 4
#         self.ruby_glow_radius = 3
#         self.line_height = 0
#         self.ruby_height = 0

#         self._measure_cache: Dict[Tuple[int, str], int] = {}
#         self._outline_offset_cache: Dict[int, List[Tuple[int, int]]] = {}
#         self._split_cache: Dict[Tuple[str, int, int], Tuple[str, ...]] = {}
#         self._wrap_cache: Dict[
#             Tuple[Tuple[Tuple[str, Optional[str]], ...], int, int, int, int, int],
#             Tuple[Tuple[Tuple[str, Optional[str]], ...], ...],
#         ] = {}
#         self._layout_cache: Dict[Tuple, Dict] = {}
#         self._last_overlay_width = None
#         self._layout_cache_hits = 0
#         self._layout_cache_misses = 0

#     def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
#         self._refresh_fonts_if_needed()

#         self._hover_regions = []
#         self._hover_active_region = None

#         base_height = int(self.ruby_height * 2 + self.line_height * 2)
#         y_ruby_top = self.ruby_height // 2
#         y_base1 = self.ruby_height + self.line_height // 2

#         wrap_limit_px = self._get_wrap_limit_px()

#         if int(overlay.max_w) != int(self._last_overlay_width or 0):
#             self._layout_cache.clear()
#             self._last_overlay_width = overlay.max_w

#         layout_cache_key = (
#             repr(top_segments),
#             repr(bottom_segments),
#             int(overlay.max_w),
#             self._font_settings,
#             int(wrap_limit_px) if wrap_limit_px else -1,
#         )
#         cached_layout = self._layout_cache.get(layout_cache_key)
#         if cached_layout is not None:
#             self._layout_cache_hits += 1
#             wrapped_top = cached_layout["wrapped_top"]
#             wrapped_bottom = cached_layout["wrapped_bottom"]
#         else:
#             self._layout_cache_misses += 1
#             wrapped_top = self._wrap_segments(top_segments, overlay.max_w, line_limit_px=wrap_limit_px) if top_segments else []
#             wrapped_bottom = self._wrap_segments(bottom_segments, overlay.max_w, line_limit_px=wrap_limit_px) if bottom_segments else []
#             self._layout_cache[layout_cache_key] = {
#                 "wrapped_top": wrapped_top,
#                 "wrapped_bottom": wrapped_bottom,
#             }

#         lines = wrapped_top + wrapped_bottom

#         self.canvas.delete("hover_ruby")

#         if not lines:
#             self._finish_hover_bindings()
#             return

#         if len(lines) <= 2:
#             try:
#                 if int(overlay.max_h) != int(base_height):
#                     overlay.update_geometry(int(overlay.max_w), int(base_height))
#             except Exception:
#                 pass
#         else:
#             block_h = self.line_height + self.ruby_height
#             needed_h = int(block_h * len(lines))

#             try:
#                 sh = overlay.root.winfo_vrootheight()
#                 max_h_allowed = max(80, int(sh) - 40)
#             except Exception:
#                 max_h_allowed = overlay.max_h

#             target_h = max(int(base_height), needed_h)
#             target_h = min(target_h, max_h_allowed)

#             if int(overlay.max_h) != int(target_h):
#                 overlay.update_geometry(int(overlay.max_w), int(target_h))

#         self._render_subtitle_lines(lines, y_ruby_top, y_base1, overlay)
#         self._finish_hover_bindings()

#     def _get_wrap_limit_px(self) -> Optional[int]:
#         wrap_limit_px = None
#         try:
#             v = self.config.get("SUBTITLE_WRAP_LIMIT_PX")
#             if v is not None:
#                 v = int(v)
#                 if v > 0:
#                     wrap_limit_px = v
#         except Exception:
#             wrap_limit_px = None
#         return wrap_limit_px

#     def _render_subtitle_lines(self, lines, y_ruby_top, y_base1, overlay: SubtitleOverlayUI) -> None:
#         if len(lines) == 1:
#             self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
#             return

#         if len(lines) == 2:
#             block_h = self.line_height + self.ruby_height
#             base2_start = block_h
#             y_base2 = base2_start + self.line_height // 2
#             y_ruby_bot = base2_start + self.line_height + self.ruby_height // 2
#             self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w)
#             self._render_line(lines[1], y_ruby_bot, y_base2, overlay.max_w)
#             return

#         block_h = self.line_height + self.ruby_height
#         avail_h = int(overlay.max_h)
#         total_h = min(int(block_h * len(lines)), avail_h)
#         top_offset = max(0, int((avail_h - total_h) / 2))

#         for i, segs in enumerate(lines):
#             block_y = top_offset + int(i * block_h)
#             if i == len(lines) - 1:
#                 base_y = block_y + self.line_height // 2
#                 ruby_y = block_y + self.line_height + self.ruby_height // 2
#             else:
#                 ruby_y = block_y + self.ruby_height // 2
#                 base_y = block_y + self.ruby_height + self.line_height // 2
#             self._render_line(segs, ruby_y, base_y, overlay.max_w)

#     def _render_line(self, segments, ruby_y, base_y, max_width):
#         if not segments:
#             return

#         seg_meta = []
#         total_w = 0

#         for base, ruby in segments:
#             base_w = self._measure_text(self.font, base)
#             if ruby:
#                 ruby_w = self._measure_text(self.ruby_font, ruby)
#                 seg_w = max(base_w, ruby_w)
#             else:
#                 ruby_w = 0
#                 seg_w = base_w
#             seg_meta.append((base, ruby, base_w, ruby_w, seg_w))
#             total_w += seg_w

#         cur_x = (max_width - total_w) / 2

#         for base, ruby, base_w, ruby_w, seg_w in seg_meta:
#             cx = cur_x + seg_w / 2

#             if ruby and not self.hover_ruby_enabled:
#                 self._draw_ruby_text(ruby, base_w, ruby_w, cx, ruby_y)
#             elif ruby and self.hover_ruby_enabled and self._contains_kanji(base):
#                 self._hover_regions.append(
#                     {
#                         "bbox": (
#                             cur_x,
#                             base_y - (self.line_height / 2),
#                             cur_x + seg_w,
#                             base_y + (self.line_height / 2),
#                         ),
#                         "ruby": ruby,
#                         "base_w": base_w,
#                         "ruby_w": ruby_w,
#                         "cx": cx,
#                         "ruby_y": ruby_y,
#                     }
#                 )

#             self._draw_outlined_text(
#                 self.canvas,
#                 cx,
#                 base_y,
#                 base,
#                 self.font,
#                 fill=self.color,
#                 outline=self.glow_color,
#                 thickness=self.glow_radius,
#             )
#             cur_x += seg_w

#     def _refresh_fonts_if_needed(self) -> None:
#         font_family = self.config.get("SUBTITLE_FONT")
#         font_size = int(self.config.get("SUBTITLE_FONT_SIZE") or 12)
#         hover_ruby_enabled = self.config.get("SUBTITLE_HOVER_RUBY")
#         color = self.config.get("SUBTITLE_COLOR")
#         glow_color = str(self.config.get("GLOW_COLOR") or "black")

#         try:
#             glow_radius = int(float(self.config.get("GLOW_RADIUS") or 10))
#         except Exception:
#             glow_radius = 10
#         glow_radius = max(0, min(glow_radius, 20))

#         settings = (font_family, font_size, hover_ruby_enabled, color, glow_color, glow_radius)

#         if settings != self._font_settings or self.font is None or self.ruby_font is None:
#             self.font = tkFont.Font(family=font_family, size=font_size, weight="bold")
#             ruby_size = max(1, int(round(int(self.font.actual("size")) * 0.6)))
#             self.ruby_font = tkFont.Font(family=self.font.actual("family"), size=ruby_size, weight="bold")

#             self.hover_ruby_enabled = hover_ruby_enabled
#             self.color = str(color or "white")
#             self.glow_color = glow_color
#             self.glow_radius = glow_radius
#             self.ruby_glow_radius = max(0, int(round(self.glow_radius * 0.6667)))

#             self.line_height = int(self.font.metrics("linespace"))
#             self.ruby_height = int(self.line_height * 0.6)

#             self._font_settings = settings
#             self._invalidate_measure_caches()

#     def _invalidate_measure_caches(self) -> None:
#         self._measure_cache.clear()
#         self._outline_offset_cache.clear()
#         self._split_cache.clear()
#         self._wrap_cache.clear()
#         self._layout_cache.clear()

#     def _wrap_segments(
#         self,
#         segments: List[Tuple[str, Optional[str]]],
#         max_width: int,
#         padding: int = 40,
#         line_limit_px: Optional[int] = None,
#     ) -> List[List[Tuple[str, Optional[str]]]]:
#         cache_key = (
#             tuple(segments),
#             int(max_width),
#             int(padding),
#             int(line_limit_px) if line_limit_px is not None else -1,
#             id(self.font),
#             id(self.ruby_font),
#         )
#         cached = self._wrap_cache.get(cache_key)
#         if cached is not None:
#             return [list(line) for line in cached]

#         try:
#             if not segments:
#                 return []

#             limit = max(60, int(max_width) - int(padding) * 2)
#             if line_limit_px is not None:
#                 try:
#                     limit = max(60, min(limit, int(line_limit_px)))
#                 except Exception:
#                     pass

#             lines: List[List[Tuple[str, Optional[str]]]] = []
#             cur: List[Tuple[str, Optional[str]]] = []
#             cur_w = 0

#             def _flush():
#                 nonlocal cur, cur_w
#                 if cur:
#                     lines.append(cur)
#                 cur = []
#                 cur_w = 0

#             for base, ruby in segments:
#                 if not base:
#                     continue

#                 base_w = self._measure_text(self.font, base)
#                 if ruby:
#                     ruby_w = self._measure_text(self.ruby_font, ruby)
#                     seg_w = max(base_w, ruby_w)
#                 else:
#                     seg_w = base_w

#                 if cur and (cur_w + seg_w) <= limit:
#                     cur.append((base, ruby))
#                     cur_w += seg_w
#                     continue

#                 if cur and (cur_w + seg_w) > limit:
#                     _flush()

#                 if seg_w <= limit:
#                     cur.append((base, ruby))
#                     cur_w += seg_w
#                     continue

#                 if ruby:
#                     cur.append((base, ruby))
#                     _flush()
#                     continue

#                 for chunk in self._split_text_to_fit(base, limit):
#                     chunk_w = self._measure_text(self.font, chunk)
#                     if cur and (cur_w + chunk_w) > limit:
#                         _flush()
#                     cur.append((chunk, None))
#                     cur_w += chunk_w

#             _flush()
#             wrapped = [tuple(line) for line in lines]
#             self._wrap_cache[cache_key] = tuple(wrapped)
#             return lines
#         finally:
#             pass

#     def _measure_text(self, font_obj: Optional[tkFont.Font], text: str) -> int:
#         if font_obj is None:
#             return 0
#         s = text or ""
#         if not s:
#             return 0

#         key = (id(font_obj), s)
#         cached = self._measure_cache.get(key)
#         if cached is not None:
#             return cached

#         value = int(font_obj.measure(s))
#         self._measure_cache[key] = value
#         return value

#     def _get_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
#         thickness = max(0, int(thickness))
#         cached = self._outline_offset_cache.get(thickness)
#         if cached is not None:
#             return cached

#         offsets = [
#             (dx, dy)
#             for dx in range(-thickness, thickness + 1)
#             for dy in range(-thickness, thickness + 1)
#             if dx or dy
#         ]
#         self._outline_offset_cache[thickness] = offsets
#         return offsets

#     def _draw_outlined_text(
#         self,
#         canvas: tk.Canvas,
#         x: float,
#         y: float,
#         text: str,
#         font: tkFont.Font,
#         fill: str,
#         outline: str,
#         thickness: int,
#         anchor: str = "center",
#         tags=(),
#     ) -> None:
#         thickness = max(0, int(thickness))
#         text = text or ""

#         if thickness == 0:
#             canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)
#             return

#         create_text = canvas.create_text
#         offsets = self._get_outline_offsets(thickness)
#         if len(offsets) > 16:
#             offsets = self._get_approximate_outline_offsets(thickness)

#         for dx, dy in offsets:
#             create_text(
#                 x + dx,
#                 y + dy,
#                 text=text,
#                 fill=outline,
#                 font=font,
#                 anchor=anchor,
#                 tags=tags,
#             )

#         create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)

#     def _get_approximate_outline_offsets(self, thickness: int) -> List[Tuple[int, int]]:
#         thickness = max(0, int(thickness))
#         if thickness <= 1:
#             return [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]

#         offsets = [
#             (-thickness, 0),
#             (thickness, 0),
#             (0, -thickness),
#             (0, thickness),
#             (-thickness, -thickness),
#             (-thickness, thickness),
#             (thickness, -thickness),
#             (thickness, thickness),
#         ]

#         mid = max(1, thickness // 2)
#         offsets.extend([
#             (-mid, -thickness),
#             (mid, -thickness),
#             (-mid, thickness),
#             (mid, thickness),
#             (-thickness, -mid),
#             (-thickness, mid),
#             (thickness, -mid),
#             (thickness, mid),
#         ])
#         return offsets

#     def _draw_ruby_text(
#         self,
#         ruby: str,
#         base_w: int,
#         ruby_w: int,
#         center_x: float,
#         y: float,
#         tags=(),
#     ) -> None:
#         if not ruby:
#             return

#         if base_w <= 0 or ruby_w >= base_w or len(ruby) <= 1:
#             self._draw_outlined_text(
#                 self.canvas,
#                 center_x,
#                 y,
#                 ruby,
#                 self.ruby_font,
#                 fill=self.color,
#                 outline=self.glow_color,
#                 thickness=self.ruby_glow_radius,
#                 tags=tags,
#             )
#             return

#         slot = base_w / max(1, len(ruby))
#         start_x = center_x - base_w / 2

#         for i, ch in enumerate(ruby):
#             ch_x = start_x + slot * (i + 0.5)
#             self._draw_outlined_text(
#                 self.canvas,
#                 ch_x,
#                 y,
#                 ch,
#                 self.ruby_font,
#                 fill=self.color,
#                 outline=self.glow_color,
#                 thickness=self.ruby_glow_radius,
#                 tags=tags,
#             )

#     def _split_text_to_fit(self, text: str, max_width: int) -> List[str]:
#         out: List[str] = []
#         s = (text or "").replace("\t", " ")
#         cache_key = (s, int(max_width), id(self.font))
#         cached = self._split_cache.get(cache_key)
#         if cached is not None:
#             return list(cached)

#         while s:
#             if self._measure_text(self.font, s) <= max_width:
#                 out.append(s)
#                 break

#             lo, hi = 1, len(s)
#             best = 1
#             while lo <= hi:
#                 mid = (lo + hi) // 2
#                 if self._measure_text(self.font, s[:mid]) <= max_width:
#                     best = mid
#                     lo = mid + 1
#                 else:
#                     hi = mid - 1

#             cut = best
#             prefix = s[:best]

#             m = re.search(r"[ \u3000、。，,.!?！？:：;；)\]】」』]\s*$", prefix)
#             if m:
#                 cut = m.end()
#                 if cut < max(1, int(best * 0.5)):
#                     cut = best

#             chunk = s[:cut].rstrip()
#             if chunk:
#                 out.append(chunk)
#             s = s[cut:].lstrip()

#         self._split_cache[cache_key] = tuple(out)
#         return out

#     def _finish_hover_bindings(self) -> None:
#         if self.hover_ruby_enabled and self._hover_regions:
#             self.canvas.bind("<Motion>", self._on_hover_motion)
#             self.canvas.bind("<Leave>", self._clear_hover_ruby)
#         else:
#             self._clear_hover_ruby()
#             try:
#                 self.canvas.unbind("<Motion>")
#                 self.canvas.unbind("<Leave>")
#             except Exception:
#                 pass

#     def _on_hover_motion(self, event) -> None:
#         hit = None
#         x = float(getattr(event, "x", 0))
#         y = float(getattr(event, "y", 0))

#         for region in self._hover_regions:
#             x1, y1, x2, y2 = region["bbox"]
#             if x1 <= x <= x2 and y1 <= y <= y2:
#                 hit = region
#                 break

#         if hit is self._hover_active_region:
#             return

#         self._clear_hover_ruby()
#         self._hover_active_region = hit

#         if hit is None:
#             return

#         self._draw_ruby_text(
#             hit["ruby"],
#             hit["base_w"],
#             hit["ruby_w"],
#             hit["cx"],
#             hit["ruby_y"],
#             tags=("hover_ruby",),
#         )

#     def _clear_hover_ruby(self, _event=None) -> None:
#         try:
#             self.canvas.delete("hover_ruby")
#         except Exception:
#             pass
#         self._hover_active_region = None

#     @staticmethod
#     def _contains_kanji(text: str) -> bool:
#         for ch in text or "":
#             code = ord(ch)
#             if 0x4E00 <= code <= 0x9FFF or code == 0x3005:
#                 return True
#         return False

#     def update_canvas(self, canvas: tk.Canvas):
#         """
#         Switch the renderer to a different canvas (after overlay update).
#         This renderer is stateless with respect to canvas items, so no cache reset is needed here.
#         """
#         self.canvas = canvas
