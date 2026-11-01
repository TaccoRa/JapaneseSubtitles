# Disk-only startup and deferred source choice

Startup no longer invokes remote initialization/search or the source dialog from
the preparation worker. It checks LAST_LOCAL_SRT_FILE and, for a saved GitHub URL,
the corresponding expected cache filename. Saved episode maps can be read from
disk without a provider-plan refresh. A cached file for a different URL filename
is not silently substituted.

When no usable local source exists, the empty model completes UI construction.
After the main window is shown and the startup overlay closed, the existing source
menu opens. It explains the missing local source and shows the previous search.
Remote Search prefills that query with selected text, allowing retry or replacement;
Jimaku, saved search, local file and URL choices remain available. Cancelling leaves
the app usable and does not change saved source settings or episode progress.

Automatic browser source restoration/selection and episode prefetch cannot bypass
this initial decision while startup_needs_source remains true. A successfully
loaded user-selected source clears that flag and restores ordinary behavior.

Validation: static AST/compile of four Python files and whitespace checks only.
No automated tests, live app/browser actions, downloads or Git uploads performed.

Manual checks: restart with an existing local/cached file (loads without search),
then with a missing file/old URL (main UI then source menu); retry the previous
search, choose Jimaku or another source, and cancel the menu. Confirm cancelling
does not trigger an automatic One Piece search or overwrite remembered progress.
App restart required; extension unchanged. Runtime behavior not live verified.
