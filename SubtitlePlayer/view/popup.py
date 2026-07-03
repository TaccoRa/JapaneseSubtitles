"""
Copy popup window shown on right-click.

Displays subtitle text and provides a simple context menu for mouse-only copy.
"""

from __future__ import annotations

import logging
import re
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkFont
from typing import Any

from utils import (
    get_monitor_rects,
    make_draggable,
    make_nonactivating_tool_window,
    show_window_no_activate,
)


logger = logging.getLogger(__name__)

TextSegment = tuple[str, str | None]
HitRegion = dict[str, Any]
ScreenRect = tuple[int, int, int, int]


@dataclass(frozen=True)
class _PopupMeasurements:
    lines: list[str]
    group_widths: list[int]
    line_count: int
    desired_width: int
    desired_height: int


class CopyPopup:
    _INLINE_RUBY_RE = re.compile(r"([^\[\]\n]+?)\[(.+?)\]")
    _TRAILING_TOKEN_RE = re.compile(r"^(.*?)(\S+)$", re.DOTALL)
    _TRAILING_RUBY_BASE_RE = re.compile(
        r"^(.*?)([\u3400-\u9fff\uf900-\ufaff\u3005]+|[\u30a0-\u30ff\u30fc]+)$",
        re.DOTALL,
    )
    _FALLBACK_SCREEN_RECT: ScreenRect = (0, 0, 1920, 1080)
    _TEXT_PAD_X = 8
    _TEXT_PAD_Y = 4
    _POPUP_PAD_X = 10
    _POPUP_PAD_Y = 5

    def __init__(self, root: tk.Tk, config) -> None:
        self.root = root
        self.config = config

        self._popup: tk.Toplevel | None = None
        self._close_job: str | None = None
        self._pinned = False
        self._menu_open = False
        self._entry_widget: tk.Text | None = None
        self._drag_grip: tk.Label | None = None
        self._on_add_anki = None
        self._dictionary_lookup = None
        self._translation_lookup = None
        self._shift_state_callback = None
        self._translation_state_callback = None
        self._translation_provider_callback = None
        self._anchor_window_callback = None
        self._dragging = False

        self._raw_subtitle_text: str = ""
        self._line_segments: list[list[TextSegment]] = []
        self._segment_hits: list[HitRegion] = []
        self._word_hits: list[HitRegion] = []
        self._hover_active_region: HitRegion | None = None
        self._hover_active_mode: str | None = None
        self._hover_ruby_window: tk.Toplevel | None = None
        self._hover_ruby_label: tk.Label | None = None
        self._word_tokenizer = None
        self._annotation_provider = None

        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self.root.bind_all("<Control-a>", self._select_all_if_pointer_in_popup, add="+")
        self.root.bind_all("<Control-A>", self._select_all_if_pointer_in_popup, add="+")

        self.bg_color = self.config.get("POPUP_BG_COLOR")
        self.font_name = self.config.get("POPUP_FONT")
        self.font_color = self.config.get("POPUP_FONT_COLOR")
        self.font_size = self.config.get("POPUP_FONT_SIZE")
        self.close_delay = int(self.config.get("POPUP_CLOSE_TIMER") or 1000)

    @staticmethod
    def _window_exists(window: tk.Misc | None) -> bool:
        if window is None:
            return False
        try:
            return bool(window.winfo_exists())
        except tk.TclError:
            return False
        except Exception:
            return False

    @classmethod
    def _safe_destroy(cls, window: tk.Misc | None) -> None:
        if not cls._window_exists(window):
            return
        try:
            window.destroy()
        except tk.TclError:
            pass
        except Exception:
            logger.debug("Failed to destroy popup window", exc_info=True)

    @classmethod
    def _safe_withdraw(cls, window: tk.Misc | None) -> None:
        if not cls._window_exists(window):
            return
        try:
            window.withdraw()
        except tk.TclError:
            pass
        except Exception:
            logger.debug("Failed to withdraw popup window", exc_info=True)

    def _on_root_destroy(self, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not self.root:
            return
        self._cancel_close()

    def _reset_popup_state(self, *, clear_content: bool = True) -> None:
        self._pinned = False
        self._menu_open = False
        self._dragging = False
        self._entry_widget = None
        self._drag_grip = None
        self._segment_hits = []
        self._word_hits = []
        self._hover_active_region = None
        self._hover_active_mode = None
        if clear_content:
            self._raw_subtitle_text = ""
            self._line_segments = []

    @classmethod
    def _split_trailing_token(cls, text: str) -> tuple[str, str]:
        text = text or ""
        if not text:
            return "", ""
        ruby_base_match = cls._TRAILING_RUBY_BASE_RE.match(text)
        if ruby_base_match:
            return ruby_base_match.group(1), ruby_base_match.group(2)
        match = cls._TRAILING_TOKEN_RE.match(text)
        if not match:
            return "", text
        return match.group(1), match.group(2)

    def _parse_inline_ruby(self, subtitle_text: str | None) -> list[list[TextSegment]]:
        """
        Parse a line like:
            文[もん]句[く]があるなら 盾[たて]つくか？
        into visible base text plus hidden ruby text.

        The ruby is attached to the trailing token immediately before the bracket,
        not to the whole preceding phrase.
        """
        lines: list[list[TextSegment]] = []
        for raw_line in (subtitle_text or "").splitlines():
            segments: list[TextSegment] = []
            last = 0

            for match in self._INLINE_RUBY_RE.finditer(raw_line):
                start, end = match.span()

                if start > last:
                    segments.append((raw_line[last:start], None))

                base_and_prefix = match.group(1) or ""
                ruby = match.group(2) or ""
                prefix, base = self._split_trailing_token(base_and_prefix)

                if prefix:
                    segments.append((prefix, None))
                if base and ruby:
                    segments.append((base, ruby))
                else:
                    segments.append((match.group(0), None))

                last = end

            if last < len(raw_line):
                segments.append((raw_line[last:], None))

            if not segments:
                segments = [("", None)]

            lines.append(segments)

        if not lines and subtitle_text:
            lines = [[(subtitle_text, None)]]
        return lines

    def _plain_popup_text(self) -> str:
        if not self._line_segments:
            return ""
        return "\n".join("".join(base for base, _ruby in line) for line in self._line_segments)

    def _clear_hover_ruby(self, _event=None) -> None:
        self._hover_active_region = None
        self._hover_active_mode = None
        self._clear_hover_highlight()
        self._safe_withdraw(self._hover_ruby_window)

    def _destroy_hover_ruby_window(self) -> None:
        win = self._hover_ruby_window
        self._hover_active_region = None
        self._hover_active_mode = None
        self._hover_ruby_window = None
        self._hover_ruby_label = None
        self._safe_destroy(win)

    def _clear_hover_highlight(self) -> None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return
        try:
            entry.tag_remove("hover_word", "1.0", "end")
        except Exception:
            pass

    def _set_hover_highlight(self, region: HitRegion | None) -> None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return
        self._clear_hover_highlight()
        if not region:
            return
        try:
            entry.tag_add("hover_word", region["start"], region["end"])
        except Exception:
            pass

    def _segment_bbox(self, entry: tk.Text, start: str, end: str):
        """
        Return a bounding box covering the whole visible segment from start to end.
        """
        try:
            first = entry.bbox(start)
            last = entry.bbox(f"{end} -1c")

            if not first:
                return None

            if not last:
                return first

            x1, y1, w1, h1 = first
            x2, y2, w2, h2 = last

            if y1 != y2:
                return first

            left = x1
            top = y1
            right = x2 + w2
            bottom = max(y1 + h1, y2 + h2)

            return (
                left,
                top,
                max(1, right - left),
                max(1, bottom - top),
            )
        except Exception:
            return None

    def _same_hover_region(self, left: HitRegion | None, right: HitRegion | None) -> bool:
        if left is None or right is None:
            return left is right
        return (
            str(left.get("start") or "") == str(right.get("start") or "")
            and str(left.get("end") or "") == str(right.get("end") or "")
            and str(left.get("lookup") or left.get("base") or "") == str(right.get("lookup") or right.get("base") or "")
        )

    def _get_selected_text(self) -> str:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return ""
        try:
            return re.sub(r"\s+", " ", entry.get("sel.first", "sel.last") or "").strip()
        except tk.TclError:
            return ""
        except Exception:
            return ""

    def _selected_text_region(self) -> HitRegion | None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return None
        selected = self._get_selected_text()
        if not selected:
            return None
        try:
            start = entry.index("sel.first")
            end = entry.index("sel.last")
            line_no = int(str(start).split(".", 1)[0])
        except Exception:
            return None
        return {
            "start": start,
            "end": end,
            "line": line_no,
            "base": selected,
            "lookup": selected,
            "ruby": "",
            "is_selection": True,
        }

    def _apply_popup_line_margins(
        self,
        entry: tk.Text,
        lines: list[str],
        group_widths: list[int],
        total_width: int,
        font: tkFont.Font,
    ) -> None:
        content_width = max(1, int(total_width) - 16)
        for line_no, line in enumerate(lines or [""], start=1):
            line_width = int(font.measure(line or ""))
            if line_no - 1 < len(group_widths):
                line_width = max(line_width, int(group_widths[line_no - 1] or 0))
            margin = max(0, int((content_width - line_width) / 2))
            tag_name = f"line_center_{line_no}"
            try:
                entry.tag_configure(tag_name, justify="left", lmargin1=margin, lmargin2=margin)
                entry.tag_add(tag_name, f"{line_no}.0", f"{line_no}.end")
            except Exception:
                pass


    def _pointer_position(self) -> tuple[int, int]:
        try:
            return int(self.root.winfo_pointerx()), int(self.root.winfo_pointery())
        except Exception:
            return 0, 0

    def _pointer_inside_window(self, window: tk.Misc | None) -> bool:
        if not self._window_exists(window):
            return False
        try:
            pointer_x, pointer_y = self._pointer_position()
            win_x = int(window.winfo_rootx())
            win_y = int(window.winfo_rooty())
            win_w = int(window.winfo_width() or window.winfo_reqwidth() or 0)
            win_h = int(window.winfo_height() or window.winfo_reqheight() or 0)
            return win_x <= pointer_x < win_x + win_w and win_y <= pointer_y < win_y + win_h
        except Exception:
            return False

    def _fallback_screen_rect(self, widget: tk.Misc | None = None) -> ScreenRect:
        owner = widget or self.root
        try:
            return (
                0,
                0,
                int(owner.winfo_screenwidth() or self._FALLBACK_SCREEN_RECT[2]),
                int(owner.winfo_screenheight() or self._FALLBACK_SCREEN_RECT[3]),
            )
        except Exception:
            return self._FALLBACK_SCREEN_RECT

    def _screen_rect_near_point(
        self,
        pointer_x: int,
        pointer_y: int,
        *,
        fallback_widget: tk.Misc | None = None,
    ) -> ScreenRect:
        try:
            rects = list(get_monitor_rects(self.root) or [])
        except Exception:
            rects = []

        for rx, ry, rw, rh in rects:
            if rx <= pointer_x < rx + rw and ry <= pointer_y < ry + rh:
                return (int(rx), int(ry), int(rw), int(rh))

        if rects:
            rx, ry, rw, rh = min(
                rects,
                key=lambda rect: (
                    abs(pointer_x - (rect[0] + (rect[2] // 2)))
                    + abs(pointer_y - (rect[1] + (rect[3] // 2)))
                ),
            )
            return int(rx), int(ry), int(rw), int(rh)

        return self._fallback_screen_rect(fallback_widget)

    @staticmethod
    def _clamp_window_position(
        x: int,
        y: int,
        width: int,
        height: int,
        screen_rect: ScreenRect,
    ) -> tuple[int, int]:
        screen_x, screen_y, screen_w, screen_h = screen_rect
        max_x = screen_x + max(0, screen_w - width)
        max_y = screen_y + max(0, screen_h - height)
        return (
            max(screen_x, min(int(x), max_x)),
            max(screen_y, min(int(y), max_y)),
        )

    def _ensure_hover_window(self, popup: tk.Toplevel) -> bool:
        if self._window_exists(self._hover_ruby_window) and self._hover_ruby_label is not None:
            return True

        self._destroy_hover_ruby_window()
        try:
            hover = tk.Toplevel(popup)
            hover.withdraw()
            hover.overrideredirect(True)
            hover.geometry("1x1+-32000+-32000")
            hover.attributes("-topmost", True)
            make_nonactivating_tool_window(hover)
            hover.configure(bg=self.bg_color)
            label = tk.Label(
                hover,
                text="",
                bg=self.bg_color,
                fg=self.font_color,
                bd=0,
                padx=2,
                pady=0,
                justify="left",
                anchor="w",
            )
            label.pack()
        except Exception:
            logger.debug("Failed to create hover popup", exc_info=True)
            self._hover_ruby_window = None
            self._hover_ruby_label = None
            return False

        self._hover_ruby_window = hover
        self._hover_ruby_label = label
        return True

    def _show_hover_text(self, region: HitRegion, text: str, *, font_scale: float = 0.60, bold: bool = True) -> None:
        popup = getattr(self, "_popup", None)
        entry = getattr(self, "_entry_widget", None)
        if not self._window_exists(popup) or entry is None:
            return

        label_text = str(text or "").strip()
        if not label_text:
            return

        if not self._ensure_hover_window(popup):
            return
        hover = self._hover_ruby_window
        label = self._hover_ruby_label
        if hover is None or label is None:
            return

        try:
            font_size = max(8, int(float(self.font_size) * float(font_scale)))
            font = (self.font_name, font_size, "bold" if bold else "normal")
            label.configure(text=label_text, font=font)
        except Exception:
            try:
                label.configure(text=label_text)
            except Exception:
                pass

        try:
            entry.update_idletasks()
            popup.update_idletasks()
            hover.update_idletasks()
        except Exception:
            pass

        bbox = self._segment_bbox(entry, region["start"], region["end"])
        if not bbox:
            return

        base_x, base_y, base_w, base_h = bbox

        try:
            hover_w = int(hover.winfo_reqwidth() or 1)
            hover_h = int(hover.winfo_reqheight() or 1)
            popup_x = int(popup.winfo_rootx())
            popup_y = int(popup.winfo_rooty())
            popup_h = int(popup.winfo_height() or popup.winfo_reqheight() or 1)
        except Exception:
            hover_w = hover_h = 1
            popup_x = popup_y = 0
            popup_h = 1

        center_x = int(popup_x + base_x + (base_w / 2) - (hover_w / 2))

        line_no = int(region.get("line") or 1)
        is_second_row = line_no == 2

        above_y = popup_y + base_y - hover_h - 3
        below_y = popup_y + base_y + base_h + 3
        desired_y = below_y if is_second_row else above_y

        try:
            pointer_x = int(self.root.winfo_pointerx())
            pointer_y = int(self.root.winfo_pointery())
        except Exception:
            pointer_x, pointer_y = popup_x + (hover_w // 2), popup_y + (popup_h // 2)

        screen_rect = self._screen_rect_near_point(pointer_x, pointer_y, fallback_widget=popup)
        desired_x, desired_y = self._clamp_window_position(center_x, desired_y, hover_w, hover_h, screen_rect)

        try:
            hover.geometry(f"{hover_w}x{hover_h}+{desired_x}+{desired_y}")
            show_window_no_activate(hover)
        except Exception:
            pass

    def _show_hover_ruby(self, region: HitRegion) -> None:
        ruby_text = str(region.get("ruby") or "")
        if ruby_text:
            self._show_hover_text(region, ruby_text, font_scale=0.60, bold=True)

    def _show_hover_dictionary(self, region: HitRegion) -> None:
        lookup = getattr(self, "_dictionary_lookup", None)
        if not callable(lookup):
            self._show_hover_ruby(region)
            return

        query = str(region.get("lookup") or region.get("base") or "").strip()
        if not query:
            self._show_hover_ruby(region)
            return

        try:
            if bool(region.get("is_selection")):
                entry_text = str(lookup(query, allow_translation_fallback=True) or "").strip()
            else:
                entry_text = str(lookup(query) or "").strip()
        except TypeError:
            try:
                entry_text = str(lookup(query) or "").strip()
            except Exception:
                entry_text = ""
        except Exception:
            entry_text = ""

        if entry_text:
            self._show_hover_text(region, entry_text, font_scale=0.55, bold=True)
        else:
            self._show_hover_ruby(region)

    def _show_hover_translation(self, region: HitRegion) -> None:
        lookup = getattr(self, "_translation_lookup", None)
        if not callable(lookup):
            return

        query = str(region.get("lookup") or region.get("base") or "").strip()
        if not query:
            return

        try:
            entry_text = str(lookup(query, provider=self._translation_provider()) or "").strip()
        except TypeError:
            try:
                entry_text = str(lookup(query) or "").strip()
            except Exception:
                entry_text = ""
        except Exception:
            entry_text = ""

        if entry_text:
            self._show_hover_text(region, entry_text, font_scale=0.55, bold=True)

    def refresh_hover_display(self) -> None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return
        try:
            x = int(entry.winfo_pointerx() - entry.winfo_rootx())
            y = int(entry.winfo_pointery() - entry.winfo_rooty())
        except Exception:
            return
        if not self._is_translation_held():
            try:
                width = int(entry.winfo_width())
                height = int(entry.winfo_height())
                if x < 0 or y < 0 or x >= width or y >= height:
                    self._clear_hover_ruby()
                    return
            except Exception:
                pass
        self._hover_active_region = None
        self._hover_active_mode = None
        self._on_popup_motion(type("_PopupMotion", (), {"x": x, "y": y})())

    def _region_at_pointer(self, entry: tk.Text, regions: list[HitRegion], x: int, y: int) -> HitRegion | None:
        for region in regions:
            if bool(region.get("is_selection")):
                try:
                    idx = entry.index(f"@{int(x)},{int(y)}")
                    line_no = int(str(idx).split(".", 1)[0])
                    line_start = f"{line_no}.0"
                    line_end = f"{line_no}.end"
                    start = region["start"] if entry.compare(region["start"], ">", line_start) else line_start
                    end = region["end"] if entry.compare(region["end"], "<", line_end) else line_end
                    if entry.compare(start, ">=", end):
                        continue
                    bbox = self._segment_bbox(entry, start, end)
                    if not bbox:
                        continue
                    bx, by, bw, bh = bbox
                    if bx <= x <= bx + bw and by <= y <= by + bh:
                        line_region = dict(region)
                        line_region["start"] = start
                        line_region["end"] = end
                        line_region["line"] = line_no
                        return line_region
                except Exception:
                    pass
                continue
            bbox = self._segment_bbox(entry, region["start"], region["end"])
            if not bbox:
                continue
            bx, by, bw, bh = bbox
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return region
        return None

    def _on_popup_motion(self, event) -> None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return

        x = int(event.x)
        y = int(event.y)
        translation_mode = self._is_translation_held()
        dictionary_mode = (not translation_mode) and self._shift_hover_dictionary_enabled() and self._is_shift_held()
        regions = self._word_hits if dictionary_mode else self._segment_hits

        hit = None
        if translation_mode:
            hit = self._selected_text_region()
            if hit is None:
                self._clear_hover_ruby()
                return
        elif dictionary_mode:
            selected_region = self._selected_text_region()
            if selected_region is not None:
                hit = self._region_at_pointer(entry, [selected_region], x, y)
        if hit is None:
            hit = self._region_at_pointer(entry, regions, x, y)
        if translation_mode:
            mode = "translation"
        elif dictionary_mode:
            mode = "dictionary"
        else:
            mode = "ruby"

        if self._same_hover_region(hit, self._hover_active_region) and mode == self._hover_active_mode:
            return

        if hit is None:
            self._clear_hover_ruby()
            return

        if self._hover_active_region is not None or self._hover_active_mode is not None:
            self._clear_hover_ruby()
        self._hover_active_region = hit
        self._hover_active_mode = mode
        if translation_mode:
            self._set_hover_highlight(hit)
            self._show_hover_translation(hit)
        elif dictionary_mode:
            self._set_hover_highlight(hit)
            self._show_hover_dictionary(hit)
        else:
            self._show_hover_ruby(hit)

    def _tokenize_words(self, text: str) -> list[dict[str, Any]]:
        callback = getattr(self, "_word_tokenizer", None)
        if callable(callback):
            try:
                spans = callback(text)
            except Exception:
                spans = []
            out: list[dict[str, Any]] = []
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

    def _rebuild_word_hits(self, plain_text: str) -> None:
        self._word_hits = []
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return

        lines = (plain_text or "").splitlines()
        if not lines and plain_text:
            lines = [plain_text]

        for line_no, line in enumerate(lines, start=1):
            for token in self._tokenize_words(line):
                start_col = int(token["start"])
                end_col = int(token["end"])
                if end_col <= start_col:
                    continue
                self._word_hits.append(
                    {
                        "start": f"{line_no}.{start_col}",
                        "end": f"{line_no}.{end_col}",
                        "line": line_no,
                        "base": str(token.get("surface") or line[start_col:end_col]),
                        "lookup": str(token.get("lookup") or token.get("surface") or ""),
                        "ruby": str(token.get("reading") or ""),
                    }
                )

    def _apply_annotation_tags(self, plain_text: str) -> None:
        provider = getattr(self, "_annotation_provider", None)
        entry = getattr(self, "_entry_widget", None)
        if provider is None or entry is None:
            return
        try:
            if not provider.enabled():
                return
            if bool(self.config.get("ANNOTATION_ONLY_ON_HOVER") or False):
                return
        except Exception:
            return

        lines = (plain_text or "").splitlines()
        if not lines and plain_text:
            lines = [plain_text]

        configured_tags: set[str] = set()
        for line_no, line in enumerate(lines, start=1):
            try:
                tokens = provider._tokens_for_line(line, self._word_tokenizer)
                intervals = provider._matched_intervals(tokens)
            except Exception:
                tokens = self._tokenize_words(line)
                intervals = []
                for token in tokens:
                    try:
                        match = provider.match_token(token)
                    except Exception:
                        match = None
                    if match is not None:
                        intervals.append((int(token.get("start") or 0), int(token.get("end") or 0), match))
            for start_col, end_col, match in intervals:
                try:
                    start_col = int(start_col)
                    end_col = int(end_col)
                except Exception:
                    continue
                if end_col <= start_col:
                    continue
                try:
                    style = provider._style_for_status(match.status)
                except Exception:
                    style = {}
                if not bool(style.get("enabled")):
                    continue
                tag_name = f"annotation_{match.status}"
                if tag_name not in configured_tags:
                    options = {}
                    if style.get("text_color"):
                        options["foreground"] = str(style.get("text_color"))
                    if style.get("background_color"):
                        options["background"] = str(style.get("background_color"))
                    if style.get("underline"):
                        options["underline"] = True
                    try:
                        if options:
                            entry.tag_configure(tag_name, **options)
                    except Exception:
                        logger.debug("Failed to configure popup annotation tag", exc_info=True)
                    configured_tags.add(tag_name)
                try:
                    entry.tag_add(tag_name, f"{line_no}.{start_col}", f"{line_no}.{end_col}")
                except Exception:
                    logger.debug("Failed to add popup annotation tag", exc_info=True)

    def _rebuild_segment_hits(self) -> None:
        self._segment_hits = []
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return

        for line_no, line_segments in enumerate(self._line_segments, start=1):
            col = 0
            for seg_index, (base, ruby) in enumerate(line_segments):
                base = base or ""
                start = f"{line_no}.{col}"
                end = f"{line_no}.{col + len(base)}"

                if ruby and base:
                    tag_name = f"ruby_{line_no}_{seg_index}"
                    try:
                        entry.tag_add(tag_name, start, end)
                    except Exception:
                        pass
                    self._segment_hits.append(
                        {
                            "tag": tag_name,
                            "ruby": ruby,
                            "start": start,
                            "end": end,
                            "line": line_no,
                            "base": base,
                        }
                    )

                col += len(base)

    def _destroy_current_popup(self) -> None:
        popup = self._popup
        if popup is None:
            self._cancel_close()
            self._destroy_hover_ruby_window()
            self._reset_popup_state()
            return

        self._cancel_close()
        self._destroy_hover_ruby_window()
        self._safe_destroy(popup)
        if popup is self._popup:
            self._popup = None
            self._reset_popup_state()

    def _prepare_popup_content(self, subtitle_text: str | None) -> str:
        self._raw_subtitle_text = subtitle_text or ""
        self._line_segments = self._parse_inline_ruby(self._raw_subtitle_text)
        return self._plain_popup_text()

    def _create_popup_window(self) -> tk.Toplevel:
        popup = tk.Toplevel(self.root)
        self._popup = popup
        self._reset_popup_state(clear_content=False)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        make_nonactivating_tool_window(popup)
        return popup

    def _popup_fonts(self) -> tuple[tkFont.Font, tkFont.Font]:
        base_font = tkFont.Font(family=self.font_name, size=self.font_size, weight="bold")
        ruby_font = tkFont.Font(
            family=self.font_name,
            size=max(8, int(float(self.font_size) * 0.60)),
            weight="bold",
        )
        return base_font, ruby_font

    def _measure_popup_content(
        self,
        plain_text: str,
        base_font: tkFont.Font,
        ruby_font: tkFont.Font,
    ) -> _PopupMeasurements:
        lines = plain_text.splitlines()
        visible_lines = [line for line in lines if line.strip()]
        text_width = max(
            (base_font.measure(line) for line in visible_lines),
            default=base_font.measure((plain_text or "").strip() or " "),
        )
        group_widths = [
            self._group_line_width(line_segments, base_font, ruby_font)
            for line_segments in self._line_segments
        ]
        text_width = max(text_width, max(group_widths, default=0))

        line_count = max(1, len(lines) if lines else 1)
        desired_width = int(text_width + 2 * self._POPUP_PAD_X)
        desired_height = int((base_font.metrics("linespace") * line_count) + 2 * self._POPUP_PAD_Y)
        return _PopupMeasurements(
            lines=lines,
            group_widths=group_widths,
            line_count=line_count,
            desired_width=desired_width,
            desired_height=desired_height,
        )

    def _create_text_widget(
        self,
        popup: tk.Toplevel,
        plain_text: str,
        font: tkFont.Font,
        line_count: int,
    ) -> tk.Text:
        entry = tk.Text(
            popup,
            font=font,
            wrap="word",
            padx=self._TEXT_PAD_X,
            pady=self._TEXT_PAD_Y,
            bg=self.bg_color,
            fg=self.font_color,
            cursor="xterm",
            height=line_count,
        )
        entry.insert("1.0", plain_text)
        entry.tag_configure("hover_word", background="#343434")
        self._entry_widget = entry
        return entry

    def _create_drag_grip(self, popup: tk.Toplevel) -> tk.Label:
        drag_grip = tk.Label(
            popup,
            text=":::",
            font=(self.font_name, max(8, int(float(self.font_size) * 0.45))),
            fg=self.font_color,
            bg=self.bg_color,
            cursor="fleur",
            bd=0,
            padx=2,
            pady=0,
        )
        drag_grip.place(relx=1.0, rely=1.0, x=-2, y=-2, anchor="se")
        self._drag_grip = drag_grip
        make_draggable(drag_grip, popup, on_release=self._on_popup_drag_end)
        drag_grip.bind("<ButtonPress-1>", self._on_popup_drag_start, add="+")
        drag_grip.bind("<ButtonRelease-1>", self._on_popup_drag_end, add="+")
        drag_grip.bind("<Enter>", lambda _event: self._cancel_close(), add="+")
        drag_grip.bind("<Leave>", lambda _event: self._on_popup_leave(), add="+")
        return drag_grip

    def _copy_to_clipboard(self, owner: tk.Misc, text: str) -> None:
        if not text:
            return
        try:
            owner.clipboard_clear()
            owner.clipboard_append(text)
        except Exception:
            logger.debug("Failed to copy popup text to clipboard", exc_info=True)

    def _copy_selection_to_clipboard(self, owner: tk.Misc) -> None:
        self._copy_to_clipboard(owner, self._get_selected_text())

    def _copy_all_to_clipboard(self, owner: tk.Misc) -> None:
        self._copy_to_clipboard(owner, self._raw_subtitle_text or "")

    def _add_selection_to_anki_from_popup(self, popup: tk.Toplevel, plain_text: str) -> None:
        selected = self._get_selected_text()
        if not selected:
            logger.debug("Add Selection To Anki skipped because no text is selected")
            return
        if not callable(self._on_add_anki):
            logger.debug("Add Selection To Anki skipped because no callback is bound")
            return

        def _run() -> None:
            try:
                self._on_add_anki(selected_text=selected, subtitle_text=plain_text or "")
            except Exception:
                logger.exception("Add Selection To Anki failed")

        try:
            popup.after(1, _run)
        except Exception:
            _run()

    def add_selected_to_anki_if_pointer_inside(self) -> bool:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return False
        if not self._pointer_inside_window(popup):
            return False
        if not self._get_selected_text():
            return False
        self._add_selection_to_anki_from_popup(popup, self._plain_popup_text())
        return True

    def _build_context_menu(self, popup: tk.Toplevel, plain_text: str) -> tk.Menu:
        menu = tk.Menu(popup, tearoff=0)
        menu.add_command(label="Copy", command=lambda: self._copy_selection_to_clipboard(popup))
        menu.add_command(label="Copy All", command=lambda: self._copy_all_to_clipboard(popup))
        menu.add_command(
            label="Add Selection To Anki",
            command=lambda: self._add_selection_to_anki_from_popup(popup, plain_text),
        )
        menu.add_separator()
        menu.add_command(label="Pin", command=lambda: self._pin(popup))
        return menu

    def _selection_contains_region(self, entry: tk.Text, region: HitRegion) -> bool:
        try:
            sel_first = entry.index("sel.first")
            sel_last = entry.index("sel.last")
            return (
                entry.compare(region["start"], ">=", sel_first)
                and entry.compare(region["end"], "<=", sel_last)
            )
        except Exception:
            return False

    def _select_word_at_pointer(self, entry: tk.Text, x: int, y: int) -> None:
        region = self._region_at_pointer(entry, self._word_hits, int(x), int(y))
        if not region:
            try:
                entry.tag_remove("sel", "1.0", "end")
            except Exception:
                pass
            return
        if self._selection_contains_region(entry, region):
            return
        try:
            entry.tag_remove("sel", "1.0", "end")
            entry.tag_add("sel", region["start"], region["end"])
            entry.mark_set("insert", region["end"])
        except Exception:
            pass

    def _select_all_text(self, entry: tk.Text | None = None):
        entry = entry or getattr(self, "_entry_widget", None)
        if entry is None:
            return "break"
        try:
            self._cancel_close()
            self._clear_hover_ruby()
            entry.tag_remove("sel", "1.0", "end")
            entry.tag_add("sel", "1.0", "end-1c")
            entry.mark_set("insert", "end-1c")
            entry.see("insert")
        except Exception:
            pass
        return "break"

    def _select_all_if_pointer_in_popup(self, event=None):
        popup = getattr(self, "_popup", None)
        entry = getattr(self, "_entry_widget", None)
        if entry is None or not self._window_exists(popup):
            return None
        if getattr(event, "widget", None) in (popup, entry):
            return self._select_all_text(entry)
        if self._pointer_inside_window(popup):
            return self._select_all_text(entry)
        return None

    def _show_context_menu(self, event, menu: tk.Menu, entry: tk.Text):
        self._menu_open = True
        self._cancel_close()
        try:
            self._clear_hover_ruby()
            self._select_word_at_pointer(entry, int(event.x), int(event.y))
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
            self._menu_open = False
            if not self._pinned:
                self._restart_close()
        return "break"

    def _bind_popup_events(self, popup: tk.Toplevel, entry: tk.Text, menu: tk.Menu) -> None:
        entry.bind("<Button-3>", lambda event: self._show_context_menu(event, menu, entry))
        entry.bind("<Enter>", lambda _event: self._cancel_close())
        entry.bind("<Motion>", self._on_popup_motion)
        entry.bind("<Leave>", self._on_popup_leave)
        entry.bind("<Control-a>", lambda _event: self._select_all_text(entry))
        entry.bind("<Control-A>", lambda _event: self._select_all_text(entry))
        popup.bind("<Control-a>", lambda _event: self._select_all_text(entry))
        popup.bind("<Control-A>", lambda _event: self._select_all_text(entry))
        popup.bind("<Enter>", lambda _event: self._cancel_close())
        popup.bind("<Leave>", lambda _event: self._on_popup_leave())
        popup.bind("<Destroy>", lambda event, owner=popup: self._on_popup_destroy(owner, event), add="+")

    @staticmethod
    def _clamp_popup_size(measurements: _PopupMeasurements, screen_rect: ScreenRect) -> tuple[int, int]:
        _screen_x, _screen_y, screen_w, screen_h = screen_rect
        max_w = max(240, int(screen_w * 0.90))
        max_h = max(120, int(screen_h * 0.60))
        return (
            min(max_w, int(measurements.desired_width)),
            min(max_h, int(measurements.desired_height)),
        )

    def _popup_position(
        self,
        width: int,
        height: int,
        screen_rect: ScreenRect,
        pointer_x: int,
        pointer_y: int,
    ) -> tuple[int, int]:
        anchor_rect = self._anchor_window_rect()
        if anchor_rect is not None:
            anchor_x, anchor_y, anchor_w, _anchor_h = anchor_rect
            x = int(anchor_x + (anchor_w // 2) - (width // 2))
            y = int(anchor_y - height)
        else:
            x = int(pointer_x - (width // 2))
            y = int(pointer_y - height - 16)
        return self._clamp_window_position(x, y, width, height, screen_rect)

    def open_copy_popup(self, subtitle_text=None) -> None:
        self._destroy_current_popup()

        popup = self._create_popup_window()
        plain_text = self._prepare_popup_content(subtitle_text)
        base_font, ruby_font = self._popup_fonts()
        measurements = self._measure_popup_content(plain_text, base_font, ruby_font)

        entry = self._create_text_widget(popup, plain_text, base_font, measurements.line_count)
        self._rebuild_segment_hits()
        self._rebuild_word_hits(plain_text)
        self._apply_annotation_tags(plain_text)
        entry.config(state="disabled")
        entry.pack(fill="both", expand=True)

        self._create_drag_grip(popup)
        menu = self._build_context_menu(popup, plain_text)
        self._bind_popup_events(popup, entry, menu)

        pointer_x, pointer_y = self._pointer_position()
        screen_rect = self._screen_rect_near_point(pointer_x, pointer_y, fallback_widget=popup)
        total_width, total_height = self._clamp_popup_size(measurements, screen_rect)
        x, y = self._popup_position(total_width, total_height, screen_rect, pointer_x, pointer_y)

        self._apply_popup_line_margins(
            entry,
            measurements.lines,
            measurements.group_widths,
            total_width,
            base_font,
        )
        popup.geometry(f"{total_width}x{total_height}+{x}+{y}")
        show_window_no_activate(popup)
        self._restart_close(popup)

    def bind_add_to_anki(self, callback) -> None:
        self._on_add_anki = callback

    def bind_dictionary_lookup(self, callback) -> None:
        self._dictionary_lookup = callback

    def bind_translation_lookup(self, callback) -> None:
        self._translation_lookup = callback

    def bind_word_tokenizer(self, callback) -> None:
        self._word_tokenizer = callback

    def bind_annotation_provider(self, provider) -> None:
        self._annotation_provider = provider

    def bind_shift_state(self, callback) -> None:
        self._shift_state_callback = callback

    def bind_translation_state(self, callback) -> None:
        self._translation_state_callback = callback

    def bind_translation_provider(self, callback) -> None:
        self._translation_provider_callback = callback

    def bind_anchor_window(self, callback) -> None:
        self._anchor_window_callback = callback

    def _shift_hover_dictionary_enabled(self) -> bool:
        return bool(self.config.get("SHIFT_HOVER_KANJI_DICTIONARY") or False)

    def _is_shift_held(self) -> bool:
        callback = getattr(self, "_shift_state_callback", None)
        if not callable(callback):
            return False
        try:
            return bool(callback())
        except Exception:
            return False

    def _is_translation_held(self) -> bool:
        callback = getattr(self, "_translation_state_callback", None)
        if not callable(callback):
            return False
        try:
            return bool(callback())
        except Exception:
            return False

    def _translation_provider(self) -> str:
        callback = getattr(self, "_translation_provider_callback", None)
        if callable(callback):
            try:
                value = str(callback() or "").strip().lower()
                if value in {"deepl", "google"}:
                    return value
            except Exception:
                pass
        return "deepl"

    def _anchor_window_rect(self) -> tuple[int, int, int, int] | None:
        callback = getattr(self, "_anchor_window_callback", None)
        if not callable(callback):
            return None
        try:
            win = callback()
            if not self._window_exists(win):
                return None
            win.update_idletasks()
            return (
                int(win.winfo_rootx()),
                int(win.winfo_rooty()),
                int(win.winfo_width() or win.winfo_reqwidth() or 0),
                int(win.winfo_height() or win.winfo_reqheight() or 0),
            )
        except Exception:
            return None

    def is_open(self) -> bool:
        return self._window_exists(getattr(self, "_popup", None))

    def ensure_on_top(self) -> None:
        """
        Keep popup above the other always-on-top windows in this app.

        Note: other windows (overlay/control) also set -topmost, so z-order depends on who is lifted last.
        """
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return
        try:
            show_window_no_activate(popup)
        except Exception:
            pass

    def set_busy_cursor(self, cursor: str) -> None:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return
        if cursor:
            self._cancel_close()
        try:
            popup.configure(cursor=cursor)
        except Exception:
            pass
        entry = getattr(self, "_entry_widget", None)
        if entry is not None:
            try:
                entry.configure(cursor=cursor or "xterm")
            except Exception:
                pass

    def _set_popup_colors(self, bg: str, entry_cursor: str = "xterm") -> None:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return
        try:
            popup.configure(bg=bg)
        except Exception:
            pass
        entry = getattr(self, "_entry_widget", None)
        if entry is not None:
            try:
                entry.configure(bg=bg, cursor=entry_cursor)
            except Exception:
                pass
        grip = getattr(self, "_drag_grip", None)
        if grip is not None:
            try:
                grip.configure(bg=bg)
            except Exception:
                pass

    def mark_anki_busy(self) -> None:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return
        self._cancel_close()
        self._set_popup_colors(str(self.bg_color or "black"), entry_cursor="watch")

    def mark_anki_success(self, duration_ms: int = 1200) -> None:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return

        success_bg = "#168a3a"
        self._cancel_close()
        self._set_popup_colors(success_bg, entry_cursor="xterm")
        try:
            popup.configure(cursor="")
        except Exception:
            pass
        if not self._pointer_inside_window(popup):
            self._schedule_close(popup, max(300, int(duration_ms)))

    def mark_anki_warning(self, duration_ms: int = 1800) -> None:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return
        self._cancel_close()
        self._set_popup_colors("#a66a00", entry_cursor="xterm")
        try:
            popup.configure(cursor="")
        except Exception:
            pass
        if not self._pointer_inside_window(popup):
            self._schedule_close(popup, max(500, int(duration_ms)))

    def mark_anki_failure(self, duration_ms: int = 2200) -> None:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return
        self._cancel_close()
        self._set_popup_colors("#9f2525", entry_cursor="xterm")
        try:
            popup.configure(cursor="")
        except Exception:
            pass
        if not self._pointer_inside_window(popup):
            self._schedule_close(popup, max(800, int(duration_ms)))

    def _schedule_close(self, popup: tk.Toplevel, delay_ms: int) -> None:
        self._cancel_close()
        if not self._window_exists(popup):
            return
        try:
            self._close_job = self.root.after(max(0, int(delay_ms)), lambda target=popup: self._close(target))
        except Exception:
            self._close_job = None

    def _close(self, popup: tk.Toplevel | None = None) -> None:
        target = popup or self._popup
        if target is None:
            self._close_job = None
            return

        if target is not self._popup:
            self._safe_destroy(target)
            return

        if self._pointer_inside_window(target):
            self._close_job = None
            return

        self._close_job = None
        self._destroy_hover_ruby_window()
        self._safe_destroy(target)
        self._popup = None
        self._reset_popup_state()

    def _cancel_close(self) -> None:
        if not self._close_job:
            return
        try:
            self.root.after_cancel(self._close_job)
        except Exception:
            pass
        self._close_job = None

    def _restart_close(self, popup: tk.Toplevel | None = None) -> None:
        target = popup or self._popup
        if target is not None:
            self._schedule_close(target, self.close_delay)

    def _on_popup_leave(self, _event=None) -> None:
        if self._pinned or self._menu_open or self._dragging:
            return
        self._clear_hover_ruby()
        self._restart_close()

    def _on_popup_drag_start(self, event=None) -> None:
        self._dragging = True
        self._cancel_close()

    def _on_popup_drag_end(self, *args, **kwargs) -> None:
        self._dragging = False
        if not self._pinned and not self._menu_open:
            self._restart_close()

    def _pin(self, popup: tk.Toplevel) -> None:
        if popup is not self._popup or not self._window_exists(popup):
            return
        self._cancel_close()
        self._pinned = True
        try:
            popup.overrideredirect(False)
            popup.lift()
        except Exception:
            pass

    def _on_popup_destroy(self, popup: tk.Toplevel | None = None, event=None) -> None:
        if event is not None and getattr(event, "widget", None) is not popup:
            return
        if popup is not None and popup is not self._popup:
            return
        self._destroy_hover_ruby_window()
        self._popup = None
        self._close_job = None
        self._reset_popup_state()


    def _group_line_width(
        self,
        line_segments: list[TextSegment],
        base_font: tkFont.Font,
        ruby_font: tkFont.Font,
    ) -> int:
        total = 0
        for base, ruby in line_segments:
            base = base or ""
            base_w = int(base_font.measure(base)) if base else 0
            if ruby:
                ruby_w = int(ruby_font.measure(ruby))
                total += max(base_w, ruby_w)
            else:
                total += base_w
        return total
