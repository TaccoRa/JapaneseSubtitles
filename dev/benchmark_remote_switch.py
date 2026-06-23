from __future__ import annotations

import argparse
import collections
import os
import random
import statistics
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import tkinter as tk

ROOT = Path.cwd()
SUBTITLE_PLAYER_DIR = ROOT / "SubtitlePlayer"
if str(SUBTITLE_PLAYER_DIR) not in sys.path:
    sys.path.insert(0, str(SUBTITLE_PLAYER_DIR))

from model.config_manager import ConfigManager
from model.subtitle_manager import SubtitleManager
from model.renderer import SubtitleRenderer
from view.subtitle_overlay import SubtitleOverlayUI
from controller.controller import SubtitleController
from controller.episode_controller import EpisodeController


DEFAULT_RUNS = 10
DEFAULT_WARMUP_RUNS = 1
DEFAULT_SWITCH_STEPS = 20
DEFAULT_TARGETS_PER_GROUP = 5
DEFAULT_RESET_CACHES_EACH_RUN = False
DEFAULT_REBUILD_REMOTE_MAPS_EACH_RUN = False
DEFAULT_REFRESH_LOCAL_INDEX_EACH_RUN = False
DEFAULT_RANDOM_SEED = 1337
DEFAULT_TARGET_MODE = "spread"
HEARTBEAT_INTERVAL_MS = 5


@dataclass(frozen=True)
class EpisodeTarget:
    label: str
    raw_value: Optional[int]
    season: Optional[int]
    episode: Optional[int]
    global_index: Optional[int]
    source_name: str


class BenchmarkSettingsStub:
    def __init__(self, root: tk.Tk, initial_episode_text: str = "") -> None:
        self.root = root
        self.episode_var = tk.StringVar(master=root, value=initial_episode_text)
        self.control_time_str = tk.StringVar(master=root, value="")
        self._last_offset_value = 0.0

    def set_total_duration(self, value: float) -> None:
        self.total_duration = float(value)

    def set_episode_nav_state(self, can_dec=True, can_inc=True, is_movie=False) -> None:
        self._episode_nav_state = (bool(can_dec), bool(can_inc), bool(is_movie))

    def set_episode_values(self, values) -> None:
        self._episode_values = list(values) if values is not None else []


class PlaybackStub:
    def __init__(self) -> None:
        self.current_time = 0.0
        self.controller = None

    def set_controller(self, controller) -> None:
        self.controller = controller

    def set_current_time(self, t: float) -> None:
        self.current_time = float(t)

    def toggle_play(self) -> None:
        return None

    def go_back(self) -> None:
        return None

    def go_forward(self) -> None:
        return None

    def update_loop(self):
        return None

    def schedule_update(self):
        return None


class TkHeartbeat:
    """Measure how long Tk callbacks are delayed while synchronous work runs."""

    def __init__(self, root: tk.Tk, interval_ms: int = HEARTBEAT_INTERVAL_MS) -> None:
        self.root = root
        self.interval_ms = max(1, int(interval_ms))
        self.max_delay_ms = 0.0
        self.samples = 0
        self._running = False
        self._job = None
        self._expected = 0.0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        now = time.perf_counter()
        self._expected = now + self.interval_ms / 1000.0
        self._job = self.root.after(self.interval_ms, self._tick)

    def stop(self) -> None:
        self._running = False
        if self._job is not None:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
        self._job = None

    def reset(self) -> None:
        self.max_delay_ms = 0.0
        self.samples = 0
        self._expected = time.perf_counter() + self.interval_ms / 1000.0

    def _tick(self) -> None:
        if not self._running:
            return
        now = time.perf_counter()
        delay_ms = max(0.0, (now - self._expected) * 1000.0)
        self.max_delay_ms = max(self.max_delay_ms, delay_ms)
        self.samples += 1
        self._expected = now + self.interval_ms / 1000.0
        self._job = self.root.after(self.interval_ms, self._tick)


def clone_config_from_path(config_path: Path) -> ConfigManager:
    config = ConfigManager(str(config_path))

    def set_in_memory(key, value):
        if key not in config.config:
            config._key_order.append(key)
        config.config[key] = value

    def set_many_in_memory(updates):
        if not isinstance(updates, dict):
            return
        for key, value in updates.items():
            set_in_memory(key, value)

    config.set = set_in_memory
    config.set_many = set_many_in_memory
    return config


def group_remote_records(remote_map: Dict[int, dict]) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = collections.defaultdict(list)
    for _g, rec in remote_map.items():
        path = str(rec.get("path") or rec.get("name") or "")
        parts = path.replace("\\", "/").split("/")
        if len(parts) >= 3 and parts[0] == "subtitles":
            group_name = parts[1]
        elif len(parts) >= 2:
            group_name = parts[0]
        else:
            group_name = "remote"
        grouped[group_name].append(rec)
    return dict(sorted(grouped.items(), key=lambda kv: kv[0].casefold()))


