# Synchronization, responsiveness and playback cleanup

## Approved behavior

- Website caption timing remains the default; audio alignment is selected manually.
- Prioritize episode/tab switching and alignment work. Preserve manual trim.
- Keep offline subtitle browsing and the original asbplayer capture/hover fallback.
- Local edits only. Static checks only; no automated tests or browser/Anki actions.

## Implementation

- Immutable timing maps cache their bisect indexes, version and evidence summary.
  The controller reuses a shifted map until either its base or trim changes.
- Source hashes are prepared with subtitle display payloads off the UI thread.
  The playback loop checks a display revision and throttles unchanged-file stats
  to once per second instead of copying cue arrays and resolving files per tick.
- Prepared display payloads remain in the existing four-entry, settings/file-scoped
  cache for revisits. Browser and app navigation share this preparation path.
- Saved-source restoration prepares data asynchronously. Numbered remote sources
  use the existing episode resolver instead of first loading a remembered episode
  synchronously. Results must still match the tab, URL, source and display settings.
- Source preparation uses two workers with replaceable queued jobs per entry point.
  Bridge observations are processed before completed source changes are committed.
  Source workers are daemon threads, so an old network timeout does not keep the
  application process alive after shutdown.
- Timing cache reads, map serialization and reference writes run on one background
  worker. Restored maps are accepted only for their still-current generation.
- Caption timing received ahead of its matching video snapshot is retained briefly
  and replayed once the matching fingerprint arrives. Invalidated alignment work
  cannot launch a stale subprocess in the dequeue/start cancellation gap.
- Advanced > General > Subtitle synchronization shows the reference, method,
  confirmed cue coverage, offset range and manual trim. Repeated unchanged Tk
  values are not written again; evidence refreshes at most twice per second.
- One internal transport boundary now sends all player commands, including abort
  against the frozen identity of the owned native clip.
- Progress saving uses the browser clock and video duration. Removed the old
  additional desktop elapsed-time increment, inactive timer methods/callers,
  unused controller OCR state and unused external-key/window imports.

## Retained and remaining limits

- The asbplayer adapter, its capture shortcut/window targeting and disconnected
  hover fallback remain required and were not changed in this implementation.
- Offline cue navigation, frozen capture times and per-episode trim remain.
- Existing settings layout caching/window reuse remain; cold-open latency and
  Windows scaling have not been measured. No new alignment solver or confidence
  thresholds were introduced. Clock error and speed improvements are unmeasured.
- No subtitle files, existing Anki notes or personal configuration were edited by
  these changes. No extension update is needed; restart SubtitlePlayer.

## Static verification and user checks

AST parsing and in-memory compilation passed for the 14 touched Python modules.
Source searches found one player-command transport call and no remaining calls
to the removed simulated-clock methods. No runtime behavior is claimed verified.

User checks:

1. Switch episodes and tabs repeatedly, including revisiting a cached episode.
   Playback and input should remain responsive; an old result must not replace it.
2. Check automatic offset, manual trim and the new evidence display. Reopen a known
   episode and use refresh; captions should work without selecting audio mode.
3. Save progress after watching for several minutes, including paused/buffering
   playback; the saved position should match the video without accumulated time.
4. Exercise skip, hover and native capture; then the original asbplayer path with
   and without a browser connection. Connected mode must not send playback keys.
5. Check capture cancellation, start-padding display and post-capture playback.
   A late source or timing result must not change an active clip's frozen target.
