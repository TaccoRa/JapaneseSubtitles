# Complete dictionary expression: 性格悪い

The user clarified that the reported problem was dictionary word selection.
The preceding ruby-boundary fix addressed an additional display problem, but
left hover targets as the short morphological units 性格 and 悪い.

A live Jisho API query verified a complete JMdict entry for 性格悪い, reading
せいかくわるい, with glosses including nasty, mean, heartless, inconsiderate,
and selfish. The shared tokenizer now recognizes this verified lexical
adjective expression as a complete hover/card/database target. Its existing
nominal-compound logic remains separate from this expression rule.

The expression retains the displayed surface text and offsets, including past
or negative adjective endings, while dictionary lookup uses 性格悪い. A full
selection also keeps that canonical expression for card preparation. The
surface readings and ruby word boundaries remain available independently.

Knowing only 性格 and 悪い does not mark the whole expression as collected.
An exact expression entry does. No new lookup is performed synchronously by
the tokenizer; the verified expression is included in its local lexical rules.
This does not claim complete coverage of every JMdict expression.

Tests cover hovering both parts, full selection, source spacing, inflected
forms, whole-expression status, and prepared Anki front/meaning fields. Live
Anki writes are forbidden by the preparation test.

Observed dictionary source:
https://jisho.org/api/v1/search/words?keyword=%E6%80%A7%E6%A0%BC%E6%82%AA%E3%81%84

Restart the desktop app to rebuild cached word regions.
