"""Tiny stdlib HTTP bridge exposing the RAG PoC to the ViVeSec frontend.

No third-party dependencies (matches the PoC's stdlib-only philosophy) — just
http.server. It wraps rag.answer() so the React app can switch from its mock
responses to real, locally-generated answers.

Run:
    python server.py
Env overrides:
    VIVESEC_HOST  (default 127.0.0.1)
    VIVESEC_PORT  (default 8000)
plus all the usual PoC vars (VIVESEC_GEN_MODEL, OLLAMA_URL, ...).

Endpoints:
    GET  /api/health           -> backend status for the UI toggle
    POST /api/ask {query,lang}  -> {answer, mode, backend, citations[...]}
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import commands
import config
import embeddings
import llm
import ollama_client as ollama
import rag

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HOST = os.environ.get("VIVESEC_HOST", "127.0.0.1")
PORT = int(os.environ.get("VIVESEC_PORT", "8000"))

# UI language code -> instruction passed to the model, so the answer always
# matches the language selected in the frontend regardless of corpus language.
LANG_MAP = {"EN": "English", "HU": "Hungarian", "DA": "Danish", "DE": "German"}

# rag.answer() mutates no shared state, but we set config.ANSWER_LANG per
# request; serialize generation so concurrent requests can't race on it.
_GEN_LOCK = threading.Lock()


def _lang_name(value):
    if not value:
        return ""
    return LANG_MAP.get(str(value).strip().upper(), str(value).strip())


def _load_manifest():
    """Map each ingested corpus source (e.g. 'Q4_Financials.txt') to the
    DriveFile shown in the UI {fileId, name}. Lets live citations open the right
    document. Lives next to the data dir; missing/invalid -> empty (graceful)."""
    path = os.path.join(config.DATA_DIR, "manifest.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


MANIFEST = _load_manifest()


def _to_citations(hits):
    out = []
    for rank, (score, rec) in enumerate(hits, 1):
        snippet = " ".join((rec.get("text") or "")[:240].split())
        source = rec.get("source")
        info = MANIFEST.get(source, {})
        out.append({
            "rank": rank,
            "source": source,
            "fileId": info.get("fileId", ""),   # real DriveFile id -> citation opens the file
            "chunk": rec.get("chunk_index"),
            "score": round(float(score) * 100),   # 0..100 like the mock UI
            "label": info.get("name", source),    # nice display name (falls back to source)
            "snippet": snippet,
        })
    return out


def _synth_search_answer(citations, sources):
    """Retrieval-only commands return answer=None; build a short, faithful
    summary line so the UI always has something to show alongside citations."""
    if not citations:
        return "No local matches found in the on-device corpus."
    top = citations[0]
    return (
        "Resolved across %d matching passage%s in the local corpus. Strongest "
        "match (%d%% relevance) is in %s: \"%s\"." % (
            len(citations), "" if len(citations) == 1 else "s",
            top["score"], top["label"], top["snippet"][:180],
        )
    )


def build_health():
    running = ollama.available()
    gen_ok = llm.use_ollama_gen()
    store = rag.load_store()
    index_ready = store is not None
    embed_backend = ""
    if index_ready:
        embed_backend = store.meta.get("embed_backend", "")
    return {
        "ok": True,
        "ollama_running": running,
        "llm_available": bool(gen_ok),
        "gen_model": config.GEN_MODEL,
        "embed_model": config.EMBED_MODEL,
        "embed_backend": embed_backend,
        "index_ready": index_ready,
        "mode": "rag-generate" if gen_ok else "extractive-fallback",
    }


def handle_ask(payload):
    query = (payload.get("query") or "").strip()
    if not query:
        return 400, {"ok": False, "error": "Missing 'query'."}

    lang = _lang_name(payload.get("lang"))
    with _GEN_LOCK:
        prev = config.ANSWER_LANG
        config.ANSWER_LANG = lang
        try:
            result = rag.answer(query)
        finally:
            config.ANSWER_LANG = prev

    if "error" in result:
        return 200, {"ok": False, "error": result["error"]}

    citations = _to_citations(result.get("hits", []))
    answer = result.get("answer")
    if answer is None:  # retrieval-only (/search)
        answer = _synth_search_answer(citations, result.get("sources", []))

    return 200, {
        "ok": True,
        "command": result.get("command"),
        "label": result.get("label"),
        "mode": result.get("mode"),
        "backend": result.get("llm_backend", ""),
        "answer": answer,
        "sources": result.get("sources", []),
        "citations": citations,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "ViVeSecRAG/0.1"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") in ("/api/health", "/health"):
            try:
                self._send(200, build_health())
            except Exception as e:  # noqa: BLE001 - report, don't crash the box
                self._send(500, {"ok": False, "error": str(e)})
            return
        if self.path in ("/", ""):
            self._send(200, {"ok": True, "service": "ViVeSec RAG bridge",
                             "endpoints": ["GET /api/health", "POST /api/ask"]})
            return
        self._send(404, {"ok": False, "error": "Not found: %s" % self.path})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/api/ask", "/ask"):
            self._send(404, {"ok": False, "error": "Not found: %s" % self.path})
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(length) if length else b""
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError) as e:
            self._send(400, {"ok": False, "error": "Invalid JSON: %s" % e})
            return
        try:
            code, obj = handle_ask(payload)
            self._send(code, obj)
        except Exception as e:  # noqa: BLE001
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, fmt, *args):  # concise one-line logging
        sys.stderr.write("[bridge] %s\n" % (fmt % args))


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    h = build_health()
    print("ViVeSec RAG bridge on http://%s:%d" % (HOST, PORT))
    print("  Ollama running :", h["ollama_running"])
    print("  LLM available  :", h["llm_available"], "(", h["gen_model"], ")")
    print("  Index ready    :", h["index_ready"], "| embed:", h["embed_backend"] or "(none)")
    print("  Mode           :", h["mode"])
    if not h["index_ready"]:
        print("  WARNING: no index. Run:  python cli.py ingest")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
