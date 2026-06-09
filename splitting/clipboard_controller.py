"""Clipboard helper for subtitle copy popup behavior.

This is intentionally narrow: it handles only the subtitle copy popup flow and
subtitle text assembly used by the right-click copy action.
"""

from __future__ import annotations

from typing import Any


class ClipboardController:
    """Encapsulates clipboard/copy-popup behavior for SubtitleController."""

    def __init__(self, controller: Any) -> None:
        self.controller = controller

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

    def on_copy_popup(self, event=None):
        """Open the copy popup and simulate the video click, preserving existing behavior."""
        controller = self.controller
        # Create popup first so we can click relative to its position.
        controller.popup.open_copy_popup(controller.last_subtitle_raw)
        controller.simulate_video_click()
        return "break"
