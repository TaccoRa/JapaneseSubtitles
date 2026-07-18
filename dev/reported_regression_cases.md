# Reported Regression Cases

This is the persistent ledger for concrete cases reported by the user as wrong.
Append new cases when they are reported. Preserve the exact Japanese text, line
breaks, spaces, and punctuation whenever possible.

## Recording Rules

- Record the observed wrong behavior and the expected behavior separately.
- Record whether the expectation was explicit or inferred from the discussion.
- Use the same case for the subtitle window and popup unless their expected
  behavior intentionally differs.
- A generated compound lookup candidate must not silently replace a more precise
  parsed ruby/token span under the pointer.
- Do not delete old cases after a fix. They are regression examples.
- Superseded UI layout experiments are not authoritative. Record only the latest
  clear behavior when requests conflict.
- Current implementation status is informational; every case remains a regression
  guard even after it is fixed.

Last backfill: 2026-07-16

## Annotation And Matching

### ANN-001 - Partial known word inside a ruby compound

**Input**

```text
心臓と 重要器官の 位置を
ずらしたのさ
```

**Was wrong:** Marking known `重要` caused the ruby for all of `重要器官` to
disappear.

**Should be:** Only the matching `重要` text may receive its annotation color,
but the complete ruby for `重要器官` must remain available and correctly aligned.

### ANN-002 - Passive verb form

**Input:** Subtitle `殺される`; database/Anki entry `殺す` / `殺[ころ]す`.

**Was wrong:** The inflected subtitle form was not recognized.

**Should be:** `殺される` must match the base verb `殺す` in both subtitle and
popup annotation paths.

### ANN-003 - Renyou form in a verb construction

**Input**

```text
今その バケモノを
切り 離してやるからね
```

**Was wrong:** `切り` was marked in the popup but not in the subtitle window.

**Should be:** In this verb-construction context, known `切る` may match `切り`,
and the result must be identical in both windows. A noun use of `切り` must remain
blocked unless it has its own exact entry.

### ANN-004 - Same token in different punctuation/context

**Inputs**

```text
“お 前も”？
殺してやるお 前も
```

**Was wrong:** `前` was colored in the second subtitle and in the popup, but not
in the first subtitle.

**Should be:** Quotes and surrounding punctuation must not change the match. The
same known token must receive the same annotation in popup and subtitle.

### ANN-005 - Derived verb noun must not over-match

**Input:** Known `動く`; subtitle token `動き` used as a noun.

**Was wrong:** `動き` was colored as known from `動く`.

**Should be:** A noun-token `動き` must not match `動く` by default. An exact
database entry for `動き` still matches. The optional derived-verb-noun setting
may explicitly restore broader behavior.

### ANN-006 - Kana adjective/adverb over-match

**Input:** Subtitle `うまく`; related database entry `旨い` or `うまい`.

**Was wrong:** `うまく` could be marked through broad adjective inference even
when it was not an intended direct match.

**Should be:** Match only an exact entry or a tokenizer-supported, explicitly safe
inflection. Do not infer a known sense merely from related spelling/translation.

### ANN-007 - Newly added words must refresh immediately

**Input**

```text
その 少年を 殺さず
監視 観察してほしい
```

**Was wrong:** After adding `少年`, `殺さず`, `監視`, and `観察`, only `観察`
was immediately annotated.

**Should be:** Every successfully added/synced word must refresh the word database,
annotation index, subtitle renderer, and popup so all applicable words are marked
without restarting or manually syncing.

### ANN-008 - Exact known pronoun

**Input:** `我々` exists in the word database.

**Was wrong:** `我々` was not marked.

**Should be:** The exact entry must be annotated in both windows.

### ANN-009 - Exact known words in a sentence

**Input**

```text
いや
全てが 一瞬のことだったからな
```

**Was wrong:** Known entries `全て` and `一瞬` were not marked.

**Should be:** Both exact entries must be annotated; particles and line breaks must
not prevent the match.

### ANN-010 - Non-kanji exact entries

