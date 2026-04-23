# Hover Renderer Experiment

This is an isolated test framework for subtitle rendering speed and hover lookup behavior.
It does not change the main SubtitlePlayer runtime.

## What is included

- `CanvasTextBackend`: baseline `tk.Canvas.create_text` rendering.
- `CanvasImageBackend`: cached RGBA image rendering via Pillow + `create_image`.
- Hover hit testing from token spans, with token highlight and mock dictionary output.
- Shift-to-hover behavior (boxes only appear while Shift is pressed).
- Transparent subtitle canvas background using Windows color-key transparency (when supported).

## Run

```bash
python -m experiments.hover_renderer.demo_app --backend image
```

Or baseline text backend:

```bash
python -m experiments.hover_renderer.demo_app --backend text
```

## Why this helps

- Lets us compare "draw every glyph every frame" vs "cache image and blit".
- Provides a separate place to test Japanese font fallback (to avoid tofu boxes).
- Gives a Yomitan-like first step: hover token detection + lookup panel.

## Next steps

1. Swap mock dictionary with real parser + dictionary pipeline.
2. Add grammar chunk grouping on top of token spans.
3. Add benchmark mode (N renders with timing and cache stats).
