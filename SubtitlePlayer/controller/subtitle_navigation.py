"""Subtitle time display, slider sync, and subtitle redraw helper."""

import bisect
import threading
import tkinter as tk
import re
from typing import Any
from utils import format_time, parse_time_value

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
            print(e)
            focused = None
        try:
            time_entry = self.settings.time_entry
        except Exception as e:
            print(e)
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
            print(e)
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

    def update_time_and_subtitle_displays(self):#updates settings time overlay and control window entry
        text = format_time(self.current_time)
        pending = float(getattr(self, "_pending_seek_delta", 0.0) or 0.0)
        if abs(pending) >= 0.001:
            sign = "+" if pending > 0 else "-"
            text = f"{text} ({sign}{self._format_delta_seconds(pending)}s)"
        self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
        self.settings.update_time_overlay_position()
        if not self.entry_editing:
            self.settings.control_time_str.set(text)
        self._update_subtitle_display()

    def _update_subtitle_display(
        self,
        force: bool = False,
        allow_auto_ruby: bool = True,
        schedule_auto_ruby: bool = False,
    ):
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

        start_times = self._get_display_start_times()
        idx = bisect.bisect_right(start_times, sub_t) - 1

        if idx < 0:
            self.last_rendered_index = None
            self.last_subtitle_text = ""
            self._reset_canvas()
            return

        clean, _, top, bottom = self.sub_manager.display_data[idx]
        copy_text = self.segments_to_copy_text(top, bottom) or clean
        self.last_subtitle_raw = copy_text

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

        if self.subtitle_timeout_job:
            self.overlay.root.after_cancel(self.subtitle_timeout_job)
            self.subtitle_timeout_job = None

        self.renderer.update_canvas(self.overlay.subtitle_canvas)

        self.last_subtitle_text = copy_text
        self.last_rendered_index = idx
        self.subtitle_deleted = False

        self.renderer.render_subtitle(top, bottom, self.overlay)

        self.subtitle_timeout_job = self.overlay.root.after(
            self.hide_subtitles_ms,
            self._hide_subtitles_temporarily
        )
    
    @staticmethod
    def segments_to_copy_text(top_segments, bottom_segments) -> str:
        def _line_text(segments) -> str:
            out = []
            for base, ruby in segments or []:
                if ruby:
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
                self.settings.root.after(0, _refresh_if_current)
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

        if self.subtitle_deleted:
            self.subtitle_deleted = False
            self.last_subtitle_text = ""
            self._update_subtitle_display(force=True)
            return "break"

        try:
            self.renderer.canvas.delete("all")
            if hasattr(self.renderer, "destroy_hover_windows"):
                self.renderer.destroy_hover_windows()
        except Exception:
            pass
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
        self.subtitle_deleted    = False
        self.update_time_and_subtitle_displays()

    def on_slider_press(self, event):
        self.slider_dragging = True

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

        value = getattr(self, "_slider_pending_value", None)
        if value is not None:
            self.current_time = float(value)
        self._update_subtitle_display(allow_auto_ruby=False)

    def _schedule_slider_preview_render(self, value: float) -> None:
        self._slider_pending_value = float(value)
        if getattr(self, "_slider_render_job", None) is not None:
            return
        try:
            self._slider_render_job = self.settings.root.after(16, self._render_slider_preview)
        except Exception:
            self._slider_render_job = None
            self._render_slider_preview()

    def on_slider_change(self, value):
        if self._shutting_down:
            return
        if self.slider_dragging:
            slider_value = float(value)
            text = format_time(slider_value)
            self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
            self.settings.update_time_overlay_position()
            if not self.entry_editing:
                self.settings.control_time_str.set(text)
            self.current_time = slider_value
            self._schedule_slider_preview_render(slider_value)

    def on_slider_release(self, event):
        self._cancel_slider_render_job()
        self._slider_pending_value = None
        self.slider_dragging = False
        self._defer_auto_ruby_once = True
        self.last_subtitle_text = ""
        self.playback.set_current_time(self.settings.slider.get())