**Input:** Known `だから`.

**Was wrong:** Kana-only text was not annotated.

**Should be:** Exact kana-only entries are valid annotation targets. Conservative
lemma rules must not disable exact kana matches.

### ANN-011 - Newly added multi-token expression versus mature component

**Input**

```text
たった１人 同じ 思いを
分かり 合える 人だったのに
```

**Was wrong:** The popup colored only `分かり` as mature/green while the subtitle
colored the newly added `分かり 合える` as new/red; `合える` was also omitted in
some runs.

**Should be:** An exact newly added phrase entry must win for the complete phrase
in both windows. Do not replace it with a partial mature component match.

### ANN-012 - Related component must not create a false compound match

**Input:** `今日はこれで 引き 揚げよう`.

**Was wrong:** The popup marked the expression while the subtitle did not, although
the full verb was not in the database.

**Should be:** Both paths must make the same decision. Without an exact phrase or a
safe tokenizer lemma matching a database entry, leave it unmarked.

### ANN-013 - Contracted verb form

**Input:** Subtitle `持ってるな`; database entry `持つ`.

**Was wrong:** The popup recognized it but the subtitle did not.

**Should be:** The contracted inflection must match `持つ` consistently in both
paths, while the final particle `な` is not included in the annotation span.

### ANN-014 - Purpose construction

**Input:** `だとしたら 俺を 殺しに？`; database entry `殺す`.

**Was wrong:** `殺しに` was not marked.

**Should be:** The verb-purpose construction must resolve to `殺す`; unrelated noun
uses of a renyou form remain conservative.

### ANN-015 - Potential past form with orthographic lemma

**Input:** Subtitle `登れた`; database entry `登る`.

**Was wrong:** UniDic returned canonical lemma `上る` and orthographic potential
base `登れる`, so the mature `登る` entry was missed and a duplicate `上る` card
could be created.

**Should be:** Resolve `登れた -> 登れる -> 登る`, annotate exactly `登れた`, and
use `登る` as the future Anki headword. Do not absorb following `かも`.

**Status:** Fixed and directly reproduced on 2026-07-15. Existing duplicate Anki
notes are not automatically deleted.

### ANN-016 - Conservative nominalization guards

**Inputs and expected results**

- Known `休む`, noun `休み` -> no fallback match.
- Known `切る`, noun `切り` -> no fallback match.
- Known `切る`, verb-context `切り` -> match.
- Known `高い`, noun `高さ` -> no fallback match.
- Known `高い`, adjective form `高く` -> match.
- Known `動く`, verb form `動いた` -> match.
- Known `食べる`, inflected `食べました` -> match.

### ANN-017 - Reading conflict safety

**Input:** Database `人気[にんき]`; subtitle token read as `人気[ひとけ]`.

**Was wrong risk:** Identical kanji could be treated as the same known word despite
a conflicting available reading.

**Should be:** If both readings are available and conflict, do not match. Missing
reading data alone must not block an otherwise safe match.

### ANN-018 - Unexpected ignored status

**Input:** `普通`.

**Was wrong:** It appeared in the `ignored` category unexpectedly.

**Should be:** It is ignored only if an explicit ignored database entry/config rule
exists. Otherwise its actual Anki/local status must be shown.

**Status:** Expected behavior is clear; root cause still needs verification if it
reappears.

### ANN-019 - Honorific name recognition

**Input pattern:** Japanese names followed by `さん`, `くん`, `君`, `ちゃん`,
`様`, and configured honorifics.

**Was wrong:** Names with honorifics were not consistently recognized as one name
annotation.

**Should be:** Recognize the name plus honorific as a name span without falsely
adding ordinary nouns as known vocabulary.

## Ruby, Segmentation, And Anki Formatting

### RUBY-001 - Counter reading `１匹`

**Input:** `１匹`.

**Was wrong:** Hover did not reliably show the counter reading.

**Should be:** Hover ruby is `いっぴき`, associated with the complete number-counter
span.

### RUBY-002 - Counter reading `２つ`

