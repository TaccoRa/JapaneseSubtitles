"""Episode / subtitle-file switching and geometry refresh helper."""

import bisect
import logging
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
            values = self.sub_manager.get_episode_dropdown_values()
            self.settings.set_episode_values(values)

    @staticmethod
    def _normalize_anime_key(value) -> str:
            try:
                return re.sub(r"\s+", " ", str(value or "").strip()).casefold()
            except Exception:#
                logger.debug("Normalizing anime name failed", exc_info=True)
                return ""

    def restore_startup_time_and_mode(self) -> None:
            self._startup_resume_play = False
            try:
                current_anime = self._normalize_anime_key(self.sub_manager.get_anime_name())
                last_anime = self._normalize_anime_key(self.config.get("LAST_ANIME_NAME"))
                if not current_anime or current_anime != last_anime:
                    self.current_time = float(self.default_start_time or 0.0)
                    return
                saved_time = self.config.get("LAST_SESSION_TIME_SEC")
                if saved_time is not None:
                    try:
                        self.current_time = max(0.0, float(saved_time))
                    except Exception:#
                        logger.debug("Saved startup time is invalid", exc_info=True)
                        self.current_time = float(self.default_start_time or 0.0)
                else:
                    self.current_time = float(self.default_start_time or 0.0)

                play_mode = self.config.get("LAST_SESSION_PLAY_MODE")
                if play_mode is None:
                    play_mode = self.config.get("LAST_SESSION_PLAYING")
                self._startup_resume_play = bool(play_mode)
            except Exception:#
                logger.debug("Saved startup state is invalid", exc_info=True)
                self.current_time = float(self.default_start_time or 0.0)
                self._startup_resume_play = False

    def _get_display_start_times(self):
            start_times = getattr(self.sub_manager, "display_start_times", None)
            if isinstance(start_times, list) and start_times:
                return start_times
            return [item[1] for item in getattr(self.sub_manager, "display_data", [])]

    def on_open_srt(self, event=None):
            path = self.sub_manager.set_new_file()
            if path:
                self._after_episode_change()

    def change_episode(self, action: str):
            switch_start = time.perf_counter()
            def _restore_entry():
                if self.sub_manager.current_episode is None:
                    self.settings.episode_var.set("Movie")
                else:
                    self.settings.episode_var.set(str(self.sub_manager.current_episode))

            raw = self.settings.episode_var.get().strip()
            if action in ("inc", "dec"):
                target_season, target_episode = self.sub_manager.change_episode(action)
                if target_episode is not None:
                    self.settings.episode_var.set(str(target_episode))
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
            try:
                candidate = int(raw)
                if candidate > 0:
                    raw_int = candidate
            except ValueError:#
                logger.debug("Episode entry is not a plain integer: %s", raw)
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
                self.settings.episode_var.set(str(target_episode))
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
            if self.sub_manager.current_episode is None:
                self.settings.episode_var.set("Movie")
            else: 
                self.settings.episode_var.set(str(self.sub_manager.current_episode))
            self.update_episode_nav_controls()

            new_total = self.sub_manager.get_total_duration() ##maybe not needed anymore
            self.settings.set_total_duration(new_total)
            self.total_duration = new_total
            self.update_max_width()
            title= f'S{self.sub_manager.get_current_season()}E{self.sub_manager.get_current_episode()} {self.sub_manager.get_anime_name()}'
            self.settings.root.title(title)
            self.current_time = self.default_start_time
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
            start_times = self._get_display_start_times()
            idx = bisect.bisect_right(start_times, sub_t) - 1
            if idx < 0:
                # nothing to draw
                self.renderer.canvas.delete("all")
                return

            _, _, top_segments, bottom_segments = self.sub_manager.display_data[idx]
            # render freshly using updated overlay/canvas
            self.renderer.canvas.delete("all")
            self.renderer.render_subtitle(top_segments, bottom_segments, self.overlay)
