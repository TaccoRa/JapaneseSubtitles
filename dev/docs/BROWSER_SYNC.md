# Browser synchronization rollout

Backup: `backup/pre-sync-2026-10-18-121846`, commit
`5e3c610176224ea711517123a1a4b31156a3e81e` on `VoiceControl`.
Development: `feature/browser-sync-native-capture`. Main is unchanged.

## Set up the connection

1. Install `requirements.txt` and restart SubtitlePlayer after stopping the old instance normally.
2. In Chrome open `chrome://extensions`, enable Developer mode, and use **Load unpacked** to select the repository's `browser-extension` directory.
3. When upgrading an unpacked copy, use **Reload** on the existing extension once. Version **0.1.2** requests `scripting` permission to recover its content scripts in existing nested frames. Routine page reloads are no longer required for discovery.
4. Open the extension on the video tab. It discovers frames and automatically binds the single visible watching video. Approve the first connection in SubtitlePlayer once. **Connect video** also resumes automatic connection after an explicit disconnect. A code is only a manual fallback.
5. Wait for **Aligned**. Choose the experimental native recorder under **Advanced Settings → Anki → Browser Recording**, then click **Enable native recording** in the extension once (Chrome requires this gesture for tab audio). **After capture** is in the same settings section. The default remains `asbplayer` until live acceptance passes.

Chrome requires the extension button gesture for tab capture. No F12, stream URL, second browser, or embedded video is needed. The extension requests HTTP/HTTPS host access to read clocks in cross-origin video frames and caption resources. The bridge binds only `127.0.0.1`, authenticates before accepting messages, and rejects ordinary webpage origins. Pairing codes live in ignored local configuration, are omitted from profile exports, and never go into URLs.

Automatic first pairing requires an explicit approval in the desktop app and a Chrome extension origin; ordinary pages and clients without an origin cannot use it. Approved origins and authentication tokens stay local to the computer and are excluded from learning profiles.

## Timing and capture

The selected Japanese file remains the content source. Source bytes and cue intervals are unchanged. JSON maps under ignored `timing_maps/` hold mapped intervals, confidence, source hash, media fingerprint and reference fingerprint. Navigation, overlay lookup and frozen recording intervals use the same map. A stale/disconnected clock freezes instead of falling back to the legacy timer. Explicit **Disconnect** returns to paused legacy controls.

Native capture records at normal speed using the connected tab's audio and a video-region image. Audio is PCM WAV encoded in the browser, so native capture needs no external codec executable. After the clip it pauses, attaches only the configured media fields to the explicit note, verifies them, restores viewing speed, and applies Play / Stay paused / Restore. Text-only **add** remains separate from **capture**. Recording operations reject competing recordings, and cancellation never resumes playback.

Clock synchronization does not acquire audio. When asbplayer is selected, the companion releases its audio stream so the two recorders do not compete. Native recording uses audio-only tab capture while watching; a still image is taken at the requested video time using `captureVisibleTab`, then cropped to the video. It checks the bound tab before and after the screenshot, checks the document, and refuses a changed layout. This avoids continuous video capture's special Chrome fullscreen behavior. Live fullscreen acceptance is still pending.

Disconnect/reconnect cleanup is idempotent and serialized. Shutdown releases the bridge even when an existing Quit path exits the Tk loop directly. A process lock prevents duplicate launches. Connection refusal shows a waiting status and retries with backoff; port conflicts have a Retry connection control. Browser errors are retained long enough to read, and local logs record capture operation IDs and timing evidence.

Miruro's explicit `ep` number and AniWorld's season/episode path are supported episode references. The desktop follows within the matching selected series, invalidates the previous timing map, and loads the next original subtitle file without seeking the browser to a remembered desktop position. Replacement frames/documents are rebound within the already watched tab. A different series requires selecting matching subtitles. Unknown episode identifiers are not inferred from a clock reset.

