# Startup source discovery

The startup source prompt used a four-second grace period. In the reported run,
the browser connected about six seconds after startup and automatic subtitle
selection then succeeded. The prompt appeared before browser discovery finished.

Startup now allows 15 seconds for the initial browser connection and at least
10 seconds for metadata after a newly connected video is observed. Once an
automatic source request is active, the generic chooser stays closed; the source
pipeline owns any specific search or confirmation fallback. Explicit manual
selection, cancellation, and shutdown retain ownership over the startup timer.

Known browser media is resolved through the normal cached-map path. Only the
explicit refresh action requests a map refresh.

Eight focused tests pass for delayed connection, late metadata, slow lookup,
manual fallback, explicit selection ownership, and cached versus refreshed
resolution. Python compilation and the scoped whitespace check also pass.

The separate native-recording question requires no code change. Chrome requires
a user invocation of the extension before tab audio capture. Opening the popup
supplies that invocation, and the existing popup starts capture for the connected
tab. The background reuses a live stream across clips and episode navigation in
the same tab. A closed stream or a different tab can require another invocation.

Chrome reference: https://developer.chrome.com/docs/extensions/reference/api/tabCapture

Restart the desktop app to activate the startup change. No live browser capture
or user account interaction was performed for this change.
