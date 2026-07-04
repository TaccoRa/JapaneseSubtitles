"""Episode / subtitle-file switching and geometry refresh helper."""

import bisect
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

class _ControllerProxy:
    """Proxy base that forwards attribute access and assignment to SubtitleController."""

    def __init__(self, controller: Any) -> None:
        object.__setattr__(self, "controller", controller)

    def __getattr__(self, name: str):
        return getattr(self.controller, name)

    def __setattr__(self, name: str, value) -> None:
        if name == "controller":
            object.__setattr__(self, name, value)
        else:
            setattr(self.controller, name, value)

class EpisodeController(_ControllerProxy):
    """Episode / subtitle-file switching and geometry refresh helper."""

    def __init__(self, controller: Any) -> None:
        super().__init__(controller)

    def update_episode_nav_controls(self) -> None:
            """
            Grey out + / - when we know from the episode maps/index that no prev/next exists.
            """
            try:
                can_dec, can_inc, is_movie = self.sub_manager.get_episode_nav_state()
            except Exception:#
                # Unknown -> keep enabled, don't break the UI.
                can_dec, can_inc = True, True
                is_movie = (self.sub_manager.current_episode is None)
            self.settings.set_episode_nav_state(can_dec=can_dec, can_inc=can_inc, is_movie=is_movie)
            getter = getattr(self.sub_manager, "get_episode_dropdown_items", None)
            values = getter() if callable(getter) else self.sub_manager.get_episode_dropdown_values()
            self.settings.set_episode_values(values)

    def _current_episode_label(self) -> str:
            getter = getattr(self.sub_manager, "get_current_episode_label", None)
            if callable(getter):
                try:
                    return str(getter())
                except Exception:
                    pass
            if self.sub_manager.current_episode is None:
                return "Movie"
            return str(self.sub_manager.current_episode)

    @staticmethod
    def _parse_episode_label(text: str) -> tuple[int | None, int | None, int | None]:
            match = re.fullmatch(r"\s*(?P<global>\d+)\s*\(\s*S(?P<s>\d{1,2})\s*E(?P<e>\d{1,4})\s*\)\s*", text or "", re.I)
            if not match:
                return None, None, None
            return int(match.group("s")), int(match.group("e")), int(match.group("global"))

    @staticmethod
    def _normalize_anime_key(value) -> str:
            try:
                return re.sub(r"\s+", " ", str(value or "").strip()).casefold()
            except Exception:#
                logger.debug("Normalizing anime name failed", exc_info=True)
                return ""

    @staticmethod
    def _coerce_bool(value, default: bool = False) -> bool:
            if value is None:
                return bool(default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                text = value.strip().lower()
                if text in {"1", "true", "yes", "on"}:
                    return True
                if text in {"0", "false", "no", "off"}:
                    return False
            return bool(value)

    def _start_episodes_at_default_time(self) -> bool:
            config = getattr(self, "config", None)
            if config is None:
                return False
            try:
                return self._coerce_bool(config.get("START_EPISODES_AT_DEFAULT_TIME"), False)
            except Exception:
                return False

    def _episode_resume_positions(self) -> dict:
            config = getattr(self, "config", None)
            if config is None:
                return {}
            try:
                raw = config.get("EPISODE_TIME_POSITIONS")
            except Exception:
                raw = None
            return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _normalize_resume_path(path) -> str:
            text = str(path or "").strip()
            if not text:
                return ""
            try:
                text = os.path.normcase(os.path.abspath(text))
            except Exception:
                pass
            return text.replace("\\", "/")

    def _episode_resume_key(self) -> str | None:
            sub_manager = getattr(self, "sub_manager", None)
            if sub_manager is None:
                return None

            parts = ["v1"]
            anime = self._normalize_anime_key(sub_manager.get_anime_name() if hasattr(sub_manager, "get_anime_name") else "")
            if anime:
                parts.append(f"anime={anime}")

            season = getattr(sub_manager, "current_season", None)
            episode = getattr(sub_manager, "current_episode", None)
            try:
                if hasattr(sub_manager, "get_current_season"):
                    season = sub_manager.get_current_season()
                if hasattr(sub_manager, "get_current_episode"):
                    episode = sub_manager.get_current_episode()
            except Exception:
                logger.debug("Failed to read current episode metadata", exc_info=True)

            global_episode = None
            try:
                if hasattr(sub_manager, "get_current_global"):
                    global_episode = sub_manager.get_current_global()
            except Exception:
                global_episode = None

            has_episode_identity = False
            for label, value in (("s", season), ("e", episode), ("g", global_episode)):
                if value is None:
                    continue
                has_episode_identity = True
                try:
                    parts.append(f"{label}={int(value)}")
                except Exception:
                    parts.append(f"{label}={value}")

            remote_path = str(getattr(sub_manager, "remote_path", "") or "").strip()
            if remote_path:
                has_episode_identity = True
                parts.append("remote=" + remote_path.replace("\\", "/").casefold())
            else:
                local_path = self._normalize_resume_path(getattr(sub_manager, "srt_file", ""))
                if local_path:
                    has_episode_identity = True
                    parts.append("path=" + local_path)

            if not has_episode_identity:
                return None
            return "|".join(parts)

    def _current_episode_resume_time(self) -> float | None:
            if self._start_episodes_at_default_time():
                return None
            key = self._episode_resume_key()
            if not key:
                return None
            positions = self._episode_resume_positions()
            raw = positions.get(key)
            if isinstance(raw, dict):
                raw = raw.get("time")
            if raw is None:
                return None
            try:
                return max(0.0, float(raw))
            except Exception:
                logger.debug("Saved episode time is invalid for %s", key, exc_info=True)
                return None

    def _default_episode_start_time(self) -> float:
            try:
                return max(0.0, float(self.default_start_time or 0.0))
            except Exception:
                return 0.0

    def _episode_start_time_for_current(self) -> float:
            saved = self._current_episode_resume_time()
            if saved is not None:
                return saved
            return self._default_episode_start_time()

    def _time_for_resume_save(self) -> float:
            try:
                current = max(0.0, float(self.current_time or 0.0))
            except Exception:
                current = 0.0
            if bool(getattr(self, "playing", False)):
                try:
                    now = time.perf_counter()
                    elapsed = max(0.0, now - float(getattr(self, "last_update", now)))
                    current += elapsed
                    self.current_time = current
                    self.last_update = now
                except Exception:
                    logger.debug("Failed to advance current time before resume save", exc_info=True)
            try:
                total = float(getattr(self, "total_duration", 0.0) or 0.0) + float(self.get_offset_value())
                if total > 0:
                    current = min(current, total)
            except Exception:
                pass
            return current

    def save_current_episode_position(self) -> None:
            config = getattr(self, "config", None)
            if config is None:
                return
            key = self._episode_resume_key()
            if not key:
                return
            positions = self._episode_resume_positions()
            positions[key] = round(self._time_for_resume_save(), 3)
            try:
                config.set("EPISODE_TIME_POSITIONS", positions)
            except Exception:
                cfg = getattr(config, "config", None)
                if isinstance(cfg, dict):
                    cfg["EPISODE_TIME_POSITIONS"] = positions
                logger.debug("Failed to persist episode resume position", exc_info=True)

    def restore_startup_time_and_mode(self) -> None:
            self._startup_resume_play = False
            try:
                current_anime = self._normalize_anime_key(self.sub_manager.get_anime_name())
                last_anime = self._normalize_anime_key(self.config.get("LAST_ANIME_NAME"))
                if not current_anime or current_anime != last_anime:
                    self.current_time = self._default_episode_start_time()
                    return

                saved_episode_time = self._current_episode_resume_time()
                if saved_episode_time is not None:
                    self.current_time = saved_episode_time
                else:
                    saved_time = None if self._start_episodes_at_default_time() else self.config.get("LAST_SESSION_TIME_SEC")
                    if saved_time is not None:
                        try:
                            self.current_time = max(0.0, float(saved_time))
                        except Exception:#
                            logger.debug("Saved startup time is invalid", exc_info=True)
                            self.current_time = self._default_episode_start_time()
                    else:
                        self.current_time = self._default_episode_start_time()

                play_mode = self.config.get("LAST_SESSION_PLAY_MODE")
                if play_mode is None:
                    play_mode = self.config.get("LAST_SESSION_PLAYING")
                self._startup_resume_play = bool(play_mode)
            except Exception:#
                logger.debug("Saved startup state is invalid", exc_info=True)
                self.current_time = self._default_episode_start_time()
                self._startup_resume_play = False

    def _get_display_start_times(self):
            start_times = getattr(self.sub_manager, "display_start_times", None)
            if isinstance(start_times, list) and start_times:
                return start_times
            return [item[1] for item in getattr(self.sub_manager, "display_data", [])]

    def _get_display_end_times(self):
            end_times = getattr(self.sub_manager, "display_end_times", None)
            if isinstance(end_times, list) and end_times:
                return end_times
            by_start = {}
            for sub in getattr(self.sub_manager, "subtitles", []) or []:
                try:
                    by_start.setdefault(sub.start.total_seconds(), []).append(sub.end.total_seconds())
                except Exception:
                    continue
            result = []
            for item in getattr(self.sub_manager, "display_data", []) or []:
                try:
                    values = by_start.get(float(item[1]))
                except Exception:
                    values = None
                if values:
                    result.append(values.pop(0))
            return result

    def on_open_srt(self, event=None):
            self.save_current_episode_position()
            path = self.sub_manager.set_new_file()
            if path:
                self._after_episode_change()

    def change_episode(self, action: str):
            switch_start = time.perf_counter()
            self.save_current_episode_position()
            def _restore_entry():
                self.settings.episode_var.set(self._current_episode_label())

            raw = self.settings.episode_var.get().strip()
            if action in ("inc", "dec"):
                target_season, target_episode = self.sub_manager.change_episode(action)
                if target_episode is not None:
                    self.settings.episode_var.set(self._current_episode_label())
                    self._after_episode_change()
                else:
                    _restore_entry()
                self._record_episode_switch_time(switch_start)
                return

            if not raw:
                _restore_entry()
                self._record_episode_switch_time(switch_start)
                return
            if raw.lower() == 'movie':
                self._record_episode_switch_time(switch_start)
                return

            raw_int = None
            season_hint = None
            parsed_global = None
            label_s, label_e, label_g = self._parse_episode_label(raw)
            if label_s is not None and label_e is not None:
                season_hint = label_s
                raw_int = label_e
                parsed_global = label_g
            try:
                if raw_int is None:
                    candidate = int(raw)
                    if candidate > 0:
                        raw_int = candidate
            except ValueError:#
                logger.debug("Episode entry is not a plain integer: %s", raw)
                if raw_int is None:
                    try:
                        parsed_s, parsed_e, parsed_g = self.sub_manager.extract_season_episode_global(raw)
                    except Exception:#
                        logger.debug("Episode/season parser rejected entry: %s", raw, exc_info=True)
                        parsed_s, parsed_e, parsed_g = None, None, None
                    if parsed_s is not None and parsed_e is not None:
                        season_hint = int(parsed_s)
                        raw_int = int(parsed_e)
                        parsed_global = int(parsed_g) if parsed_g is not None else None
                    elif parsed_g is not None:
                        raw_int = int(parsed_g)

            if raw_int is None or raw_int <= 0:
                _restore_entry()
                self._record_episode_switch_time(switch_start)
                return

            before = (
                getattr(self.sub_manager, "srt_file", None),
                getattr(self.sub_manager, "current_season", None),
                getattr(self.sub_manager, "current_episode", None),
            )
            target_season, target_episode = self.sub_manager.change_episode("set", raw_int, season_hint)
            after = (
                getattr(self.sub_manager, "srt_file", None),
                getattr(self.sub_manager, "current_season", None),
                getattr(self.sub_manager, "current_episode", None),
            )

            # Fallback: if explicit SxxEyy did not resolve, and parser also gave a global
            # candidate, try the global target once.
            if before == after and season_hint is not None and parsed_global is not None and parsed_global > 0:
                target_season, target_episode = self.sub_manager.change_episode("set", parsed_global, None)

            if target_episode is not None:
                self.settings.episode_var.set(self._current_episode_label())
                self._after_episode_change() #reset all with new srt data
            else: #change not allowed
                _restore_entry()
            self._record_episode_switch_time(switch_start)

    def _record_episode_switch_time(self, switch_start: float) -> None:
            try:
                self._record_perf_sample("episode_switch", (time.perf_counter() - switch_start) * 1000.0)
            except Exception as e:
                logger.debug("Failed to record episode switch time: %s", e, exc_info=True)

    def _after_episode_change(self):
            self.settings.episode_var.set(self._current_episode_label())
            self.update_episode_nav_controls()

            new_total = self.sub_manager.get_total_duration() ##maybe not needed anymore
            self.settings.set_total_duration(new_total)
            self.total_duration = new_total
            self.update_max_width()
            title= f'S{self.sub_manager.get_current_season()}E{self.sub_manager.get_current_episode()} {self.sub_manager.get_anime_name()}'
            self.settings.root.title(title)
            self.current_time = self._episode_start_time_for_current()
            self._defer_auto_ruby_once = True
            self.last_subtitle_text = ""
            self.playback.set_current_time(self.current_time)
            self.ocr_controller._schedule_ocr_time_jump("episode_change")
            try:
                self.sub_manager.schedule_episode_preload_around_current()
            except Exception:
                pass

    def update_max_width(self) -> None:
            # Recompute content width + padding
            max_w, max_h = self.sub_manager.get_subtitle_geometry()
            max_w, max_h = int(max_w), int(max_h)

            # Apply geometry on overlay and update renderer's canvas ref
            self.overlay.update_geometry(max_w, max_h)
            self.renderer.update_canvas(self.overlay.subtitle_canvas)

            # ensure layout finalized so canvas.winfo_width() matches what renderer expects
            self.overlay.subtitle_canvas.update_idletasks()

            # Immediately re-render the subtitle at current_time (same logic as _update_subtitle_display)
            offset = self.settings._last_offset_value
            sub_t = self.current_time - offset
            finder = getattr(getattr(self.controller, "subtitle_navigation", None), "_display_index_at_time", None)
            if callable(finder):
                idx = finder(sub_t)
            else:
                start_times = self._get_display_start_times()
                idx = bisect.bisect_right(start_times, sub_t) - 1
                if idx >= 0:
                    end_times = self._get_display_end_times()
                    if idx < len(end_times):
                        try:
                            if float(sub_t) >= float(end_times[idx]) - 0.0005:
                                idx = None
                        except Exception:
                            pass
            if idx is None or idx < 0:
                # nothing to draw
                self.renderer.canvas.delete("all")
                return

            _, _, top_segments, bottom_segments = self.sub_manager.display_data[idx]
            # render freshly using updated overlay/canvas
            self.renderer.canvas.delete("all")
            self.renderer.render_subtitle(top_segments, bottom_segments, self.overlay)
