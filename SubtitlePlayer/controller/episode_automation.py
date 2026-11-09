"""Opt-in episode/intro automation, tied to the exact connected media."""
import time
import uuid


class EpisodeAutomation:
    def __init__(self, browser):
        self.browser = browser
        self.skipped = None
        self.next_requested = None
        self.pending = None
        self.last_probe = 0.0

    def media_key(self):
        b = self.browser
        return (b.identity, b.snapshot.fingerprint, getattr(b.snapshot, 'resource_fingerprint', ''),
                getattr(b.snapshot, 'player_fingerprint', '')) if b.identity and b.snapshot else None

    def series_key(self):
        b = self.browser
        context = b._last_episode_context or b._media_context or {}
        # Netflix watch IDs change every episode; title/season is the stable fallback.
        return str(context.get('seriesKey') or ('title:' + str(context.get('title') or '').strip().casefold()
            if context.get('title') else ''))

    def marker(self, action='status'):
        b = self.browser
        key = self.series_key()
        if not key:
            return 'Connect a video with series information first.'
        mapping = dict(b.controller.config.get('INTRO_SKIP_TARGETS') or {})
        if action == 'remember':
            if not b.snapshot or b.snapshot.current_time <= 0:
                return 'Play or seek to the intro/title target first.'
            mapping[key] = round(b.snapshot.current_time, 3)
            b.controller.config.set('INTRO_SKIP_TARGETS', mapping)
        elif action == 'clear':
            mapping.pop(key, None)
            b.controller.config.set('INTRO_SKIP_TARGETS', mapping)
        if action in {'remember', 'clear'}:
            persist = getattr(b.controller.settings, 'persist_active_profile_fields', None)
            if callable(persist):
                persist({'INTRO_SKIP_TARGETS': mapping})
        value = mapping.get(key)
        return f'This series: intro/title target {float(value):.1f} seconds.' if value else 'This series: website Skip Intro button only.'

    def skip_intro(self, *, manual=True):
        b = self.browser
        if not b.following or not self.media_key() or b._capture or getattr(b, '_episode_navigation', None):
            if manual:
                b.report_error('Intro skip unavailable: connect a video and finish any recording or episode change first')
            return False
        if self.pending:
            return False
        operation = uuid.uuid4().hex
        self.pending = dict(id=operation, media=self.media_key(), manual=manual,
                            before=b.snapshot.current_time, started=time.monotonic())
        try:
            b.bridge.send(dict(type='SkipIntro', operationId=operation, identity=b.identity.wire(), fingerprint=b.snapshot.fingerprint))
        except Exception as exc:
            self.pending = None
            if manual:
                b.report_error('Intro skip failed: ' + str(exc))
            return False
        b.controller.settings.root.after(3000, lambda: self.ack(dict(operationId=operation, ok=False,
            error='Website did not confirm an intro skip')))
        if manual:
            b._set_status('Looking for Skip Intro…')
        return True

    def ack(self, event):
        pending = self.pending
        if not pending or event.get('operationId') != pending['id']:
            return
        b = self.browser
        if self.media_key() != pending['media']:
            self.pending = None
            return
        if event.get('ok'):
            pending['accepted'] = True
            return
        self.pending = None
        error = str(event.get('error') or '')
        if (b._capture or getattr(b, '_episode_navigation', None)
                or error and 'No website Skip Intro control' not in error):
            if pending['manual']:
                b.report_error(error or 'Intro skip interrupted by recording or episode navigation')
            return
        target = (b.controller.config.get('INTRO_SKIP_TARGETS') or {}).get(self.series_key())
        try:
            target = float(target or 0)
        except (ValueError, TypeError):
            target = 0
        if target > b.snapshot.current_time + .25 and target < b.snapshot.duration:
            media = pending['media']
            def finished(ok):
                if media != self.media_key():
                    return
                if ok:
                    self.skipped = media
                    b._set_status(f'Skipped to saved intro/title target ({target:.1f} s)')
                else:
                    self.skipped = None
                    if pending['manual']:
                        b.report_error('Could not seek to the saved intro/title target')
            try:
                self.skipped = media
                if not b.command('seek', time=target, on_complete=finished):
                    self.skipped = None
                    finished(False)
            except Exception as exc:
                self.skipped = None
                if pending['manual']:
                    b.report_error('Intro skip failed: ' + str(exc))
        elif pending['manual']:
            b.report_error(str(event.get('error') or 'No Skip Intro control or later saved intro/title target is available'))

    def tick(self, event=None):
        b = self.browser
        key = self.media_key()
        if not key or not b.following or b._capture or getattr(b, '_episode_navigation', None):
            return
        pending = self.pending
        if pending and pending.get('accepted'):
            elapsed = max(0, time.monotonic() - pending['started'])
            natural = elapsed * max(1, getattr(b.snapshot, 'rate', 1))
            if key != pending['media']:
                self.pending = None
            elif b.snapshot.current_time > pending['before'] + natural + 1:
                self.skipped = key
                self.pending = None
                b._set_status('Skipped intro using the website control')
        config = b.controller.config
        if event == 'ended' and config.get('AUTO_NEXT_EPISODE') and self.next_requested != key:
            self.next_requested = key
            b.navigate_episode('next', autoplay=True)
        if (config.get('AUTO_SKIP_INTRO') and self.skipped != key and not b.snapshot.paused
                and not b.snapshot.seeking and not b.snapshot.buffering and time.monotonic() - self.last_probe >= 2):
            self.last_probe = time.monotonic()
            self.skip_intro(manual=False)
