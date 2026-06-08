import sys
from pathlib import Path
import os
import time
import tkinter as tk
import random
import types
import collections
import statistics

root_path = Path.cwd()
sys.path.insert(0, str(root_path / "SubtitlePlayer"))

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.subtitle_overlay import SubtitleOverlayUI
from controller.controller import SubtitleController


# -----------------------------
# Benchmark settings
# -----------------------------
BENCHMARK_RUNS = 20       # measured runs
WARMUP_RUNS = 1           # ignored warmup runs
SLIDER_STEPS = 100         # number of simulated slider positions per run
RESET_CACHE_EACH_RUN = False  # set True for cold-cache benchmarking


def build_test_environment():
    config = ConfigManager(str(root_path / "config.json"))

    srt_path = config.get("DEBUGGING_SRT_FILE") or config.get("LAST_LOCAL_SRT_FILE")
    if not srt_path or not os.path.exists(srt_path):
        for p in root_path.rglob("*.srt"):
            srt_path = str(p)
            break
    if not srt_path or not os.path.exists(srt_path):
        raise FileNotFoundError("Could not find an SRT to profile")

    print("Using SRT:", srt_path)
    config.config["LAST_LOCAL_SRT_FILE"] = srt_path

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
    controller.settings = types.SimpleNamespace(_last_offset_value=0.0)
    controller.overlay = overlay
    controller.current_time = 0.0
    controller.total_duration = manager.get_total_duration()
    controller.subtitle_timeout_job = None
    controller.last_rendered_index = None
    controller.last_subtitle_text = ""
    controller.last_subtitle_raw = ""
    controller.subtitle_deleted = False
    controller.slider_dragging = False
    controller.entry_editing = False
    controller.config = config
    controller._pending_seek_delta = 0.0
    controller.hide_subtitles_ms = config.get("SUBTITLE_TIMEOUT_MS")
    controller._hide_subtitles_temporarily = lambda: None
    controller._reset_canvas = lambda: renderer.canvas.delete("all")

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

    def wrapped_render_subtitle(top, bottom, overlay_obj):
        t0 = time.perf_counter()
        result = orig_render_subtitle(top, bottom, overlay_obj)
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
            if isinstance(manager._ruby_stats[key], (int, float)):
                manager._ruby_stats[key] = 0 if isinstance(manager._ruby_stats[key], int) else 0.0


def run_slider_simulation(controller, manager, root, steps=50):
    scrub_times = []
    start_value = manager.display_data[0][1]
    step = controller.total_duration / max(1, steps)

    for i in range(steps):
        controller.current_time = start_value + i * step
        controller.subtitle_timeout_job = None
        controller.last_rendered_index = None

        t0 = time.perf_counter()
        controller._update_subtitle_display(force=True)
        scrub_times.append(time.perf_counter() - t0)

        if controller.subtitle_timeout_job:
            root.after_cancel(controller.subtitle_timeout_job)
            controller.subtitle_timeout_job = None

    avg_ms = sum(scrub_times) / len(scrub_times) * 1000.0
    min_ms = min(scrub_times) * 1000.0
    max_ms = max(scrub_times) * 1000.0
    total_ms = sum(scrub_times) * 1000.0

    return {
        "avg_ms": avg_ms,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "total_ms": total_ms,
        "samples": scrub_times,
    }


