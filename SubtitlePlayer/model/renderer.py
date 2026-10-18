

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
import threading
import time
import tkinter as tk
from collections import OrderedDict
from tkinter import font as tkFont
from typing import Any, Callable, Dict, List, Optional, Tuple

from model.config_manager import ConfigManager
from model.cue_snapshot import CueSnapshot
from model.hover_layers import (
    choose_hover_region,
    clean_hover_translation,
    combine_hover_text,
    hover_layers,
)
from view.subtitle_overlay import SubtitleOverlayUI
from utils import dispatch_to_tk, get_monitor_rects, make_nonactivating_tool_window, show_window_no_activate

logger = logging.getLogger(__name__)

Segment = Tuple[str, Optional[str]]
FrozenSegments = Tuple[Segment, ...]
WrappedLines = Tuple[Tuple[Segment, ...], ...]


class SubtitleRenderer:
    _HOVER_TEXT_MAX_WIDTH_PX = 1000
    _HOVER_TEXT_PAD_X = 36
    _HOVER_TEXT_PAD_Y = 8
    _HOVER_TEXT_HEADINGS = ("Dictionary:", "Status:", "Translation:")

    def __init__(self, canvas: tk.Canvas, config: ConfigManager) -> None:
        self.config = config
        self.canvas = canvas

        self._hover_regions: List[dict] = []
        self._word_regions: List[dict] = []
        self._hover_active_region = None
        self._hover_active_mode: str | None = None
        self._hover_active_translation_key: tuple[str, str] | None = None
        self._translation_hover_cache: dict[tuple[str, str], str] = {}
        self._translation_hover_pending: set[tuple[str, str]] = set()
        self._voice_translation_each_generation = 0
        # Hover callbacks run later on Tk's event queue.  Keep a cue generation
        # as well as a local interaction generation so an old leave/delay cannot
        # clear or redraw the tooltip for a newer cue/word.
        self._hover_cue_generation = 0
        self._hover_generation = 0
        self._hover_suspended = False
        self._hover_requires_fresh_motion = False
        self._hover_drag_overlay = None
        self._hover_layer_job = None
        self._hover_clear_job = None
        self._hover_status_highlights: set[tuple[float, float, float, float]] = set()
        self._status_selected_regions: list[dict] = []
        self._voice_selected_word_keys: list[tuple] = []
        self._voice_action_indicator_job = None
        self._voice_action_indicator_hide_job = None
        self._voice_action_indicator_generation = 0
        self._voice_action_indicator_phase = 0
        self._voice_action_indicator_state = "idle"
        self._hover_click_bind_id = None
        self._hover_text_window: tk.Toplevel | None = None
        self._hover_text_label: tk.Text | None = None
        self._hover_text_current: str = ""
        self._hover_text_size: tuple[int, int] = (1, 1)
        self._hover_copy_values: dict[str, str] = {}
        self._dictionary_lookup: Callable[[str], str] | None = None
        self._translation_lookup: Callable[..., str] | None = None
        self._translation_provider_callback: Callable[[], str] | None = None
        self._word_tokenizer: Callable[[str], List[dict[str, Any]]] | None = None
        self._shift_state_callback: Callable[[], bool] | None = None
        self._hover_mode_callback: Callable[[], str] | None = None
        self._annotation_provider = None
        self._cue_snapshot: CueSnapshot | None = None

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
        self._annotation_segment_cache: OrderedDict[Tuple, object] = OrderedDict()
        self._annotation_segment_cache_limit = 2048
        self._preview_render_cache: OrderedDict[Tuple, str] = OrderedDict()
        self._preview_render_cache_limit = 6
        self._preview_render_tag_counter = 0
        self._active_preview_render_tag: str | None = None
        self._current_render_tags: Tuple[str, ...] = ()
        self._last_overlay_width: Optional[int] = None
        self._layout_cache_hits = 0
        self._layout_cache_misses = 0
        self._annotation_segment_cache_hits = 0
        self._annotation_segment_cache_misses = 0
        self._preview_render_cache_hits = 0
        self._preview_render_cache_misses = 0

        self._timing_enabled = bool(config.get("DEBUGGING") or False)
        self._timing_data = {
            "render_subtitle_time": 0.0,
            "font_creation_time": 0.0,
            "wrap_segments_time": 0.0,
            "split_text_to_fit_time": 0.0,
            "font_measure_time": 0.0,
            "draw_outlined_text_time": 0.0,
            "canvas_delete_time": 0.0,
            "draw_outlined_text_count": 0,
            "canvas_text_item_count": 0,
            "preview_cache_show_time": 0.0,
            "render_count": 0,
        }

    def render_subtitle(
        self,
        top_segments,
        bottom_segments,
        overlay: SubtitleOverlayUI,
        preview: bool = False,
        cue_snapshot: CueSnapshot | None = None,
    ) -> None:
        start = time.perf_counter()
        preview = bool(preview)
        if self._timing_enabled:
            self._timing_data["render_count"] += 1

        try:
            self._bind_overlay_drag_callbacks(overlay)
            # A render replaces hit regions.  Any queued hover work for the old
            # regions must not be allowed to act on the newly rendered canvas.
            self._hover_cue_generation += 1
            self._clear_hover_ruby()
            self._status_selected_regions.clear()
            self._refresh_fonts_if_needed()

            self._hover_regions = []
            self._word_regions = []
            self._hover_active_region = None
            self._hover_active_mode = None
            self._cue_snapshot = cue_snapshot

            base_height = int(self.ruby_height * 2 + self.line_height * 2)
            y_ruby_top = self.ruby_height // 2
            y_base1 = self.ruby_height + self.line_height // 2

            wrap_limit_px = self._get_wrap_limit_px()

            if int(overlay.max_w) != int(self._last_overlay_width or 0):
                self._layout_cache.clear()
                self._last_overlay_width = int(overlay.max_w)

            if cue_snapshot is not None:
                render_top_segments = cue_snapshot.renderer_segments("first", annotated=True)
                render_bottom_segments = cue_snapshot.renderer_segments("second", annotated=True)
            else:
                render_top_segments = self._prepare_annotation_segments(top_segments)
                render_bottom_segments = self._prepare_annotation_segments(bottom_segments)

            layout_cache_key = (
                self._freeze_segments(render_top_segments),
                self._freeze_segments(render_bottom_segments),
                int(overlay.max_w),
                self._font_settings,
                int(wrap_limit_px) if wrap_limit_px else -1,
                int(getattr(getattr(self, "_annotation_provider", None), "version", 0) or 0),
            )

            cached_layout = self._layout_cache.get(layout_cache_key)
            if cached_layout is not None:
                self._layout_cache_hits += 1
                wrapped_top = cached_layout["wrapped_top"]
                wrapped_bottom = cached_layout["wrapped_bottom"]
            else:
                self._layout_cache_misses += 1
                wrapped_top = self._wrap_segments(
                    render_top_segments, overlay.max_w, line_limit_px=wrap_limit_px
                ) if render_top_segments else ()
                wrapped_bottom = self._wrap_segments(
                    render_bottom_segments, overlay.max_w, line_limit_px=wrap_limit_px
                ) if render_bottom_segments else ()
                self._layout_cache[layout_cache_key] = {
                    "wrapped_top": wrapped_top,
                    "wrapped_bottom": wrapped_bottom,
                }

            lines = wrapped_top + wrapped_bottom

            if not lines:
                if preview:
                    self._hide_active_preview_render()
                    if not self._preview_render_cache:
                        self._delete_all_canvas_items()
                else:
                    self._delete_all_canvas_items()
                    self._clear_preview_render_cache(delete_items=False)
                    self._voice_selected_word_keys.clear()
                self._finish_hover_bindings(preview=preview)
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

            preview_cache_key = None
            preview_tag = None
            if preview:
                preview_cache_key = (layout_cache_key, int(overlay.max_h))
                if self._show_cached_preview_render(preview_cache_key):
                    self._finish_hover_bindings(preview=True)
                    return
                preview_tag = self._begin_preview_render(preview_cache_key)
            else:
                self._delete_all_canvas_items()
                self._clear_preview_render_cache(delete_items=False)

            try:
                self._render_subtitle_lines(lines, y_ruby_top, y_base1, overlay, preview=preview)
            finally:
                self._current_render_tags = ()
            if not preview:
                self._restore_voice_word_highlights()
            if preview and preview_cache_key is not None and preview_tag:
                self._store_preview_render(preview_cache_key, preview_tag)
            self._finish_hover_bindings(preview=preview)

        finally:
            if self._timing_enabled:
                self._timing_data["render_subtitle_time"] += time.perf_counter() - start

    def _freeze_segments(self, segments) -> FrozenSegments:
        if not segments:
            return ()
        frozen = []
        for segment in segments:
            base = self._segment_base(segment)
            ruby = self._segment_ruby(segment)
            meta = self._segment_meta(segment)
            if meta:
                style = meta.get("style") if isinstance(meta.get("style"), dict) else {}
                frozen.append(
                    (
                        base,
                        ruby,
                        str(meta.get("status") or ""),
                        bool(meta.get("hide_ruby")),
                        bool(meta.get("normal_style_visible", True)),
                        str(style.get("text_color") or ""),
                        str(style.get("background_color") or ""),
                        bool(style.get("underline")),
                        bool(style.get("overline")),
                        bool(style.get("outline")),
                        str(meta.get("ruby_group_id") or ""),
                        str(meta.get("ruby_group_base") or ""),
                        str(meta.get("ruby_group_ruby") or ""),
                        int(meta.get("_cue_line_index", -1)),
                        int(meta.get("_cue_start", -1)),
                        int(meta.get("_cue_end", -1)),
                    )
                )
            else:
                frozen.append((base, ruby))
        return tuple(frozen)

    def _prepare_annotation_segments(self, segments):
        provider = getattr(self, "_annotation_provider", None)
        if not segments or provider is None:
            return segments
        try:
            enabled = provider.enabled()
        except Exception:
            enabled = False
        if not enabled:
            return segments
        try:
            version = int(getattr(provider, "version", 0) or 0)
        except Exception:
            version = 0
        try:
            cache_key = (version, self._freeze_segments(segments))
        except Exception:
            cache_key = None
        if cache_key is not None:
            cached = self._annotation_segment_cache.get(cache_key)
            if cached is not None:
                self._annotation_segment_cache_hits += 1
                self._annotation_segment_cache.move_to_end(cache_key)
                return cached
            self._annotation_segment_cache_misses += 1
        try:
            annotated = provider.annotate_segments(segments, self._word_tokenizer)
            if cache_key is not None:
                self._annotation_segment_cache[cache_key] = annotated
                self._annotation_segment_cache.move_to_end(cache_key)
                while len(self._annotation_segment_cache) > self._annotation_segment_cache_limit:
                    self._annotation_segment_cache.popitem(last=False)
            return annotated
        except Exception:
            logger.debug("Failed to prepare annotation segments", exc_info=True)
            return segments

    def _delete_all_canvas_items(self) -> None:
        if not self._timing_enabled:
            self.canvas.delete("all")
            return
        start = time.perf_counter()
        try:
            self.canvas.delete("all")
        finally:
            self._timing_data["canvas_delete_time"] += time.perf_counter() - start

    def _merge_render_tags(self, tags=()) -> Tuple[str, ...]:
        current = tuple(getattr(self, "_current_render_tags", ()) or ())
        if not current:
            return tuple(tags or ())
        merged = list(current)
        for tag in tuple(tags or ()):
            if tag not in merged:
                merged.append(tag)
        return tuple(merged)

    def _hide_active_preview_render(self) -> None:
        tag = getattr(self, "_active_preview_render_tag", None)
        if not tag:
            return
        try:
            self.canvas.itemconfigure(tag, state="hidden")
        except Exception:
            pass
        self._active_preview_render_tag = None

    def _clear_preview_render_cache(self, *, delete_items: bool = True) -> None:
        if delete_items:
            for tag in list(self._preview_render_cache.values()):
                try:
                    self.canvas.delete(tag)
                except Exception:
                    pass
        self._preview_render_cache.clear()
        self._active_preview_render_tag = None
        self._current_render_tags = ()

    def _show_cached_preview_render(self, cache_key: Tuple) -> bool:
        tag = self._preview_render_cache.get(cache_key)
        if not tag:
            self._preview_render_cache_misses += 1
            return False

        start = time.perf_counter() if self._timing_enabled else 0.0
        try:
            active = getattr(self, "_active_preview_render_tag", None)
            if active and active != tag:
                self.canvas.itemconfigure(active, state="hidden")
            self.canvas.itemconfigure(tag, state="normal")
            self._active_preview_render_tag = tag
            self._preview_render_cache.move_to_end(cache_key)
            self._preview_render_cache_hits += 1
            return True
        except Exception:
            try:
                self.canvas.delete(tag)
            except Exception:
                pass
            self._preview_render_cache.pop(cache_key, None)
            self._active_preview_render_tag = None
            return False
        finally:
            if self._timing_enabled:
                self._timing_data["preview_cache_show_time"] += time.perf_counter() - start

    def _begin_preview_render(self, cache_key: Tuple) -> str:
        if not self._preview_render_cache:
            self._delete_all_canvas_items()
        else:
            self._hide_active_preview_render()
        self._preview_render_tag_counter += 1
        tag = f"preview_subtitle_{self._preview_render_tag_counter}"
        self._current_render_tags = (tag,)
        self._active_preview_render_tag = tag
        return tag

    def _store_preview_render(self, cache_key: Tuple, tag: str) -> None:
        self._preview_render_cache[cache_key] = tag
        self._preview_render_cache.move_to_end(cache_key)
        while len(self._preview_render_cache) > self._preview_render_cache_limit:
            _old_key, old_tag = self._preview_render_cache.popitem(last=False)
            if old_tag == self._active_preview_render_tag:
                self._preview_render_cache[_old_key] = old_tag
                self._preview_render_cache.move_to_end(_old_key)
                break
            try:
                self.canvas.delete(old_tag)
            except Exception:
                pass

    @staticmethod
    def _segment_base(segment) -> str:
        if isinstance(segment, dict):
            return str(segment.get("base") or "")
        try:
            return str(segment[0] or "")
        except Exception:
            return ""

    @staticmethod
    def _segment_ruby(segment) -> str | None:
        if isinstance(segment, dict):
            value = segment.get("ruby")
        else:
            try:
                value = segment[1]
            except Exception:
                value = None
        return str(value) if value else None

    @staticmethod
    def _segment_meta(segment) -> dict:
        if isinstance(segment, dict):
            value = segment.get("meta") or {}
        else:
            try:
                value = segment[2] if len(segment) > 2 else {}
            except Exception:
                value = {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _make_segment(base: str, ruby: str | None, meta: dict | None = None):
        if meta:
            return (base, ruby, meta)
        return (base, ruby)

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

    def _render_subtitle_lines(self, lines, y_ruby_top, y_base1, overlay: SubtitleOverlayUI, preview: bool = False) -> None:
        block_h = self.line_height + self.ruby_height
        base2_start = block_h
        y_base2 = base2_start + self.line_height // 2
        y_ruby_bot = base2_start + self.line_height + self.ruby_height // 2

        if len(lines) == 1:
            self._render_line(lines[0], y_ruby_bot, y_base2, overlay.max_w, preview=preview, line_position="second")
            return

        if len(lines) == 2:
            self._render_line(lines[0], y_ruby_top, y_base1, overlay.max_w, preview=preview, line_position="first")
            self._render_line(lines[1], y_ruby_bot, y_base2, overlay.max_w, preview=preview, line_position="second")
            return

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
            position = "first" if i < max(1, len(lines) // 2) else "second"
            self._render_line(segs, ruby_y, base_y, overlay.max_w, preview=preview, line_position=position)

    def _render_line(self, segments, ruby_y, base_y, max_width, preview: bool = False, line_position: str = "second"):
        if not segments:
            return

        seg_meta = []
        total_w = 0
        collect_word_regions = not preview
        line_text = "".join(self._segment_base(segment) for segment in segments) if collect_word_regions else ""
        line_region_meta = []
        line_col = 0
        segment_col = 0

        for segment in segments:
            base = self._segment_base(segment)
            ruby = self._segment_ruby(segment)
            meta = self._segment_meta(segment)
            ruby_group_id = str(meta.get("ruby_group_id") or "")
            ruby_group_ruby = str(meta.get("ruby_group_ruby") or "")
            visible_ruby = None if ruby_group_id or meta.get("hide_ruby") else ruby
            hover_ruby = str(meta.get("hidden_ruby") or ruby or "")
            base_w = self._measure_text(self.font, base)
            if visible_ruby:
                ruby_w = self._measure_text(self.ruby_font, visible_ruby)
                seg_w = max(base_w, ruby_w)
            else:
                ruby_w = 0
                seg_w = base_w
            hover_ruby_w = self._measure_text(self.ruby_font, hover_ruby) if hover_ruby and not preview else 0
            seg_meta.append(
                {
                    "base": base,
                    "ruby": visible_ruby,
                    "base_w": base_w,
                    "ruby_w": ruby_w,
                    "seg_w": seg_w,
                    "meta": meta,
                    "hover_ruby": hover_ruby,
                    "hover_ruby_w": hover_ruby_w,
                    "ruby_group_id": ruby_group_id,
                    "ruby_group_ruby": ruby_group_ruby,
                    "text_start": segment_col,
                    "text_end": segment_col + len(base),
                    "pre_pad": 0.0,
                    "post_pad": 0.0,
                }
            )
            segment_col += len(base)
            total_w += seg_w

        idx = 0
        while idx < len(seg_meta):
            ruby_group_id = seg_meta[idx].get("ruby_group_id") or ""
            if not ruby_group_id:
                idx += 1
                continue
            start_idx = idx
            while idx < len(seg_meta) and seg_meta[idx].get("ruby_group_id") == ruby_group_id:
                idx += 1
            group_items = seg_meta[start_idx:idx]
            group_ruby = str(group_items[0].get("ruby_group_ruby") or "")
            if not group_ruby:
                continue
            group_base = "".join(str(item.get("base") or "") for item in group_items)
            group_base_w = sum(int(item.get("base_w") or 0) for item in group_items)
            group_ruby_w = self._measure_text(self.ruby_font, group_ruby)
            extra_w = max(0, group_ruby_w - group_base_w)
            if extra_w:
                left_pad = extra_w / 2
                right_pad = extra_w - left_pad
                group_items[0]["pre_pad"] = float(group_items[0].get("pre_pad") or 0.0) + left_pad
                group_items[-1]["post_pad"] = float(group_items[-1].get("post_pad") or 0.0) + right_pad
                total_w += extra_w
            group_items[0]["ruby_group_draw"] = True
            group_slot_w = group_base_w + extra_w
            group_text_start = int(group_items[0].get("text_start") or 0)
            group_text_end = int(group_items[-1].get("text_end") or group_text_start)
            for group_item in group_items:
                group_item["ruby_group_base"] = group_base
                group_item["ruby_group_base_w"] = group_base_w
                group_item["ruby_group_ruby_w"] = group_ruby_w
                group_item["ruby_group_slot_w"] = group_slot_w
                group_item["ruby_group_text_start"] = group_text_start
                group_item["ruby_group_text_end"] = group_text_end

        cur_x = (max_width - total_w) / 2
        pending_group_hover_regions = []
        ruby_group_positions = {}

        for item in seg_meta:
            base = str(item.get("base") or "")
            ruby = item.get("ruby")
            base_w = int(item.get("base_w") or 0)
            ruby_w = int(item.get("ruby_w") or 0)
            seg_w = int(item.get("seg_w") or 0)
            meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
            text_start = int(item.get("text_start") or 0)
            text_end = int(item.get("text_end") or text_start)
            hover_ruby = str(item.get("hover_ruby") or "")
            hover_ruby_w = int(item.get("hover_ruby_w") or 0)
            slot_left = cur_x
            cur_x += float(item.get("pre_pad") or 0.0)
            seg_left = cur_x
            seg_right = seg_left + seg_w
            cx = seg_left + seg_w / 2
            base_left = cx - (base_w / 2)
            if collect_word_regions:
                line_region_meta.append(
                    {
                        "start": line_col,
                        "end": line_col + len(base or ""),
                        "base": base or "",
                        "base_left": base_left,
                        "cue_line_index": meta.get("_cue_line_index"),
                        "cue_start": meta.get("_cue_start"),
                        "cue_end": meta.get("_cue_end"),
                    }
                )
                line_col += len(base or "")

            if item.get("ruby_group_draw"):
                group_ruby = str(item.get("ruby_group_ruby") or "")
                group_base = str(item.get("ruby_group_base") or "")
                group_base_w = int(item.get("ruby_group_base_w") or 0)
                group_ruby_w = int(item.get("ruby_group_ruby_w") or 0)
                group_slot_w = float(item.get("ruby_group_slot_w") or group_base_w)
                group_cx = slot_left + group_slot_w / 2
                ruby_group_positions[str(item.get("ruby_group_id") or "")] = {
                    "ruby": group_ruby,
                    "base_w": group_base_w,
                    "ruby_w": group_ruby_w,
                    "cx": group_cx,
                    "ruby_y": ruby_y,
                    "base": group_base,
                    "lookup": group_base,
                }
                if group_ruby and not self.hover_ruby_enabled:
                    self._draw_ruby_text(group_ruby, group_base_w, group_ruby_w, group_cx, ruby_y)
                if (
                    group_ruby
                    and not preview
                    and self._has_hoverable_ruby_base(group_base)
                ):
                    pending_group_hover_regions.append(
                        {
                            "bbox": (
                                slot_left,
                                base_y - (self.line_height / 2),
                                slot_left + group_slot_w,
                                base_y + (self.line_height / 2),
                            ),
                            "ruby": group_ruby,
                            "base_w": group_base_w,
                            "ruby_w": group_ruby_w,
                            "cx": group_cx,
                            "ruby_y": ruby_y,
                            "base": group_base,
                            "lookup": group_base,
                            "char_start": int(item.get("ruby_group_text_start") or text_start),
                            "char_end": int(item.get("ruby_group_text_end") or text_end),
                            "line_position": line_position,
                        }
                    )

            annotation_region_added = False
            if meta.get("annotation") and not preview:
                annotation_ruby = hover_ruby
                annotation_base_w = base_w
                annotation_ruby_w = hover_ruby_w
                annotation_cx = cx
                show_ruby_on_hover = (
                    bool(self.hover_ruby_enabled)
                    or bool(meta.get("show_ruby_on_hover", True))
                    or not bool(meta.get("hide_ruby"))
                )
                group_position = ruby_group_positions.get(str(item.get("ruby_group_id") or ""))
                if group_position and group_position.get("ruby"):
                    annotation_ruby = str(group_position.get("ruby") or "")
                    annotation_base_w = int(group_position.get("base_w") or base_w)
                    annotation_ruby_w = int(group_position.get("ruby_w") or hover_ruby_w)
                    annotation_cx = float(group_position.get("cx") or cx)
                self._hover_regions.append(
                    {
                        "bbox": (
                            seg_left,
                            base_y - (self.line_height / 2),
                            seg_right,
                            base_y + (self.line_height / 2),
                        ),
                        "ruby": annotation_ruby,
                        "base_w": annotation_base_w,
                        "ruby_w": annotation_ruby_w,
                        "cx": annotation_cx,
                        "ruby_y": ruby_y,
                        "base": base,
                        "lookup": str(meta.get("lookup") or base),
                        "char_start": text_start,
                        "char_end": text_end,
                        "line_position": line_position,
                        "annotation": True,
                        "status": str(meta.get("status") or ""),
                        "source": str(meta.get("source") or ""),
                        "annotation_text": str(meta.get("annotation_text") or ""),
                        "annotation_meaning": str(meta.get("annotation_meaning") or ""),
                        "annotation_match_reason": str(meta.get("annotation_match_reason") or ""),
                        "annotation_matched_key": str(meta.get("annotation_matched_key") or ""),
                        "hover_highlight": bool(meta.get("hover_highlight", True)),
                        "show_ruby_on_hover": show_ruby_on_hover,
                    }
                )
                annotation_region_added = True

            self._draw_annotation_background(meta, seg_left, seg_right, base_y)

            if ruby and not self.hover_ruby_enabled:
                self._draw_ruby_text(ruby, base_w, ruby_w, cx, ruby_y)
            if (
                ruby
                and not preview
                and self._has_hoverable_ruby_base(base)
                and not annotation_region_added
            ):
                self._hover_regions.append(
                    {
                        "bbox": (
                            seg_left,
                            base_y - (self.line_height / 2),
                            seg_right,
                            base_y + (self.line_height / 2),
                        ),
                        "ruby": ruby,
                        "base_w": base_w,
                        "ruby_w": ruby_w,
                        "cx": cx,
                        "ruby_y": ruby_y,
                        "base": base,
                        "lookup": base,
                        "char_start": text_start,
                        "char_end": text_end,
                        "line_position": line_position,
                    }
                )

            fill, outline, thickness = self._text_style_for_segment(meta)
            self._draw_outlined_text(
                self.canvas,
                cx,
                base_y,
                base,
                self.font,
                fill=fill,
                outline=outline,
                thickness=thickness,
            )
            self._draw_annotation_lines(meta, seg_left, seg_right, base_y)
            cur_x = seg_right + float(item.get("post_pad") or 0.0)

        self._hover_regions.extend(pending_group_hover_regions)

        if collect_word_regions:
            self._add_word_regions_for_line(line_text, line_region_meta, base_y, ruby_y, line_position=line_position)

    def _annotation_style_visible(self, meta: dict) -> bool:
        if not meta or not meta.get("annotation"):
            return False
        if not bool(meta.get("normal_style_visible", True)):
            return False
        style = meta.get("style")
        return isinstance(style, dict) and bool(style.get("enabled"))

    def _text_style_for_segment(self, meta: dict) -> tuple[str, str, int]:
        if not self._annotation_style_visible(meta):
            return self.color, self.glow_color, self.glow_radius
        style = meta.get("style") or {}
        fill = str(style.get("text_color") or self.color)
        if bool(style.get("outline")):
            outline = str(style.get("outline_color") or self.glow_color)
            try:
                thickness = int(float(style.get("outline_thickness") or self.glow_radius))
            except Exception:
                thickness = self.glow_radius
            return fill, outline, max(0, min(20, thickness))
        return fill, self.glow_color, self.glow_radius

    def _draw_annotation_background(self, meta: dict, x1: float, x2: float, base_y: float) -> None:
        if not self._annotation_style_visible(meta):
            return
        style = meta.get("style") or {}
        color = str(style.get("background_color") or "").strip()
        if not color:
            return
        # Tk canvas items are not per-item alpha composited; background_alpha is stored for future backends.
        try:
            pad_x = 3
            pad_y = 2
            self.canvas.create_rectangle(
                x1 - pad_x,
                base_y - (self.line_height / 2) + pad_y,
                x2 + pad_x,
                base_y + (self.line_height / 2) - pad_y,
                fill=color,
                outline="",
                tags=self._merge_render_tags(("annotation_bg",)),
            )
        except Exception:
            logger.debug("Failed to draw annotation background", exc_info=True)

    def _draw_annotation_lines(self, meta: dict, x1: float, x2: float, base_y: float) -> None:
        if not self._annotation_style_visible(meta):
            return
        style = meta.get("style") or {}
        try:
            if bool(style.get("underline")):
                width = max(1, int(float(style.get("underline_thickness") or 1)))
                color = str(style.get("underline_color") or style.get("text_color") or self.color)
                y = base_y + (self.line_height * 0.40)
                self.canvas.create_line(
                    x1,
                    y,
                    x2,
                    y,
                    fill=color,
                    width=width,
                    tags=self._merge_render_tags(("annotation_underline",)),
                )
            if bool(style.get("overline")):
                width = max(1, int(float(style.get("overline_thickness") or 1)))
                color = str(style.get("overline_color") or style.get("text_color") or self.color)
                y = base_y - (self.line_height * 0.44)
                self.canvas.create_line(
                    x1,
                    y,
                    x2,
                    y,
                    fill=color,
                    width=width,
                    tags=self._merge_render_tags(("annotation_overline",)),
                )
        except Exception:
            logger.debug("Failed to draw annotation line", exc_info=True)

    def _refresh_fonts_if_needed(self) -> None:
        font_family = self.config.get("SUBTITLE_FONT")
        font_size = int(self.config.get("SUBTITLE_FONT_SIZE") or 12)
        hover_ruby_enabled = bool(self.config.get("HOVER_DEFAULT_RUBY"))
        color = self.config.get("SUBTITLE_COLOR")
        glow_color = str(self.config.get("GLOW_COLOR") or "black")

        try:
            raw_glow_radius = self.config.get("GLOW_RADIUS")
            glow_radius = 10 if raw_glow_radius is None else int(float(raw_glow_radius))
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
        self._annotation_segment_cache.clear()
        self._clear_preview_render_cache(delete_items=True)
        self._last_overlay_width = None

    def _wrap_segments(
        self,
        segments: List[Tuple[str, Optional[str]]],
        max_width: int,
        padding: int = 0,
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

            segment_list = list(segments)
            idx = 0
            while idx < len(segment_list):
                segment = segment_list[idx]
                base = self._segment_base(segment)
                ruby = self._segment_ruby(segment)
                meta = self._segment_meta(segment)
                ruby_group_id = str(meta.get("ruby_group_id") or "")
                if not base:
                    idx += 1
                    continue

                if ruby_group_id:
                    unit: list[Segment] = []
                    unit_base_w = 0
                    unit_base = ""
                    unit_ruby = str(meta.get("ruby_group_ruby") or "")
                    while idx < len(segment_list):
                        grouped_segment = segment_list[idx]
                        grouped_meta = self._segment_meta(grouped_segment)
                        if str(grouped_meta.get("ruby_group_id") or "") != ruby_group_id:
                            break
                        grouped_base = self._segment_base(grouped_segment)
                        if grouped_base:
                            unit_base += grouped_base
                            unit_base_w += self._measure_text(self.font, grouped_base)
                            unit.append(
                                self._make_segment(
                                    grouped_base,
                                    self._segment_ruby(grouped_segment),
                                    grouped_meta,
                                )
                            )
                        idx += 1
                    if not unit:
                        continue
                    ruby_w = self._measure_text(self.ruby_font, unit_ruby) if unit_ruby else 0
                    unit_w = max(unit_base_w, ruby_w)

                    if cur and (cur_w + unit_w) <= limit:
                        cur.extend(unit)
                        cur_w += unit_w
                        continue

                    if cur and (cur_w + unit_w) > limit:
                        _flush()

                    cur.extend(unit)
                    cur_w += unit_w
                    if unit_w > limit:
                        _flush()
                    continue

                idx += 1
                visible_ruby = None if meta.get("hide_ruby") else ruby
                base_w = self._measure_text(self.font, base)
                if visible_ruby:
                    ruby_w = self._measure_text(self.ruby_font, visible_ruby)
                    seg_w = max(base_w, ruby_w)
                else:
                    seg_w = base_w

                if cur and (cur_w + seg_w) <= limit:
                    cur.append(self._make_segment(base, ruby, meta))
                    cur_w += seg_w
                    continue

                if cur and (cur_w + seg_w) > limit:
                    _flush()

                if seg_w <= limit:
                    cur.append(self._make_segment(base, ruby, meta))
                    cur_w += seg_w
                    continue

                if visible_ruby:
                    cur.append(self._make_segment(base, ruby, meta))
                    _flush()
                    continue

                source_cursor = 0
                for chunk in self._split_text_to_fit(base, limit):
                    chunk_w = self._measure_text(self.font, chunk)
                    if cur and (cur_w + chunk_w) > limit:
                        _flush()
                    chunk_meta = dict(meta)
                    if "_cue_start" in chunk_meta:
                        local_start = base.find(chunk, source_cursor)
                        if local_start < 0:
                            local_start = source_cursor
                        source_cursor = local_start + len(chunk)
                        source_start = int(chunk_meta.get("_cue_start") or 0) + local_start
                        chunk_meta["_cue_start"] = source_start
                        chunk_meta["_cue_end"] = source_start + len(chunk)
                    cur.append(self._make_segment(chunk, None, chunk_meta))
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

        if thickness <= 1:
            offsets = [
                (-1, 0),
                (1, 0),
                (0, -1),
                (0, 1),
                (-1, -1),
                (-1, 1),
                (1, -1),
                (1, 1),
            ] if thickness else []
        else:
            offsets = self._get_approximate_outline_offsets(thickness)
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
        tags = self._merge_render_tags(tags)
        timing = bool(self._timing_enabled)
        start = time.perf_counter() if timing else 0.0
        item_count = 0

        try:
            if thickness == 0:
                canvas.create_text(x, y, text=text, fill=fill, font=font, anchor=anchor, tags=tags)
                item_count = 1
                return

            create_text = canvas.create_text
            offsets = self._get_outline_offsets(thickness)

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
            item_count = len(offsets) + 1
        finally:
            if timing:
                self._timing_data["draw_outlined_text_time"] += time.perf_counter() - start
                self._timing_data["draw_outlined_text_count"] += 1
                self._timing_data["canvas_text_item_count"] += item_count

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

    def _add_word_regions_for_line(
        self,
        line_text: str,
        segment_meta: list[dict[str, Any]],
        base_y: float,
        ruby_y: float,
        *,
        line_position: str = "second",
    ) -> None:
        if not line_text:
            return
        tokens: list[dict[str, Any]] = []
        source_coordinates = False
        used_snapshot = False
        snapshot_line = None
        snapshot = getattr(self, "_cue_snapshot", None)
        line_indexes = {
            int(segment.get("cue_line_index"))
            for segment in segment_meta
            if segment.get("cue_line_index") is not None
        }
        source_ranges = [
            (int(segment.get("cue_start")), int(segment.get("cue_end")))
            for segment in segment_meta
            if segment.get("cue_start") is not None and segment.get("cue_end") is not None
        ]
        if snapshot is not None and len(line_indexes) == 1 and source_ranges:
            snapshot_line = snapshot.line(next(iter(line_indexes)))
            if snapshot_line is not None:
                used_snapshot = True
                source_start = min(start for start, _end in source_ranges)
                source_end = max(end for _start, end in source_ranges)
                tokens = [
                    token.as_mapping()
                    for token in snapshot_line.tokens
                    if token.start >= source_start and token.end <= source_end
                ]
                source_coordinates = True
        if not used_snapshot:
            tokens = self._tokenize_words(line_text)
            source_coordinates = False
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
                if source_coordinates:
                    seg_start = int(segment.get("cue_start") or 0)
                    seg_end = int(segment.get("cue_end") or seg_start)
                else:
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
            region = dict(token)
            region.update(
                {
                    "bbox": (
                        x1,
                        base_y - (self.line_height / 2),
                        x2,
                        base_y + (self.line_height / 2),
                    ),
                    "base": surface,
                    "lookup": str(token.get("lookup") or surface).strip(),
                    "char_start": start,
                    "char_end": end,
                    "compound": bool(token.get("compound")),
                    "orth_base": str(token.get("orth_base") or "").strip(),
                    "sentence_lookup": snapshot_line.text if snapshot_line is not None else line_text,
                    "cue_text": snapshot.plain_text if snapshot is not None else line_text,
                    "ruby": str(token.get("ruby") or token.get("reading") or "").strip(),
                    "reading": str(token.get("reading") or "").strip(),
                    "pos1": str(token.get("pos1") or ""),
                    "pos2": str(token.get("pos2") or ""),
                    "c_type": str(token.get("c_type") or ""),
                    "c_form": str(token.get("c_form") or ""),
                    "cx": (x1 + x2) / 2,
                    "ruby_y": ruby_y,
                    "line_position": line_position,
                }
            )
            self._word_regions.append(region)

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

    def _finish_hover_bindings(self, preview: bool = False) -> None:
        if preview:
            if not getattr(self, "_preview_hover_bindings_cleared", False):
                self._clear_hover_ruby()
                try:
                    self.canvas.unbind("<Motion>")
                    self.canvas.unbind("<Leave>")
                except Exception:
                    pass
                self._preview_hover_bindings_cleared = True
            return
        self._preview_hover_bindings_cleared = False
        normal_layers = hover_layers(self.config, "ruby")
        has_ruby_hover = "ruby" in normal_layers and self.hover_ruby_enabled and self._hover_regions
        has_dictionary_hover = self._shift_hover_dictionary_enabled() and bool(self._word_regions)
        has_layer_hover = bool(self._word_regions and (
            any(layer != "ruby" for layer in normal_layers)
            or
            has_dictionary_hover
            or callable(getattr(self, "_translation_lookup", None))
            or getattr(self, "_annotation_provider", None) is not None
        ))
        if has_ruby_hover or has_layer_hover:
            self.canvas.bind("<Motion>", self._on_hover_motion)
            self.canvas.bind("<Leave>", self._schedule_hover_clear)
            if self._hover_click_bind_id is None:
                self._hover_click_bind_id = self.canvas.bind(
                    "<Control-Button-1>",
                    self._on_status_word_click,
                    add="+",
                )
        else:
            self._clear_hover_ruby()
            try:
                self.canvas.unbind("<Motion>")
                self.canvas.unbind("<Leave>")
            except Exception:
                pass

    @staticmethod
    def _hover_region_key(region: dict | None) -> tuple | None:
        """Return a stable identity for a rendered hover region.

        Object ids are convenient while a canvas is unchanged, but a delayed
        callback can survive a redraw.  The rendered cue/span/bounds identity is
        stable enough to distinguish repeated word surfaces in one subtitle.
        """
        if not isinstance(region, dict):
            return None
        try:
            bbox = tuple(round(float(value), 3) for value in tuple(region.get("bbox") or ())[:4])
        except Exception:
            bbox = ()
        return (
            str(region.get("line_position") or ""),
            str(region.get("sentence_lookup") or ""),
            region.get("line_index"),
            region.get("cue_line_index"),
            region.get("cue_start"),
            region.get("cue_end"),
            int(region.get("char_start") or 0),
            int(region.get("char_end") or 0),
            str(region.get("base") or ""),
            str(region.get("lookup") or ""),
            bbox,
        )

    @classmethod
    def _hover_target_key(cls, ruby_hit: dict | None, layer_hit: dict | None) -> tuple:
        return (cls._hover_region_key(ruby_hit), cls._hover_region_key(layer_hit))

    def _advance_hover_generation(self) -> int:
        self._hover_generation += 1
        return self._hover_generation

    def _bind_overlay_drag_callbacks(self, overlay: SubtitleOverlayUI | None) -> None:
        """Attach hover suppression once an overlay becomes available.

        The renderer is constructed from the canvas, so the overlay is first
        available here rather than in ``__init__``.  Older/test overlays simply
        do not expose this optional binding.
        """
        if overlay is self._hover_drag_overlay:
            return
        self._hover_drag_overlay = overlay
        bind_callbacks = getattr(overlay, "bind_drag_callbacks", None)
        if callable(bind_callbacks):
            try:
                bind_callbacks(self.suspend_hover_for_overlay_drag, self.resume_hover_after_overlay_drag)
            except Exception:
                logger.debug("Failed to bind subtitle-overlay drag callbacks", exc_info=True)
        try:
            self.set_hover_suspended(bool(getattr(overlay, "is_dragging", False)))
        except Exception:
            pass

    def set_hover_suspended(self, suspended: bool) -> None:
        """Suspend transient hover UI until a real subsequent mouse movement.

        Dragging moves the window below a stationary pointer, which can produce
        synthetic canvas motion/refresh events.  Hiding and invalidating hover
        state here prevents those events from opening a tooltip mid-drag.
        """
        suspended = bool(suspended)
        if suspended == self._hover_suspended:
            return
        self._hover_suspended = suspended
        self._hover_requires_fresh_motion = True
        self._advance_hover_generation()
        if suspended:
            self._clear_hover_ruby()

    def suspend_hover_for_overlay_drag(self) -> None:
        self.set_hover_suspended(True)

    def resume_hover_after_overlay_drag(self) -> None:
        self.set_hover_suspended(False)

    def _on_hover_motion(self, event) -> None:
        self._handle_hover_motion(event, actual_motion=True)

    def _handle_hover_motion(self, event, *, actual_motion: bool) -> None:
        if self._hover_suspended:
            return
        if self._hover_requires_fresh_motion:
            if not actual_motion:
                return
            self._hover_requires_fresh_motion = False
        try:
            ctrl_held = bool(int(getattr(event, "state", 0) or 0) & 0x0004)
        except Exception:
            ctrl_held = False
        x = float(getattr(event, "x", 0))
        y = float(getattr(event, "y", 0))
        ruby_hit = self._region_at_point(self._hover_regions, x, y)
        pointer_word = self._region_at_point(self._word_regions, x, y, anchor=ruby_hit)
        if len(self._status_selected_regions) > 1 and self._selected_status_region(pointer_word):
            self._cancel_hover_clear()
            selection_key = self._status_selection_key()
            if self._hover_active_region != selection_key or self._hover_active_mode != "multi_selection":
                self._advance_hover_generation()
                self._cancel_hover_layer_job()
                self.canvas.delete("hover_ruby")
                self.canvas.delete("hover_word_highlight")
                self._hide_hover_text_window()
                self._hover_active_region = selection_key
                self._hover_active_mode = "multi_selection"
                self._hover_active_translation_key = None
                self._show_selected_status_regions(pointer_word)
            return
        if ctrl_held and self._status_selected_regions:
            self._cancel_hover_clear()
            return
        mode = self._hover_mode(event)
        layers = hover_layers(self.config, mode)

        layer_hit = (
            pointer_word
            if any(layer != "ruby" for layer in layers)
            else None
        )
        active_key = self._hover_target_key(ruby_hit, layer_hit)

        if ruby_hit is None and layer_hit is None:
            self._cancel_hover_layer_job()
            if self._hover_clear_job is None:
                self._schedule_hover_clear()
            return

        self._cancel_hover_clear()
        if active_key == self._hover_active_region and mode == self._hover_active_mode:
            return

        previous_mode = self._hover_active_mode
        self._advance_hover_generation()
        self._cancel_hover_layer_job()
        try:
            self.canvas.delete("hover_ruby")
            self.canvas.delete("hover_word_highlight")
            if previous_mode == "status" and mode != "status":
                self.canvas.delete("hover_status_highlight")
                self._hover_status_highlights.clear()
                self._status_selected_regions.clear()
        except Exception:
            pass
        self._hover_active_translation_key = None
        self._hover_active_region = active_key
        self._hover_active_mode = mode

        if ruby_hit is not None and "ruby" in layers:
            self._show_hover_ruby_region(ruby_hit, allow_annotation_highlight=(mode == "ruby"))

        if layer_hit is None:
            return
        self._schedule_hover_layers(layer_hit, layers, active_key, mode)

    def _hover_layer_delay_ms(self) -> int:
        try:
            value = self.config.get("HOVER_LAYER_DELAY_MS")
            if value is None or str(value).strip() == "":
                value = 500
            return max(0, min(10000, int(float(value))))
        except Exception:
            return 500

    def _cancel_hover_layer_job(self) -> None:
        job, self._hover_layer_job = self._hover_layer_job, None
        if job is not None:
            try:
                self.canvas.after_cancel(job)
            except Exception:
                pass

    def _cancel_hover_clear(self) -> None:
        job, self._hover_clear_job = self._hover_clear_job, None
        if job is not None:
            try:
                self.canvas.after_cancel(job)
            except Exception:
                pass

    def _pointer_inside_hover_target(self) -> bool:
        try:
            px = int(self.canvas.winfo_pointerx())
            py = int(self.canvas.winfo_pointery())
        except Exception:
            return False

        hover_window = self._hover_text_window
        if hover_window is not None:
            try:
                hover_visible = bool(hover_window.winfo_exists()) and str(hover_window.state()) != "withdrawn"
            except Exception:
                hover_visible = False
            if hover_visible:
                try:
                    x = int(hover_window.winfo_rootx())
                    y = int(hover_window.winfo_rooty())
                    width = int(hover_window.winfo_width() or hover_window.winfo_reqwidth())
                    height = int(hover_window.winfo_height() or hover_window.winfo_reqheight())
                    if x <= px < x + width and y <= py < y + height:
                        return True
                except Exception:
                    pass

        try:
            x = float(px - int(self.canvas.winfo_rootx()))
            y = float(py - int(self.canvas.winfo_rooty()))
            if x < 0 or y < 0 or x >= int(self.canvas.winfo_width()) or y >= int(self.canvas.winfo_height()):
                return False
        except Exception:
            return False

        ruby_hit = self._region_at_point(self._hover_regions, x, y)
        word_hit = self._region_at_point(self._word_regions, x, y, anchor=ruby_hit)
        if self._hover_active_mode == "multi_selection":
            return self._selected_status_region(word_hit) is not None

        layers = hover_layers(self.config, self._hover_active_mode or "ruby")
        layer_hit = word_hit if any(layer != "ruby" for layer in layers) else None
        active_key = self._hover_target_key(ruby_hit, layer_hit)
        return active_key != (None, None) and active_key == self._hover_active_region

    def _schedule_hover_clear(self, _event=None, delay_ms: int | None = None) -> None:
        self._cancel_hover_clear()
        cue_generation = self._hover_cue_generation
        hover_generation = self._hover_generation
        active_key = self._hover_active_region
        active_mode = self._hover_active_mode
        if delay_ms is None:
            try:
                value = self.config.get("SUBTITLE_HOVER_CLEAR_DELAY_MS")
                if value is None or str(value).strip() == "":
                    value = 700
                delay_ms = int(float(value))
            except Exception:
                delay_ms = 700

        def clear_if_outside() -> None:
            self._hover_clear_job = None
            if (
                self._hover_suspended
                or cue_generation != self._hover_cue_generation
                or hover_generation != self._hover_generation
                or active_key != self._hover_active_region
                or active_mode != self._hover_active_mode
            ):
                return
            if self._pointer_inside_hover_target():
                return
            self._clear_hover_ruby()

        self._hover_clear_job = self.canvas.after(max(0, int(delay_ms)), clear_if_outside)

    def _schedule_hover_layers(self, region: dict, layers: tuple[str, ...], active_key, mode: str) -> None:
        non_ruby_layers = tuple(layer for layer in layers if layer != "ruby")
        if not non_ruby_layers:
            return
        cue_generation = self._hover_cue_generation
        hover_generation = self._hover_generation
        region_key = self._hover_region_key(region)

        def show() -> None:
            self._hover_layer_job = None
            if (
                self._hover_suspended
                or cue_generation != self._hover_cue_generation
                or hover_generation != self._hover_generation
                or self._hover_active_region != active_key
                or self._hover_active_mode != mode
                or self._hover_region_key(region) != region_key
            ):
                return
            logger.debug(
                "Subtitle hover layers mode=%s layers=%s lookup=%r",
                mode,
                ",".join(layers),
                str(region.get("lookup") or region.get("base") or ""),
            )
            self._draw_word_highlight(region)
            self._show_hover_layers(region, layers)

        delay = self._hover_layer_delay_ms()
        if delay <= 0:
            show()
        else:
            self._hover_layer_job = self.canvas.after(delay, show)

    def _on_status_word_click(self, event):
        try:
            ctrl_held = bool(int(getattr(event, "state", 0) or 0) & 0x0004)
        except Exception:
            ctrl_held = False
        if not ctrl_held and self._hover_mode(event) != "status":
            return None
        region = self._region_at_point(
            self._word_regions,
            float(getattr(event, "x", 0)),
            float(getattr(event, "y", 0)),
            anchor=self._region_at_point(
                self._hover_regions,
                float(getattr(event, "x", 0)),
                float(getattr(event, "y", 0)),
            ),
        )
        if region is None:
            self.canvas.delete("hover_status_highlight")
            self._hover_status_highlights.clear()
            self._status_selected_regions.clear()
            self._hide_hover_text_window()
            return "break"

        bbox = tuple(float(value) for value in region.get("bbox", (0, 0, 0, 0)))
        existing = next(
            (item for item in self._status_selected_regions if tuple(float(v) for v in item.get("bbox", ())) == bbox),
            None,
        )
        if existing is not None:
            self._status_selected_regions.remove(existing)
        else:
            self._status_selected_regions.append(region)

        self.canvas.delete("hover_status_highlight")
        self._hover_status_highlights.clear()
        ordered = sorted(
            self._status_selected_regions,
            key=lambda item: (float(item["bbox"][1]), float(item["bbox"][0])),
        )
        for item in ordered:
            self._draw_word_highlight(item, accumulated=True)
        if not ordered:
            self._hide_hover_text_window()
            return "break"
        self._hover_active_region = self._status_selection_key()
        self._hover_active_mode = "multi_selection"
        self._hover_active_translation_key = None
        self._show_selected_status_regions(region)
        return "break"

    @staticmethod
    def _same_word_region(left: dict | None, right: dict | None) -> bool:
        if not left or not right:
            return False
        return tuple(left.get("bbox") or ()) == tuple(right.get("bbox") or ())

    def _selected_status_region(self, region: dict | None) -> dict | None:
        for selected in self._status_selected_regions:
            if self._same_word_region(selected, region):
                return selected
        return None

    def _status_selection_key(self):
        boxes = sorted(tuple(float(value) for value in item.get("bbox", ())) for item in self._status_selected_regions)
        return ("multi_selection", tuple(boxes))

    @staticmethod
    def _region_with_combined_hover_anchor(region: dict, regions: list[dict]) -> dict:
        """Anchor one hover window over the horizontal extent of selected words."""
        anchor = dict(region or {})
        valid_regions = []
        for item in regions or []:
            try:
                box = tuple(float(value) for value in item["bbox"])
            except Exception:
                continue
            if len(box) == 4:
                valid_regions.append((item, box))
        if not valid_regions:
            return anchor

        upper_line = [
            box
            for item, box in valid_regions
            if str(item.get("line_position") or "") == "first"
        ]
        vertical_boxes = upper_line or [box for _item, box in valid_regions]
        anchor["hover_anchor_bbox"] = (
            min(box[0] for _item, box in valid_regions),
            min(box[1] for box in vertical_boxes),
            max(box[2] for _item, box in valid_regions),
            max(box[3] for box in vertical_boxes),
        )
        anchor["hover_anchor_above"] = True
        return anchor

    def _show_selected_status_regions(self, anchor_region: dict) -> None:
        ordered = sorted(
            self._status_selected_regions,
            key=lambda item: (float(item["bbox"][1]), float(item["bbox"][0])),
        )
        phrase = " ".join(str(item.get("base") or "").strip() for item in ordered).strip()
        if not phrase:
            return
        anchor = self._region_with_combined_hover_anchor(anchor_region, ordered)
        anchor["base"] = phrase
        anchor["lookup"] = phrase
        status_parts = []
        for item in ordered:
            status = self._hover_status_text(item)
            if status:
                status_parts.append(f"{str(item.get('base') or '').strip()}: {status}")
        anchor["multi_status_text"] = " | ".join(status_parts)
        logger.debug("Subtitle Ctrl-click hover selection words=%d query=%r", len(ordered), phrase)
        self._show_hover_layers(anchor, ("translation", "status"))

    @staticmethod
    def _region_at_point(
        regions: list[dict],
        x: float,
        y: float,
        *,
        anchor: dict | None = None,
    ) -> dict | None:
        candidates = []
        for region in regions or []:
            try:
                x1, y1, x2, y2 = region["bbox"]
            except Exception:
                continue
            if x1 <= x <= x2 and y1 <= y <= y2:
                candidates.append(region)
        return choose_hover_region(candidates, anchor)

    def _show_hover_ruby_region(self, region: dict, *, allow_annotation_highlight: bool) -> None:
        if bool(region.get("annotation")):
            if allow_annotation_highlight and bool(region.get("hover_highlight")):
                self._draw_word_highlight(region)
            ruby = str(region.get("ruby") or "")
            if not (ruby and (bool(self.hover_ruby_enabled) or bool(region.get("show_ruby_on_hover", True)))):
                return
            self._draw_ruby_text(
                ruby,
                int(region.get("base_w") or 0),
                int(region.get("ruby_w") or 0),
                float(region.get("cx") or 0.0),
                float(region.get("ruby_y") or 0.0),
                tags=("hover_ruby",),
            )
            return
        self._draw_ruby_text(
            region["ruby"],
            region["base_w"],
            region["ruby_w"],
            region["cx"],
            region["ruby_y"],
            tags=("hover_ruby",),
        )

    def _draw_word_highlight(self, region: dict, *, accumulated: bool = False) -> None:
        try:
            x1, y1, x2, y2 = region["bbox"]
            bbox_key = (float(x1), float(y1), float(x2), float(y2))
            if accumulated and bbox_key in self._hover_status_highlights:
                return
            pad_x = 4
            pad_y = 2
            tag = "hover_status_highlight" if accumulated else "hover_word_highlight"
            self.canvas.create_rectangle(
                x1 - pad_x,
                y1 + pad_y,
                x2 + pad_x,
                y2 - pad_y,
                fill="#303030",
                outline="#8a8a8a",
                width=1,
                tags=(tag,),
            )
            self.canvas.tag_lower(tag)
            if accumulated:
                self._hover_status_highlights.add(bbox_key)
        except Exception:
            pass

    def _clear_hover_ruby(self, _event=None) -> None:
        self._advance_hover_generation()
        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        try:
            self.canvas.delete("hover_ruby")
            self.canvas.delete("hover_word_highlight")
            self.canvas.delete("hover_status_highlight")
            self._hover_status_highlights.clear()
        except Exception:
            pass
        self._hide_hover_text_window()
        self._hover_active_region = None
        self._hover_active_mode = None
        self._hover_active_translation_key = None
        self._hover_copy_values.clear()

    def _shift_hover_dictionary_enabled(self) -> bool:
        try:
            return bool(self.config.get("HOVER_DICTIONARY_ENABLED") or self.config.get("SHIFT_HOVER_KANJI_DICTIONARY") or False)
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
        return "dictionary" if self._shift_hover_dictionary_enabled() and self._is_shift_held() else "ruby"

    def _show_hover_dictionary(self, region: dict) -> None:
        text = self._hover_dictionary_text(region)
        if text:
            self._show_hover_text(region, text)

    def _hover_dictionary_text(self, region: dict) -> str:
        lookup = getattr(self, "_dictionary_lookup", None)
        if not callable(lookup):
            return ""
        query = str(region.get("lookup") or region.get("base") or region.get("sentence_lookup") or "").strip()
        if not query:
            return ""
        try:
            text = str(lookup(query) or "").strip()
        except Exception:
            text = ""
        return text

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

    def _show_hover_translation(self, region: dict) -> None:
        lookup = getattr(self, "_translation_lookup", None)
        if not callable(lookup):
            return
        query = str(region.get("base") or region.get("lookup") or "").strip()
        if not query:
            return
        try:
            text = str(lookup(query, provider=self._translation_provider()) or "").strip()
        except TypeError:
            try:
                text = str(lookup(query) or "").strip()
            except Exception:
                text = ""
        except Exception:
            text = ""
        if text:
            self._show_hover_text(region, text)

    def _show_hover_status(self, region: dict) -> None:
        text = self._hover_status_text(region)
        if text:
            self._show_hover_text(region, text)

    def _hover_status_text(self, region: dict) -> str:
        multi_status = str(region.get("multi_status_text") or "").strip()
        if multi_status:
            return multi_status
        provider = getattr(self, "_annotation_provider", None)
        if bool(region.get("database_check")) and provider is not None:
            describe = getattr(provider, "describe_selection", None)
            if callable(describe):
                try:
                    text = str(region.get("base") or region.get("lookup") or "").strip()
                    tokens = region.get("cue_tokens")
                    if tokens is None and region.get("cue_token") is not None:
                        tokens = (region.get("cue_token"),)
                    return str(describe(text, tokens=tokens) or "").strip()
                except Exception:
                    logger.debug("Failed to build database diagnostic hover text", exc_info=True)
        parts = []
        if bool(region.get("annotation")):
            status = str(region.get("status") or "").replace("_", " ").strip()
            source = str(region.get("source") or "").strip()
            if status:
                parts.append(status if not source else f"{status} ({source})")
            reason = str(region.get("annotation_match_reason") or "").replace("_", " ").strip()
            matched_key = str(region.get("annotation_matched_key") or "").strip()
            if reason:
                parts.append(f"via {reason}" + (f" [{matched_key}]" if matched_key else ""))
            text = str(region.get("annotation_text") or "").strip()
            if text:
                parts.append(text)
            elif bool(self.config.get("ANNOTATION_SHOW_MEANING_ON_HOVER") or False):
                meaning = str(region.get("annotation_meaning") or "").strip()
                if meaning:
                    parts.append(meaning)
        elif region.get("cue_token") is not None:
            pass
        elif provider is not None:
            token = {
                "surface": str(region.get("base") or "").strip(),
                "lookup": str(region.get("lookup") or region.get("base") or "").strip(),
                "reading": str(region.get("ruby") or "").strip(),
            }
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

    def _show_hover_layers(self, region: dict, layers: tuple[str, ...]) -> None:
        parts: list[tuple[str, str]] = []
        if "dictionary_together" in layers:
            parts.append(("dictionary", self._hover_dictionary_text(region) or "No dictionary entry"))
        elif "dictionary" in layers:
            dictionary_text = str(region.get("multi_dictionary_text") or "").strip()
            parts.append(("dictionary", dictionary_text or self._hover_dictionary_text(region) or "No dictionary entry"))
        if "translation_each" in layers:
            translation_text = str(region.get("multi_translation_text") or "").strip()
            parts.append(("translation", translation_text or "No translation"))
        elif "translation" in layers:
            query = str(region.get("base") or region.get("lookup") or "").strip()
            provider = self._translation_provider()
            cache_key = (provider, query)
            if not hasattr(self, "_translation_hover_cache"):
                self._translation_hover_cache = {}
                self._translation_hover_pending = set()
            self._hover_active_translation_key = cache_key
            cached = self._translation_hover_cache.get(cache_key)
            if cached is not None:
                logger.debug(
                    "Subtitle hover translation cache hit provider=%s query=%r result_chars=%d",
                    provider,
                    query,
                    len(cached),
                )
            parts.append(("translation", "Translating..." if cached is None else (cached or "No translation")))
            if query and cached is None and cache_key not in self._translation_hover_pending:
                self._start_layer_translation(region, layers, cache_key, query, provider)
        if "status" in layers:
            status_region = dict(region)
            if bool(region.get("database_check")):
                status_region["database_check"] = True
            parts.append(("status", self._hover_status_text(status_region) or "Not in database"))
        self._hover_copy_values = {
            name: str(value or "").strip()
            for name, value in parts
            if str(value or "").strip()
            not in {"Translating...", "No translation", "No dictionary entry", "Not in database"}
        }
        text = combine_hover_text(parts)
        if text:
            self._show_hover_text(region, text)

    def _start_layer_translation(self, region, layers, cache_key, query, provider) -> None:
        lookup = getattr(self, "_translation_lookup", None)
        if not callable(lookup):
            logger.debug("Subtitle hover translation skipped: no lookup callback")
            return
        self._translation_hover_pending.add(cache_key)
        active_key = self._hover_active_region
        cue_generation = self._hover_cue_generation
        hover_generation = self._hover_generation
        region_key = self._hover_region_key(region)
        logger.debug("Subtitle hover translation requested provider=%s query=%r", provider, query)

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
                "Subtitle hover translation completed provider=%s query=%r result_chars=%d",
                provider,
                query,
                len(value),
            )

            def apply_result() -> None:
                self._translation_hover_pending.discard(cache_key)
                self._translation_hover_cache[cache_key] = value
                if not value:
                    self.canvas.after(
                        10000,
                        lambda key=cache_key: self._translation_hover_cache.pop(key, None)
                        if self._translation_hover_cache.get(key) == ""
                        else None,
                    )
                if len(self._translation_hover_cache) > 256:
                    self._translation_hover_cache.pop(next(iter(self._translation_hover_cache)), None)
                if (
                    self._hover_suspended
                    or cue_generation != self._hover_cue_generation
                    or hover_generation != self._hover_generation
                    or self._hover_active_region != active_key
                    or self._hover_active_translation_key != cache_key
                    or self._hover_region_key(region) != region_key
                ):
                    return
                self._show_hover_layers(region, layers)

            dispatch_to_tk(self.canvas, apply_result)

        threading.Thread(target=worker, daemon=True).start()

    def _show_hover_text(self, region: dict, text: str) -> None:
        if self._hover_suspended:
            return
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
            label = tk.Text(
                win,
                bg="black",
                fg=self.color,
                bd=0,
                padx=self._HOVER_TEXT_PAD_X // 2,
                pady=3,
                relief="flat",
                highlightthickness=0,
                insertwidth=0,
                takefocus=True,
                cursor="xterm",
                wrap="none",
                width=1,
                height=1,
            )
            label.pack(fill="both", expand=True)
            win.bind("<Enter>", self._keep_hover_text_visible, add="+")
            win.bind("<Leave>", self._schedule_hover_clear, add="+")
            label.bind("<Enter>", self._keep_hover_text_visible, add="+")
            label.bind("<Leave>", self._schedule_hover_clear, add="+")
            label.bind("<ButtonPress-1>", self._focus_hover_text, add="+")
            label.bind("<ButtonRelease-1>", self._keep_hover_text_visible, add="+")
            label.bind("<Control-a>", self._select_all_hover_text)
            label.bind("<Control-A>", self._select_all_hover_text)
            label.bind("<Control-c>", self._copy_hover_text_selection)
            label.bind("<Control-C>", self._copy_hover_text_selection)
            label.bind("<Button-3>", self._show_hover_text_context_menu)
            self._hover_text_window = win
            self._hover_text_label = label

        try:
            font_size = max(8, int(float(self.config.get("SUBTITLE_FONT_SIZE") or 12) * 0.38))
            if label_text != self._hover_text_current:
                family = self.font.actual("family") if self.font else "Arial"
                body_font = tkFont.Font(family=family, size=font_size, weight="normal")
                heading_font = tkFont.Font(family=family, size=font_size, weight="bold")
                max_width_px = self._hover_max_width_px()
                content_width = max_width_px - self._HOVER_TEXT_PAD_X
                lines = self._wrap_hover_text_lines(
                    label_text,
                    body_font,
                    heading_font,
                    content_width,
                )
                display_text = "\n".join(lines)
                measured_line_width = max(
                    self._measure_hover_line(line or " ", body_font, heading_font)
                    for line in lines
                )
                width_px = min(
                    max_width_px,
                    measured_line_width + 2*self._HOVER_TEXT_PAD_X,
                )
                line_height = max(
                    1,
                    int(body_font.metrics("linespace") or font_size + 3),
                    int(heading_font.metrics("linespace") or font_size + 3),
                )
                height_px = (line_height * max(1, len(lines))) + self._HOVER_TEXT_PAD_Y
                self._hover_text_size = (max(24, int(width_px)), max(line_height + 6, int(height_px)))
                self._hover_text_label.configure(
                    state="normal",
                    fg=self.color,
                    font=body_font,
                    width=1,
                    height=1,
                    wrap="none",
                )
                self._hover_text_label.delete("1.0", "end")
                self._hover_text_label.insert("1.0", display_text)
                self._hover_text_label.xview_moveto(0)
                self._hover_text_label.yview_moveto(0)
                self._hover_text_label.tag_configure("heading", font=heading_font)
                self._hover_text_label.tag_configure("dictionary_entry", font=heading_font)
                in_dictionary_section = False
                for line_no, line in enumerate(lines, start=1):
                    for heading in self._HOVER_TEXT_HEADINGS:
                        if line.startswith(heading):
                            self._hover_text_label.tag_add("heading", f"{line_no}.0", f"{line_no}.{len(heading)}")
                            in_dictionary_section = heading == "Dictionary:"
                            break
                    if not in_dictionary_section:
                        continue
                    entry_start = len("Dictionary:") if line.startswith("Dictionary:") else 0
                    while entry_start < len(line) and line[entry_start].isspace():
                        entry_start += 1
                    separator_positions = [
                        position
                        for position in (line.find("—", entry_start), line.find("–", entry_start))
                        if position > entry_start
                    ]
                    hyphen_match = re.search(r"\s-\s", line[entry_start:])
                    if hyphen_match is not None:
                        separator_positions.append(entry_start + hyphen_match.start())
                    if not separator_positions:
                        continue
                    entry_end = min(separator_positions)
                    entry_text = line[entry_start:entry_end].strip()
                    if entry_text and any(
                        0x3000 <= ord(char) <= 0x30FF
                        or 0x3400 <= ord(char) <= 0x9FFF
                        or 0xF900 <= ord(char) <= 0xFAFF
                        for char in entry_text
                    ):
                        self._hover_text_label.tag_add(
                            "dictionary_entry",
                            f"{line_no}.{entry_start}",
                            f"{line_no}.{entry_end}",
                        )
                self._hover_text_label.configure(state="disabled")
                self._hover_text_current = label_text
        except Exception:
            try:
                self._hover_text_label.configure(state="normal")
                self._hover_text_label.delete("1.0", "end")
                self._hover_text_label.insert("1.0", label_text)
                self._hover_text_label.configure(state="disabled")
                self._hover_text_current = label_text
            except Exception:
                pass

        try:
            self._hover_text_window.update_idletasks()
        except Exception:
            pass

        try:
            anchor_bbox = region.get("hover_anchor_bbox") or region["bbox"]
            x1, y1, x2, y2 = (float(value) for value in anchor_bbox)
            measured_w, measured_h = self._hover_text_size
            hover_w = max(1, int(measured_w or 1))
            hover_h = max(1, int(measured_h or 1))
            canvas_x = int(self.canvas.winfo_rootx())
            canvas_y = int(self.canvas.winfo_rooty())
            desired_x = int(canvas_x + ((x1 + x2) / 2) - (hover_w / 2))
            if bool(region.get("hover_anchor_above")):
                desired_y = int(canvas_y + y1 - hover_h - 8)
            elif str(region.get("line_position") or "second") == "first":
                desired_y = int(canvas_y + y2 + 8)
            else:
                desired_y = int(canvas_y + y1 - hover_h - 8)
        except Exception:
            return

        desired_x, desired_y = self._clamp_hover_position(desired_x, desired_y, hover_w, hover_h)
        try:
            self._hover_text_window.geometry(f"{hover_w}x{hover_h}+{desired_x}+{desired_y}")
            self._raise_hover_text_window()
        except Exception:
            pass

    def _keep_hover_text_visible(self, _event=None) -> None:
        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        self._raise_hover_text_window()

    def _raise_hover_text_window(self) -> None:
        """Reassert hover z-order without focusing the overlay or tooltip."""
        win = self._hover_text_window
        if win is None or self._hover_suspended or self._hover_requires_fresh_motion:
            return
        generation = int(self._hover_generation)
        try:
            if not win.winfo_exists():
                return
            show_window_no_activate(win)
            win.lift()
        except Exception:
            return

        def reassert() -> None:
            if generation != int(self._hover_generation):
                return
            if self._hover_suspended or self._hover_requires_fresh_motion or win is not self._hover_text_window:
                return
            try:
                if win.winfo_exists() and str(win.state() or "") != "withdrawn":
                    show_window_no_activate(win)
                    win.lift()
            except Exception:
                pass

        try:
            self.canvas.after_idle(reassert)
        except Exception:
            pass

    def ensure_hover_text_on_top(self) -> None:
        """Restore a visible hover window after the subtitle window is raised."""
        if self._hover_suspended or self._hover_requires_fresh_motion:
            return
        win = self._hover_text_window
        if win is None:
            return
        try:
            if not win.winfo_exists() or str(win.state()) == "withdrawn":
                return
            self._raise_hover_text_window()
        except Exception:
            pass

    def _focus_hover_text(self, event=None) -> None:
        self._keep_hover_text_visible()
        widget = getattr(event, "widget", None) or self._hover_text_label
        try:
            widget.focus_set()
        except Exception:
            pass

    def _hover_text_content(self) -> str:
        label = self._hover_text_label
        if label is None:
            return ""
        try:
            return str(label.get("1.0", "end-1c") or "")
        except Exception:
            return ""

    def _hover_text_selection(self) -> str:
        label = self._hover_text_label
        if label is None:
            return ""
        try:
            return str(label.get("sel.first", "sel.last") or "")
        except tk.TclError:
            return ""
        except Exception:
            return ""

    def _copy_hover_text(self, text: str) -> bool:
        if not text:
            return False
        owner = self._hover_text_window or self.canvas
        try:
            owner.clipboard_clear()
            owner.clipboard_append(text)
            return True
        except Exception:
            logger.debug("Failed to copy subtitle hover text", exc_info=True)
            return False

    def _select_all_hover_text(self, _event=None):
        label = self._hover_text_label
        if label is None:
            return "break"
        self._keep_hover_text_visible()
        try:
            label.tag_remove("sel", "1.0", "end")
            label.tag_add("sel", "1.0", "end-1c")
            label.mark_set("insert", "end-1c")
            label.see("insert")
        except Exception:
            pass
        return "break"

    def _copy_hover_text_selection(self, _event=None):
        self._keep_hover_text_visible()
        self._copy_hover_text(self._hover_text_selection())
        return "break"

    def _show_hover_text_context_menu(self, event):
        self._keep_hover_text_visible()
        owner = self._hover_text_window or self.canvas
        menu = tk.Menu(owner, tearoff=0)
        menu.add_command(label="Copy", command=lambda: self._copy_hover_text(self._hover_text_selection()))
        menu.add_command(label="Copy All", command=lambda: self._copy_hover_text(self._hover_text_content()))
        menu.add_command(label="Select All", command=self._select_all_hover_text)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass
        return "break"

    def copy_hover_selection_to_clipboard_if_pointer_inside(self) -> bool:
        win = self._hover_text_window
        if win is None:
            return False
        try:
            if not win.winfo_exists() or str(win.state()) == "withdrawn":
                return False
            px = int(win.winfo_pointerx())
            py = int(win.winfo_pointery())
            x = int(win.winfo_rootx())
            y = int(win.winfo_rooty())
            width = int(win.winfo_width() or win.winfo_reqwidth())
            height = int(win.winfo_height() or win.winfo_reqheight())
            if not (x <= px < x + width and y <= py < y + height):
                return False
        except Exception:
            return False
        return self._copy_hover_text(self._hover_text_selection())

    def hovered_subtitle_word_for_anki(self) -> str:
        """Return the exact visible subtitle token currently under the pointer."""
        return str(self.hovered_subtitle_target_for_anki().get("text") or "")

    def subtitle_target_at_pointer_for_anki(self) -> dict:
        """Hit-test the canvas at the live pointer location for an Anki action.

        This intentionally does not consult the delayed hover-panel state.  A
        recognizer callback can arrive after the panel has been redrawn or
        hidden, while the pointer still identifies the word the user chose.
        """
        try:
            x = int(self.canvas.winfo_pointerx() - self.canvas.winfo_rootx())
            y = int(self.canvas.winfo_pointery() - self.canvas.winfo_rooty())
            width = int(self.canvas.winfo_width())
            height = int(self.canvas.winfo_height())
        except Exception:
            return {"text": "", "snapshot": None, "spans": ()}
        if x < 0 or y < 0 or x >= width or y >= height:
            return {"text": "", "snapshot": None, "spans": ()}

        ruby_hit = self._region_at_point(self._hover_regions, x, y)
        word_hit = self._region_at_point(self._word_regions, x, y, anchor=ruby_hit)
        region = word_hit or ruby_hit
        if not isinstance(region, dict):
            return {"text": "", "snapshot": None, "spans": ()}
        line_index = region.get("line_index")
        spans = ()
        if line_index is not None:
            spans = (
                (
                    int(line_index),
                    int(region.get("char_start") or 0),
                    int(region.get("char_end") or 0),
                ),
            )
        return {
            "text": str(region.get("base") or "").strip(),
            "snapshot": getattr(self, "_cue_snapshot", None),
            "spans": spans,
            "token": region.get("cue_token"),
        }

    def hovered_subtitle_target_for_anki(self) -> dict:
        """Compatibility name for the live pointer Anki target."""
        return self.subtitle_target_at_pointer_for_anki()

    def subtitle_words_for_anki(self) -> List[str]:
        """Return visible base tokens in sentence order, excluding overlapping compounds."""
        return [str(region.get("base") or "").strip() for region in self._subtitle_word_regions_for_anki()]

    def _subtitle_word_regions_for_anki(self) -> List[dict]:
        regions: List[dict] = []
        seen = set()
        for region in self._word_regions:
            if not isinstance(region, dict) or bool(region.get("compound")):
                continue
            surface = str(region.get("base") or "").strip()
            if not surface:
                continue
            marker = (
                str(region.get("line_position") or ""),
                str(region.get("sentence_lookup") or ""),
                int(region.get("char_start") or 0),
                int(region.get("char_end") or 0),
                surface,
            )
            if marker in seen:
                continue
            seen.add(marker)
            regions.append(region)
        return regions

    def _subtitle_word_region_at_position_for_voice(self, position: int) -> dict:
        regions = self._subtitle_word_regions_for_anki()
        word_count = len(regions)
        try:
            position = int(position)
        except Exception:
            position = 0
        index = position - 1 if position > 0 else word_count + position
        if word_count <= 0:
            return {"ok": False, "reason": "no_words", "word_count": 0}
        if position == 0 or index < 0 or index >= word_count:
            return {
                "ok": False,
                "reason": "out_of_range",
                "position": position,
                "word_count": word_count,
            }
        region = regions[index]
        return {
            "ok": True,
            "region": region,
            "word": str(region.get("base") or "").strip(),
            "index": index + 1,
            "word_count": word_count,
        }

    def subtitle_word_at_position_for_voice(self, position: int) -> dict:
        """Resolve a subtitle token by 1-based or end-relative position without highlighting it."""
        result = self._subtitle_word_region_at_position_for_voice(position)
        region = result.pop("region", None)
        if isinstance(region, dict):
            line_index = region.get("line_index")
            result["snapshot"] = getattr(self, "_cue_snapshot", None)
            result["token"] = region.get("cue_token")
            result["spans"] = (
                (
                    int(line_index),
                    int(region.get("char_start") or 0),
                    int(region.get("char_end") or 0),
                ),
            ) if line_index is not None else ()
        return result

    def show_indexed_subtitle_word_layers_for_voice(self, position: int, layers) -> dict:
        """Show hover information for an indexed subtitle token without persisting a highlight."""
        result = self._subtitle_word_region_at_position_for_voice(position)
        if not bool(result.get("ok")):
            result.pop("region", None)
            return result

        layer_order = ("dictionary", "dictionary_together", "translation", "status")
        requested = tuple(layer for layer in layer_order if layer in set(layers or ()))
        if not requested:
            return {
                "ok": False,
                "reason": "no_layers",
                "word_count": int(result.get("word_count") or 0),
            }

        region = dict(result.pop("region"))
        if "status" in requested:
            region["database_check"] = True
            if region.get("cue_token") is not None:
                region["cue_tokens"] = (region.get("cue_token"),)
        region = self._region_with_combined_hover_anchor(region, [region])
        active_key = (
            "voice_indexed_word",
            self._voice_word_region_key(region),
            requested,
        )
        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        self._hover_active_region = active_key
        self._hover_active_mode = "voice_indexed_word"
        self._hover_active_translation_key = None
        self._show_hover_layers(region, requested)
        result["layers"] = list(requested)
        return result

    def show_hovered_subtitle_layers_for_voice(self, layers) -> dict:
        """Show voice lookup layers for the subtitle token under the pointer."""
        try:
            x = int(self.canvas.winfo_pointerx() - self.canvas.winfo_rootx())
            y = int(self.canvas.winfo_pointery() - self.canvas.winfo_rooty())
        except Exception:
            return {"ok": False, "reason": "no_hover", "words": []}
        try:
            width = int(self.canvas.winfo_width())
            height = int(self.canvas.winfo_height())
            if x < 0 or y < 0 or x >= width or y >= height:
                return {"ok": False, "reason": "no_hover", "words": []}
        except Exception:
            return {"ok": False, "reason": "no_hover", "words": []}
        ruby_hit = self._region_at_point(self._hover_regions, x, y)
        region = self._region_at_point(self._word_regions, x, y, anchor=ruby_hit) or ruby_hit
        if not isinstance(region, dict):
            return {"ok": False, "reason": "no_hover", "words": []}

        layer_order = ("dictionary", "dictionary_together", "translation", "status")
        requested = tuple(layer for layer in layer_order if layer in set(layers or ()))
        if not requested:
            return {"ok": False, "reason": "no_layers", "words": []}

        region = dict(region)
        if "status" in requested:
            region["database_check"] = True
            if region.get("cue_token") is not None:
                region["cue_tokens"] = (region.get("cue_token"),)
        region = self._region_with_combined_hover_anchor(region, [region])
        active_key = ("voice_hovered_word", self._voice_word_region_key(region), requested)
        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        self._hover_active_region = active_key
        self._hover_active_mode = "voice_hovered_word"
        self._hover_active_translation_key = None
        self._show_hover_layers(region, requested)
        return {
            "ok": True,
            "layers": list(requested),
            "words": [str(region.get("base") or "").strip()],
        }

    @staticmethod
    def _voice_word_region_key(region: dict) -> tuple:
        return (
            str(region.get("line_position") or ""),
            str(region.get("sentence_lookup") or ""),
            int(region.get("char_start") or 0),
            int(region.get("char_end") or 0),
            str(region.get("base") or "").strip(),
        )

    def _selected_voice_word_regions(self) -> list[tuple[int, dict]]:
        if not self._voice_selected_word_keys:
            return []
        selected_keys = set(self._voice_selected_word_keys)
        selected = [
            (index, region)
            for index, region in enumerate(self._subtitle_word_regions_for_anki())
            if self._voice_word_region_key(region) in selected_keys
        ]
        if len(selected) != len(selected_keys):
            self._voice_selected_word_keys = [
                self._voice_word_region_key(region)
                for _index, region in selected
            ]
        return selected

    def _draw_voice_word_highlights(self, regions: list[dict]) -> None:
        self.canvas.delete("voice_word_highlight")
        for region in regions:
            x1, y1, x2, y2 = region["bbox"]
            self.canvas.create_rectangle(
                x1 - 5,
                y1 + 1,
                x2 + 5,
                y2 - 1,
                fill="",
                outline="#7ed0ff",
                width=3,
                tags=("voice_word_highlight",),
            )
        self.canvas.tag_raise("voice_word_highlight")

    def _voice_action_indicator_enabled(self) -> bool:
        try:
            return self.config.get("VOICE_ACTION_INDICATOR_ENABLED") is not False
        except Exception:
            return True

    def _cancel_voice_action_indicator_jobs(self) -> None:
        for attr in ("_voice_action_indicator_job", "_voice_action_indicator_hide_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.canvas.after_cancel(job)
                except Exception:
                    pass
            setattr(self, attr, None)

    def _voice_action_indicator_center(self) -> tuple[int, int]:
        try:
            width = max(1, int(self.canvas.winfo_width() or self.canvas.winfo_reqwidth()))
            height = max(1, int(self.canvas.winfo_height() or self.canvas.winfo_reqheight()))
        except Exception:
            width, height = 100, 60
        return max(14, width - 18), max(14, height - 18)

    def _draw_voice_action_indicator(self) -> None:
        try:
            self.canvas.delete("voice_action_indicator")
            x, y = self._voice_action_indicator_center()
            if self._voice_action_indicator_state == "working":
                radius = 8
                self.canvas.create_arc(
                    x - radius,
                    y - radius,
                    x + radius,
                    y + radius,
                    start=int(self._voice_action_indicator_phase),
                    extent=255,
                    style="arc",
                    outline="#7ed0ff",
                    width=3,
                    tags=("voice_action_indicator",),
                )
            elif self._voice_action_indicator_state == "success":
                self.canvas.create_text(
                    x,
                    y,
                    text="\u2713",
                    fill="#62d68a",
                    font=("Arial", 14, "bold"),
                    tags=("voice_action_indicator",),
                )
            self.canvas.tag_raise("voice_action_indicator")
        except Exception:
            pass

    def set_voice_action_indicator(self, state: str) -> None:
        """Show a spinner, a one-second success check, or clear action feedback."""
        self._voice_action_indicator_generation += 1
        generation = self._voice_action_indicator_generation
        self._cancel_voice_action_indicator_jobs()
        normalized = str(state or "idle").strip().lower()
        if not self._voice_action_indicator_enabled() or normalized not in {"working", "success"}:
            self._voice_action_indicator_state = "idle"
            try:
                self.canvas.delete("voice_action_indicator")
            except Exception:
                pass
            return

        self._voice_action_indicator_state = normalized
        if normalized == "working":
            self._voice_action_indicator_phase = 0

            def animate() -> None:
                if generation != self._voice_action_indicator_generation:
                    return
                self._voice_action_indicator_phase = (self._voice_action_indicator_phase - 32) % 360
                self._draw_voice_action_indicator()
                try:
                    self._voice_action_indicator_job = self.canvas.after(80, animate)
                except Exception:
                    self._voice_action_indicator_job = None

            animate()
            return

        self._draw_voice_action_indicator()

        def clear_success() -> None:
            if generation == self._voice_action_indicator_generation:
                self.set_voice_action_indicator("idle")

        try:
            self._voice_action_indicator_hide_job = self.canvas.after(1000, clear_success)
        except Exception:
            self._voice_action_indicator_hide_job = None

    def abort_voice_action_feedback(self) -> None:
        self._voice_translation_each_generation += 1
        self._advance_hover_generation()
        self._cancel_hover_layer_job()
        self._hover_active_region = None
        self._hover_active_mode = None
        self._hover_active_translation_key = None
        self._hide_hover_text_window()
        self.set_voice_action_indicator("idle")

    def _restore_voice_word_highlights(self) -> None:
        try:
            selected = self._selected_voice_word_regions()
            if not selected:
                self._voice_selected_word_keys.clear()
            self._draw_voice_word_highlights([region for _index, region in selected])
        except Exception:
            logger.exception("Failed to restore voice subtitle-word highlights")

    def highlight_subtitle_words_for_voice(self, positions: list[int]) -> dict:
        """Replace the voice selection with 1-based or end-relative token positions."""
        regions = self._subtitle_word_regions_for_anki()
        word_count = len(regions)
        if word_count <= 0:
            return {"ok": False, "reason": "no_words", "word_count": 0}

        selected_indexes: list[int] = []
        for raw_position in positions or []:
            position = int(raw_position)
            index = position - 1 if position > 0 else word_count + position
            if index < 0 or index >= word_count:
                return {
                    "ok": False,
                    "reason": "out_of_range",
                    "word_count": word_count,
                    "position": position,
                }
            if index not in selected_indexes:
                selected_indexes.append(index)
        if not selected_indexes:
            return {"ok": False, "reason": "empty", "word_count": word_count}

        selected_indexes.sort()
        selected_regions = [regions[index] for index in selected_indexes]
        self._voice_selected_word_keys = [self._voice_word_region_key(region) for region in selected_regions]
        try:
            self._draw_voice_word_highlights(selected_regions)
        except Exception:
            logger.exception("Failed to draw voice subtitle-word highlights")
            return {"ok": False, "reason": "draw_failed", "word_count": word_count}
        words = [str(region.get("base") or "").strip() for region in selected_regions]
        return {
            "ok": True,
            "words": words,
            "indexes": [index + 1 for index in selected_indexes],
            "word_count": word_count,
        }

    def highlight_subtitle_word_for_voice(self, position: int) -> dict:
        """Highlight one selectable subtitle token by a 1-based or end-relative position."""
        result = self.highlight_subtitle_words_for_voice([int(position)])
        if result.get("ok"):
            result["word"] = result["words"][0]
            result["index"] = result["indexes"][0]
        return result

    def modify_subtitle_word_highlights_for_voice(self, positions: list[int], *, add: bool) -> dict:
        """Add or remove indexed tokens without replacing the current voice selection."""
        regions = self._subtitle_word_regions_for_anki()
        word_count = len(regions)
        if word_count <= 0:
            return {"ok": False, "reason": "no_words", "word_count": 0}

        target_indexes: list[int] = []
        for raw_position in positions or []:
            position = int(raw_position)
            index = position - 1 if position > 0 else word_count + position
            if index < 0 or index >= word_count:
                return {
                    "ok": False,
                    "reason": "out_of_range",
                    "word_count": word_count,
                    "position": position,
                }
            if index not in target_indexes:
                target_indexes.append(index)
        if not target_indexes:
            return {"ok": False, "reason": "empty", "word_count": word_count}

        current_keys = list(self._voice_selected_word_keys)
        target_keys = [self._voice_word_region_key(regions[index]) for index in target_indexes]
        if add:
            for key in target_keys:
                if key not in current_keys:
                    current_keys.append(key)
        else:
            removed = set(target_keys)
            current_keys = [key for key in current_keys if key not in removed]
        changed = current_keys != self._voice_selected_word_keys
        self._voice_selected_word_keys = current_keys

        selected = self._selected_voice_word_regions()
        self._draw_voice_word_highlights([region for _index, region in selected])
        if not selected and self._hover_active_mode == "voice_highlight":
            self._clear_hover_ruby()
        return {
            "ok": True,
            "changed": changed,
            "operation": "add" if add else "remove",
            "indexes": [index + 1 for index in target_indexes],
            "words": [str(regions[index].get("base") or "").strip() for index in target_indexes],
            "selected_count": len(selected),
            "word_count": word_count,
        }

    def _populate_multi_voice_layer_text(
        self,
        anchor: dict,
        regions: list[dict],
        words: list[str],
        layers: tuple[str, ...],
    ) -> None:
        if len(regions) <= 1:
            return
        if "dictionary" in layers:
            dictionary_parts = []
            for word, region in zip(words, regions):
                result = self._hover_dictionary_text(region).lstrip("| ").strip()
                dictionary_parts.append(result or f"{word} — No dictionary entry")
            anchor["multi_dictionary_text"] = "\n".join(dictionary_parts)
        if "status" in layers:
            status_parts = []
            for word, region in zip(words, regions):
                result = self._hover_status_text(region) or "Not in database"
                status_parts.append(f"{word}: {result}" if word else result)
            anchor["multi_status_text"] = "\n".join(status_parts)

    def show_voice_highlight_layers(self, layers) -> dict:
        """Show requested hover layers for the current voice-highlighted words."""
        layer_order = ("dictionary", "dictionary_together", "translation", "status")
        requested = tuple(layer for layer in layer_order if layer in set(layers or ()))
        if not requested:
            return {"ok": False, "reason": "no_layers", "words": []}

        selected = self._selected_voice_word_regions()
        if not selected:
            return {"ok": False, "reason": "no_highlight", "words": []}

        regions = [region for _index, region in selected]
        words = [str(region.get("base") or "").strip() for region in regions]
        anchor = self._region_with_combined_hover_anchor(regions[-1], regions)
        selection = self.highlighted_subtitle_selection_for_anki()
        phrase = str(selection.get("text") or "").strip() or " ".join(words).strip()
        if len(regions) > 1 and phrase:
            anchor["base"] = phrase
            anchor["lookup"] = phrase
        if "status" in requested:
            anchor["database_check"] = True
            anchor["cue_tokens"] = tuple(
                region.get("cue_token")
                for region in regions
                if region.get("cue_token") is not None
            )

        multi_layers = tuple(layer for layer in requested if not (layer == "status" and bool(anchor.get("database_check"))))
        self._populate_multi_voice_layer_text(anchor, regions, words, multi_layers)

        active_key = (
            "voice_highlight",
            tuple(self._voice_word_region_key(region) for region in regions),
            requested,
        )
        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        self._hover_active_region = active_key
        self._hover_active_mode = "voice_highlight"
        self._hover_active_translation_key = None
        self._show_hover_layers(anchor, requested)
        return {"ok": True, "layers": list(requested), "words": words}

    def show_voice_highlight_translations_individually(self, layers=()) -> dict:
        """Show per-word translations, optionally combined with other hover layers."""
        requested_set = {str(layer or "").strip() for layer in (layers or ())}
        requested_set.discard("translation")
        requested_set.add("translation_each")
        requested = tuple(
            layer
            for layer in ("dictionary", "dictionary_together", "translation_each", "status")
            if layer in requested_set
        )
        selected = self._selected_voice_word_regions()
        if not selected:
            return {"ok": False, "reason": "no_highlight", "words": []}

        lookup = getattr(self, "_translation_lookup", None)
        if not callable(lookup):
            return {"ok": False, "reason": "unavailable", "words": []}

        regions = [region for _index, region in selected]
        words = [str(region.get("base") or "").strip() for region in regions]
        provider = self._translation_provider()
        entries = [
            (
                self._voice_word_label(region),
                str(region.get("base") or region.get("lookup") or "").strip(),
            )
            for region in regions
        ]
        entries = [(word, query) for word, query in entries if word and query]
        if not entries:
            return {"ok": False, "reason": "unavailable", "words": words}

        if not hasattr(self, "_translation_hover_cache"):
            self._translation_hover_cache = {}
        self._voice_translation_each_generation = int(
            getattr(self, "_voice_translation_each_generation", 0) or 0
        ) + 1
        generation = self._voice_translation_each_generation
        active_key = (
            "voice_highlight_translation_each",
            tuple(self._voice_word_region_key(region) for region in regions),
            requested,
        )
        anchor = self._region_with_combined_hover_anchor(regions[-1], regions)
        selection = self.highlighted_subtitle_selection_for_anki()
        phrase = str(selection.get("text") or "").strip() or " ".join(words).strip()
        if len(regions) > 1 and phrase:
            anchor["base"] = phrase
            anchor["lookup"] = phrase
        if "status" in requested:
            anchor["database_check"] = True
            anchor["cue_tokens"] = tuple(
                region.get("cue_token")
                for region in regions
                if region.get("cue_token") is not None
            )
        multi_layers = tuple(layer for layer in requested if not (layer == "status" and bool(anchor.get("database_check"))))
        self._populate_multi_voice_layer_text(anchor, regions, words, multi_layers)

        def render_current() -> None:
            if generation != self._voice_translation_each_generation:
                return
            if self._hover_active_region != active_key:
                return
            lines = []
            for word, query in entries:
                cache_key = (provider, query)
                if cache_key not in self._translation_hover_cache:
                    translation = "Translating..."
                else:
                    translation = str(self._translation_hover_cache.get(cache_key) or "No translation")
                lines.append(f"{word} — {translation}")
            anchor["multi_translation_text"] = "\n".join(lines)
            self._show_hover_layers(anchor, requested)

        self._cancel_hover_clear()
        self._cancel_hover_layer_job()
        self._hover_active_region = active_key
        self._hover_active_mode = "voice_highlight"
        self._hover_active_translation_key = None
        render_current()

        missing = []
        missing_keys = set()
        for _word, query in entries:
            cache_key = (provider, query)
            if cache_key in self._translation_hover_cache or cache_key in missing_keys:
                continue
            missing.append((query, cache_key))
            missing_keys.add(cache_key)
        if missing:
            def worker() -> None:
                for query, cache_key in missing:
                    try:
                        value = clean_hover_translation(query, str(lookup(query, provider=provider) or ""))
                    except TypeError:
                        try:
                            value = clean_hover_translation(query, str(lookup(query) or ""))
                        except Exception:
                            value = ""
                    except Exception:
                        value = ""

                    def apply_result(key=cache_key, translated=value) -> None:
                        if generation != self._voice_translation_each_generation:
                            return
                        self._translation_hover_cache[key] = translated
                        if len(self._translation_hover_cache) > 256:
                            self._translation_hover_cache.pop(next(iter(self._translation_hover_cache)), None)
                        render_current()

                    dispatch_to_tk(self.canvas, apply_result)

            threading.Thread(target=worker, daemon=True).start()

        return {"ok": True, "layers": list(requested), "words": words}

    def clear_voice_word_highlights(self) -> int:
        count = len(self._voice_selected_word_keys)
        self._voice_selected_word_keys.clear()
        try:
            self.canvas.delete("voice_word_highlight")
        except Exception:
            pass
        if self._hover_active_mode == "voice_highlight":
            self._clear_hover_ruby()
        return count

    def highlighted_subtitle_selection_for_anki(self) -> dict:
        """Return selected token surfaces and a sentence-faithful combined selection."""
        selected = self._selected_voice_word_regions()
        if not selected:
            return {"text": "", "words": [], "indexes": []}

        groups: list[list[tuple[int, dict]]] = []
        for item in selected:
            if not groups:
                groups.append([item])
                continue
            previous_index, previous = groups[-1][-1]
            index, region = item
            same_line = (
                str(previous.get("line_position") or "") == str(region.get("line_position") or "")
                and str(previous.get("sentence_lookup") or "") == str(region.get("sentence_lookup") or "")
            )
            if same_line and index == previous_index + 1:
                groups[-1].append(item)
            else:
                groups.append([item])

        group_texts: list[str] = []
        for group in groups:
            first = group[0][1]
            last = group[-1][1]
            source = str(first.get("sentence_lookup") or "")
            start = int(first.get("char_start") or 0)
            end = int(last.get("char_end") or start)
            text = source[start:end].strip() if source and end > start else ""
            if not text:
                text = "".join(str(region.get("base") or "") for _index, region in group).strip()
            if text:
                group_texts.append(text)

        return {
            "text": " ".join(group_texts).strip(),
            "words": [str(region.get("base") or "").strip() for _index, region in selected],
            "indexes": [index + 1 for index, _region in selected],
            "spans": tuple(
                (
                    int(region.get("line_index")),
                    int(region.get("char_start") or 0),
                    int(region.get("char_end") or 0),
                )
                for _index, region in selected
                if region.get("line_index") is not None
            ),
            "snapshot": getattr(self, "_cue_snapshot", None),
        }

    @staticmethod
    def _voice_word_label(region: dict) -> str:
        word = str(region.get("base") or "").strip()
        ruby = str(region.get("ruby") or "").strip()
        if ruby and ruby != word:
            return f"{word} [{ruby}]"
        return word

    def voice_copy_text(self, kind: str = "active") -> dict:
        """Resolve explicit voice-copy targets without depending on pointer location."""
        normalized = str(kind or "active").strip().lower()
        if normalized == "active":
            for name in ("translation", "dictionary", "status"):
                value = str(self._hover_copy_values.get(name) or "").strip()
                if value:
                    return {"ok": True, "text": value, "kind": name}
            return {"ok": False, "reason": "no_active_result"}

        selected = self._selected_voice_word_regions()
        if not selected:
            return {"ok": False, "reason": "no_highlight"}
        regions = [region for _index, region in selected]
        words = [str(region.get("base") or "").strip() for region in regions]
        selection = self.highlighted_subtitle_selection_for_anki()
        phrase = str(selection.get("text") or "").strip() or " ".join(words).strip()

        if normalized == "kanji":
            return {"ok": bool(phrase), "text": phrase, "kind": normalized}
        if normalized == "kanji_each":
            text = "\n".join(word for word in words if word)
            return {"ok": bool(text), "text": text, "kind": normalized}
        if normalized == "dictionary":
            values = [self._hover_dictionary_text(region) for region in regions]
            text = "\n".join(value for value in values if value)
            return {"ok": bool(text), "text": text, "kind": normalized, "reason": "no_dictionary" if not text else ""}
        if normalized == "dictionary_together":
            anchor = dict(regions[-1])
            anchor["base"] = phrase
            anchor["lookup"] = phrase
            text = self._hover_dictionary_text(anchor)
            return {"ok": bool(text), "text": text, "kind": normalized, "reason": "no_dictionary" if not text else ""}

        provider = self._translation_provider()
        if normalized == "translation":
            text = str(self._translation_hover_cache.get((provider, phrase)) or "").strip()
            return {"ok": bool(text), "text": text, "kind": normalized, "reason": "translation_not_ready" if not text else ""}
        if normalized == "translation_each":
            lines = []
            for region in regions:
                query = str(region.get("base") or region.get("lookup") or "").strip()
                translated = str(self._translation_hover_cache.get((provider, query)) or "").strip()
                if not translated:
                    return {"ok": False, "reason": "translation_not_ready", "kind": normalized}
                lines.append(f"{self._voice_word_label(region)} - {translated}")
            text = "\n".join(lines)
            return {"ok": bool(text), "text": text, "kind": normalized}
        return {"ok": False, "reason": "unsupported", "kind": normalized}

    @classmethod
    def _measure_hover_line(
        cls,
        text: str,
        body_font: tkFont.Font,
        heading_font: tkFont.Font,
    ) -> int:
        line = str(text or "")
        for heading in cls._HOVER_TEXT_HEADINGS:
            if line.startswith(heading):
                return int(heading_font.measure(heading) + body_font.measure(line[len(heading):]))
        return int(body_font.measure(line))

    @classmethod
    def _wrap_hover_text_lines(
        cls,
        text: str,
        body_font: tkFont.Font,
        heading_font: tkFont.Font,
        max_width_px: int,
    ) -> list[str]:
        wrapped: list[str] = []
        for raw_line in str(text or "").splitlines() or [""]:
            if not raw_line:
                wrapped.append("")
                continue
            remaining = raw_line
            while remaining:
                if cls._measure_hover_line(remaining, body_font, heading_font) <= int(max_width_px):
                    wrapped.append(remaining.rstrip())
                    break

                fit = 1
                for end in range(2, len(remaining) + 1):
                    candidate = remaining[:end]
                    if cls._measure_hover_line(candidate, body_font, heading_font) > int(max_width_px):
                        break
                    fit = end

                candidate = remaining[:fit]
                split_at = max(candidate.rfind(" "), candidate.rfind("\t"))
                if split_at > 0:
                    line = candidate[:split_at].rstrip()
                    consumed = split_at + 1
                else:
                    line = candidate.rstrip()
                    consumed = fit
                if not line:
                    line = remaining[:fit]
                    consumed = fit
                wrapped.append(line)
                remaining = remaining[consumed:].lstrip(" \t")
        return wrapped or [""]

    def _clamp_hover_position(self, x: int, y: int, w: int, h: int) -> tuple[int, int]:
        try:
            anchor_x = int(self.canvas.winfo_rootx()) + max(1, int(self.canvas.winfo_width())) // 2
            anchor_y = int(self.canvas.winfo_rooty()) + max(1, int(self.canvas.winfo_height())) // 2
            rects = list(get_monitor_rects(self.canvas) or [])
        except Exception:
            anchor_x = int(x) + max(1, int(w)) // 2
            anchor_y = int(y) + max(1, int(h)) // 2
            rects = []

        monitor = None
        for rect in rects:
            rx, ry, rw, rh = rect
            if rx <= anchor_x < rx + rw and ry <= anchor_y < ry + rh:
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

    def _hover_max_width_px(self) -> int:
        """Keep hover text readable on the monitor that owns the overlay."""
        try:
            anchor_x = int(self.canvas.winfo_rootx()) + max(1, int(self.canvas.winfo_width())) // 2
            anchor_y = int(self.canvas.winfo_rooty()) + max(1, int(self.canvas.winfo_height())) // 2
            rects = list(get_monitor_rects(self.canvas) or [])
            for x, y, width, height in rects:
                if x <= anchor_x < x + width and y <= anchor_y < y + height:
                    return max(160, min(self._HOVER_TEXT_MAX_WIDTH_PX, int(width) - 32))
        except Exception:
            pass
        return self._HOVER_TEXT_MAX_WIDTH_PX

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
        if self._hover_suspended or self._hover_requires_fresh_motion:
            return
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
        self._advance_hover_generation()
        self._hover_active_region = None
        self._hover_active_mode = None
        self._handle_hover_motion(
            type("_SubtitleHoverMotion", (), {"x": x, "y": y})(),
            actual_motion=False,
        )

    def bind_dictionary_lookup(self, callback) -> None:
        self._dictionary_lookup = callback

    def bind_translation_lookup(self, callback) -> None:
        self._translation_lookup = callback

    def bind_translation_provider(self, callback) -> None:
        self._translation_provider_callback = callback

    def bind_word_tokenizer(self, callback) -> None:
        self._word_tokenizer = callback

    def bind_shift_state(self, callback) -> None:
        self._shift_state_callback = callback

    def bind_hover_mode(self, callback) -> None:
        self._hover_mode_callback = callback

    def bind_annotation_provider(self, provider) -> None:
        self._annotation_provider = provider
        self._layout_cache.clear()
        self._wrap_cache.clear()
        self._annotation_segment_cache.clear()
        self._clear_preview_render_cache(delete_items=True)

    def clear_cue_snapshot(self) -> None:
        self._hover_cue_generation += 1
        self._clear_hover_ruby()
        self._cue_snapshot = None
        self._hover_regions = []
        self._word_regions = []
        self._voice_selected_word_keys.clear()

    @staticmethod
    def _contains_kanji(text: str) -> bool:
        for ch in text or "":
            code = ord(ch)
            if 0x4E00 <= code <= 0x9FFF or code == 0x3005:
                return True
        return False

    @staticmethod
    def _contains_katakana(text: str) -> bool:
        for ch in text or "":
            code = ord(ch)
            if 0x30A1 <= code <= 0x30FA or 0x30FD <= code <= 0x30FF:
                return True
        return False

    @classmethod
    def _has_hoverable_ruby_base(cls, text: str) -> bool:
        return cls._contains_kanji(text) or cls._contains_katakana(text)

    def update_canvas(self, canvas: tk.Canvas):
        """
        Switch the renderer to a different canvas (after overlay update).
        """
        self._hover_cue_generation += 1
        self._clear_hover_ruby()
        self.destroy_hover_windows()
        if canvas is not self.canvas:
            self._clear_preview_render_cache(delete_items=True)
            self._hover_drag_overlay = None
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
