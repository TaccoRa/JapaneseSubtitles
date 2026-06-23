import argparse
import random
import statistics
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "SubtitlePlayer"))

from controller.playback_controller import PlaybackController


class FakeRoot:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []

    def after(self, delay_ms, callback):
        handle = f"after-{len(self.scheduled)}"
        self.scheduled.append((handle, delay_ms, callback))
        return handle

    def after_cancel(self, handle):
        self.cancelled.append(handle)


class FakeSlider:
    def winfo_exists(self):
        return True

    def set(self, _value):
        return None


class FakeButton:
    def config(self, **_kwargs):
        return None


def make_controller(update_interval_ms: int):
    root = FakeRoot()
    controller = SimpleNamespace(
        _shutting_down=False,
        _update_loop_job=None,
        audio_padding=0.0,
        current_time=0.0,
        entry_editing=False,
        get_offset_value=lambda: 0.0,
        last_subtitle_text="",
        last_update=0.0,
        overlay=SimpleNamespace(root=root),
        playing=False,
        settings=SimpleNamespace(slider=FakeSlider(), play_pause_btn=FakeButton()),
        slider_dragging=False,
        sub_manager=SimpleNamespace(subtitles=[]),
        subtitle_deleted=False,
        subtitle_timeout_job=None,
        total_duration=24 * 3600.0,
        update_interval_ms=int(update_interval_ms),
        update_time_and_subtitle_displays=lambda: None,
        _get_display_start_times=lambda: [],
        _schedule_hide_controls=lambda: None,
        control_time_entry_return=lambda _event: None,
    )
    playback = PlaybackController(controller)
    return controller, playback


def generate_cycles(count: int, seed: int):
    rng = random.Random(seed)
    cycles = []
    for _ in range(count):
        play_duration = rng.uniform(0.015, 0.260)
        pause_gap = rng.uniform(0.040, 0.220)
        latency = rng.uniform(0.000, 0.018)
        cycles.append((play_duration, pause_gap, latency))
    return cycles


def run_fixed(cycles, update_interval_ms: int):
    controller, playback = make_controller(update_interval_ms)
    now = 0.0
    playback._now = lambda: now
    app_time = 0.0
    video_time = 0.0
    update_interval = update_interval_ms / 1000.0

    for play_duration, pause_gap, latency in cycles:
        start_event = app_time
        start_dispatch = start_event + latency
        now = start_dispatch
        playback.toggle_play(event_time=start_event)

        pause_event = start_event + play_duration
        pause_dispatch = pause_event + latency

        tick = start_dispatch + update_interval
        while tick <= pause_dispatch:
            now = tick
            playback.update_loop()
            tick += update_interval

        now = pause_dispatch
        playback.toggle_play(event_time=pause_event)

        video_time += play_duration
        app_time = pause_event + pause_gap

    return float(controller.current_time) - video_time


def run_old_accounting(cycles, update_interval_ms: int):
    current_time = 0.0
    video_time = 0.0
    app_time = 0.0
    playing = False
    last_update = 0.0
    update_interval = update_interval_ms / 1000.0

    for play_duration, pause_gap, latency in cycles:
        start_event = app_time
        start_dispatch = start_event + latency
        playing = True
        last_update = start_dispatch

        pause_event = start_event + play_duration
        pause_dispatch = pause_event + latency

        tick = start_dispatch + update_interval
        while tick <= pause_dispatch:
            if playing:
                current_time += tick - last_update
                last_update = tick
            tick += update_interval

        # Old bug: pausing changed state without adding pause_dispatch - last_update.
        playing = False

        video_time += play_duration
        app_time = pause_event + pause_gap

    return current_time - video_time


def summarize(values):
    values_ms = [v * 1000.0 for v in values]
    return {
        "avg_ms": statistics.mean(values_ms),
        "median_ms": statistics.median(values_ms),
        "worst_abs_ms": max(abs(v) for v in values_ms),
        "min_ms": min(values_ms),
        "max_ms": max(values_ms),
    }


def main():
    parser = argparse.ArgumentParser(description="Benchmark repeated spacebar play/pause sync drift.")
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--update-ms", type=int, nargs="*", default=[100, 250, 500])
    args = parser.parse_args()

    print("Spacebar sync drift benchmark")
    print(f"cycles/run: {args.cycles}")
    print(f"runs:       {args.runs}")
    print("latency:    random 0-18 ms per spacebar event")
    print()

    for update_ms in args.update_ms:
        old_drifts = []
        fixed_drifts = []
        for run_idx in range(args.runs):
            cycles = generate_cycles(args.cycles, seed=1337 + run_idx)
            old_drifts.append(run_old_accounting(cycles, update_ms))
            fixed_drifts.append(run_fixed(cycles, update_ms))

        old = summarize(old_drifts)
        fixed = summarize(fixed_drifts)
        print(f"UPDATE_INTERVAL_MS={update_ms}")
        print(
            "  old accounting:   "
            f"avg={old['avg_ms']:.2f} ms, "
            f"median={old['median_ms']:.2f} ms, "
            f"worst_abs={old['worst_abs_ms']:.2f} ms, "
            f"range=[{old['min_ms']:.2f}, {old['max_ms']:.2f}] ms"
        )
        print(
            "  fixed accounting: "
            f"avg={fixed['avg_ms']:.6f} ms, "
            f"median={fixed['median_ms']:.6f} ms, "
            f"worst_abs={fixed['worst_abs_ms']:.6f} ms, "
            f"range=[{fixed['min_ms']:.6f}, {fixed['max_ms']:.6f}] ms"
        )
        print()


if __name__ == "__main__":
    main()
