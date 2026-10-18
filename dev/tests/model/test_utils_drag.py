from types import SimpleNamespace

from SubtitlePlayer.utils import make_draggable
from SubtitlePlayer.view.subtitle_overlay import SubtitleOverlayUI


class _DragWidget:
    def __init__(self, x=0, y=0, width=100, height=40):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.pointer_x = x
        self.pointer_y = y
        self.bindings = {}
        self.geometry_calls = []

    def bind(self, sequence, callback):
        self.bindings[sequence] = callback

    def update_idletasks(self):
        pass

    def winfo_pointerx(self):
        return self.pointer_x

    def winfo_pointery(self):
        return self.pointer_y

    def winfo_x(self):
        return self.x

    def winfo_y(self):
        return self.y

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def winfo_exists(self):
        return True

    def geometry(self, value):
        self.geometry_calls.append(value)
        _empty, x_text, y_text = value.split("+")
        self.x = int(x_text)
        self.y = int(y_text)


def _event(state=0, x_root=0, y_root=0):
    return SimpleNamespace(state=state, x_root=x_root, y_root=y_root)


def test_make_draggable_emits_one_lifecycle_for_a_complete_drag():
    handle = _DragWidget()
    target = _DragWidget(10, 20, 200, 80)
    synced = _DragWidget(10, 20, 30, 80)
    events = []
    released = []
    make_draggable(
        handle,
        target,
        sync_windows=[synced],
        on_start=lambda: events.append("start"),
        on_move=lambda: events.append("move"),
        on_end=lambda: events.append("end"),
        on_release=lambda *geometry: released.append(geometry),
    )

    handle.bindings["<ButtonPress-1>"](_event())
    handle.pointer_x = 50
    handle.pointer_y = 70
    handle.bindings["<B1-Motion>"](_event())
    handle.bindings["<ButtonRelease-1>"](_event())

    assert (target.x, target.y) == (60, 90)
    assert (synced.x, synced.y) == (60, 90)
    assert events == ["start", "move", "end"]
    assert released == [(60, 90, 200, 80)]


def test_make_draggable_finishes_once_when_ctrl_interrupts_drag():
    handle = _DragWidget()
    target = _DragWidget(10, 20)
    events = []
    make_draggable(
        handle,
        target,
        on_start=lambda: events.append("start"),
        on_end=lambda: events.append("end"),
    )

    handle.bindings["<ButtonPress-1>"](_event())
    assert handle.bindings["<B1-Motion>"](_event(state=0x0004)) == "break"
    handle.bindings["<ButtonRelease-1>"](_event())

    assert events == ["start", "end"]


def test_subtitle_overlay_drag_callbacks_are_deduplicated_across_handles():
    overlay = object.__new__(SubtitleOverlayUI)
    overlay._drag_active = False
    calls = []
    SubtitleOverlayUI.bind_drag_callbacks(
        overlay,
        lambda: calls.append("start"),
        lambda: calls.append("end"),
    )

    SubtitleOverlayUI._on_drag_start(overlay)
    SubtitleOverlayUI._on_drag_start(overlay)
    assert overlay.is_dragging is True
    SubtitleOverlayUI._on_drag_end(overlay)
    SubtitleOverlayUI._on_drag_end(overlay)

    assert overlay.is_dragging is False
    assert calls == ["start", "end"]
