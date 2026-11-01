"""Confirmed multi-entry Jimaku series. Playback order is not an absolute episode number."""
from copy import deepcopy
import json
from pathlib import Path
import re
import threading
import time

from model.anilist import AniListClient, AniListError
from model.timing_map import digest


class SeriesConfirmationRequired(ValueError):
    def __init__(self, proposal):
        super().__init__("Jimaku: Staffel-Liste einmal bestätigen.")
        self.proposal = proposal


class SeriesGap(ValueError):
    pass


class JimakuSeries:
    def __init__(self, library, directory):
        self.library = library
        self.path = Path(directory)/"jimaku_series.json"
        self.anilist = AniListClient(Path(directory)/"anilist_metadata.json")
        self.lock = threading.RLock()
        self.refreshing = set()
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}
        for key in ("series", "entries", "redirects"):
            self.data.setdefault(key, {})

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False), "utf-8")
        temporary.replace(self.path)

    def get(self, descriptor):
        if not descriptor or descriptor.get("series_separate"):
            return None
        with self.lock:
            key = self.data["entries"].get(str(descriptor.get("entry_id"))) or descriptor.get("series_id")
            seen = set()
            while key in self.data["redirects"] and key not in seen:
                seen.add(key)
                key = self.data["redirects"][key]
            record = self.data["series"].get(key)
            if record and descriptor.get("entry_id") not in {p["entry_id"] for p in record["parts"]}:
                return None
            if record and self._repair_continuous_source_numbering(record):
                self._save()
            return deepcopy(record)

    @staticmethod
    def _repair_continuous_source_numbering(record):
        """S05E126 can be an absolute file number, not season-local E126.

        Only infer this from complete, disjoint and contiguous file ranges in
        successive seasons of the same entry. Preserve explicit custom ranges.
        """
        changed = False
        groups = {}
        for part in record["parts"]:
            groups.setdefault(part["entry_id"], []).append(part)
        for parts in groups.values():
            if len(parts) < 2:
                continue
            parts = sorted(parts, key=lambda p: p.get("source_season") or 0)
            ranges = []
            for part in parts:
                numbers = sorted({f["episode"] for f in part["files"] if isinstance(f.get("episode"), int)})
                if (not part.get("source_season") or not numbers or
                        numbers != list(range(numbers[0], numbers[-1]+1))):
                    break
                ranges.append((numbers[0], numbers[-1]))
            if (len(ranges) != len(parts) or ranges[0][0] != 1 or
                    any(right[0] != left[1]+1 for left, right in zip(ranges, ranges[1:]))):
                continue
            for index, (part, (first, last)) in enumerate(zip(parts, ranges)):
                if part["first"] not in (1, first) or part["last"] != last:
                    continue  # A deliberate user-defined range remains authoritative.
                if part.get("global_first") not in (None, first):
                    continue
                desired = dict(first=first, global_first=first)
                if index < len(parts)-1 or part.get("anilist_count") == last:
                    desired["end_confirmed"] = True
                if any(part.get(k) != v for k, v in desired.items()):
                    part.setdefault("numbering_before_repair", {k: part.get(k) for k in desired})
                    part.update(desired)
                    changed = True
        return changed

    def entry(self, entry_id, anime=True):
        for entry in self.library.client.catalog(anime):
            if int(entry["id"]) == int(entry_id):
                return deepcopy(entry)
        with self.library.lock:
            return deepcopy(self.library.data["entries"].get(str(entry_id), {}).get("entry", {}))

    def excluded_entry(self, entry_id):
        prefix = str(entry_id)+":"
        with self.lock:
            return any(any(part.startswith(prefix) for part in record.get("excluded_parts", []))
                       and not any(p["entry_id"] == entry_id for p in record["parts"])
                       for record in self.data["series"].values())

    def _parts(self, entry, parser, refresh=False, metadata=None, seed=None):
        descriptor = {"entry_id": int(entry["id"]), "title": entry.get("name") or "Jimaku", "anime": True, "movie": False}
        rows = self.library.candidates(descriptor, parser, refresh)
        members = [row for row in self.library.cached_candidates(descriptor, parser) if row.get("path")]
        rows += members
        if seed and seed.get("id") == entry["id"]:
            selection = seed.get("_selection") or {}
            for path in selection.get("members", []):
                name = selection.get("member_names", {}).get(path, Path(path).name)
                s, e, g = parser(name)
                rows.append({"season": s, "episode": e if e is not None else g, "global": g, "name": name, "path": path})
        rows = [row for row in rows if row.get("episode") is not None and row.get("season") != 0]
        known = [row for row in rows if row["season"] is not None]
        normalized = []
        for row in rows:
            if row["season"] is None:
                peers = {(r["season"], r["episode"]) for r in known if row.get("global") is not None and r.get("global") == row["global"]}
                if len(peers) == 1:
                    s, e = next(iter(peers))
                    row = dict(row, season=s, episode=e)
                elif len({r["season"] for r in known}) == 1 and any(r["episode"] == row["episode"] for r in known):
                    row = {**row, "season": known[0]["season"], "global": None}
            g = row.get("global")
            if row["season"] is not None and g is not None and re.search(r"s\d+[ ._-]*e\d+", row["name"], re.I):
                explicit = re.search(r"第\s*"+str(g)+r"\s*話|[\[(]\s*"+str(g)+r"\s*[\])]", row["name"])
                if not explicit:
                    row = {**row, "global": None}
            normalized.append(row)
        rows = normalized
        groups = {}
        for row in rows:
            groups.setdefault(row["season"], []).append(row)
        if not groups:
            groups[None] = []
        result = []
        total = (metadata or {}).get("episodes")
        for season, values in sorted(groups.items(), key=lambda item: item[0] if item[0] is not None else -1):
            numbers = sorted({r["episode"] for r in values if r.get("episode") is not None})
            first = 1
            if (season is None and total and numbers and numbers[0] > 1 and numbers[-1] > total
                    and numbers[-1]-numbers[0]+1 <= total):
                first = numbers[0]
            end = max(numbers, default=first)
            known_end = False
            if len(groups) == 1 and total:
                if first == 1:
                    end, known_end = max(end, int(total)), True
                elif int(total) < first or end > int(total):
                    end, known_end = max(end, first+int(total)-1), True
                elif end == int(total):
                    known_end = True
            result.append({"id": f"{entry['id']}:{season if season is not None else 'all'}",
                "entry_id": int(entry["id"]), "title": entry.get("name") or "Jimaku",
                "anilist_id": entry.get("anilist_id"), "anilist_count": total,
                "source_season": season, "first": first, "last": end, "end_confirmed": known_end,
                "files": values, "archive_pending": not values})
        # Explicit absolute/local anchors reveal preceding season boundaries.
        for left, right in zip(result, result[1:]):
            left_offsets = {r["global"]-r["episode"] for r in left["files"] if r.get("global") is not None}
            right_globals = [r["global"] for r in right["files"] if r.get("global") is not None]
            if len(left_offsets) == 1 and right_globals:
                boundary = min(right_globals)-next(iter(left_offsets))-1
                if boundary >= left["last"]:
                    left["last"], left["end_confirmed"] = boundary, True
        self._repair_continuous_source_numbering({"parts": result})
        return result

    def propose(self, descriptor, parser, refresh=False, seed=None):
        root = self.entry(descriptor["entry_id"])
        if not root:
            root = {"id": descriptor["entry_id"], "name": descriptor["title"], "flags": {"anime": True}}
        current = self.get(descriptor)
        notices, related = [], []
        if root.get("anilist_id"):
            try:
                meta = self.anilist.media(root["anilist_id"], refresh)
                if meta.get("format") not in {"TV", "TV_SHORT", "ONA"}:
                    return {"separate": True, "title": descriptor["title"], "parts": []}
                related = self.anilist.related(root["anilist_id"], refresh)
            except (AniListError, OSError) as exc:
                notices.append(str(exc)+" Weitere Staffeln können manuell ergänzt werden.")
        catalog = self.library.client.catalog(True)
        by_anilist = {}
        for entry in catalog:
            if entry.get("anilist_id") and not entry.get("flags", {}).get("movie"):
                by_anilist.setdefault(entry["anilist_id"], []).append(entry)
        parts, seen = [], set()
        for media in related:
            matches = by_anilist.get(media["id"], [])
            if not matches:
                notices.append("Keine Jimaku-Quelle: "+(media["title"].get("romaji") or str(media["id"])))
            # Alternative entries are selectable in the confirmation UI, not mixed.
            chosen = next((entry for entry in matches if entry["id"] == root["id"]), matches[0] if matches else None)
            if chosen:
                seen.add(chosen["id"])
                parts.extend(self._parts(chosen, parser, refresh, media, seed))
        if root["id"] not in seen:
            parts.extend(self._parts(root, parser, refresh, seed=seed))
        if current is None:
            current = next((known for part in parts if (known := self.get({"entry_id": part["entry_id"]}))), None)
        if current:
            parts = [part for part in parts if part["id"] not in current.get("excluded_parts", [])]
            for part in parts:
                equivalents = [old for old in current["parts"] if old["entry_id"] == part["entry_id"]
                               and old["source_season"] == part["source_season"]]
                if len(equivalents) == 1:
                    part["id"] = equivalents[0]["id"]
            proposed = {part["id"]: part for part in parts}
            ordered = []
            for old in current["parts"]:
                if (old.get("archive_pending") and old["id"] not in proposed and any(
                        p["entry_id"] == old["entry_id"] and not p.get("archive_pending") for p in proposed.values())):
                    continue
                new = proposed.pop(old["id"], deepcopy(old))
                # User-confirmed numbering and boundaries survive background refresh.
                for key in ("first", "last", "end_confirmed", "season", "global_first"):
                    if old.get("archive_pending") and new.get("files") and key != "season":
                        continue  # The original source numbering was not indexed yet.
                    if key not in old:
                        continue
                    new[key] = max(old[key], new[key]) if key == "last" and not old["end_confirmed"] else old[key]
                ordered.append(new)
            parts = ordered+list(proposed.values())
        for index, part in enumerate(parts, 1):
            part.setdefault("season", part["source_season"] if len(seen or {root['id']}) == 1 and part["source_season"] is not None else index)
        canonical_title = descriptor["title"]
        if len({part["entry_id"] for part in parts}) > 1 and parts and canonical_title in self.library.aliases(root):
            canonical_title = parts[0]["title"]
        root_id = related[0]["id"] if related else root.get("anilist_id") or "entry-"+str(root["id"])
        return {"id": current["id"] if current else "jimaku-"+str(root_id),
                "title": current["title"] if current else canonical_title, "parts": parts,
                "excluded_parts": current.get("excluded_parts", []) if current else [],
                "base_revision": current.get("revision", 0) if current else 0,
                "numbering_confirmed": current.get("numbering_confirmed", False) if current else False,
                "confirmed": False, "notices": notices, "checked": time.time()}

    def add_entry(self, proposal, entry, parser):
        metadata = None
        if entry.get("anilist_id"):
            try:
                metadata = self.anilist.media(entry["anilist_id"])
            except AniListError:
                pass
        if metadata and metadata.get("format") not in {"TV", "TV_SHORT", "ONA"}:
            raise ValueError("Filme und Specials bleiben separat; bitte einen TV-Abschnitt wählen.")
        parts = self._parts(entry, parser, metadata=metadata)
        existing = {part["id"] for part in proposal["parts"]}
        for part in parts:
            if part["id"] not in existing:
                if part["id"] in proposal.get("excluded_parts", []):
                    proposal["excluded_parts"].remove(part["id"])
                part["season"] = max((p["season"] for p in proposal["parts"]), default=0)+1
                proposal["parts"].append(part)
        return proposal

    def confirm(self, proposal):
        value = deepcopy(proposal)
        if not value["parts"]:
            raise ValueError("Mindestens einen Staffelabschnitt auswählen.")
        self._repair_continuous_source_numbering(value)
        seasons = [int(part["season"]) for part in value["parts"]]
        if len(seasons) != len(set(seasons)) or any(season < 1 for season in seasons):
            raise ValueError("Jeder Abschnitt braucht eine eindeutige positive Staffelnummer.")
        for part in value["parts"]:
            if int(part["first"]) < 1 or int(part["last"]) < int(part["first"]) or part["last"]-part["first"] >= 10000:
                raise ValueError("Ungültiger Folgenbereich.")
        if value.get("numbering_confirmed"):
            numbers = [row["global"] for row in self.episodes(value) if row["global"] is not None]
            if len(numbers) != len(set(numbers)) or any(n < 1 for n in numbers):
                raise ValueError("Gesamtfolgen müssen positiv sein und dürfen sich nicht überschneiden.")
        value.update(confirmed=True, checked=time.time(), pending_parts=[])
        with self.lock:
            previous = self.data["series"].get(value["id"], {})
            if previous.get("revision", 0) != value.get("base_revision", 0):
                raise ValueError("Die Map wurde inzwischen geändert. Bitte aktualisieren und erneut bestätigen.")
            value["revision"] = previous.get("revision", 0)+1
            included = {str(p["entry_id"]) for p in value["parts"]}
            for key, owner in list(self.data["entries"].items()):
                if owner == value["id"] and key not in included:
                    self.data["entries"].pop(key, None)
            for part in value["parts"]:
                previous = self.data["entries"].get(str(part["entry_id"]))
                if previous and previous != value["id"]:
                    self.data["redirects"][previous] = value["id"]
                self.data["entries"][str(part["entry_id"])] = value["id"]
            self.data["series"][value["id"]] = value
            self._save()
        return value

    @staticmethod
    def episodes(record):
        result = []
        next_global = 1 if record.get("numbering_confirmed") else None
        for part in record["parts"]:
            global_first = part.get("global_first", next_global)
            if part["last"]-part["first"] >= 10000:
                raise ValueError("Folgenbereich ist zu groß. Anime-Map prüfen.")
            indexed = {}
            for file in part["files"]:
                indexed.setdefault(file.get("episode"), []).append(file)
            for episode in range(int(part["first"]), int(part["last"])+1):
                rows = indexed.get(episode, [])
                absolute = {r["global"] for r in rows if r.get("global") is not None}
                global_number = (int(global_first)+episode-int(part["first"]) if global_first is not None
                                 else next(iter(absolute)) if len(absolute) == 1 else None)
                label = f"S{part['season']:02}E{episode-part['first']+1:02}"
                if global_number is not None and global_number != episode-part["first"]+1:
                    label = f"{global_number} ({label})"
                result.append({"key": f"{part['id']}:{episode}", "part_id": part["id"], "entry_id": part["entry_id"],
                    "source_season": part["source_season"], "source_episode": episode,
                    "season": part["season"], "episode": episode-part["first"]+1,
                    "global": global_number,
                    "label": label,
                    "available": bool(rows), "archive_pending": part.get("archive_pending", False)})
            next_global = (int(global_first)+int(part["last"])-int(part["first"])+1
                           if global_first is not None and part.get("end_confirmed") else None)
        return result

    def describe_path(self, path, descriptor, parser):
        record = self.get(descriptor)
        if not record:
            return descriptor
        season, episode, absolute = self.library.episode_for_path(path, descriptor, parser)
        descriptor = dict(descriptor, legacy_media=self.legacy_media(descriptor, season, episode))
        matches = [r for r in self.episodes(record) if r["entry_id"] == descriptor["entry_id"] and
                   r["source_season"] == season and r["source_episode"] == episode]
        if len(matches) == 1:
            row = matches[0]
            return {k: v for k, v in dict(descriptor, series_id=record["id"], episode_key=row["key"],
                                         part_id=row["part_id"], title=record["title"]).items() if not k.startswith("_")}
        if season is None:
            candidates = [r for r in self.episodes(record) if r["entry_id"] == descriptor["entry_id"] and r["source_episode"] == episode]
            if len(candidates) == 1:
                row = candidates[0]
                return dict(descriptor, series_id=record["id"], episode_key=row["key"], part_id=row["part_id"], title=record["title"])
        if absolute is not None:
            candidates = [r for r in self.episodes(record) if r["entry_id"] == descriptor["entry_id"] and r["global"] == absolute]
            if len(candidates) == 1:
                row = candidates[0]
                return dict(descriptor, series_id=record["id"], episode_key=row["key"], part_id=row["part_id"], title=record["title"])
        if descriptor.get("episode_key") not in {row["key"] for row in self.episodes(record)}:
            descriptor = dict(descriptor)
            descriptor.pop("episode_key", None)
            descriptor.pop("part_id", None)
        return descriptor

    def legacy_media(self, descriptor, season, episode):
        names = self.library._previous_titles(descriptor["entry_id"])+[descriptor.get("title", "")]
        return sorted({digest(json.dumps([name, season, episode], ensure_ascii=False).encode()) for name in names if name})

    def resolve(self, descriptor, episode=None, season=None, scope=None):
        record = self.get(descriptor)
        if not record:
            return None
        rows = self.episodes(record)
        key = descriptor.get("episode_key")
        mapping = descriptor.get("episode_mapping")
        if (not key and descriptor.get("_browser_selection") and mapping and mapping.get("scope") == scope
                and (mapping.get("browser_season") is None or season in (None, mapping["browser_season"]))):
            direct = [row for row in rows if row["entry_id"] == descriptor["entry_id"] and row["global"] == episode]
            if mapping.get("source_scope") or not direct:
                mapped = episode+mapping["source_base"]-mapping["browser_base"]
                converted = [row for row in rows if row["entry_id"] == descriptor["entry_id"] and
                             (row["global"] == mapped if mapping.get("source_scope") == "global" else
                              row["source_episode"] == mapped and (mapping.get("source_season") is None or
                                                                   row["source_season"] == mapping["source_season"]))]
                if len(converted) == 1:
                    return converted[0]
        if key:
            matches = [row for row in rows if row["key"] == key]
        elif scope == "global":
            matches = [row for row in rows if row["global"] == episode]
        elif season is not None:
            matches = [row for row in rows if row["season"] == season and row["episode"] == episode]
            if descriptor.get("_browser_selection") and not record.get("numbering_confirmed"):
                provider = [row for row in rows if row["entry_id"] == descriptor["entry_id"] and
                            row["source_season"] == season and row["source_episode"] == episode]
                if provider:
                    matches = provider
        else:
            matches = [row for row in rows if row["global"] == episode]
            in_entry = [row for row in matches if row["entry_id"] == descriptor["entry_id"]]
            if in_entry:
                matches = in_entry
            absolute_numbering = any(row["entry_id"] == descriptor["entry_id"] and row["global"] is not None
                                     and row["global"] != row["source_episode"] for row in rows)
            if not matches and not absolute_numbering:
                matches = [row for row in rows if row["entry_id"] == descriptor["entry_id"] and row["source_episode"] == episode]
            if not matches and descriptor.get("_browser_selection") and not absolute_numbering:
                own_parts = [p for p in record["parts"] if p["entry_id"] == descriptor["entry_id"]]
                if len(own_parts) == 1 and own_parts[0]["first"] > 1:
                    matches = [row for row in rows if row["part_id"] == own_parts[0]["id"] and row["episode"] == episode]
            if len(matches) != 1 and episode is not None and not absolute_numbering:
                parts = [p for p in record["parts"] if p["entry_id"] == descriptor["entry_id"]]
                if not parts or len(parts) == 1 or parts[0]["source_season"] != 1:
                    parts = []
                offset = 0
                for part in parts:
                    count = part["last"]-part["first"]+1
                    if offset < episode <= offset+count:
                        inferred = [r for r in rows if r["part_id"] == part["id"] and r["episode"] == episode-offset]
                        if len(inferred) == 1:
                            matches = inferred
                        break
                    if not part["end_confirmed"]:
                        break
                    offset += count
        if len(matches) != 1:
            raise SeriesGap("Folge ist nicht eindeutig zugeordnet. Staffel und Folge auswählen.")
        return matches[0]

    def neighbor(self, descriptor, direction):
        record = self.get(descriptor)
        rows = self.episodes(record)
        current = self.resolve(descriptor)
        index = next(i for i, row in enumerate(rows) if row["key"] == current["key"])
        target = index+direction
        if not 0 <= target < len(rows):
            if direction > 0 and not record["parts"][-1]["end_confirmed"]:
                raise SeriesGap("Staffelende unbekannt: Map aktualisieren oder Folgenbereich bestätigen.")
            return None
        if rows[target]["part_id"] != current["part_id"]:
            part = next(p for p in record["parts"] if p["id"] == (current if direction > 0 else rows[target])["part_id"])
            if not part["end_confirmed"]:
                raise SeriesGap("Staffelende ist noch nicht bestätigt. Map aktualisieren.")
        return rows[target]

    def prepare(self, descriptor, episode, season, scope, parser, refresh=False):
        record = self.get(descriptor)
        target = self.resolve(descriptor, episode, season, scope)
        part = next(p for p in record["parts"] if p["id"] == target["part_id"])
        selected = {"entry_id": target["entry_id"], "title": record["title"], "anime": True, "movie": False,
                    "_entry_only": True, "_strict_season": True, "_expected_global": target["global"], "_cache_season": target["season"]}
        try:
            state = self.library.prepare(selected, target["source_episode"], target["source_season"],
                                         "season" if target["source_season"] is not None else "global", parser, refresh=refresh)
        except ValueError as exc:
            if part.get("archive_pending") and any(r.get("path") for r in self.library.cached_candidates(selected, parser)):
                raise SeriesConfirmationRequired(self.propose(selected, parser)) from exc
            raise SeriesGap(f"{target['label']} ({part['title']}): {exc}") from exc
        if part.get("archive_pending"):
            members = [r for r in self.library.cached_candidates(selected, parser) if r.get("path") and r.get("episode") is not None]
            seasons = {r["season"] for r in members}
            if (len(seasons) == 1 and members and all(part["first"] <= r["episode"] <= part["last"] for r in members)):
                part.update(files=members, archive_pending=False, source_season=next(iter(seasons)))
                target["source_season"] = part["source_season"]
            else:
                raise SeriesConfirmationRequired(self.propose(selected, parser))
        actual = self.library.episode_for_path(state["srt_file"], selected, parser)
        if target["source_season"] is not None and actual[0] is not None and actual[0] != target["source_season"]:
            raise SeriesGap("Datei passt nicht zur bestätigten Staffel; Ersatzdatei auswählen.")
        if actual[1] != target["source_episode"] and (target["global"] is None or
                actual[2] != target["global"] and not (actual[0] is None and actual[1] == target["global"])):
            raise SeriesGap("Datei passt nicht zur bestätigten Folge; Ersatzdatei auswählen.")
        chosen = state.get("_selected_row")
        if chosen:
            part["files"] = [r for r in part["files"] if r["name"] != chosen["name"]]+[chosen]
            with self.lock:
                saved = self.data["series"].get(record["id"])
                current = next((p for p in saved["parts"] if p["id"] == part["id"]), None) if saved else None
                if current is not None and current != part:
                    current.update(files=part["files"], archive_pending=part.get("archive_pending", False), source_season=part["source_season"])
                    self._save()
        effective = {"series_id": record["id"], "entry_id": target["entry_id"], "episode_key": target["key"],
                     "part_id": target["part_id"], "title": record["title"], "anime": True, "movie": False}
        effective["legacy_media"] = self.legacy_media(effective, actual[0], actual[1])
        if descriptor.get("episode_mapping"):
            effective["episode_mapping"] = deepcopy(descriptor["episode_mapping"])
        state.update(source_descriptor=effective, current_season=target["season"], current_episode=target["episode"],
                     anime_folder_name=record["title"], display_anime_name=record["title"])
        state["_series_episode"] = target
        state["_series_revision"] = record.get("revision", 0)
        self.library.remember_path(state["srt_file"], effective)
        self.refresh_later(effective, parser)
        return state

    def progress_binding(self, descriptor):
        record = self.get(descriptor)
        row = self.resolve(descriptor)
        part = next(p for p in record["parts"] if p["id"] == row["part_id"])
        media_id = part.get("anilist_id")
        if not media_id:
            return None
        siblings = [p for p in record["parts"] if p.get("anilist_id") == media_id]
        progress = row["episode"]
        if len(siblings) > 1:
            if row["global"] is not None:
                progress = row["global"]
            elif all(p["end_confirmed"] for p in siblings[:siblings.index(part)]):
                progress += sum(p["last"]-p["first"]+1 for p in siblings[:siblings.index(part)])
            else:
                return None
        return {"media_id": int(media_id), "progress": int(progress), "episodes": part.get("anilist_count")}

    def refresh_later(self, descriptor, parser):
        record = self.get(descriptor)
        if not record or time.time()-record.get("checked", 0) < 86400:
            return
        with self.lock:
            if record["id"] in self.refreshing:
                return
            self.refreshing.add(record["id"])
        def work():
            try:
                proposal = self.propose(descriptor, parser)
                old_ids = {p["id"] for p in record["parts"]}
                fresh = {p["id"]: p for p in proposal["parts"]}
                with self.lock:
                    saved = self.data["series"].get(record["id"])
                    if saved:
                        for part in saved["parts"]:
                            if part["id"] in fresh:
                                part["files"] = fresh[part["id"]]["files"]
                                part["archive_pending"] = fresh[part["id"]]["archive_pending"]
                                if not part["end_confirmed"]:
                                    part["last"] = max(part["last"], fresh[part["id"]]["last"])
                        saved["pending_parts"] = [p for p in proposal["parts"] if p["id"] not in old_ids]
                        saved["checked"] = time.time()
                        self._save()
            except Exception as exc:
                with self.lock:
                    saved = self.data["series"].get(record["id"])
                    if saved:
                        saved["refresh_error"] = str(exc)
            finally:
                with self.lock:
                    self.refreshing.discard(record["id"])
        threading.Thread(target=work, name="jimaku-map-refresh", daemon=True).start()
