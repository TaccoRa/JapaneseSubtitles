"""
Prototype B: selectable-text shift+hover glossary.

This mimics the DOM/tag approach used by browser extensions:
- tokens are explicit text ranges
- hover index resolves token tag
- tooltip shows token metadata
"""

import tkinter as tk
from fugashi import Tagger


class TextGlossaryPrototype:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Text Hover Glossary Prototype")
        self.root.geometry("980x300")

        self.shift_down = False
        self.current_tag = None
        self.tooltip = None
        self.token_meta = {}
        self.tagger = Tagger()

        self.text = tk.Text(
            self.root,
            wrap="word",
            font=("Meiryo", 24, "bold"),
            padx=16,
            pady=14,
            bg="#0f0f0f",
            fg="white",
            insertbackground="white",
            relief="flat",
            bd=0,
            height=4,
        )
        self.text.pack(fill="both", expand=True)

        self.text.tag_configure("hint", foreground="#d9d9d9", font=("Arial", 11))
        self.text.insert("1.0", "Hold Shift and hover over a token.\n", ("hint",))

        line = "昨日は学校に行かなかった。今日は家で勉強している。"
        self._insert_tokenized_line(line)
        self.text.config(state="normal")

        self.root.bind_all("<KeyPress-Shift_L>", self._on_shift_down)
        self.root.bind_all("<KeyPress-Shift_R>", self._on_shift_down)
        self.root.bind_all("<KeyRelease-Shift_L>", self._on_shift_up)
        self.root.bind_all("<KeyRelease-Shift_R>", self._on_shift_up)
        self.text.bind("<Motion>", self._on_motion)
        self.text.bind("<Leave>", self._on_leave)

    def _token_meta_from_token(self, token):
        f = token.feature
        return {
            "surface": token.surface,
            "lemma": getattr(f, "lemma", "") or "",
            "reading": getattr(f, "reading", "") or "",
            "pos1": getattr(f, "pos1", "") or "",
            "pos2": getattr(f, "pos2", "") or "",
        }

    def _insert_tokenized_line(self, line: str) -> None:
        tokens = list(self.tagger(line))
        for i, token in enumerate(tokens):
            surface = token.surface
            if not surface:
                continue
            start = self.text.index("end-1c")
            self.text.insert("end", surface)
            end = self.text.index("end-1c")
            tag = f"tok_{i}"
            self.text.tag_add(tag, start, end)
            self.text.tag_configure(tag, foreground="white", background="#0f0f0f")
            self.token_meta[tag] = self._token_meta_from_token(token)
        self.text.insert("end", "\n")

    def _on_shift_down(self, _event):
        self.shift_down = True

    def _on_shift_up(self, _event):
        self.shift_down = False
        self._clear_hover()

    def _on_leave(self, _event):
        self._clear_hover()

    def _find_token_tag_at(self, x: int, y: int):
        idx = self.text.index(f"@{x},{y}")
        tags = self.text.tag_names(idx)
        for tag in tags:
            if tag.startswith("tok_"):
                return tag
        return None

    def _on_motion(self, event):
        if not self.shift_down:
            self._clear_hover()
            return

        tag = self._find_token_tag_at(event.x, event.y)
        if not tag:
            self._clear_hover()
            return
        if tag == self.current_tag:
            self._move_tooltip(event.x_root + 16, event.y_root + 14)
            return

        self._clear_hover()
        self.current_tag = tag
        self.text.tag_configure(tag, background="#33475b")
        self._show_tooltip(self.token_meta.get(tag, {}), event.x_root + 16, event.y_root + 14)

    def _show_tooltip(self, meta: dict, x: int, y: int):
        self._hide_tooltip()
        win = tk.Toplevel(self.root)
        self.tooltip = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        frame = tk.Frame(win, bg="#111", bd=1, relief="solid")
        frame.pack(fill="both", expand=True)
        info = (
            f"surface: {meta.get('surface', '-')}\n"
            f"lemma: {meta.get('lemma', '-') or '-'}\n"
            f"reading: {meta.get('reading', '-') or '-'}\n"
            f"pos: {meta.get('pos1', '-') or '-'} / {meta.get('pos2', '-') or '-'}"
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
        if self.current_tag:
            self.text.tag_configure(self.current_tag, background="#0f0f0f")
        self.current_tag = None
        self._hide_tooltip()


def main():
    root = tk.Tk()
    TextGlossaryPrototype(root)
    root.mainloop()


if __name__ == "__main__":
    main()
