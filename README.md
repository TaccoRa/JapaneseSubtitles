# JapaneseSubtitles

JapaneseSubtitles is a small desktop app that shows subtitle text in a floating overlay while you watch a video in another player or browser window.

The app does not play the video itself. You keep your video open in the background, then use this app to show, time, copy, translate, and send subtitle text to Anki.

An experimental Chrome connection can follow the existing video clock and record
media while keeping your original subtitles unchanged. See the
[browser setup, feature checklist and validation status](dev/docs/BROWSER_SYNC.md).
The existing asbplayer capture workflow remains the default during validation.

## What The App Can Do

- Show subtitles in a transparent always-on-top overlay.
- Load local `.srt`, `.ass`, or `.ssa` subtitle files.
- Search and cache subtitles from a GitHub mirror of subtitle files.
- Switch between episodes.
- Adjust time, offset, skip length, and overlay position.
- Show ruby/furigana above Japanese words.
- Show ruby only on hover if that mode is enabled.
- Right-click subtitles to open a copy popup.
- Add selected words or sentences to Anki through AnkiConnect, with sentence-context grammar markers for Japanese words.
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

### Combined keyboard and voice controls

Advanced Settings now includes **Controls**, alongside the existing Shortcuts and
Voice tabs. Configure both input methods together, including independent automatic
next-episode and intro skipping options. See the [controls and automation guide](dev/docs/UNIFIED_CONTROLS.md)
for submark selection, per-series title targets, browser extension reload, and validation details.

### Capture range shortcuts

With the native browser recorder, the top-row and numpad number keys re-record media for
the last card added during this app session, using the currently displayed cue:

| Key | Subtitle segments recorded |
| --- | --- |
| 3 | Previous two + current |
| 4 | Previous one + current |
| 5 | Current only |
| 6 | Current + next one |
| 7 | Current + next two |

These actions create no new cards. Fresh audio and image replace the media on
all notes with the exact same Japanese sentence, including Listening notes.
The existing Capture-only shortcut also replaces exact-sentence media.
Card text remains unchanged. Ranges are clamped at the episode boundaries,
and numpad 4/6 now capture instead of using the former navigation bindings. Global shortcut and Capture-only
disable settings also disable the number shortcuts.

In mode 1, numpad **0** and numpad **Enter** both toggle play/pause, alongside
the configured playback shortcut. Regular Enter is unaffected. Numpad shortcuts
work with Num Lock on or off.

Automatic sentence grouping uses punctuation with a maximum of three segments.
Without ending punctuation in the current cue, capture uses that cue alone.
Automatic grouping stops at gaps longer than 1.5 seconds between the end of one
subtitle and the start of the next, even when punctuation suggests a connection.
Adjust **Auto sentence: maximum subtitle gap (s)** under **Advanced Settings >
Anki > Browser Recording** if needed. This uses subtitle timestamps, not detected
audio silence. Explicit multi-segment recording keys keep their requested range.

### Anki

To add cards to Anki:

1. Install Anki.
2. Install the AnkiConnect add-on in Anki.
3. Start Anki before using the app's Anki button or popup action.
4. The default AnkiConnect URL is `http://127.0.0.1:8765`.

When adding a selected word, the app also checks the subtitle sentence. For example, selecting only `勉強` in `私は勉強した` creates the lookup/front form `勉強する` and adds the `する-Verb` tag. Noun usage such as `勉強は楽しい` stays `勉強`.

Manual popup selections can contain a complete compound or a phrase. Selecting `全国模試` uses that whole expression for the card; selecting `全国模試の結果` creates one phrase card with the subtitle sentence as context. Single verb inflections can still use their dictionary form, while phrases containing additional content words stay intact. Dictionary search suggestions for only a component word are rejected. When there is no matching entry for the whole expression, the app translates the complete expression instead.

Hover, popup lookup, database status, and card creation share the contextual word form. For example, `説明` in `説明した` uses `説明する` throughout; a particle such as `に` remains a particle. Quantity boundaries and operators such as `プラス` keep neighboring expressions separate.

Automatic grammar marker tags:

- `する-Verb`
- `い-Adj`
- `な-Adj`

No default source tag is added. Custom Anki tags can be set in Advanced Settings > Anki > Tags.

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
- Anki: deck names, note type, custom tags, field names, translation targets.
- Shortcuts: keyboard shortcuts and disable switches.
- OCR: Tesseract path and OCR capture boxes.
- Performance: shown only while debugging is enabled with `Ctrl+Shift+D`. Press `Ctrl+Shift+D` again to hide it.

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
- `config.json`: tracked default settings.
- `config.local.json`: local user settings and remembered app state.
- `ideas.txt`: project ideas and known issues.
- `AI_CODEBASE_NOTES.md`: technical notes for future code work.

## Voice, Hover, and Anki Capture

