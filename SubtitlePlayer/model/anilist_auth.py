"""Windows credential storage and a one-use loopback OAuth code callback."""
import ctypes
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import queue
import re
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

REDIRECT_URI = "http://127.0.0.1:8768/anilist/callback"
DEVELOPER_URL = "https://anilist.co/settings/developer"
TARGET = "SubtitlePlayer/AniList"
TOKEN_URL = "https://anilist.co/api/v2/oauth/token"


class CredentialStore:
    def __init__(self, target=TARGET):
        self.target = target

    def _api(self):
        if os.name != "nt":
            raise ValueError("AniList-Anmeldung benötigt den Windows-Anmeldeinformationsspeicher.")

        class Credential(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        pointer = ctypes.POINTER(Credential)
        api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(pointer)]
        api.CredReadW.restype = wintypes.BOOL
        api.CredWriteW.argtypes = [pointer, wintypes.DWORD]
        api.CredWriteW.restype = wintypes.BOOL
        api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        api.CredDeleteW.restype = wintypes.BOOL
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree.restype = None
        return api, Credential

    def read(self):
        api, credential = self._api()
        pointer = ctypes.POINTER(credential)()
        if not api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == 1168:
                return None
            raise ValueError("Windows konnte die AniList-Anmeldung nicht lesen.")
        try:
            raw = ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize)
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else None
        finally:
            api.CredFree(pointer)

    def write(self, value):
        api, credential = self._api()
        raw = json.dumps(value).encode("utf-8")
        if len(raw) > 2560:
            raise ValueError("AniList-Anmeldung ist zu groß für den Windows-Speicher.")
        blob = (ctypes.c_ubyte*len(raw)).from_buffer_copy(raw)
        entry = credential(Type=1, TargetName=self.target, UserName=str(value["user_id"]),
                           CredentialBlobSize=len(raw), CredentialBlob=blob, Persist=2)
        if not api.CredWriteW(ctypes.byref(entry), 0):
            raise ValueError("Windows konnte die AniList-Anmeldung nicht speichern.")

    def delete(self):
        api, _ = self._api()
        if not api.CredDeleteW(self.target, 1, 0) and ctypes.get_last_error() != 1168:
            raise ValueError("Windows konnte die AniList-Anmeldung nicht entfernen.")


def validated_client_id(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,17}", value):
        raise ValueError("Die öffentliche AniList-Client-ID fehlt.")
    return value


class ClientSecretStore:
    """Client secrets stay outside config files, profiles and account tokens."""
    def __init__(self, factory=CredentialStore):
        self.factory = factory

    def read(self, client_id):
        client_id = validated_client_id(client_id)
        value = self.factory(target=TARGET + "/Client/" + client_id).read()
        return str((value or {}).get("client_secret") or "")

    def write(self, client_id, secret):
        client_id = validated_client_id(client_id)
        secret = str(secret or "").strip()
        if not 1 <= len(secret) <= 2000:
            raise ValueError("Bitte das AniList Client Secret eingeben.")
        self.factory(target=TARGET + "/Client/" + client_id).write(
            {"user_id": client_id, "client_secret": secret})


def exchange_authorization_code(client_id, client_secret, code, redirect_uri):
    try:
        response = requests.post(TOKEN_URL, json={
            "grant_type": "authorization_code", "client_id": client_id,
            "client_secret": client_secret, "redirect_uri": redirect_uri, "code": code},
            headers={"Accept": "application/json"}, timeout=(5, 20), allow_redirects=False)
    except requests.RequestException:
        raise ValueError("AniList ist momentan nicht erreichbar. Bitte erneut verbinden.") from None
    if response.status_code != 200:
        raise ValueError(f"AniList-Anmeldung fehlgeschlagen (HTTP {response.status_code}). "
                         "Client ID, Client Secret und Redirect URL in der Einrichtung prüfen.")
    try:
        value = response.json()
        token = value["access_token"]
        lifetime = int(value["expires_in"])
        if (not isinstance(token, str) or not 10 <= len(token) <= 4000
                or str(value.get("token_type", "Bearer")).lower() != "bearer"
                or not 1 <= lifetime <= 366*86400):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ValueError("AniList hat keine gültige Anmeldung zurückgegeben.") from None
    return token, time.time() + lifetime


