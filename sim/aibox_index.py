"""AIBox-side index store implementing the ViVeSec `/index/*` contract.

Mock/dev implementation: in-memory document metadata + chunk vectors, with
optional JSON persistence. In production this is backed by a vector DB
(Qdrant/Milvus) living on the LUKS-encrypted volume; the interface (the methods
below) stays the same.

Reuses the `poc/` embedding + chunking pipeline so the simulator exercises the
exact same retrieval mechanics as the real box (bge-m3 on the Jetson, hashing
fallback on a dev laptop with no Ollama).

Key spec facts encoded here:
  * `path` is the unique document id (resolved absolute path on the ViveSecBox).
  * sync is two-phase: check(path,size,mtime,head) -> token|null, then
    content/{token} with the raw bytes.
  * the AIBox decides per file (magic-bytes + size) whether to vectorize.
  * drop/tree is path-SEGMENT aware: `/d/beta` must NOT match `/d/beta2`.
  * concurrent sync is safe: a single RLock serializes index writes and guards
    the token->metadata map.
"""
import base64
import json
import os
import sys
import threading
import uuid

_POC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc")
if _POC not in sys.path:
    sys.path.insert(0, _POC)

import config  # noqa: E402  (poc config: CHUNK_WORDS, EMBED_MODEL, ...)
import embeddings  # noqa: E402
from chunking import chunk_text  # noqa: E402
from store import VectorStore  # noqa: E402  (reuse the cosine implementation)

# Files larger than this are indexed as metadata-only (token=null). Keeps the
# mock bounded; the real box uses a format/size policy here.
MAX_CONTENT_BYTES = int(os.environ.get("AIBOX_MAX_FILE_BYTES", str(5 * 1024 * 1024)))

# Formats the MVP extractor can turn into text RIGHT NOW. PDF/DOCX/XLSX/etc. are
# intentionally NOT here yet -> they index as metadata-only (token=null) until
# FA.2 plugs in the real extractor (MarkItDown/Docling). This is also the real
# AIBox behaviour: only ingest content it can faithfully extract.
# Override with AIBOX_TEXT_EXTS="txt,md,csv".
TEXT_EXTS = set(
    "." + e.strip().lstrip(".").lower()
    for e in os.environ.get(
        "AIBOX_TEXT_EXTS",
        "txt,md,markdown,text,csv,tsv,json,log,rst,yaml,yml,ini,xml,html",
    ).split(",")
    if e.strip()
)


def norm(path):
    """Normalize a path for prefix matching: strip a trailing slash (the
    VVS-Drive header ends in '/', stored paths do not)."""
    if not path:
        return path
    if len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def under(path, prefix):
    """Path-SEGMENT-aware prefix test. `/d/beta` matches `/d/beta` and
    `/d/beta/x` but NOT `/d/beta2` or `/d/beta dev 2`."""
    prefix = norm(prefix)
    return path == prefix or path.startswith(prefix + "/")


def _is_texty(head_bytes):
    """Cheap magic-bytes check: reject anything with a NUL byte (binary)."""
    if head_bytes is None:
        return True
    if b"\x00" in head_bytes:
        return False
    return True


def _should_process(path, head, size):
    """Decide whether the AIBox vectorizes this file NOW.

    Allow-list of text formats + magic-bytes + size cap. Unsupported formats
    (pdf/docx/binary) return False -> the caller stores metadata only and issues
    no token, so e.g. a PDF is cleanly skipped (no latin-1 garbage chunks) until
    the real extractor (FA.2) is wired in.
    """
    if size is not None and size > MAX_CONTENT_BYTES:
        return False
    if not _is_texty(head):
        return False
    ext = os.path.splitext(path)[1].lower()
    return ext in TEXT_EXTS


