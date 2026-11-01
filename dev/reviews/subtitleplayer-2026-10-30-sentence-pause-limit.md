# Automatic sentence grouping: pause boundary

Automatic grouping previously permitted gaps up to three seconds. The default
is now 1.5 seconds from the previous subtitle's end to the following subtitle's
start. This is a conservative starting heuristic, not a measured linguistic
boundary or audio-silence detector. Punctuation cannot override a longer gap.

A long gap ends backward expansion into older dialogue. The remaining adjacent
segments may still form the current sentence, rather than discarding that valid
context because unrelated earlier dialogue lacked punctuation. Existing limits
remain: at most three whole subtitle segments, at most 30 seconds total, and
ending punctuation in the selected cue. Whole cues are not split internally.

The setting `CAPTURE_SENTENCE_MAX_GAP_SEC` is exposed under Advanced Settings >
Anki > Browser Recording as “Auto sentence: maximum subtitle gap (s).” Valid
values range from 0 to 10 seconds; missing, invalid, negative, or nonfinite
values fall back to 1.5 seconds. Zero permits overlapping or touching cues only.
Explicit recording ranges selected with the multi-segment keys retain the
requested range, including deliberate crossings of longer pauses.

All 68 capture-range tests pass, including threshold edges, gap-delimited
dialogue blocks, configurable values, invalid values, frozen recording ranges,
and the settings specification. Compile and scoped whitespace checks pass.

An additional run including the older mining-settings suite had 75 passes and
28 failures. Those failures come from its borrowed browser fixture omitting
the existing `_manual_offset` field, before it reaches pause grouping. That
fixture and the unrelated playback-policy/offset code were not changed here.
No live recordings or Anki notes were created during verification.

Restart the desktop app to activate the new default.