class OAuthAttempt:
    def __init__(self, client_id, client_secret, *, port=8768, exchange=None):
        self.client_id = validated_client_id(client_id)
        if not isinstance(client_secret, str) or not client_secret.strip():
            raise ValueError("AniList Client Secret fehlt. Bitte die AniList-Einrichtung öffnen.")
        self._client_secret = client_secret.strip()
        self._exchange = exchange or exchange_authorization_code
        self.state = secrets.token_urlsafe(32)
        self.stop = threading.Event()
        self.cancelled = threading.Event()
        self.results = queue.Queue()
        self.errors = queue.Queue()
        self.created = time.monotonic()
        attempt = self
        nonce = secrets.token_urlsafe(20)

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, *_args):
                pass  # No OAuth URLs, tokens, request bodies or headers in logs.

            def send_body(self, code, content, content_type="text/plain; charset=utf-8"):
                body = content.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'nonce-{nonce}'; connect-src 'self'")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                request = urlsplit(self.path)
                if request.path != "/anilist/callback" or self.headers.get("Host") != attempt.host:
                    self.send_body(404, "Not found")
                    return
                try:
                    if len(request.query) > 16384:
                        raise ValueError()
                    values = parse_qs(request.query, keep_blank_values=True, max_num_fields=20)
                except ValueError:
                    self.send_body(400, "Invalid callback")
                    return
                states, codes = values.get("state", []), values.get("code", [])
                if (attempt.stop.is_set() or time.monotonic()-attempt.created > 300
                        or len(states) != 1 or not states[0].isascii()
                        or not secrets.compare_digest(states[0], attempt.state)):
                    self.send_body(400, "Invalid callback")
                    return
                if values.get("error"):
                    attempt.errors.put("AniList-Anmeldung wurde abgebrochen. Bitte erneut verbinden.")
                    attempt.stop.set()
                    self.send_body(200, "Anmeldung abgebrochen. Zurück zu SubtitlePlayer.")
                    return
                if len(codes) != 1 or not 1 <= len(codes[0]) <= 8192:
                    self.send_body(400, "Invalid callback")
                    return
                attempt.stop.set()  # A code is exchanged once, even if the browser retries.
                try:
                    result = attempt._exchange(attempt.client_id, attempt._client_secret,
                                               codes[0], attempt.redirect_uri)
                    if not attempt.cancelled.is_set():
                        attempt.results.put(result)
                    message = "Zurück zu SubtitlePlayer. Das Konto wird geprüft."
                except ValueError as exc:
                    attempt.errors.put(str(exc))
                    message = "Anmeldung fehlgeschlagen. Bitte die Meldung in SubtitlePlayer prüfen."
                except Exception:
                    attempt.errors.put("AniList-Anmeldung fehlgeschlagen. Bitte erneut verbinden.")
                    message = "Anmeldung fehlgeschlagen. Bitte zu SubtitlePlayer zurückkehren."
                self.send_body(200, '<!doctype html><meta charset="utf-8"><title>SubtitlePlayer</title><p>'
                    + message + '</p><script nonce="' + nonce + '">'
                    "history.replaceState(null,'','/anilist/callback');</script>", "text/html; charset=utf-8")

            def do_POST(self):
                self.send_body(405, "Method not allowed")

        try:
            self.server = HTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            raise ValueError("AniList-Rückrufport 8768 ist belegt. Vorherigen Login schließen und erneut versuchen.") from None
        self.server.timeout = .3
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.redirect_uri = f"http://{self.host}/anilist/callback"
        self.url = "https://anilist.co/api/v2/oauth/authorize?" + urlencode({
            "client_id": self.client_id, "response_type": "code", "redirect_uri": self.redirect_uri, "state": self.state})
        self.thread = threading.Thread(target=self._serve, name="anilist-oauth", daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            while not self.stop.is_set() and time.monotonic()-self.created < 300:
                self.server.handle_request()
        finally:
            self.server.server_close()
            self._client_secret = None

    def close(self):
        self.cancelled.set()
        self.stop.set()
        if threading.current_thread() is not self.thread:
            self.thread.join(1)
