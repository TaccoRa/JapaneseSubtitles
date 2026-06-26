# AI Codebase Notes

These notes are for future AI/code work. They summarize the repository shape, runtime flow, and the main performance/readability/logic observations from a full codebase pass.

## Entry Points

- `SubtitlePlayer/main.py` is the normal app entry point.
- `SubtitlePlayer/app.py` creates `SubtitlePlayerApp`, loads `config.json` plus local overrides, builds the hidden Tk root, starts a subtitle-loading worker, then builds UI, renderer, and controller on the Tk thread.
- `SubtitlePlayer/video_sync_server.py` is an optional Flask helper for receiving video time from an external browser extension/prototype. It is not currently wired into the main app.

## High-Level Runtime Flow

1. `main.py` creates `SubtitlePlayerApp`.
2. `ConfigManager` reads tracked defaults from `config.json` and user/runtime overrides from `config.local.json`.
3. `SubtitleManager` loads the last local subtitle file or initializes a remote subtitle from GitHub/cache.
4. Startup worker parses subtitle data and prepares `display_data`.
5. Tk UI is built:
   - `SettingsUI` for controls.
   - `SubtitleOverlayUI` for the transparent subtitle canvas.
   - `CopyPopup` for right-click copy/Anki actions.
6. `SubtitleRenderer` draws parsed subtitle segments onto the overlay canvas.
7. `SubtitleController` wires playback, hotkeys, episode switching, overlay behavior, OCR, popup, and Anki.

## Important Data Flow

- `SubtitleManager.display_data` is the core subtitle display list. Each item is roughly:
  - cleaned subtitle text
  - start time
  - top-line segments
  - bottom-line segments
- Segment tuples are `(base_text, ruby_text_or_none)`.
- `SubtitleNavigationController._update_subtitle_display()` chooses the active subtitle by bisecting start times, updates copy text, optionally ensures auto ruby, and calls `SubtitleRenderer.render_subtitle()`.
- `PlaybackController` owns current-time mutation and keeps `current_time`, slider, time display, and subtitle redraw in sync.
- `EpisodeController` calls `SubtitleManager.change_episode()`, resets duration/current time, recomputes geometry, and schedules OCR/preload after a switch.

## Main Areas

### Controllers

- `controller/controller.py`: central coordinator. The helper controllers are smaller files, but state still lives mostly on `SubtitleController`.
- `controller/playback_controller.py`: timer, play/pause, seek, subtitle segment jumps, end handling.
- `controller/subtitle_navigation.py`: time display, slider scrubbing, subtitle redraw, auto-ruby refresh scheduling.
- `controller/hotkey_controller.py`: global keyboard/mouse listeners, queued input actions, held-key repeat, pending seek preview.
- `controller/episode_controller.py`: local/remote episode changes and overlay geometry refresh.
- `controller/ocr_controller.py`: OCR capture, temporary window hiding, Tesseract execution, time sync.
- `controller/anki_controller.py`: Anki wait dialog, background note creation, success/busy UI.
- `controller/overlay_controller.py`: hide/show behavior for control and subtitle windows.

### Models

- `model/config_manager.py`: JSON-backed config wrapper. It merges tracked defaults with `config.local.json` overrides and saves local changes atomically through a temporary file.
- `model/subtitle_manager.py`: largest and most complex file. It handles subtitle parsing/cleaning, local file selection, remote GitHub search, cache paths, episode maps, downloads, prefetch, ruby generation coordination, and geometry measurement.
- `model/renderer.py`: canvas renderer with font/measurement/wrap/layout caches, glow drawing, ruby drawing, and hover dictionary/ruby windows.
- `model/anki_client.py`: AnkiConnect API client, context-aware word normalization, grammar marker tags, translation, dictionary lookup, card field building, deck/card routing, media reuse, and stroke SVG sync.
- `model/anki_ruby.py`: wrapper around bundled MeCab/Kakasi executables for ruby generation. Keeps long-lived subprocesses open for performance and splits bracket ruby into segment tuples.

### Views

- `view/settings_ui.py`: main control window and small runtime controls.
- `view/settings_advanced_ui.py`: advanced settings tabs, value coercion, OCR region selection, and apply/default behavior.
- `view/subtitle_overlay.py`: transparent always-on-top subtitle canvas and optional drag handle.
- `view/popup.py`: right-click popup with selectable text, hover ruby/dictionary behavior, Anki hooks, safe close helpers, and Ctrl+A selection.
- `view/overlays.py`: loading/startup overlays.

### Dev And Maintenance

- `dev/tests/model/`: current pytest coverage for parser/ruby/Anki/playback/controller flags.
- `dev/benchmark_slider.py`: slider/render benchmark with per-component timings.
- `dev/benchmark_remote_switch.py`: episode switching benchmark with Tk heartbeat delay tracking.
- `dev/benchmark_spacebar_sync.py` and `dev/benchmark_skip_sync.py`: playback timing drift checks.
- `dev/analysis/`: previous written analyses.
- `anki_modify/`: standalone Anki maintenance scripts, mostly using AnkiConnect.

## Performance Notes

