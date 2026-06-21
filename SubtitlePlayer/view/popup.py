"""
Copy popup window shown on right-click.

Displays subtitle text and provides a simple context menu for mouse-only copy.
"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import font as tkFont
from typing import Any

from utils import (get_monitor_rects,make_draggable,make_nonactivating_tool_window,show_window_no_activate)


class CopyPopup:
    _INLINE_RUBY_RE = re.compile(r"([^\[\]\n]+?)\[(.+?)\]")

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
        self._line_segments: list[list[tuple[str, str | None]]] = []
        self._segment_hits: list[dict[str, Any]] = []
        self._word_hits: list[dict[str, Any]] = []
        self._hover_active_region: dict[str, Any] | None = None
        self._hover_active_mode: str | None = None
        self._hover_ruby_window: tk.Toplevel | None = None
        self._hover_ruby_label: tk.Label | None = None
        self._word_tokenizer = None

        self.root.bind("<Destroy>", lambda _e: self._cancel_close())

        self.bg_color = self.config.get("POPUP_BG_COLOR")
        self.font_name = self.config.get("POPUP_FONT")
        self.font_color = self.config.get("POPUP_FONT_COLOR")
        self.font_size = self.config.get("POPUP_FONT_SIZE")
        self.close_delay = int(self.config.get("POPUP_CLOSE_TIMER") or 1000)

    def _parse_inline_ruby(self, subtitle_text: str | None) -> list[list[tuple[str, str | None]]]:
        """
        Parse a line like:
            文[もん]句[く]があるなら 盾[たて]つくか？
        into visible base text plus hidden ruby text.

        The ruby is attached to the trailing token immediately before the bracket,
        not to the whole preceding phrase.
        """
        def _split_trailing_token(text: str) -> tuple[str, str]:
            text = text or ""
            if not text:
                return "", ""
            m = re.match(r"^(.*?)(\S+)$", text, flags=re.DOTALL)
            if not m:
                return "", text
            return m.group(1), m.group(2)

        lines: list[list[tuple[str, str | None]]] = []
        for raw_line in (subtitle_text or "").splitlines():
            segments: list[tuple[str, str | None]] = []
            last = 0

            for match in self._INLINE_RUBY_RE.finditer(raw_line):
                start, end = match.span()

                if start > last:
                    segments.append((raw_line[last:start], None))

                base_and_prefix = match.group(1) or ""
                ruby = match.group(2) or ""
                prefix, base = _split_trailing_token(base_and_prefix)

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
        win = self._hover_ruby_window
        if win is not None:
            try:
                if win.winfo_exists():
                    win.withdraw()
            except Exception:
                pass

    def _destroy_hover_ruby_window(self) -> None:
        win = self._hover_ruby_window
        self._hover_active_region = None
        self._hover_active_mode = None
        self._hover_ruby_window = None
        self._hover_ruby_label = None
        if win is not None:
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass

    def _clear_hover_highlight(self) -> None:
        entry = getattr(self, "_entry_widget", None)
        if entry is None:
            return
        try:
            entry.tag_remove("hover_word", "1.0", "end")
        except Exception:
            pass

    def _set_hover_highlight(self, region: dict[str, Any] | None) -> None:
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

    def _same_hover_region(self, left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
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

    def _selected_text_region(self) -> dict[str, Any] | None:
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


    def _show_hover_text(self, region: dict[str, Any], text: str, *, font_scale: float = 0.60, bold: bool = True) -> None:
        popup = getattr(self, "_popup", None)
        entry = getattr(self, "_entry_widget", None)
        if popup is None or entry is None:
            return

        label_text = str(text or "").strip()
        if not label_text:
            return

        if self._hover_ruby_window is None or not self._hover_ruby_window.winfo_exists():
            hover = tk.Toplevel(popup)
            hover.withdraw()
            hover.overrideredirect(True)
            try:
                hover.geometry("1x1+-32000+-32000")
            except Exception:
                pass
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
            self._hover_ruby_window = hover
            self._hover_ruby_label = label

        try:
            font_size = max(8, int(float(self.font_size) * float(font_scale)))
            font = (self.font_name, font_size, "bold" if bold else "normal")
            self._hover_ruby_label.configure(text=label_text, font=font)
        except Exception:
            try:
                self._hover_ruby_label.configure(text=label_text)
            except Exception:
                pass

        try:
            entry.update_idletasks()
            popup.update_idletasks()
            self._hover_ruby_window.update_idletasks()
        except Exception:
            pass

        bbox = self._segment_bbox(entry, region["start"], region["end"])
        if not bbox:
            return

        base_x, base_y, base_w, base_h = bbox

        try:
            hover_w = int(self._hover_ruby_window.winfo_reqwidth() or 1)
            hover_h = int(self._hover_ruby_window.winfo_reqheight() or 1)
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

        monitor_rect = None
        try:
            rects = list(get_monitor_rects(self.root) or [])
            for rx, ry, rw, rh in rects:
                if rx <= pointer_x < rx + rw and ry <= pointer_y < ry + rh:
                    monitor_rect = (rx, ry, rw, rh)
                    break
            if monitor_rect is None and rects:
                monitor_rect = min(
                    rects,
                    key=lambda r: (
                        abs(pointer_x - (r[0] + (r[2] // 2)))
                        + abs(pointer_y - (r[1] + (r[3] // 2)))
                    ),
                )
        except Exception:
            monitor_rect = None

        if monitor_rect is not None:
            screen_x, screen_y, screen_w, screen_h = monitor_rect
        else:
            try:
                screen_x, screen_y = 0, 0
                screen_w = int(self.root.winfo_screenwidth() or 1920)
                screen_h = int(self.root.winfo_screenheight() or 1080)
            except Exception:
                screen_x, screen_y, screen_w, screen_h = 0, 0, 1920, 1080

        max_x = screen_x + max(0, screen_w - hover_w)
        max_y = screen_y + max(0, screen_h - hover_h)
        desired_x = max(screen_x, min(center_x, max_x))
        desired_y = max(screen_y, min(desired_y, max_y))

        try:
            self._hover_ruby_window.geometry(f"{hover_w}x{hover_h}+{desired_x}+{desired_y}")
            show_window_no_activate(self._hover_ruby_window)
        except Exception:
            pass

    def _show_hover_ruby(self, region: dict[str, Any]) -> None:
        ruby_text = str(region.get("ruby") or "")
        if ruby_text:
            self._show_hover_text(region, ruby_text, font_scale=0.60, bold=True)

    def _show_hover_dictionary(self, region: dict[str, Any]) -> None:
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

    def _show_hover_translation(self, region: dict[str, Any]) -> None:
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

    def _region_at_pointer(self, entry: tk.Text, regions: list[dict[str, Any]], x: int, y: int) -> dict[str, Any] | None:
        try:
            entry.update_idletasks()
        except Exception:
            pass
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

    def open_copy_popup(self, subtitle_text=None) -> None:
        if self._popup:
            self._cancel_close()
            self._destroy_hover_ruby_window()
            self._popup.destroy()
            self._popup = None

        popup = tk.Toplevel(self.root)
        self._popup = popup
        self._menu_open = False
        self._dragging = False
        self._entry_widget = None
        self._drag_grip = None
        self._hover_active_region = None
        self._hover_active_mode = None
        self._segment_hits = []
        self._word_hits = []

        self._raw_subtitle_text = subtitle_text or ""
        self._line_segments = self._parse_inline_ruby(self._raw_subtitle_text)
        plain_text = self._plain_popup_text()

        popup.withdraw()
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        make_nonactivating_tool_window(popup)

        font = tkFont.Font(family=self.font_name, size=self.font_size, weight="bold")
        ruby_font = tkFont.Font(
            family=self.font_name,
            size=max(8, int(float(self.font_size) * 0.60)),
            weight="bold",
        )

        lines = plain_text.splitlines()
        nonempty_lines = [line for line in lines if line.strip()]
        pixel_widths = [font.measure(line) for line in nonempty_lines]
        text_width = max(pixel_widths) if pixel_widths else font.measure((plain_text or "").strip() or " ")

        # Reserve width using ruby-group width as well, so ruby-heavy lines get more room.
        group_widths = [
            self._group_line_width(line_segments, font, ruby_font)
            for line_segments in self._line_segments
        ]
        group_width = max(group_widths) if group_widths else 0
        text_width = max(text_width, group_width)

        line_height = font.metrics("linespace")
        line_count = max(1, len(lines) if lines else 1)
        text_height = line_height * line_count
        pad_x, pad_y = 10, 5
        total_width = text_width + 2 * pad_x
        total_height = text_height + 2 * pad_y

        entry = tk.Text(
            popup,
            font=font,
            wrap="word",
            padx=8,
            pady=4,
            bg=self.bg_color,
            fg=self.font_color,
            cursor="xterm",
            height=line_count,
        )
        entry.insert("1.0", plain_text)
        entry.tag_configure("hover_word", background="#343434")
        self._entry_widget = entry

        self._rebuild_segment_hits()
        self._rebuild_word_hits(plain_text)

        entry.config(state="disabled")
        entry.pack(fill="both", expand=True)

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

        menu = tk.Menu(popup, tearoff=0)

        def _copy_selection():
            selected = self._get_selected_text()
            if not selected:
                return
            try:
                popup.clipboard_clear()
                popup.clipboard_append(selected)
            except Exception:
                pass

        def _add_selection_to_anki():
            selected = self._get_selected_text()
            if not selected:
                print("Add Selection To Anki: no text selected.")
                return
            if not callable(self._on_add_anki):
                print("Add Selection To Anki: callback not bound.")
                return

            def _run():
                try:
                    self._on_add_anki(selected_text=selected, subtitle_text=plain_text or "")
                except Exception as e:
                    print(f"Add Selection To Anki failed: {e}")

            try:
                popup.after(1, _run)
            except Exception:
                _run()

        def _copy_all():
            try:
                popup.clipboard_clear()
                popup.clipboard_append(self._raw_subtitle_text or "")
            except Exception:
                pass

        menu.add_command(label="Copy", command=_copy_selection)
        menu.add_command(label="Copy All", command=_copy_all)
        menu.add_command(label="Add Selection To Anki", command=_add_selection_to_anki)
        menu.add_separator()
        menu.add_command(label="Pin", command=lambda: self._pin(popup))

        def _select_word_at_pointer(event) -> None:
            region = self._region_at_pointer(entry, self._word_hits, int(event.x), int(event.y))
            if not region:
                try:
                    entry.tag_remove("sel", "1.0", "end")
                except Exception:
                    pass
                return
            try:
                sel_first = entry.index("sel.first")
                sel_last = entry.index("sel.last")
                if (
                    entry.compare(region["start"], ">=", sel_first)
                    and entry.compare(region["end"], "<=", sel_last)
                ):
                    return
            except Exception:
                pass
            try:
                entry.tag_remove("sel", "1.0", "end")
                entry.tag_add("sel", region["start"], region["end"])
                entry.mark_set("insert", region["end"])
            except Exception:
                pass

        def _show_menu(event):
            self._menu_open = True
            self._cancel_close()
            try:
                self._clear_hover_ruby()
                _select_word_at_pointer(event)
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

        entry.bind("<Button-3>", _show_menu)
        entry.bind("<Motion>", self._on_popup_motion)
        entry.bind("<Leave>", self._on_popup_leave)

        try:
            pointer_x = int(self.root.winfo_pointerx())
            pointer_y = int(self.root.winfo_pointery())
        except Exception:
            pointer_x, pointer_y = 0, 0

        monitor_rect = None
        try:
            rects = list(get_monitor_rects(self.root) or [])
            for rx, ry, rw, rh in rects:
                if rx <= pointer_x < rx + rw and ry <= pointer_y < ry + rh:
                    monitor_rect = (rx, ry, rw, rh)
                    break
            if monitor_rect is None and rects:
                monitor_rect = min(
                    rects,
                    key=lambda r: (
                        abs(pointer_x - (r[0] + (r[2] // 2)))
                        + abs(pointer_y - (r[1] + (r[3] // 2)))
                    ),
                )
        except Exception:
            monitor_rect = None

        if monitor_rect is not None:
            screen_x, screen_y, screen_w, screen_h = monitor_rect
        else:
            try:
                screen_x, screen_y = 0, 0
                screen_w = int(popup.winfo_screenwidth() or 1920)
                screen_h = int(popup.winfo_screenheight() or 1080)
            except Exception:
                screen_x, screen_y, screen_w, screen_h = 0, 0, 1920, 1080

        max_w = max(240, int(screen_w * 0.90))
        max_h = max(120, int(screen_h * 0.60))
        total_width = min(max_w, int(total_width))
        total_height = min(max_h, int(total_height))

        anchor_rect = self._anchor_window_rect()
        if anchor_rect is not None:
            anchor_x, anchor_y, anchor_w, _anchor_h = anchor_rect
            x = int(anchor_x + (anchor_w // 2) - (total_width // 2))
            y = int(anchor_y - total_height)
        else:
            x = int(pointer_x - (total_width // 2))
            y = int(pointer_y - total_height - 16)
        max_x = screen_x + max(0, screen_w - total_width)
        max_y = screen_y + max(0, screen_h - total_height)
        x = max(screen_x, min(x, max_x))
        y = max(screen_y, min(y, max_y))
        self._apply_popup_line_margins(entry, lines, group_widths, total_width, font)
        popup.geometry(f"{total_width}x{total_height}+{x}+{y}")
        show_window_no_activate(popup)

        self._pinned = False
        popup.bind("<Enter>", lambda _e: self._cancel_close())
        popup.bind("<Leave>", lambda _e: self._on_popup_leave())
        popup.bind("<Destroy>", lambda _e: self._on_popup_destroy())
        self._restart_close()

    def bind_add_to_anki(self, callback) -> None:
        self._on_add_anki = callback

    def bind_dictionary_lookup(self, callback) -> None:
        self._dictionary_lookup = callback

    def bind_translation_lookup(self, callback) -> None:
        self._translation_lookup = callback

    def bind_word_tokenizer(self, callback) -> None:
        self._word_tokenizer = callback

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
            if win is None or not win.winfo_exists():
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
        popup = getattr(self, "_popup", None)
        if popup is None:
            return False
        try:
            return bool(popup.winfo_exists())
        except Exception:
            return False

    def ensure_on_top(self) -> None:
        """
        Keep popup above the other always-on-top windows in this app.

        Note: other windows (overlay/control) also set -topmost, so z-order depends on who is lifted last.
        """
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        try:
            show_window_no_activate(popup)
        except Exception:
            pass

    def set_busy_cursor(self, cursor: str) -> None:
        popup = getattr(self, "_popup", None)
        if not popup:
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

    def mark_anki_success(self, duration_ms: int = 1200) -> None:
        popup = getattr(self, "_popup", None)
        if not popup:
            return
        try:
            if not popup.winfo_exists():
                return
        except Exception:
            return

        success_bg = "#168a3a"
        self._cancel_close()
        try:
            popup.configure(bg=success_bg, cursor="")
        except Exception:
            pass
        entry = getattr(self, "_entry_widget", None)
        if entry is not None:
            try:
                entry.configure(bg=success_bg, cursor="xterm")
            except Exception:
                pass
        grip = getattr(self, "_drag_grip", None)
        if grip is not None:
            try:
                grip.configure(bg=success_bg)
            except Exception:
                pass
        try:
            self._close_job = popup.after(max(300, int(duration_ms)), self._close)
        except Exception:
            self._restart_close()

    def _close(self) -> None:
        self._destroy_hover_ruby_window()
        if self._popup:
            self._popup.destroy()
        self._popup = None
        self._entry_widget = None
        self._drag_grip = None
        self._close_job = None
        self._segment_hits = []
        self._word_hits = []
        self._raw_subtitle_text = ""
        self._line_segments = []

    def _cancel_close(self) -> None:
        if self._popup and self._close_job:
            self._popup.after_cancel(self._close_job)
        self._close_job = None

    def _restart_close(self) -> None:
        self._cancel_close()
        if self._popup:
            self._close_job = self._popup.after(self.close_delay, self._close)

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
        self._cancel_close()
        self._pinned = True
        popup.overrideredirect(False)
        popup.lift()

    def _on_popup_destroy(self) -> None:
        self._destroy_hover_ruby_window()
        self._popup = None
        self._entry_widget = None
        self._drag_grip = None
        self._dragging = False
        self._close_job = None
        self._segment_hits = []
        self._word_hits = []
        self._hover_active_region = None
        self._hover_active_mode = None
        self._raw_subtitle_text = ""
        self._line_segments = []


    def _group_line_width(
        self,
        line_segments: list[tuple[str, str | None]],
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
