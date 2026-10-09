"""HTTP service implementing the CoLearn drive-sync / RAG contract.

This is a drop-in stand-in for CoLearn's PageIndexes service: it speaks the
SAME HTTP contract (`drive_sync_api_spec.txt`) so it can be swapped behind the
adapter for the real implementation without the adapter noticing.

Stdlib-only HTTP layer (ThreadingHTTPServer) on top of `RagStore`; the heavy
lifting (extraction, embeddings) lives in extract.py / store.py.

Contract summary:
  ops       GET  /health  /ready  /stats
  sync      POST /index/upsert/directory
            POST /index/upsert/file/check
            POST /index/upsert/file/content/{token}
            POST /index/upsert/file/content        (single-step, content_b64)
            POST /ingest
            POST /index/rebuild
  delete    POST /index/drop/tree
  retrieval POST /rag/search_context
            POST /tools/rag_search_context_tool

Auth: optional X-API-Key (env RAG_API_KEY). When unset the service runs open
(it is only reachable through the adapter, never exposed externally).

Responses: {"ok": true, ...} / {"ok": false, "error": "..."}.
Errors: 400 bad input, 401 unauthorized, 409 embedding conflict, 503 runtime
unavailable, 500 otherwise.
"""
import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_info  # noqa: E402
from store import (  # noqa: E402
    MIN_SCORE,
    REACCENT_ACTIVE,
    RagStore,
    EmbeddingConflict,
    norm_path,
    normalize_corpus_ids,
)

