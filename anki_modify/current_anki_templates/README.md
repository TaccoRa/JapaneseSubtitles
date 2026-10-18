# Anki template snapshot

Exported from the live `Standard (und umgekehrte Karte) Japanese` note type on 2026-10-02.

- Karte 1: Japanese to translation (`1_FRONT_To_BACK_*`).
- Karte 2: translation to Japanese (`2_BACK_To_FRONT_*`).
- `styling.css`: shared live model styling.
- `templates.json`: original template names and both sides.
- Shared JavaScript dependencies are copied from Anki media alongside these files.

This is a snapshot; exporting did not modify the live templates.

## Sentence spacing repair

From the repository root:

```powershell
.venv\Scripts\python.exe anki_modify/fix_sentence_ruby_spacing.py
.venv\Scripts\python.exe anki_modify/fix_sentence_ruby_spacing.py --apply
```

The default command previews all notes without changing them. `--apply` saves
the original and proposed field values to a timestamped JSON backup before
writing. It only inserts a space after a raw line break or `<br>` immediately
followed by bracket furigana in `SentenceJA`. Other fields and readings are
preserved. Use repeated `--field` arguments for custom sentence field names.
