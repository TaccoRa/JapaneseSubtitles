"""Account-scoped AniList progress queue driven only by bound browser observations."""
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
import webbrowser

from model.anilist import AniListError, AniListAuthError
from model.anilist_auth import ClientSecretStore, CredentialStore, OAuthAttempt

logger = logging.getLogger(__name__)


class AniListController:
    def __init__(self, browser, directory):
        self.browser = browser
        self.manager = browser.controller.sub_manager
        self.config = browser.controller.config
        self.settings = browser.controller.settings
        self.client = self.manager.jimaku.series.anilist
        self.store = CredentialStore()
        self.client_secrets = ClientSecretStore()
        self.path = Path(directory)/"anilist_progress.json"
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.tasks, self.events = queue.Queue(), queue.Queue()
        self.credential = self.attempt = self.watch = None
        self.epoch, self.retry_at = 0, 0.0
        self.restore_needed = True
        self.status = tk.StringVar(self.settings.root, "Nicht verbunden")
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}
        if not isinstance(self.data, dict):
            self.data = {}
        self.data.setdefault("pending", {})
        self.data.setdefault("completed", {})
        self.settings.anilist_status_var = self.status
        self.settings._on_anilist_connect = self.connect
        self.settings._on_anilist_disconnect = self.disconnect
        self.settings._on_anilist_setup = self.setup
        self.manager.choose_anilist_list = self.choose_list
        threading.Thread(target=self._worker, name="anilist-account", daemon=True).start()
        self.tasks.put(self._load)

    def _say(self, text, epoch=None):
        self.events.put((self.epoch if epoch is None else epoch, str(text)))

    def _save(self):
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.data), "utf-8")
            temporary.replace(self.path)

    def _load(self):
        epoch = self.epoch
        saved = self.store.read()
        if not saved:
            self.restore_needed = False
            return
        if float(saved.get("expires", 0)) <= time.time():
            self.restore_needed = False
            self._say("AniList-Anmeldung abgelaufen · erneut verbinden")
            return
        user = self.client.viewer(saved["token"])
        if int(user["id"]) != int(saved["user_id"]):
            raise AniListAuthError("Gespeicherte AniList-Anmeldung gehört zu einem anderen Konto.")
        with self.lock:
            if epoch == self.epoch and not self.closed.is_set():
                self.credential = saved
                self.restore_needed = False
                self._say("Verbunden: "+user["name"])

    def connect(self, parent=None):
        client_id = self.config.get("ANILIST_CLIENT_ID") or os.environ.get("SUBTITLEPLAYER_ANILIST_CLIENT_ID")
        try:
            client_secret = self.client_secrets.read(client_id) if str(client_id or "").isdigit() else ""
        except (ValueError, OSError) as exc:
            self.status.set(str(exc))
            return
        if not client_secret:
            from view.anilist_ui import setup_client
            client_id = setup_client(parent or self.settings.advanced_window or self.settings.root,
                                     initial_id=str(client_id or ""), secret_store=self.client_secrets)
            if not client_id:
                return
            self.config.set("ANILIST_CLIENT_ID", str(client_id))
            self.settings.persist_active_profile_fields({"ANILIST_CLIENT_ID": str(client_id)})
            client_secret = self.client_secrets.read(client_id)
        with self.lock:
            self.epoch += 1
            epoch, old = self.epoch, self.attempt
            self.credential = self.attempt = None
            self.watch = None
            self.restore_needed = False
        self.status.set("Anmeldung im Browser bestätigen…")
        def begin():
            if old:
                old.close()
            attempt = OAuthAttempt(client_id, client_secret)
            with self.lock:
                if self.epoch != epoch or self.closed.is_set():
                    attempt.close()
                    return
                self.attempt = attempt
            webbrowser.open(attempt.url)
        self.tasks.put(begin)

    def setup(self):
        from view.anilist_ui import setup_client
        value = setup_client(self.settings.advanced_window or self.settings.root,
                             initial_id=str(self.config.get("ANILIST_CLIENT_ID") or ""), secret_store=self.client_secrets)
        if value:
            self.config.set("ANILIST_CLIENT_ID", value)
            self.settings.persist_active_profile_fields({"ANILIST_CLIENT_ID": value})

    def disconnect(self):
        with self.lock:
            self.epoch += 1
            old = self.attempt
            self.credential = self.attempt = self.watch = None
            self.restore_needed = False
        self.status.set("Nicht verbunden · ausstehende Vorgänge bleiben ihrem Konto zugeordnet")
        try:
            self.store.delete()  # Local API only; do not leave logout queued behind a network timeout.
        except ValueError as exc:
            self.status.set(str(exc))
        def remove():
            if old:
                old.close()
        self.tasks.put(remove)

    def poll(self):
        try:
            while True:
                epoch, text = self.events.get_nowait()
                if epoch == self.epoch:
                    self.status.set(text)
        except queue.Empty:
            pass
        attempt = self.attempt
        if attempt:
            try:
                error = attempt.errors.get_nowait()
            except queue.Empty:
                pass
            else:
                self.attempt = None
                attempt.close()
                self.status.set(error)
                return
            try:
                token, expires = attempt.results.get_nowait()
                epoch = self.epoch
                self.attempt = None
                self.status.set("AniList-Konto wird geprüft…")
                def finish():
                    user = self.client.viewer(token)
                    saved = {"token": token, "expires": expires, "user_id": int(user["id"]), "name": user["name"]}
                    with self.lock:
                        if self.epoch != epoch or self.closed.is_set():
                            return
                        self.store.write(saved)
                        self.credential = saved
                    self._say("Verbunden: "+user["name"], epoch)
                self.tasks.put(finish)
            except queue.Empty:
                if time.monotonic()-attempt.created > 300:
                    self.attempt = None
                    self.status.set("Anmeldung nicht abgeschlossen · erneut verbinden")

    def observe(self, snapshot, event):
        binding = getattr(self.browser, "_progress_binding", None)
        credential = self.credential
        enabled = self.config.get("ANILIST_SYNC_PROGRESS")
        if (not credential or enabled is False or not binding or self.browser._capture or
                not self.browser._connected or not self.browser.following or
                snapshot.identity != self.browser.identity or
                binding["identity"] != snapshot.identity.wire() or
                binding["page_url"] != (self.browser._media_context or {}).get("pageUrl") or
                snapshot.duration <= 0 or snapshot.paused or snapshot.buffering or snapshot.seeking or
                event in {"seeking", "seeked", "waiting", "stalled", "emptied"}):
            self.watch = None
            return
        key = (credential["user_id"], binding["media_id"], binding["progress"],
               snapshot.identity, snapshot.fingerprint, binding["page_url"])
        now, position = snapshot.received, snapshot.current_time/snapshot.duration
        previous = self.watch
        continuous, run, crossing = False, 0.0, False
        if previous and previous["key"] == key:
            dt = now-previous["now"]
            advance = snapshot.current_time-previous["time"]
            continuous = (0 < dt <= .5 and 0 <= advance <= dt*max(snapshot.rate, previous["rate"])+.25)
            if continuous:
                run = previous["run"]+dt
                crossing = previous["position"] < .9 <= position and advance > 0
        self.watch = {"key": key, "now": now, "time": snapshot.current_time, "rate": snapshot.rate,
                      "position": position, "run": run}
        if position < .9 or not (crossing or continuous and run >= 10):
            return
        with self.lock:
            queue_key = f"{credential['user_id']}:{binding['media_id']}"
            if self.data["completed"].get(queue_key, 0) >= binding["progress"]:
                return
            old = self.data["pending"].get(queue_key)
            if old and old["progress"] >= binding["progress"]:
                return
            self.data["pending"][queue_key] = {"user_id": credential["user_id"],
                "media_id": binding["media_id"], "progress": binding["progress"], "episodes": binding.get("episodes")}
            count = sum(int(row["user_id"]) == int(credential["user_id"]) for row in self.data["pending"].values())
        self._say(f"Verbunden: {credential['name']} · {count} Aktualisierung(en) ausstehend")
        self.tasks.put(self._save)

    def _sync_one(self):
        credential, epoch = self.credential, self.epoch
        if (not credential or self.config.get("ANILIST_SYNC_PROGRESS") is False or
                time.monotonic() < self.retry_at):
            return
        if credential["expires"] <= time.time():
            self.credential = None
            raise AniListAuthError("AniList-Anmeldung abgelaufen · erneut verbinden")
        with self.lock:
            pair = next(((key, dict(row)) for key, row in self.data["pending"].items()
                         if int(row["user_id"]) == int(credential["user_id"])), None)
        if not pair:
            return
        key, row = pair
        self._save()  # Durable account-owned work precedes any remote mutation.
        media = self.client.progress(credential["token"], row["media_id"])
        entry = media.get("mediaListEntry") or {}
        remote = int(entry.get("progress") or 0)
        desired = max(remote, int(row["progress"]))
        total = media.get("episodes") or row.get("episodes")
        if total and int(row["progress"]) > int(total) and remote < int(row["progress"]):
            raise AniListError("Folgenzuordnung überschreitet die AniList-Folgenzahl. Map prüfen.", 300)
        if desired > remote:
            status = "COMPLETED" if total and desired >= int(total) else "CURRENT"
            if entry.get("status") == "COMPLETED":
                status = "COMPLETED"
            if (epoch != self.epoch or self.credential is not credential or self.closed.is_set()
                    or self.config.get("ANILIST_SYNC_PROGRESS") is False):
                return
            saved = self.client.save_progress(credential["token"], row["media_id"], desired, status)
            if int(saved.get("progress") or 0) < desired:
                raise AniListError("AniList hat den Fortschritt noch nicht bestätigt.")
        with self.lock:
            self.data["completed"][key] = max(self.data["completed"].get(key, 0), desired)
            current = self.data["pending"].get(key)
            if current and current["progress"] <= row["progress"]:
                self.data["pending"].pop(key, None)
        self._save()
        self._say(f"Verbunden: {credential['name']} · Fortschritt {desired} gespeichert", epoch)

    def _worker(self):
        while not self.closed.is_set():
            epoch = self.epoch
            try:
                try:
                    job = self.tasks.get(timeout=.5)
                    job()
                except queue.Empty:
                    pass
                if self.restore_needed and time.monotonic() >= self.retry_at:
                    self._load()
                self._sync_one()
            except AniListAuthError as exc:
                self.credential = None
                self.restore_needed = False
                self._say(str(exc), epoch)
            except Exception as exc:
                self.retry_at = time.monotonic()+getattr(exc, "retry_after", 60)
                suffix = " · lokales Speichern fehlgeschlagen" if isinstance(exc, OSError) else " · ausstehende Vorgänge bleiben gespeichert"
                self._say("AniList: "+str(exc)+suffix, epoch)
                logger.warning("AniList operation deferred (%s)", type(exc).__name__)

    def choose_list(self, parent):
        if not self.credential:
            self.connect(parent)
            return None
        from view.anilist_ui import choose_user_anime
        selected = choose_user_anime(parent, self)
        if selected:
            title = selected["title"]
            return self.manager.choose_jimaku_source(parent, query=title.get("romaji") or title.get("english") or title.get("native"),
                                                     anilist_id=selected["id"])
        return None

    def close(self):
        self.closed.set()
        self.epoch += 1
        try:
            self._save()
        except OSError:
            logger.warning("Could not persist pending AniList updates during shutdown")
        if self.attempt:
            self.attempt.close()