**Input:** `２つ`.

**Was wrong:** No ruby was shown.

**Should be:** Ruby is `ふたつ` for the complete token.

### RUBY-003 - Counter spacing in Anki

**Input:** `たった１人`.

**Was wrong:** Generated Anki text `たった１人[ひとり]` could make the ruby appear
to extend over preceding text.

**Should be:** Generate `たった １人[ひとり]` when sentence-spacing mode is active,
so the ruby belongs only to `１人`.

### RUBY-004 - Number plus unit `１秒`

**Input:** `１秒` or source-spaced `１ 秒`.

**Was uncertain/wrong:** The number/unit reading and ruby ownership were unclear.

**Should be:** Preserve the source spacing. For an unspaced counter token, the full
reading is `いちびょう` and belongs to `１秒`; do not attach another word's ruby.

**Expectation source:** Linguistic inference; confirm if a different display is
desired for source-spaced `１ 秒`.

### RUBY-005 - Katakana ruby

**Input**

```text
そうか ものすごいスピードで
変形しながら動いていたのか
```

**Was wrong:** `スピード` did not show its configured katakana ruby.

**Should be:** When katakana ruby generation is enabled, show
`スピード[すぴーど]`; otherwise preserve the plain katakana without disturbing
neighboring ruby spans.

### RUBY-006 - Missing boundary before `マッシュ`

**Input:** `ここはマッシュや フィンが 所属するアドラ 寮`.

**Was wrong:** Generated Anki output had no expected boundary before
`マッシュ[まっしゅ]`, causing words/rubies to run together.

**Should be:** Sentence-spacing mode must create consistent readable boundaries
without changing the underlying subtitle text used by the renderer.

### RUBY-007 - Ruby leaking into preceding text

**Input:** `そうなの びっくりでしょ 続いて 交通 情報です`.

**Was wrong:** Ruby for `続` appeared over preceding text in Anki.

**Should be:** `続[つづ]いて`; ruby must cover only its parsed base.

### RUBY-008 - Katakana/name and neighboring particle alignment

**Input:** `ミギーの 細胞が 全身に 散らばってから`.

**Was wrong:** `ミ[み]ギー[ぎー]` was split unexpectedly, and `全身[ぜんしん]`
ruby could extend over preceding `が`.

**Should be:** Prefer the coherent token `ミギー[みぎー]` when katakana ruby is
enabled, and place `全身[ぜんしん]` only above `全身`.

### RUBY-009 - Name and following verb alignment

**Input:** `怪我をしていた 眞白ちゃんを 手当したら 送って 行ってって 言われて…`.

**Was wrong:** `眞[ま] 白[しろ]` was split with an artificial space, and ruby for
`送` could appear over preceding `したら`.

**Should be:** Preserve the source boundary and prefer `眞白[ましろ]`; ruby for
`送[おく]って` covers only `送`/its intended base.

### RUBY-010 - Source spacing around compounds

**Input:** `恋愛ゲームみたいに 相手の 気持ちがわかりますように……！`

**Was wrong:** Generated output inserted a space as
`恋愛[れんあい] ゲーム[げーむ]` although the source had no space, and `相手` ruby
alignment was reported wrong.

**Should be:** Preserve the source as `恋愛[れんあい]ゲーム...` and anchor
`相手[あいて]` only to `相手`.

### RUBY-011 - Artificial split inside `無価値`

**Input:** `己の 無価値さを 自覚し 生きていくなど`.

**Was wrong:** Generated output became `無[む] 価値[かち]`.

**Should be:** Do not insert a source-nonexistent space. Prefer a coherent compound
ruby such as `無価値[むかち]` unless split-kanji mode explicitly requests otherwise.

### RUBY-012 - Unicode-space normalization

**Input:** `今その バケモノを 切り 離してやるからね`.

**Was wrong:** One visually space-like character caused ruby to span `を` and `切`;
replacing it with an ordinary space changed the result.

