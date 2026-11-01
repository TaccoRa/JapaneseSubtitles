# Settings reuse, same-tab navigation and Jimaku fallback

- Removed the episode-map refresh button from the compact window. Existing source
  menu/saved-search refresh remains; the separate Offset alignment button remains.
- Offset input receives space formerly shared equally with Set to/Skip, with a
  minimum width for the numeric value and unchanged alignment button. Offset
  display has at most two decimal places; internal timing precision is retained.
- Advanced Settings saves/validates on close and withdraws instead of destroying
  its widget tree. Reopening reuses tabs, scroll positions and layout caches.
  Config reload occurs only when the closed config snapshot differs. Empty
  settings changes no longer invoke config persistence. The first opening still
  builds the window; its runtime performance has not been measured.
- Extension 0.3.7 sends full page URL and scope with media context, and title/URL
  with episode context. Dynamic document/heading titles take priority over stale
  og:title on SPA pages; fading Netflix titles still retain previous metadata.
  Async episode hashing and desktop dispatch reject mismatched page URLs.
- Same-page metadata changes no longer restore an older remembered episode on
  each announcement. Episode loading is briefly debounced; discarded work caused
  by a changed source can be retried by subsequent observations.
- Failed explicit GitHub searches, saved-map loads and URL downloads that return
  no usable file offer Jimaku with the query prefilled. Failed automatic saved-map
  selection schedules the same chooser for the still-current bound video only.
  File choice is explicit; cancel/stale results do not load another source. The
  picker stays a local subtitle source, not an automatic Jimaku episode backend.

Static Python AST/compile, JS syntax, manifest JSON and whitespace checks only.
No automated tests or browser/Anki live actions, settings edits, restarts or uploads.

User acceptance requires app restart, extension reload to 0.3.7 and one refresh
of the video page: open/close Advanced repeatedly; verify offset width and button;
advance episodes and change anime within one Miruro tab; rapidly switch sources;
verify failed GitHub selection offers Jimaku and cancelling preserves the source.
Runtime behavior and performance remain unverified until these checks.
