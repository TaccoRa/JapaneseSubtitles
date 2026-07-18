"""
Copy popup window shown on right-click.

Displays subtitle text and provides a simple context menu for mouse-only copy.
"""

from __future__ import annotations

import logging
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkFont
from typing import Any

from model.hover_layers import (
    choose_hover_region,
    clean_hover_translation,
    combine_hover_text,
    hover_layers,
)
from utils import (
    dispatch_to_tk,
    get_monitor_rects,
    make_draggable,
    make_nonactivating_tool_window,
    show_window_no_activate,
)

try:
    from SubtitlePlayer.furigana_splitter import iter_number_counter_matches
except ImportError:
    from furigana_splitter import iter_number_counter_matches


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
    _HOVER_TEXT_MAX_WIDTH_PX = 520
    _HOVER_TEXT_MIN_WIDTH_PX = 24
    _HOVER_TEXT_PAD_X = 8
    _HOVER_TEXT_PAD_Y = 4
    _SPACE_TRANSLATION = str.maketrans(
        {
            "\u00a0": " ",
            "\u1680": " ",
            "\u180e": " ",
            "\u2000": " ",
            "\u2001": " ",
            "\u2002": " ",
            "\u2003": " ",
            "\u2004": " ",
            "\u2005": " ",
            "\u2006": " ",
            "\u2007": " ",
            "\u2008": " ",
            "\u2009": " ",
            "\u200a": " ",
            "\u200b": " ",
            "\u202f": " ",
            "\u205f": " ",
            "\u3000": " ",
            "\ufeff": " ",
        }
    )

    def __init__(self, root: tk.Tk, config) -> None:
        self.root = root
        self.config = config

        self._popup: tk.Toplevel | None = None
        self._close_job: str | None = None
        self._hover_clear_job: str | None = None
        self._pinned = False
        self._menu_open = False
        self._entry_widget: tk.Text | None = None
        self._drag_grip: tk.Label | None = None
        self._on_add_anki = None
        self._dictionary_lookup = None
        self._translation_lookup = None
        self._shift_state_callback = None
        self._hover_mode_callback = None
        self._translation_state_callback = None
        self._translation_provider_callback = None
        self._anchor_window_callback = None
        self._dragging = False
        self._manual_selection_active = False

        self._raw_subtitle_text: str = ""
        self._line_segments: list[list[TextSegment]] = []
        self._segment_hits: list[HitRegion] = []
        self._word_hits: list[HitRegion] = []
        self._last_selected_text: str = ""
        self._hover_active_region: HitRegion | None = None
        self._hover_active_ruby_region: HitRegion | None = None
        self._hover_active_mode: str | None = None
        self._hover_ruby_window: tk.Toplevel | None = None
        self._hover_ruby_label: tk.Text | None = None
        self._hover_layer_window: tk.Toplevel | None = None
        self._hover_layer_label: tk.Text | None = None
        self._hover_active_translation_key: tuple[str, str] | None = None
        self._translation_hover_cache: dict[tuple[str, str], str] = {}
        self._translation_hover_pending: set[tuple[str, str]] = set()
        self._hover_layer_job = None
        self._word_tokenizer = None
        self._annotation_provider = None

        self.root.bind("<Destroy>", self._on_root_destroy, add="+")
        self.root.bind_all("<Control-a>", self._select_all_if_pointer_in_popup, add="+")
        self.root.bind_all("<Control-A>", self._select_all_if_pointer_in_popup, add="+")

        self.bg_color = self.config.get("POPUP_BG_COLOR")
        self.font_name = self.config.get("POPUP_FONT")
        self.font_color = self.config.get("POPUP_FONT_COLOR")
        self.font_size = self.config.get("POPUP_FONT_SIZE")
        close_delay = self.config.get("POPUP_CLOSE_TIMER")
        if close_delay is None or str(close_delay).strip() == "":
            close_delay = 1000
        self.close_delay = max(0, int(float(close_delay)))
        self.hover_clear_delay = self._coerce_hover_clear_delay(self.config.get("POPUP_HOVER_CLEAR_DELAY_MS"))

    @staticmethod
    def _coerce_hover_clear_delay(value) -> int:
        try:
            delay = int(float(value))
        except Exception:
            delay = 500
        return max(0, min(60000, delay))

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
        self._cancel_hover_clear()
        self._cancel_close()

    def _reset_popup_state(self, *, clear_content: bool = True) -> None:
        self._cancel_hover_clear()
        self._pinned = False
        self._menu_open = False
        self._dragging = False
        self._manual_selection_active = False
        self._entry_widget = None
        self._drag_grip = None
        self._segment_hits = []
        self._word_hits = []
        self._hover_active_region = None
        self._hover_active_ruby_region = None
        self._hover_active_mode = None
        self._hover_active_translation_key = None
        if clear_content:
            self._raw_subtitle_text = ""
            self._line_segments = []

    @classmethod
    def _split_trailing_token(cls, text: str) -> tuple[str, str]:
        text = text or ""
        if not text:
            return "", ""
        counter_matches = [
            match
            for match, _reading in iter_number_counter_matches(text)
            if match.end() == len(text)
        ]
        if counter_matches:
            match = counter_matches[-1]
            return text[: match.start()], match.group(0)
        ruby_base_match = cls._TRAILING_RUBY_BASE_RE.match(text)
        if ruby_base_match:
            return ruby_base_match.group(1), ruby_base_match.group(2)
        match = cls._TRAILING_TOKEN_RE.match(text)
        if not match:
            return "", text
        return match.group(1), match.group(2)

    @classmethod
    def _normalize_inline_ruby_spacing(cls, text: str) -> str:
        return str(text or "").translate(cls._SPACE_TRANSLATION)

    def _parse_inline_ruby(self, subtitle_text: str | None) -> list[list[TextSegment]]:
        """
        Parse a line like:
            文[もん]句[く]があるなら 盾[たて]つくか？
        into visible base text plus hidden ruby text.

        The ruby is attached to the trailing token immediately before the bracket,
        not to the whole preceding phrase.
        """
        lines: list[list[TextSegment]] = []
        for raw_line in self._normalize_inline_ruby_spacing(subtitle_text or "").splitlines():
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
        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        self._hover_active_region = None
        self._hover_active_ruby_region = None
        self._hover_active_mode = None
        self._hover_active_translation_key = None
        self._clear_hover_highlight()
        self._safe_withdraw(self._hover_ruby_window)
        self._safe_withdraw(self._hover_layer_window)

    def _destroy_hover_ruby_window(self) -> None:
        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        win = self._hover_ruby_window
        self._hover_active_region = None
        self._hover_active_ruby_region = None
        self._hover_active_mode = None
        self._hover_active_translation_key = None
        self._hover_ruby_window = None
        self._hover_ruby_label = None
        self._safe_destroy(win)
        self._destroy_hover_layer_window()

    def _destroy_hover_layer_window(self) -> None:
        win = self._hover_layer_window
        self._hover_layer_window = None
        self._hover_layer_label = None
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
        if not region or self._manual_selection_active:
            return
        try:
            if entry.tag_ranges("sel"):
                return
        except Exception:
            pass
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
            and left.get("ruby_char_start") == right.get("ruby_char_start")
            and left.get("ruby_char_end") == right.get("ruby_char_end")
        )

    @classmethod
    def _clean_selected_text(cls, value: str | None) -> str:
        text = str(value or "").translate(cls._SPACE_TRANSLATION)
        return re.sub(r"\s+", " ", text).strip()

    def _get_selected_text(self) -> str:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return ""
        try:
            selected = self._clean_selected_text(entry.get("sel.first", "sel.last"))
            if selected:
                self._last_selected_text = selected
            return selected
        except tk.TclError:
            return ""
        except Exception:
            return ""

    def _current_or_last_selected_text(self) -> str:
        return self._get_selected_text() or self._clean_selected_text(getattr(self, "_last_selected_text", ""))

    def _remember_popup_selection(self, _event=None):
        if self._get_selected_text():
            self._clear_hover_highlight()
        return None

    def _begin_popup_selection(self, _event=None):
        self._manual_selection_active = True
        self._focus_popup_for_hotkeys()
        self._clear_hover_ruby()
        return None

    def _continue_popup_selection(self, _event=None):
        self._clear_hover_highlight()
        return None

    def _end_popup_selection(self, _event=None):
        self._manual_selection_active = False

        def finish_selection() -> None:
            self._remember_popup_selection()
            self._hover_active_region = None
            self._hover_active_mode = None
            self.refresh_hover_display()

        try:
            self.root.after_idle(finish_selection)
        except Exception:
            finish_selection()
        return None

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
        context_region = self._word_region_for_selection(entry, start, end)
        lookup = selected
        reading = ""
        sentence_lookup = selected
        if context_region is not None:
            lookup = str(context_region.get("lookup") or selected).strip()
            reading = str(context_region.get("reading") or "").strip()
            sentence_lookup = str(context_region.get("sentence_lookup") or selected).strip()
        return {
            "start": start,
            "end": end,
            "line": line_no,
            "base": selected,
            "lookup": lookup,
            "sentence_lookup": sentence_lookup,
            "lookup_reading": reading,
            "is_selection": True,
        }

    def _word_region_for_selection(
        self,
        entry: tk.Text,
        start: str,
        end: str,
    ) -> HitRegion | None:
        """Keep the tokenizer lemma when a manual selection is one whole token."""
        matches: list[HitRegion] = []
        for region in self._word_hits:
            try:
                if not entry.compare(region["start"], "==", start):
                    continue
                if not entry.compare(region["end"], "==", end):
                    continue
            except Exception:
                continue
            matches.append(region)
        if not matches:
            return None
        return min(
            matches,
            key=lambda region: len(str(region.get("base") or "")),
        )

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

    def _ensure_hover_window(self, popup: tk.Toplevel, *, layer: bool = False) -> bool:
        window_attr = "_hover_layer_window" if layer else "_hover_ruby_window"
        label_attr = "_hover_layer_label" if layer else "_hover_ruby_label"
        current_window = getattr(self, window_attr, None)
        current_label = getattr(self, label_attr, None)
        if self._window_exists(current_window) and current_label is not None:
            return True

        if layer:
            self._destroy_hover_layer_window()
        else:
            win = self._hover_ruby_window
            self._hover_ruby_window = None
            self._hover_ruby_label = None
            self._safe_destroy(win)
        try:
            hover = tk.Toplevel(popup)
            hover.withdraw()
            hover.overrideredirect(True)
            hover.geometry("1x1+-32000+-32000")
            hover.attributes("-topmost", True)
            make_nonactivating_tool_window(hover)
            hover.configure(bg=self.bg_color)
            label = tk.Text(
                hover,
                bg=self.bg_color,
                fg=self.font_color,
                bd=0,
                padx=self._HOVER_TEXT_PAD_X // 2,
                pady=self._HOVER_TEXT_PAD_Y // 2,
                cursor="xterm",
                exportselection=True,
                highlightthickness=0,
                insertwidth=0,
                relief="flat",
                takefocus=True,
                wrap="char",
                width=1,
                height=1,
            )
            label.pack(fill="both", expand=True)
            hover.bind("<Enter>", self._keep_hover_visible, add="+")
            hover.bind("<Leave>", self._on_popup_leave, add="+")
            label.bind("<Enter>", self._keep_hover_visible, add="+")
            label.bind("<Leave>", self._on_popup_leave, add="+")
            label.bind("<ButtonPress-1>", self._keep_hover_visible, add="+")
            label.bind("<ButtonRelease-1>", self._on_popup_leave, add="+")
            label.bind("<Control-a>", lambda _event, widget=label: self._select_all_hover_text(widget))
            label.bind("<Control-A>", lambda _event, widget=label: self._select_all_hover_text(widget))
            label.bind("<Control-c>", lambda _event, widget=label: self._copy_hover_text_selection(hover, widget))
            label.bind("<Control-C>", lambda _event, widget=label: self._copy_hover_text_selection(hover, widget))
            label.bind("<Button-3>", lambda event, widget=label: self._show_hover_text_context_menu(event, hover, widget))
        except Exception:
            logger.debug("Failed to create hover popup", exc_info=True)
            setattr(self, window_attr, None)
            setattr(self, label_attr, None)
            return False

        setattr(self, window_attr, hover)
        setattr(self, label_attr, label)
        return True

    @staticmethod
    def _wrap_text_to_pixel_lines(text: str, font, max_width_px: int) -> list[str]:
        max_width = max(1, int(max_width_px))
        wrapped: list[str] = []
        for raw_line in str(text or "").splitlines() or [""]:
            if not raw_line:
                wrapped.append("")
                continue
            current = ""
            for ch in raw_line:
                candidate = current + ch
                try:
                    too_wide = bool(current and int(font.measure(candidate)) > max_width)
                except Exception:
                    too_wide = bool(current and len(candidate) > max_width)
                if too_wide:
                    wrapped.append(current)
                    current = ch
                else:
                    current = candidate
            wrapped.append(current)
        return wrapped or [""]

    def _set_hover_text_widget_text(self, widget: tk.Text, text: str, max_width_px: int | None = None) -> tuple[int, int]:
        max_width_px = int(max_width_px or self._HOVER_TEXT_MAX_WIDTH_PX)
        try:
            font_obj = tkFont.Font(font=widget.cget("font"))
        except Exception:
            font_obj = tkFont.Font(family=self.font_name, size=max(8, int(float(self.font_size) * 0.60)))

        wrapped_lines = self._wrap_text_to_pixel_lines(text, font_obj, max_width_px)
        try:
            text_width = max(int(font_obj.measure(line or " ")) for line in wrapped_lines)
        except Exception:
            text_width = max(len(line) for line in wrapped_lines) * 12
        try:
            line_height = int(font_obj.metrics("linespace") or 14)
        except Exception:
            line_height = 14

        width_px = max(self._HOVER_TEXT_MIN_WIDTH_PX, min(max_width_px, text_width) + self._HOVER_TEXT_PAD_X)
        height_px = max(line_height + self._HOVER_TEXT_PAD_Y, (line_height * max(1, len(wrapped_lines))) + self._HOVER_TEXT_PAD_Y)
        width_chars = max(1, max(len(line) for line in wrapped_lines))
        height_lines = max(1, len(wrapped_lines))

        try:
            widget.configure(state="normal", width=width_chars, height=height_lines, wrap="char")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="disabled")
        except Exception:
            logger.debug("Failed to update hover text widget", exc_info=True)
        return int(width_px), int(height_px)

    def _show_hover_text(
        self,
        region: HitRegion,
        text: str,
        *,
        font_scale: float = 0.60,
        bold: bool = True,
        ruby_position: str = "",
        layer_window: bool = False,
    ) -> None:
        popup = getattr(self, "_popup", None)
        entry = getattr(self, "_entry_widget", None)
        if not self._window_exists(popup) or entry is None:
            return

        label_text = str(text or "").strip()
        if not label_text:
            return

        if not self._ensure_hover_window(popup, layer=layer_window):
            return
        hover = self._hover_layer_window if layer_window else self._hover_ruby_window
        label = self._hover_layer_label if layer_window else self._hover_ruby_label
        if hover is None or label is None:
            return

        try:
            font_size = max(8, int(float(self.font_size) * float(font_scale)))
            font = (self.font_name, font_size, "bold" if bold else "normal")
            label.configure(font=font)
        except Exception:
            pass

        try:
            pointer_x = int(self.root.winfo_pointerx())
            pointer_y = int(self.root.winfo_pointery())
        except Exception:
            pointer_x = pointer_y = 0
        screen_rect = self._screen_rect_near_point(pointer_x, pointer_y, fallback_widget=popup)
        max_hover_width = max(
            160,
            min(self._HOVER_TEXT_MAX_WIDTH_PX, int(screen_rect[2] * 0.75)),
        )
        measured_w, measured_h = self._set_hover_text_widget_text(label, label_text, max_width_px=max_hover_width)
        try:
            normal_font = tkFont.Font(family=self.font_name, size=font_size, weight="normal")
            bold_font = tkFont.Font(family=self.font_name, size=font_size, weight="bold")
            label.tag_configure("hover_heading", font=bold_font)
            label.tag_configure("hover_ruby", font=bold_font)
            lines = label_text.splitlines() or [""]
            for line_no, line in enumerate(lines, start=1):
                for heading in ("Dictionary:", "Status:", "Translation:"):
                    if line.startswith(heading):
                        label.tag_add("hover_heading", f"{line_no}.0", f"{line_no}.{len(heading)}")
                        break
            if ruby_position == "first":
                label.tag_add("hover_ruby", "1.0", "1.end")
            elif ruby_position == "last":
                last = len(lines)
                label.tag_add("hover_ruby", f"{last}.0", f"{last}.end")
            if not bold:
                label.configure(font=normal_font)
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
            hover_w = max(int(measured_w or 1), int(hover.winfo_reqwidth() or 1))
            hover_h = max(int(measured_h or 1), int(hover.winfo_reqheight() or 1))
            popup_x = int(popup.winfo_rootx())
            popup_y = int(popup.winfo_rooty())
            popup_h = int(popup.winfo_height() or popup.winfo_reqheight() or 1)
        except Exception:
            hover_w = int(measured_w or 1)
            hover_h = int(measured_h or 1)
            popup_x = popup_y = 0
            popup_h = 1

        center_x = int(popup_x + base_x + (base_w / 2) - (hover_w / 2))

        above_y = popup_y + base_y - hover_h - 3
        below_y = popup_y + base_y + base_h + 3
        is_second_line = int(region.get("line") or 1) == 2
        if layer_window:
            desired_y = above_y if is_second_line else below_y
        else:
            desired_y = below_y if is_second_line else above_y

        if not pointer_x and not pointer_y:
            pointer_x, pointer_y = popup_x + (hover_w // 2), popup_y + (popup_h // 2)
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
        entry_text = self._hover_dictionary_text(region)
        if entry_text:
            self._show_hover_text(region, entry_text, font_scale=0.55, bold=True)

    def _hover_dictionary_text(self, region: HitRegion) -> str:
        lookup = getattr(self, "_dictionary_lookup", None)
        if not callable(lookup):
            return ""

        query = str(region.get("lookup") or region.get("base") or region.get("sentence_lookup") or "").strip()
        if not query:
            return ""

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

        return entry_text

    def _show_hover_translation(self, region: HitRegion) -> None:
        lookup = getattr(self, "_translation_lookup", None)
        if not callable(lookup):
            return

        query = str(region.get("base") or region.get("lookup") or "").strip()
        if not query:
            return
        provider = self._translation_provider()
        cache_key = (provider, query)
        self._hover_active_translation_key = cache_key

        cached = self._translation_hover_cache.get(cache_key)
        if cached is not None:
            if cached:
                self._show_hover_text(region, cached, font_scale=0.55, bold=True)
            return

        self._show_hover_text(region, "Translating...", font_scale=0.55, bold=True)
        if cache_key in self._translation_hover_pending:
            return
        self._translation_hover_pending.add(cache_key)

        def worker() -> None:
            try:
                entry_text = str(lookup(query, provider=provider) or "").strip()
            except TypeError:
                try:
                    entry_text = str(lookup(query) or "").strip()
                except Exception:
                    entry_text = ""
            except Exception:
                entry_text = ""

            def apply_result() -> None:
                self._translation_hover_pending.discard(cache_key)
                self._translation_hover_cache[cache_key] = entry_text
                if len(self._translation_hover_cache) > 256:
                    try:
                        oldest = next(iter(self._translation_hover_cache))
                        self._translation_hover_cache.pop(oldest, None)
                    except Exception:
                        pass
                if self._hover_active_mode != "translation":
                    return
                if self._hover_active_translation_key != cache_key:
                    return
                active_region = self._hover_active_region or region
                if entry_text:
                    self._show_hover_text(active_region, entry_text, font_scale=0.55, bold=True)
                else:
                    self._clear_hover_ruby()

            try:
                dispatch_to_tk(self.root, apply_result)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _show_hover_status(self, region: HitRegion) -> None:
        text = self._hover_status_text(region)
        if text:
            self._show_hover_text(region, text, font_scale=0.55, bold=True)

    def _hover_status_text(self, region: HitRegion) -> str:
        provider = getattr(self, "_annotation_provider", None)
        token = {
            "surface": str(region.get("base") or "").strip(),
            "lookup": str(region.get("lookup") or region.get("base") or "").strip(),
            "reading": str(
                region.get("lookup_reading")
                or region.get("reading")
                or region.get("ruby")
                or ""
            ).strip(),
        }
        parts = []
        if provider is not None:
            try:
                match = provider.match_token(token)
            except Exception:
                match = None
            if match is not None:
                status = str(getattr(match, "status", "") or "").replace("_", " ").strip()
                source = str(getattr(match, "source", "") or "").strip()
                if status:
                    parts.append(status if not source else f"{status} ({source})")
                try:
                    if bool(self.config.get("ANNOTATION_SHOW_MEANING_ON_HOVER") or False):
                        meaning = str(getattr(match, "meaning", "") or "").strip()
                        if meaning:
                            parts.append(meaning)
                except Exception:
                    pass
        text = "\n".join(part for part in parts if part)
        return text

    def _show_hover_layers(self, region: HitRegion, layers: tuple[str, ...]) -> None:
        parts: list[tuple[str, str]] = []
        if "dictionary" in layers:
            parts.append(("dictionary", self._hover_dictionary_text(region) or "No dictionary entry"))
        if "translation" in layers:
            query = str(region.get("base") or region.get("lookup") or "").strip()
            provider = self._translation_provider()
            cache_key = (provider, query)
            self._hover_active_translation_key = cache_key
            cached = self._translation_hover_cache.get(cache_key)
            if cached is not None:
                logger.debug(
                    "Popup hover translation cache hit provider=%s query=%r result_chars=%d",
                    provider,
                    query,
                    len(cached),
                )
            parts.append(("translation", "Translating..." if cached is None else (cached or "No translation")))
            if query and cached is None and cache_key not in self._translation_hover_pending:
                self._start_layer_translation(region, layers, cache_key, query, provider)
        if "status" in layers:
            parts.append(("status", self._hover_status_text(region) or "Not in database"))

        text = combine_hover_text(parts)
        if text:
            self._show_hover_text(
                region,
                text,
                font_scale=0.55,
                bold=False,
                layer_window=True,
            )

    def _start_layer_translation(
        self,
        region: HitRegion,
        layers: tuple[str, ...],
        cache_key: tuple[str, str],
        query: str,
        provider: str,
    ) -> None:
        lookup = getattr(self, "_translation_lookup", None)
        if not callable(lookup):
            logger.debug("Popup hover translation skipped: no lookup callback")
            return
        self._translation_hover_pending.add(cache_key)
        logger.debug("Popup hover translation requested provider=%s query=%r", provider, query)

        def worker() -> None:
            try:
                value = clean_hover_translation(query, str(lookup(query, provider=provider) or ""))
            except TypeError:
                try:
                    value = clean_hover_translation(query, str(lookup(query) or ""))
                except Exception:
                    value = ""
            except Exception:
                value = ""
            logger.debug(
                "Popup hover translation completed provider=%s query=%r result_chars=%d",
                provider,
                query,
                len(value),
            )

            def apply_result() -> None:
                self._translation_hover_pending.discard(cache_key)
                self._translation_hover_cache[cache_key] = value
                if not value:
                    self.root.after(
                        10000,
                        lambda key=cache_key: self._translation_hover_cache.pop(key, None)
                        if self._translation_hover_cache.get(key) == ""
                        else None,
                    )
                if len(self._translation_hover_cache) > 256:
                    self._translation_hover_cache.pop(next(iter(self._translation_hover_cache)), None)
                if self._hover_active_translation_key != cache_key:
                    return
                if not self._same_hover_region(region, self._hover_active_region):
                    return
                self._show_hover_layers(region, layers)

            dispatch_to_tk(self.root, apply_result)

        threading.Thread(target=worker, daemon=True).start()

    def _keep_hover_visible(self, _event=None):
        self._cancel_hover_clear()
        self._cancel_close()
        if getattr(_event, "widget", None) in {self._hover_layer_window, self._hover_layer_label}:
            self._cancel_hover_layer_job()
        return None

    def _cancel_hover_clear(self) -> None:
        job = getattr(self, "_hover_clear_job", None)
        if not job:
            return
        try:
            self.root.after_cancel(job)
        except Exception:
            pass
        self._hover_clear_job = None

    def _cancel_hover_layer_job(self) -> None:
        job, self._hover_layer_job = self._hover_layer_job, None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    def _hover_layer_delay_ms(self) -> int:
        try:
            value = self.config.get("HOVER_LAYER_DELAY_MS")
            if value is None or str(value).strip() == "":
                value = 500
            return max(0, min(10000, int(float(value))))
        except Exception:
            return 500

    def _schedule_hover_layers(
        self,
        region: HitRegion,
        layers: tuple[str, ...],
        mode: str,
    ) -> None:
        if not any(layer != "ruby" for layer in layers):
            return

        def show() -> None:
            self._hover_layer_job = None
            if self._hover_active_mode != mode or not self._same_hover_region(region, self._hover_active_region):
                return
            self._set_hover_highlight(region)
            self._show_hover_layers(region, layers)

        delay = self._hover_layer_delay_ms()
        if delay <= 0:
            show()
        else:
            self._hover_layer_job = self.root.after(delay, show)

    def _schedule_hover_clear(self, delay_ms: int | None = None, *, keep_if_inside_popup: bool = False) -> None:
        self._cancel_hover_clear()
        if self._pinned or self._menu_open or self._dragging:
            return
        if delay_ms is None:
            delay_ms = self._coerce_hover_clear_delay(getattr(self, "hover_clear_delay", 500))

        def _clear_if_not_reentered() -> None:
            self._hover_clear_job = None
            inside_hover = self._pointer_inside_window(getattr(self, "_hover_ruby_window", None))
            inside_hover = inside_hover or self._pointer_inside_window(getattr(self, "_hover_layer_window", None))
            inside_popup = self._pointer_inside_window(getattr(self, "_popup", None))
            if inside_hover or (keep_if_inside_popup and inside_popup):
                self._cancel_close()
                return
            self._clear_hover_ruby()
            if not inside_popup:
                self._restart_close()

        try:
            self._hover_clear_job = self.root.after(max(0, int(delay_ms)), _clear_if_not_reentered)
        except Exception:
            _clear_if_not_reentered()

    def refresh_hover_display(self) -> None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return
        try:
            x = int(entry.winfo_pointerx() - entry.winfo_rootx())
            y = int(entry.winfo_pointery() - entry.winfo_rooty())
        except Exception:
            return
        if self._hover_mode() != "translation":
            try:
                width = int(entry.winfo_width())
                height = int(entry.winfo_height())
                if x < 0 or y < 0 or x >= width or y >= height:
                    self._schedule_hover_clear(keep_if_inside_popup=True)
                    return
            except Exception:
                pass
        self._hover_active_region = None
        self._hover_active_mode = None
        self._on_popup_motion(type("_PopupMotion", (), {"x": x, "y": y})())

    def _region_at_pointer(
        self,
        entry: tk.Text,
        regions: list[HitRegion],
        x: int,
        y: int,
        *,
        anchor: HitRegion | None = None,
    ) -> HitRegion | None:
        candidates: list[HitRegion] = []
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
                candidates.append(region)
        return choose_hover_region(candidates, anchor)

    def _on_popup_motion(self, event) -> None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return
        if self._manual_selection_active:
            self._clear_hover_highlight()
            return

        x = int(event.x)
        y = int(event.y)
        mode = self._hover_mode(event)
        layers = hover_layers(self.config, mode, popup=True)
        uses_word_hit = any(layer != "ruby" for layer in layers)
        ruby_hit = self._region_at_pointer(entry, self._segment_hits, x, y)

        selected_region = self._selected_text_region()
        hit = None
        if uses_word_hit:
            if selected_region is not None:
                hit = selected_region
            else:
                hit = self._region_at_pointer(entry, self._word_hits, x, y, anchor=ruby_hit)
        if hit is None:
            hit = ruby_hit

        visible_ruby_hit = ruby_hit if "ruby" in layers else None
        if not self._same_hover_region(visible_ruby_hit, self._hover_active_ruby_region):
            self._hover_active_ruby_region = visible_ruby_hit
            if visible_ruby_hit is not None:
                self._show_hover_ruby(visible_ruby_hit)
            else:
                self._safe_withdraw(self._hover_ruby_window)

        if self._same_hover_region(hit, self._hover_active_region) and mode == self._hover_active_mode:
            self._cancel_hover_clear()
            return

        if hit is None:
            if self._hover_clear_job is None:
                self._schedule_hover_clear(keep_if_inside_popup=False)
            return

        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        if self._hover_active_region is not None or self._hover_active_mode is not None:
            self._hover_active_translation_key = None
            self._clear_hover_highlight()
            if not any(layer != "ruby" for layer in layers):
                self._safe_withdraw(self._hover_layer_window)
        self._hover_active_region = hit
        self._hover_active_mode = mode
        self._schedule_hover_layers(hit, layers, mode)

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
                item = dict(span)
                item.update(
                    {
                        "surface": surface,
                        "lookup": str(span.get("lookup") or surface).strip(),
                        "reading": str(span.get("reading") or "").strip(),
                        "start": start,
                        "end": end,
                    }
                )
                out.append(item)
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
                surface = str(token.get("surface") or line[start_col:end_col])
                self._word_hits.append(
                    {
                        "start": f"{line_no}.{start_col}",
                        "end": f"{line_no}.{end_col}",
                        "char_start": start_col,
                        "char_end": end_col,
                        "line": line_no,
                        "base": surface,
                        "lookup": str(token.get("lookup") or token.get("surface") or ""),
                        "orth_base": str(token.get("orth_base") or ""),
                        "sentence_lookup": self._plain_popup_text(),
                        # Ruby display must come from the same parsed segments as
                        # the subtitle window, not a whole-token dictionary reading.
                        "ruby": "",
                        "reading": str(token.get("reading") or ""),
                        "pos1": str(token.get("pos1") or ""),
                        "pos2": str(token.get("pos2") or ""),
                        "c_type": str(token.get("c_type") or ""),
                        "c_form": str(token.get("c_form") or ""),
                        "compound": bool(token.get("compound")),
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

        def _tag_options(style: dict) -> dict:
            options = {}
            if style.get("text_color"):
                options["foreground"] = str(style.get("text_color"))
            if style.get("background_color"):
                options["background"] = str(style.get("background_color"))
            if style.get("underline"):
                options["underline"] = True
            return options

        def _ensure_tag(status: str, style: dict) -> str:
            tag_name = f"annotation_{status}"
            if tag_name in configured_tags:
                return tag_name
            try:
                options = _tag_options(style)
                if options:
                    entry.tag_configure(tag_name, **options)
            except Exception:
                logger.debug("Failed to configure popup annotation tag", exc_info=True)
            configured_tags.add(tag_name)
            return tag_name

        if self._line_segments:
            for line_no, line_segments in enumerate(self._line_segments, start=1):
                try:
                    annotated = provider.annotate_segments(line_segments, self._word_tokenizer)
                except Exception:
                    logger.debug("Failed to annotate popup segments", exc_info=True)
                    annotated = []
                col = 0
                for segment in annotated or []:
                    try:
                        base = str(segment[0] or "")
                        meta = segment[2] if len(segment) > 2 and isinstance(segment[2], dict) else {}
                    except Exception:
                        continue
                    next_col = col + len(base)
                    if meta.get("annotation") and bool(meta.get("normal_style_visible", True)):
                        status = str(meta.get("status") or "")
                        style = meta.get("style") if isinstance(meta.get("style"), dict) else {}
                        if status and bool(style.get("enabled")):
                            tag_name = _ensure_tag(status, style)
                            try:
                                entry.tag_add(tag_name, f"{line_no}.{col}", f"{line_no}.{next_col}")
                            except Exception:
                                logger.debug("Failed to add popup annotation tag", exc_info=True)
                    col = next_col
            return

        for line_no, line in enumerate(lines, start=1):
            try:
                tokens = provider._tokens_for_line(line, self._word_tokenizer)
                intervals = provider._matched_intervals(tokens, line_text=line)
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
                tag_name = _ensure_tag(str(match.status), style)
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

                if ruby and base and self._popup_ruby_base(base):
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
                            "char_start": col,
                            "char_end": col + len(base),
                            "line": line_no,
                            "base": base,
                        }
                    )

                col += len(base)

    @staticmethod
    def _popup_ruby_base(text: str) -> bool:
        if re.search(r"[0-9\uff10-\uff19]+\u3064", str(text or "")):
            return True
        for ch in str(text or ""):
            code = ord(ch)
            if code == 0x3005:
                return True
            if 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF or ch == "々":
                return True
            if 0x30A0 <= code <= 0x30FF:
                return True
        return False

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

    @staticmethod
    def _copy_popup_line_segments(line_segments) -> list[list[TextSegment]]:
        copied: list[list[TextSegment]] = []
        for line in line_segments or []:
            copied_line: list[TextSegment] = []
            for segment in line or []:
                if isinstance(segment, dict):
                    base = str(segment.get("base") or "")
                    ruby = segment.get("ruby")
                else:
                    try:
                        base = str(segment[0] or "")
                    except Exception:
                        base = ""
                    try:
                        ruby = segment[1]
                    except Exception:
                        ruby = None
                if base:
                    copied_line.append((base, str(ruby) if ruby else None))
            if copied_line:
                copied.append(copied_line)
        return copied

    def _prepare_popup_content(self, subtitle_text: str | None, line_segments=None) -> str:
        self._raw_subtitle_text = subtitle_text or ""
        self._line_segments = self._copy_popup_line_segments(line_segments)
        if not self._line_segments:
            self._line_segments = self._parse_inline_ruby(self._raw_subtitle_text)
        return self._plain_popup_text()

    def _create_popup_window(self) -> tk.Toplevel:
        popup = tk.Toplevel(self.root)
        self._popup = popup
        self._reset_popup_state(clear_content=False)
        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
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

    def _hover_text_content(self, widget: tk.Text | None = None) -> str:
        widget = widget or getattr(self, "_hover_ruby_label", None)
        if widget is None:
            return ""
        try:
            return str(widget.get("1.0", "end-1c") or "")
        except Exception:
            return ""

    def _hover_text_selection(self, widget: tk.Text | None = None) -> str:
        widget = widget or getattr(self, "_hover_ruby_label", None)
        if widget is None:
            return ""
        try:
            return str(widget.get("sel.first", "sel.last") or "")
        except tk.TclError:
            return ""
        except Exception:
            return ""

    def _select_all_hover_text(self, widget: tk.Text | None = None):
        widget = widget or getattr(self, "_hover_ruby_label", None)
        if widget is None:
            return "break"
        try:
            self._cancel_close()
            widget.tag_remove("sel", "1.0", "end")
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "end-1c")
            widget.see("insert")
        except Exception:
            pass
        return "break"

    def _copy_hover_text_selection(self, owner: tk.Misc, widget: tk.Text | None = None):
        text = self._hover_text_selection(widget) or self._hover_text_content(widget)
        self._copy_to_clipboard(owner, text)
        return "break"

    def _show_hover_text_context_menu(self, event, owner: tk.Misc, widget: tk.Text) -> str:
        self._menu_open = True
        self._cancel_close()
        menu = tk.Menu(owner, tearoff=0)
        menu.add_command(label="Copy", command=lambda: self._copy_hover_text_selection(owner, widget))
        menu.add_command(label="Copy All", command=lambda: self._copy_to_clipboard(owner, self._hover_text_content(widget)))
        menu.add_command(label="Select All", command=lambda: self._select_all_hover_text(widget))
        try:
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

    def _copy_selection_to_clipboard(self, owner: tk.Misc) -> None:
        self._copy_to_clipboard(owner, self._current_or_last_selected_text())

    def _copy_all_to_clipboard(self, owner: tk.Misc) -> None:
        self._copy_to_clipboard(owner, self._raw_subtitle_text or "")

    def _add_selection_to_anki_from_popup(
        self,
        popup: tk.Toplevel,
        plain_text: str,
        selected_text: str | None = None,
        post_add_capture: bool = False,
    ) -> None:
        selected = self._clean_selected_text(
            selected_text if selected_text is not None else self._get_selected_text()
        )
        if not selected:
            logger.debug("Add Selection To Anki skipped because no text is selected")
            return
        if not callable(self._on_add_anki):
            logger.debug("Add Selection To Anki skipped because no callback is bound")
            return

        def _run() -> None:
            try:
                kwargs = {
                    "selected_text": selected,
                    "subtitle_text": plain_text or "",
                }
                if post_add_capture:
                    kwargs["post_add_capture"] = True
                self._on_add_anki(**kwargs)
            except Exception:
                logger.exception("Add Selection To Anki failed")

        try:
            popup.after(1, _run)
        except Exception:
            _run()

    def add_selected_to_anki_if_pointer_inside(self, post_add_capture: bool = False) -> bool:
        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return False
        selected = self._get_selected_text()
        if not selected:
            return False
        self._add_selection_to_anki_from_popup(
            popup,
            self._plain_popup_text(),
            selected_text=selected,
            post_add_capture=bool(post_add_capture),
        )
        return True

    def copy_selection_to_clipboard_if_pointer_inside(self) -> bool:
        for hover, widget in (
            (getattr(self, "_hover_ruby_window", None), getattr(self, "_hover_ruby_label", None)),
            (getattr(self, "_hover_layer_window", None), getattr(self, "_hover_layer_label", None)),
        ):
            if not (self._window_exists(hover) and self._pointer_inside_window(hover)):
                continue
            text = self._hover_text_selection(widget) or self._hover_text_content(widget)
            if not text:
                return False
            self._copy_to_clipboard(hover, text)
            return True

        popup = getattr(self, "_popup", None)
        if not self._window_exists(popup):
            return False
        text = self._current_or_last_selected_text()
        if not text:
            return False
        self._copy_to_clipboard(popup, text)
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
        ruby_hit = self._region_at_pointer(entry, self._segment_hits, int(x), int(y))
        region = self._region_at_pointer(
            entry,
            self._word_hits,
            int(x),
            int(y),
            anchor=ruby_hit,
        )
        if not region:
            try:
                entry.tag_remove("sel", "1.0", "end")
            except Exception:
                pass
            self._last_selected_text = ""
            return
        if self._selection_contains_region(entry, region):
            return
        try:
            entry.tag_remove("sel", "1.0", "end")
            entry.tag_add("sel", region["start"], region["end"])
            entry.mark_set("insert", region["end"])
            self._remember_popup_selection()
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
        entry.bind("<ButtonPress-1>", self._begin_popup_selection, add="+")
        entry.bind("<B1-Motion>", self._continue_popup_selection, add="+")
        entry.bind("<Button-3>", lambda event: self._show_context_menu(event, menu, entry))
        entry.bind("<Enter>", lambda _event: self._cancel_close())
        entry.bind("<Motion>", self._on_popup_motion)
        entry.bind("<Leave>", self._on_popup_leave)
        entry.bind("<ButtonRelease-1>", self._end_popup_selection, add="+")
        entry.bind("<KeyRelease>", self._remember_popup_selection, add="+")
        entry.bind("<Control-a>", lambda _event: self._select_all_text(entry))
        entry.bind("<Control-A>", lambda _event: self._select_all_text(entry))
        popup.bind("<Control-a>", lambda _event: self._select_all_text(entry))
        popup.bind("<Control-A>", lambda _event: self._select_all_text(entry))
        popup.bind("<ButtonPress-1>", lambda _event: self._focus_popup_for_hotkeys(), add="+")
        popup.bind("<Enter>", lambda _event: self._cancel_close())
        popup.bind("<Leave>", lambda _event: self._on_popup_leave())
        popup.bind("<Destroy>", lambda event, owner=popup: self._on_popup_destroy(owner, event), add="+")

    def _focus_popup_for_hotkeys(self) -> None:
        popup = getattr(self, "_popup", None)
        entry = getattr(self, "_entry_widget", None)
        if not self._window_exists(popup):
            return
        try:
            popup.lift()
            popup.focus_force()
        except Exception:
            pass
        if entry is not None:
            try:
                entry.focus_force()
            except Exception:
                pass

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

    def open_copy_popup(self, subtitle_text=None, line_segments=None) -> None:
        self._destroy_current_popup()

        popup = self._create_popup_window()
        plain_text = self._prepare_popup_content(subtitle_text, line_segments=line_segments)
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

    def bind_hover_mode(self, callback) -> None:
        self._hover_mode_callback = callback

    def bind_translation_state(self, callback) -> None:
        self._translation_state_callback = callback

    def bind_translation_provider(self, callback) -> None:
        self._translation_provider_callback = callback

    def bind_anchor_window(self, callback) -> None:
        self._anchor_window_callback = callback

    def _shift_hover_dictionary_enabled(self) -> bool:
        return bool(self.config.get("HOVER_DICTIONARY_ENABLED") or self.config.get("SHIFT_HOVER_KANJI_DICTIONARY") or False)

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

    def _event_has_modifier_state(self, event) -> bool:
        return event is not None and hasattr(event, "state")

    def _hover_mode_from_event(self, event) -> str:
        try:
            state = int(getattr(event, "state", 0) or 0)
        except Exception:
            return "ruby"
        if state & (0x0008 | 0x20000):
            return "translation"
        if state & 0x0004:
            return "status"
        if state & 0x0001 and self._shift_hover_dictionary_enabled():
            callback = getattr(self, "_shift_state_callback", None)
            if callable(callback) and not self._is_shift_held():
                return "ruby"
            return "dictionary"
        return "ruby"

    def _hover_mode(self, event=None) -> str:
        callback = getattr(self, "_hover_mode_callback", None)
        if callable(callback):
            try:
                mode = str(callback() or "").strip().lower()
                if mode in {"ruby", "dictionary", "status", "translation"}:
                    return mode
            except Exception:
                pass
        if self._event_has_modifier_state(event):
            return self._hover_mode_from_event(event)
        if self._is_translation_held():
            return "translation"
        if self._shift_hover_dictionary_enabled() and self._is_shift_held():
            return "dictionary"
        return "ruby"

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

        if (
            self._pointer_inside_window(target)
            or self._pointer_inside_window(getattr(self, "_hover_ruby_window", None))
            or self._pointer_inside_window(getattr(self, "_hover_layer_window", None))
        ):
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
        try:
            state = int(getattr(_event, "state", 0) or 0)
        except Exception:
            state = 0
        if state & 0x0700:
            return
        if self._pointer_inside_window(getattr(self, "_popup", None)) or self._pointer_inside_window(
            getattr(self, "_hover_ruby_window", None)
        ) or self._pointer_inside_window(getattr(self, "_hover_layer_window", None)):
            self._cancel_close()
            self._cancel_hover_clear()
            return
        self._schedule_hover_clear(keep_if_inside_popup=True)

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