**Should be:** Normalize supported Unicode spaces consistently before measuring and
formatting while preserving logical word boundaries. `切[き]り` must not include
`を`.

### RUBY-013 - `突き刺さる` dictionary form and ruby

**Input:** `だからてめえの 触手が 突き 刺る 寸前に` with intended word
`突き刺さる（つきささる）`.

**Was wrong:** Generated `突[つ]き 刺[さし]る` and the wrong dictionary form.

**Should be:** Resolve the word as `突き刺さる[つきささる]`; if split, the ruby
must correspond to the actual bases (for example `突[つ]き刺[さ]さる`), never
`刺[さし]る`.

### RUBY-014 - Contextual verb with partial kanji ruby

**Input:** `てめえの仲間 装甲車から引きずり出して`.

**Was wrong:** Subtitle ruby correctly covered only the kanji portion (`引[ひ]...`),
while popup manual hover used whole-token reading `ひきずり`.

**Should be:** Popup display ruby comes from the exact shared subtitle segments,
so it remains `ひ` for the kanji base. Whole-token reading may be lookup metadata
only.

### RUBY-015 - Ordinal prefix plus noun

**Input:** `第１関門 クリア！`.

**Was wrong:** Overlapping synthetic spans treated `第１関門` as the hover word;
the popup retained either `だい` or `かんもん` depending on which kanji was entered
first, and subtitle dictionary hover over `関門` queried the `第`/whole span. The
first correction still split the ordinal into `第[だい]` plus an unread `１`.

**Should be:** Parsed components are `第１[だいいち]` and `関門[かんもん]`.
Hovering either `第` or `１` resolves the combined ordinal `第１`; hovering `関門`
resolves `関門`. The full expression may remain a phrase-search candidate but must
not override the precise ruby span under the pointer.

**Status:** Fixed on 2026-07-16. The shared popup/subtitle ruby source and Anki
bracket output were runtime-checked as `第１[だいいち] 関門[かんもん]`.

### RUBY-016 - Popup manual selection widened ruby to the whole word

**Input:** Manually select or mark a mixed kanji/kana word in the popup.

**Was wrong:** The popup concatenated all readings overlapping the selection and
centered that aggregate ruby above the selection's complete bounding box.

**Should be:** Selection controls the dictionary, translation, status, copy, and
Anki lookup target only. Ruby remains anchored to the original parsed subtitle
segments, so it appears only over kanji, katakana, or recognized number/counter
units such as `１匹[いっぴき]`.

**Status:** Fixed on 2026-07-16 by separating popup lookup-selection state from
the active parsed ruby segment.

## Dictionary, Translation, And Hover Parity

### HOVER-001 - Dictionary always used first word

**Input:** `１ 秒でも 早くお 前を 殺す`.

**Was wrong:** Hovering multiple words repeatedly showed the dictionary entry for
the first word/`秒`.

**Should be:** Resolve and query the exact contextual token under the pointer.

### HOVER-002 - Surface noun versus contextual verb dictionary

**Input:** `てめえの仲間 装甲車から引きずり出して`; selected `引きずり`.

**Was wrong:** Popup queried isolated noun `引きずり` and returned “train of dress,
trailing skirt...”; subtitle queried the contextual verb and returned “to drag,
to trail...”.

**Should be:** Manual selection keeps surface text for display/translation but uses
the containing sentence token's lemma `引き摺る` for dictionary lookup. Both
windows show the contextual verb sense.

**Status:** Fixed and directly checked on 2026-07-15.

### HOVER-003 - Missing subtitle translation result

**Input:** `指令`.

**Was wrong:** Popup translated it correctly while subtitle hover displayed
`指令 -` or an empty translation.

**Should be:** Both use the same database-first translation and provider fallback;
do not repeat the source text when the translated remainder is empty.

### HOVER-004 - Multi-selection translation

**Input:** Multiple words selected with Ctrl-click in the subtitle window.

**Was wrong:** Translation continued to use only the currently hovered word.

**Should be:** Once multiple words are selected, hovering any selected word uses
the complete ordered selected phrase for translation, even after Ctrl is released.

