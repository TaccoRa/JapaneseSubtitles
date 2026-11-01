"""Asynchronous app-side episode navigation for the selected Jimaku source."""
from copy import deepcopy
import logging
import queue
import re


logger = logging.getLogger(__name__)


class JimakuNavigation:
    def __init__(self, controller):
        self.controller = controller
        self.results = queue.Queue()
        self.token = 0
        self.choosing = False

    def request(self, action):
        c, manager = self.controller, self.controller.sub_manager
        if c.browser_sync._capture or self.choosing:
            return
        descriptor = deepcopy(manager.source_descriptor)
        original_descriptor = deepcopy(descriptor)
        descriptor.pop("episode_mapping", None)  # Main-app numbers are source numbers.
        record = manager.jimaku.series.get(descriptor)
        if record and descriptor.get("episode_key"):
            try:
                if action in ("inc", "dec"):
                    target = manager.jimaku.series.neighbor(descriptor, 1 if action == "inc" else -1)
                else:
                    raw = c.settings.episode_var.get().strip()
                    rows = manager.jimaku.series.episodes(record)
                    matches = [row for row in rows if row["label"].lower() == raw.lower() or
                               (row["global"] is not None and str(row["global"]) == raw)]
                    if not matches and raw.isdigit():
                        matches = [row for row in rows if row["part_id"] == descriptor.get("part_id") and row["episode"] == int(raw)]
                    match = re.fullmatch(r"S(\d+)\s*E(\d+)", raw, re.I)
                    if match:
                        matches = [row for row in rows if (row["season"], row["episode"]) == (int(match[1]), int(match[2]))]
                    if len(matches) != 1:
                        raise ValueError("Bitte eine eindeutige Staffel/Folge aus der Liste auswählen.")
                    target = matches[0]
                if target is None:
                    return
                descriptor.update(entry_id=target["entry_id"], episode_key=target["key"], part_id=target["part_id"])
                episode, season = target["episode"], target["season"]
            except ValueError as exc:
                c.browser_sync._episode_problem = str(exc)
                c.settings.episode_var.set(c.episode_controller._current_episode_label())
                return
        else:
            target = None
        current = manager.get_current_global() or manager.current_episode
        # A plain number follows the selected release across season-folder
        # boundaries (e.g. absolute One Piece numbers). Only explicit SxxEyy
        # input constrains the provider season.
        if target is not None:
            pass
        elif action in ("inc", "dec"):
            season = manager.current_season if descriptor.get("series_separate") else None
            values = manager.get_episode_dropdown_values()
            candidates = [value for value in values if value > current] if action == "inc" else [value for value in values if value < current]
            if not candidates:
                return
            episode = min(candidates) if action == "inc" else max(candidates)
        else:
            raw = c.settings.episode_var.get().strip()
            season_hint, episode_hint, absolute = c.episode_controller._parse_episode_label(raw)
            try:
                episode = episode_hint or int(raw)
            except ValueError:
                season_hint, episode, absolute = manager.extract_season_episode_global(raw)
                episode = episode or absolute
            if not episode or episode < 1:
                c.settings.episode_var.set(c.episode_controller._current_episode_label())
                return
            season = season_hint
        scope = "season" if season is not None else "global"
        self.token += 1
        token, source = self.token, manager.srt_file
        watching = (c.browser_sync.identity, (c.browser_sync._media_context or {}).get("pageUrl"))
        c.settings.episode_var.set(c.episode_controller._current_episode_label())
        c.browser_sync._set_status(f"Jimaku: loading episode {episode}")
        def work():
            if token != self.token or c.browser_sync._closed:
                return
            try:
                state = manager.jimaku.prepare(descriptor, episode, season, scope, manager.extract_season_episode_global)
                if not record:
                    state["source_descriptor"] = original_descriptor
                    manager.jimaku.remember_path(state["srt_file"], original_descriptor)
                state["_prepared_key"], state["_prepared_payload"] = manager.prepare_subtitle_display(state["srt_file"])
                error = None
            except Exception as exc:
                state, error = None, exc
            self.results.put((token, source, descriptor, episode, season, scope, state, error, watching))
        c.browser_sync.submit_source_task("app-navigation", work)

    def pump(self):
        if self.choosing:
            return
        c, manager = self.controller, self.controller.sub_manager
        try:
            while True:
                result = self.results.get_nowait()
                token, source, descriptor, episode, season, scope, state, error, watching = result
                if token != self.token or source != manager.srt_file or manager.source_kind != "jimaku":
                    continue
                if watching != (c.browser_sync.identity, (c.browser_sync._media_context or {}).get("pageUrl")):
                    continue
                if c.browser_sync._capture:
                    self.results.put(result)
                    break
                if not getattr(c.browser_sync, "_subtitle_navigation_only", False):
                    c.episode_controller.save_current_episode_position()
                c.browser_sync._subtitle_navigation_only = True
                if error:
                    self.choosing = True
                    manager._set_modal_input_active(True)
                    try:
                        from model.jimaku_series import SeriesConfirmationRequired
                        if isinstance(error, SeriesConfirmationRequired):
                            confirmed = manager.confirm_jimaku_series(c.settings.root, descriptor, proposal=error.proposal,
                                valid=lambda: token == self.token and source == manager.srt_file and
                                    watching == (c.browser_sync.identity, (c.browser_sync._media_context or {}).get("pageUrl")))
                            if confirmed:
                                c.settings.episode_var.set(f"S{season:02}E{episode:02}" if season else str(episode))
                                self.choosing = False
                                self.request("set")
                            continue
                        source_target = manager.jimaku.series.resolve(descriptor) if descriptor.get("episode_key") else None
                        expected = ({"episode": source_target["source_episode"], "season": source_target["source_season"],
                                     "scope": "season" if source_target["source_season"] is not None else "global"} if source_target else
                                    {"episode": episode, "season": season, "scope": scope})
                        path = manager.choose_jimaku_source(c.settings.root, query=descriptor["title"],
                            entry_id=descriptor["entry_id"],
                            expected=expected,
                            valid=lambda: token == self.token and source == manager.srt_file
                                and watching == (c.browser_sync.identity, (c.browser_sync._media_context or {}).get("pageUrl")))
                        if not path:
                            c.browser_sync._episode_problem = str(error)
                            continue
                        manager._load_local_and_process(path)
                    except Exception:
                        logger.exception("Jimaku episode selection failed")
                        continue
                    finally:
                        manager._set_modal_input_active(False)
                        self.choosing = False
                else:
                    try:
                        if state.get("_series_revision") is not None:
                            current_map = manager.jimaku.series.get(state["source_descriptor"])
                            if not current_map or state["_series_revision"] != current_map.get("revision", 0):
                                c.settings.episode_var.set(c.episode_controller._current_episode_label())
                                c.browser_sync._episode_problem = "Anime-Map wurde geändert. Episodenwahl bitte wiederholen."
                                continue
                        c.browser_sync._source_bindings.restore_state(state, manager)
                    except Exception:
                        logger.exception("Could not apply prepared Jimaku episode")
                        continue
                c.episode_controller._after_episode_change()
        except queue.Empty:
            pass
