# Browser synchronization, local alignment and recording

Watch in the existing Chrome tab. SubtitlePlayer owns the chosen Japanese subtitle content, translations, furigana, learning status and Anki fields. Source SRT/ASS/VTT bytes and cue times remain unchanged. Timing corrections and episode-specific trims are separate local data.

## Playback and timing

Voice, buttons, physical shortcuts, hover pause and cue navigation use the bound browser command gateway. Forward/Back use Skip seconds; voice repeats multiply that value. Rapid skips accumulate against the pending seek target. Acknowledgements mirror state and complete voice actions without issuing another command.

The desktop no longer runs an independent playback clock. Offline subtitle browsing and explicit cue navigation remain; timed playback needs a connected video. The extension prevents the website from handling registered physical playback shortcuts so the desktop hook is their sole owner. Typing and disabled/input modes remain respected. A closed connection releases website shortcuts.

Transport, binding and observation freshness are separate. Delayed samples freeze interpolation and trigger a non-mutating PlaybackProbe; they do not mean Chrome disconnected or imply buffering. Repeated bindings for the same video retain timing evidence. Video replacement and explicit episode metadata invalidate the old episode map.

The selector beside Offset chooses **Dialog/Audio** (default) or **Website-Untertitel** per video. Neither changes the selected Japanese text source. Analysis never issues a seek. A mode switch invalidates old workers/results and changes cache scope, while an active recording retains its frozen map/trim/backend.

**Dialog/Audio** uses tab PCM and a local CPU/int8 Whisper base model. Language is detected automatically. Only Japanese recognition can create Japanese text anchors; unique matches can cover up to three adjacent SRT cues. Six independent anchors spanning 30 seconds are required per accepted region. Robust rate/intercept fitting separates drift from corroborated steps. Other audio languages use corroborated speech-onset patterns, never Japanese text matching. Silence/repeated phrases cannot establish alignment. Confident language changes discard old speech anchors and revalidate. Audio-track fingerprints also invalidate maps when exposed by the player; private streaming players still need live track-switch acceptance.

**Website-Untertitel** prefers complete loaded tracks, with rendered caption changes as supplemental local timing evidence. Split/merged lines contribute shared onsets rather than one-to-one row pairing. The Python local-region solver no longer requires two 120-second voting windows when six distinct mutual onsets span 30 seconds. Competing repeated patterns remain uncertain. Regions preserve corroborated breaks and frame-rate corrections; onset refinement requires consistent residuals within 150 ms and improved onset error. Only supported cue spans authorize recording.

Offset is an independent manual trim: negative earlier, positive later, zero automatic. It never moves the video slider, seeks Chrome, or changes playback. The automatic offset is shown separately, with a brief inline announcement at the first confirmed model or a region transition in either direction. Continuous drift updates the number without a notice on every clock sample. Unconfirmed spans use original-time SRT preview; they cannot authorize native recording. The same confirmed map plus trim drives display, cue navigation and capture.

New preferences live in ignored `alignment_preferences.json`, keyed by stable media/episode identity, source hash and mode. Old `subtitle_trims.json` remains intact and is archived in the new preferences; new per-mode trims start at zero to prevent applying a legacy +25-second estimate on top of a new automatic correction. Timing-cache scopes additionally include the observed media/audio fingerprint and reference kind. No retimed SRT is written.

The one-time model-data setup is `python dev/setup_audio_alignment.py`. After download, recognition runs locally with no audio upload. The packaged alass helper is built by the existing Windows GitHub workflow, which previously passed at `b890791`. Source installations retain the Python solver. Local Rust compilation was blocked by Windows and must not be retried or bypassed. If Windows blocks the packaged helper with error 4551, this process remembers that block and uses Python without repeatedly executing the helper.

## Add, Capture and completion

- **Add only** creates the card, retains pause-on-add, and can copy existing exact-sentence media.
- **Capture** creates a new card and always records fresh audio AND a fresh screenshot, even if another card already has media for that sentence.
- **Capture only** records fresh media onto the explicit last note created by this app run. It never guesses the newest note in a deck.

With Native, recording starts in parallel with card creation. At the mapped boundary the extension stops collecting audio and applies the frozen After capture preference. Play continues without waiting for encoding, note creation or attachment; the previous speed is restored once at that boundary. Stay paused and Restore previous play/pause state remain available. Late Anki completions have no player ownership and never seek or resume playback.

