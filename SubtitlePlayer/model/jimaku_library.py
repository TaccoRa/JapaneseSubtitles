"""Persistent Jimaku series, release choices and read-only episode preparation."""
from copy import deepcopy
import json
import logging
from pathlib import Path
import re
import threading
import time

from model.jimaku import JimakuClient
from model.jimaku_series import JimakuSeries, SeriesConfirmationRequired

logger = logging.getLogger(__name__)


class JimakuChoiceRequired(ValueError):
    pass


class JimakuLibrary:
    def __init__(self, root, state_dir):
        self.client = JimakuClient(root)
        self.path = Path(state_dir) / "jimaku_library.json"
        self.lock = threading.RLock()
        self.prefetch_epoch = 0
        self.prefetch_job = None
        self.prefetch_error = ""
        self._episode_identity_cache = {}
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}
        for key in ("entries", "choices", "bindings", "paths"):
            self.data.setdefault(key, {})
        self.series = JimakuSeries(self, state_dir)
        self.client.cached_paths = self._cached_paths
        self.client.previous_titles = self._previous_titles

    def _cached_paths(self, entry_id):
        with self.lock:
            paths = [path for path, descriptor in self.data["paths"].items() if descriptor.get("entry_id") == int(entry_id)]
            paths += [row["path"] for row in self.data["entries"].get(str(entry_id), {}).get("members", []) if row.get("path")]
            return paths

    def _previous_titles(self, entry_id):
        with self.lock:
            return self.aliases(self.data["entries"].get(str(entry_id), {}).get("entry", {}))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False), "utf-8")
        temporary.replace(self.path)

    @staticmethod
    def aliases(entry):
        return [str(entry[key]) for key in ("name", "english_name", "japanese_name", "display_name") if entry.get(key)]

    @staticmethod
    def signature(name):
        text = JimakuClient.normalized(Path(name).stem)
        text = re.sub(r"\d+", "#", text)
        # Episode titles vary, but source/format/release labels usually persist.
        tags = re.findall(r"\[[^\]]+\]", str(name))
        provider = re.findall(r"netflix|amazon|hulu|crunchyroll|webrip|bluray|bdrip|hdtv|\bcc\b|\bsdh\b", text)
        language = re.findall(r"\b(?:ja|jpn|japanese|en|eng|english|de|ger|german)\b", text)
        return {"tags": [re.sub(r"\d+", "#", tag.casefold()) for tag in tags], "provider": provider, "language": language,
                "extension": Path(name).suffix.casefold(), "pattern": text}

    def descriptor_for_path(self, path, legacy=()):
        path = str(Path(path).resolve())
        with self.lock:
            found = self.data["paths"].get(path)
            if found:
                self._adopt_existing_file(path, found)
                return deepcopy(found)
        for row in legacy or ():
            if (isinstance(row, dict) and row.get("entry_id")
                    and str(Path(row.get("directory", "")).resolve()) == str(Path(path).parent)):
                descriptor = {"entry_id": int(row["entry_id"]), "title": row.get("title") or "Jimaku",
                              "movie": bool(row.get("movie")), "anime": True}
                with self.lock:
                    self._adopt_existing_file(path, descriptor)
                return descriptor
        return None

    def _adopt_existing_file(self, path, descriptor):
        key = str(descriptor["entry_id"])
        if key in self.data["choices"] or not Path(path).is_file():
            return
        name = Path(path).name
        self.data["entries"].setdefault(key, {"entry": {"id": int(key), "name": descriptor["title"],
            "flags": {"anime": descriptor.get("anime", True), "movie": descriptor.get("movie", False)}}, "files": [], "fetched": 0})
        self.data["choices"][key] = {"signature": self.signature(name), "file": {"name": name}, "archive": None}
        self.data["paths"][path] = deepcopy(descriptor)
        self._save()

    def remember_selection(self, entry, path, parser, *, mapping=None):
        selection = entry.get("_selection") or {}
        metadata = {key: deepcopy(value) for key, value in entry.items() if not key.startswith("_")}
        entry_id = str(int(entry["id"]))
        descriptor = {"entry_id": int(entry_id), "title": entry.get("display_name") or entry.get("name") or "Jimaku",
                      "movie": bool(entry.get("flags", {}).get("movie")),
                      "anime": bool(entry.get("flags", {}).get("anime", True))}
        if mapping:
            descriptor["episode_mapping"] = mapping
        selected_file = selection.get("file") or {"name": Path(path).name}
        archive_selected = str(selected_file.get("name", "")).lower().endswith(".zip")
        member_name = selection.get("member_names", {}).get(path, Path(path).name)
        choice = {"signature": self.signature(member_name if archive_selected else selected_file["name"]), "file": selected_file,
                  "archive": selected_file if str(selected_file.get("name", "")).lower().endswith(".zip") else None}
        with self.lock:
            previous = self.data["entries"].get(entry_id, {})
            files = selection.get("files") or previous.get("files", [])
            self.data["entries"][entry_id] = {"entry": metadata, "files": files,
                "fetched": time.time() if selection.get("files") else previous.get("fetched", 0)}
            members = []
            for member in selection.get("members", []):
                original_name = selection.get("member_names", {}).get(member, Path(member).name)
                s, e, g = parser(original_name)
                members.append({"season": s, "episode": e if e is not None else g, "global": g,
                                "name": original_name, "path": member})
                self.data["paths"][str(Path(member).resolve())] = deepcopy(descriptor)
            if members:
                self.data["entries"][entry_id]["members"] = members
            self.data["choices"][entry_id] = choice
            self.data["paths"][str(Path(path).resolve())] = descriptor
            self._save()
        return descriptor

    def bind(self, key, descriptor, title=""):
        if not key or not descriptor:
            return
        with self.lock:
            self.data["bindings"][key] = deepcopy(descriptor)
            record = self.data["entries"].get(str(descriptor["entry_id"]))
            if record and title:
                names = record["entry"].setdefault("browser_aliases", [])
                if title not in names:
                    names.append(title)
                    del names[:-20]
            self._save()

    def remember_path(self, path, descriptor):
        with self.lock:
            key = str(Path(path).resolve())
            if self.data["paths"].get(key) != descriptor:
                self.data["paths"][key] = deepcopy(descriptor)
                self._save()

    def find_series(self, title, binding_keys=(), anime=True):
        def clean(value):
            text = JimakuClient.normalized(value)
            text = re.sub(r"\s*(?:miruro|netflix|aniworld|youtube)\s*$", "", text).strip()
            text = re.sub(r"^watch\s+", "", text)
            text = re.sub(r"\s+(?:episode|folge|ep)\s*\d+.*$", "", text).strip()
            return re.sub(r"\s+s\d+\s+[ef]\d+.*$", "", text).strip()
        needle, cleaned = JimakuClient.normalized(title), clean(title)
        with self.lock:
            saved = deepcopy(self.data["entries"])
            for key in binding_keys:
                descriptor = self.data["bindings"].get(key) if key else None
                if descriptor:
                    entry = saved.get(str(descriptor["entry_id"]), {}).get("entry", {})
                    aliases = self.aliases(entry) + entry.get("browser_aliases", []) + [descriptor.get("title", "")]
                    # These keys identify a concrete browser series/page, not
                    # a title search. A confirmed user association survives
                    # changed spelling, aliases and website title formatting.
                    if descriptor.get("entry_id"):
                        selected = deepcopy(descriptor)
                        selected.pop("episode_key", None)
                        selected["_browser_selection"] = True
                        return selected
        def exact(entry):
            aliases = self.aliases(entry) + entry.get("browser_aliases", [])
            return bool(cleaned) and any(JimakuClient.normalized(alias) == needle or clean(alias) == cleaned or
                                       clean(alias).replace(" ", "") == cleaned.replace(" ", "") for alias in aliases)
        matches = [record["entry"] for record in saved.values() if exact(record["entry"])]
        if not matches:
            suggestions = self.client.search(cleaned or title, anime)
            if not suggestions:
                suggestions = self.client.search(cleaned or title, not anime)
            matches = [entry for entry in suggestions if exact(entry)] or suggestions
        if not matches:
            raise JimakuChoiceRequired("Jimaku: keine passende Serie gefunden. Bitte anderen Titel suchen.")
        entry = matches[0]
        return {"entry_id": int(entry["id"]), "title": entry.get("display_name") or entry["name"],
                "_browser_selection": True,
                "movie": bool(entry.get("flags", {}).get("movie")),
                "anime": bool(entry.get("flags", {}).get("anime", anime))}

    def files(self, descriptor, refresh=False):
        key = str(descriptor["entry_id"])
        with self.lock:
            record = deepcopy(self.data["entries"].get(key, {}))
        if not refresh and record.get("files") and time.time() - record.get("fetched", 0) < 86400:
            return record["files"]
        try:
            files = self.client.files(int(key), refresh=refresh)
        except Exception:
            if record.get("files") and not refresh:
                return record["files"]
            raise
        if not record.get("entry"):
            record["entry"] = {"id": int(key), "name": descriptor["title"],
                               "flags": {"movie": descriptor.get("movie", False), "anime": descriptor.get("anime", True)}}
        record.update(files=files, fetched=time.time())
        with self.lock:
            self.data["entries"][key] = record
            self._save()
        return files

    def candidates(self, descriptor, parser, refresh=False):
        records = []
        for file in self.files(descriptor, refresh):
            if Path(file.get("name", "")).suffix.lower() not in (".srt", ".ass", ".ssa"):
                continue
            season, episode, absolute = parser(file["name"])
            if episode is None:
                episode = absolute
            records.append({"season": season, "episode": episode, "global": absolute,
                            "file": file, "name": file["name"]})
        return records

    def cached_candidates(self, descriptor, parser):
        if not descriptor:
            return []
        with self.lock:
            entry = deepcopy(self.data["entries"].get(str(descriptor["entry_id"]), {}))
            files = entry.get("files", [])
        records = list(entry.get("members", []))
        for file in files:
            if Path(file.get("name", "")).suffix.lower() in (".srt", ".ass", ".ssa"):
                s, e, g = parser(file["name"])
                if e is not None or g is not None:
                    records.append({"season": s, "episode": e if e is not None else g, "global": g,
                                    "file": file, "name": file["name"]})
        return records

    def episode_for_path(self, path, descriptor, parser):
        """Recover both numbering systems from original names, not shortened cache names."""
        key = (str((descriptor or {}).get("entry_id")), str(Path(path).resolve()))
        with self.lock:
            if key in self._episode_identity_cache:
                return self._episode_identity_cache[key]
        season, episode, absolute = parser(Path(path).name)
        rows = self.cached_candidates(descriptor, parser)
        exact = [row for row in rows if row.get("path") and
                 Path(row["path"]).resolve() == Path(path).resolve()]
        if not exact:
            exact = [row for row in rows if row["season"] == season and row["episode"] == episode]
        identities = {(row["season"], row["episode"], row["global"]) for row in exact}
        if len(identities) == 1:
            result = next(iter(identities))
            with self.lock:
                self._episode_identity_cache[key] = result
            return result
        return season, episode, absolute

    def _choose(self, rows, choice, *, mapped=False):
        if not rows:
            return None
        if not choice:
            return rows[0]
        wanted = choice["signature"]
        def rank(row):
            actual = self.signature(row["name"])
            if wanted["provider"] != actual["provider"] or wanted["tags"] != actual["tags"]:
                return -1
            if wanted.get("language", []) != actual.get("language", []):
                return -1
            return (10 if wanted["extension"] == actual["extension"] else 0) + (1 if wanted["pattern"] == actual["pattern"] else 0)
        best = max(rank(row) for row in rows)
        selected = [row for row in rows if rank(row) == best]
        if best >= 0 and choice.get("automatic"):
            return selected[0]
        if mapped:
            # A confirmed episode identity is authoritative. Release preferences
            # rank alternatives; they must not reopen search on every episode.
            return sorted(selected, key=lambda row: (not bool(row.get("path")), row["name"].casefold()))[0]
        if best < 0 or len(selected) != 1:
            raise JimakuChoiceRequired("Jimaku: passende Veröffentlichung fehlt oder mehrere Dateien passen. Bitte auswählen.")
        return selected[0]

    def prepare(self, descriptor, episode, season, scope, parser, *, refresh=False):
        if (not descriptor.get("_entry_only") and not descriptor.get("series_separate")
                and descriptor.get("anime", True) and not descriptor.get("movie")):
            record = self.series.get(descriptor)
            if record is None:
                if self.series.excluded_entry(descriptor["entry_id"]):
                    descriptor = dict(descriptor, _entry_only=True)
                else:
                    proposal = self.series.propose(descriptor, parser, refresh)
                    if proposal.get("separate"):
                        descriptor = dict(descriptor, series_separate=True, _entry_only=True)
                    else:
                        raise SeriesConfirmationRequired(proposal)
            else:
                return self.series.prepare(descriptor, episode, season, scope, parser, refresh)
        mapping = descriptor.get("episode_mapping")
        # A URL without a season can contain an absolute episode number even
        # when the site's scope says "season". Explicit filename evidence wins
        # over old arithmetic mappings between absolute and season-local values.
        absolute_match = season is None and not (mapping or {}).get("source_scope") and any(
            row["global"] == episode for row in self.cached_candidates(descriptor, parser))
        if absolute_match:
            scope = "global"
            mapping = None
        if mapping and scope == mapping["scope"]:
            if mapping.get("browser_season") is not None and season is not None and season != mapping["browser_season"]:
                raise JimakuChoiceRequired("Jimaku: neue Staffel benötigt eine bestätigte Zuordnung.")
            episode += mapping["source_base"] - mapping["browser_base"]
            season = mapping.get("source_season")
            effective = deepcopy(descriptor)
            effective.pop("episode_mapping", None)
            state = self.prepare(effective, episode, season, mapping.get("source_scope", scope), parser, refresh=refresh)
            state["source_descriptor"] = deepcopy(descriptor)
            with self.lock:
                self.data["paths"][str(Path(state["srt_file"]).resolve())] = deepcopy(descriptor)
                self._save()
            return state
        with self.lock:
            choice = deepcopy(self.data["choices"].get(str(descriptor["entry_id"])))
        rows = self.candidates(descriptor, parser, refresh)
        archive = choice.get("archive") if choice else None
        if not choice and not any(row["episode"] == episode or row["global"] == episode for row in rows):
            archive = next((file for file in self.files(descriptor)
                            if str(file.get("name", "")).lower().endswith(".zip")), None)
        if archive:
            if refresh:
                with self.lock:
                    replacements = [file for file in self.data["entries"].get(str(descriptor["entry_id"]), {}).get("files", [])
                                    if file.get("name") == archive.get("name")]
                if len(replacements) != 1:
                    raise JimakuChoiceRequired("Jimaku: das gewählte Archiv ist nicht mehr eindeutig verfügbar.")
                archive = replacements[0]
                with self.lock:
                    if str(descriptor["entry_id"]) in self.data["choices"]:
                        self.data["choices"][str(descriptor["entry_id"])]["archive"] = archive
                        self._save()
            try:
                paths = self.client.download(descriptor["entry_id"], archive, title=descriptor["title"], parser=parser,
                                             cache_season=descriptor.get("_cache_season"))
            except Exception:
                if not refresh:
                    return self.prepare(descriptor, episode, season, scope, parser, refresh=True)
                raise
            rows = []
            for path in paths:
                original_name = self.client.member_names.get(path, Path(path).name)
                s, e, g = parser(original_name)
                rows.append({"season": s, "episode": e if e is not None else g, "global": g,
                             "name": original_name, "path": path})
            with self.lock:
                self.data["entries"].setdefault(str(descriptor["entry_id"]), {})["members"] = rows
                self._save()
        if descriptor.get("movie"):
            matching = rows
        elif scope == "global" or (season is None and any(row["global"] == episode for row in rows)):
            matching = [row for row in rows if row["global"] == episode]
            if not matching:
                matching = [row for row in rows if row["episode"] == episode]
        else:
            matching = [row for row in rows if row["episode"] == episode and
                        (season is None or row["season"] == season)]
            strict = descriptor.get("_strict_season") and season is not None
            if not matching and strict:
                matching = [row for row in rows if row["season"] is None and (row["episode"] == episode or
                            descriptor.get("_expected_global") is not None and (row.get("global") == descriptor["_expected_global"] or
                            row.get("episode") == descriptor["_expected_global"]))]
            if not matching and not strict:
                matching = [row for row in rows if row["global"] == episode]
            if not matching and not strict:
                matching = [row for row in rows if row["episode"] == episode]
            if choice and season is None and len({row["season"] for row in matching}) > 1:
                raise JimakuChoiceRequired("Jimaku: Staffel ist nicht eindeutig. Bitte passende Datei auswählen.")
        # All candidates already identify this episode. Prefer SRT, then
        # explicit season numbering, keeping provider order for equal choices.
        matching = sorted(matching, key=lambda row: (
            Path(row["name"]).suffix.lower() != ".srt",
            not bool(re.search(r"s\d+\s*[._ -]*e\d+", row["name"], re.I))))
        remaining = list(matching)
        last_error = None
        selected = None
        while remaining:
            candidate = remaining.pop(0)
            try:
                path = candidate.get("path")
                if not path or not Path(path).is_file():
                    downloaded = self.client.download(descriptor["entry_id"], candidate["file"],
                        title=descriptor["title"], parser=parser, cache_season=descriptor.get("_cache_season"))
                    if not downloaded:
                        raise ValueError("Keine Untertiteldatei im Download.")
                    path = downloaded[0]
                if not Path(path).is_file() or Path(path).stat().st_size == 0:
                    raise ValueError("Untertiteldatei fehlt oder ist leer.")
                selected = candidate
                break
            except Exception as exc:
                last_error = exc
                logger.warning("Jimaku: Datei %s nicht verfügbar; versuche nächste passende Datei (%s)",
                               candidate["name"], type(exc).__name__)
        if selected is None:
            if not refresh and not archive:
                return self.prepare(descriptor, episode, season, scope, parser, refresh=True)
            if last_error is not None:
                raise ValueError("Keine passende Untertiteldatei konnte geladen werden.") from last_error
            raise JimakuChoiceRequired("Jimaku: Für diese Folge ist keine passende Datei verfügbar.")
        public_descriptor = {k: deepcopy(v) for k, v in descriptor.items() if not k.startswith("_")}
        state = {"srt_file": path, "remote_flag": False, "source_kind": "jimaku",
                 "source_descriptor": public_descriptor, "anime_folder_name": descriptor["title"],
                 "display_anime_name": descriptor["title"], "is_movie": bool(descriptor.get("movie")),
                 "current_season": selected["season"], "current_episode": selected["episode"],
                 "remote_path": None, "remote_url": None, "remote_search_query": None,
                 "file_name": Path(path).name, "all_results_items": []}
        state["_selected_row"] = dict(selected, path=path)
        if descriptor.get("movie"):
            state["current_season"] = state["current_episode"] = None
        with self.lock:
            if not choice:
                # Commit the automatic release preference only after a usable
                # episode was downloaded. Subsequent episodes reuse this source.
                self.data["choices"].setdefault(str(descriptor["entry_id"]), {
                    "signature": self.signature(selected["name"]),
                    "file": archive or selected.get("file") or {"name": selected["name"]},
                    "archive": archive, "automatic": True})
            self.data["paths"][str(Path(path).resolve())] = public_descriptor
            self._save()
        return state

    def prefetch(self, descriptor, episode, season, scope, parser, radius=2):
        descriptor = deepcopy(descriptor)
        descriptor.pop("episode_mapping", None)
        self.prefetch_epoch += 1
        epoch = self.prefetch_epoch
        def run():
            if self.series.get(descriptor):
                try:
                    record = self.series.get(descriptor)
                    rows = self.series.episodes(record)
                    current = self.series.resolve(descriptor, episode, season, scope)
                    center = next(i for i, row in enumerate(rows) if row["key"] == current["key"])
                    for distance in range(1, max(0, min(50, int(radius)))+1):
                        for index in (center+distance, center-distance):
                            if epoch != self.prefetch_epoch:
                                return
                            if not 0 <= index < len(rows):
                                continue
                            target = rows[index]
                            selected = dict(descriptor, entry_id=target["entry_id"], episode_key=target["key"])
                            try:
                                state = self.series.prepare(selected, None, None, None, parser)
                                prepare = getattr(self, "prepare_display", None)
                                if prepare:
                                    prepare(state["srt_file"])
                            except Exception as exc:
                                self.prefetch_error = str(exc)
                    return
                except Exception as exc:
                    self.prefetch_error = str(exc)
                    return
            try:
                # Legacy selections may have a file/release but no persisted list.
                # Fetch it once here, not on Tk, then populate the complete window.
                self.files(descriptor)
                if epoch != self.prefetch_epoch:
                    return
                self.prepare(descriptor, episode, season, scope, parser)
            except Exception as exc:
                self.prefetch_error = str(exc)
                logger.debug("Jimaku prefetch unavailable: %s", exc)
                return
            available = self.cached_candidates(descriptor, parser)
            logger.debug("Jimaku: cache window %s episodes %s–%s", descriptor["title"], max(1, episode-radius), episode+radius)
            for distance in range(1, max(0, min(50, int(radius))) + 1):
                for target in (episode + distance, episode - distance):
                    if epoch != self.prefetch_epoch:
                        return
                    if target < 1:
                        continue
                    if not any((row.get("global") == target or (row.get("global") is None and row.get("episode") == target))
                               if scope == "global" else
                               (row.get("episode") == target and (season is None or row.get("season") == season))
                               for row in available):
                        continue
                    try:
                        self.prepare(descriptor, target, season, scope, parser)
                    except Exception as exc:
                        self.prefetch_error = str(exc)
        # One serial prefetch worker; replace pending windows rather than spawning
        # simultaneous recordings of download/parse work for every navigation.
        self._pending_prefetch = run
        with self.lock:
            if self.prefetch_job and self.prefetch_job.is_alive():
                return
            def pump():
                while True:
                    with self.lock:
                        work = self._pending_prefetch
                        self._pending_prefetch = None
                        if work is None:
                            self.prefetch_job = None
                            return
                    work()
            self.prefetch_job = threading.Thread(target=pump, name="jimaku-prefetch", daemon=True)
            self.prefetch_job.start()
