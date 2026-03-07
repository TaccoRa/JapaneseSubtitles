"""
Prototype A: canvas-based shift+hover glossary.

This keeps the current subtitle rendering model (drawn text on a canvas) and adds
token hit-testing metadata for hover lookup.
"""

import tkinter as tk
from tkinter import font as tkFont
from fugashi import Tagger


class CanvasGlossaryPrototype:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Canvas Hover Glossary Prototype")
        self.root.geometry("980x280")

        self.canvas = tk.Canvas(self.root, bg="#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.base_font = tkFont.Font(family="Meiryo", size=34, weight="bold")
        self.shift_down = False
        self.hover_item = None
        self.token_boxes = []
        self.tooltip = None
        self.highlight_id = None
        self.tagger = Tagger()

        # A line that includes inflected words for quick manual testing.
        self.current_text = "昨日は学校に行かなかった。今日は家で勉強している。"
        self._render_tokens(self.current_text)

        self.root.bind_all("<KeyPress-Shift_L>", self._on_shift_down)
        self.root.bind_all("<KeyPress-Shift_R>", self._on_shift_down)
        self.root.bind_all("<KeyRelease-Shift_L>", self._on_shift_up)
        self.root.bind_all("<KeyRelease-Shift_R>", self._on_shift_up)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)

    def _token_meta(self, token):
        f = token.feature
        return {
            "surface": token.surface,
            "lemma": getattr(f, "lemma", "") or "",
            "reading": getattr(f, "reading", "") or "",
            "pos1": getattr(f, "pos1", "") or "",
            "pos2": getattr(f, "pos2", "") or "",
        }

    def _draw_outlined_text(self, x: int, y: int, text: str, fill: str = "white") -> int:
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if dx or dy:
                    self.canvas.create_text(
                        x + dx,
                        y + dy,
                        text=text,
                        font=self.base_font,
                        fill="black",
                        anchor="nw",
                    )
        return self.canvas.create_text(x, y, text=text, font=self.base_font, fill=fill, anchor="nw")

    def _render_tokens(self, text: str) -> None:
        self.canvas.delete("all")
        self.token_boxes.clear()
        self.highlight_id = None

        tokens = list(self.tagger(text))
        x = 40
        y = 90
        line_h = self.base_font.metrics("linespace")
        canvas_w = max(400, self.canvas.winfo_width() or 900)

        for token in tokens:
            surface = token.surface
            if not surface:
                continue
            w = self.base_font.measure(surface)
            if x + w > canvas_w - 40:
                x = 40
                y += line_h + 24
            text_id = self._draw_outlined_text(x, y, surface)
            bbox = self.canvas.bbox(text_id)
            if bbox:
                self.token_boxes.append({
                    "bbox": bbox,
                    "meta": self._token_meta(token),
                })
            x += w + 2

        self.canvas.create_text(
            20,
            20,
            anchor="nw",
            fill="#d9d9d9",
            font=("Arial", 11),
            text="Hold Shift and hover over a token.",
        )

    def _on_shift_down(self, _event):
        self.shift_down = True

    def _on_shift_up(self, _event):
        self.shift_down = False
        self._clear_hover()

    def _on_leave(self, _event):
        self._clear_hover()

    def _find_token(self, x: int, y: int):
        for item in self.token_boxes:
            l, t, r, b = item["bbox"]
            if l <= x <= r and t <= y <= b:
                return item
        return None

    def _on_motion(self, event):
        if not self.shift_down:
            self._clear_hover()
            return
        item = self._find_token(event.x, event.y)
        if item is None:
            self._clear_hover()
            return
        if self.hover_item is item:
            self._move_tooltip(event.x_root + 16, event.y_root + 14)
            return
        self.hover_item = item
        self._render_highlight(item["bbox"])
        self._show_tooltip(item["meta"], event.x_root + 16, event.y_root + 14)

    def _render_highlight(self, bbox):
        if self.highlight_id is not None:
            self.canvas.delete(self.highlight_id)
            self.highlight_id = None
        l, t, r, b = bbox
        self.highlight_id = self.canvas.create_rectangle(
            l - 2, t - 2, r + 2, b + 2,
            outline="#ffd966",
            width=2,
        )

    def _show_tooltip(self, meta: dict, x: int, y: int):
        self._hide_tooltip()
        win = tk.Toplevel(self.root)
        self.tooltip = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        frame = tk.Frame(win, bg="#111", bd=1, relief="solid")
        frame.pack(fill="both", expand=True)

        info = (
            f"surface: {meta['surface']}\n"
            f"lemma: {meta['lemma'] or '-'}\n"
            f"reading: {meta['reading'] or '-'}\n"
            f"pos: {meta['pos1'] or '-'} / {meta['pos2'] or '-'}"
        )
        tk.Label(
            frame,
            text=info,
            justify="left",
            anchor="w",
            bg="#111",
            fg="white",
            font=("Consolas", 10),
            padx=8,
            pady=6,
        ).pack()
        win.geometry(f"+{x}+{y}")

    def _move_tooltip(self, x: int, y: int):
        if self.tooltip is not None and self.tooltip.winfo_exists():
            self.tooltip.geometry(f"+{x}+{y}")

    def _hide_tooltip(self):
        if self.tooltip is not None:
            try:
                self.tooltip.destroy()
            except Exception:
                pass
        self.tooltip = None

    def _clear_hover(self):
        self.hover_item = None
        if self.highlight_id is not None:
            self.canvas.delete(self.highlight_id)
            self.highlight_id = None
        self._hide_tooltip()


def main():
    root = tk.Tk()
    CanvasGlossaryPrototype(root)
    root.mainloop()


if __name__ == "__main__":
    main()
