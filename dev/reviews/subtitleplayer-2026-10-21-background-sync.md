# Background alignment and native audio readiness

- Caption alignment runs in a serial local Python child process. A supervisor
  thread handles its pipes; Tk only schedules jobs and applies completed maps.
- Automatic jobs wait for 800 ms of stable reference input. New pending input
  replaces old pending input; obsolete generations cannot update the current map.
- Browser event processing yields after an 8 ms budget instead of draining an
  unlimited backlog. Failed timing-cache lookups run once per context.
- **Abgleichen**, next to the automatic offset, requests fresh caption evidence
  and bypasses the computed-map cache. A confirmed same-video map stays usable
  during the retry, including frozen recording intervals.
- Native readiness comes from the offscreen document's actual live audio track,
  scoped to the connected tab. Binding, reconnection, popup, probes and capture
  recheck it. Old track-ended callbacks cannot invalidate a replacement stream.
- Ordinary episode replacements preserve the tab stream. A browser/extension
  restart or a genuinely ended track can still require Chrome's popup gesture.
- Capture diagnostics distinguish a disconnected video, stale clock, pending or
  unconfirmed alignment, and absent native audio. No uncertain timing is bypassed.

Validation: targeted alignment/runtime, native capture/playback, asbplayer and
media-reuse tests pass, together with extension and offscreen recorder tests.
Live manual alignment completed during playback while the app clock continued;
the existing confirmed offset remained visible. Native audio was reported ready
again after an app-only restart, without another capture grant. No live test note
was created. Full episode-transition latency was not separately benchmarked.

Source subtitle files, voice settings and existing notes are not migrated by
these changes. All implementation changes remain local.
