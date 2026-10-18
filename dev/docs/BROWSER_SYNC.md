# Browser synchronization rollout

Backup: `backup/pre-sync-2026-10-18-121846`, commit
`5e3c610176224ea711517123a1a4b31156a3e81e` on `VoiceControl`.
Development: `feature/browser-sync-native-capture`. Main is unchanged.

## Set up the connection

1. Install `requirements.txt` and restart SubtitlePlayer after stopping the old instance normally.
2. In Chrome open `chrome://extensions`, enable Developer mode, and use **Load unpacked** to select the repository's `browser-extension` directory.
3. Reload each prepared video page once so Chrome installs its content script in the existing nested frames.
4. In SubtitlePlayer choose **Browser…**, copy the pairing code, open the extension on the video tab, select its video, and click **Connect video and enable recording**.
5. Wait for **Aligned**. Choose the experimental `native` recorder in Browser settings to validate capture. The default remains `asbplayer` until live acceptance passes.

Chrome requires the extension button gesture for tab capture. No F12, stream URL, second browser, or embedded video is needed. The extension requests HTTP/HTTPS host access to read clocks in cross-origin video frames and caption resources. The bridge binds only `127.0.0.1`, authenticates before accepting messages, and rejects ordinary webpage origins. Pairing codes live in ignored local configuration, are omitted from profile exports, and never go into URLs.

## Timing and capture

The selected Japanese file remains the content source. Source bytes and cue intervals are unchanged. JSON maps under ignored `timing_maps/` hold mapped intervals, confidence, source hash, media fingerprint and reference fingerprint. Navigation, overlay lookup and frozen recording intervals use the same map. A stale/disconnected clock freezes instead of falling back to the legacy timer. Explicit **Disconnect** returns to paused legacy controls.

Native capture records at normal speed using the connected tab's audio and a video-region image. Audio is PCM WAV encoded in the browser, so native capture needs no external codec executable. After the clip it pauses, attaches only the configured media fields to the explicit note, verifies them, restores viewing speed, and applies Play / Stay paused / Restore. Text-only **add** remains separate from **capture**. Recording operations reject competing recordings, and cancellation never resumes playback.

Failed media stays in ignored `pending_capture_media/` and is retryable with **Retry media**, including after restart. Retries attach to the existing note ID and never create notes or issue playback commands. Existing exact-sentence media reconciliation and fresh-media detection remain in the Anki pipeline.

Automatic OCR is bypassed while connected. OCR and manual offsets remain legacy tools.

## Local audio fallback and packaged solver

Caption interval evidence uses the packaged `alass-core` JSON helper when present. The source checkout has a conservative Python interval bootstrap. Build the helper with `cargo build --release --manifest-path native/subtitle-align/Cargo.toml`, then place `subtitle-align.exe` in `SubtitlePlayer/bin/`. The Windows workflow builds and packages it so end users need no Rust toolchain.

For captionless pages install `requirements-alignment.txt`, then run `python dev/setup_audio_alignment.py` once. Japanese recognition uses a local CPU int8 model and VAD. Only unique, corroborated dialogue anchors earn confidence; repeated phrases, unsupported regions and disagreement remain uncertain. Audio never goes to a speech API. Existing text translation providers keep their existing behavior.

On the current acceptance machine Windows application control blocked a PyAV DLL during Whisper setup. Local transcription has therefore **not passed live validation**. No security protection was disabled.

## Acceptance status

- Python 3.13 regression suite runs with disposable configuration and cache directories.
- The measured episode has a +1.000 / −7.216 second timing step; regression tests check both regions and unchanged source bytes.
- New tests cover clock staleness, buffering, playback speed, transport authentication, exact media identity, map-version freezing, conflicting captures, media-only Anki updates, retry idempotency, cancellation, drift and supported audio-anchor steps.
- Miruro, AniWorld and Hydra live native-capture acceptance is **pending manual extension loading**. Browser automation policy blocks `chrome://extensions`; it must not be bypassed.
- The <150 ms clock p95, <500 ms seek convergence and <250 ms capture-boundary targets remain **unmeasured live**. Native is experimental and is not the default.
- asbplayer's supported explicit-note API integration and rendered custom-caption aggregation need further acceptance work. Its existing hotkey backend remains available; no global load-subtitles broadcast was introduced.

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