One active recording is separate from pending Anki work. Another recording can start while an earlier card finishes. Anki creation and attachment use a separate serialized queue. Frozen selections, cues, operation IDs and explicit note IDs correlate results in either completion order. Media writes change only the target audio/image fields and are verified before success. Fresh capture does not copy its media backwards onto other notes.

Temporary attachment errors retry automatically against the same note (1, 3, 10 and 30 second delays). Finished bytes and explicit target IDs persist in ignored `pending_capture_media/` across restarts. Cancelled operations remain cancelled across restart and are not retried. After exhaustion or permanent failure, the still-bound matching video pauses once; if another clip is active, that clip finishes first. A different tab/episode is never paused because an older operation failed. Successful background recovery never resumes playback.

An uncertain card-creation response is never retried automatically and never matched to an older card by text. The app reports uncertainty and retains any finished recording without assigning a guessed note ID. Check Anki's add history before repeating the add.

## Interface and migration

The compact status row, Browser button and Retry media button are removed. After capture, padding, connection/alignment evidence, pending media and recovery diagnostics are in **Advanced Settings > Anki > Browser Recording**. Existing recording and verified-completion indicators remain.

Native remains the default recorder. asbplayer is the optional original-shortcut recorder, described below. The installed external extension and existing notes remain untouched. Retired profile values are archived under `ARCHIVED_PLAYBACK_SETTINGS` on load/save, rather than executed. Profiles, vocabulary, decks/custom fields, histories, positions, voice recognition and display/lookup settings remain.

| Removed workaround/settings | Replacement |
|---|---|
| Global focus-dependent playback keys | Browser gateway; original external recording hotkey stays restricted to asbplayer |
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

Caption fetches are limited to the bound video and current media fingerprint, omit credentials/referrers and reject automatic redirects. Each resource is requested once; a network/HTTP failure stops further external caption requests for that video, preventing repeated protection alerts caused by an automatic retry loop. Already loaded caption intervals remain available. Failure diagnostics record the hostname, without URL paths or tokens. Captions are parsed as timing text, never executed as script. Screenshot processing accepts local PNG data only. The companion does not open advertising pages or disable browser/antivirus protections.

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

Startup recovery checks AnkiConnect availability quietly before attaching saved recordings. When Anki is closed, recovered operations retain their media and explicit note IDs and wait five seconds between availability checks; this does not consume attachment retries or trigger playback failure handling. New capture failures retain bounded attachment retries.

Netflix seeks use a site-specific adapter to the player session in the bound document. Direct `video.currentTime` writes are disabled on Netflix, including capture preparation. If the private player interface is absent or the session is ambiguous, the command fails without a raw seek fallback. The adapter requires live validation after extension reload; clock connection alone does not establish Netflix playback/capture compatibility.

Media presentation: the browser sends a separate `MediaContext` with a human-readable title. Netflix reads its visible `video-title` control rather than the generic tab title and retains it while controls fade. Explicit episode metadata keeps the selected subtitle library's display name; a standalone video with no detected episode shows its website title and Movie, with episode navigation disabled. This only changes presentation, never source files, library names, note tags or episode maps. Disconnecting restores library presentation.

Local movie source correction: selecting a local subtitle exits remote source mode, persists that choice, and derives the display name from the filename (e.g. Shrek), rather than retaining a remote anime or using the Downloads directory. Selected episodic subtitles take precedence over stale browser movie metadata. Cues with three or more lines retain all their text within the two-row overlay. Before automatic alignment is established, original SRT timing plus manual trim remains available for display and lookup; recording still requires verified alignment. Connected source changes preserve the browser slider duration and never seek to a saved desktop position. Unmatched slider-release events cannot seek. Playback commands log their call origin to investigate reported unexpected Netflix restarts; that reported restart has not yet been reproduced live.

Browser tab/media associations are stored locally in ignored `browser_subtitle_sources.json`. A matching initial title or an explicit source selection records the cached file, canonical/display names, local/remote mode and remote episode-search metadata. Returning to that known media restores the source and episode controls without issuing playback commands. Associations use media page identity, not the currently active tab or an opaque video instance; a new media item is never assigned another video's subtitles by guessing. Existing selections made before this feature need one initial association. Missing cached files produce an actionable error; source loading failures restore the previous selection. Pending Anki operations keep their frozen targets.

