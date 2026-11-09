# Controls, word selection, and playback automation

Open **Advanced Settings → Controls**. Keyboard bindings and English/German voice
phrases appear together by action. Microphone, wake prefix, speech models, tests,
recognition feedback, shortcut modes, and enable/disable options are available
there too. The existing **Shortcuts** and **Voice** tabs remain available and edit
the same values. Keyboard shortcuts do not require voice listening to be enabled.

Existing bindings remain unchanged. New keyboard actions start unassigned; enter
your preferred combination in the Keyboard column and click **Apply Now**. Actions
requiring spoken numbers/timestamps display **Requires spoken parameters**.
Use your configured wake prefix when speaking commands. Separate aliases with `;`.
Spoken hover commands toggle on/off; physical hover keys retain hold behavior.

## Word selection

- `mark all` / `highlight all` (`alles markieren`) selects each subtitle word,
  including particles, excluding punctuation and whitespace.
- `copy all` (`alles kopieren`) copies marked Japanese text in subtitle order.
  Without marks it copies the complete current subtitle, preserving line breaks.
- `submark 7 2` selects component 2 of word 7. Subsequently `submark 1` selects
  component 1 of that same parent. A newly selected single word becomes the new
  parent. A subtitle/source change clears the remembered context.
- German sub-selection uses `teil markieren`. Both written and spoken numbers
  are supported, with parent positions 1–40 and component positions 1–20.
- Morphological components remain intact (`化かし | 合い`). All-kanji components
  expose individual kanji. The component list is shown with the selection.

`海賊はな 化かし合いだぜ.` now has six numbered words:
`海賊 | は | な | 化かし合い | だ | ぜ`.
Known lexical adjective expressions and reciprocal verb nouns are kept together;
arbitrary neighboring nouns are not automatically merged.

**Dictionary together** still looks up any selected words as one expression,
using original source spans. The result window shows the query, loading state,
definition, explicit dictionary miss, or error. Other displayed result sections
remain visible. Dictionary misses are not silently replaced with translations.
Lookups run outside Tk, and responses for old selections are discarded.

## Episodes and intros

Restart the app and reload the **SubtitlePlayer Browser Connection** extension
(version **0.3.12**), then reload the video page to activate the new protocol.

While following a browser video, next/previous episode commands and buttons first
operate the website. App subtitles follow only after both episode metadata and a
new video resource/player are confirmed. Merely changing the URL is insufficient.
Failures, unavailable episode controls, and a 15-second timeout leave the app's
episode unchanged. Outside browser-following mode, local navigation is unchanged.

Miruro uses real episode links, with the embedded player's next/previous controls
as a fallback. Netflix uses its real Next Episode control or episode-pane items;
the extension opens the episode menu when necessary. Opaque Netflix IDs are never
incremented. Unavailable season boundaries are reported rather than guessed.

**Playback Automation** in General and Controls contains two independent options,
both **off by default**:

- **Automatically play the next website episode** requests the next episode when
  the current video ends. The website's own autoplay setting remains independent.
- **Automatically skip intro / jump to saved title** uses an available website
  Skip Intro control, or a saved target for the current series. A site-button click
  is only considered successful after an observed time jump. Recording and pending
  episode changes block automation.

For a custom One Piece title position, seek the video to that position, then use
**Controls → Intro / title target for this series → Save current video position**.
The target is stored per series, not globally across shows. **Clear saved target**
returns that series to website-controls-only behavior. Saved targets are used
only when ahead of the current clock and within the video's duration.

The **Skip intro / jump to saved title** action is available even with both
automation options disabled. Assign its keyboard shortcut in Controls, or say
`skip intro`, `skip opening`, `skip to title`, `intro überspringen`,
`vorspann überspringen`, or `zum anfangstitel`, with your wake prefix if required.
`remember title start` / `anfangstitel merken` saves the current target by voice.

## Validation, 2026-11-09

- Isolated Advanced Settings opening baseline: **2346.2 ms**. After lazy tab
  creation, batched layout, and avoiding unchanged voice-grammar validation:
  **284.7 ms first open**, **6.0 ms reopen** in the final visible-window check.
  Individual tabs still incur their construction cost on first selection;
  Controls' first selection measured about **1.02 s** including layout.
- Reproducible isolated benchmark: `python dev/preview_controls.py`; use `--keep`
  to inspect it. This uses disposable defaults/configuration, not user settings.
  Timings and screenshots are in `dev/reviews/unified-controls/`.
- Visually checked Controls, Shortcuts, and Voice. Miruro episode 1012 → 1013
  was navigated in a temporary browser tab; an authenticated Netflix session's
  real Next Episode control changed its watch ID. Netflix's modern episode-pane
  option structure was inspected and incorporated in the adapter.
- The new app/extension navigation handshake, intro automation, saved-target
  fallback, and failure handling are regression-tested using fixtures. They have
  not been tested end-to-end against the running user app after extension reload.
  Site controls may change; unavailable controls produce visible failures.
- A broad Python run found 26 failures in older controller/episode/voice fixtures.
  All 26 also fail against an isolated copy of the unchanged HEAD sources with
  the same tests. Five loopback tests initially blocked by the sandbox passed
  outside it. No test modified real Anki cards or personal configuration.
- The focused Python suite passed **205 tests**, and the extension suite passed
  **104 tests**. Additional final protocol/automation smoke checks also passed.
  A real controller-construction regression test covers the reported startup
  callback ordering failure; that callback now resolves the browser lazily.
