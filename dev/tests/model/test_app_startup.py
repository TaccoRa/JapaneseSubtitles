import app as app_module
import view.overlays as overlays_module
from app import SubtitlePlayerApp


class _FakeRoot:
    def __init__(self):
        self.deiconify_calls = 0
        self.lift_calls = 0
        self.quit_calls = 0
        self.destroy_calls = 0
        self.destroyed = False

    def deiconify(self):
        self.deiconify_calls += 1

    def lift(self):
        self.lift_calls += 1

    def update_idletasks(self):
        pass

    def quit(self):
        self.quit_calls += 1

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroy_calls += 1
        self.destroyed = True


class _FakeOverlay:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class _FakeToplevel:
    def __init__(self, _root):
        self.attribute_calls = []
        self.lift_calls = 0
        self.withdraw_calls = 0
        self.deiconify_calls = 0
        self.destroy_calls = 0

    def overrideredirect(self, _enabled):
        pass

    def attributes(self, name, value):
        self.attribute_calls.append((name, value))

    def grab_set(self):
        pass

    def grab_release(self):
        pass

    def update_idletasks(self):
        pass

    def update(self):
        pass

    def lift(self):
        self.lift_calls += 1

    def withdraw(self):
        self.withdraw_calls += 1

    def deiconify(self):
        self.deiconify_calls += 1

    def destroy(self):
        self.destroy_calls += 1


class _FakePackedWidget:
    def __init__(self, *_args, **_kwargs):
        pass

    def pack(self, **_kwargs):
        pass


class _FakeProgressbar(_FakePackedWidget):
    instances = []

    def __init__(self, *_args, **_kwargs):
        super().__init__()
        self.start_calls = []
        self.stop_calls = 0
        self.__class__.instances.append(self)

    def start(self, interval):
        self.start_calls.append(interval)

    def stop(self):
        self.stop_calls += 1


def _make_starting_app(monkeypatch):
    player = SubtitlePlayerApp()
    player.root = _FakeRoot()
    overlay = _FakeOverlay()
    player._startup_overlay = overlay
    registrations = []
    monkeypatch.setattr(app_module, "set_startup_overlay", registrations.append)
    return player, overlay, registrations


def test_startup_splash_uses_normal_window_stacking(monkeypatch):
    calls = []

    class FakeLoadingOverlay:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs))

    registrations = []
    monkeypatch.setattr(app_module, "LoadingOverlay", FakeLoadingOverlay)
    monkeypatch.setattr(app_module, "set_startup_overlay", registrations.append)

    player = SubtitlePlayerApp()
    player.root = object()
    player._show_startup_overlay()

    assert calls[0][0] == (player.root,)
    assert calls[0][1] == {
        "text": "Starting SubtitlePlayer...",
        "modal": False,
        "topmost": False,
    }
    assert registrations == [player._startup_overlay]


def test_non_topmost_overlay_stays_non_topmost_and_stops_animation(monkeypatch):
    _FakeProgressbar.instances.clear()
    windows = []

    def make_toplevel(root):
        window = _FakeToplevel(root)
        windows.append(window)
        return window

    monkeypatch.setattr(overlays_module.tk, "Toplevel", make_toplevel)
    monkeypatch.setattr(overlays_module.tk, "Frame", _FakePackedWidget)
    monkeypatch.setattr(overlays_module.tk, "Label", _FakePackedWidget)
    monkeypatch.setattr(overlays_module.ttk, "Progressbar", _FakeProgressbar)
    monkeypatch.setattr(overlays_module, "_center_toplevel", lambda *_args, **_kwargs: None)

    overlay = overlays_module.LoadingOverlay(object(), modal=False, topmost=False)
    overlay.hide()
    overlay.show()
    overlay.close()
    overlay.close()

    window = windows[0]
    progress = _FakeProgressbar.instances[0]
    assert window.attribute_calls == [
        ("-topmost", False),
        ("-topmost", False),
        ("-topmost", False),
    ]
    assert progress.start_calls == [50, 50]
    assert progress.stop_calls == 2
    assert window.withdraw_calls == 1
    assert window.deiconify_calls == 1
    assert window.destroy_calls == 1


def test_keyboard_interrupt_during_ui_build_closes_splash_and_app(monkeypatch):
    player, overlay, registrations = _make_starting_app(monkeypatch)
    messages = []

    def interrupt_build():
        raise KeyboardInterrupt

    monkeypatch.setattr(player, "_build_ui", interrupt_build)
    monkeypatch.setattr(player, "_show_error_message_once", lambda *args: messages.append(args))

    player._finish_startup_ui()

    assert player._closing is True
    assert player._startup_overlay is None
    assert overlay.close_calls == 1
    assert registrations == [None]
    assert player.root.quit_calls == 1
    assert player.root.destroy_calls == 1
    assert messages == []


def test_keyboard_interrupt_after_controller_build_still_closes_splash(monkeypatch):
    player, overlay, registrations = _make_starting_app(monkeypatch)

    class FakeController:
        def __init__(self):
            self.shutdown_calls = 0

        def shutdown(self):
            self.shutdown_calls += 1

    controller = FakeController()

    def interrupt_title_update():
        raise KeyboardInterrupt

    monkeypatch.setattr(player, "_build_ui", lambda: None)
    monkeypatch.setattr(player, "_build_renderer", lambda: None)
    monkeypatch.setattr(player, "_build_controller", lambda: setattr(player, "controller", controller))
    monkeypatch.setattr(player, "_update_title", interrupt_title_update)

    player._finish_startup_ui()

    assert player._closing is True
    assert player._startup_overlay is None
    assert overlay.close_calls == 1
    assert registrations == [None]
    assert controller.shutdown_calls == 1