### HOVER-005 - Manual popup selection owns hover content

**Was wrong:** Automatic word hover could remain active while the user dragged a
manual text selection, and lookup/ruby could switch to tokenizer readings.

**Should be:** During drag, automatic highlight is suppressed. After selection,
dictionary/translation target the selected text, contextual lemma is retained for
a single exact token, and visible ruby remains identical to subtitle segments.

### HOVER-006 - Hold hotkeys must not repeat or flash

**Was wrong:** Holding translation/dictionary/status hotkeys repeatedly fired the
action, causing flashing, duplicate translations, delays, or crashes.

**Should be:** Activate once when the exact configured chord becomes held, remain
visible while held, update only when the target word changes, and clear once the
chord is released. Chord order and modifier behavior follow the current shortcut
configuration.

### HOVER-007 - Hover tooltips must be enterable and copyable

**Was wrong:** Moving from a word into its tooltip hid it before text could be
selected; subtitle hover text was not copyable.

**Should be:** Pointer presence in either source word or tooltip keeps it alive for
the configured delay. Text selection, Ctrl+C, and context-menu copy work in popup
and subtitle hover overlays.

### HOVER-008 - Popup and subtitle share the same source data

**Was wrong pattern:** Ruby, annotation status, dictionary query, or translation
could differ between popup and subtitle for the same current cue.

**Should be:** Ruby comes from the same `SubtitleManager` line segments; word spans
come from the same tokenizer callback; hit resolution uses the shared span chooser;
annotation uses the same provider/index/version. Intentional presentation
differences must not alter semantic results.

### HOVER-009 - Tooltip delay starts when leaving its word

**Was wrong:** The subtitle tooltip remained visible while the pointer was anywhere
inside the subtitle window. Moving across blank subtitle space also repeatedly
restarted the hide timer.

**Should be:** Leaving the specific active word starts the configured tooltip hide
delay immediately, even while the pointer remains inside the subtitle window.
Re-entering that word or entering the tooltip cancels the pending hide. A delay of
`0` hides immediately and must not be replaced by a default delay.

**Status:** Fixed and directly checked on 2026-07-16.

### HOVER-010 - Tooltip remains above subtitle after re-entry

**Was wrong:** Moving from a subtitle word into its tooltip and quickly back onto
the same word raised the subtitle window above the still-visible tooltip.

**Should be:** Returning to the subtitle window must restore any visible tooltip to
the top without activating a window, changing the active word, or restarting the
hover content.

**Status:** Fixed on 2026-07-17.

## Subtitle Cue, Cleaning, And Layout

### SUB-001 - Right-click at a cue boundary

**Input:** Reported near 15:53/15:54 in
`寄生獣.セイの格率.S01E07...ja[cc].srt`.

**Was wrong:** Immediately after the displayed subtitle changed, right-clicking the
new cue jumped back and opened the previous subtitle in the popup.

**Should be:** Popup uses the currently rendered cue/index. It must not resample an
older playback time or reopen the previous cue at a boundary.

### SUB-002 - Fixed two-line vertical model

**Was wrong:** Subtitle/ruby rows changed position over time.

**Should be:** Vertical order is `Ruby1`, `Line1`, `Line2`, `Ruby2`. A one-line cue
occupies `Line2` with its ruby in `Ruby2`; a two-line cue uses `Line1` and `Line2`
with each ruby in its corresponding ruby row.

### SUB-003 - Parenthetical non-speech cues

**Inputs**

```text
（ジャックの 雄たけびが 続く）
（ヘリコプターの プロペラ 音が 続く）
```

**Was wrong:** These remained visible when configured non-speech/sound cues should
have been hidden.

**Should be:** Subtitle cleaning recognizes the complete parenthetical sound cue;
ordinary dialogue containing similar words must not be removed.

### SUB-004 - Multiline Anki sentence preservation

**Was wrong:** The app passed a raw newline into Anki fields. Anki fields are HTML,
so the card display collapsed both subtitle lines into one line.

