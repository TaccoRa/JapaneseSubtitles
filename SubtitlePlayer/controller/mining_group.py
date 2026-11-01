"""Tk-owned capture groups; preparation never blocks the Anki writer queue."""
import threading
from model.capture_session import CaptureSession
from utils import dispatch_to_tk


class MiningGroups:
    def __init__(self, anki):
        self.anki = anki
        self.groups = {}

    def start(self, session, prepared=None, callback=None):
        if session.id in self.groups:
            return self.groups[session.id]
        group = dict(session=session, tasks=[], media=None, running=False)
        self.groups[session.id] = group
        if session.backend == 'native' and not session.capture_only:
            self._append(group, session.target, True, prepared, callback, owner=True)
        return group

    @staticmethod
    def same_sentence(left, right):
        return (left.sentence_key == right.sentence_key and left.source_hash == right.source_hash
                and left.media_key == right.media_key)

    def offer(self, target, capture, callback):
        browser = getattr(self.anki.controller, 'browser_sync', None)
        if browser is None:
            return False
        active = browser._capture
        group = self.groups.get(active.id) if active else next((g for g in reversed(list(self.groups.values()))
            if not g['session'].cancelled and not g.get('done') and self.same_sentence(g['session'].target, target)), None)
        if not active and capture:
            return False  # After the boundary, Capture always means a fresh clip.
        if active and group is None and active.capture_only:
            group = self.start(active)
        if not group:
            return False
        same = self.same_sentence(group['session'].target, target)
        if active and capture and not same:
            if callable(callback):
                callback(False, {'ignored': True})
            return True  # A different clip cannot compete with the recorder.
        self.anki._begin_action_feedback(target)
        self.anki._begin_word_feedback(target)
        if active is group['session']:
            group['session'].feedback_generation = int(getattr(self.anki.controller.renderer,
                '_voice_action_indicator_generation', -1))
        self._append(group, target, same, None, callback)
        if capture and target.initiator in ('mouse', 'popup_capture_key'):
            from utils import move_pointer_to_monitor_bottom
            move_pointer_to_monitor_bottom(self.anki.settings.root)
        return True

    def _append(self, group, target, shared, prepared, callback, owner=False):
        if any(t['target'] is target for t in group['tasks']):
            return
        task = dict(target=target, shared=shared, prepared=prepared, callback=callback,
                    ready=prepared is not None, owner=owner, error=None, started=False, completed=False)
        group['tasks'].append(task)
        if prepared is not None:
            self._drain(group)
            return
        anime = self.anki._current_anki_anime_name()
        def prepare():
            try:
                value = self.anki.anki.prepare_note_from_selection(selection_text=target.selected_text,
                    subtitle_text=target.subtitle_text, anime_name=anime, copy_existing_media=not shared,
                    cue_snapshot=target.cue_snapshot, selection_spans=target.selection_spans,
                    sentence_context=target.sentence_context)
                error = None
            except Exception as exc:
                value, error = None, str(exc)
            def ready():
                task.update(prepared=value, error=error, ready=True)
                self._drain(group)
            dispatch_to_tk(self.anki.settings.root, ready)
        threading.Thread(target=prepare, name='mining-prepare', daemon=True).start()

    def ready(self, session, media):
        group = self.groups.get(session.id)
        if group and not session.cancelled:
            group['media'] = dict(media)
            self._drain(group)

    def cancel(self, session):
        group = self.groups.get(session.id)
        if not group or group.get('done'):
            return
        group['done'] = True
        group['media'] = None
        for task in group['tasks']:
            if not task['started']:
                self.anki._finish_word_feedback(task['target'], False)
                self.anki._finish_action_feedback(task['target'], False)
                if callable(task['callback']):
                    task['callback'](False, None)
        self.groups.pop(session.id, None)

    def completed(self, session):
        group = self.groups.get(session.id)
        if group:
            self._drain(group)

    def pump(self):
        for group in list(self.groups.values()):
            self._drain(group)

    def _drain(self, group):
        session = group['session']
        if session.cancelled or group.get('done') or group['running'] or group['media'] is None:
            return
        active = self.anki.controller.browser_sync._capture
        if active and active.backend == 'asbplayer':
            return  # The original recorder must retain the last-created note.
        task = next((t for t in group['tasks'] if not t['started']), None)
        if task is None:
            if session.phase == 'completed':
                group['done'] = True
                group['media'] = None
                self.groups.pop(session.id, None)
            return
        if not task['ready']:
            return
        task['started'] = True
        target = task['target']
        if task['error']:
            self.anki._finish_word_feedback(target, False)
            self.anki._finish_action_feedback(target, False)
            self.anki.controller.browser_sync.report_error('Card preparation failed: ' + task['error'])
            if task['owner']:
                self.anki.controller.browser_sync.capture_note_failed(session, task['error'])
            if callable(task['callback']):
                task['callback'](False, None)
            self._drain(group)
            return
        recording = None
        if task['shared']:
            recording = session if task['owner'] else CaptureSession(target=target)
            if not task['owner']:
                recording.action_feedback_key = id(target)
                recording.media_identity = session.media_identity
                recording.media_fingerprint = session.media_fingerprint
                recording.backend = 'native'
                recording.phase = 'waiting_note'
                recording.native_result = dict(group['media'])
                recording.recording_feedback_done = True
                self.anki.controller.browser_sync._operations[recording.id] = recording
        group['running'] = True
        def complete(ok, result):
            if task['completed']:
                return
            task['completed'] = True
            group['running'] = False
            if callable(task['callback']):
                task['callback'](ok, result)
            self._drain(group)
        self.anki._start_anki_commit_worker(selected_text=target.selected_text,
            subtitle_text=target.subtitle_text, prepared=task['prepared'], post_add_capture=task['shared'],
            action_target=target, on_complete=complete, recording_session=recording, background=True)