Netflix timing references now include its visible `.player-timedtext` caption container. Enabled site subtitles provide observation intervals for local alignment of the selected SRT; they do not replace its text. This path needs an analysis period and corroborated timing evidence, and no Shrek offset has been claimed measured from unit tests. Movie episode-entry focus loss preserves Movie rather than restoring a stale anime episode number.

## asbplayer: original recording workflow

Select **Advanced Settings → Anki → Browser Recording → Recorder → asbplayer (original capture shortcut)**. This restores the pre-sync recorder workflow: create the Anki card first, focus the configured Chrome/asbplayer window, send the existing recording shortcut, wait for both media fields, then update to the frozen subtitle end and optionally continue. There is **no asbplayer API, WebSocket server, media proxy, minimum API-version requirement or native-recording grant** for this backend. Existing asbplayer audio-recording permissions and shortcut/Anki settings still apply, exactly as for the original external recorder.

The original `POST_ADD_CAPTURE_EXTERNAL_HOTKEY`, window title, post-card wait, media-check interval, resume and Capture-only resume preferences are recovered from `ARCHIVED_PLAYBACK_SETTINGS` into adapter-specific `ASBPLAYER_*` controls. Defaults are the original `ctrl+shift+y`, `ABSPlayer, Google Chrome, Chrome`, zero delay and 1500 ms polling. Old negative early-capture estimates remain archived; capture now waits for the exact newly created note. Native's After capture setting remains independent.

The original shortcut updates asbplayer's last-added card. Before sending it, SubtitlePlayer checks that the expected explicit note is still the newest and prevents another SubtitlePlayer add from replacing that target during this operation. It does not silently choose a different note. Other applications can still create notes independently: the old hotkey cannot provide the explicit `noteId` guarantee of an API. Such changes, missing media, or unexpected text changes are reported without claiming success. Existing content is never blindly restored over a user edit.

Capture verifies fresh audio and image on that exact target. Another same-sentence card's media cannot end the recording early. Only Add without capture reuses previous sentence media. In connected mode, the browser clock already follows asbplayer's movement to the clip end; completion issues at most one necessary end correction and optional resume. It never rewinds playback that continued while Anki finished. Normal voice/Skip/playback commands retain the browser gateway; the external recording chord is restricted to this selected backend. Native remains the default for newly migrated native-only profiles and retains parallel recording/card creation.

Cancelled operations cannot send delayed shortcuts, resume later, or get retargeted. The external asbplayer recorder itself must be stopped using its own controls when necessary; no API/proxy is assumed. An app/backend restart never replays a recording hotkey automatically. Existing pending metadata can verify the exact note on recovery but cannot create a new note or seek the player.

## Recovery, input and acceptance evidence

Warnings stay in diagnostics/logs; no floating failure windows are created. Deleted-note recoveries persist `terminalError` and never retry or pause current playback. The two historical operations targeting missing notes are retained locally with their media. Transient attachment retries are informational, not warning popups.

X1 remains Add with Capture. X2 remains Add only over subtitles and subtitle toggle elsewhere; it is not Capture-only. Shift+V and voice Capture-only target an app-created note and report missing target, changed source/video, uncertain timing, disabled recorder and unavailable backend distinctly. Voice wake-word settings and recognition behavior are unchanged.

Additional regression coverage: [modes, trim, steps, drift and diagnostics](../tests/model/test_alignment_modes.py), [original asbplayer shortcut and frozen-target completion](../tests/model/test_asbplayer_capture.py), [native playback/recovery](../tests/model/test_native_playback.py), [native media and queue](../tests/model/test_native_capture.py).

2026-10-19 local acceptance: the base CPU/int8 model loaded successfully and generated no speech anchors for silence. Real AnkiConnect integration verified native media attachment on isolated notes, preserving text and tags; those temporary notes/media were removed. This validates Anki attachment, **not live browser recording**. Logs also showed restoration of the Shrek source after tab binding without an app seek. Live Netflix/anime timing, audio-track changes, recorder boundaries and clock p95 remain unmeasured: computer-use safety stopped browser interaction because the current Chrome URL could not be established confidently. Do not claim the 150 ms / 250 ms targets or full site acceptance from unit tests.

asbplayer acceptance: focused regressions cover card-first dispatch, the original window/shortcut settings, cancellation, explicit-target fresh media and completion without duplicate seeks. Actual external recording still needs live acceptance. Native attachment tests do not establish asbplayer recording success.