- Subtitle lookup during playback is efficient: current subtitle is found with `bisect` on start times.
- Slider scrubbing avoids synchronous auto-ruby and throttles preview render jobs to roughly one per 16 ms. This is important and should be preserved.
- `SubtitleRenderer` has useful caches for text measurement, wrapping, outline offsets, and full layout. Cache invalidation depends on font/style/overlay width.
- Auto ruby is expensive because it can call MeCab/Kakasi. The code now avoids doing it synchronously while dragging the slider and can refresh after the fact.
- Episode switching is still mostly bounded by `SubtitleManager`: loading/parsing, geometry calculation, remote map lookup, and cache/download checks.
- Background remote episode preparation exists and is valuable. It should stay throttled so old CPUs do not feel mouse/UI stalls.
- OCR is expensive: screenshots, image preprocessing, subprocess/Python OCR, and retry variants can all block a worker for noticeable time. Tk updates must stay on `root.after`.
- Canvas glow cost grows with text length and glow radius because outline drawing creates many canvas text items. Radius `0` should remain a valid fast path.
- Anki operations are network-bound through AnkiConnect and translation APIs. The controller correctly runs note creation off the Tk thread.
- The Advanced Settings Performance tab is gated by `DEBUGGING`. Ctrl+Shift+D toggles debug logging and adds/removes the tab live when the advanced window is open.

## Current Behavior Notes

- Anki card headwords use the selected text plus the full subtitle sentence when available. This lets a selected stem such as `勉強` become `勉強する` when the sentence uses `勉強した`, while keeping noun usage such as `勉強は楽しい` unchanged.
- Dynamic Anki grammar tags are `する-Verb`, `い-Adj`, and `な-Adj`. The old default `subtitleplayer` tag is not added unless a user explicitly puts it in `ANKI_TAGS`.
- `ANKI_TAGS` is exposed in Advanced Settings > Anki > Tags and is parsed from comma/semicolon-separated text.
- In phone mode, the subtitle drag handle is hidden while the pointer is over the full settings or advanced-settings window rectangle, including the native titlebar/X area on Windows.

## Readability Notes

- `SubtitleManager` should be split first if doing major cleanup. Suggested modules:
  - subtitle parsing and cleaning
  - local episode index
  - remote GitHub search/cache
  - episode map heuristics
  - download/prefetch workers
  - geometry calculation
- `SettingsAdvancedUI` is also large but has a clearer tab/spec structure. A future split by tab would help.
- The controller helper files reduce file size, but `_ControllerProxy` makes dependencies implicit. Future tests should prefer explicit small interfaces or constructor-injected services.
- There are still many `print(...)` debug statements, especially in remote search, OCR, Anki controller, and window helpers. Prefer `logging.getLogger(__name__)` with debug/info/warning/exception.
- Some comments are stale or informal. Keep comments where they explain timing/threading decisions; remove comments that only repeat code.
- `requirements.txt` currently mixes runtime, test, benchmark, and experiment dependencies. Consider `requirements.txt` plus `requirements-dev.txt`.

## Logic And Risk Notes

- Runtime state should be written to `config.local.json`, but `config.json` still contains many default keys for runtime/user settings. Keep defaults clean and avoid documenting local machine values in tracked config.
- Remote episode mapping uses filename/provider heuristics. It is useful but fragile. Keep tests around `extract_season_episode_global`, `compute_season_offsets_per_season`, and global/local mapping.
- Same-subtitle Anki media reuse is helpful, but exact text matching can be ambiguous for repeated subtitles. Storing source episode/path metadata on notes would make it safer.
- Any Tk widget operation from a worker thread is risky. The intended pattern is worker thread for slow work and `root.after(0, ...)` for UI mutation.
- Shutdown safety depends on `_shutting_down`, generation IDs, after-job cancellation, listener stopping, and closing subprocesses/sessions. When adding workers, follow that pattern.
- The optional video sync server prints every received time update. If it becomes active in the main app, throttle or log at debug level.

## Dependency Notes

Direct runtime dependencies found in app code:

- `requests`
- `regex`
- `srt`
- `chardet`
- `fugashi`
- `unidic-lite`
- `pynput`
- `PyAutoGUI`
- `Pillow`
- `pytesseract` for OCR feature
- `Flask` for optional `video_sync_server.py`

Development/benchmark/experiment dependencies include:

- `pytest`
- `selenium`
- `geckodriver-autoinstaller`
- `matplotlib`
- `numpy`

`tkinter` is part of the Python standard library on Windows, but may need an OS package on some Linux installs.

## Useful Commands

Run the app:

```powershell
python SubtitlePlayer\main.py
```

Run model tests:

```powershell
$env:PYTHONPATH='.;SubtitlePlayer'
python -m pytest dev\tests\model
```

Run the slider benchmark:

```powershell
python dev\benchmark_slider.py
```

Run remote episode switch benchmark:

```powershell
python dev\benchmark_remote_switch.py
```

Run sync benchmarks:

```powershell
python dev\benchmark_spacebar_sync.py
python dev\benchmark_skip_sync.py
```

Profile auto ruby:

```powershell
python dev\experiments\profile_autoruby.py
```

## Suggested Next Improvements

1. Introduce a small app logger and replace casual prints in touched areas.
2. Continue separating runtime user config from repo defaults.
3. Add tests for episode switching and popup selection using fake Tk objects.
4. Add remote search cache schema/version metadata.
5. Centralize background task throttling, cancellation, and shutdown.
6. Split `SubtitleManager` once tests cover the main episode/search flows.
