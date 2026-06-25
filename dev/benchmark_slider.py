import sys
from pathlib import Path
import argparse
import os
import time
import tkinter as tk
import random
import types
import collections
import statistics
from typing import Dict, List, Tuple

root_path = Path.cwd()
sys.path.insert(0, str(root_path / "SubtitlePlayer"))

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.subtitle_overlay import SubtitleOverlayUI
from controller.controller import SubtitleController
from controller.subtitle_navigation import SubtitleNavigationController
from controller.episode_controller import EpisodeController

# -----------------------------
# Benchmark settings
# -----------------------------
BENCHMARK_RUNS = 20
WARMUP_RUNS = 1
SLIDER_STEPS = 100
RESET_CACHE_EACH_RUN = False

# How many SRT files to benchmark per anime folder.
# Set to 0 to benchmark all files in each anime folder.
MAX_FILES_PER_ANIME = 3

SUBS_ROOT = root_path / "subs"
RANDOM_SEED = 1337


def configure_stdout_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark subtitle slider scrubbing and rendering.")
    parser.add_argument("--file", type=Path, help="Specific subtitle file to benchmark.")
    parser.add_argument("--current-config", action="store_true", help="Benchmark LAST_LOCAL_SRT_FILE from config.local.json/config.json.")
    parser.add_argument("--runs", type=int, default=BENCHMARK_RUNS, help="Measured runs per file.")
    parser.add_argument("--warmup", type=int, default=WARMUP_RUNS, help="Warmup runs per file.")
    parser.add_argument("--steps", type=int, default=SLIDER_STEPS, help="Slider steps per run.")
    parser.add_argument("--max-files", type=int, default=MAX_FILES_PER_ANIME, help="Max files per anime group; 0 means all.")
    parser.add_argument("--reset-cache-each-run", action="store_true", help="Clear renderer and ruby caches before each measured run.")
    parser.add_argument("--smoke", action="store_true", help="Fast acceptance smoke run.")
    return parser.parse_args()


def apply_cli_options(args) -> None:
    global BENCHMARK_RUNS, WARMUP_RUNS, SLIDER_STEPS, MAX_FILES_PER_ANIME, RESET_CACHE_EACH_RUN
    BENCHMARK_RUNS = max(1, int(args.runs))
    WARMUP_RUNS = max(0, int(args.warmup))
    SLIDER_STEPS = max(1, int(args.steps))
    MAX_FILES_PER_ANIME = int(args.max_files)
    RESET_CACHE_EACH_RUN = bool(args.reset_cache_each_run)
    if args.smoke:
        BENCHMARK_RUNS = min(BENCHMARK_RUNS, 3)
        WARMUP_RUNS = min(WARMUP_RUNS, 1)
        SLIDER_STEPS = min(SLIDER_STEPS, 60)
        if MAX_FILES_PER_ANIME <= 0 or MAX_FILES_PER_ANIME > 1:
            MAX_FILES_PER_ANIME = 1


def current_config_subtitle_file() -> Path:
    config = ConfigManager(str(root_path / "config.json"))
    value = config.get("LAST_LOCAL_SRT_FILE")
    if value and Path(str(value)).is_file():
        return Path(str(value))
    raise FileNotFoundError("No existing LAST_LOCAL_SRT_FILE found in merged config.")


def discover_anime_groups(subs_root: Path) -> Dict[str, List[Path]]:
    """
    Group SRT files by the first folder under subs/.

    Example:
      subs/Anime Name/Episode 01.srt -> anime group "Anime Name"
      subs/Anime Name/Season 1/Episode 02.srt -> anime group "Anime Name"
      subs/root_level.srt -> anime group "(root)"
    """
    groups: Dict[str, List[Path]] = collections.defaultdict(list)

    for srt_path in sorted(subs_root.rglob("*.srt")):
        try:
            rel = srt_path.relative_to(subs_root)
            if len(rel.parts) >= 2:
                anime_name = rel.parts[0]
            else:
                anime_name = "(root)"
        except Exception:
            anime_name = "(root)"

        groups[anime_name].append(srt_path)

    return dict(sorted(groups.items(), key=lambda item: item[0].lower()))