def _extract_text(raw, path):
    """MVP extractor: decode text. Real box plugs MarkItDown/Docling here for
    PDF/DOCX/etc. Binary never reaches this (check() returned token=null)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


class AIBoxIndex:
    def __init__(self, persist_path=None):
        self._lock = threading.RLock()
        self._docs = {}     # path -> {path, file, mtime, size}
        self._chunks = {}   # path -> [{chunk_index, text, vector}]
        self._tokens = {}   # token -> {path, size, mtime}
        self.persist_path = persist_path
        self.embed_backend = ""
        if persist_path and os.path.exists(persist_path):
            self._load()

    # -- persistence (optional; in-memory by default) --------------------
    def _persist(self):
        if not self.persist_path:
            return
        with open(self.persist_path, "w", encoding="utf-8") as f:
            json.dump({"docs": self._docs, "chunks": self._chunks,
                       "embed_backend": self.embed_backend}, f)

    def _load(self):
        with open(self.persist_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._docs = data.get("docs", {})
        self._chunks = data.get("chunks", {})
        self.embed_backend = data.get("embed_backend", "")

    # -- read side -------------------------------------------------------
    def get(self, path):
        with self._lock:
            meta = self._docs.get(norm(path))
            return dict(meta) if meta else None

    def get_children(self, path):
        base = norm(path)
        prefix = base + "/"
        out = []
        with self._lock:
            for p, meta in self._docs.items():
                if p == base or not p.startswith(prefix):
                    continue
                rest = p[len(prefix):]
                if "/" not in rest:  # immediate child only
                    out.append(dict(meta))
        return out

    def list_subtree(self, path):
        """All document paths under `path` (inclusive). Used for reconcile."""
        base = norm(path)
        with self._lock:
            return [p for p in self._docs if under(p, base)]

    # -- write side ------------------------------------------------------
    def upsert_directory(self, path):
        path = norm(path)
        with self._lock:
            self._docs[path] = {"path": path, "file": False,
                                "mtime": None, "size": None}
            self._persist()

    def check(self, path, size, mtime, head_b64):
        """Phase 1: store metadata; decide whether to ingest content.
        Returns a token (process the content) or None (metadata-only)."""
        path = norm(path)
        head = base64.b64decode(head_b64) if head_b64 else b""
        process = _should_process(path, head, size)
        with self._lock:
            self._docs[path] = {"path": path, "file": True,
                                "mtime": mtime, "size": size}
            if not process:
                self._chunks.pop(path, None)  # drop stale vectors if any
                self._persist()
                return None
            token = uuid.uuid4().hex
            self._tokens[token] = {"path": path, "size": size, "mtime": mtime}
            return token

    def commit_content(self, token, raw):
        """Phase 2: vectorize the raw bytes for a token from check()."""
        with self._lock:
            tok = self._tokens.pop(token, None)
        if tok is None:
            raise KeyError("unknown or expired token")
        path = tok["path"]
        text = _extract_text(raw, path)
        chunks = chunk_text(text, config.CHUNK_WORDS, config.CHUNK_OVERLAP)
        use_ol = embeddings.use_ollama_embed()
        vecs = embeddings.embed_many(chunks, use_ol)
        recs = [{"chunk_index": i, "text": c, "vector": v}
                for i, (c, v) in enumerate(zip(chunks, vecs))]
        with self._lock:
            self._docs[path] = {"path": path, "file": True,
                                "mtime": tok["mtime"], "size": tok["size"]}
            self._chunks[path] = recs
            self.embed_backend = embeddings.backend_name(use_ol)
            self._persist()
        return len(recs)

    def drop_tree(self, path, keep_exact=False):
        """Remove every document under `path` (segment-aware). With
        keep_exact=False the exact `path` document is removed too."""
        base = norm(path)
        removed = 0
        with self._lock:
            for p in list(self._docs.keys()):
                is_under = p.startswith(base + "/")
                is_exact = (p == base)
                if is_under or (is_exact and not keep_exact):
                    self._docs.pop(p, None)
                    self._chunks.pop(p, None)
                    removed += 1
            self._persist()
        return removed

    # -- query side (ACL pre-filter by drive prefix) ---------------------
    def search(self, qvec, top_k, drive_prefix=None):
        scored = []
        with self._lock:
            for p, recs in self._chunks.items():
                if drive_prefix and not under(p, drive_prefix):
                    continue  # HARD pre-filter: drive-prefix is mandatory
                for rec in recs:
                    s = VectorStore._cosine(qvec, rec["vector"])
                    scored.append((s, {"path": p,
                                       "chunk_index": rec["chunk_index"],
                                       "text": rec["text"]}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    # -- introspection ---------------------------------------------------
    def stats(self):
        with self._lock:
            n_files = sum(1 for m in self._docs.values() if m.get("file"))
            n_dirs = sum(1 for m in self._docs.values() if not m.get("file"))
            n_vecs = sum(len(v) for v in self._chunks.values())
            return {"documents": len(self._docs), "files": n_files,
                    "directories": n_dirs, "chunks": n_vecs,
                    "pending_tokens": len(self._tokens),
                    "embed_backend": self.embed_backend}
