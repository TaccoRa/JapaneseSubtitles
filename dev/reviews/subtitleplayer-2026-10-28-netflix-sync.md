# Netflix episode selection and caption timing

The open Netflix video was Assassination Classroom S01E02. Selecting the correct
Japanese SRT still produced `no corroborated subtitle-onset region`. No full
caption reference appeared in the application log. The page exposed no native
`TextTrack` or `<track>` elements. Its paused overlay exposed the series title,
`Season 1` and `Baseball Time: Ep. 2`, which the old metadata parser ignored.

Extension version 0.3.10 adds:

- Netflix metadata from the current playback session, plus the paused DOM overlay
  as a fallback. Episode messages use a stable series key and a clean series name
  so existing Jimaku aliases and confirmed series maps can be reused.
- Timing from the selected English or German text track's loaded caption URL in
  that same session. The adapter does not select a different track or seek/play.
- TTML tick/frame rates and duration-only cues. Only timing and display metadata
  leave the MAIN world; subtitle text and caption URLs stay there.
- Bounded player-state traversal, one request per selected track until an explicit
  refresh, and rejection of late responses from another episode/track/generation.

Validation: all 89 browser-extension tests pass, including recording regressions.
JavaScript syntax checks pass. An isolated copy of the user's saved Jimaku state
resolved `Assassination Classroom` to entry 490 and episode key `490:1:2` with
network search disabled. Existing content tests were updated to locate playback
snapshots by message type rather than assuming they precede input-focus messages.

Live activation remains pending: browser policy rejected navigation to
`chrome://extensions/`. The user must reload SubtitlePlayer Browser Connection,
reload Netflix, and click the circular retry arrow next to Offset. That retry
also replaces a previously saved manual offset when the automatic fit succeeds.
No live Netflix offset has been claimed as verified.

Reference for Netflix's current session metadata and timedtext delivery:
https://github.com/asbplayer/asbplayer/blob/main/extension/src/entrypoints/netflix-page.ts
TTML tick-format reference:
https://github.com/asbplayer/asbplayer/pull/1060/files