English/German references are prioritized rather than merged across languages. Captions discovered before a replacement player binds are replayed after binding; temporary resource failures retry. Stale alignment-worker results cannot replace newer caption evidence. The measured +1.000 / −7.216-second step remains an automatic interval-map correction, with no SRT edits.

Failed media stays in ignored `pending_capture_media/` and is retryable with **Retry media**, including after restart. Retries attach to the existing note ID and never create notes or issue playback commands. Existing exact-sentence media reconciliation and fresh-media detection remain in the Anki pipeline.

Automatic OCR is bypassed while connected. OCR and manual offsets remain legacy tools.

Connected cues are no longer erased by the legacy wall-clock subtitle timeout. Streaming duration updates preserve the media identity and timing map. Alignment starts once both clock and captions are available, whichever arrives first; unsuccessful evidence is not retried continuously. **Browser… → Show subtitles** restores the overlay if needed. Seek completion checks the actual clock as well as player events, and real failures include the requested and observed positions.

## Local audio fallback and packaged solver

Caption interval evidence uses the packaged `alass-core` JSON helper when present. The source checkout has a conservative Python interval bootstrap. Build the helper with `cargo build --release --manifest-path native/subtitle-align/Cargo.toml`, then place `subtitle-align.exe` in `SubtitlePlayer/bin/`. The Windows workflow builds and packages it so end users need no Rust toolchain.

For captionless pages install `requirements-alignment.txt`, then run `python dev/setup_audio_alignment.py` once. Japanese recognition uses a local CPU int8 model and VAD. Only unique, corroborated dialogue anchors earn confidence; repeated phrases, unsupported regions and disagreement remain uncertain. Audio never goes to a speech API. Existing text translation providers keep their existing behavior.

On the current acceptance machine Windows application control blocked a PyAV DLL during Whisper setup. Local transcription has therefore **not passed live validation**. No security protection was disabled.

## Acceptance status