def choose_evenly_spaced(items: Sequence, max_count: int) -> List:
    items = list(items)
    if max_count <= 0 or len(items) <= max_count:
        return items
    if max_count == 1:
        return [items[len(items) // 2]]
    idxs = [round(i * (len(items) - 1) / (max_count - 1)) for i in range(max_count)]
    seen = set()
    out = []
    for i in idxs:
        if i not in seen:
            seen.add(i)
            out.append(items[i])
    return out


def pump_tk_for(root: tk.Tk, duration_ms: int) -> None:
    deadline = time.perf_counter() + max(0, int(duration_ms)) / 1000.0
    while time.perf_counter() < deadline:
        try:
            root.update()
        except Exception:
            return
        time.sleep(0.005)


def build_controller_environment(config: ConfigManager, manager: SubtitleManager):
    root = tk.Tk()
    root.withdraw()
    root.update_idletasks()

    overlay_geometry = manager.calculate_geometry_for_longest_lines(5)
    cleaned_subs = [item[0] for item in getattr(manager, "display_data", [])]

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
    controller.settings = BenchmarkSettingsStub(root, initial_episode_text="")
    controller.overlay = overlay
    controller.popup = types.SimpleNamespace()
    controller.config = config
    controller.playback = PlaybackStub()
    controller.playback.set_controller(controller)
    controller.episode_controller = EpisodeController(controller)
    controller.current_time = 0.0
    controller.total_duration = float(manager.get_total_duration())
    controller.subtitle_timeout_job = None
    controller.last_rendered_index = None
    controller.last_subtitle_text = ""
    controller.last_subtitle_raw = ""
    controller.subtitle_deleted = False
    controller.slider_dragging = False
    controller.entry_editing = False
    controller.hide_subtitles_ms = int(config.get("SUBTITLE_TIMEOUT_MS") or 0)
    controller.default_start_time = float(config.get("DEFAULT_START_TIME") or 0.0)
    controller._pending_seek_delta = 0.0
    controller._shutting_down = False
    controller._ocr_job = None
    controller._ocr_thread = None
    controller._ocr_generation = 0
    controller._ocr_pending_time = None
    controller._ocr_sync_generation = 0
    controller._ocr_sync_thread = None
    controller._anki_wait_window = None
    controller._anki_wait_status_var = None
    controller._anki_wait_thread = None
    controller._pending_anki_payload = None
    controller._startup_resume_play = False
    controller._slider_render_job = None
    controller._slider_pending_value = None
    controller._defer_auto_ruby_once = False
    controller._auto_ruby_generation_id = 0
    controller._auto_ruby_thread = None
    controller.ocr_controller = types.SimpleNamespace(_schedule_ocr_time_jump=lambda *args, **kwargs: None)
    controller.sub_hidden = False

    controller._hide_subtitles_temporarily = lambda: None

    def _reset_canvas():
        renderer.canvas.delete("all")
        controller.last_rendered_index = None
        controller.last_subtitle_text = ""
        controller.subtitle_deleted = True

    controller._reset_canvas = _reset_canvas
    controller._schedule_ocr_time_jump = lambda *args, **kwargs: None

    def _update_episode_nav_controls():
        try:
            can_dec, can_inc, is_movie = manager.get_episode_nav_state()
        except Exception:
            can_dec, can_inc, is_movie = True, True, False
        try:
            controller.settings.set_episode_nav_state(can_dec=can_dec, can_inc=can_inc, is_movie=is_movie)
        except Exception:
            pass
        try:
            values = manager.get_episode_dropdown_values()
            controller.settings.set_episode_values(values)
        except Exception:
            pass

    controller._update_episode_nav_controls = _update_episode_nav_controls
    controller._get_display_start_times = SubtitleController._get_display_start_times.__get__(controller, SubtitleController)
    controller._segments_to_copy_text = SubtitleController._segments_to_copy_text
    controller._after_episode_change = controller.episode_controller._after_episode_change
    controller.update_max_width = controller.episode_controller.update_max_width

    try:
        controller.settings.episode_var.set(str(manager.get_current_episode() or ""))
    except Exception:
        pass

    return root, overlay, renderer, controller


def install_timers(manager: SubtitleManager, renderer: SubtitleRenderer, controller: SubtitleController, stats, times):
    orig_after_episode_change = controller._after_episode_change
    episode_controller = getattr(controller, "episode_controller", None)
    orig_update_max_width = getattr(episode_controller, "update_max_width", controller.update_max_width)
    orig_render_subtitle = renderer.render_subtitle
    orig_draw_text = renderer._draw_outlined_text
    orig_wrap = renderer._wrap_segments
    orig_measure = renderer._measure_text
    orig_auto_ruby = manager.ensure_auto_ruby_for_index
    orig_load_local_record = getattr(manager, "_load_local_record", None)
    orig_load_local_and_process = getattr(manager, "_load_local_and_process", None)
    orig_set_display_data = getattr(manager, "set_subtitle_display_data", None)
    orig_change_episode_remote = getattr(manager, "change_episode_remote", None)
    orig_load_subtitles = getattr(manager, "load_subtitles", None)
    orig_download_file = getattr(manager, "_download_file", None)
    orig_schedule_prefetch = getattr(manager, "_schedule_prefetch_window", None)
    orig_download_window = getattr(manager, "download_window_around_global", None)
    orig_create_remote_map = getattr(manager, "_create_remote_episode_map_per_season", None)
    orig_calculate_geometry = getattr(manager, "calculate_geometry", None)
    orig_delete = renderer.canvas.delete
    orig_create_text = renderer.canvas.create_text
    orig_build_remote_maps = getattr(manager, "build_remote_episode_maps", None)
    orig_update_local_srt_files = getattr(manager, "update_local_srt_files", None)

    def wrapped_after_episode_change():
        t0 = time.perf_counter()
        try:
            return orig_after_episode_change()
        finally:
            times["controller._after_episode_change"] += time.perf_counter() - t0
            stats["controller._after_episode_change_calls"] += 1

    def wrapped_update_max_width():
        t0 = time.perf_counter()
        try:
            return orig_update_max_width()
        finally:
            times["controller.update_max_width"] += time.perf_counter() - t0
            stats["controller.update_max_width_calls"] += 1

    def wrapped_render_subtitle(top, bottom, overlay_obj):
        t0 = time.perf_counter()
        result = orig_render_subtitle(top, bottom, overlay_obj)
        times["renderer.render_subtitle"] += time.perf_counter() - t0
        stats["renderer.render_subtitle_calls"] += 1
        return result

    def wrapped_draw_outlined_text(canvas, x, y, text, font, fill, outline, thickness, anchor="center", tags=()):
        stats["renderer._draw_outlined_text_calls"] += 1
        t0 = time.perf_counter()
        result = orig_draw_text(canvas, x, y, text, font, fill, outline, thickness, anchor=anchor, tags=tags)
        times["renderer._draw_outlined_text"] += time.perf_counter() - t0
        return result

    def wrapped_wrap_segments(*args, **kwargs):
        t0 = time.perf_counter()
        result = orig_wrap(*args, **kwargs)
        times["renderer._wrap_segments"] += time.perf_counter() - t0
        stats["renderer._wrap_segments_calls"] += 1
        return result

    def wrapped_measure_text(font_obj, text):
        t0 = time.perf_counter()
        result = orig_measure(font_obj, text)
        times["renderer._measure_text"] += time.perf_counter() - t0
        stats["renderer._measure_text_calls"] += 1
        return result

    def wrapped_auto_ruby(idx):
        t0 = time.perf_counter()
        result = orig_auto_ruby(idx)
        times["manager.ensure_auto_ruby_for_index"] += time.perf_counter() - t0
        stats["manager.ensure_auto_ruby_for_index_calls"] += 1
        return result

    def wrapped_load_local_record(rec):
        t0 = time.perf_counter()
        result = orig_load_local_record(rec)
        times["manager._load_local_record"] += time.perf_counter() - t0
        stats["manager._load_local_record_calls"] += 1
        return result

    def wrapped_load_local_and_process(path):
        t0 = time.perf_counter()
        result = orig_load_local_and_process(path)
        times["manager._load_local_and_process"] += time.perf_counter() - t0
        stats["manager._load_local_and_process_calls"] += 1
        return result

    def wrapped_set_display_data(path):
        t0 = time.perf_counter()
        result = orig_set_display_data(path)
        times["manager.set_subtitle_display_data"] += time.perf_counter() - t0
        stats["manager.set_subtitle_display_data_calls"] += 1
        return result

    def wrapped_change_episode_remote(action, raw=None, target_season=None):
        t0 = time.perf_counter()
        result = orig_change_episode_remote(action, raw, target_season)
        times["manager.change_episode_remote"] += time.perf_counter() - t0
        stats["manager.change_episode_remote_calls"] += 1
        return result

    def wrapped_load_subtitles(path):
        t0 = time.perf_counter()
        result = orig_load_subtitles(path)
        times["manager.load_subtitles"] += time.perf_counter() - t0
        stats["manager.load_subtitles_calls"] += 1
        return result

    def wrapped_download_file(raw_url, local_path, session=None):
        existed_before = os.path.exists(local_path)
        if existed_before:
            stats["manager._download_file_cache_hit_calls"] += 1
        else:
            stats["manager._download_file_cache_miss_calls"] += 1
        t0 = time.perf_counter()
        result = orig_download_file(raw_url, local_path, session=session)
        times["manager._download_file"] += time.perf_counter() - t0
        stats["manager._download_file_calls"] += 1
        if not existed_before and os.path.exists(local_path):
            stats["manager._download_file_downloaded_calls"] += 1
        return result

    def wrapped_schedule_prefetch(center_global):
        t0 = time.perf_counter()
        result = orig_schedule_prefetch(center_global)
        times["manager._schedule_prefetch_window"] += time.perf_counter() - t0
        stats["manager._schedule_prefetch_window_calls"] += 1
        return result

    def wrapped_download_window(center_global, window, async_download=True):
        t0 = time.perf_counter()
        result = orig_download_window(center_global, window, async_download=async_download)
        times["manager.download_window_around_global"] += time.perf_counter() - t0
        stats["manager.download_window_around_global_calls"] += 1
        return result

    def wrapped_create_remote_map():
        t0 = time.perf_counter()
        result = orig_create_remote_map()
        times["manager._create_remote_episode_map_per_season"] += time.perf_counter() - t0
        stats["manager._create_remote_episode_map_per_season_calls"] += 1
        return result

    def wrapped_calculate_geometry():
        t0 = time.perf_counter()
        result = orig_calculate_geometry()
        times["manager.calculate_geometry"] += time.perf_counter() - t0
        stats["manager.calculate_geometry_calls"] += 1
        return result

    def wrapped_delete(tag_or_id):
        if tag_or_id == "all":
            t0 = time.perf_counter()
            result = orig_delete(tag_or_id)
            times["canvas.delete(all)"] += time.perf_counter() - t0
            stats["canvas.delete(all)_calls"] += 1
            return result
        return orig_delete(tag_or_id)

    def wrapped_create_text(*args, **kwargs):
        stats["canvas.create_text_calls"] += 1
        t0 = time.perf_counter()
        result = orig_create_text(*args, **kwargs)
        times["canvas.create_text"] += time.perf_counter() - t0
        return result

    controller._after_episode_change = wrapped_after_episode_change
    controller.update_max_width = wrapped_update_max_width
    if episode_controller is not None:
        object.__setattr__(episode_controller, "update_max_width", wrapped_update_max_width)
    renderer.render_subtitle = wrapped_render_subtitle
    renderer._draw_outlined_text = wrapped_draw_outlined_text
    renderer._wrap_segments = wrapped_wrap_segments
    renderer._measure_text = wrapped_measure_text
    manager.ensure_auto_ruby_for_index = wrapped_auto_ruby

    if orig_load_local_record is not None:
        manager._load_local_record = wrapped_load_local_record
    if orig_load_local_and_process is not None:
        manager._load_local_and_process = wrapped_load_local_and_process
    if orig_set_display_data is not None:
        manager.set_subtitle_display_data = wrapped_set_display_data
    if orig_change_episode_remote is not None:
        manager.change_episode_remote = wrapped_change_episode_remote
    if orig_load_subtitles is not None:
        manager.load_subtitles = wrapped_load_subtitles
    if orig_download_file is not None:
        manager._download_file = wrapped_download_file
    if orig_schedule_prefetch is not None:
        manager._schedule_prefetch_window = wrapped_schedule_prefetch
    if orig_download_window is not None:
        manager.download_window_around_global = wrapped_download_window
    if orig_create_remote_map is not None:
        manager._create_remote_episode_map_per_season = wrapped_create_remote_map
    if orig_calculate_geometry is not None:
        manager.calculate_geometry = wrapped_calculate_geometry
    if orig_build_remote_maps is not None:
        manager.build_remote_episode_maps = orig_build_remote_maps
    if orig_update_local_srt_files is not None:
        manager.update_local_srt_files = orig_update_local_srt_files

    renderer.canvas.delete = wrapped_delete
    renderer.canvas.create_text = wrapped_create_text


def snapshot_metrics(stats: collections.Counter, times: Dict[str, float]):
    return {"stats": collections.Counter(stats), "times": dict(times)}


def diff_metrics(after, before):
    stats = collections.Counter(after["stats"])
    stats.subtract(before["stats"])
    stats = collections.Counter({k: v for k, v in stats.items() if v})

    times = {}
    keys = set(after["times"]) | set(before["times"])
    for key in keys:
        times[key] = float(after["times"].get(key, 0.0)) - float(before["times"].get(key, 0.0))
    return stats, times


def reset_caches(renderer: SubtitleRenderer, manager: SubtitleManager) -> None:
    for attr in ("_measure_cache", "_outline_offset_cache", "_split_cache", "_wrap_cache", "_layout_cache"):
        cache = getattr(renderer, attr, None)
        if isinstance(cache, dict):
            cache.clear()
    renderer._last_overlay_width = None
    renderer._layout_cache_hits = 0
    renderer._layout_cache_misses = 0

    try:
        manager._auto_ruby_cache.clear()
    except Exception:
        pass


def get_remote_target_cache_path(manager: SubtitleManager, target: EpisodeTarget) -> Optional[str]:
    item = None
    remote_map = getattr(manager, "remote_episode_map_global", None) or {}
    if target.global_index is not None:
        item = remote_map.get(int(target.global_index))

    if item is None and target.season is not None and target.episode is not None:
        for candidate in getattr(manager, "remote_episode_map_season", {}).get(int(target.season), []):
            if candidate.get("episode") == int(target.episode):
                item = candidate
                break

    if not item or not item.get("path"):
        return None

    try:
        season = item.get("season") or getattr(manager, "current_season", None)
        season_dir = manager._season_cache_dir(season)
        filename = manager.sanitize_filename(os.path.basename(item["path"]))
        return os.path.join(season_dir, filename)
    except Exception:
        return None


def note_target_cache_state(manager: SubtitleManager, target: EpisodeTarget, stats, prefix: str) -> None:
    path = get_remote_target_cache_path(manager, target)
    if path and os.path.exists(path):
        stats[f"{prefix}_cache_hit"] += 1
    else:
        stats[f"{prefix}_cache_miss"] += 1
    try:
        manager._ruby_stats["cache_hits"] = 0
        manager._ruby_stats["cache_misses"] = 0
        manager._ruby_stats["generator_calls"] = 0
        manager._ruby_stats["generator_time"] = 0.0
    except Exception:
        pass


def build_remote_environment(config_path: Path):
    config = clone_config_from_path(config_path)
    if not (config.get("REMOTE_FLAG") or config.get("LAST_GITHUB_URL")):
        return None

    try:
        config.config["REMOTE_FLAG"] = True
    except Exception:
        pass

    manager = SubtitleManager(config)
    if not getattr(manager, "remote_episode_map_global", None):
        try:
            manager.build_remote_episode_maps()
        except Exception:
            pass

    root, overlay, renderer, controller = build_controller_environment(config, manager)
    return root, config, manager, overlay, renderer, controller


def make_targets_from_remote_map(
    remote_map: Dict[int, dict],
    max_targets: int,
    target_mode: str = DEFAULT_TARGET_MODE,
    center_global: Optional[int] = None,
) -> List[EpisodeTarget]:
    records = list(remote_map.values())
    if not records:
        return []

    records = sorted(
        records,
        key=lambda r: (
            int(r.get("season") or 0),
            int(r.get("episode") or 0),
            int(r.get("global") or 0),
            str(r.get("name") or ""),
        )
    )
    if target_mode == "adjacent":
        if center_global is not None:
            idx = 0
            for i, rec in enumerate(records):
                if rec.get("global") == int(center_global):
                    idx = i
                    break
            start = max(0, min(idx, max(0, len(records) - max_targets)))
        else:
            start = 0
        selected = records[start:start + max_targets]
    else:
        selected = choose_evenly_spaced(records, max_targets)
    return [make_target_for_remote_record(rec) for rec in selected]


def make_target_for_remote_record(rec: dict) -> EpisodeTarget:
    season = rec.get("season")
    episode = rec.get("episode")
    global_index = rec.get("global")

    if episode is not None:
        raw_value = int(episode)
    elif global_index is not None:
        raw_value = int(global_index)
    else:
        raw_value = None

    if season is not None and episode is not None:
        label = f"S{int(season)}E{int(episode)}"
    elif global_index is not None:
        label = str(int(global_index))
    elif episode is not None:
        label = str(int(episode))
    else:
        label = str(rec.get("name") or rec.get("path") or "remote")

    return EpisodeTarget(
        label=label,
        raw_value=raw_value,
        season=season,
        episode=episode,
        global_index=global_index,
        source_name=str(rec.get("name") or rec.get("path") or ""),
    )


def switch_to_target(manager: SubtitleManager, controller: SubtitleController, target: EpisodeTarget):
    raw = target.raw_value
    season_hint = target.season if target.season is not None else None

    if raw is None:
        if target.global_index is not None:
            raw = int(target.global_index)
            season_hint = None
        elif target.episode is not None:
            raw = int(target.episode)
        else:
            raise ValueError(f"Target has no usable episode value: {target}")

    controller.settings.episode_var.set(target.label)
    return manager.change_episode("set", int(raw), season_hint)


def run_remote_sequence(
    controller: SubtitleController,
    manager: SubtitleManager,
    root: tk.Tk,
    targets: List[EpisodeTarget],
    steps: int,
    warmup: int,
    rebuild_remote_maps: bool = False,
    refresh_local_index: bool = False,
    reset_cache_each_run: bool = False,
    heartbeat: Optional[TkHeartbeat] = None,
    preload_wait_ms: int = 0,
) -> Dict[str, object]:
    if not targets:
        raise RuntimeError("No remote targets available for benchmark")

    sequence = targets[:]
    if len(sequence) < 2:
        sequence = sequence * 2

    # Warmup.
    for i in range(min(warmup, max(1, len(sequence) - 1))):
        target = sequence[(i + 1) % len(sequence)]
        try:
            switch_to_target(manager, controller, target)
            controller._after_episode_change()
        except Exception:
            pass
        if getattr(controller, "subtitle_timeout_job", None):
            try:
                root.after_cancel(controller.subtitle_timeout_job)
            except Exception:
                pass
            controller.subtitle_timeout_job = None

    before = snapshot_metrics(run_remote_sequence.stats, run_remote_sequence.times)
    if heartbeat is not None:
        heartbeat.reset()

    run_times: List[float] = []
    sample_rows: List[Dict[str, object]] = []
    steps = max(1, int(steps))

    for i in range(steps):
        if preload_wait_ms > 0:
            pump_tk_for(root, preload_wait_ms)

        if rebuild_remote_maps:
            try:
                manager.remote_episode_map_global = {}
                manager.remote_episode_map_season = {}
                manager.all_results_items = []
                manager.build_remote_episode_maps()
            except Exception:
                pass

        if refresh_local_index:
            try:
                manager.update_local_srt_files()
            except Exception:
                pass

        if reset_cache_each_run:
            reset_caches(controller.renderer, manager)

        target = sequence[(i + 1) % len(sequence)]
        path_before = get_remote_target_cache_path(manager, target)
        cached_before = bool(path_before and os.path.exists(path_before))
        note_target_cache_state(manager, target, run_remote_sequence.stats, "target_before")
        t0 = time.perf_counter()
        try:
            switch_to_target(manager, controller, target)
            controller._after_episode_change()
        except Exception:
            pass
        elapsed = time.perf_counter() - t0
        run_times.append(elapsed)
        path_after = get_remote_target_cache_path(manager, target)
        cached_after = bool(path_after and os.path.exists(path_after))
        note_target_cache_state(manager, target, run_remote_sequence.stats, "target_after")
        sample_rows.append({
            "target": target.label,
            "ms": elapsed * 1000.0,
            "cached_before": cached_before,
            "cached_after": cached_after,
        })

        try:
            root.update()
        except Exception:
            pass

        if getattr(controller, "subtitle_timeout_job", None):
            try:
                root.after_cancel(controller.subtitle_timeout_job)
            except Exception:
                pass
            controller.subtitle_timeout_job = None

    after = snapshot_metrics(run_remote_sequence.stats, run_remote_sequence.times)
    stats_delta, times_delta = diff_metrics(after, before)

    return {
        "avg_ms": statistics.mean(run_times) * 1000.0,
        "min_ms": min(run_times) * 1000.0,
        "max_ms": max(run_times) * 1000.0,
        "total_ms": sum(run_times) * 1000.0,
        "samples": run_times,
        "sample_rows": sample_rows,
        "heartbeat_max_delay_ms": heartbeat.max_delay_ms if heartbeat is not None else 0.0,
        "heartbeat_samples": heartbeat.samples if heartbeat is not None else 0,
        "stats": stats_delta,
        "times": times_delta,
    }


run_remote_sequence.stats = collections.Counter()
run_remote_sequence.times = collections.defaultdict(float)


def print_breakdown(title: str, stats: collections.Counter, times: Dict[str, float]) -> None:
    print(f"\n=== {title} FUNCTION BREAKDOWN ===\n")
    print(f"{'Function':<36} {'Calls':>10} {'Total ms':>14} {'Avg ms':>14}")
    print("-" * 80)

    rows = [
        ("manager.change_episode_remote", "manager.change_episode_remote_calls"),
        ("manager._load_local_record", "manager._load_local_record_calls"),
        ("manager._load_local_and_process", "manager._load_local_and_process_calls"),
        ("manager.set_subtitle_display_data", "manager.set_subtitle_display_data_calls"),
        ("manager.load_subtitles", "manager.load_subtitles_calls"),
        ("controller._after_episode_change", "controller._after_episode_change_calls"),
        ("controller.update_max_width", "controller.update_max_width_calls"),
        ("manager.calculate_geometry", "manager.calculate_geometry_calls"),
        ("renderer.render_subtitle", "renderer.render_subtitle_calls"),
        ("renderer._draw_outlined_text", "renderer._draw_outlined_text_calls"),
        ("renderer._wrap_segments", "renderer._wrap_segments_calls"),
        ("renderer._measure_text", "renderer._measure_text_calls"),
        ("manager.ensure_auto_ruby_for_index", "manager.ensure_auto_ruby_for_index_calls"),
        ("manager._download_file", "manager._download_file_calls"),
        ("manager.download_window_around_global", "manager.download_window_around_global_calls"),
        ("manager._schedule_prefetch_window", "manager._schedule_prefetch_window_calls"),
        ("manager._create_remote_episode_map_per_season", "manager._create_remote_episode_map_per_season_calls"),
        ("canvas.delete(all)", "canvas.delete(all)_calls"),
        ("canvas.create_text", "canvas.create_text_calls"),
    ]
    for name, call_key in rows:
        call_count = int(stats.get(call_key, 0))
        total_ms = float(times.get(name, 0.0)) * 1000.0
        avg_ms = (total_ms / call_count) if call_count else 0.0
        print(f"{name:<36} {call_count:>10} {total_ms:>14.1f} {avg_ms:>14.3f}")


def summarize_group(results: List[Dict[str, object]]) -> Dict[str, float]:
    if not results:
        return {}
    total_create_text = sum(int(r.get("stats", {}).get("canvas.create_text_calls", 0)) for r in results)
    total_heartbeat_delay = max(float(r.get("heartbeat_max_delay_ms", 0.0)) for r in results)
    total_switches = sum(
        int(r.get("stats", {}).get("manager.change_episode_remote_calls", 0))
        for r in results
    )
    target_hits = sum(int(r.get("stats", {}).get("target_before_cache_hit", 0)) for r in results)
    target_misses = sum(int(r.get("stats", {}).get("target_before_cache_miss", 0)) for r in results)
    return {
        "avg_ms": statistics.mean(r["avg_ms"] for r in results),
        "best_ms": min(r["min_ms"] for r in results),
        "worst_ms": max(r["max_ms"] for r in results),
        "stddev_ms": statistics.stdev(r["avg_ms"] for r in results) if len(results) > 1 else 0.0,
        "avg_create_text": total_create_text / max(1, total_switches),
        "max_heartbeat_delay_ms": total_heartbeat_delay,
        "target_cache_hit_rate": (target_hits / max(1, target_hits + target_misses)) * 100.0,
    }


def benchmark_remote(config_path: Path, runs: int, warmup: int, steps: int, max_targets: int,
                     reset_cache_each_run: bool, rebuild_remote_maps_each_run: bool,
                     refresh_local_index_each_run: bool, target_mode: str, preload_wait_ms: int) -> None:
    remote_env = build_remote_environment(config_path)
    if remote_env is None:
        print("Remote benchmark skipped: no remote configuration found (REMOTE_FLAG/LAST_GITHUB_URL not set).")
        return

    root, config, manager, overlay, renderer, controller = remote_env

    try:
        if not getattr(manager, "remote_episode_map_global", None):
            manager.build_remote_episode_maps()
    except Exception:
        pass

    remote_map = getattr(manager, "remote_episode_map_global", None) or {}
    if not remote_map:
        print("Remote benchmark skipped: no remote_episode_map_global entries available.")
        try:
            root.destroy()
        except Exception:
            pass
        return

    grouped = group_remote_records(remote_map)
    random.seed(DEFAULT_RANDOM_SEED)

    print("\n" + "=" * 90)
    print("REMOTE EPISODE SWITCH BENCHMARK")
    print("=" * 90)
    print(f"Runs per group:      {runs}")
    print(f"Warmup switches:     {warmup}")
    print(f"Switches per run:    {steps}")
    print(f"Reset caches/run:    {reset_cache_each_run}")
    print(f"Rebuild maps/run:    {rebuild_remote_maps_each_run}")
    print(f"Refresh index/run:   {refresh_local_index_each_run}")
    print(f"Max targets/group:   {max_targets}")
    print(f"Target mode:         {target_mode}")
    print(f"Preload wait:        {preload_wait_ms} ms")
    print(f"Heartbeat interval:  {HEARTBEAT_INTERVAL_MS} ms")
    print()

    stats = collections.Counter()
    times = collections.defaultdict(float)
    install_timers(manager, renderer, controller, stats, times)
    run_remote_sequence.stats = stats
    run_remote_sequence.times = times
    heartbeat = TkHeartbeat(root)
    heartbeat.start()

    overall_results: List[Dict[str, object]] = []

    try:
        for group_name, items in sorted(grouped.items(), key=lambda kv: kv[0].casefold()):
            if len(items) < 2:
                print(f"\n[{group_name}] skipped (needs at least 2 remote episodes)")
                continue

            try:
                center_global = manager.get_current_global()
            except Exception:
                center_global = None
            targets = make_targets_from_remote_map(
                {i: rec for i, rec in enumerate(items)},
                max_targets,
                target_mode=target_mode,
                center_global=center_global,
            )
            if len(targets) < 2:
                print(f"\n[{group_name}] skipped (not enough usable targets)")
                continue

            print("\n" + "#" * 90)
            print(f"REMOTE GROUP: {group_name}")
            print(f"Remote items: {len(items)} | Targets benchmarked: {len(targets)}")
            print("#" * 90)

            # Prime the first target.
            try:
                switch_to_target(manager, controller, targets[0])
                controller._after_episode_change()
                if preload_wait_ms > 0:
                    pump_tk_for(root, preload_wait_ms)
            except Exception:
                pass

            if controller.subtitle_timeout_job:
                try:
                    root.after_cancel(controller.subtitle_timeout_job)
                except Exception:
                    pass
                controller.subtitle_timeout_job = None

            group_results: List[Dict[str, object]] = []
            for run_idx in range(runs):
                if reset_cache_each_run:
                    reset_caches(renderer, manager)

                result = run_remote_sequence(
                    controller=controller,
                    manager=manager,
                    root=root,
                    targets=targets,
                    steps=steps,
                    warmup=warmup,
                    rebuild_remote_maps=rebuild_remote_maps_each_run,
                    refresh_local_index=refresh_local_index_each_run,
                    reset_cache_each_run=False,
                    heartbeat=heartbeat,
                    preload_wait_ms=preload_wait_ms,
                )
                group_results.append(result)
                overall_results.append(result)

                print(
                    f"Run {run_idx + 1:2d}: "
                    f"avg={result['avg_ms']:7.2f} ms  "
                    f"min={result['min_ms']:7.2f} ms  "
                    f"max={result['max_ms']:7.2f} ms  "
                    f"total={result['total_ms']:8.2f} ms  "
                    f"tk_delay={result['heartbeat_max_delay_ms']:7.2f} ms"
                )
                for sample in result.get("sample_rows", [])[: min(3, len(result.get("sample_rows", [])))]:
                    cache_label = "cached" if sample.get("cached_before") else "missing"
                    print(f"         {sample['target']:<10} {sample['ms']:7.2f} ms  before={cache_label}")

            summary = summarize_group(group_results)
            print("\n--- GROUP SUMMARY ---")
            print(f"Average switch time: {summary['avg_ms']:.2f} ms")
            print(f"Std dev of run avgs: {summary['stddev_ms']:.2f} ms")
            print(f"Best observed switch: {summary['best_ms']:.2f} ms")
            print(f"Worst observed switch: {summary['worst_ms']:.2f} ms")
            print(f"Average create_text:  {summary['avg_create_text']:.0f}")
            print(f"Target cache hit rate: {summary['target_cache_hit_rate']:.1f}%")
            print(f"Max Tk callback delay: {summary['max_heartbeat_delay_ms']:.2f} ms")
            hit_rate = (
                (renderer._layout_cache_hits / (renderer._layout_cache_hits + renderer._layout_cache_misses) * 100.0)
                if (renderer._layout_cache_hits + renderer._layout_cache_misses) > 0 else 0.0
            )
            print(f"Layout cache hit rate: {hit_rate:.1f}%")

            print_breakdown(f"REMOTE / {group_name}", stats, times)

            print("\n--- TARGETS USED ---")
            for t in targets:
                print(f"{t.label}  ({t.source_name})")

    finally:
        try:
            heartbeat.stop()
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    if overall_results:
        print("\n" + "=" * 90)
        print("REMOTE OVERALL SUMMARY")
        print("=" * 90)
        print(f"Samples:             {len(overall_results)}")
        print(f"Average switch time: {statistics.mean(r['avg_ms'] for r in overall_results):.2f} ms")
        print(f"Best observed:       {min(r['min_ms'] for r in overall_results):.2f} ms")
        print(f"Worst observed:      {max(r['max_ms'] for r in overall_results):.2f} ms")
        print("=" * 90)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark remote episode switching.")
    parser.add_argument("--config", type=Path, default=ROOT / "config.json", help="Path to config.json")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP_RUNS)
    parser.add_argument("--steps", type=int, default=DEFAULT_SWITCH_STEPS)
    parser.add_argument("--max-targets", type=int, default=DEFAULT_TARGETS_PER_GROUP)
    parser.add_argument("--reset-cache-each-run", action="store_true", default=DEFAULT_RESET_CACHES_EACH_RUN)
    parser.add_argument("--rebuild-remote-maps-each-run", action="store_true", default=DEFAULT_REBUILD_REMOTE_MAPS_EACH_RUN)
    parser.add_argument("--refresh-local-index-each-run", action="store_true", default=DEFAULT_REFRESH_LOCAL_INDEX_EACH_RUN)
    parser.add_argument("--target-mode", choices=("spread", "adjacent"), default=DEFAULT_TARGET_MODE)
    parser.add_argument("--preload-wait-ms", type=int, default=0)
    args = parser.parse_args()

    if not args.config.exists():
        raise FileNotFoundError(f"Config file not found: {args.config}")

    print(f"Benchmark root: {ROOT}")
    print(f"Config: {args.config}")
    print()

    benchmark_remote(
        config_path=args.config,
        runs=args.runs,
        warmup=args.warmup,
        steps=args.steps,
        max_targets=args.max_targets,
        reset_cache_each_run=args.reset_cache_each_run,
        rebuild_remote_maps_each_run=args.rebuild_remote_maps_each_run,
        refresh_local_index_each_run=args.refresh_local_index_each_run,
        target_mode=args.target_mode,
        preload_wait_ms=args.preload_wait_ms,
    )


if __name__ == "__main__":
    main()
