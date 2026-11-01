# Confirmed Jimaku series maps and AniList accounts

## Implemented locally

- A persistent `jimaku_series.json` groups multiple Jimaku entries into one named
  anime. Public AniList prequel/sequel metadata supplies an editable TV proposal.
  The confirmation window supports adding/removing/reordering parts and editing
  app seasons, source episode ranges and confirmed season ends. Movies/specials
  remain separate. Cancelling preserves the active source/release selection.
- Stable part/episode keys drive dropdowns, app +/- navigation and neighbor
  prefetch. Explicit absolute numbers remain separate from playback order. Missing
  episodes stop navigation; unknown season ends do not infer the next season.
- The existing download window (default +/-2) follows confirmed parts across
  season boundaries. Neighbor display payloads are prepared too. ZIP indexes are
  verified when loaded; newly revealed numbering can require confirmation.
- New files use the shared anime title and app-season directory. Version-matching
  cached files and old paths are reused; no forced moves or SRT retiming.
- File lists refresh after 24 hours in background. Unconfirmed new parts are
  offered through Choose Source > Jimaku: Map aktualisieren. Confirmed order,
  exclusions and known boundaries remain; unfinished ranges can gain new files.
- Map revisions and existing source/video guards discard stale preparation.
  Subtitle-only navigation does not rebind the physical video or AniList target.
- Existing resume positions have an exact-file fallback after label changes;
  unambiguous offline trim preferences can be inherited under the new names.

## AniList

- Advanced > General > AniList exposes connect/disconnect, setup and automatic
  progress. Choose Source > Meine AniList-Liste opens Watching by default and
  matches selected titles to Jimaku through their AniList ID where available.
- OAuth uses a public client ID, browser implicit flow, one-use random state and
  `http://127.0.0.1:8768/anilist/callback`. The callback removes its fragment and
  accepts token POSTs only for its loopback origin/session. No client secret is
  embedded. Tokens are stored in Windows Credential Manager, never profiles/logs.
- Account-owned pending progress is persisted in `anilist_progress.json`. Only
  verified series/episode bindings and normal bound-video observations qualify.
  Crossing 90% in normal playback counts; starting/seeking above 90% requires ten
  continuous seconds. Pauses, buffering, seeks and capture reset continuity.
- Remote progress is read before writing, never decreased, and repeated playback
  does not add another increment. Writes own only progress and Watching/Completed.
  Completed is not downgraded; other fields are omitted from the mutation.
- Disconnect/account changes invalidate in-flight setup results and prevent old
  pending rows being sent with a different account. API errors remain in settings;
  no player commands or warning windows are issued by account synchronization.

## Not performed / rollout prerequisites

Only static source inspection, AST parsing and in-memory compilation were run.
No automated tests, app restart, browser/Anki live actions, account mutations,
OAuth registration, login or GitHub uploads were performed. Runtime behavior and
API interoperability remain unverified.

The public OAuth client ID is not provisioned. First use opens a guided setup:
create an AniList application named SubtitlePlayer with the exact callback above,
paste its **Client ID** (not Client Secret), then complete browser login/consent.
The gear reopens this setup. This external one-time registration is still required.

Restart SubtitlePlayer. No extension update or new Python dependency is needed.
New personal map/metadata/progress JSON files and temporary files are Git-ignored.

## User acceptance

1. Confirm a multi-season TV map once; restart and revisit it. Check canonical
   names, repeated E01 values, source ranges and explicit One Piece numbers.
2. Use +/- at a confirmed season boundary and verify +/-2 files/prepared neighbors.
   Missing files must stop at the gap and offer the correct Jimaku entry.
3. Load ZIP sources, including absolute-only numbering. Unknown membership is
   not shown as verified; confirm newly discovered ranges when requested.
4. Switch episodes/tabs quickly and refresh maps. Old results must not replace
   the current source. Browsing subtitles must not navigate the website.
5. Check original SRT hashes, existing manual trim, lookup, capture and Anki flows.
6. Complete AniList setup/login with your chosen account. Check Watching/list
   selection, 90%, seek-only exclusion, ten-second continuation and capture replay.
7. Verify the correct AniList season/absolute progress, higher remote progress,
   Completed preservation, API failure recovery, disconnect and account switching.
