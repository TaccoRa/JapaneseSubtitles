"""
Prototype A: canvas-based shift+hover glossary.

This keeps the current subtitle rendering model (drawn text on a canvas) and adds
token hit-testing metadata for hover lookup.
"""

import json
import threading
import tkinter as tk
from tkinter import font as tkFont
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fugashi import Tagger


class CanvasGlossaryPrototype:
    def __init__(self, root: tk.Tk, transparent: bool = False, target_lang: str = "de") -> None:
        self.root = root
        self.target_lang = (target_lang or "de").strip().lower()
        if transparent:
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            try:
                self.root.attributes("-transparentcolor", "grey")
            except Exception:
                pass
            self.root.geometry("980x260+40+40")
        else:
            self.root.title("Canvas Hover Glossary Prototype")
            self.root.geometry("980x280")

        self.canvas = tk.Canvas(self.root, bg="grey" if transparent else "#2b2b2b", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self.base_font = tkFont.Font(family="Meiryo", size=34, weight="bold")
        self.tooltip_font = tkFont.Font(family="Meiryo", size=18, weight="bold")
        self.shift_down = False
        self.hover_item = None
        self.token_boxes = []
        self.tooltip = None
        self.highlight_id = None
        self.tagger = Tagger()
        self._lookup_cache = {}
        self._hover_key = None

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

    @staticmethod
    def _kata_from_hira(text: str) -> str:
        out = []
        for ch in text or "":
            code = ord(ch)
            if 0x3041 <= code <= 0x3096:
                out.append(chr(code + 0x60))
            else:
                out.append(ch)
        return "".join(out)

    def _fetch_jisho(self, query: str) -> dict:
        if not query:
            return {}
        cached = self._lookup_cache.get(query)
        if cached and isinstance(cached, dict) and cached.get("jisho_done"):
            return cached
        url = "https://jisho.org/api/v1/search/words?" + urlencode({"keyword": query})
        data = {}
        try:
            req = Request(url, headers={"User-Agent": "SubtitlePlayer-Demo"})
            with urlopen(req, timeout=4) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
            data = json.loads(payload)
        except Exception:
            data = {}
        result = {"jisho_done": True}
        entries = (data or {}).get("data") or []
        if entries:
            entry = entries[0]
            jp = (entry.get("japanese") or [{}])[0]
            reading = jp.get("reading") or ""
            senses = entry.get("senses") or []
            english = []
            if senses:
                english = senses[0].get("english_definitions") or []
            if reading:
                result["reading_kata"] = self._kata_from_hira(str(reading))
            if english:
                result["english"] = ", ".join(english)
        return result

    def _translate_google(self, text: str, source_lang: str, target_lang: str) -> str:
        if not text:
            return ""
        params = {
            "client": "gtx",
            "sl": source_lang,
            "tl": target_lang,
            "dt": "t",
            "q": text,
        }
        url = "https://translate.googleapis.com/translate_a/single?" + urlencode(params)
        try:
            req = Request(url, headers={"User-Agent": "SubtitlePlayer-Demo"})
            with urlopen(req, timeout=4) as resp:
                payload = resp.read().decode("utf-8", errors="replace")
            data = json.loads(payload)
        except Exception:
            return ""
        translated = []
        try:
            for chunk in data[0]:
                translated.append(chunk[0] or "")
        except Exception:
            return ""
        return "".join(translated).strip()

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
        try:
            win.attributes("-transparentcolor", "grey")
        except Exception:
            pass
        canvas = tk.Canvas(win, bg="grey", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        win._tooltip_canvas = canvas

        self._hover_key = (meta.get("lemma") or meta.get("surface") or "").strip()
        self._render_tooltip_lines(meta, x, y)
        self._kickoff_lookup(meta)

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
        self._hover_key = None

    def _clear_hover(self):
        self.hover_item = None
        if self.highlight_id is not None:
            self.canvas.delete(self.highlight_id)
            self.highlight_id = None
        self._hide_tooltip()

    def _render_tooltip_lines(self, meta: dict, x: int, y: int):
        if self.tooltip is None or not self.tooltip.winfo_exists():
            return
        canvas = getattr(self.tooltip, "_tooltip_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")

        key = (meta.get("lemma") or meta.get("surface") or "").strip()
        cached = self._lookup_cache.get(key) or {}
        reading = cached.get("reading_kata") or self._kata_from_hira(meta.get("reading") or "")
        english = cached.get("english") or ""
        german = cached.get("german") or ""

        line1 = meta.get("surface") or ""
        if reading:
            line1 = f"{line1} [{reading}]"
        lines = [line1]
        if english:
            lines.append(f"EN: {english}")
        if german:
            lines.append(f"{self.target_lang.upper()}: {german}")
        lines.append(f"EX: {self.current_text}")

        pad_x = 10
        pad_y = 8
        max_w = 0
        for line in lines:
            max_w = max(max_w, self.tooltip_font.measure(line))
        line_h = self.tooltip_font.metrics("linespace") + 4
        width = max_w + pad_x * 2
        height = line_h * len(lines) + pad_y * 2
        canvas.config(width=width, height=height)
        self.tooltip.geometry(f"{width}x{height}+{x}+{y}")

        cy = pad_y
        for line in lines:
            self._draw_outlined_tooltip_text(canvas, pad_x, cy, line)
            cy += line_h

    def _draw_outlined_tooltip_text(self, canvas: tk.Canvas, x: int, y: int, text: str) -> None:
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if dx or dy:
                    canvas.create_text(
                        x + dx, y + dy,
                        text=text,
                        font=self.tooltip_font,
                        fill="black",
                        anchor="nw",
                    )
        canvas.create_text(
            x, y,
            text=text,
            font=self.tooltip_font,
            fill="white",
            anchor="nw",
        )

    def _kickoff_lookup(self, meta: dict) -> None:
        key = (meta.get("lemma") or meta.get("surface") or "").strip()
        if not key or key in self._lookup_cache:
            return

        def worker():
            result = self._fetch_jisho(key)
            english = result.get("english") or ""
            german = ""
            if english and self.target_lang and self.target_lang not in ("en", "ja"):
                german = self._translate_google(english, source_lang="en", target_lang=self.target_lang)
            if german:
                result["german"] = german
            self._lookup_cache[key] = result
            try:
                self.root.after(0, lambda: self._refresh_tooltip_for_key(key))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_tooltip_for_key(self, key: str) -> None:
        if key != self._hover_key:
            return
        if self.hover_item is None:
            return
        meta = self.hover_item.get("meta", {})
        try:
            x = self.tooltip.winfo_x()
            y = self.tooltip.winfo_y()
        except Exception:
            x = 40
            y = 40
        self._render_tooltip_lines(meta, x, y)


def main():
    root = tk.Tk()
    CanvasGlossaryPrototype(root, transparent=False, target_lang="de")
    root.mainloop()


if __name__ == "__main__":
    main()
