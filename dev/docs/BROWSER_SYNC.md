# Browser synchronization and native recording (0.2.1)

Watch in the existing Chrome tab. SubtitlePlayer owns the chosen Japanese subtitle content, translations, furigana, learning status and Anki fields. Source SRT/ASS/VTT bytes and cue times remain unchanged. Timing corrections and episode-specific trims are separate local data.

## Playback and timing

Voice, buttons, physical shortcuts, hover pause and cue navigation use the bound browser command gateway. Forward/Back use Skip seconds; voice repeats multiply that value. Rapid skips accumulate against the pending seek target. Acknowledgements mirror state and complete voice actions without issuing another command.

The desktop no longer runs an independent playback clock. Offline subtitle browsing and explicit cue navigation remain; timed playback needs a connected video. The extension prevents the website from handling registered physical playback shortcuts so the desktop hook is their sole owner. Typing and disabled/input modes remain respected. A closed connection releases website shortcuts.

Transport, binding and observation freshness are separate. Delayed samples freeze interpolation and trigger a non-mutating PlaybackProbe; they do not mean Chrome disconnected or imply buffering. Repeated bindings for the same video retain timing evidence. Video replacement and explicit episode metadata invalidate the old episode map.

Alignment compares Japanese cue timing with English/German caption timing. Confidence uses subtitle onsets, rather than accepting matching ends alone. Regional onset refinement needs six unique mutual pairs spanning at least 30 seconds, consistent residuals within 150 ms, and improved onset error. Timing steps and rate conversions remain separate regions. Insufficient or ambiguous evidence remains uncertain and cannot authorize capture.

Offset is a trim over the automatic map: negative earlier, positive later, zero automatic. It never moves the video slider, seeks Chrome, or changes playback. The same effective map drives display, navigation and capture. Active clips retain their frozen map/version/trim. Trims live in ignored `subtitle_trims.json`, keyed by source content and stable anime/season/episode identity.

Captionless local audio alignment still requires the optional quantized Whisper model and alignment dependencies. It is not a cloud transcription service. The packaged alass helper uses Rust only during packaging; normal users need no Rust toolchain. Source installations retain the conservative Python interval solver.

## Add, Capture and completion

- **Add only** creates the card, retains pause-on-add, and can copy existing exact-sentence media.
- **Capture** creates a new card and always records fresh audio AND a fresh screenshot, even if another card already has media for that sentence.
- **Capture only** records fresh media onto the explicit last note created by this app run. It never guesses the newest note in a deck.

Recording starts in parallel with card creation. At the mapped boundary the extension stops collecting audio and applies the frozen After capture preference. Play continues without waiting for encoding, note creation or attachment; the previous speed is restored once at that boundary. Stay paused and Restore previous play/pause state remain available. Late Anki completions have no player ownership and never seek or resume playback.

One active recording is separate from pending Anki work. Another recording can start while an earlier card finishes. Anki creation and attachment use a separate serialized queue. Frozen selections, cues, operation IDs and explicit note IDs correlate results in either completion order. Media writes change only the target audio/image fields and are verified before success. Fresh capture does not copy its media backwards onto other notes.

Temporary attachment errors retry automatically against the same note (1, 3, 10 and 30 second delays). Finished bytes and explicit target IDs persist in ignored `pending_capture_media/` across restarts. After exhaustion or permanent failure, the still-bound matching video pauses once; if another clip is active, that clip finishes first. A different tab/episode is never paused because an older operation failed. Successful background recovery never resumes playback.

An uncertain card-creation response is never retried automatically and never matched to an older card by text. The app reports uncertainty and retains any finished recording without assigning a guessed note ID. Check Anki's add history before repeating the add.

## Interface and migration

The compact status row, Browser button and Retry media button are removed. After capture, padding, connection/alignment evidence, pending media and recovery diagnostics are in **Advanced Settings > Anki > Browser Recording**. Existing recording and verified-completion indicators remain.

Native is the only recorder, as explicitly requested. asbplayer's installed Chrome extension and existing Anki notes are untouched. Retired profile values are archived under `ARCHIVED_PLAYBACK_SETTINGS` on load/save, rather than executed. Profiles, vocabulary, decks/custom fields, histories, positions, voice recognition and display/lookup settings remain.

| Removed workaround/settings | Replacement |
|---|---|
| asbplayer recorder/backend selector | Native recording |
| Capture hotkey, window title/process targeting, focus restore and mouse movement | Explicit bound tab/document/frame/video commands |
| Injected arrows/Space and synthetic-key suppression | One command gateway and ownership of real shortcuts |
| Positive/negative capture delays and waiting for card creation | Parallel recording and Anki creation |
| External-note media/timestamp polling and copy-back | Operation-owned bytes attached to an explicit note |
| Delayed capture-end jumps and legacy resume paths | Extension-owned recording boundary |
| Simulated playback timer and speed progression | Authoritative browser observations |
| OCR clock/startup/episode/post-add sync and calibration controls | Browser clock and automatic alignment |
| Legacy global/anime offsets | Episode-specific map trim |

