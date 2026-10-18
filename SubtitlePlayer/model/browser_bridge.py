"""Authenticated loopback-only transport. Workers never call Tk directly."""
import hmac
import errno
import json
import queue
import re
import secrets
import threading
import time
import uuid

from model.browser_protocol import PROTOCOL_VERSION


class BrowserBridge:
    def __init__(self, token=None, port=8767, trusted_origins=()):
        self.token = token or secrets.token_urlsafe(32)
        self.port = port
        self.events = queue.Queue(maxsize=200)
        self._peer = None
        self._server = None
        self._lock = threading.RLock()
        self.ready = threading.Event()
        self.error = None
        self.trusted_origins = set(trusted_origins)
        self._pairing = {}
        self._thread = None
        self._stop = threading.Event()

    def approve_pairing(self, request_id, allowed):
        with self._lock:
            request = self._pairing.get(request_id)
            if request is not None:
                request["allowed"] = bool(allowed)
                request["ready"].set()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self.ready.clear()
        self.error = None
        self._stop.clear()
        def run():
            try:
                from websockets.sync.server import serve
                with serve(self._accept, "127.0.0.1", self.port, max_size=32 * 1024 * 1024,
                           ping_interval=10, ping_timeout=10) as server:
                    with self._lock:
                        self._server = server
                    if self._stop.is_set():
                        return
                    self.port = server.socket.getsockname()[1]
                    self.ready.set()
                    server.serve_forever()
            except Exception as exc:
                if isinstance(exc, OSError) and (exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == 10048):
                    self.error = f"Port {self.port} is in use. Close the other SubtitlePlayer instance, then click Retry connection."
                else:
                    self.error = str(exc)
            finally:
                with self._lock:
                    self._server = None
                self.ready.set()
        self._thread = threading.Thread(target=run, name="browser-bridge", daemon=True)
        self._thread.start()

    def _emit(self, event):
        event = {**event, "_received_monotonic": time.monotonic()}
        try:
            self.events.put_nowait(event)
        except queue.Full:
            # Clock samples may be dropped. Completion/ack/error messages may not.
            if event.get("type") == "PlaybackSnapshot":
                return
            self.events.put(event, timeout=2)

    def _accept(self, peer):
        authenticated = False
        try:
            origin = peer.request.headers.get("Origin", "")
            if origin and not origin.startswith("chrome-extension://"):
                peer.close(1008, "Extension origin required")
                return
            hello = json.loads(peer.recv(timeout=5))
            valid = hmac.compare_digest(str(hello.get("token", "")), self.token)
            pairing = False
            if hello.get("type") == "Hello" and hello.get("version") == PROTOCOL_VERSION and not valid and hello.get("pair") and re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
                pairing = True
                if origin in self.trusted_origins:
                    valid = True
                else:
                    request_id = uuid.uuid4().hex
                    request = {"ready": threading.Event(), "allowed": False}
                    with self._lock:
                        if self._peer is not None or self._pairing:
                            peer.close(1008, "Connection or pairing already active")
                            return
                        self._pairing[request_id] = request
                    self._emit(dict(type="PairingRequested", requestId=request_id, origin=origin))
                    request["ready"].wait(30)
                    with self._lock:
                        self._pairing.pop(request_id, None)
                    valid = request["allowed"]
            if hello.get("type") != "Hello" or hello.get("version") != PROTOCOL_VERSION or not valid:
                peer.close(1008, "Pairing required")
                return
            with self._lock:
                if self._peer is not None:
                    peer.close(1008, "Already connected")
                    return
                self._peer = peer
                authenticated = True
            welcome = {"type": "Welcome", "version": PROTOCOL_VERSION}
            if pairing:
                welcome["token"] = self.token
            peer.send(json.dumps(welcome))
            if re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
                self.trusted_origins.add(origin)
                self._emit(dict(type="TrustedOrigin", origin=origin))
            self._emit({"type": "Connected"})
            for raw in peer:
                value = json.loads(raw)
                if isinstance(value, dict) and isinstance(value.get("type"), str):
                    self._emit(value)
        except Exception:
            pass
        finally:
            if authenticated:
                with self._lock:
                    if self._peer is peer:
                        self._peer = None
                self._emit({"type": "Disconnected"})

    def send(self, message):
        from websockets.exceptions import ConnectionClosed
        payload = json.dumps(message)
        with self._lock:
            if self._peer is None:
                raise ConnectionError("Chrome is disconnected")
            try:
                self._peer.send(payload)
            except (ConnectionClosed, OSError) as exc:
                raise ConnectionError("Chrome is disconnected") from exc

    def command(self, identity, action, **arguments):
        operation = uuid.uuid4().hex
        self.send(dict(type="PlayerCommand", operationId=operation,
                       identity=identity.wire(), action=action, **arguments))
        return operation

    def close(self):
        self._stop.set()
        with self._lock:
            for request in self._pairing.values():
                request["ready"].set()
            peer, server = self._peer, self._server
        if peer:
            peer.close()
        if server:
            server.shutdown()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