**Should be:** `SentenceJA` and `AddRubiesToSentenceJA` preserve the subtitle line
break at the same position. Keep real newlines during preparation and preview, then
encode them as `<br>` when committing the note to Anki. Translation input may
normalize lines only where the configured provider requires it. Existing raw-newline
and new `<br>` notes must remain equivalent for same-sentence media matching.

**Status:** Fixed on 2026-07-18.

### SUB-005 - Slider thumb preview at startup

**Was wrong:** The time label above the slider thumb started at a position that did
not correspond to the current time.

**Should be:** Label position and value derive from the same clamped slider fraction
after geometry is known, including at startup and after episode changes.

### SUB-006 - Scrubbing responsiveness

**Was wrong:** Slider scrubbing became laggy after time-label/end-time changes.

**Should be:** Dragging updates preview/time without synchronous subtitle parsing,
network work, or repeated expensive renders; final seek commits on release.

## Interaction And Window Behavior

### UI-001 - Advanced Settings entries

**Was wrong:** Entries required two clicks, lost the first click after another app
had focus, or blocked control-window entries after Advanced Settings opened.

**Should be:** One click focuses any entry and typing works immediately. Latest
explicit behavior: Apply Now and Enter apply settings and remove entry focus;
clicking elsewhere in the app removes entry focus without making the next entry
require a second click.

### UI-002 - Advanced Settings footer

**Was wrong:** Status text and Apply/Reload/Reset/Close buttons could be clipped when
the window was resized or a tab was tall.

**Should be:** Footer remains fixed and visible; only tab content scrolls.

### UI-003 - Advanced Settings taskbar/titlebar behavior

**Was wrong:** Opening activated the taskbar; minimizing left a small titlebar-only
rectangle; topmost/focus mechanics interfered with other windows.

**Should be:** Keep normal minimize/maximize/close controls. Minimize hides the
window, reopening restores it without activating a stray taskbar rectangle. The
Advanced button closes it when frontmost and raises it when obscured.

### UI-004 - Main/Advanced settings toggle from control window

**Was wrong:** With Advanced Settings open, the control-window settings button no
longer hid/showed the main settings window.

**Should be:** The control button coordinates both settings windows according to
their current visible state.

### UI-005 - Phone-mode subtitle drag handle

**Was wrong:** The subtitle drag window/handle appeared outside phone mode or did
not consistently disappear over settings.

**Should be:** It exists only in phone mode and respects the option to remove it.

### UI-006 - DPI-aware dragging

**Was wrong:** At 125% Windows scaling, windows jumped while dragged, pointer and
window separated, and moving to the far-left monitor could place a window at the
far right.

**Should be:** Use per-monitor DPI-aware absolute pointer/window coordinates and
valid negative virtual-screen geometry. Crossing monitors must not teleport.

### UI-007 - Control-window auto-hide

**Was wrong:** It disappeared while the pointer was still inside or immediately
after briefly leaving and returning.

**Should be:** Hide timeout starts only when the pointer is outside both subtitle
and control windows; re-entering cancels the pending hide.

### UI-008 - Control visibility hotkey when hover-show is disabled

**Should be:** If neither window is shown, show subtitle and controls. If only one
is shown, show the other too. If both are shown, hide only the controls.

### UI-009 - Crash reporting

**Was wrong:** The app exited during startup/runtime without a visible error or
useful final log entry.

**Should be:** Unhandled Tk, worker, startup, and native-adjacent failures are logged
with context; startup preparation completion/failure is explicit.

## Hotkeys And External Focus

### KEY-001 - Popup add/copy after using another application

**Was wrong:** After selecting/copying or using ABSPlayer/Explorer/Chrome, popup add,
copy, and translation hotkeys stopped until an app/subtitle window was clicked.

**Should be:** Safe configured global actions work while the popup selection is
valid, regardless of whether the foreground window is the app or configured
background Chrome window.

### KEY-002 - Chrome-specific hover failure

