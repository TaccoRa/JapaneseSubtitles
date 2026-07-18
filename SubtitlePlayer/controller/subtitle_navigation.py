"""Subtitle time display, slider sync, and subtitle redraw helper."""

import bisect
import logging
import threading
import tkinter as tk
import re
import time
from typing import Any
from utils import dispatch_to_tk, format_time, parse_time_value

logger = logging.getLogger(__name__)

class _ControllerProxy:
    """Proxy base that forwards attribute access and assignment to SubtitleController."""

    def __init__(self, controller: Any) -> None:
        object.__setattr__(self, "controller", controller)

    def __getattr__(self, name: str):
        return getattr(self.controller, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "controller":
            object.__setattr__(self, name, value)
        else:
            setattr(self.controller, name, value)

class SubtitleNavigationController(_ControllerProxy):
    """Subtitle time display, slider sync, and subtitle redraw helper."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def get_offset_value(self) -> float: return float(self.settings._last_offset_value)
    
    @staticmethod
    def _format_delta_seconds(value: float) -> str:
        text = f"{abs(float(value)):.2f}".rstrip("0").rstrip(".")
        return text if text else "0"

    def on_set_to_return(self, text: str):
        self.settto_editing = False
        text = (text or "").strip()

        if not text or text.lower() == "unused":
            self.settings.setto_var.set("")
            try:
                self.settings.setto_entry.delete(0, tk.END)
            except Exception:
                pass
            self._release_time_entry_focus()
            return

        if not re.fullmatch(r"[\d:.]+", text):
            self.settings.setto_var.set("")
            try:
                self.settings.setto_entry.delete(0, tk.END)
            except Exception:
                pass
            self._release_time_entry_focus()
            return

        try:
            secs = parse_time_value(text)
        except Exception:
            self.settings.setto_var.set("")
            try:
                self.settings.setto_entry.delete(0, tk.END)
            except Exception:
                pass
            self._release_time_entry_focus()
            return

        self.playback.set_current_time(secs)
        self.settings.setto_var.set("")
        try:
            self.settings.setto_entry.delete(0, tk.END)
        except Exception:
            pass
        self._release_time_entry_focus()

    def _release_time_entry_focus(self) -> None:
        try:
            focused = self.settings.control_window.focus_get()
        except Exception as e:
            logger.debug("Failed to read focused widget: %s", e, exc_info=True)
            focused = None
        try:
            time_entry = self.settings.time_entry
        except Exception as e:
            logger.debug("Failed to read time entry widget: %s", e, exc_info=True)
            time_entry = None
        if focused is not time_entry:
            return
        target = getattr(self.overlay, "sub_window", None)
        if target is not None and target.winfo_exists():
            target.focus_force()
            return
        try:
            self.settings.control_window.after_idle(
                lambda: self.settings.control_window.tk.call("focus", "")
            )
            return
        except Exception as e:
            logger.debug("Failed to release time entry focus: %s", e, exc_info=True)
            pass
        self.settings.control_window.focus_set()

    def control_time_entry_return(self, event):
        self.entry_editing  = False
        text = self.settings.control_time_str.get().strip()
        if not re.fullmatch(r"[\d:.]+", text):
            self.settings.control_time_str.set(format_time(self.current_time))
            self._release_time_entry_focus()
            return
        new_time = parse_time_value(text)
        self.playback.set_current_time(new_time)
        self._release_time_entry_focus()

    def control_clear_time_entry(self, event):
        if self.playing:
            self.playback.toggle_play()
        self.entry_editing  = True
        event.widget.delete(0, tk.END)

    def _time_display_text(self, value: float | None = None, *, include_pending: bool = True) -> str:
        text = format_time(self.current_time if value is None else value)
        pending = float(getattr(self, "_pending_seek_delta", 0.0) or 0.0) if include_pending else 0.0
        if abs(pending) >= 0.001:
            sign = "+" if pending > 0 else "-"
            text = f"{text} ({sign}{self._format_delta_seconds(pending)}s)"
        return text

    def _publish_time_display(self, text: str) -> None:
        position_scheduled = False
        if text != getattr(self, "_last_time_overlay_text", None):
            setter = getattr(self.settings, "set_time_overlay_text", None)
            if callable(setter):
                setter(text)
                position_scheduled = True
            else:
                self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
            self._last_time_overlay_text = text
        if not position_scheduled:
            schedule_position = getattr(self.settings, "_schedule_time_overlay_position_update", None)
            if callable(schedule_position):
                schedule_position()
            else:
                self.settings.update_time_overlay_position()
        if not self.entry_editing and not self.slider_dragging:
            try:
                current_text = self.settings.control_time_str.get()
            except Exception:
                current_text = None
            if current_text != text:
                self.settings.control_time_str.set(text)

    def update_time_display(self) -> None:
        self._publish_time_display(self._time_display_text())

    def update_time_and_subtitle_displays(self):#updates settings time overlay and control window entry
        self.update_time_display()
        self._update_subtitle_display()

    def refresh_after_offset_change(self) -> None:
        self.update_time_display()
        self._update_subtitle_display(force=True)

    def _display_start_times(self):
        getter = getattr(self.controller, "_get_display_start_times", None)
        if callable(getter):
            return getter()
        return getattr(self.sub_manager, "display_start_times", None) or [
            item[1] for item in getattr(self.sub_manager, "display_data", []) or []
        ]

    def _display_end_times(self):
        getter = getattr(self.controller, "_get_display_end_times", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                logger.debug("Failed to read display end times from controller", exc_info=True)
        return getattr(self.sub_manager, "display_end_times", None) or []

    def _display_index_at_time(self, sub_t: float) -> int | None:
        start_times = self._display_start_times()
        if not start_times:
            return None
        idx = bisect.bisect_right(start_times, sub_t) - 1
        if idx < 0:
            return None

        end_times = self._display_end_times()
        if idx < len(end_times):
            try:
                end_time = float(end_times[idx])
            except Exception:
                end_time = None
            if end_time is not None and float(sub_t) >= end_time - 0.0005:
                return None
        return idx

    def _copy_text_for_display_index(self, idx: int | None) -> str:
        if idx is None:
            return ""
        try:
            clean, _, top, bottom = self.sub_manager.display_data[int(idx)]
        except Exception:
            return ""
        return self.segments_to_copy_text(top, bottom) or str(clean or "")

    def copy_text_for_current_subtitle(self) -> str:
        idx = None
        if not bool(getattr(self, "subtitle_deleted", False)):
            try:
                idx = int(getattr(self, "last_rendered_index"))
            except Exception:
                idx = None
        text = self._copy_text_for_display_index(idx)
        if text:
            self.last_subtitle_raw = text
            return text

        try:
            sub_t = float(self.current_time) - float(self.settings._last_offset_value)
        except Exception:
            sub_t = 0.0
        idx = self._display_index_at_time(sub_t)
        if idx is None:
            return ""
        text = self._copy_text_for_display_index(idx)
        if text:
            self.last_subtitle_raw = text
            return text
        return ""

    def line_segments_for_current_subtitle(self):
        idx = None
        if not bool(getattr(self, "subtitle_deleted", False)):
            try:
                idx = int(getattr(self, "last_rendered_index"))
            except Exception:
                idx = None
        if idx is None:
            try:
                sub_t = float(self.current_time) - float(self.settings._last_offset_value)
            except Exception:
                sub_t = 0.0
            idx = self._display_index_at_time(sub_t)
        if idx is None:
            return []
        try:
            _clean, _start, top, bottom = self.sub_manager.display_data[int(idx)]
        except Exception:
            return []
        return [list(segments) for segments in (top, bottom) if segments]

    def _slider_preview_needs_render(self, value: float) -> bool:
        try:
            sub_t = float(value) - float(self.settings._last_offset_value)
        except Exception:
            return True
        try:
            if sub_t < 0 or sub_t > float(self.total_duration):
                idx = None
            else:
                idx = self._display_index_at_time(sub_t)
        except Exception:
            return True

        if idx is None:
            return not (
                self.last_rendered_index is None
                and bool(getattr(self, "subtitle_deleted", False))
                and not self.last_subtitle_text
            )

        if bool(getattr(self, "subtitle_deleted", False)):
            return True
        if idx != getattr(self, "last_rendered_index", None):
            return True
        return False

    def _update_subtitle_display(
        self,
        force: bool = False,
        allow_auto_ruby: bool = True,
        schedule_auto_ruby: bool = False,
        preview: bool = False,
    ):
        render_start = time.perf_counter()
        offset = self.settings._last_offset_value
        sub_t = self.current_time - offset
        if getattr(self, "_defer_auto_ruby_once", False):
            self._defer_auto_ruby_once = False
            allow_auto_ruby = False
            schedule_auto_ruby = True

        if sub_t < 0 or sub_t > self.total_duration:
            self.last_rendered_index = None
            self.last_subtitle_text = ""
            self._reset_canvas()
            return

        idx = self._display_index_at_time(sub_t)
        if idx is None:
            self.last_rendered_index = None
            self.last_subtitle_text = ""
            self._reset_canvas()
            return

        clean, _, top, bottom = self.sub_manager.display_data[idx]
        copy_text = self.segments_to_copy_text(top, bottom) or clean
        self.last_subtitle_raw = copy_text

        if getattr(self, "subtitles_user_hidden", False):
            hidden_idx = getattr(self, "subtitles_hidden_until_index", None)
            try:
                same_hidden_cue = hidden_idx is not None and int(hidden_idx) == int(idx)
            except Exception:
                same_hidden_cue = False
            if not same_hidden_cue:
                self.subtitles_user_hidden = False
                self.subtitles_hidden_until_index = None
                self._show_subtitle_overlay_window()
                self.last_subtitle_text = ""
            else:
                if self.subtitle_timeout_job:
                    try:
                        self.overlay.root.after_cancel(self.subtitle_timeout_job)
                    except Exception:
                        pass
                    self.subtitle_timeout_job = None
                self._reset_canvas()
                return

        # KEEP HIDDEN UNTIL SUBTITLE CHANGES
        if (
            not force
            and self.subtitle_deleted
            and idx == self.last_rendered_index
            and copy_text == self.last_subtitle_text
        ):
            return

        if (
            not force
            and not self.subtitle_deleted
            and idx == self.last_rendered_index
            and copy_text == self.last_subtitle_text
        ):
            return

        if allow_auto_ruby:
            self.sub_manager.ensure_auto_ruby_for_index(idx)

            clean, _, top, bottom = self.sub_manager.display_data[idx]
            copy_text = self.segments_to_copy_text(top, bottom) or clean
            self.last_subtitle_raw = copy_text
        elif schedule_auto_ruby and self._subtitle_needs_auto_ruby(idx):
            self._schedule_auto_ruby_refresh(idx)

        if self.subtitle_timeout_job and not preview:
            self.overlay.root.after_cancel(self.subtitle_timeout_job)
            self.subtitle_timeout_job = None

        if not preview:
            self._ensure_overlay_width_for_display_lines(top, bottom)
        self.renderer.update_canvas(self.overlay.subtitle_canvas)

        self.last_subtitle_text = copy_text
        self.last_rendered_index = idx
        self.subtitle_deleted = False

        self.renderer.render_subtitle(top, bottom, self.overlay, preview=preview)
        try:
            self._record_perf_sample("subtitle_render", (time.perf_counter() - render_start) * 1000.0)
        except Exception:
            pass

        if not preview:
            self.subtitle_timeout_job = self.overlay.root.after(
                self.hide_subtitles_ms,
                self._hide_subtitles_temporarily
            )

    def _ensure_overlay_width_for_display_lines(self, top, bottom) -> None:
        """Grow the overlay when lazy ruby makes the current cue wider than the cached size."""
        calculator = getattr(self.sub_manager, "calculate_geometry_for_display_lines", None)
        updater = getattr(self.overlay, "update_geometry", None)
        if not callable(calculator) or not callable(updater):
            return

        try:
            target_w, target_h = calculator(top, bottom)
            target_w = int(target_w)
            target_h = int(target_h)
        except Exception:
            logger.debug("Failed to measure current subtitle geometry", exc_info=True)
            return

        try:
            current_w = int(getattr(self.overlay, "max_w", 0) or 0)
            current_h = int(getattr(self.overlay, "max_h", 0) or 0)
        except Exception:
            current_w = 0
            current_h = 0

        if target_w <= current_w:
            return

        try:
            updater(target_w, max(current_h, target_h))
        except Exception:
            logger.debug("Failed to grow subtitle overlay for current subtitle", exc_info=True)

    def _show_subtitle_overlay_window(self) -> None:
        try:
            show = getattr(self.overlay, "show", None)
            if callable(show):
                show()
                return
        except Exception:
            pass
        try:
            self.overlay.sub_window.deiconify()
            self.overlay.sub_window.lift()
            self.overlay.sub_window.attributes("-topmost", True)
        except Exception:
            pass

    def _hide_subtitle_overlay_window(self) -> None:
        try:
            if hasattr(self.renderer, "destroy_hover_windows"):
                self.renderer.destroy_hover_windows()
        except Exception:
            pass
        try:
            self.overlay.sub_window.withdraw()
        except Exception:
            pass
        try:
            self.overlay.hide_handle()
        except Exception:
            pass
    
    @staticmethod
    def segments_to_copy_text(top_segments, bottom_segments) -> str:
        def _starts_with_kanji(text: str) -> bool:
            if not text:
                return False
            code = ord(text[0])
            return (
                code == 0x3005
                or 0x3400 <= code <= 0x4DBF
                or 0x4E00 <= code <= 0x9FFF
                or 0xF900 <= code <= 0xFAFF
            )

        def _line_text(segments) -> str:
            out = []
            for base, ruby in segments or []:
                if ruby:
                    if (
                        _starts_with_kanji(str(base or ""))
                        and out
                        and not out[-1].endswith((" ", "\n", "\t"))
                        and not out[-1].endswith(("[", "(", "\uff08", "{", "\uff5b", "<", "\uff1c", "\u300c", "\u300e", "\u3010"))
                    ):
                        out.append(" ")
                    out.append(f"{base}[{ruby}]")
                else:
                    out.append(str(base or ""))
            return "".join(out).strip()

        lines = []
        for segments in (top_segments, bottom_segments):
            text = _line_text(segments)
            if text:
                lines.append(text)
        return "\n".join(lines)
    
    def _reset_canvas(self):
        if self.subtitle_deleted and self.last_rendered_index is None and not self.last_subtitle_text:
            return
        self.renderer.canvas.delete("all")
        self.last_rendered_index = None
        self.last_subtitle_text = ""
        self.subtitle_deleted = True

    def _subtitle_needs_auto_ruby(self, idx: int) -> bool:
        try:
            if not bool(self.sub_manager._auto_ruby_enabled()):
                return False
        except Exception:
            return False
        ready = getattr(self.sub_manager, "_auto_ruby_ready_indices", None)
        if ready is not None and int(idx) in ready:
            return False
        return True

    def _schedule_auto_ruby_refresh(self, idx: int) -> None:
        if self._shutting_down or not self._subtitle_needs_auto_ruby(idx):
            return

        generation_id = int(getattr(self, "_auto_ruby_generation_id", 0) or 0) + 1
        self._auto_ruby_generation_id = generation_id
        target_idx = int(idx)

        def _worker() -> None:
            try:
                self.sub_manager.ensure_auto_ruby_for_index(target_idx)
            except Exception:
                return

            def _refresh_if_current() -> None:
                if self._shutting_down:
                    return
                if generation_id != getattr(self, "_auto_ruby_generation_id", None):
                    return
                if self.slider_dragging:
                    return
                if target_idx != getattr(self, "last_rendered_index", None):
                    return
                if self.subtitle_deleted:
                    return
                self.last_subtitle_text = ""
                self._update_subtitle_display(force=True, allow_auto_ruby=False)

            try:
                dispatch_to_tk(self.settings.root, _refresh_if_current)
            except Exception:
                pass

        thread = threading.Thread(target=_worker, daemon=True, name="auto-ruby-refresh")
        self._auto_ruby_thread = thread
        thread.start()

    def toggle_subtitle_visibility(self, event=None):
        if self.subtitle_timeout_job:
            try:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
            except Exception:
                pass
            self.subtitle_timeout_job = None

        if getattr(self, "subtitles_user_hidden", False):
            self.subtitles_user_hidden = False
            self.subtitles_hidden_until_index = None
            self._show_subtitle_overlay_window()
            self.subtitle_deleted = False
            self.last_subtitle_text = ""
            self._update_subtitle_display(force=True)
            return "break"

        self.subtitles_user_hidden = True
        self.subtitles_hidden_until_index = getattr(self, "last_rendered_index", None)
        try:
            self.renderer.canvas.delete("all")
            if hasattr(self.renderer, "destroy_hover_windows"):
                self.renderer.destroy_hover_windows()
        except Exception:
            pass
        self._hide_subtitle_overlay_window()
        self.subtitle_deleted = True
        return "break"

    def _hide_subtitles_temporarily(self):
        if not self.playing:
            self.subtitle_timeout_job = None
            return

        if not self.subtitle_deleted:
            self.renderer.canvas.delete("all")
            self.subtitle_deleted = True

        self.subtitle_timeout_job = None

    def on_refresh_subtitles(self, event):
        if self.subtitle_timeout_job:
            self.overlay.root.after_cancel(self.subtitle_timeout_job)
            self.subtitle_timeout_job = None
        self.last_subtitle_text = ""
        self.subtitles_user_hidden = False
        self.subtitles_hidden_until_index = None
        self._show_subtitle_overlay_window()
        self.subtitle_deleted    = False
        self.update_time_and_subtitle_displays()

    def on_slider_press(self, event):
        self.slider_dragging = True
        self._last_slider_drag_value = None
        self._last_slider_drag_seen_at = 0.0
        self._slider_pending_time_display = None
        self._slider_time_display_job = None
        self._slider_last_preview_ms = 0.0
        if self.subtitle_timeout_job:
            try:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
            except Exception:
                pass
            self.subtitle_timeout_job = None

    def _cancel_slider_render_job(self) -> None:
        job = getattr(self, "_slider_render_job", None)
        if job is None:
            return
        try:
            self.settings.root.after_cancel(job)
        except Exception:
            pass
        self._slider_render_job = None

    def _render_slider_preview(self) -> None:
        self._slider_render_job = None
        if self._shutting_down or not self.slider_dragging:
            return

        start = time.perf_counter()
        value = getattr(self, "_slider_pending_value", None)
        if value is not None:
            self.current_time = float(value)
        self._update_subtitle_display(allow_auto_ruby=False, preview=True)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        self._slider_last_preview_ms = elapsed_ms
        try:
            self._record_perf_sample("slider_preview", elapsed_ms)
        except Exception:
            pass

    def _slider_preview_delay_ms(self) -> int:
        try:
            last_ms = float(getattr(self, "_slider_last_preview_ms", 0.0) or 0.0)
        except Exception:
            last_ms = 0.0
        if last_ms <= 0.0:
            return 16
        return max(16, min(50, int(round(last_ms * 1.5))))

    def _schedule_slider_preview_render(self, value: float) -> None:
        self._slider_pending_value = float(value)
        if getattr(self, "_slider_render_job", None) is not None:
            return
        try:
            self._slider_render_job = self.settings.root.after(
                self._slider_preview_delay_ms(),
                self._render_slider_preview,
            )
        except Exception:
            self._slider_render_job = None
            self._render_slider_preview()

    def _flush_slider_time_display(self) -> None:
        self._slider_time_display_job = None
        pending = getattr(self, "_slider_pending_time_display", None)
        if pending is None or self._shutting_down:
            return
        value, text = pending
        self.current_time = float(value)
        self._publish_time_display(str(text))

    def _schedule_slider_time_display(self, value: float) -> None:
        self._slider_pending_time_display = (
            float(value),
            self._time_display_text(float(value), include_pending=False),
        )
        if getattr(self, "_slider_time_display_job", None) is not None:
            return
        try:
            self._slider_time_display_job = self.settings.root.after(16, self._flush_slider_time_display)
        except Exception:
            self._slider_time_display_job = None
            self._flush_slider_time_display()

    def on_slider_change(self, value):
        if self._shutting_down:
            return
        start = time.perf_counter()
        if self.slider_dragging:
            slider_value = float(value)
            last_value = getattr(self, "_last_slider_drag_value", None)
            last_time = float(getattr(self, "_last_slider_drag_seen_at", 0.0) or 0.0)
            now = time.perf_counter()
            if last_value is not None and abs(float(last_value) - slider_value) < 0.0005 and (now - last_time) < 0.05:
                return
            self._last_slider_drag_value = slider_value
            self._last_slider_drag_seen_at = now
            self.current_time = slider_value
            self._schedule_slider_time_display(slider_value)
            if self._slider_preview_needs_render(slider_value):
                self._schedule_slider_preview_render(slider_value)
            else:
                self._cancel_slider_render_job()
                self._slider_pending_value = None
        try:
            self._record_perf_sample("slider_change", (time.perf_counter() - start) * 1000.0)
        except Exception:
            pass

    def on_slider_release(self, event):
        start = time.perf_counter()
        self._cancel_slider_render_job()
        time_job = getattr(self, "_slider_time_display_job", None)
        if time_job is not None:
            try:
                self.settings.root.after_cancel(time_job)
            except Exception:
                pass
            self._slider_time_display_job = None
        self._slider_pending_time_display = None
        self._slider_pending_value = None
        self._last_slider_drag_value = None
        self._last_slider_drag_seen_at = 0.0
        self.slider_dragging = False
        self._defer_auto_ruby_once = True
        self.last_subtitle_text = ""
        self.playback.set_current_time(self.settings.slider.get())
        try:
            self._record_perf_sample("slider_release", (time.perf_counter() - start) * 1000.0)
        except Exception:
            pass
