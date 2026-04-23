"""
SubtitleRenderer draws parsed subtitle segments onto the overlay canvas.

Responsibilities:
- Text/ruby layout
- Pixel-based wrapping (optional)
- Window height adjustments for multi-line subtitles
"""

import tkinter as tk
from tkinter import font as tkFont
import re
from typing import List, Tuple, Optional
from view.subtitle_overlay import SubtitleOverlayUI
from model.config_manager import ConfigManager

class SubtitleRenderer:
    TOKEN_RE = re.compile(
        r"([0-9A-Za-z]+|[\u3040-\u30ff\u4e00-\u9fff\u3400-\u4dbf\u3005]+|[^\s])"
    )

    def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
        self.config = config
        self.canvas = canvas
        self.last_hover_boxes = []

    def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
        self.last_hover_boxes = []
        self.font = tkFont.Font(family=self.config.get("SUBTITLE_FONT"),size=self.config.get("SUBTITLE_FONT_SIZE"),weight="bold")
        self.ruby_font = tkFont.Font(family=self.font.actual("family"), size=int(self.font.actual("size") * 0.6), weight="bold")

        self.color=self.config.get("SUBTITLE_COLOR")
        self.glow_color = str(self.config.get("GLOW_COLOR") or "black")
        try:
            glow_radius = int(float(self.config.get("GLOW_RADIUS") or 10))
        except Exception:
            glow_radius = 10
        self.glow_radius = max(0, min(glow_radius, 20))
        self.ruby_glow_radius = max(0, int(round(self.glow_radius * 0.6667)))
        self.line_height = self.font.metrics("linespace")
        self.ruby_height = int(self.line_height * 0.6)
        base_height = int(self.ruby_height * 2 + self.line_height * 2)  # 2 lines + 2 ruby rows

        y_ruby_top = self.ruby_height // 2
        y_base1   = self.ruby_height + self.line_height//2
        y_ruby_bot = self.ruby_height + self.line_height*2 + self.ruby_height//2
        y_base2   = self.ruby_height + self.line_height + self.line_height//2

        # Wrap very long lines to the overlay width (clamped to the screen).
        wrap_limit_px = None
        try:
            v = self.config.get("SUBTITLE_WRAP_LIMIT_PX")
            if v is not None:
                v = int(v)
                if v > 0:
                    wrap_limit_px = v
        except Exception:
            wrap_limit_px = None

        wrapped_top = self._wrap_segments(top_segments, overlay.max_w, line_limit_px=wrap_limit_px) if top_segments else []
        wrapped_bottom = self._wrap_segments(bottom_segments, overlay.max_w, line_limit_px=wrap_limit_px) if bottom_segments else []
        lines = wrapped_top + wrapped_bottom

        self.canvas.delete("all")
        if not lines:
            return

        # Keep overlay height at the baseline (2 lines + 2 rubies) unless more lines are needed.
        if len(lines) <= 2:
            try:
                if int(overlay.max_h) != int(base_height):
                    overlay.update_geometry(int(overlay.max_w), int(base_height))
            except Exception:
                pass

        # Keep the old 2-line layout for simple cases (no visible behavior change).
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

        # Multi-line layout: ruby above each base line with enough spacing to avoid collisions.
        block_h = self.line_height + self.ruby_height
        needed_h = int(block_h * len(lines))

        # Grow overlay height if needed (clamped by overlay itself).
        try:
            sh = overlay.root.winfo_vrootheight()
            max_h_allowed = max(80, int(sh) - 40)
        except Exception:
            max_h_allowed = overlay.max_h

        # Grow/shrink overlay to the exact needed multi-line height, but never below the baseline.
        target_h = max(int(base_height), needed_h)
        target_h = min(target_h, max_h_allowed)
        if int(overlay.max_h) != int(target_h):
            overlay.update_geometry(int(overlay.max_w), int(target_h))

        # Center vertically when there is extra space.
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
        for base, ruby in segments:
            base_w = self.font.measure(base)
            if ruby:
                ruby_w = self.ruby_font.measure(ruby)
                seg_w = max(base_w, ruby_w)
            else:
                ruby_w = 0
                seg_w = base_w
            seg_meta.append((base, ruby, base_w, ruby_w, seg_w))
            total_w += seg_w
        cur_x = (max_width - total_w) / 2

        for base, ruby, base_w, ruby_w, seg_w in seg_meta:
            cx = cur_x + seg_w / 2
            if ruby:
                self._draw_ruby_text(ruby, base_w, ruby_w, cx, ruby_y)
            self.draw_outlined_text(
                self.canvas, cx, base_y,
                base, self.font, fill=self.color,
                outline=self.glow_color, thickness=self.glow_radius
            )
            self._append_hover_boxes_for_base(base, seg_left=cur_x, seg_w=seg_w, base_y=base_y)
            cur_x += seg_w

    def _draw_ruby_text(self, ruby: str, base_w: int, ruby_w: int, center_x: float, y: float) -> None:
        if not ruby:
            return
        # If ruby is longer than the base, just center it.
        if base_w <= 0 or ruby_w >= base_w or len(ruby) <= 1:
            self.draw_outlined_text(
                self.canvas, center_x, y,
                ruby, self.ruby_font, fill=self.color,
                outline=self.glow_color, thickness=self.ruby_glow_radius
            )
            return

        slot = base_w / max(1, len(ruby))
        start_x = center_x - base_w / 2
        for i, ch in enumerate(ruby):
            ch_x = start_x + slot * (i + 0.5)
            self.draw_outlined_text(
                self.canvas, ch_x, y,
                ch, self.ruby_font, fill=self.color,
                outline=self.glow_color, thickness=self.ruby_glow_radius
            )

    @staticmethod
    def draw_outlined_text(canvas: tk.Canvas, x: int, y: int, text: str,
                           font: tkFont.Font, fill: str, outline: str, thickness: int,
                           anchor: str = "center") -> None:
        for dx in range(-thickness, thickness + 1):
            for dy in range(-thickness, thickness + 1):
                if dx or dy:
                    canvas.create_text(x + dx, y + dy, text=text, fill=outline, font=font, anchor=anchor)
        canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor)

    def update_canvas(self, canvas: tk.Canvas):
        """Switch the renderer to a different canvas (after overlay update)."""
        self.canvas = canvas

    def _append_hover_boxes_for_base(self, base: str, seg_left: float, seg_w: float, base_y: float) -> None:
        if not base:
            return
        try:
            base_w = float(self.font.measure(base))
        except Exception:
            return
        base_left = float(seg_left + max(0.0, (float(seg_w) - base_w) / 2.0))
        y0 = float(base_y - (self.line_height / 2.0))
        y1 = float(y0 + self.line_height)
        for m in self.TOKEN_RE.finditer(base):
            token = m.group(0)
            if not token or not token.strip():
                continue
            x0 = base_left + float(self.font.measure(base[: m.start()]))
            x1 = base_left + float(self.font.measure(base[: m.end()]))
            if x1 <= x0:
                continue
            self.last_hover_boxes.append(
                {
                    "token": token,
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                }
            )

    def find_hover_box(self, x: float, y: float):
        for box in self.last_hover_boxes:
            try:
                if box["x0"] <= x <= box["x1"] and box["y0"] <= y <= box["y1"]:
                    return box
            except Exception:
                continue
        return None

    # ---------------------- Wrapping helpers ----------------------
    def _wrap_segments(
        self,
        segments: List[Tuple[str, Optional[str]]],
        max_width: int,
        padding: int = 40,
        line_limit_px: Optional[int] = None,
    ) -> List[List[Tuple[str, Optional[str]]]]:
        """
        Wraps a list of (base, ruby) segments into multiple lines that fit max_width.
        If a single plain-text segment exceeds the width, it will be split.
        Ruby segments are treated as atomic units.
        """
        if not segments:
            return []
        limit = max(60, int(max_width) - int(padding) * 2)
        if line_limit_px is not None:
            try:
                limit = max(60, min(limit, int(line_limit_px)))
            except Exception:
                pass

        lines: List[List[Tuple[str, Optional[str]]]] = []
        cur: List[Tuple[str, Optional[str]]] = []
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
            base_w = self.font.measure(base)
            if ruby:
                ruby_w = self.ruby_font.measure(ruby)
                seg_w = max(base_w, ruby_w)
            else:
                seg_w = base_w

            # If it fits on the current line, keep it.
            if cur and (cur_w + seg_w) <= limit:
                cur.append((base, ruby))
                cur_w += seg_w
                continue

            # If it doesn't fit and we already have content, start a new line.
            if cur and (cur_w + seg_w) > limit:
                _flush()

            # Now we're at the start of a line.
            if seg_w <= limit:
                cur.append((base, ruby))
                cur_w += seg_w
                continue

            # Segment itself is too wide.
            if ruby:
                # Can't split ruby safely; keep it as-is on its own line.
                cur.append((base, ruby))
                _flush()
                continue

            # Split plain text into chunks that fit.
            for chunk in self._split_text_to_fit(base, limit):
                chunk_w = self.font.measure(chunk)
                if cur and (cur_w + chunk_w) > limit:
                    _flush()
                cur.append((chunk, None))
                cur_w += chunk_w
        _flush()
        return lines

    def _split_text_to_fit(self, text: str, max_width: int) -> List[str]:
        """
        Split plain text into chunks that fit max_width (pixel-based).
        Prefers splitting at whitespace/punctuation when possible.
        """
        out: List[str] = []
        s = text or ""
        s = s.replace("\t", " ")
        while s:
            if self.font.measure(s) <= max_width:
                out.append(s)
                break

            # Find largest prefix that fits (binary search).
            lo, hi = 1, len(s)
            best = 1
            while lo <= hi:
                mid = (lo + hi) // 2
                if self.font.measure(s[:mid]) <= max_width:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1

            cut = best
            prefix = s[:best]

            # Try to cut at a nicer breakpoint near the end of the prefix.
            # (space, Japanese space, or common punctuation)
            m = re.search(r"[ \u3000、。，,.!?！？:：;；)\]】」』]\s*$", prefix)
            if m:
                # cut right after the breakpoint char(s)
                cut = m.end()
                # avoid tiny chunks
                if cut < max(1, int(best * 0.5)):
                    cut = best

            chunk = s[:cut].rstrip()
            if chunk:
                out.append(chunk)
            s = s[cut:].lstrip()
        return out