HOST = os.environ.get("RAG_HOST", "127.0.0.1")
PORT = int(os.environ.get("RAG_PORT", "8090"))
API_KEY = os.environ.get("RAG_API_KEY", "")
# Backend: "json" (in-memory dicts + JSON file, PoC scale) or "sqlite"
# (SQLite metadata + sqlite-vec KNN). Both expose the same store interface.
STORE_BACKEND = os.environ.get("RAG_STORE_BACKEND", "json").lower()
_DEFAULT_INDEX = "rag_index.db" if STORE_BACKEND == "sqlite" else "rag_index.json"
INDEX_PATH = os.environ.get("RAG_INDEX_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), _DEFAULT_INDEX))
DEFAULT_TENANT = os.environ.get("RAG_DEFAULT_TENANT", "default")

if STORE_BACKEND == "sqlite":
    from sqlite_store import SqliteVecStore  # noqa: E402
    STORE = SqliteVecStore(persist_path=INDEX_PATH)
    VECTOR_BACKEND = "sqlite-vec"
else:
    STORE = RagStore(persist_path=INDEX_PATH)
    VECTOR_BACKEND = "in-memory-json"


class HttpError(Exception):
    def __init__(self, status, error):
        super().__init__(error)
        self.status = status
        self.error = error


class Handler(BaseHTTPRequestHandler):
    server_version = "ViVeSecRAG/1.0"

    # ----- low-level helpers -------------------------------------------------
    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, **kw):
        out = {"ok": True}
        out.update(kw)
        self._send(200, out)

    def _fail(self, status, error):
        if status == 401:
            self._send(401, {"detail": "Unauthorized"})
        else:
            self._send(status, {"ok": False, "error": error})

    def _check_auth(self):
        if not API_KEY:
            return
        if self.headers.get("X-API-Key") != API_KEY:
            raise HttpError(401, "Unauthorized")

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _read_json(self):
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "invalid JSON body")

    def _tenant(self, body):
        return body.get("tenant_id") or DEFAULT_TENANT

    def log_message(self, fmt, *args):  # silence default stderr spam
        return

    # ----- routing -----------------------------------------------------------
    def do_GET(self):
        try:
            path = self.path.split("?", 1)[0]
            if path == "/health":
                return self._ok(
                    status="ok",
                    version=build_info.BUILD,
                    embedding=STORE.embedding_info(),
                    vector_backend=VECTOR_BACKEND,
                    min_score=MIN_SCORE,
                    query_reaccent=REACCENT_ACTIVE,
                )
            if path == "/ready":
                return self._ok(ready=True)
            if path == "/stats":
                return self._ok(stats=STORE.stats(), version=build_info.BUILD)
            raise HttpError(404, "not found")
        except HttpError as e:
            self._fail(e.status, e.error)
        except Exception as e:  # noqa: BLE001
            self._fail(500, str(e))

    def do_POST(self):
        try:
            self._check_auth()
            path = self.path.split("?", 1)[0]
            handler = self._routes().get(path)
            if handler is None and path.startswith("/index/upsert/file/content/"):
                return self._content_with_token(path)
            if handler is None:
                raise HttpError(404, "not found")
            return handler()
        except HttpError as e:
            self._fail(e.status, e.error)
        except EmbeddingConflict as e:
            self._fail(409, str(e))
        except Exception as e:  # noqa: BLE001
            self._fail(500, str(e))

    def _routes(self):
        return {
            "/index/upsert/directory": self._upsert_directory,
            "/index/upsert/file/check": self._upsert_check,
            "/index/upsert/file/content": self._content_single_step,
            "/ingest": self._ingest,
            "/index/rebuild": self._rebuild,
            "/index/drop/tree": self._drop_tree,
            "/index/skipped": self._skipped,
            "/rag/search_context": self._search_context,
            "/rag/document_context": self._document_context,
            "/tools/rag_search_context_tool": self._search_context,
        }

    # ----- sync endpoints ----------------------------------------------------
    def _skipped(self):
        """Files that are known but cannot be answered from, with the reason.

        Authenticated, unlike /stats, because it returns document paths. This is
        how an operator finds the scanned PDFs that need OCR instead of them
        silently never appearing in an answer.
        """
        body = self._read_json()
        docs = STORE.skipped_documents(body.get("corpus_id"))
        self._ok(skipped=docs, count=len(docs))

    def _upsert_directory(self):
        body = self._read_json()
        corpus_id = body.get("corpus_id")
        path = body.get("path")
        if not corpus_id or not path:
            raise HttpError(400, "corpus_id and path are required")
        res = STORE.upsert_directory(corpus_id, self._tenant(body), path)
        self._ok(**res)

    def _upsert_check(self):
        body = self._read_json()
        corpus_id = body.get("corpus_id")
        path = body.get("path")
        if not corpus_id or not path:
            raise HttpError(400, "corpus_id and path are required")
        token, reason = STORE.check(
            corpus_id,
            self._tenant(body),
            path,
            body.get("size"),
            body.get("mtime"),
            body.get("head"),
        )
        if token is None:
            self._ok(token=None, reason=reason)
        else:
            self._ok(token=token, token_ttl_seconds=STORE.token_ttl_seconds)

    def _content_with_token(self, path):
        token = path[len("/index/upsert/file/content/"):]
        if not token:
            raise HttpError(400, "missing token")
        raw = self._read_body()
        if not raw:
            raise HttpError(400, "empty body")
        res = STORE.commit_content(token, raw)
        if res is None:
            raise HttpError(400, "invalid_or_expired_upload_token")
        self._ok(**res)

    def _content_single_step(self):
        body = self._read_json()
        corpus_id = body.get("corpus_id")
        path = body.get("path")
        content_b64 = body.get("content_b64")
        if not corpus_id or not path or content_b64 is None:
            raise HttpError(400, "corpus_id, path and content_b64 are required")
        try:
            raw = base64.b64decode(content_b64)
        except Exception:  # noqa: BLE001
            raise HttpError(400, "content_b64 is not valid base64")
        res = STORE.ingest_content(
            corpus_id, self._tenant(body), path, body.get("title"), raw, body.get("metadata"),
        )
        self._ok(**res)

    def _ingest(self):
        body = self._read_json()
        corpus_id = body.get("corpus_id")
        path = body.get("path") or body.get("source_path")
        text = body.get("text")
        if not corpus_id or not path or text is None:
            raise HttpError(400, "corpus_id, path and text are required")
        res = STORE.ingest_text(
            corpus_id, self._tenant(body), path, body.get("title"), text, body.get("metadata"),
        )
        self._ok(**res)

    def _rebuild(self):
        body = self._read_json()
        corpus_id = body.get("corpus_id")
        if not corpus_id:
            raise HttpError(400, "corpus_id is required")
        if not body.get("clear"):
            raise HttpError(400, "rebuild requires clear=true")
        res = STORE.drop_tree(corpus_id, "/", keep_exact=False)
        self._ok(cleared=True, **res)

    # ----- delete ------------------------------------------------------------
    def _drop_tree(self):
        body = self._read_json()
        corpus_id = body.get("corpus_id")
        path = body.get("path")
        if not corpus_id or not path:
            raise HttpError(400, "corpus_id and path are required")
        res = STORE.drop_tree(corpus_id, path, bool(body.get("keep_exact", False)))
        self._ok(**res)

    # ----- retrieval ---------------------------------------------------------
    def _search_context(self):
        body = self._read_json()
        # `corpus_ids` is the multi-drive form; `corpus_id` stays valid so the
        # harness and every recorded eval run keep working unchanged.
        scope = normalize_corpus_ids(body.get("corpus_id"), body.get("corpus_ids"))
        question = body.get("question")
        if not scope or not question:
            raise HttpError(400, "corpus_id and question are required")
        top_k = int(body.get("top_k") or 3)
        max_tokens = int(body.get("max_context_tokens") or 4000)
        options = {}
        for key, limit in (("source_paths", 20), ("evidence_chunk_ids", 8)):
            value = body.get(key)
            if value is not None:
                if (not isinstance(value, list) or len(value) > limit
                        or any(not isinstance(item, str) or not item.strip() for item in value)):
                    raise HttpError(400, key + " must be a bounded list of nonempty strings")
                options[key] = value
        contexts, debug = STORE.search_context(scope, question, top_k, max_tokens, **options)
        out = {"contexts": contexts}
        if body.get("include_debug"):
            out["debug"] = debug
        self._ok(**out)

    def _document_context(self):
        """Whole-document context: every chunk of one named file, in reading
        order. Retrieval can only surface what matches the question, so an
        explicit 'analyse this file' request must not go through it.
        """
        body = self._read_json()
        corpus_id = body.get("corpus_id")
        source_path = body.get("source_path") or body.get("path")
        if not corpus_id or not source_path:
            raise HttpError(400, "corpus_id and source_path are required")
        max_tokens = int(body.get("max_context_tokens") or 12000)
        contexts, document = STORE.document_context(corpus_id, source_path, max_tokens)
        self._ok(contexts=contexts, document=document)


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("ViVeSec RAG service on http://%s:%d (auth=%s, index=%s)" % (
        HOST, PORT, "on" if API_KEY else "off", INDEX_PATH))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
