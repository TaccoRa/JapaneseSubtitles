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


START_TIME = 12 * 3600.0
TOTAL_DURATION = 24 * 3600.0


def make_controller(update_interval_ms: int, skip_seconds: float):
    root = FakeRoot()
    controller = SimpleNamespace(
        _shutting_down=False,
        _update_loop_job=None,
        audio_padding=0.0,
        current_time=START_TIME,
        entry_editing=False,
        get_offset_value=lambda: 0.0,
        last_subtitle_text="",
        last_update=0.0,
        overlay=SimpleNamespace(root=root),
        playing=True,
        settings=SimpleNamespace(
            slider=FakeSlider(),
            play_pause_btn=FakeButton(),
            _last_skip_value=float(skip_seconds),
        ),
        slider_dragging=False,
        sub_manager=SimpleNamespace(subtitles=[]),
        subtitle_deleted=False,
        subtitle_timeout_job=None,
        total_duration=TOTAL_DURATION,
        update_interval_ms=int(update_interval_ms),
        update_time_and_subtitle_displays=lambda: None,
        _get_display_start_times=lambda: [],
        _skip_buttons_use_subtitle_segments=lambda: False,
        _schedule_hide_controls=lambda: None,
        control_time_entry_return=lambda _event: None,
    )
    playback = PlaybackController(controller)
    return controller, playback


def generate_events(count: int, seed: int, direction_mode: str):
    rng = random.Random(seed)
    events = []
    for _ in range(count):
        if direction_mode == "back":
            direction = -1
        elif direction_mode == "forward":
            direction = 1
        else:
            direction = -1 if rng.random() < 0.5 else 1
        gap = rng.uniform(0.040, 0.280)
        hold = rng.uniform(0.020, 0.160)
        latency = rng.uniform(0.000, 0.018)
        events.append((direction, gap, hold, latency))
    return events


def run_fixed(events, update_interval_ms: int, app_skip_seconds: float, video_skip_seconds: float):
    controller, playback = make_controller(update_interval_ms, app_skip_seconds)
    now = 0.0
    playback._now = lambda: now
    app_clock = 0.0
    video_time = float(controller.current_time)
    update_interval = update_interval_ms / 1000.0

    for direction, gap, hold, latency in events:
        key_event = app_clock + gap
        dispatch = key_event + hold + latency

        tick = float(controller.last_update) + update_interval
        while tick <= dispatch:
            now = tick
            playback.update_loop()
            tick += update_interval

        now = dispatch
        if direction < 0:
            playback.go_back(event_time=key_event)
        else:
            playback.go_forward(event_time=key_event)

        video_time += (key_event - app_clock) + (direction * video_skip_seconds)
        app_clock = key_event

    return float(controller.current_time) - video_time


def run_old(events, update_interval_ms: int, app_skip_seconds: float, video_skip_seconds: float):
    current_time = START_TIME
    video_time = START_TIME
    app_clock = 0.0
    last_update = 0.0
    update_interval = update_interval_ms / 1000.0

    for direction, gap, hold, latency in events:
        key_event = app_clock + gap
        dispatch = key_event + hold + latency

        tick = last_update + update_interval
        while tick <= dispatch:
            current_time += tick - last_update
            last_update = tick
            tick += update_interval

        current_time += direction * app_skip_seconds
        video_time += (key_event - app_clock) + direction * video_skip_seconds
        app_clock = key_event

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
    parser = argparse.ArgumentParser(description="Benchmark repeated skip hotkey sync drift.")
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--skip", type=float, default=5.0)
    parser.add_argument(
        "--video-skip",
        type=float,
        default=None,
        help="Video player's skip amount. Defaults to --skip.",
    )
    parser.add_argument("--update-ms", type=int, nargs="*", default=[100, 250, 500])
    parser.add_argument(
        "--direction",
        choices=["all", "mixed", "back", "forward"],
        default="all",
        help="Which skip direction pattern to benchmark.",
    )
    args = parser.parse_args()
    video_skip = args.skip if args.video_skip is None else float(args.video_skip)

    print("Skip sync drift benchmark")
    print(f"events/run: {args.events}")
    print(f"runs:       {args.runs}")
    print(f"app skip:   {args.skip:.3f} s")
    print(f"video skip: {video_skip:.3f} s")
    print("latency:    random 0-18 ms per hotkey dispatch")
    print("hold:       random 20-160 ms per key tap")
    print()

    direction_modes = ["mixed", "back", "forward"] if args.direction == "all" else [args.direction]
    for direction_mode in direction_modes:
        print(f"direction:  {direction_mode}")
        for update_ms in args.update_ms:
            old_drifts = []
            fixed_drifts = []
            for run_idx in range(args.runs):
                events = generate_events(args.events, seed=7331 + run_idx, direction_mode=direction_mode)
                old_drifts.append(run_old(events, update_ms, args.skip, video_skip))
                fixed_drifts.append(run_fixed(events, update_ms, args.skip, video_skip))

            old = summarize(old_drifts)
            fixed = summarize(fixed_drifts)
            print(f"  UPDATE_INTERVAL_MS={update_ms}")
            print(
                "    old accounting:   "
                f"avg={old['avg_ms']:.2f} ms, "
                f"median={old['median_ms']:.2f} ms, "
                f"worst_abs={old['worst_abs_ms']:.2f} ms, "
                f"range=[{old['min_ms']:.2f}, {old['max_ms']:.2f}] ms"
            )
            print(
                "    fixed accounting: "
                f"avg={fixed['avg_ms']:.6f} ms, "
                f"median={fixed['median_ms']:.6f} ms, "
                f"worst_abs={fixed['worst_abs_ms']:.6f} ms, "
                f"range=[{fixed['min_ms']:.6f}, {fixed['max_ms']:.6f}] ms"
            )
        print()


if __name__ == "__main__":
    main()
