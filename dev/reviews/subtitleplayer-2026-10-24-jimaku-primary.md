# Jimaku as primary subtitle source

Jimaku now owns a persistent local catalogue and series library. GitHub remains an
explicit backup; the automatic normal path does not run GitHub searches or refresh
its maps. Existing loaded local/GitHub files remain usable. Startup is still disk-only.

## Data and behavior

- Shared Jimaku client stores separate anime/drama title catalogues under ignored
  cache_jimaku. Initial retrieval runs in the search worker; after 24 hours a stale
  catalogue stays usable during background refresh. Failures use a cooldown.
- Local search normalizes Unicode/punctuation/case, searches romaji/English/Japanese
  aliases and ranks exact titles, substrings and token prefixes. Typing is debounced
  250 ms and never issues an API call for each keypress. Explicit searches retain
  recent query/category history. Automatic selection requires a known binding or
  one exact title/normalized alias; fuzzy suggestions are never auto-selected.
- jimaku_library.json stores IDs, metadata, file lists, preferred releases, source
  paths and browser bindings. The file is ignored. File/version information is
  separate from unchanged SRT bytes and existing timing sidecars.
- source_kind (local/jimaku/github) and source_descriptor extend remembered source
  state. Legacy Jimaku directory/entry records are recognized without network I/O;
  an unknown release preference is requested when another file is needed. Recognized
  custom names and existing progress remain. Source restore also rolls back kind,
  descriptor, local index and prepared display state on failure.
- First file selection records provider/release tags, language and format. Future
  episodes prefer that release; absence or ambiguity prompts selection. Confirmed
  browser/source episode-number differences are stored explicitly per association;
  unrecognized numbering cannot silently select the first file.
- App episode buttons/dropdown use complete cached Jimaku file lists and async
  navigation, not just downloaded local siblings. Browser following reuses a series
  ID and resolves the next episode from its files. Page URL/request/source guards
  discard old results. Recordings block automatic source application until finished.
- Current file downloads first; +/-2 episodes are queued serially in the background.
  Downloads share per-file locks and reuse URL/modification-version caches. ZIPs
  download once, index member filenames and apply the same release selection.
  Old pending prefetch windows are superseded; prefetch never changes the active
  manager or playback position. Missing neighbours do not initiate catalogue searches.
- Missing or failed files refresh their file list once before prompting. Jimaku's
  picker includes GitHub backup, which requires an explicit click. A previously
  explicitly chosen GitHub backup may follow its saved map without a code-search
  refresh; failures do not start an automatic GitHub query.
- DOWNLOAD_WINDOW is migrated once to 2 for the active profile, with a persisted
  migration marker. Subsequent user changes are respected. Prepared display payloads
  for asynchronous episode loads are built in workers without eager generated ruby.
- Offset width is measured for '-13.95 s', borders and the unchanged refresh button,
  with a small buffer. It no longer gets double stretch weight; display precision
  remains <=2 decimals and timing calculation retains full precision.

## Validation and user acceptance

Only static AST/compile and whitespace checks were run. No project imports,
automated tests, live app/browser/Anki actions, API searches/downloads, personal
settings edits or uploads were performed during implementation. Runtime and UI
behavior remain unverified.

After restarting the app (extension unchanged, latest version remains 0.3.7):

1. Search full English title, 'The Laid-Off Cheat' and 'Cheat Fuyo'; confirm the same
   Jimaku entry appears. Verify Anime/Drama and recent searches separately.
2. Choose the desired file once. Advance episodes and change anime inside one Miruro
   tab, then across tabs. Confirm correct numbers/title and no browser seek.
3. Confirm current episode and +/-2 neighbours download; return to them and restart
   to verify cache, release preference, mapping and remembered progress.
4. Verify ZIP members and multiple releases/languages; ambiguous choices must prompt.
5. Verify missing file, disabled/invalid token, offline API and missing neighbours
   preserve existing content. GitHub is used only after explicitly selecting backup.
6. Change source quickly and during recording, and cancel pickers: old work must not
   replace the new source or interrupt capture. Verify compact offset at Windows DPI.

No end-to-end success or timing/latency targets are claimed before these user checks.
