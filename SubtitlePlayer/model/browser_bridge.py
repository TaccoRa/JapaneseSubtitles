"""Authenticated loopback-only transport. Workers never call Tk directly."""
import hmac
import json
import queue
import secrets
import threading
import uuid

from model.browser_protocol import PROTOCOL_VERSION


class BrowserBridge:
    def __init__(self, token=None, port=8767):
        self.token = token or secrets.token_urlsafe(32)
        self.port = port
        self.events = queue.Queue(maxsize=200)
        self._peer = None
        self._server = None
        self._lock = threading.RLock()
        self.ready = threading.Event()
        self.error = None

    def start(self):
        def run():
            try:
                from websockets.sync.server import serve
                with serve(self._accept, "127.0.0.1", self.port, max_size=32 * 1024 * 1024,
                           ping_interval=10, ping_timeout=10) as server:
                    self._server = server
                    self.port = server.socket.getsockname()[1]
                    self.ready.set()
                    server.serve_forever()
            except Exception as exc:
                self.error = str(exc)
                self.ready.set()
        threading.Thread(target=run, name="browser-bridge", daemon=True).start()

    def _emit(self, event):
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
            if hello.get("type") != "Hello" or hello.get("version") != PROTOCOL_VERSION or not hmac.compare_digest(str(hello.get("token", "")), self.token):
                peer.close(1008, "Pairing required")
                return
            with self._lock:
                if self._peer is not None:
                    peer.close(1008, "Already connected")
                    return
                self._peer = peer
                authenticated = True
            peer.send(json.dumps({"type": "Welcome", "version": PROTOCOL_VERSION}))
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
        with self._lock:
            if self._peer is None:
                raise ConnectionError("Chrome is disconnected")
            self._peer.send(json.dumps(message))

    def command(self, identity, action, **arguments):
        operation = uuid.uuid4().hex
        self.send(dict(type="PlayerCommand", operationId=operation,
                       identity=identity.wire(), action=action, **arguments))
        return operation

    def close(self):
        with self._lock:
            if self._peer:
                self._peer.close()
        if self._server:
            self._server.shutdown()
