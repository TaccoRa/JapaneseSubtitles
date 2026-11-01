# Whole-word lookup, cards, and database annotations

Reported sentence: `ああ 全国模試の結果ね`.

The Jisho API request for `全国模試` reproduced the reported wrong meaning: its
first suggestion was `全国` with the definition “the whole country.” The popup
passed the complete manual selection to Anki, but dictionary extraction accepted
the first suggestion even when none matched the requested expression.

Dictionary extraction and hover formatting now share complete-entry matching.
Word/reading matches accept normalized width and whitespace. A `する` lookup can
use its noun stem only when that entry explicitly supports a suru verb. Partial
and unrelated suggestions no longer provide card meanings or definitions. The
existing translation fallback receives the entire expression. A live read-only
check returned `Nationale Probeprüfung` for `全国模試` after the fix, with no false
dictionary definition.

Hover now chooses the largest detected nominal compound containing the ruby
anchor. Ruby display remains independent. Compound hover uses the same complete
expression translation fallback when there is no whole-entry dictionary match.
Explicit popup selections retain their complete text and immutable cue spans
through card preparation. A no-snapshot fallback no longer drops additional
content words from phrases beginning with a verb.

Annotation matching suppresses component intervals inside a larger lexical word,
including individual ruby pieces. The whole word can still match its own database
entry or grammatical base form. Standalone occurrences retain their own status.
The diagnostic status view continues to report components separately, but selects
an exact compound token for its direct status and reading; overlapping compound
and component tokens no longer duplicate its reading.

The focused regressions cover the supplied sentence, word and phrase popup
selection through actual card fields, frozen selection spans, dictionary search
suggestions and full-text translation, retained verb lemmatization, canvas/popup
compound hover, component-only databases, exact whole-word entries, split ruby,
standalone component occurrences, and direct database status with readings.

All 190 tests pass across the full-word, annotation-provider, Anki-client, ruby,
capture-range, and popup-pause suites. Python compilation and scoped whitespace
checks pass. A broader run also included the older Anki-controller suite: three
tests passed and two native-recorder cases failed because their fixtures expect
direct worker dispatch where the existing controller now starts a mining group.
The Anki-controller implementation was not changed for this task.

Two prior annotation expectations were updated to the requested behavior: a
known substring in `重要器官` and known `前` in `お前` no longer mark those larger
words as collected.

Activation requires restarting the desktop app. No Anki notes were created or
changed during verification.

Observed API endpoint:
https://jisho.org/api/v1/search/words?keyword=%E5%85%A8%E5%9B%BD%E6%A8%A1%E8%A9%A6