**Was wrong:** Hover hotkeys worked with Spotify/VS Code focused but not Chrome,
although pointer-inside detection succeeded. Some global jump keys still worked.

**Should be:** Physical hold-state polling and exact chord matching are foreground
application independent for approved global-safe actions.

### KEY-003 - Modifier chords and AltGr

**Was wrong:** `Ctrl+Y`, `Ctrl+Alt+I`, repeated Ctrl chords, and AltGr combinations
could fail or require releasing/repressing Ctrl.

**Should be:** All configured chord keys are detected as held, with AltGr normalized
according to the configured shortcut. Conflict warnings identify overlapping
actions; M1 and M2 mode bindings are not conflicts with each other.

### KEY-004 - Settings changes apply immediately

**Was wrong:** A changed hotkey did not work while Advanced Settings remained open
until another app window received focus.

**Should be:** Apply Now rebuilds runtime shortcut bindings/state immediately.

## Anki, Media, And Database

### ANKI-001 - Same-sentence media propagation

**Was wrong:** Image/audio copying only reached one preceding card or depended on
which card was most recently added.

**Should be:** After media becomes available, copy it to every relevant card from
the exact same `AddRubiesToSentenceJA` sentence, without overwriting unrelated
concurrent capture jobs.

### ANKI-002 - Delayed ABSPlayer media availability

**Was wrong:** A fixed copy-back delay missed media that ABSPlayer created later;
logs were noisy and did not identify useful state.

**Should be:** Each capture job independently polls its own note/sentence until
media appears or timeout expires, then propagates once. Multiple pending jobs do
not overwrite each other. Log start, success, timeout, and target count only.

### ANKI-003 - New cards enter the word database

**Was wrong:** Some newly added cards were not immediately present/annotated.

**Should be:** Successful add creates or updates the corresponding database entry,
refreshes the annotation provider, and redraws current subtitle/popup.

### ANKI-004 - Editing a database word

**Was wrong:** Editing meaning could create a duplicate, stop annotation, or fail to
update the original Anki note.

**Should be:** Use the stored note ID (`nid`) to write fields back to the same Anki
note and update the existing database record in place.

### ANKI-005 - Anime provenance

**Was wrong:** Word database/Anki notes did not preserve which anime created a card.

**Should be:** Store anime in the database and add the sanitized anime name itself
as the Anki tag, without an `Anime::` prefix. Existing hierarchical tags remain
readable for backward-compatible sync; notes without provenance remain blank unless
safely inferable.

### ANKI-006 - Pre-add preview

**Expected field order**

1. `AddRubiesToFront`
2. `Front`
3. `Back`
4. `SentenceJA`
5. `SentenceDE`
6. `Sound`
7. `Image`
8. `Definition`
9. `AddRubiesToSentenceJA`
10. `Tags`

**Was wrong:** Preview was not mouse-wheel scrollable or could be too small.

**Should be:** Centered, sized/wrapped to content, mouse-wheel scrollable, and send
the exact edited payload only after confirmation.

### ANKI-007 - Add plus external capture timing

**Was wrong:** Several overlapping delay settings made sequencing unclear.

**Should be:** One configurable offset controls when focus+external capture hotkey
runs relative to add completion; negative milliseconds permit starting capture
early. Media copy-back polling remains a separate availability mechanism.

### ANKI-008 - Selection whitespace

**Expected:** Leading/trailing accidental whitespace in selected vocabulary is
cleaned before lookup/add, while internal intended spaces and sentence line breaks
are preserved.

### ANKI-009 - Hovered subtitle add must not generate ruby twice

**Input:** `引[ひ]き 金[がね]を 引[ひ]くだけでクリア[くりあ]だ` from the
already-rendered subtitle segments.

**Was wrong:** The hovered-subtitle add shortcut passed this ruby-formatted display
text into the plain sentence input. `AddRubiesToSentenceJA` therefore contained
ruby, and the Anki generator produced nested readings such as
`引[ひき][ひ]き` and `クリア[くりあ][くりあ]` in `SentenceJA`.

