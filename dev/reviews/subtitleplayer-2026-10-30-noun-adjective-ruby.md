# Noun/adjective ruby boundaries

The reported Death Note S01E01 line is cue 313 in the active Jimaku SRT:
`（リューク）そんなことしたら` followed by `性格悪いの お前だけになるぞ`.
The source has no space inside `お前`; that displayed space came from the older
spacing alignment, corrected during the corpus audit. Restarting the desktop
app rebuilds the active cue with source spacing preserved.

The word tokenizer correctly returns `性格` and `悪い`, but the ruby formatter
splits okurigana from the adjective and then merged its kanji stem into the
preceding noun: `性格悪[せいかくわる]い`. Default-mode ruby merging now respects
lexical starts of mixed kanji/kana words reported by the bundled MeCab. The result
is `性格[せいかく]悪[わる]い`, preserving the original text. The rule also applies
to inflected adjectives and cases such as `攻撃力高い`; adjacent kanji names and
nominal compounds retain their existing merging behavior.

Manual selection of the complete phrase `性格悪い` still retains that complete
phrase for lookup and card preparation. This change does not turn every noun
plus adjective into a single lexical word.

Focused tests passed for the supplied line, adjective inflection, a second
noun/adjective example, whole-phrase selection, existing ruby rules, canvas
annotations, and the earlier compound-selection regressions. Compile and scoped
whitespace checks also passed. No live Anki cards were created.
