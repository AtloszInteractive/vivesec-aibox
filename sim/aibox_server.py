"""AIBox HTTP server — serves the ViVeSec `/api/v1/index/*` sync contract,
plus a `/api/v1/ui/query` endpoint (drive-prefix hard filter demo) and
`/api/v1/status` (presence watchdog / lock state).

stdlib-only (http.server) so it runs on the embeddable Python on Windows and on
arm64 Python 3.8 on the Jetson with nothing installed.

Run:
    python sim/aibox_server.py
Env:
    AIBOX_HOST (default 127.0.0.1)
    AIBOX_PORT (default 8088)
    AIBOX_INDEX_PATH (optional JSON persistence; default in-memory)
    plus poc vars (VIVESEC_EMBED_MODEL, OLLAMA_URL, VIVESEC_BACKEND, ...)

Endpoints (all POST + JSON unless noted):
    GET  /api/v1/status
    POST /api/v1/index/get                      {path}
    POST /api/v1/index/get/children             {path}
    POST /api/v1/index/upsert/directory         {path}
    POST /api/v1/index/upsert/file/check        {path,size,mtime,head}
    POST /api/v1/index/upsert/file/content/{token}   (body = raw file bytes)
    POST /api/v1/index/drop/tree                {path,keep_exact}
    POST /api/v1/ui/query                       {query,top_k}  + VVS-Drive header
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from aibox_index import AIBoxIndex, norm  # noqa: E402

_POC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc")
if _POC not in sys.path:
    sys.path.insert(0, _POC)
import config  # noqa: E402
import embeddings  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HOST = os.environ.get("AIBOX_HOST", "127.0.0.1")
PORT = int(os.environ.get("AIBOX_PORT", "8088"))

INDEX = AIBoxIndex(persist_path=os.environ.get("AIBOX_INDEX_PATH") or None)

# Presence watchdog: the ViVeSecBox must poll /status periodically; if it stops
# (60-90s) the real box LOCKS the LUKS volume. The mock only tracks the gap.
_LAST_STATUS_TS = [time.time()]
WATCHDOG_SECONDS = int(os.environ.get("AIBOX_WATCHDOG_SECONDS", "90"))

CONTENT_PREFIX = "/api/v1/index/upsert/file/content/"


def _drive_prefix(headers):
    """Read the mandatory VVS-Drive header and normalize it for prefix match.
    Drive names may contain spaces ('beta dev 2') -> never tokenize."""
    raw = headers.get("VVS-Drive")
    return norm(raw) if raw else None


class Handler(BaseHTTPRequestHandler):
    server_version = "AIBox/0.1"

    # -- helpers ---------------------------------------------------------
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _read_raw(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        return self.rfile.read(length) if length else b""

    # -- GET -------------------------------------------------------------
    def do_GET(self):
        if self.path.rstrip("/") == "/api/v1/status":
            _LAST_STATUS_TS[0] = time.time()
            self._send(200, {"ok": True, "locked": False,
                             "watchdog_seconds": WATCHDOG_SECONDS,
                             "index": INDEX.stats()})
            return
        if self.path in ("/", ""):
            self._send(200, {"ok": True, "service": "AIBox sync+query (mock)",
                             "index": INDEX.stats()})
            return
        self._send(404, {"ok": False, "error": "Not found: %s" % self.path})

    # -- POST ------------------------------------------------------------
    def do_POST(self):
        path = self.path
        try:
            if path.startswith(CONTENT_PREFIX):
                return self._content(path[len(CONTENT_PREFIX):])

            route = {
                "/api/v1/index/get": self._get_doc,
                "/api/v1/index/get/children": self._get_children,
                "/api/v1/index/upsert/directory": self._upsert_dir,
                "/api/v1/index/upsert/file/check": self._check,
                "/api/v1/index/drop/tree": self._drop_tree,
                "/api/v1/ui/query": self._query,
            }.get(path.rstrip("/"))
            if route is None:
                self._send(404, {"ok": False, "error": "Not found: %s" % path})
                return
            route()
        except Exception as e:  # noqa: BLE001 - report, don't crash the box
            self._send(500, {"ok": False, "error": str(e)})

    # -- /index/* handlers ----------------------------------------------
    def _get_doc(self):
        payload = self._read_json()
        self._send(200, {"ok": True, "document": INDEX.get(payload.get("path", ""))})

    def _get_children(self):
        payload = self._read_json()
        self._send(200, {"ok": True,
                         "children": INDEX.get_children(payload.get("path", ""))})

    def _upsert_dir(self):
        payload = self._read_json()
        INDEX.upsert_directory(payload.get("path", ""))
        self._send(200, {"ok": True})

    def _check(self):
        payload = self._read_json()
        token = INDEX.check(payload.get("path", ""), payload.get("size"),
                            payload.get("mtime"), payload.get("head"))
        self._send(200, {"ok": True, "token": token})

    def _content(self, token):
        raw = self._read_raw()
        try:
            n = INDEX.commit_content(token, raw)
        except KeyError as e:
            self._send(404, {"ok": False, "error": str(e)})
            return
        self._send(200, {"ok": True, "chunks": n})

    def _drop_tree(self):
        payload = self._read_json()
        removed = INDEX.drop_tree(payload.get("path", ""),
                                  bool(payload.get("keep_exact", False)))
        self._send(200, {"ok": True, "removed": removed})

    # -- /ui/query (ACL hard filter demo) -------------------------------
    def _query(self):
        payload = self._read_json()
        drive = _drive_prefix(self.headers)
        if not drive:
            self._send(400, {"ok": False, "error": "Missing VVS-Drive header"})
            return
        query = (payload.get("query") or "").strip()
        if not query:
            self._send(400, {"ok": False, "error": "Missing 'query'"})
            return
        top_k = int(payload.get("top_k") or config.TOP_K)
        use_ol = embeddings.use_ollama_embed()
        qvec = embeddings.embed_one(query, use_ol)
        hits = INDEX.search(qvec, top_k, drive_prefix=drive)
        out = [{"rank": i, "path": rec["path"], "chunk": rec["chunk_index"],
                "score": round(float(score), 4),
                "snippet": " ".join((rec["text"] or "")[:200].split())}
               for i, (score, rec) in enumerate(hits, 1)]
        self._send(200, {"ok": True, "drive": drive,
                         "user": self.headers.get("VVS-User", ""), "hits": out})

    def log_message(self, fmt, *args):
        sys.stderr.write("[aibox] %s\n" % (fmt % args))


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("AIBox sync+query (mock) on http://%s:%d" % (HOST, PORT))
    print("  Index persistence:", INDEX.persist_path or "(in-memory)")
    print("  Embed backend    :", embeddings.backend_name(embeddings.use_ollama_embed()))
    print("  Watchdog         :", WATCHDOG_SECONDS, "s")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