def main():
    root, config, manager, overlay, renderer, controller = build_test_environment()

    stats = collections.Counter()
    times = collections.defaultdict(float)
    install_external_timers(manager, renderer, stats, times)

    # Warmup runs are not included in the final average.
    for _ in range(WARMUP_RUNS):
        if RESET_CACHE_EACH_RUN:
            reset_renderer_caches(renderer, manager)
        _ = run_slider_simulation(controller, manager, root, steps=SLIDER_STEPS)

    run_avgs = []
    run_mins = []
    run_maxs = []
    run_totals = []

    all_data = getattr(manager, "display_data", [])
    subtitle_count = len(all_data)

    sample_indices = []
    if subtitle_count > 0:
        worst_by_segments = max(
            range(subtitle_count),
            key=lambda i: len(list((all_data[i][2] or []) + (all_data[i][3] or [])))
        )
        worst_by_lines = max(
            range(subtitle_count),
            key=lambda i: (
                len(renderer._wrap_segments(all_data[i][2], overlay.max_w)) if all_data[i][2] else 0
            ) + (
                len(renderer._wrap_segments(all_data[i][3], overlay.max_w)) if all_data[i][3] else 0
            )
        )

        sample_indices.extend([
            0,
            subtitle_count // 4,
            subtitle_count // 2,
            3 * subtitle_count // 4,
            subtitle_count - 1,
            worst_by_segments,
            worst_by_lines,
        ])
        sample_indices = list(dict.fromkeys([i for i in sample_indices if 0 <= i < subtitle_count]))
        sample_indices.extend(random.sample(range(subtitle_count), min(10, subtitle_count)))
        sample_indices = sample_indices[:20]

    # Prime the cache a little so the benchmark reflects actual scrolling, not first-load cost.
    for idx in sample_indices[:5]:
        controller.last_rendered_index = None
        controller.last_subtitle_text = ""
        controller.current_time = manager.display_data[idx][1] + 0.01
        controller.subtitle_timeout_job = None
        controller._update_subtitle_display(force=True)
        if controller.subtitle_timeout_job:
            root.after_cancel(controller.subtitle_timeout_job)
            controller.subtitle_timeout_job = None

    print("\n=== MULTI-RUN SLIDER BENCHMARK ===\n")
    print(f"Runs: {BENCHMARK_RUNS}")
    print(f"Warmup runs: {WARMUP_RUNS}")
    print(f"Slider steps per run: {SLIDER_STEPS}")
    print(f"Reset caches each run: {RESET_CACHE_EACH_RUN}")
    print()

    for run_idx in range(BENCHMARK_RUNS):
        if RESET_CACHE_EACH_RUN:
            reset_renderer_caches(renderer, manager)

        result = run_slider_simulation(controller, manager, root, steps=SLIDER_STEPS)

        run_avgs.append(result["avg_ms"])
        run_mins.append(result["min_ms"])
        run_maxs.append(result["max_ms"])
        run_totals.append(result["total_ms"])

        print(
            f"Run {run_idx + 1:2d}: "
            f"avg={result['avg_ms']:7.2f} ms  "
            f"min={result['min_ms']:7.2f} ms  "
            f"max={result['max_ms']:7.2f} ms  "
            f"total={result['total_ms']:8.2f} ms"
        )

    overall_avg = statistics.mean(run_avgs)
    overall_min = min(run_mins)
    overall_max = max(run_maxs)
    overall_total = sum(run_totals)
    overall_stdev = statistics.stdev(run_avgs) if len(run_avgs) > 1 else 0.0
    throughput = 1000.0 / overall_avg if overall_avg > 0 else 0.0

    print("\n=== FINAL RESULTS ===\n")
    print(f"Average update time: {overall_avg:.2f} ms")
    print(f"Std dev of run avgs: {overall_stdev:.2f} ms")
    print(f"Best observed update: {overall_min:.2f} ms")
    print(f"Worst observed update: {overall_max:.2f} ms")
    print(f"Equivalent throughput: {throughput:.1f} updates/sec")
    print(f"Total time across runs: {overall_total:.2f} ms")

    print("\n=== FUNCTION BREAKDOWN ===\n")

    def print_metric(name, time_key, call_key):
        total_ms = times[time_key] * 1000.0
        calls = stats[call_key]
        avg_ms = total_ms / calls if calls else 0.0
        print(f"{name:<28} {calls:>10} {total_ms:>14.1f} {avg_ms:>14.3f}")

    print(f"{'Function':<28} {'Calls':>10} {'Total ms':>14} {'Avg ms':>14}")
    print("-" * 70)
    print_metric("render_subtitle", "render_subtitle", "render_subtitle_calls")
    print_metric("_draw_outlined_text", "draw_outlined_text", "draw_outlined_text_calls")
    print_metric("_wrap_segments", "wrap_segments", "wrap_segments_calls")
    print_metric("_measure_text", "font_measure", "font_measure_calls")
    print_metric("ensure_auto_ruby", "ensure_auto_ruby_for_index", "ensure_auto_ruby_calls")
    print_metric("canvas.delete(all)", "canvas_delete_all", "canvas_delete_all_calls")
    print_metric("canvas.create_text", "canvas_create_text", "canvas_create_text_calls")

    print("\n=== CACHE STATE ===\n")
    print(f"Layout cache hits: {renderer._layout_cache_hits}")
    print(f"Layout cache misses: {renderer._layout_cache_misses}")
    if renderer._layout_cache_hits + renderer._layout_cache_misses > 0:
        hit_rate = renderer._layout_cache_hits / (renderer._layout_cache_hits + renderer._layout_cache_misses)
        print(f"Layout cache hit rate: {hit_rate * 100:.1f}%")
    print(f"Measure cache entries: {len(renderer._measure_cache)}")
    print(f"Wrap cache entries: {len(renderer._wrap_cache)}")
    print(f"Layout cache entries: {len(renderer._layout_cache)}")

    print("\n=== AUTO-RUBY STATS ===\n")
    ruby_stats = getattr(manager, "_ruby_stats", {})
    for key in ("cache_hits", "cache_misses", "generator_calls"):
        if key in ruby_stats:
            print(f"{key}: {ruby_stats[key]}")
    if "generator_time" in ruby_stats:
        print(f"generator_time: {ruby_stats['generator_time'] * 1000:.1f} ms")

    root.destroy()


if __name__ == "__main__":
    main()