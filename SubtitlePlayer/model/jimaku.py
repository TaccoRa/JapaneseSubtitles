"""Jimaku's read-only API. Tokens never enter settings, history or download headers."""
import hashlib
import io
import json
import os
import logging
from pathlib import Path
import re
import shutil
import time
import threading
import unicodedata
from urllib.parse import urlparse
import zipfile

import requests

logger = logging.getLogger(__name__)


class JimakuClient:
    _download_locks = {}
    _locks_guard = threading.Lock()

    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache = {}
        self.retry_at = 0.0
        self._catalogs = {}
        self._catalog_lock = threading.RLock()
        self._refreshing = set()
        self._catalog_retry = {}
        self.last_catalog_error = ""
        self.member_names = {}

    def get(self, endpoint, **params):
        token = os.environ.get("JIMAKU_TOKEN", "").strip()
        if not token:
            raise ValueError("JIMAKU_TOKEN fehlt. App nach dem Setzen der Umgebungsvariable neu starten.")
        if time.monotonic() < self.retry_at:
            raise ValueError("Jimaku-Anfragelimit erreicht. Bitte etwas warten.")
        key = (endpoint, tuple(sorted(params.items())))
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < 180:
            return cached[1]
        try:
            response = requests.get("https://jimaku.cc/api/" + endpoint, params=params,
                                    headers={"Authorization": token, "User-Agent": "SubtitlePlayer"},
                                    timeout=(5, 20), allow_redirects=False)
        except requests.RequestException:
            raise ValueError("Jimaku ist momentan nicht erreichbar.") from None
        if response.status_code in (401, 403):
            raise ValueError("Jimaku hat JIMAKU_TOKEN abgelehnt. API-Token bitte prüfen.")
        if response.status_code == 429:
            try:
                seconds = max(1, min(3600, int(response.headers.get("Retry-After", 60))))
            except ValueError:
                seconds = 60
            self.retry_at = time.monotonic() + seconds
            raise ValueError(f"Jimaku-Anfragelimit erreicht. Nach {seconds} Sekunden erneut versuchen.")
        if response.status_code != 200:
            raise ValueError(f"Jimaku-Anfrage fehlgeschlagen (HTTP {response.status_code}).")
        try:
            data = response.json()
            if not isinstance(data, list):
                raise ValueError()
        except ValueError:
            raise ValueError("Jimaku hat eine unerwartete Antwort geliefert.") from None
        self.cache[key] = (time.monotonic(), data)
        return data

    @staticmethod
    def normalized(text):
        return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", str(text or "")).casefold()))

    def catalog(self, anime):
        category = "anime" if anime else "drama"
        with self._catalog_lock:
            saved = self._catalogs.get(category)
            if saved is None:
                try:
                    saved = json.loads((self.cache_dir / f"catalog_{category}.json").read_text("utf-8"))
                    if not isinstance(saved.get("entries"), list):
                        saved = None
                except (OSError, ValueError, AttributeError):
                    saved = None
                if saved:
                    self._catalogs[category] = saved
            if saved:
                if (time.time() - saved.get("fetched", 0) >= 86400 and category not in self._refreshing
                        and time.monotonic() >= self._catalog_retry.get(category, 0)):
                    self._refreshing.add(category)
                    threading.Thread(target=self._refresh_catalog, args=(category,), daemon=True,
                                     name="jimaku-catalog-refresh").start()
                return list(saved["entries"])
            if time.monotonic() < self._catalog_retry.get(category, 0):
                raise ValueError(self.last_catalog_error or "Jimaku-Katalog ist momentan nicht erreichbar. Bitte später erneut versuchen.")
            # The caller is a background worker. Serialize the initial download.
            return self._refresh_catalog(category)

    def _refresh_catalog(self, category):
        try:
            entries = self.get("entries/search", anime="true" if category == "anime" else "false")
            saved = {"fetched": time.time(), "entries": entries}
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            target = self.cache_dir / f"catalog_{category}.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(saved, ensure_ascii=False), "utf-8")
            temporary.replace(target)
            with self._catalog_lock:
                self._catalogs[category] = saved
                self.last_catalog_error = ""
            return list(entries)
        except Exception as exc:
            self.last_catalog_error = str(exc)
            self._catalog_retry[category] = max(time.monotonic() + 60, self.retry_at)
            with self._catalog_lock:
                old = self._catalogs.get(category)
            if old:
                return list(old["entries"])
            raise
        finally:
            with self._catalog_lock:
                self._refreshing.discard(category)

    def search(self, query, anime):
        needle = self.normalized(query)
        if not needle:
            return []
        def score(entry):
            best = 0
            for key in ("name", "english_name", "japanese_name"):
                title = self.normalized(entry.get(key))
                if not title:
                    continue
                if title == needle:
                    best = max(best, 10000)
                elif needle in title:
                    best = max(best, 5000 - len(title))
                elif all(any(word.startswith(term) for word in title.split()) for term in needle.split()):
                    best = max(best, 2500 - len(title))
            return best
        ranked = [(score(entry), entry) for entry in self.catalog(anime) if isinstance(entry, dict)]
        return [entry for rank, entry in sorted(ranked, key=lambda pair: (-pair[0], str(pair[1].get("name", ""))))
                if rank > 0][:150]

    def files(self, entry_id, refresh=False):
        endpoint = f"entries/{int(entry_id)}/files"
        if refresh:
            self.cache.pop((endpoint, ()), None)
        return self.get(endpoint)

    def download(self, entry_id, file, *, title=None, parser=None, cache_season=None):
        lock_key = (str(self.cache_dir), int(entry_id), file.get("url"), file.get("last_modified"))
        with self._locks_guard:
            lock = self._download_locks.setdefault(lock_key, threading.Lock())
        with lock:
            return self._download(entry_id, file, title=title, parser=parser, cache_season=cache_season)

    @staticmethod
    def safe_component(value, limit=64):
        text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip().rstrip(". ")
        if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM\d|LPT\d)", text, re.I):
            text = '_' + text
        return text[:limit].rstrip(". ") or "Jimaku"

    def _structured_path(self, title, name, version, parser, *, archive=False, cache_season=None, create=True):
        season = episode = absolute = None
        if parser and not archive:
            season, episode, absolute = parser(name)
        folder_season = cache_season if cache_season is not None else season
        folder_name = "Archives" if archive else (f"Season{folder_season:02d}" if folder_season is not None else "Season00")
        folder = self.cache_dir / self.safe_component(title or "Jimaku") / folder_name
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(name).suffix.lower()
        prefix = f"S{season:02d}E{episode:04d} " if season is not None and episode is not None else (
            f"Ep{episode or absolute:04d} " if episode or absolute else "")
        budget = max(24, min(160, 240 - len(str(folder.resolve())) - 1))
        ending = '_' + version[:10] + suffix
        stem = self.safe_component(Path(name).stem, 180)
        filename = prefix + stem[:max(1, budget-len(prefix)-len(ending))] + ending
        return folder / filename

    def _download(self, entry_id, file, *, title=None, parser=None, cache_season=None):
        url = str(file.get("url", ""))
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Ungültiger Jimaku-Downloadlink.")
        suffix = Path(str(file.get("name", ""))).suffix.lower()
        if suffix not in (".srt", ".ass", ".ssa", ".zip"):
            raise ValueError("Bitte SRT, ASS, SSA oder ZIP wählen.")
        version = hashlib.sha256((url + str(file.get("last_modified", ""))).encode()).hexdigest()[:16]
        legacy_folder = self.cache_dir / str(int(entry_id)) / version
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(file.get("name") or "subtitle"))[:180].rstrip(". ")
        target = self._structured_path(title or str(entry_id), str(file.get("name") or "subtitle"), version, parser,
                                       archive=suffix == ".zip", cache_season=cache_season)
        known_paths = getattr(self, "cached_paths", lambda _entry: [])(entry_id)
        if not target.is_file():
            previous = next((Path(path) for path in known_paths
                             if Path(path).name.endswith('_'+version[:10]+suffix) and Path(path).is_file()), None)
            if previous:
                target = previous
            else:
                for previous_title in getattr(self, "previous_titles", lambda _entry: [])(entry_id):
                    previous = self._structured_path(previous_title, str(file.get("name") or "subtitle"), version, parser, archive=suffix == ".zip", create=False)
                    if previous.is_file():
                        target = previous
                        break
        if not target.is_file() and (legacy_folder / name).is_file():
            temporary = target.with_suffix(target.suffix + ".part")
            shutil.copyfile(legacy_folder / name, temporary)
            temporary.replace(target)
        if not target.is_file():
            try:
                with requests.get(url, timeout=(5, 30), stream=True) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > 64 * 1024 * 1024:
                            raise ValueError("Untertitel-Download ist größer als 64 MB.")
            except requests.RequestException:
                raise ValueError("Untertitel-Download fehlgeschlagen. Dateiliste bitte aktualisieren.") from None
            if bytes(content[:200]).lstrip().lower().startswith((b'<!doctype html', b'<html')):
                raise ValueError("Der Download enthält HTML statt Untertiteln.")
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(content)
            temporary.replace(target)
            _s, ep, absolute = parser(str(file.get("name", ""))) if parser else (None, None, None)
            label = f"Episode {ep or absolute}" if ep or absolute else target.name
            logger.info("Jimaku: downloaded %s / %s / %s", title or entry_id, target.parent.name, label)
        if suffix != ".zip":
            return [str(target)]
        paths, total = [], 0
        with zipfile.ZipFile(io.BytesIO(target.read_bytes())) as archive:
            for index, member in enumerate(archive.infolist()):
                ext = Path(member.filename).suffix.lower()
                if member.is_dir() or ext not in (".srt", ".ass", ".ssa"):
                    continue
                total += member.file_size
                if total > 128 * 1024 * 1024 or len(paths) >= 1000:
                    raise ValueError("ZIP enthält zu viele oder zu große Untertitel.")
                basename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', member.filename.replace('\\', '/').split('/')[-1])
                member_version = hashlib.sha256((version + ':' + member.filename + ':' + str(index)).encode()).hexdigest()
                destination = self._structured_path(title or str(entry_id), basename, member_version, parser, cache_season=cache_season)
                previous = next((Path(path) for path in known_paths if
                                 Path(path).name.endswith('_'+member_version[:10]+ext) and Path(path).is_file()), None)
                if previous:
                    destination = previous
                legacy_member = legacy_folder / "unpacked" / basename[:170]
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.is_file():
                    if legacy_member.is_file() and legacy_member.stat().st_size == member.file_size:
                        shutil.copyfile(legacy_member, destination)
                    else:
                        destination.write_bytes(archive.read(member))
                paths.append(str(destination))
                self.member_names[str(destination)] = basename
        if not paths:
            raise ValueError("ZIP enthält keine unterstützten Untertitel.")
        return paths
