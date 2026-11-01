"""Small AniList client. Public metadata and account writes are deliberately separate."""
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import requests


class AniListError(ValueError):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


class AniListAuthError(AniListError):
    pass


class AniListClient:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.retry_at = self.next_request = 0.0
        try:
            self.metadata = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self.metadata = {}
        if not isinstance(self.metadata, dict):
            self.metadata = {}

    def request(self, query, variables=None, *, token=None):
        with self.lock:
            now = time.monotonic()
            if now < self.retry_at:
                raise AniListError("AniList-Anfragelimit: später erneut versuchen.", self.retry_at-now)
            delay = self.next_request-now
            if delay > 0:
                time.sleep(min(delay, 3))
            self.next_request = time.monotonic()+2.1
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer " + token
            try:
                response = requests.post("https://graphql.anilist.co", json={"query": query, "variables": variables or {}},
                                         headers=headers, timeout=(5, 20), allow_redirects=False)
            except requests.RequestException:
                raise AniListError("AniList ist momentan nicht erreichbar.") from None
            if response.status_code == 401:
                raise AniListAuthError("AniList-Anmeldung abgelaufen; bitte erneut verbinden.")
            if response.status_code == 429:
                try:
                    wait = max(1, min(3600, float(response.headers.get("Retry-After", 60))))
                except ValueError:
                    wait = 60
                self.retry_at = time.monotonic()+wait
                raise AniListError("AniList-Anfragelimit erreicht.", wait)
            if response.status_code >= 500:
                raise AniListError("AniList ist vorübergehend nicht verfügbar.")
            try:
                value = response.json()
            except ValueError:
                raise AniListError("Unerwartete AniList-Antwort.") from None
            errors = value.get("errors") or []
            if errors:
                auth = any("invalid token" in str(row.get("message", "")).lower() or
                           "unauthenticated" in str(row.get("message", "")).lower() for row in errors)
                if auth:
                    raise AniListAuthError("AniList-Anmeldung ungültig; bitte erneut verbinden.")
                raise AniListError("AniList konnte die Anfrage nicht bearbeiten.")
            if response.status_code != 200 or not isinstance(value.get("data"), dict):
                raise AniListError("AniList konnte die Anfrage nicht bearbeiten.")
            return value["data"]

    def media(self, media_id, refresh=False):
        key = str(int(media_id))
        with self.lock:
            saved = deepcopy(self.metadata.get(key))
        if saved and not refresh and time.time()-saved.get("fetched", 0) < 86400:
            return saved["value"]
        try:
            value = self.request("""query($id:Int!){Media(id:$id,type:ANIME){id format episodes status
                title{romaji english native} startDate{year month day}
                relations{edges{relationType node{id type format title{romaji english native}}}}}}""", {"id": int(key)})["Media"]
        except AniListError:
            if saved and not refresh:
                return saved["value"]
            raise
        with self.lock:
            self.metadata[key] = {"fetched": time.time(), "value": value}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.metadata, ensure_ascii=False), "utf-8")
            temporary.replace(self.path)
        return value

    def related(self, media_id, refresh=False):
        pending, seen, result = [int(media_id)], set(), []
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            media = self.media(current, refresh)
            if media.get("format") in {"TV", "TV_SHORT", "ONA"}:
                result.append(media)
            for edge in (media.get("relations") or {}).get("edges", []):
                node = edge.get("node") or {}
                if edge.get("relationType") in {"PREQUEL", "SEQUEL"} and node.get("type") == "ANIME":
                    pending.append(node["id"])
        # A chronological, editable proposal; never confirmed by the metadata service.
        return sorted(result, key=lambda m: tuple((m.get("startDate") or {}).get(k) or 9999
                                                 for k in ("year", "month", "day")))

    def viewer(self, token):
        return self.request("query{Viewer{id name}}", token=token)["Viewer"]

    def user_list(self, token, user_id, status="CURRENT"):
        value = self.request("""query($user:Int!,$status:MediaListStatus){MediaListCollection(userId:$user,type:ANIME,status:$status){
            lists{entries{progress status media{id title{romaji english native}}}}}}""", {"user": int(user_id), "status": status}, token=token)
        rows = [row for group in (value.get("MediaListCollection") or {}).get("lists", []) for row in group["entries"]]
        return list({row["media"]["id"]: row for row in rows}.values())

    def progress(self, token, media_id):
        return self.request("""query($id:Int!){Media(id:$id,type:ANIME){id episodes
            mediaListEntry{id progress status}}}""", {"id": int(media_id)}, token=token)["Media"]

    def save_progress(self, token, media_id, progress, status):
        return self.request("""mutation($id:Int!,$progress:Int!,$status:MediaListStatus){
            SaveMediaListEntry(mediaId:$id,progress:$progress,status:$status){id progress status}}""",
                            {"id": int(media_id), "progress": int(progress), "status": status}, token=token)["SaveMediaListEntry"]
