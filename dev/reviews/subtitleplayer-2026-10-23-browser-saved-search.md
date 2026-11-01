# Automatic saved-search selection

Browser MediaContext/EpisodeContext now initiates a background saved-search lookup.
Names, display names and saved aliases are matched, preferring the longest match;
equal matches remain unresolved. Only existing saved search maps are used: no
GitHub search refresh or new series discovery is performed.

Downloads use a draft manager/configuration. The UI applies the prepared source
only while the request, bound video and original subtitle selection still match.
Explicit season/episode requests cannot fall back to another global episode or
the first file. Season-relative numbers without a season require either the
already selected matching series' season or one unique candidate season.
Existing remembered page associations remain available, including local movies.
Source application uses the existing connected-mode refresh, which follows the
video clock without seeking or restoring the desktop's remembered position.

Validation: static Python AST/compile and whitespace checks only; no tests,
browser actions, Anki operations or application restarts performed.

Manual checks after restarting SubtitlePlayer (extension reload not required):

- Switch to a saved anime's video tab; verify saved title and exact episode.
- Advance the episode on the site; verify the app follows without moving video.
- Switch between two saved anime tabs, including fast successive switches.
- Verify an unknown/ambiguous title or absent episode keeps the previous source
  and exposes the reason in existing episode diagnostics.
- Verify manually choosing a source while a download is pending wins over it.

Runtime behavior remains unverified until these user checks are complete.
