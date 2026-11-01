# Miruro URL identity, cache layout and quieter startup

Reported log: an AnkiConnect version probe produced retry warnings and a full
traceback before Anki finished starting. The restored Jimaku file lacked a
persisted release choice, so automatic selection asked for a version. The old
prefetch path skipped an empty saved file list instead of loading it.

Changes:

- Desktop and extension prefer the Miruro /watch/<opaque-id>/<title-slug> title;
  the query parameter ep remains the episode number. Extension series identity
  uses the opaque ID, independent of a renamed slug. Other providers retain their
  existing metadata adapters. Desktop fallback searches also strip browser chrome.
- Legacy Jimaku files adopt a release signature from the existing selected file
  without a startup network request. Prefetch loads missing file metadata in its
  worker, prepares the centre, then caches +/- the configured radius (default 2).
  Ambiguous release/numbering still requires selection.
- New cache files live under cache_jimaku/<safe anime name>/Season01/..., including
  explicit episode prefixes and short version suffixes. Unknown seasons use
  Season00; ZIP containers live in Archives and members use their own season.
  Filename limits preserve extensions and account for Windows path length.
- Old opaque cache files are copied on reuse when the same URL/modification
  fingerprint exists. Old references/files remain valid; no recursive deletion
  or user-cache migration was executed by the agent. Subtitle bytes stay unchanged.
  ZIP original member names are retained separately for release/episode matching.
- Jimaku picker centres on the Choose Source parent's monitor, including negative
  monitor coordinates, and shrinks to fit a smaller display.
- Repeated Bound announcements no longer produce repeated INFO identity dumps.
  Source comparisons tolerate missing fields in legacy associations. Expected
  version-probe failures are short, non-retrying DEBUG diagnostics with a bounded
  timeout; actual Anki write failures still warn and retain DEBUG tracebacks.
  Messages emitted directly by Anki/add-ons are not modified.

Validation: static AST/compile of eight Python files, JS syntax, manifest JSON and
whitespace checks. No tests, live app/browser/Anki operations, API searches/downloads,
personal-data edits or uploads were performed. Runtime behavior remains unverified.

User checks after app restart, extension reload to 0.3.8 and one Miruro refresh:
URL with ep=3 identifies the correct series/episode; files 1..5 become cached for
the chosen release; popup opens on the chooser's monitor; restart with Anki still
starting produces no expected-connectivity traceback. Confirm existing cards,
timing maps and original SRT content remain unchanged.
