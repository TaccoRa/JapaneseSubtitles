# Fetch observation, AniList login, hover pause, recording keys, ruby boundaries

## Behavior

- The caption observer leaves fetch and XHR untouched until media appears. A
  MutationObserver handles players inserted after document_start; bound-video
  replays also enable observation for players inside shadow roots. Empty pages
  no longer send a timing replay every 100 ms. Request objects, init/credentials,
  receivers, and returned fetch Promises are forwarded unchanged. Extension
  version is 0.3.11. The reported Reddit message was a preload warning attributed
  to the old forwarding line, rather than a JavaScript exception.
- The reported AniList implicit authorization failure is handled by replacing
  that flow with the documented authorization-code grant. The loopback server
  validates Host, state, callback age, and duplicate/oversized parameters before
  exchanging a code once. Failure is shown in the app. Client Secrets are entered
  through a masked local setup field and stored under a per-client Windows
  credential target, separately from account tokens and JSON settings. The
  existing public client ID is retained. No user-account login was performed.
- Copy-popup entry, exit, destruction, tooltip windows, and context menus now
  participate in the existing acknowledged hover pause lease. A video paused
  before hovering is not resumed, nor is a later manual playback intervention.
- Recording keys hit-test an actual subtitle or popup word. A hit uses the normal
  Anki creation/capture pipeline with a frozen explicit segment range; sentence
  text and recording use the same range. Key 6 includes the following segment.
  A blank popup region cannot hit an underlying subtitle. With no word hit, the
  previous capture-only path and its target note remain unchanged.
- The shared counter matcher protects the numeric idioms 万が一 and 万一, and
  generated ruby merging respects their ending boundaries. This prevents the
  false 一本/いっぽん in 万が一本当に without altering subtitle text or legitimate
  counters. The supplied sentence now has まんがいち + ほんとう readings.

## Verification

- 92 extension tests pass, including no-media pages and unchanged fetch credentials.
- 72 targeted Python tests pass for OAuth callbacks, popup pause, and capture ranges.
- 99 ruby tests pass; the real bundled reading engine reproduces the corrected
  sentence in both whole-word and split-kanji modes.
- Five existing popup close/hover tests pass. The complete older fake-Tk file
  still has 10 unrelated fixture/expectation failures: missing capture snapshot
  and source-kind APIs, obsolete subtitle geometry/clock expectations, and
  expected Anki callback dictionaries lacking the existing initiator field.
- A flaky extension test now waits for its actual asynchronous command
  acknowledgement rather than assuming a fixed number of event-loop turns.
- Python compile and JavaScript syntax checks pass.

Activation requires restarting the app, reloading the extension and affected
browser page, and entering the AniList Client Secret locally once. Live AniList
account authorization remains a user step. No Anki notes were created in testing.

AniList code-flow reference:
https://docs.anilist.co/guide/auth/authorization-code
