# Jimaku numbering, alignment recovery and global visibility shortcut

Local implementation; no test suite, browser/Anki actions or uploads performed.

- The configured subtitle/control visibility shortcut bypasses foreground-focus
  checks, including queue delivery and dispatch. It also remains available during
  capture without cancelling it. Explicit shortcut disable settings and modal
  settings guards remain in place; other playback/mining shortcuts keep their
  existing focus restrictions.
- Original Jimaku filenames supply season-local and absolute episode numbers.
  Example: One Piece S08E009 / 第791話 means season 8, local episode 9,
  absolute episode 791. Cached original metadata recovers absolute numbers that
  Windows path shortening removed from the downloaded filename.
- A seasonless browser episode with an explicit absolute filename match uses
  that match instead of a legacy arithmetic local/absolute mapping. New explicit
  mappings record their target numbering scope. The mapping dialog shows both
  numbering systems and parses original API/member filenames.
- Browser following and main-window episode navigation use the shared Jimaku
  resolver; adjacent prefetch uses absolute numbers when available, including
  across provider-season boundaries. Existing library/profile files are not
  manually rewritten by this change.
- Async source selection preserves captions already received for the bound
  video and starts alignment again after applying the SRT. Obsolete alignment
  workers are terminated when invalidated. Manual refresh is available during
  automatic analysis and releases its busy state if command submission fails.

Static Python parsing/compilation passed for the six changed Python modules.
Runtime behavior remains unverified. Restart the app; no extension update is
needed for these Python changes.

Manual checks: visibility shortcut with VS Code focused; other mining shortcuts
remain inactive there; One Piece 790/791/792 via browser and app +/-; caption
alignment and manual refresh after changing tabs; neighboring cached files.
