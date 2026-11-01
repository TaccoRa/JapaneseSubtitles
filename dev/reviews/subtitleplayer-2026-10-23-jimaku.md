# Jimaku search and GitHub failure recovery

Implemented from Jimaku's official API documentation and OpenAPI schema:
https://jimaku.cc/api/docs and https://jimaku.cc/api/openapi.json

- Source picker has an additional Jimaku Search entry, leaving GitHub/local
  choices intact. Anime/Drama toggles send the documented anime=true/false flag.
- Explicit title search returns romaji/English/Japanese title suggestions;
  selecting a title loads its files. Filename filtering is local. File refresh
  bypasses the three-minute in-memory API cache. Recent successful searches
  retain query and category in local jimaku_recent_searches.json (25 entries).
- Search, file listing and downloads use background workers and a Tk-polled
  result queue. Request generations discard stale UI results. Errors stay inline.
- JIMAKU_TOKEN is read from the process environment and sent as the raw
  Authorization header only to Jimaku API requests; never written to settings,
  history, logs or file download headers. No authenticated API calls were made
  during implementation; only public API documentation was retrieved.
- Supports original SRT/ASS/SSA downloads and ZIP file selection. Uses a separate
  ignored cache_jimaku directory, versioned by URL/modification metadata. ZIP
  members are saved by sanitized basename with decompression limits; no archive
  paths are executed or followed. Files retain their original bytes.
- Selection uses the existing local subtitle loading path. Local source metadata
  keeps Jimaku's title/movie flag over restart. Only downloaded siblings are
  available to ordinary local episode navigation; this is not an automatic
  Jimaku episode-download backend or a replacement for saved GitHub searches.
- GitHub rate limits stop retries and set a shared in-process cooldown rather
  than sleeping/retrying through the UI workflow. A failed/limited refresh is
  reported as failure and retains the previous in-memory map instead of being
  reported as a successful merge. The 1000-result cap stops the broad search;
  this does not remove GitHub's cap or guarantee a missing upstream file exists.
- Empty source initialization and safe duration calculation prevent the reported
  missing-subtitles AttributeError after a failed/cancelled source load.

Validation: static AST/compile of changed Python sources and diff whitespace
checks only. No automated tests, live app/browser/Anki actions, personal settings
edits, runtime downloads or Git uploads performed.

User acceptance after restarting the app (restart its launching terminal too if
it does not inherit the new environment variable):

1. Open SRT -> Jimaku Search; Anime and Drama each return the intended category.
2. Select a title, filter its files, select a subtitle. Verify title and text.
3. Open a ZIP, choose a member, then reopen search and select a recent query.
4. Cancel/close while a search/download runs; stale results must not change source.
5. Verify missing/invalid token and rate-limit errors are inline and retryable.
6. Verify startup after a missing source/cancel remains usable for another source.

Extension unchanged; no extension reload needed. All runtime behavior remains
unverified until user acceptance.