## Chrome lifecycle and requests

Chrome requires invoking the extension to grant tab audio capture. Opening its popup acquires recording automatically for the connected tab when allowed. The existing stream survives repeated connect/enable requests and same-tab episode changes. Reloading the extension, restarting Chrome, tab closure or ending the stream can revoke it; a desktop voice command cannot bypass Chrome's grant requirement.

The authenticated loopback bridge uses port 8767. Its health endpoint contains only service/version information. Backoff avoids repeatedly constructing refused WebSockets while the desktop is stopped. Historical Chrome Errors entries remain historical until cleared; a service stopping between readiness check and handshake can still cause a network error.

Caption fetches are limited to the bound video, omit credentials/referrers and reject automatic redirects. Captions are parsed as timing text, never executed as script. Screenshot processing accepts local PNG data only. The companion does not open advertising pages or disable browser/antivirus protections.

## Validation

The local Windows Python 3.13 preservation suite and actual extension scripts run with disposable test configuration and caches. Original-source verification checked all 287 backed-up SRT/VTT/ASS/SSA files with no changed hashes. Tests cover fresh Capture versus Add-only reuse, both note/media completion orders, overlapping pending cards, recording boundary policies, stale probes, accumulated skips, safe retries and terminal failure pauses, trim and onset refinement, timing steps, rate drift and uncertain evidence.

The updated app has been restarted and its compact controls and Advanced Anki preferences inspected. Full live acceptance of this version, the reported episode lateness, and the <150 ms clock p95 / <500 ms seek / <250 ms recording-boundary targets remain pending. Browser approval blocked Miruro access during this validation; no workaround was used. Prior successful capture logs and simulated measurements are not proof of this version's live acceptance. Captionless Whisper acceptance is also still pending on this machine.

## Feature-preservation checklist

| Retained group | Capabilities | Regression coverage |
|---|---|---|
| Subtitle sources | Kitsunekko search, saved searches, provider preferences, episode/season mapping, local files, prefetch and remembered progress | [subtitle manager](../tests/model/test_subtitle_manager.py), [remote search](../tests/model/test_remote_search_plan.py), [cache](../tests/model/test_remote_search_cache.py) |
| Display | Transparent movable overlay, phone mode, wrapping/position/styles, cleanup, source/generated furigana | [controller](../tests/model/test_controller_fake_tk.py), [flags](../tests/model/test_controller_flags.py), [furigana](../tests/model/test_furigana_splitter.py), [ruby spacing](../tests/model/test_ruby_spacing.py) |
| Lookup and selection | Popup, persistent/indexed highlights, combined expressions, dictionaries, DeepL/Google, hover layers and copy variants | [flags](../tests/model/test_controller_flags.py), [fake Tk](../tests/model/test_controller_fake_tk.py), [voice](../tests/model/test_voice_commands.py) |
| Voice and shortcuts | Offline English/German, aliases/wake phrases/mics, indexed/batch actions, cancellation, modes and configurable shortcuts | [voice service](../tests/model/test_voice_service.py), [commands](../tests/model/test_voice_commands.py), [playback](../tests/model/test_playback_controller.py) |
| Anki content | Custom fields, decks/routing, dictionary forms, grammar/anime/custom tags, context/ruby/translations/definitions, previews/strokes | [Anki](../tests/model/test_anki_client.py), [controller](../tests/model/test_anki_controller.py), [ruby](../tests/model/test_anki_ruby.py), [sentence repair](../tests/model/test_sentence_ruby_repair.py) |
| Capture | Add-with-capture, capture-only, padding/images, Add-only exact-sentence reuse, fresh Capture, frozen targets and completion | [duplicate media](../tests/model/test_capture_duplicate_media.py), [Anki controller](../tests/model/test_anki_controller.py), [native capture](../tests/model/test_native_capture.py) |
| Learning status | Local vocabulary, Anki/WaniKani, maturity/style/kana/kanji matching, editing and import/export | [annotations](../tests/model/test_annotation_provider.py), [word DB](../tests/model/test_word_database.py), [Anki sync](../tests/model/test_anki_word_sync.py), [WaniKani](../tests/model/test_wanikani_client.py) |
| Settings/recovery | Profiles/search, positions, history/statistics, startup, diagnostics | [profiles](../tests/model/test_settings_profiles.py), [config](../tests/model/test_config_manager.py), [startup](../tests/model/test_app_startup.py), [flags](../tests/model/test_controller_flags.py) |
