# JapaneseSubtitles

JapaneseSubtitles is a small desktop app that shows subtitle text in a floating overlay while you watch a video in another player or browser window.

The app does not play the video itself. You keep your video open in the background, then use this app to show, time, copy, translate, and send subtitle text to Anki.

## What The App Can Do

- Show subtitles in a transparent always-on-top overlay.
- Load local `.srt`, `.ass`, or `.ssa` subtitle files.
- Search and cache subtitles from a GitHub mirror of subtitle files.
- Switch between episodes.
- Adjust time, offset, skip length, and overlay position.
- Show ruby/furigana above Japanese words.
- Show ruby only on hover if that mode is enabled.
- Right-click subtitles to open a copy popup.
- Add selected words or sentences to Anki through AnkiConnect.
- Use OCR to read the time from a video player and sync this app to it.

## Install Step By Step

These steps are written for Windows and PowerShell.

1. Install Python from `https://www.python.org/downloads/`.
   During install, enable `Add Python to PATH`.

2. Open PowerShell in the project folder:

   ```powershell
   cd C:\User\VSCode_projects\JapaneseSubtitles
   ```

3. Create a virtual Python environment:

   ```powershell
   py -m venv .venv
   ```

4. Activate it:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation, run this once:

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```

5. Install the Python packages:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

6. Start the app:

   ```powershell
   python SubtitlePlayer\main.py
   ```

## Optional Setup

### Anki

To add cards to Anki:

1. Install Anki.
2. Install the AnkiConnect add-on in Anki.
3. Start Anki before using the app's Anki button or popup action.
4. The default AnkiConnect URL is `http://127.0.0.1:8765`.

### OCR Time Sync

OCR time sync needs two parts:

1. The Python package `pytesseract`, installed from `requirements.txt`.
2. The real Tesseract OCR program installed on Windows.

If OCR does not work, open advanced settings and set the Tesseract path.

## How To Use The App

1. Start your video in a browser or video player.
2. Start this app with `python SubtitlePlayer\main.py`.
3. Choose a subtitle file when asked, or use a saved/remote subtitle search.
4. Move the subtitle overlay to the correct place on your screen.
5. Press `Play` in the app and the video, or the spacebar when inside the video, to start the video and the app.
6. Use the time entry, slider, skip buttons, and offset field to line up the app with the video. Specific hotkeys can be used to jump to the beginning of the next subtitle.

The app timer and the video timer are separate. If they drift, use the time field, OCR sync, or offset to correct them.

## Main Windows

### Control Window

This is the small window with play/pause, skip buttons, episode controls, and time controls.

- `Play` / `Stop`: starts or pauses this app's subtitle timer.
- Back/forward buttons: move by the configured skip amount.
- Slider: scrub through the subtitle timeline.
- Time entry: type a time like `01:20` or `1:02:33` or just `1234` for 12:34.
- Episode field and buttons: switch to another episode if the app knows the episode list.
- Settings button: opens advanced settings.

### Subtitle Overlay

This is the floating transparent subtitle window. It stays above the video.

- Drag it to move subtitles.
- Right-click it to open the copy popup.
- In phone mode, a small invisible handle helps move it.

### Copy Popup

This appears after right-clicking subtitles.

- Select text and copy it.
- Use `Ctrl+A` while the pointer is inside the popup to select all popup text.
- Add selected text to Anki if AnkiConnect is running.
- Hover words for ruby or dictionary lookup when enabled (see hotkeys).

### Advanced Settings

Advanced settings are grouped by area:

- General: timer, subtitle appearance, ruby behavior, speaker cleanup.
- Anki: deck names, note type, field names, translation targets.
- Shortcuts: keyboard shortcuts and disable switches.
- OCR: Tesseract path and OCR capture boxes.

## Important Folders And Files

- `SubtitlePlayer/main.py`: starts the app.
- `SubtitlePlayer/app.py`: builds the main app objects and startup flow.
- `SubtitlePlayer/controller/`: input handling, playback timer, hotkeys, episode changes, OCR, and Anki UI flow.
- `SubtitlePlayer/model/`: subtitle loading, remote search/cache, rendering support, config, AnkiConnect client, and ruby generation.
- `SubtitlePlayer/view/`: Tkinter windows: control window, advanced settings, subtitle overlay, popup, loading overlay.
- `SubtitlePlayer/anki_reading_support/`: bundled MeCab/Kakasi files used for ruby generation.
- `subs/`: local subtitle files.
- `SubtitlePlayer/cache_github/`: downloaded/cached remote subtitle files.
- `SubtitlePlayer/github_search/`: cached GitHub search results.
- `anki_modify/`: standalone maintenance tools for existing Anki notes.
- `dev/`: tests, benchmarks, experiments, and analysis notes.
- `config.json`: user settings and remembered app state.
- `ideas.txt`: project ideas and known issues.
- `AI_CODEBASE_NOTES.md`: technical notes for future code work.

## Useful Developer Commands

Run model tests:

```powershell
$env:PYTHONPATH='.;SubtitlePlayer'
python -m pytest dev\tests\model
```

Run slider benchmark:

```powershell
python dev\benchmark_slider.py
```

Run episode switch benchmark:

```powershell
python dev\benchmark_remote_switch.py
```

## Troubleshooting

If the app does not start, check that the virtual environment is active and run `pip install -r requirements.txt` again.

If Anki cards are not created, start Anki first and make sure AnkiConnect is installed.

If OCR does not detect the video time, check the OCR region boxes and the Tesseract path.

If subtitles are slow while scrubbing, disable auto ruby temporarily or check the slider benchmark.

If the wrong episode opens, clear stale remote search/cache files or choose the local subtitle manually.
