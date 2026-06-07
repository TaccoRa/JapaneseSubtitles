"""
Standalone hover glossary demo runner.

Uses the existing canvas prototype without touching production code.
"""

import os
import sys
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from hover_glossary_canvas_prototype import CanvasGlossaryPrototype  # noqa: E402


def main() -> None:
    root = tk.Tk()
    target_lang = os.environ.get("HOVER_GLOSSARY_LANG", "de")
    app = CanvasGlossaryPrototype(root, transparent=True, target_lang=target_lang)
    app.current_text = "昨日は学校に行かなかった。今日は家で勉強している。"
    app._render_tokens(app.current_text)
    root.bind("<Escape>", lambda _ev: root.destroy())
    root.mainloop()


if __name__ == "__main__":
    main()