Voice commands use locally installed Vosk models. Open **Advanced Settings > Voice** to download the English or German model, choose the recognition language, and edit command aliases. You can also say the configured language-switch command; it only switches after the requested offline model is installed.

For voice lookup, add, capture, and copy, the target is chosen in this order: selected popup text, persistent subtitle highlights, then the word directly under the pointer. The target is frozen when the command is accepted, so later pointer movement cannot change an Anki card being created.

The recording keys `3`–`7` also add a new word card when the pointer is directly over a subtitle or copy-popup word. The card's sentence covers exactly the recorded segments: `3` includes two previous segments, `4` one previous segment, `5` only the current segment, `6` one following segment, and `7` two following segments. Without a word under the pointer, these keys keep their recording-only behavior. Old selections and highlights do not create a new card through these keys.

With pause on hover enabled, playback stays paused while the pointer is in the subtitle, copy popup, its hover panels, or an open popup context menu. Playback resumes after leaving them only if this hover interaction originally paused it.

- `copy` copies the visible result, preferring a translation.
- `copy kanji`, `copy kanji each`, `copy dictionary`, `copy dictionary together`, `copy translation`, and `copy translation each` select an explicit result type.
- `dictionary together` looks up the complete selected expression. `translate each` labels every word with its ruby/reading.
- `add highlight 8` extends the current highlight; `remove highlight 9` removes only that position. Repeated words in an add/capture batch are sent once.

The optional activity indicator in the lower-right subtitle area spins while an add or capture is active. A capture remains active while Anki media is being checked and copied to exact-sentence cards; it shows a check for one second only once all configured media is ready. Saying `abort` stops unsent voice-batch work and pending app callbacks. It cannot retract a request already sent to Anki or the external player.

## AniList Connection

Open **Advanced Settings > General > AniList** to connect. In the one-time setup, register an AniList application with the redirect URL `http://127.0.0.1:8768/anilist/callback`, then enter its Client ID and Client Secret in the app. The secret and account token are stored separately in Windows Credential Manager; the secret is not saved in configuration files or profiles. The browser login uses the authorization-code flow. Existing users who previously entered only a Client ID must add their application's Client Secret once.

## Annotations and Database Checks

The Annotation tab can show local, Anki, and WaniKani status. The database/status voice command reports the selected text, reading, canonical lookup, direct status, component-word status, and individual kanji status.

Compound-word status comes from the complete word. Having `全`, `国`, `模`, `試`, or even `全国` and `模試` in the database does not mark `全国模試` as collected or hide its furigana. Hover lookup keeps detected compounds together even when their furigana is split into smaller pieces. The status view lists component and kanji knowledge separately from the direct whole-word match.

Ruby generation preserves normal subtitle characters and source spacing. Exported ASS line escapes such as `\N` are decoded before word analysis, including when they remain inside an SRT file. Time units such as `10分` and `８時間`, and calendar dates such as `10月５日`, use their full contextual readings. Files with flattened readings mixed into ordinary text still need care, especially for ambiguous names or damaged source text.

Canonical kana/katakana matching is deliberately conservative. It can recognize spelling variants such as `ようやく`/`漸く` and `マヌケ`/`間抜け` when the tokenizer and reading agree. It does not rewrite subtitle text or Anki fields, and it does not use an online "most common spelling" service.

## Remote Search and Saved Maps

Remote Search uses configurable GitHub-mirror query profiles, including Netflix, Amazon, and Hulu subtitle query terms. Hulu here is a search term for mirrored subtitle filenames; the app does not log in to or query Hulu itself.

**Use Saved Search** loads the selected JSON episode map exactly as saved, including older cache formats or maps created with older provider settings. It does not perform a GitHub search. Use **Refresh** for that saved entry or **Refresh Current Anime** only when you explicitly want to rebuild the remote map and update its JSON.

## OCR Regions and Hover Windows

Selecting an OCR area to redraw now displays the stored box as a labelled translucent rectangle. Dragging creates its replacement; Escape keeps the saved region unchanged. Hover result windows wrap to the active monitor, do not need horizontal scrolling, and are kept above the subtitle overlay without taking keyboard focus.

## Useful Developer Commands

Run model tests:

```powershell
$env:PYTHONPATH='.;SubtitlePlayer'
python -m pytest dev\tests\model
```

Test ruby/furigana for one sentence:

```powershell
python dev\audit_furigana_srt.py --sentence "人間 乗り換える 煮え切らない １ 匹" --compare-split-modes
```

Test ruby/furigana for one SRT file:

```powershell
python dev\audit_furigana_srt.py --file "subs\寄生獣.セイの格率.S01E07.Stage 7.暗夜行路.WEBRip.Netflix.ja[cc].srt" --max-examples-per-file 10
```

Preview Anki ruby fields without creating a note:

```powershell
python dev\preview_anki_ruby.py --word "人間" --sentence "人間 乗り換える 煮え切らない １ 匹" --json
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
