"""Internal relationship-analysis service (E26).

Stdlib HTTP layer over a graph store, like rag_service. It is never exposed
externally: the adapter is the only caller, decides the caller's scope per
request and sends it with every query (corpus_ids + access).

  ops      GET  /health
  profile  POST /profile/get  /profile/templates  /profile/validate  /profile/set
  ingest   POST /graph/document/put  /graph/document/drop  /graph/document/acl
           POST /graph/document/drop_tree  /graph/acl/update (by path, as the RAG)
           POST /graph/entity/merge
  extract  POST /ingest/document (bytes, extracted in the background)  /ingest/status
           POST /review/list  /review/decide (possible same-person pairs)
  queries  POST /query/find  /query/entity  /query/path  /query/common
           POST /query/copresence  /query/shared  /query/timeline
           POST /query/neighborhood  /query/communities  /query/ranking

Env: GRAPH_HOST (127.0.0.1), GRAPH_PORT (8095), GRAPH_API_KEY (unset = open,
loopback only), GRAPH_PROFILE (template id or file, default "generic"),
GRAPH_PROFILE_PATH (where the administrator's profile is saved),
GRAPH_STORE ("memory" for tests and demos, "neo4j" for persistent use with
GRAPH_NEO4J_URI / _USER / _DATABASE and the password in GRAPH_NEO4J_PASSWORD_FILE).
Extraction: GRAPH_INGEST (on), GRAPH_INGEST_QUEUE_MB (512), GRAPH_REVIEW_PATH,
and the model / OCR settings GRAPH_LLM_* / GRAPH_OCR* (llm_client, text_source).
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domain_profile  # noqa: E402
import queries  # noqa: E402
from graph_store import MemoryGraphStore, StoreError  # noqa: E402
from ingest import IngestError, parse_document_payload  # noqa: E402
from model import CORE_TYPES  # noqa: E402
from visibility import Scope, ScopeError, parse_doc_acl  # noqa: E402

HOST = os.environ.get("GRAPH_HOST", "127.0.0.1")
PORT = int(os.environ.get("GRAPH_PORT", "8095"))
API_KEY = os.environ.get("GRAPH_API_KEY", "")
PROFILE_PATH = os.environ.get("GRAPH_PROFILE_PATH", "")
MAX_BODY = 64 * 1024 * 1024


class HttpError(Exception):
    def __init__(self, status, error):
        super().__init__(error)
        self.status, self.error = status, error


class State:
    def __init__(self, store, profile):
        self.store = store
        self.profile = profile
        self.lock = threading.Lock()
        self.worker = None  # ingest_jobs.IngestWorker when extraction is enabled

    def presence_types(self):
        return [r["id"] for r in self.profile["relation_types"] if r["presence"]]

    def holding_types(self):
        return [r["id"] for r in self.profile["relation_types"] if r["holding"]]


def initial_profile() -> dict:
    if PROFILE_PATH and os.path.exists(PROFILE_PATH):
        return domain_profile.load(PROFILE_PATH)
    return domain_profile.load(os.environ.get("GRAPH_PROFILE", "generic"))


def _int(body, name, default, low, high):
    value = body.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise HttpError(400, "%s must be an integer %d..%d" % (name, low, high))
    return value


def _id(body, name):
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise HttpError(400, "%s is required" % name)
    return value


def _types(body, name, default):
    value = body.get(name, default)
    if not isinstance(value, (list, tuple)) or any(t not in CORE_TYPES for t in value):
        raise HttpError(400, "%s must be a list of entity types" % name)
    return tuple(value)


def _doc_ids(value, out: set) -> set:
    if isinstance(value, dict):
        if isinstance(value.get("doc_id"), str):
            out.add(value["doc_id"])
        for key, v in value.items():
            if key == "documents" and isinstance(v, list):
                out.update(d for d in v if isinstance(d, str))
            else:
                _doc_ids(v, out)
    elif isinstance(value, list):
        for v in value:
            _doc_ids(v, out)
    return out


def run_query(state: State, name: str, body: dict) -> dict:
    try:
        scope = Scope.parse(body)
    except ScopeError as e:
        raise HttpError(400, str(e))
    view = state.store.view(scope, include_weak=bool(body.get("include_weak", False)))
    out = _run_query(state, view, name, body)
    documents = {}
    for doc_id in sorted(_doc_ids(out, set())):
        doc = view.document(doc_id)
        if doc is not None:
            documents[doc_id] = {"corpus_id": doc.corpus_id, "source_path": doc.source_path,
                                 "title": doc.title, "kind": doc.kind}
    out["documents"] = documents
    return out


def _run_query(state: State, view, name: str, body: dict) -> dict:
    if name == "find":
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise HttpError(400, "text is required")
        types = _types(body, "types", CORE_TYPES)
        return {"entities": queries.find_entities(view, text, set(types),
                                                  _int(body, "limit", 20, 1, 200))}
    if name == "entity":
        node = view.node(_id(body, "id"))
        if node is None:
            raise HttpError(404, "not found")
        return {"entity": node, "edges": view.edges_of(node["id"])[:queries.MAX_RESULTS]}
    if name == "path":
        return {"path": queries.shortest_path(view, _id(body, "a"), _id(body, "b"),
                                              _int(body, "max_hops", 6, 1, queries.MAX_HOPS))}
    if name == "common":
        return {"common": queries.common_neighbors(view, _id(body, "a"), _id(body, "b"))}
    if name == "copresence":
        types = body.get("presence_types") or state.presence_types()
        return {"copresence": queries.copresence(view, _id(body, "id"), types,
                                                 body.get("date_from"), body.get("date_to"))}
    if name == "shared":
        return {"shared": queries.shared_identifiers(
            view, _types(body, "types", ("identifier",)),
            _types(body, "holder_types", ("person", "organization")),
            _int(body, "min_holders", 2, 2, 1000), _int(body, "limit", 100, 1, 500),
            body.get("subtypes"), state.holding_types() or None)}
    if name == "timeline":
        return {"timeline": queries.timeline(view, _id(body, "id"), body.get("date_from"),
                                             body.get("date_to"))}
    if name == "neighborhood":
        return queries.neighborhood(view, _id(body, "id"), _int(body, "hops", 1, 1, 3),
                                    _int(body, "limit", 200, 1, 2000))
    if name == "communities":
        return {"communities": queries.communities(view, _types(body, "types", ("person",)))}
    if name == "ranking":
        return {"ranking": queries.degree_ranking(view, _types(body, "types", ("person",)),
                                                  _int(body, "limit", 20, 1, 500))}
    raise HttpError(404, "not found")


def _worker(state: State):
    if state.worker is None:
        raise HttpError(503, "graph extraction is not enabled")
    return state.worker


def handle_extract(state: State, path: str, body: dict) -> dict:
    from ingest_jobs import QueueFull

    worker = _worker(state)
    if path == "/ingest/document":
        meta = {k: _id(body, k) for k in ("doc_id", "corpus_id", "source_path")}
        for k in ("title", "kind"):
            if isinstance(body.get(k), str):
                meta[k] = body[k]
        if body.get("acl") is not None:
            try:
                parse_doc_acl(body["acl"])
            except ValueError as e:
                raise HttpError(400, str(e))
            meta["acl"] = body["acl"]
        try:
            raw = base64.b64decode(_id(body, "content_b64"), validate=True)
        except (binascii.Error, ValueError):
            raise HttpError(400, "content_b64 is not valid base64")
        try:
            return {"queued": worker.submit(meta, raw)}
        except QueueFull as e:
            raise HttpError(429, str(e))
    if path == "/ingest/status":
        return worker.status()
    try:
        scope = Scope.parse(body)
    except ScopeError as e:
        raise HttpError(400, str(e))
    view = state.store.view(scope)
    if path == "/review/list":
        items = worker.open_review(view)
        return {"review": items[:_int(body, "limit", 100, 1, 1000)], "total": len(items)}
    if path == "/review/decide":
        a, b = _id(body, "a"), _id(body, "b")
        decision = body.get("decision")
        if decision not in ("merge", "separate"):
            raise HttpError(400, "decision must be merge or separate")
        if not any(i["a"] == a and i["b"] == b for i in worker.open_review(view)):
            raise HttpError(404, "no such open review pair in scope")
        try:
            worker.decide(a, b, decision == "merge")
        except StoreError as e:
            raise HttpError(400, str(e))
        return {"decided": decision}
    raise HttpError(404, "not found")


def handle_post(state: State, path: str, body: dict) -> dict:
    if path.startswith("/query/"):
        return run_query(state, path[len("/query/"):], body)
    if path.startswith(("/ingest/", "/review/")):
        return handle_extract(state, path, body)
    if path == "/profile/get":
        return {"profile": state.profile}
    if path == "/profile/templates":
        return {"templates": domain_profile.templates()}
    if path == "/profile/validate":
        try:
            return {"profile": domain_profile.validate(body.get("profile") or {})}
        except domain_profile.ProfileError as e:
            raise HttpError(400, str(e))
    if path == "/profile/set":
        try:
            profile = domain_profile.validate(body.get("profile") or {})
        except domain_profile.ProfileError as e:
            raise HttpError(400, str(e))
        with state.lock:
            if profile["version"] <= state.profile["version"] and profile["id"] == state.profile["id"]:
                raise HttpError(409, "profile version must increase (current %d)"
                                % state.profile["version"])
            if PROFILE_PATH:
                tmp = PROFILE_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(profile, f, ensure_ascii=False, indent=1)
                if os.path.exists(PROFILE_PATH):
                    os.replace(PROFILE_PATH, PROFILE_PATH + ".prev")
                os.replace(tmp, PROFILE_PATH)
            state.profile = profile
        # Existing graph content is not rewritten; changed rules apply on reprocessing.
        return {"profile": profile, "persisted": bool(PROFILE_PATH), "reprocess_required": True}
    if path == "/graph/document/put":
        try:
            doc, entities, mentions, relations = parse_document_payload(body, state.profile)
            return {"stored": state.store.put_document(doc, entities, mentions, relations)}
        except (IngestError, StoreError) as e:
            raise HttpError(400, str(e))
    if path == "/graph/document/drop":
        return {"dropped": state.store.drop_document(_id(body, "doc_id"))}
    if path == "/graph/document/drop_tree":
        if state.worker is not None:
            state.worker.forget(_id(body, "corpus_id"), _id(body, "path"))
        ids = state.store.doc_ids_by_path(_id(body, "corpus_id"), _id(body, "path"), True,
                                          bool(body.get("keep_exact", False)))
        return {"dropped": sum(1 for doc_id in ids if state.store.drop_document(doc_id))}
    if path == "/graph/acl/update":
        corpus_id, entries = _id(body, "corpus_id"), body.get("entries")
        if not isinstance(entries, list) or len(entries) > 5000:
            raise HttpError(400, "entries must be a list of at most 5000 items")
        parsed = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise HttpError(400, "every entry needs a path and an acl")
            try:
                parsed.append((entry["path"], parse_doc_acl(entry.get("acl"))))
            except ValueError as e:
                raise HttpError(400, str(e))
        updated = 0
        for source_path, acl in parsed:
            if state.worker is not None:
                state.worker.update_acl(corpus_id, source_path, acl)
            for doc_id in state.store.doc_ids_by_path(corpus_id, source_path):
                updated += bool(state.store.set_acl(doc_id, acl))
        return {"updated": updated}
    if path == "/graph/document/acl":
        try:
            acl = parse_doc_acl(body.get("acl"))
        except ValueError as e:
            raise HttpError(400, str(e))
        if not state.store.set_acl(_id(body, "doc_id"), acl):
            raise HttpError(404, "not found")
        return {"updated": True}
    if path == "/graph/entity/merge":
        try:
            state.store.merge_entities(_id(body, "keep"), _id(body, "drop"))
        except StoreError as e:
            raise HttpError(400, str(e))
        return {"merged": True}
    raise HttpError(404, "not found")


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ViVeSecGraph/0.1"

        def _send(self, status, payload):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            return

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/health":
                return self._send(200, {"ok": True, "status": "ok",
                                        "profile": {"id": state.profile["id"],
                                                    "version": state.profile["version"]},
                                        "counts": state.store.counts()})
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            try:
                if API_KEY and self.headers.get("X-API-Key") != API_KEY:
                    raise HttpError(401, "Unauthorized")
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise HttpError(413, "request too large")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    raise HttpError(400, "invalid JSON body")
                if not isinstance(body, dict):
                    raise HttpError(400, "body must be an object")
                out = {"ok": True}
                out.update(handle_post(state, self.path.split("?", 1)[0], body))
                self._send(200, out)
            except HttpError as e:
                self._send(e.status, {"ok": False, "error": e.error})
            except Exception as e:  # noqa: BLE001
                self._send(500, {"ok": False, "error": "internal error: %s" % type(e).__name__})

    return Handler


def make_store(kind: str):
    if kind == "memory":
        return MemoryGraphStore()
    if kind == "neo4j":
        from neo4j_store import CachedNeo4jStore, Neo4jGraphStore

        password_file = os.environ.get("GRAPH_NEO4J_PASSWORD_FILE", "")
        if not password_file:
            raise SystemExit("GRAPH_STORE=neo4j needs GRAPH_NEO4J_PASSWORD_FILE")
        with open(password_file, encoding="utf-8") as f:
            password = f.read().strip()
        db = Neo4jGraphStore(os.environ.get("GRAPH_NEO4J_URI", "bolt://127.0.0.1:7687"),
                             os.environ.get("GRAPH_NEO4J_USER", "neo4j"), password,
                             os.environ.get("GRAPH_NEO4J_DATABASE", "neo4j"))
        # Only an explicit off disables the mirror; queries then go to Neo4j directly.
        if os.environ.get("GRAPH_READ_CACHE", "on").strip().lower() == "off":
            return db
        return CachedNeo4jStore(db)
    raise SystemExit("GRAPH_STORE must be memory or neo4j, not %r" % kind)


def make_worker(state: State):
    """The extraction worker, unless GRAPH_INGEST=off (then /ingest and /review answer 503)."""
    if os.environ.get("GRAPH_INGEST", "on").strip().lower() == "off":
        return None
    from ingest_jobs import IngestWorker
    from llm_client import LLMClient

    queue_mb = int(os.environ.get("GRAPH_INGEST_QUEUE_MB", "512"))
    try:
        return IngestWorker(state, LLMClient(), max(1, queue_mb) * 1024 * 1024,
                            os.environ.get("GRAPH_REVIEW_PATH", ""))
    except ValueError as e:
        raise SystemExit("graph extraction: %s (or set GRAPH_INGEST=off)" % e)


def main():
    store_kind = os.environ.get("GRAPH_STORE", "memory")
    state = State(make_store(store_kind), initial_profile())
    state.worker = make_worker(state)
    httpd = ThreadingHTTPServer((HOST, PORT), make_handler(state))
    print("graph_service on %s:%d profile=%s v%d store=%s" % (
        HOST, PORT, state.profile["id"], state.profile["version"], store_kind), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
