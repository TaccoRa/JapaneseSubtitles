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
    def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
        self.config = config
        self.canvas = canvas

    def render_subtitle(self, top_segments, bottom_segments, overlay: SubtitleOverlayUI) -> None:
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
            ruby_y = top_offset + int(i * block_h) + self.ruby_height // 2
            base_y = top_offset + int(i * block_h) + self.ruby_height + self.line_height // 2
            self._render_line(segs, ruby_y, base_y, overlay.max_w)

    def _render_line(self, segments, ruby_y, base_y, max_width):
        if not segments:
            return
        total_w = sum(self.font.measure(b) for b, _ in segments)
        cur_x = (max_width - total_w) / 2

        for base, ruby in segments:
            base_w = self.font.measure(base)
            cx = cur_x + base_w / 2
            if ruby:
                self.draw_outlined_text(
                    self.canvas, cx, ruby_y,
                    ruby, self.ruby_font, fill=self.color,
                    outline=self.glow_color, thickness=self.ruby_glow_radius
                )
            self.draw_outlined_text(
                self.canvas, cx, base_y,
                base, self.font, fill=self.color,
                outline=self.glow_color, thickness=self.glow_radius
            )
            cur_x += base_w

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
            seg_w = self.font.measure(base)

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
