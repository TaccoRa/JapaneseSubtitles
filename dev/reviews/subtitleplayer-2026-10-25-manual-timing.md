# Manual timing without website captions

Approved: keep a numeric Offset field without an automatic reference, and apply
user-selected timing to display, navigation and native recording.

- Offset shows zero or the stored effective value, never an ellipsis. An explicit
  valid commit (including Enter on zero) confirms manual timing when no confirmed
  automatic region is available. Merely showing zero does not claim confirmation.
- Manual total offsets are stored separately from automatic fine corrections in
  `manualOffsets`, keyed by the existing media/SRT-content/mode key. Existing
  profiles, trims and source subtitle bytes are unchanged.
- A separately cached `manual` timing map supplies the same intervals to display,
  cue navigation and native capture. Its trusted-cue flags reflect explicit user
  confirmation, not automatic evidence. Manual maps are not written to the
  automatic evidence cache. Diagnostics and connection status say manual timing.
- Native recording still requires a connected fresh video clock, enabled audio
  capture and a frozen cue from the matching source. Padding and screenshot timing
  use the manual map. Intervals entirely outside the video are rejected.
- Automatic analysis/cache lookup remain available in the background. When a
  confirmed automatic cue covers the manually displayed source position, convert
  the manual total into a fine correction while preserving that position. This
  avoids adding a full manual offset on top of the same automatic offset. An
  active capture or offset edit postpones adoption.
- Explicit refresh resets the manual correction only when the new map confirms
  the current video region. Failed or unsupported refresh retains manual timing.
  Recording requests already own their frozen boundaries/version and do not change.
- The original asbplayer capture mechanism remains unchanged.

Static AST parsing and in-memory compilation only; no automated tests, browser or
Anki actions, uploads, configuration edits or subtitle writes were performed.

Restart SubtitlePlayer; no extension reload is required. User checks:

1. Without site captions, set a positive and negative offset; confirm with Enter.
   Video position/play state must stay unchanged while subtitle timing changes.
2. Explicitly confirm zero and try native capture. Check manual status, padding,
   screenshot timing and subtitle navigation. Native audio permission is still needed.
3. Reconnect/restart, switch episodes and return: restore only the matching stored
   manual value. Changing the original SRT content starts a separate preference.
4. If automatic evidence later appears, check that the current timing does not
   double its offset. Later timing steps keep the resulting fine correction.
5. Refresh with missing/uncertain evidence, then with confirmed evidence; manual
   timing survives failure and is reset only after successful current-region mapping.