- Python 3.13 regression suite runs with disposable configuration and cache directories.
- The measured episode has a +1.000 / −7.216 second timing step; regression tests check both regions and unchanged source bytes.
- New tests cover clock staleness, buffering, playback speed, transport authentication, exact media identity, map-version freezing, conflicting captures, media-only Anki updates, retry idempotency, cancellation, drift and supported audio-anchor steps.
- Miruro's live readable clock and English/German track exposure have been inspected. Miruro, AniWorld and Hydra live native-capture acceptance remains **pending the updated extension reload and recording gesture**. Browser automation policy blocks `chrome://extensions`; it must not be bypassed.
- The <150 ms clock p95, <500 ms seek convergence and <250 ms capture-boundary targets remain **unmeasured live**. Native is experimental and is not the default.
- `python dev/validate_sync_local.py` exercises the actual desktop app and WebSocket transport against a simulated browser clock using archived subtitles. After fixing queued-observation timestamps and synchronous furigana work, the local run measured **92.36 ms p95** and **94.79 ms maximum** clock error across 243 samples; the post-seek phase checks were within 82.02 ms after a 200 ms settling interval. Buffer/pause/disconnection freeze checks passed. These are simulated-transport measurements, not site/capture acceptance.
- The [first Windows acceptance run](https://github.com/TaccoRa/JapaneseSubtitles/actions/runs/39120855168) passed, including compilation and tests with the alass helper. Its downloadable artifact contains the helper and extension. Automatic approval review rejected reusing Git credentials to fetch this artifact locally; no credentials were retrieved through that approach.
- Rendered custom-caption observations can establish partial timing maps; unsupported regions stay uncertain. Live acceptance of this path remains pending.
- asbplayer's supported explicit-note API integration needs further acceptance work. Its existing hotkey backend remains available; no global load-subtitles broadcast was introduced. The new bridge uses port 8767 to avoid asbplayer's default proxy port 8766.
- Current regression coverage includes 485 Python tests and 37 extension tests. The latest simulated desktop/transport run detected both timing regions with **93.20 ms p95**, **557.85 ms maximum**, and a **330.71 ms** largest post-seek phase error after the settling interval. The transient maximum means live convergence and boundary acceptance must still be measured.
- Follow-up live logs confirmed connected/aligned Chrome playback. Captures encountered an Anki editor write lock and a missing native-recording gesture. Native editor-lock failures retain media and now give explicit Retry media instructions; capture-only preflight failures no longer escape into the voice input pump. The requested `ぬれ` case also exposed an incorrect potential-form conversion (`ぬれる` to `ぬる`); contextual verb normalization now preserves the wet/get-wet dictionary form.
- `python dev/validate_native_anki.py --write-test-note` verified audio/image storage and retrieval, idempotent attachment, and preserved text using a synthetic note in `SubtitlePlayer::Capture validation`; its cards are suspended. This is a real local Anki check, **not a live video capture**. The requested live word test is **ぬれ**, episode 6 cue 12, original 148.816–152.361 s, sentence `通気性は良いが / ぬれにくく 燃えにくい`.

## Feature-preservation checklist

The tests below are local regressions, not claims of complete end-to-end acceptance for every option.

| Retained group | Capabilities | Regression coverage |
|---|---|---|
| Subtitle sources | Kitsunekko search, saved searches, provider preferences, episode/season mapping, local files, prefetch and remembered progress | [subtitle manager](../tests/model/test_subtitle_manager.py), [remote search](../tests/model/test_remote_search_plan.py), [cache](../tests/model/test_remote_search_cache.py) |
| Display | Transparent movable overlay, phone mode, wrapping/position/styles, cleanup, source/generated furigana | [controller](../tests/model/test_controller_fake_tk.py), [flags](../tests/model/test_controller_flags.py), [furigana](../tests/model/test_furigana_splitter.py), [ruby spacing](../tests/model/test_ruby_spacing.py) |
| Lookup and selection | Popup, persistent/indexed highlights, combined expressions, dictionaries, DeepL/Google, hover layers and copy variants | [flags](../tests/model/test_controller_flags.py), [fake Tk](../tests/model/test_controller_fake_tk.py), [voice](../tests/model/test_voice_commands.py) |
| Voice and shortcuts | Offline English/German, aliases/wake phrases/mics, indexed/batch actions, cancellation, modes and configurable shortcuts | [voice service](../tests/model/test_voice_service.py), [commands](../tests/model/test_voice_commands.py), [playback](../tests/model/test_playback_controller.py) |
| Anki content | Custom fields, decks/routing, dictionary forms, grammar/anime/custom tags, context/ruby/translations/definitions, previews/strokes | [Anki](../tests/model/test_anki_client.py), [controller](../tests/model/test_anki_controller.py), [ruby](../tests/model/test_anki_ruby.py), [sentence repair](../tests/model/test_sentence_ruby_repair.py) |
| Capture | Add-with-capture, capture-only, padding/images, exact-sentence reuse, fresh-media detection, frozen targets and completion | [duplicate media](../tests/model/test_capture_duplicate_media.py), [Anki controller](../tests/model/test_anki_controller.py), [native capture](../tests/model/test_native_capture.py) |
| Learning status | Local vocabulary, Anki/WaniKani, maturity/style/kana/kanji matching, editing and import/export | [annotations](../tests/model/test_annotation_provider.py), [word DB](../tests/model/test_word_database.py), [Anki sync](../tests/model/test_anki_word_sync.py), [WaniKani](../tests/model/test_wanikani_client.py) |
| Settings/recovery | Profiles/search, positions, history/statistics, startup, diagnostics and legacy controls | [profiles](../tests/model/test_settings_profiles.py), [config](../tests/model/test_config_manager.py), [startup](../tests/model/test_app_startup.py), [flags](../tests/model/test_controller_flags.py) |

The connected clock and explicit browser commands replace the independent timer and focus-dependent playback hotkeys. Disconnection freezes connected playback; choosing legacy mode is explicit. Changes in content/features beyond these replacements require an updated checklist and tests.
