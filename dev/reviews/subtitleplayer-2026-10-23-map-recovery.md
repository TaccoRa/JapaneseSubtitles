# Saved source recovery and browser event overflow

Reported logs show a missing remembered local file, an HTTP 404 for a One Piece
subtitle path, and queue.Full while emitting Disconnected. The Kiseijuu startup
log describes a search-plan refresh, not proof of a second subtitle download.

Changes:
- Prefer an already loaded matching anime/episode file. Always apply the saved
  display name, including a name-only change.
- Missing remembered files fall through to saved-search recovery without a
  repeated fatal restoration warning. Remembered search identity can supply a
  match when the browser uses another title alias.
- One automatic map refresh after a confirmed HTTP 404; exclude that failed path
  from the retried selection and saved map. Other download failures do not start
  refresh loops. A refresh does not guarantee an upstream replacement exists.
- Episode-row refresh button requests a background saved-map refresh for the
  bound browser episode. Existing offline refresh remains available. A distinct
  request token prevents an older download completing a newer refresh.
- Browser event queue coalesces periodic clocks under its lock. Transition,
  control and completion events remain ordered and are not dropped at a fixed
  queue capacity. Critical backlog is intentionally lossless; this does not
  promise immunity to indefinitely stalled UI processing.

Static Python parsing/compilation and whitespace checks only. No automated tests,
network requests, browser/Anki actions, runtime-cache changes, restarts or uploads
were performed during implementation.

User checks after app restart (no extension reload required): One Piece tab and
episode refresh; missing local-cache restoration; title change between saved
anime; switching to a tab without video and back. Runtime behavior unverified.
