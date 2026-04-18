# Hover Glossary Prototypes

This folder contains two standalone prototypes:

1. `hover_glossary_canvas_prototype.py`
2. `hover_glossary_text_prototype.py`

## Why two versions

- Canvas version matches the current app renderer (outlined/shadow subtitle drawing).
- Text-tag version mimics browser-extension style token ranges (closer to Yomitan/asbplayer concepts).

## Yomitan-inspired idea used here

The shared concept is:

1. tokenize text
2. keep a mapping from visual range/box to token metadata
3. on hover, resolve token under pointer
4. show tooltip/dictionary payload

The prototypes keep this mapping explicit (`token_boxes` for canvas, `tok_*` tags for text widget),
which makes replacement of the lookup backend straightforward later.
