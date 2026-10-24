"""GPU priority for the shared model server (E26).

The box has one GPU and one Ollama; the adapter's own model calls (chat,
reports, analysis) are what a user is waiting for, the graph extraction is
background work. Background callers reach the model through a small broker on
a loopback-only port instead of calling Ollama directly; the broker lets one
background call through at a time, and only when no foreground call is
running and none ended within the last ADAPTER_GPU_GRACE_SECONDS (a chat turn
makes several calls in a row). A background call that has waited
ADAPTER_GPU_MAX_WAIT_SECONDS goes anyway, so extraction never starves.

A background call already running is not interrupted: a chat that arrives
meanwhile waits for it inside Ollama (one extraction call, typically 10-60 s).
"""
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROXIED = frozenset(("/api/chat", "/api/generate", "/v1/chat/completions"))
MAX_BODY = 16 * 1024 * 1024


class GpuGate:
    def __init__(self, grace_seconds=2.0, max_wait_seconds=240.0, clock=time.monotonic):
        self.grace = grace_seconds
        self.max_wait = max_wait_seconds
        self.clock = clock
        self.cond = threading.Condition()
        self.foreground_active = 0
        self.last_foreground_end = float("-inf")
        self.background_active = False
        self.waiting = 0
        self.counts = {"foreground": 0, "background": 0, "background_forced": 0}

    @classmethod
    def from_env(cls):
        return cls(float(os.environ.get("ADAPTER_GPU_GRACE_SECONDS", "2") or 0),
                   float(os.environ.get("ADAPTER_GPU_MAX_WAIT_SECONDS", "240") or 0))

    @contextmanager
    def foreground(self):
        with self.cond:
            self.foreground_active += 1
            self.counts["foreground"] += 1
        try:
            yield
        finally:
            with self.cond:
                self.foreground_active -= 1
                self.last_foreground_end = self.clock()
                self.cond.notify_all()

    def _idle(self):
        return (self.foreground_active == 0
                and self.clock() - self.last_foreground_end >= self.grace)

    @contextmanager
    def background(self):
        deadline = self.clock() + self.max_wait
        forced = False
        with self.cond:
            self.waiting += 1
            try:
                while True:
                    if not self.background_active and self._idle():
                        break
                    if not self.background_active and self.clock() >= deadline:
                        forced = True
                        break
                    self.cond.wait(0.25)
            finally:
                self.waiting -= 1
            self.background_active = True
            self.counts["background_forced" if forced else "background"] += 1
        try:
            yield
        finally:
            with self.cond:
                self.background_active = False
                self.cond.notify_all()

    def stats(self):
        with self.cond:
            return {"foreground_active": self.foreground_active,
                    "background_active": self.background_active,
                    "background_waiting": self.waiting, "counts": dict(self.counts)}


# One gate per process: llm.py marks foreground calls, the broker waits on it.
GATE = GpuGate.from_env()


def make_broker_handler(gate, upstream, timeout=900.0):
    """HTTP handler forwarding model calls to `upstream` behind gate.background()."""

    class BrokerHandler(BaseHTTPRequestHandler):
        server_version = "ViVeSecGpuBroker/1.0"

        def _reply(self, status, data, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _error(self, status, message):
            self._reply(status, json.dumps({"error": message}).encode("utf-8"))

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            if self.path == "/health":
                return self._reply(200, json.dumps(dict(gate.stats(), ok=True)).encode())
            self._error(404, "not found")

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            if path not in PROXIED:
                return self._error(404, "not found")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                return self._error(413, "body missing or too large")
            body = self.rfile.read(length)
            try:
                if json.loads(body.decode("utf-8")).get("stream"):
                    return self._error(400, "streaming is not supported by the broker")
            except (ValueError, UnicodeDecodeError, AttributeError):
                return self._error(400, "invalid JSON body")
            req = urllib.request.Request(upstream + path, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            with gate.background():
                try:
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        status, data = resp.status, resp.read()
                        ctype = resp.headers.get("Content-Type", "application/json")
                except urllib.error.HTTPError as e:
                    status, data = e.code, e.read()
                    ctype = e.headers.get("Content-Type", "application/json")
                except (urllib.error.URLError, OSError) as e:
                    return self._error(502, "model server unreachable: %s" % type(e).__name__)
            self._reply(status, data, ctype)

    return BrokerHandler


def start_broker(gate, upstream, port):
    """Serve the broker on 127.0.0.1:port (never another interface)."""
    server = ThreadingHTTPServer(("127.0.0.1", port), make_broker_handler(gate, upstream))
    threading.Thread(target=server.serve_forever, name="gpu-broker", daemon=True).start()
    sys.stderr.write("[adapter] gpu broker on 127.0.0.1:%d -> %s\n"
                     % (server.server_port, upstream))
    return server