**Should be:** When rendered subtitle ruby is available, use it directly as
`SentenceJA` and strip its reading brackets for `AddRubiesToSentenceJA`, sentence
translation, matching, and media lookup. Generate sentence ruby only for callers
that provide plain text. Preserve subtitle line breaks.

**Audit and repair:** 30 of the 50 newest notes in deck `Japanese` on 2026-07-16
had this specific structural corruption. All 30 were backed up, repaired through
AnkiConnect, and read back successfully. The two occurrences of contextual
`今[こん] 形態` were corrected to `今[いま] 形態` during the same repair.

### ANKI-010 - `する` headword must receive a verb translation

**Input:** Selected `決心` in `よくぞ 決心されましたね`, resolved headword
`決心する`.

**Was wrong:** Jisho correctly classified the entry as both a noun and a suru
verb, but supplied nominal English glosses (`determination`, `resolution`). Those
glosses were translated into the noun-only German Back
`Entschlossenheit, Entschiedenheit`.

**Should be:** When a `...する` query matches an explicit Jisho `Suru verb` POS,
translate the Japanese verb headword directly for `Back` while retaining Jisho's
glosses in `Definition`. Noun use of the stem keeps the noun translation. For this
case, `決心する` becomes `sich entschließen`, while `決心` remains
`Entschlossenheit, Entschiedenheit`.

## Episodes, Playback, And Persistence

### EP-001 - Stable user search identity across seasons

**Was wrong:** Reopening on a season/part-two file changed the search identity to
that file's display title, rebuilt a partial map, and lost earlier episodes.

**Should be:** Persist the user's original search query as map identity. The main UI
may show the current file/anime title, but all seasons/parts resolve through the
already built map for that query.

### EP-002 - New episodes in an existing search

**Example:** `Baki-Dou` plus differently named `Baki-Dou Part 2`.

**Was wrong:** Existing cached episode maps did not reliably discover new parts, or
reopening episode 14 showed only part two and episode 13 became unavailable.

**Should be:** Refresh merges newly discovered files into the existing canonical
map, preserves all old episodes, updates cache metadata, and does not depend on
hardcoded `Part 2` naming.

### EP-003 - Missing cached subtitle file

**Was wrong:** Applying settings could reuse a stale `cache_github` path and raise
`FileNotFoundError`.

**Should be:** Validate cached paths before loading; resolve/download again from the
episode map or show a controlled error.

### EP-004 - Anime-specific offsets

**Was wrong:** One offset was reused when changing anime.

**Should be:** Save and restore offset by stable anime/search identity, across
episodes and app runs.

### EP-005 - Episode labels and seasonal search

**Expected:** Dropdown retains global episode numbers and adds canonical labels such
as `27 (S2E1)`. Inputs like `s2e1`, `S02E001`, label text, and global episode number
resolve through the same episode map.

### PLAY-001 - Fast-forward speed display

**Latest expected behavior:** The control shows current speed rather than a toggle
button. Wheel changes by `0.1`, clamps at `0.1` instead of wrapping, and disabling
the feature removes the control and uses normal speed. Play/pause remains separate.

### PLAY-002 - Last playback mode and episode position

**Expected:** Remember M1/M2/M4 mode and per-episode playback position during the
session and across runs, unless the configured startup-default toggle overrides it.

### PLAY-003 - Offset changes refresh the active subtitle

**Was wrong:** Changing the offset adjusted the runtime/slider time, but subtitle
rendering could remain on cached content until another playback or seek update.

**Should be:** Preserve the existing runtime-position adjustment and immediately
force the subtitle overlay to resolve and render from the completed offset change.
This applies to the main Offset entry, Advanced Settings, profiles, and restored
anime-specific offsets.

### RUBY-017 - Number plus 発 counter

**Input:** `１発はくれてやる`.

**Was wrong:** `１` and `発` were not recognized as one counter expression, so the
combined reading was missing or attached only to part of the word.

**Should be:** Treat `１発` as one ruby span and render `１発[いっぱつ]`.
