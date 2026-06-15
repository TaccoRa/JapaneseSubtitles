"""Subtitle time display, slider sync, and subtitle redraw helper."""

import bisect
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

    def _update_subtitle_display(self, force: bool = False):
            offset = self.settings._last_offset_value
            sub_t = self.current_time - offset

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

            if (
                not force
                and not self.subtitle_deleted
                and idx == self.last_rendered_index
                and copy_text == self.last_subtitle_text
            ):
                return
            
            self.sub_manager.ensure_auto_ruby_for_index(idx)

            clean, _, top, bottom = self.sub_manager.display_data[idx]
            copy_text = self.segments_to_copy_text(top, bottom) or clean
            self.last_subtitle_raw = copy_text

            if self.subtitle_timeout_job:
                self.overlay.root.after_cancel(self.subtitle_timeout_job)
                self.subtitle_timeout_job = None

            # self.renderer.canvas.delete("all")

            # add this only if overlay may recreate the canvas
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
            self.renderer.canvas.delete("all")
            self.last_rendered_index = None
            self.last_subtitle_text = ""
            self.subtitle_deleted = True

    def _hide_subtitles_temporarily(self):
            if not self.playing:
                self.subtitle_timeout_job = None
                return
            if not self.subtitle_deleted:
                self.renderer.canvas.delete("all")
                self.subtitle_deleted = True
                self.last_rendered_index = None
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

    def on_slider_change(self, value):
            if self.slider_dragging:
                text = format_time(float(value))
                self.settings.time_overlay.itemconfig(self.settings.time_overlay_text, text=text)
                self.settings.update_time_overlay_position()
                if not self.entry_editing:
                    self.settings.control_time_str.set(text)
                self.current_time = float(value)
                self._update_subtitle_display()

    def on_slider_release(self, event):
            self.slider_dragging = False
            self.playback.set_current_time(self.settings.slider.get())