def test_ui_build_error_reports_failure_and_does_not_orphan_splash(monkeypatch):
    player, overlay, registrations = _make_starting_app(monkeypatch)
    messages = []

    def fail_build():
        raise RuntimeError("broken settings UI")

    monkeypatch.setattr(player, "_build_ui", fail_build)
    monkeypatch.setattr(player, "_show_error_message_once", lambda *args: messages.append(args))

    player._finish_startup_ui()

    assert player._closing is True
    assert overlay.close_calls == 1
    assert registrations == [None]
    assert player.root.deiconify_calls == 1
    assert player.root.lift_calls == 1
    assert player.root.destroy_calls == 1
    assert messages == [("SubtitlePlayer startup failed", "RuntimeError: broken settings UI")]


def test_startup_worker_error_is_reported_and_torn_down(monkeypatch):
    player, overlay, registrations = _make_starting_app(monkeypatch)
    messages = []

    class FailingSubtitleManager:
        def __init__(self, _config):
            raise RuntimeError("subtitle initialization failed")

    monkeypatch.setattr(app_module, "SubtitleManager", FailingSubtitleManager)
    monkeypatch.setattr(player, "_show_error_message_once", lambda *args: messages.append(args))

    player._startup_worker()
    player._finish_startup()

    assert player._startup_done.is_set()
    assert isinstance(player._startup_error, RuntimeError)
    assert player._startup_error_info is None
    assert player._closing is True
    assert overlay.close_calls == 1
    assert registrations == [None]
    assert player.root.destroy_calls == 1
    assert messages == [
        ("SubtitlePlayer startup failed", "RuntimeError: subtitle initialization failed")
    ]


def test_successful_ui_startup_closes_splash_once_and_starts_updates(monkeypatch):
    player, overlay, registrations = _make_starting_app(monkeypatch)
    calls = []

    class FakeSubtitleOverlay:
        def show(self):
            calls.append("show")

    class FakeController:
        def schedule_update(self):
            calls.append("schedule")

    monkeypatch.setattr(player, "_build_ui", lambda: calls.append("build-ui"))
    monkeypatch.setattr(player, "_build_renderer", lambda: calls.append("build-renderer"))
    monkeypatch.setattr(player, "_build_controller", lambda: calls.append("build-controller"))
    monkeypatch.setattr(player, "_update_title", lambda: calls.append("title"))
    player.sub_overlay_ui = FakeSubtitleOverlay()
    player.controller = FakeController()

    player._finish_startup_ui()

    assert player._closing is False
    assert overlay.close_calls == 1
    assert registrations == [None]
    assert player.root.deiconify_calls == 1
    assert player.root.destroy_calls == 0
    assert calls == [
        "build-ui",
        "build-renderer",
        "build-controller",
        "title",
        "show",
        "schedule",
    ]


def test_tk_callback_keyboard_interrupt_requests_clean_shutdown(monkeypatch):
    player = SubtitlePlayerApp()
    player.root = _FakeRoot()
    messages = []
    monkeypatch.setattr(player, "_show_error_message_once", lambda *args: messages.append(args))

    error = KeyboardInterrupt()
    player._report_tk_callback_exception(KeyboardInterrupt, error, error.__traceback__)

    assert player._closing is True
    assert player.root.quit_calls == 1
    assert player.root.destroy_calls == 1
    assert messages == []


def test_mainloop_keyboard_interrupt_requests_clean_shutdown(monkeypatch):
    class InterruptingRoot(_FakeRoot):
        def __init__(self):
            super().__init__()
            self.cancelled_jobs = []

        def after(self, _delay, _callback):
            return "startup-check"

        def after_cancel(self, job):
            self.cancelled_jobs.append(job)

        def mainloop(self):
            raise KeyboardInterrupt

    player = SubtitlePlayerApp()
    root = InterruptingRoot()
    overlay = _FakeOverlay()
    diagnostics_closed = []
    monkeypatch.setattr(player, "_load_config", lambda: None)
    monkeypatch.setattr(player, "_build_root", lambda: setattr(player, "root", root))
    monkeypatch.setattr(player, "_show_startup_overlay", lambda: setattr(player, "_startup_overlay", overlay))
    monkeypatch.setattr(player, "_start_startup_worker", lambda: None)
    monkeypatch.setattr(player, "_close_crash_diagnostics", lambda: diagnostics_closed.append(True))

    player.run()

    assert player._closing is True
    assert overlay.close_calls == 1
    assert root.cancelled_jobs == ["startup-check"]
    assert root.quit_calls == 1
    assert root.destroy_calls == 1
    assert diagnostics_closed == [True]


def test_normal_mainloop_exit_releases_services_without_a_window_close_event(monkeypatch):
    player = SubtitlePlayerApp()
    root = _FakeRoot()
    root.after = lambda *_args: "startup-check"
    root.after_cancel = lambda *_args: None
    root.mainloop = lambda: None  # The existing settings Quit path uses root.quit().
    calls = []
    from types import SimpleNamespace
    player.controller = SimpleNamespace(shutdown=lambda: calls.append("shutdown"))
    monkeypatch.setattr(player, "_load_config", lambda: None)
    monkeypatch.setattr(player, "_build_root", lambda: setattr(player, "root", root))
    monkeypatch.setattr(player, "_show_startup_overlay", lambda: None)
    monkeypatch.setattr(player, "_start_startup_worker", lambda: None)
    monkeypatch.setattr(player, "_close_crash_diagnostics", lambda: calls.append("diagnostics"))
    player.run()
    assert calls == ["shutdown", "diagnostics"]
    player._on_close()
    assert calls == ["shutdown", "diagnostics"]