def select_sample_files(files: List[Path], max_files: int) -> List[Path]:
    files = sorted(files)

    if max_files <= 0 or len(files) <= max_files:
        return files

    if max_files == 1:
        return [files[len(files) // 2]]

    # Evenly spaced selection across the folder.
    indices = [
        round(i * (len(files) - 1) / (max_files - 1))
        for i in range(max_files)
    ]

    selected = []
    seen = set()
    for idx in indices:
        if idx not in seen:
            selected.append(files[idx])
            seen.add(idx)

    return selected


def build_test_environment(srt_path: Path, config_overrides: dict | None = None):
    config = ConfigManager(str(root_path / "config.json"))
    config.config["LAST_LOCAL_SRT_FILE"] = str(srt_path)
    config.config["REMOTE_FLAG"] = False
    if config_overrides:
        config.config.update(config_overrides)

    manager = SubtitleManager(config)

    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()

    overlay_geometry = manager.calculate_geometry_for_longest_lines(5)
    cleaned_subs = [item[0] for item in manager.display_data]

    overlay = SubtitleOverlayUI(
        root=root,
        config=config,
        cleaned_subs=cleaned_subs,
        overlay_geometry=overlay_geometry,
        start_hidden=True,
    )

    renderer = SubtitleRenderer(
        canvas=overlay.subtitle_canvas,
        config=config,
    )
    
    controller = object.__new__(SubtitleController)
    controller.sub_manager = manager
    controller.renderer = renderer
    time_overlay = tk.Canvas(root, width=360, height=18)
    time_overlay_text = time_overlay.create_text(0, 0, text="")
    slider = tk.Scale(root, from_=0, to=manager.get_total_duration(), orient="horizontal", resolution=0.1)
    control_time_str = tk.StringVar(root, value="")

    controller.settings = types.SimpleNamespace(
        root=root,
        _last_offset_value=0.0,
        time_overlay=time_overlay,
        time_overlay_text=time_overlay_text,
        control_time_str=control_time_str,
        slider=slider,
        update_time_overlay_position=lambda: None,
    )
    controller.overlay = overlay
    controller.current_time = 0.0
    controller.total_duration = manager.get_total_duration()
    controller.subtitle_timeout_job = None
    controller.last_rendered_index = None
    controller.last_subtitle_text = ""
    controller.last_subtitle_raw = ""
    controller.subtitle_deleted = False
    controller.slider_dragging = False
    controller._shutting_down = False
    controller._slider_render_job = None
    controller._slider_pending_value = None
    controller._defer_auto_ruby_once = False
    controller._auto_ruby_generation_id = 0
    controller._auto_ruby_thread = None
    controller.entry_editing = False
    controller.config = config
    controller._pending_seek_delta = 0.0
    controller.hide_subtitles_ms = config.get("SUBTITLE_TIMEOUT_MS")
    controller._hide_subtitles_temporarily = lambda: None
    controller.subtitle_navigation = SubtitleNavigationController(controller)
    controller.episode_controller = EpisodeController(controller)


    def _reset_canvas():
        renderer.canvas.delete("all")
        controller.last_rendered_index = None
        controller.last_subtitle_text = ""
        controller.subtitle_deleted = True

    controller._reset_canvas = _reset_canvas

    controller._get_display_start_times = SubtitleController._get_display_start_times.__get__(
        controller, SubtitleController
    )
    controller._segments_to_copy_text = SubtitleController._segments_to_copy_text
    controller._update_subtitle_display = SubtitleController._update_subtitle_display.__get__(
        controller, SubtitleController
    )
    controller.on_slider_change = SubtitleController.on_slider_change.__get__(
        controller, SubtitleController
    )
    controller.on_slider_release = SubtitleController.on_slider_release.__get__(
        controller, SubtitleController
    )
    controller.update_time_and_subtitle_displays = SubtitleController.update_time_and_subtitle_displays.__get__(
        controller, SubtitleController
    )

    class _PlaybackStub:
        def __init__(self, owner):
            self.owner = owner

        def set_current_time(self, value):
            self.owner.current_time = float(value)
            self.owner.update_time_and_subtitle_displays()

    controller.playback = _PlaybackStub(controller)

    return root, config, manager, overlay, renderer, controller


def install_external_timers(manager, renderer, stats, times):
    """
    Wrap the expensive methods so we can measure a full benchmark run.
    """
    orig_render_subtitle = renderer.render_subtitle
    orig_draw_text = renderer._draw_outlined_text
    orig_wrap = renderer._wrap_segments
    orig_measure = renderer._measure_text
    orig_auto_ruby = manager.ensure_auto_ruby_for_index
    orig_delete = renderer.canvas.delete
    orig_create_text = renderer.canvas.create_text

    def wrapped_render_subtitle(top, bottom, overlay_obj, *args, **kwargs):
        t0 = time.perf_counter()
        result = orig_render_subtitle(top, bottom, overlay_obj, *args, **kwargs)
        times["render_subtitle"] += time.perf_counter() - t0
        stats["render_subtitle_calls"] += 1
        return result

    def wrapped_draw_outlined_text(canvas, x, y, text, font, fill, outline, thickness, anchor="center", tags=()):
        stats["draw_outlined_text_calls"] += 1
        t0 = time.perf_counter()
        result = orig_draw_text(canvas, x, y, text, font, fill, outline, thickness, anchor=anchor, tags=tags)
        times["draw_outlined_text"] += time.perf_counter() - t0
        return result

    def wrapped_wrap_segments(*args, **kwargs):
        t0 = time.perf_counter()
        result = orig_wrap(*args, **kwargs)
        times["wrap_segments"] += time.perf_counter() - t0
        stats["wrap_segments_calls"] += 1
        return result

    def wrapped_measure_text(font_obj, text):
        t0 = time.perf_counter()
        result = orig_measure(font_obj, text)
        times["font_measure"] += time.perf_counter() - t0
        stats["font_measure_calls"] += 1
        return result

    def wrapped_auto_ruby(idx):
        t0 = time.perf_counter()
        result = orig_auto_ruby(idx)
        times["ensure_auto_ruby_for_index"] += time.perf_counter() - t0
        stats["ensure_auto_ruby_calls"] += 1
        return result

    def wrapped_delete(tag_or_id):
        if tag_or_id == "all":
            t0 = time.perf_counter()
            result = orig_delete(tag_or_id)
            times["canvas_delete_all"] += time.perf_counter() - t0
            stats["canvas_delete_all_calls"] += 1
            return result
        return orig_delete(tag_or_id)

    def wrapped_create_text(*args, **kwargs):
        stats["canvas_create_text_calls"] += 1
        t0 = time.perf_counter()
        result = orig_create_text(*args, **kwargs)
        times["canvas_create_text"] += time.perf_counter() - t0
        return result

    renderer.render_subtitle = wrapped_render_subtitle
    renderer._draw_outlined_text = wrapped_draw_outlined_text
    renderer._wrap_segments = wrapped_wrap_segments
    renderer._measure_text = wrapped_measure_text
    manager.ensure_auto_ruby_for_index = wrapped_auto_ruby
    renderer.canvas.delete = wrapped_delete
    renderer.canvas.create_text = wrapped_create_text


def reset_renderer_caches(renderer, manager):
    renderer._measure_cache.clear()
    renderer._outline_offset_cache.clear()
    renderer._split_cache.clear()
    renderer._wrap_cache.clear()
    renderer._layout_cache.clear()
    renderer._last_overlay_width = None
    renderer._layout_cache_hits = 0
    renderer._layout_cache_misses = 0

    try:
        manager._auto_ruby_cache.clear()
    except Exception:
        pass

    if hasattr(manager, "_ruby_stats"):
        for key in list(manager._ruby_stats.keys()):
            if isinstance(manager._ruby_stats[key], int):
                manager._ruby_stats[key] = 0
            elif isinstance(manager._ruby_stats[key], float):
                manager._ruby_stats[key] = 0.0


def _snapshot(stats, times):
    return collections.Counter(stats), dict(times)


def _scenario_delta(before, after, keys):
    return {key: after.get(key, 0) - before.get(key, 0) for key in keys}


def run_slider_simulation(controller, manager, root, steps=50):
    scrub_times = []
    if not manager.display_data:
        raise RuntimeError("No subtitle data loaded")

    start_value = manager.display_data[0][1]
    step = controller.total_duration / max(1, steps)

    for i in range(steps):
        controller.current_time = min(start_value + i * step, controller.total_duration)
        controller.subtitle_timeout_job = None
        controller.last_rendered_index = None
        controller.last_subtitle_text = ""
        controller.subtitle_deleted = False

        t0 = time.perf_counter()
        controller._update_subtitle_display(force=True)
        scrub_times.append(time.perf_counter() - t0)

        if controller.subtitle_timeout_job:
            root.after_cancel(controller.subtitle_timeout_job)
            controller.subtitle_timeout_job = None

    return {
        "avg_ms": (sum(scrub_times) / len(scrub_times)) * 1000.0,
        "min_ms": min(scrub_times) * 1000.0,
        "max_ms": max(scrub_times) * 1000.0,
        "total_ms": sum(scrub_times) * 1000.0,
    }


def _run_tk_until(root, predicate, timeout_sec=0.25):
    deadline = time.perf_counter() + float(timeout_sec)
    while time.perf_counter() < deadline:
        root.update()
        if predicate():
            return True
        time.sleep(0.001)
    return False


def run_fast_drag_burst(controller, manager, root, steps=240):
    """
    Simulate the user dragging faster than Tk can render.

    This measures whether slider callbacks stay cheap and whether redraw work is
    coalesced to the newest position.
    """
    if not manager.display_data:
        raise RuntimeError("No subtitle data loaded")

    controller.slider_dragging = True
    controller._slider_pending_value = None
    if getattr(controller, "_slider_render_job", None) is not None:
        try:
            root.after_cancel(controller._slider_render_job)
        except Exception:
            pass
        controller._slider_render_job = None

    start_value = manager.display_data[0][1]
    step = controller.total_duration / max(1, steps)

    start = time.perf_counter()
    slider = getattr(getattr(controller, "settings", None), "slider", None)
    for i in range(steps):
        value = min(start_value + i * step, controller.total_duration)
        if slider is not None:
            try:
                slider.set(value)
            except Exception:
                pass
        controller.on_slider_change(str(value))
    handler_ms = (time.perf_counter() - start) * 1000.0

    return {
        "handler_calls": steps,
        "handler_total_ms": handler_ms,
        "handler_avg_ms": handler_ms / max(1, steps),
    }


def run_fast_drag_burst_and_preview(controller, manager, root, stats, steps=240):
    before_render_calls = int(stats.get("render_subtitle_calls", 0))
    before_create_text = int(stats.get("canvas_create_text_calls", 0))
    before_auto = int(stats.get("ensure_auto_ruby_calls", 0))

    result = run_fast_drag_burst(controller, manager, root, steps=steps)
    _run_tk_until(
        root,
        lambda: int(stats.get("render_subtitle_calls", 0)) > before_render_calls,
        timeout_sec=0.35,
    )

    result.update(
        {
            "preview_render_calls": int(stats.get("render_subtitle_calls", 0)) - before_render_calls,
            "preview_create_text_calls": int(stats.get("canvas_create_text_calls", 0)) - before_create_text,
            "preview_auto_ruby_calls": int(stats.get("ensure_auto_ruby_calls", 0)) - before_auto,
        }
    )
    return result


def run_release_after_drag(controller, root, stats):
    before_render_calls = int(stats.get("render_subtitle_calls", 0))
    before_auto = int(stats.get("ensure_auto_ruby_calls", 0))
    start = time.perf_counter()
    controller.on_slider_release(None)
    root.update()
    total_ms = (time.perf_counter() - start) * 1000.0
    wait_start = time.perf_counter()
    auto_completed = _run_tk_until(
        root,
        lambda: int(stats.get("ensure_auto_ruby_calls", 0)) > before_auto,
        timeout_sec=2.0,
    )
    background_auto_ms = (time.perf_counter() - wait_start) * 1000.0 if auto_completed else 0.0
    return {
        "release_total_ms": total_ms,
        "release_render_calls": int(stats.get("render_subtitle_calls", 0)) - before_render_calls,
        "release_auto_ruby_calls": int(stats.get("ensure_auto_ruby_calls", 0)) - before_auto,
        "background_auto_completed": auto_completed,
        "background_auto_ms": background_auto_ms,
    }


def measure_slider_scenarios(srt_path: Path, config_overrides: dict | None = None) -> dict:
    scenarios = {}

    root, _config, manager, _overlay, renderer, controller = build_test_environment(srt_path, config_overrides)
    stats = collections.Counter()
    times = collections.defaultdict(float)
    install_external_timers(manager, renderer, stats, times)
    try:
        before_stats, before_times = _snapshot(stats, times)
        scenarios["cold_direct"] = run_slider_simulation(controller, manager, root, steps=SLIDER_STEPS)
        after_stats, after_times = _snapshot(stats, times)
        scenarios["cold_direct"].update(_scenario_delta(
            before_stats,
            after_stats,
            ["render_subtitle_calls", "canvas_create_text_calls", "ensure_auto_ruby_calls"],
        ))
        scenarios["cold_direct"].update({
            "render_subtitle_ms": (after_times.get("render_subtitle", 0.0) - before_times.get("render_subtitle", 0.0)) * 1000.0,
            "auto_ruby_ms": (after_times.get("ensure_auto_ruby_for_index", 0.0) - before_times.get("ensure_auto_ruby_for_index", 0.0)) * 1000.0,
        })

        before_stats, before_times = _snapshot(stats, times)
        scenarios["warm_direct"] = run_slider_simulation(controller, manager, root, steps=SLIDER_STEPS)
        after_stats, after_times = _snapshot(stats, times)
        scenarios["warm_direct"].update(_scenario_delta(
            before_stats,
            after_stats,
            ["render_subtitle_calls", "canvas_create_text_calls", "ensure_auto_ruby_calls"],
        ))
        scenarios["warm_direct"].update({
            "render_subtitle_ms": (after_times.get("render_subtitle", 0.0) - before_times.get("render_subtitle", 0.0)) * 1000.0,
            "auto_ruby_ms": (after_times.get("ensure_auto_ruby_for_index", 0.0) - before_times.get("ensure_auto_ruby_for_index", 0.0)) * 1000.0,
        })
    finally:
        root.destroy()

    root, _config, manager, _overlay, renderer, controller = build_test_environment(srt_path, config_overrides)
    stats = collections.Counter()
    times = collections.defaultdict(float)
    install_external_timers(manager, renderer, stats, times)
    try:
        before_stats, before_times = _snapshot(stats, times)
        scenarios["fast_drag_burst"] = run_fast_drag_burst_and_preview(
            controller,
            manager,
            root,
            stats,
            steps=max(SLIDER_STEPS, 240),
        )
        after_stats, after_times = _snapshot(stats, times)
        scenarios["fast_drag_burst"].update({
            "render_subtitle_ms": (after_times.get("render_subtitle", 0.0) - before_times.get("render_subtitle", 0.0)) * 1000.0,
            "auto_ruby_ms": (after_times.get("ensure_auto_ruby_for_index", 0.0) - before_times.get("ensure_auto_ruby_for_index", 0.0)) * 1000.0,
        })

        before_stats, before_times = _snapshot(stats, times)
        scenarios["release_after_drag"] = run_release_after_drag(controller, root, stats)
        after_stats, after_times = _snapshot(stats, times)
        scenarios["release_after_drag"].update({
            "render_subtitle_ms": (after_times.get("render_subtitle", 0.0) - before_times.get("render_subtitle", 0.0)) * 1000.0,
            "auto_ruby_ms": (after_times.get("ensure_auto_ruby_for_index", 0.0) - before_times.get("ensure_auto_ruby_for_index", 0.0)) * 1000.0,
        })
    finally:
        root.destroy()

    return scenarios


def benchmark_one_file(srt_path: Path, config_overrides: dict | None = None):
    scenarios = measure_slider_scenarios(srt_path, config_overrides)
    root, config, manager, overlay, renderer, controller = build_test_environment(srt_path, config_overrides)

    stats = collections.Counter()
    times = collections.defaultdict(float)
    install_external_timers(manager, renderer, stats, times)

    try:
        for _ in range(WARMUP_RUNS):
            if RESET_CACHE_EACH_RUN:
                reset_renderer_caches(renderer, manager)
            run_slider_simulation(controller, manager, root, steps=SLIDER_STEPS)

        run_avgs = []
        run_mins = []
        run_maxs = []
        run_totals = []

        for run_idx in range(BENCHMARK_RUNS):
            if RESET_CACHE_EACH_RUN:
                reset_renderer_caches(renderer, manager)

            result = run_slider_simulation(controller, manager, root, steps=SLIDER_STEPS)
            run_avgs.append(result["avg_ms"])
            run_mins.append(result["min_ms"])
            run_maxs.append(result["max_ms"])
            run_totals.append(result["total_ms"])

        overall_avg = statistics.mean(run_avgs)
        overall_min = min(run_mins)
        overall_max = max(run_maxs)
        overall_total = sum(run_totals)
        overall_stdev = statistics.stdev(run_avgs) if len(run_avgs) > 1 else 0.0
        throughput = 1000.0 / overall_avg if overall_avg > 0 else 0.0

        ruby_stats = getattr(manager, "_ruby_stats", {})

        return {
            "srt_path": srt_path,
            "avg_update_ms": overall_avg,
            "stddev_ms": overall_stdev,
            "best_update_ms": overall_min,
            "worst_update_ms": overall_max,
            "throughput": throughput,
            "total_ms": overall_total,
            "stats": stats,
            "times": times,
            "layout_hits": renderer._layout_cache_hits,
            "layout_misses": renderer._layout_cache_misses,
            "measure_cache_size": len(renderer._measure_cache),
            "wrap_cache_size": len(renderer._wrap_cache),
            "layout_cache_size": len(renderer._layout_cache),
            "ruby_stats": ruby_stats,
            "scenarios": scenarios,
            "run_avgs": run_avgs,
            "run_mins": run_mins,
            "run_maxs": run_maxs,
            "run_totals": run_totals,
        }
    finally:
        root.destroy()


def format_ms(value):
    return f"{value:7.2f}"


def print_file_result(anime_name: str, file_result: dict):
    srt_path = file_result["srt_path"]
    stats = file_result["stats"]
    times = file_result["times"]
    ruby_stats = file_result["ruby_stats"]

    file_name = srt_path.name
    print(f"\n[{anime_name}] {file_name}")
    print(f"  Avg update:    {file_result['avg_update_ms']:.2f} ms")
    print(f"  Std dev:       {file_result['stddev_ms']:.2f} ms")
    print(f"  Best update:   {file_result['best_update_ms']:.2f} ms")
    print(f"  Worst update:  {file_result['worst_update_ms']:.2f} ms")
    print(f"  Throughput:    {file_result['throughput']:.1f} updates/sec")
    print(f"  create_text:   {stats['canvas_create_text_calls']}")
    print(f"  render_subtitle total: {times['render_subtitle'] * 1000.0:.1f} ms")
    print(f"  draw_outlined_text:    {times['draw_outlined_text'] * 1000.0:.1f} ms")
    print(f"  wrap_segments:         {times['wrap_segments'] * 1000.0:.1f} ms")
    print(f"  measure_text:          {times['font_measure'] * 1000.0:.1f} ms")
    print(f"  ensure_auto_ruby:      {times['ensure_auto_ruby_for_index'] * 1000.0:.1f} ms")
    print(f"  canvas.delete(all):    {times['canvas_delete_all'] * 1000.0:.1f} ms")
    print(f"  layout hit rate:       {(
        (file_result['layout_hits'] / (file_result['layout_hits'] + file_result['layout_misses']) * 100.0)
        if (file_result['layout_hits'] + file_result['layout_misses']) > 0 else 0.0
    ):.1f}%")
    print(f"  auto-ruby generator_calls: {ruby_stats.get('generator_calls', 0)}")
    print(f"  auto-ruby generator_time:  {ruby_stats.get('generator_time', 0.0) * 1000.0:.1f} ms")

    scenarios = file_result.get("scenarios") or {}
    if scenarios:
        cold = scenarios.get("cold_direct") or {}
        warm = scenarios.get("warm_direct") or {}
        drag = scenarios.get("fast_drag_burst") or {}
        release = scenarios.get("release_after_drag") or {}
        print("  Scenario: cold direct first scrub")
        print(
            f"    avg={cold.get('avg_ms', 0.0):.2f} ms  "
            f"worst={cold.get('max_ms', 0.0):.2f} ms  "
            f"auto={cold.get('auto_ruby_ms', 0.0):.1f} ms  "
            f"renders={int(cold.get('render_subtitle_calls', 0))}"
        )
        print("  Scenario: warm direct scrub")
        print(
            f"    avg={warm.get('avg_ms', 0.0):.2f} ms  "
            f"worst={warm.get('max_ms', 0.0):.2f} ms  "
            f"auto={warm.get('auto_ruby_ms', 0.0):.1f} ms  "
            f"renders={int(warm.get('render_subtitle_calls', 0))}"
        )
        print("  Scenario: fast drag burst")
        print(
            f"    callbacks={int(drag.get('handler_calls', 0))}  "
            f"handler_avg={drag.get('handler_avg_ms', 0.0):.4f} ms  "
            f"preview_renders={int(drag.get('preview_render_calls', 0))}  "
            f"preview_auto={int(drag.get('preview_auto_ruby_calls', 0))}"
        )
        print("  Scenario: release after drag")
        print(
            f"    total={release.get('release_total_ms', 0.0):.2f} ms  "
            f"auto={release.get('auto_ruby_ms', 0.0):.1f} ms  "
            f"renders={int(release.get('release_render_calls', 0))}  "
            f"bg_auto_done={bool(release.get('background_auto_completed', False))}  "
            f"bg_auto_wait={release.get('background_auto_ms', 0.0):.1f} ms"
        )


def print_anime_summary(anime_name: str, file_results: List[dict]):
    if not file_results:
        return

    avg_update = statistics.mean(r["avg_update_ms"] for r in file_results)
    best_update = min(r["best_update_ms"] for r in file_results)
    worst_update = max(r["worst_update_ms"] for r in file_results)
    avg_create_text = statistics.mean(r["stats"]["canvas_create_text_calls"] for r in file_results)
    avg_layout_hit = statistics.mean(
        (r["layout_hits"] / (r["layout_hits"] + r["layout_misses"]) * 100.0)
        if (r["layout_hits"] + r["layout_misses"]) > 0 else 0.0
        for r in file_results
    )

    print("\n" + "=" * 90)
    print(f"ANIME SUMMARY: {anime_name}")
    print("=" * 90)
    print(f"Files benchmarked:  {len(file_results)}")
    print(f"Average update:     {avg_update:.2f} ms")
    print(f"Best observed:      {best_update:.2f} ms")
    print(f"Worst observed:     {worst_update:.2f} ms")
    print(f"Average create_text:{avg_create_text:.0f}")
    print(f"Average layout hit: {avg_layout_hit:.1f}%")
    print("-" * 90)
    for r in file_results:
        print(
            f"{r['srt_path'].name:<55} "
            f"avg={r['avg_update_ms']:6.2f} ms  "
            f"best={r['best_update_ms']:6.2f} ms  "
            f"worst={r['worst_update_ms']:6.2f} ms"
        )


def main():
    configure_stdout_utf8()
    random.seed(RANDOM_SEED)
    args = parse_args()
    apply_cli_options(args)

    if args.current_config:
        srt_path = current_config_subtitle_file()
        print("Benchmark current config file:", srt_path)
        file_result = benchmark_one_file(srt_path)
        print_file_result("current-config", file_result)
        return

    if args.file:
        srt_path = Path(args.file)
        if not srt_path.is_file():
            raise FileNotFoundError(f"Subtitle file not found: {srt_path}")
        print("Benchmark file:", srt_path)
        file_result = benchmark_one_file(srt_path)
        print_file_result(srt_path.parent.name or "(file)", file_result)
        return

    if not SUBS_ROOT.exists():
        raise FileNotFoundError(f"Could not find subs folder: {SUBS_ROOT}")

    anime_groups = discover_anime_groups(SUBS_ROOT)
    if not anime_groups:
        raise RuntimeError(f"No SRT files found under {SUBS_ROOT}")

    print("Benchmark root:", SUBS_ROOT)
    print()
    print("=== MULTI-ANIME SLIDER BENCHMARK ===")
    print()
    print(f"Runs per file:       {BENCHMARK_RUNS}")
    print(f"Warmup runs:         {WARMUP_RUNS}")
    print(f"Slider steps/run:    {SLIDER_STEPS}")
    print(f"Reset caches/run:    {RESET_CACHE_EACH_RUN}")
    print(f"Max files/anime:     {MAX_FILES_PER_ANIME if MAX_FILES_PER_ANIME > 0 else 'all'}")
    print()

    all_results = []
    anime_summaries = []

    for anime_name, files in anime_groups.items():
        selected_files = select_sample_files(files, MAX_FILES_PER_ANIME)
        if not selected_files:
            continue

        print("\n" + "#" * 90)
        print(f"ANIME: {anime_name}")
        print(f"Files found: {len(files)} | Files benchmarked: {len(selected_files)}")
        print("#" * 90)

        anime_file_results = []
        for srt_path in selected_files:
            print(f"\nUsing SRT: {srt_path}")
            file_result = benchmark_one_file(srt_path)
            anime_file_results.append(file_result)
            all_results.append((anime_name, file_result))
            print_file_result(anime_name, file_result)

        print_anime_summary(anime_name, anime_file_results)

        anime_summaries.append({
            "anime": anime_name,
            "file_count": len(anime_file_results),
            "avg_update_ms": statistics.mean(r["avg_update_ms"] for r in anime_file_results),
            "best_update_ms": min(r["best_update_ms"] for r in anime_file_results),
            "worst_update_ms": max(r["worst_update_ms"] for r in anime_file_results),
            "avg_create_text": statistics.mean(r["stats"]["canvas_create_text_calls"] for r in anime_file_results),
        })

    if not all_results:
        raise RuntimeError("No benchmark results were produced")

    overall_avg = statistics.mean(item[1]["avg_update_ms"] for item in all_results)
    overall_best = min(item[1]["best_update_ms"] for item in all_results)
    overall_worst = max(item[1]["worst_update_ms"] for item in all_results)
    overall_total_files = len(all_results)

    print("\n" + "=" * 90)
    print("OVERALL SUMMARY")
    print("=" * 90)
    print(f"Files benchmarked:   {overall_total_files}")
    print(f"Average update:      {overall_avg:.2f} ms")
    print(f"Best observed:       {overall_best:.2f} ms")
    print(f"Worst observed:      {overall_worst:.2f} ms")
    print("=" * 90)

    print("\nANIME AVERAGES")
    print("-" * 90)
    for item in sorted(anime_summaries, key=lambda x: x["anime"].lower()):
        print(
            f"{item['anime']:<30} "
            f"files={item['file_count']:<3d} "
            f"avg={item['avg_update_ms']:6.2f} ms "
            f"best={item['best_update_ms']:6.2f} ms "
            f"worst={item['worst_update_ms']:6.2f} ms "
            f"create_text={item['avg_create_text']:.0f}"
        )


if __name__ == "__main__":
    main()
