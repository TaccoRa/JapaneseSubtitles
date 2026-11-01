"""Keep the browser pause lease while moving between subtitle and popup windows."""
from types import SimpleNamespace as NS

from controller.overlay_controller import OverlayController
from view.popup import CopyPopup


class Scheduler:
    def __init__(self):
        self.time, self.serial, self.jobs = 0, 0, {}

    def after(self, delay, callback):
        self.serial += 1
        self.jobs[self.serial] = (self.time + delay, callback)
        return self.serial

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, delay):
        target = self.time + delay
        while self.jobs:
            job, (when, callback) = min(self.jobs.items(), key=lambda item: item[1][0])
            if when > target:
                break
            self.time = when
            self.jobs.pop(job)
            callback()
        self.time = target


def setup(paused=False, enabled=True):
    root, pointer, commands = Scheduler(), [20, 10], []

    class Window:
        def __init__(self, y, height): self.y, self.height, self.visible = y, height, True
        def winfo_exists(self): return self.visible
        def winfo_viewable(self): return self.visible
        def winfo_pointerx(self): return pointer[0]
        def winfo_pointery(self): return pointer[1]
        def winfo_rootx(self): return 0
        def winfo_rooty(self): return self.y
        def winfo_width(self): return 100
        def winfo_height(self): return self.height
        def attributes(self, *args): pass

    subtitle, window = Window(0, 30), Window(40, 100)
    popup = object.__new__(CopyPopup)
    popup._popup = window
    popup._hover_ruby_window = popup._hover_layer_window = None
    popup._hover_layer_label = None
    popup._pinned = popup._menu_open = popup._dragging = False
    popup._cancel_close = popup._cancel_hover_clear = lambda: None
    popup._schedule_hover_clear = lambda **kwargs: None
    popup._destroy_hover_ruby_window = popup._reset_popup_state = lambda: None
    browser = NS(following=True, _connected=True, fresh=True, identity='video', _capture=None,
                 snapshot=NS(paused=paused, fingerprint='episode'), _control_generation=0)
    def command(action, on_complete=None):
        commands.append(action)
        browser._control_generation += 1
        browser.snapshot.paused = action == 'pause'
        if on_complete:
            on_complete(True)
    browser.command = command
    owner = NS(subtitle_hover_pause_video=enabled, subtitles_user_hidden=False,
               settings=NS(root=root, default_phone_mode=True), overlay=NS(sub_window=subtitle),
               renderer=NS(_hover_text_window=None), popup=popup, browser_sync=browser)
    overlay = OverlayController(owner)
    object.__setattr__(overlay, '_windows_alive', lambda: True)
    popup._pointer_inside_window = overlay._pointer_inside_window
    popup.bind_hover_presence(overlay.hover_popup_presence)
    return NS(root=root, pointer=pointer, commands=commands, popup=popup, window=window, browser=browser, overlay=overlay)


def test_subtitle_to_popup_keeps_pause_and_resumes_once_after_leaving_both():
    env = setup()
    env.overlay._enter_hover_domain()
    assert env.commands == ['pause']
    env.pointer[:] = [20, 70]
    env.overlay.sub_window_leave(None)
    env.popup._on_popup_enter()
    env.root.advance(250)
    assert env.commands == ['pause']
    env.pointer[:] = [200, 200]
    env.popup._on_popup_leave()
    env.root.advance(250)
    assert env.commands == ['pause', 'play']


def test_entering_popup_directly_pauses_and_child_leave_events_do_not_resume():
    env = setup()
    env.pointer[:] = [20, 70]
    env.popup._on_popup_enter()
    env.popup._on_popup_leave()  # Moving between its entry and other child widgets.
    env.root.advance(250)
    assert env.commands == ['pause']


def test_previously_paused_video_is_never_resumed_by_popup_hover():
    env = setup(paused=True)
    env.pointer[:] = [20, 70]
    env.popup._on_popup_enter()
    env.pointer[:] = [200, 200]
    env.popup._on_popup_leave()
    env.root.advance(250)
    assert env.commands == []


def test_disabled_hover_pause_does_not_control_playback():
    env = setup(enabled=False)
    env.pointer[:] = [20, 70]
    env.popup._on_popup_enter()
    env.pointer[:] = [200, 200]
    env.popup._on_popup_leave()
    env.root.advance(250)
    assert env.commands == []


def test_destroying_popup_releases_its_pause_when_pointer_is_outside_subtitles():
    env = setup()
    env.pointer[:] = [20, 70]
    env.popup._on_popup_enter()
    env.window.visible = False
    env.popup._on_popup_destroy(env.window)
    env.root.advance(250)
    assert env.commands == ['pause', 'play']


def test_manual_playback_intervention_invalidates_popup_resume():
    env = setup()
    env.pointer[:] = [20, 70]
    env.popup._on_popup_enter()
    env.browser._control_generation += 1
    env.pointer[:] = [200, 200]
    env.popup._on_popup_leave()
    env.root.advance(250)
    assert env.commands == ['pause']


def test_popup_context_menu_keeps_pause_until_dismissed():
    env = setup()
    env.pointer[:] = [20, 70]
    env.popup._on_popup_enter()
    env.popup._menu_open = True
    env.pointer[:] = [200, 200]
    env.popup._on_popup_leave()
    env.root.advance(250)
    assert env.commands == ['pause']
    env.popup._menu_open = False
    env.root.advance(250)
    assert env.commands == ['pause', 'play']
