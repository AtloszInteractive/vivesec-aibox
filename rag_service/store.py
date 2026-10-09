"""doc/page/chunk store for the RAG service.

Implements the PageIndexes data model from `drive_sync_api_spec.txt`:

    one drive  -> one corpus   (corpus_id, supplied by the adapter)
    one file   -> one document (doc_id)
    document   -> pages -> chunks

The store is deliberately backend-light (in-memory dicts + JSON persistence) so
it can stand in for the real Qdrant-backed service while exposing the SAME
contract. Embeddings + chunking are reused from the poc retrieval core so the
mechanics match the rest of the stack.

Deterministic IDs make re-ingest idempotent: the same (tenant, corpus, path)
always yields the same doc_id, so a changed file overwrites its previous
document in place (no orphan duplicates).
"""
import base64
import hashlib
import json
import math
import os
import sys
import threading
import time

# Reuse the poc retrieval core (embeddings, chunking, params).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
import config  # noqa: E402
import embeddings  # noqa: E402
from chunking import chunk_rows, chunk_text  # noqa: E402

import access as access_filter  # noqa: E402
import extract  # noqa: E402
import reaccent  # noqa: E402


def chunk_page(source_path, page_text):
    """Chunk one page the way its format needs: spreadsheets/CSV on row
    boundaries with the header repeated, everything else by word window."""
    if extract.is_tabular(source_path):
        return chunk_rows(page_text, config.CHUNK_WORDS, config.CHUNK_OVERLAP,
                          header_lines=extract.header_lines(source_path, page_text))
    return chunk_text(page_text, config.CHUNK_WORDS, config.CHUNK_OVERLAP)


# Size cap: above this a file is registered metadata-only (no content upload).
MAX_CONTENT_BYTES = int(os.environ.get("RAG_MAX_FILE_BYTES", str(20 * 1024 * 1024)))

# Upload-token lifetime: the `check` -> `content/{token}` handshake must complete
# within this window, after which the token is rejected (spec v1 #6).
TOKEN_TTL_SECONDS = int(os.environ.get("RAG_TOKEN_TTL_SECONDS", "900"))

# Relevance floor for retrieval. Cosine similarity below this is treated as
# "nothing relevant found" and the chunk is not returned at all, so a question
# the corpus cannot answer yields an EMPTY context list instead of the least-bad
# match. That empty list is what lets the generation layer refuse (the silver
# set's T4 negative/out-of-scope tier). 0 disables the floor, which is the
# default so existing deployments keep their current behaviour; the eval box
# runs with a measured value (0.55 gave the best T4 vs non-T4 separation).
#
# NB: the floor is only applied when it is POSITIVE. Cosine similarity is
# legitimately negative for unrelated text, so comparing against a floor of 0
# would still drop those chunks — that is a filter, not "disabled".
MIN_SCORE = float(os.environ.get("RAG_MIN_SCORE", "0") or 0)
MIN_SCORE_ACTIVE = MIN_SCORE > 0

# Query spelling repair (reaccent.py). Users type Hungarian without accents,
# and measured on the production index that is not a degradation but a failure:
# every hit falls under the relevance floor and the box answers "no data".
# Repairing the query from the corpus's own vocabulary restores it.
REACCENT_ACTIVE = os.environ.get("RAG_QUERY_REACCENT", "on").strip().lower() not in (
    "0", "off", "false", "no", "")
# The vocabulary is built by scanning chunk text. Measured on the Jetson:
# 12k chunks take 1.7 s, so this bound keeps the first query on a very large
# corpus responsive -- a partial vocabulary only means fewer repairs.
REACCENT_MAX_CHUNKS = int(os.environ.get("RAG_REACCENT_MAX_CHUNKS", "50000"))


class EmbeddingConflict(Exception):
    """Raised when a corpus was indexed with an incompatible embedding config."""


# A file that extracts to zero text (typically a scanned PDF with no text layer)
# passes every pre-upload check, so without this it would be stored as a normal
# indexed document that no search can ever return. The condition is reported on
# the ingest response, logged, and recorded on the document as a skip reason.
EMPTY_EXTRACTION_WARNING = "extraction_empty"


def normalize_corpus_ids(corpus_id=None, corpus_ids=None):
    """Ordered, de-duplicated corpus list from either contract form.

    The v1 contract carries a single `corpus_id`; a multi-drive request carries
    `corpus_ids`. Accepting both keeps every existing caller (harness, eval
    runs, the adapter's analyse path) working unchanged.
    """
    raw = corpus_ids if corpus_ids else corpus_id
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for value in raw:
        cid = value.strip() if isinstance(value, str) else value
        if cid and cid not in out:
            out.append(cid)
    return out


def index_result(doc_id, page_records, chunk_records, source_path, size):
    """Build the ingest response, flagging documents that produced no chunks."""
    result = {
        "doc_id": doc_id,
        "pages": len(page_records),
        "chunks": len(chunk_records),
    }
    if not chunk_records:
        result["warning"] = EMPTY_EXTRACTION_WARNING
        sys.stderr.write(
            "%s: no text extracted from %s (size=%s) -- kept on record, not searchable\n"
            % (EMPTY_EXTRACTION_WARNING, source_path, size)
        )
        sys.stderr.flush()
    return result


def norm_path(path):
    """Strip a single trailing slash (except for a bare root)."""
    if path and len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def under(path, prefix):
    """Segment-aware prefix test: path == prefix OR path is below prefix/.

    Avoids the classic bug where '/d/beta' would match '/d/beta dev 2' and
    '/d/beta2' as raw string prefixes. The root prefix "/" needs the rstrip:
    without it the test would be startswith("//"), which matches nothing, so
    dropping or rebuilding a whole corpus would silently delete zero documents.
    """
    p = norm_path(path)
    base = norm_path(prefix)
    return p == base or p.startswith(base.rstrip("/") + "/")


def _document_chunk_context(corpus_id, meta, chunk_id, page_id, page_number,
                            section_path, text):
    """One whole-document chunk in the same shape search_context returns.

    score is 1.0 on purpose: the document was named by the caller, so this is an
    exact match, not a ranked guess.
    """
    return {
        "chunk_id": chunk_id,
        "doc_id": meta["doc_id"],
        "page_id": page_id,
        "corpus_id": corpus_id,
        "title": meta.get("title"),
        "source_path": meta.get("source_path"),
        "page_number": page_number,
        "section_path": section_path or "root",
        "text": text,
        "score": 1.0,
        "score_breakdown": {"chunk": 1.0, "page": 0.0, "doc": 1.0, "keyword": 0.0},
        "metadata": {
            "file": bool(meta.get("file", True)),
            "mtime": meta.get("mtime"),
            "size": meta.get("size"),
            "whole_document": True,
        },
    }


def _document_debug(meta, chunks_total, chunks_used, used_tokens):
    """Coverage report for a whole-document fetch: the caller must be able to
    tell a complete analysis from a truncated one."""
    return {
        "found": meta is not None,
        "source_path": (meta or {}).get("source_path"),
        "title": (meta or {}).get("title"),
        "chunks_total": chunks_total,
        "chunks_used": chunks_used,
        "truncated": chunks_used < chunks_total,
        "estimated_tokens": used_tokens,
    }


def _doc_id(tenant_id, corpus_id, source_path):
    raw = "%s\x00%s\x00%s" % (tenant_id, corpus_id, norm_path(source_path))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _decode_head(head_b64):
    """Decode the base64 head sample to bytes; never raises (empty on error)."""
    if not head_b64:
        return b""
    try:
        return base64.b64decode(head_b64)
    except Exception:  # noqa: BLE001
        return b""


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _estimate_tokens(text):
    # Rough heuristic; good enough for a context budget.
    return int(len(text.split()) * 1.3) + 1


class RagStore:
    def __init__(self, persist_path=None):
        self._lock = threading.RLock()
        self._docs = {}        # doc_id -> document metadata
        self._pages = {}       # doc_id -> [ {page_id, page_number, section_path} ]
        self._chunks = {}      # doc_id -> [ {chunk_id, page_id, page_number, section_path, text, vector} ]
        self._corpora = {}     # corpus_id -> {tenant_id, embedding: {...}}
        self._pending = {}     # token -> {corpus_id, tenant_id, source_path, title, size, mtime}
        self.persist_path = persist_path
        self.token_ttl_seconds = TOKEN_TTL_SECONDS
        self._use_ollama = embeddings.use_ollama_embed()
        self._backend = embeddings.backend_name(self._use_ollama)
        self._dim = None
        self._vocabularies = {}  # corpus_id -> (chunk count, Vocabulary)
        if persist_path and os.path.exists(persist_path):
            self._load()

    # ----- embedding helpers -------------------------------------------------
    def _embed(self, texts):
        """Embed texts. Safe to call without the store lock: this only talks to
        the model server and touches no shared state."""
        return embeddings.embed_many(texts, self._use_ollama)

    def _note_dim(self, vectors):
        """Record the embedding width the first time we see it (lock held)."""
        if self._dim is None and vectors and vectors[0]:
            self._dim = len(vectors[0])

    def _ensure_dim(self):
        """Probe the embedding backend once so /health reports the true
        dimension before any document has been ingested. Failures (e.g. Ollama
        not reachable) leave _dim None and fall back to the configured value."""
        if self._dim is not None:
            return
        try:
            vec = embeddings.embed_one("dimension probe", self._use_ollama)
            if vec:
                self._dim = len(vec)
        except Exception:  # noqa: BLE001
            pass

    def embedding_info(self):
        if self._use_ollama:
            provider = "ollama"
            model = config.EMBED_MODEL
        else:
            provider = "fallback-hashing"
            model = "signed-feature-hash"
        self._ensure_dim()
        return {
            "provider": provider,
            "model_name": model,
            "dimension": self._dim if self._dim is not None else config.FALLBACK_EMBED_DIM,
            "normalized": True,
        }

    def _corpus_embedding_guard(self, corpus_id, tenant_id):
        """Enforce one embedding config per corpus (the spec's 409 case)."""
        existing = self._corpora.get(corpus_id)
        if existing is None:
            self._corpora[corpus_id] = {"tenant_id": tenant_id, "embedding": self._backend}
            return
        if existing.get("embedding") != self._backend:
            raise EmbeddingConflict(
                "Corpus %s for tenant %s was indexed with incompatible embedding "
                "settings: model=%s runtime=%s"
                % (corpus_id, tenant_id, existing.get("embedding"), self._backend)
            )

    # ----- persistence -------------------------------------------------------
    def _persist(self):
        if not self.persist_path:
            return
        data = {
            "docs": self._docs,
            "pages": self._pages,
            "chunks": self._chunks,
            "corpora": self._corpora,
        }
        tmp = self.persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.persist_path)

    def _load(self):
        with open(self.persist_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._docs = data.get("docs", {})
        self._pages = data.get("pages", {})
        self._chunks = data.get("chunks", {})
        self._corpora = data.get("corpora", {})
        for chunks in self._chunks.values():
            if chunks and chunks[0].get("vector"):
                self._dim = len(chunks[0]["vector"])
                break

    # ----- directory ---------------------------------------------------------
    def _acl_for(self, doc_id, acl):
        """A new ACL, or the one the document already had (lock held)."""
        if acl is not None:
            return acl
        return (self._docs.get(doc_id) or {}).get("acl")

    def update_acl(self, corpus_id, tenant_id, entries):
        parsed = [(norm_path(entry["path"]), access_filter.parse_doc_acl(entry["acl"]))
                  for entry in entries]
        updated = missing = 0
        with self._lock:
            for path, acl in parsed:
                doc = self._docs.get(_doc_id(tenant_id, corpus_id, path))
                if doc is None:
                    missing += 1
                    continue
                doc["acl"] = acl
                updated += 1
            self._persist()
        return {"updated": updated, "missing": missing}

    def scope_stats(self, corpus_ids, access=None):
        scope = set(corpus_ids or [])
        out = {"documents": 0, "pages": 0, "chunks": 0, "skipped": 0}
        with self._lock:
            for doc_id, doc in self._docs.items():
                if doc.get("corpus_id") not in scope or not doc.get("file", True):
                    continue
                if not access_filter.doc_allowed(doc.get("acl"), access):
                    continue
                out["documents"] += 1
                out["pages"] += len(self._pages.get(doc_id, []))
                out["chunks"] += len(self._chunks.get(doc_id, []))
                out["skipped"] += 1 if doc.get("skip_reason") else 0
        return out

    def upsert_directory(self, corpus_id, tenant_id, path, acl=None):
        path = norm_path(path)
        doc_id = _doc_id(tenant_id, corpus_id, path)
        with self._lock:
            self._corpora.setdefault(corpus_id, {"tenant_id": tenant_id, "embedding": self._backend})
            acl = self._acl_for(doc_id, acl)
            self._docs[doc_id] = {
                "doc_id": doc_id,
                "corpus_id": corpus_id,
                "tenant_id": tenant_id,
                "source_path": path,
                "title": os.path.basename(path) or path,
                "file": False,
                "mtime": None,
                "size": None,
                "pages": 0,
                "chunks": 0,
                "acl": acl,
            }
            self._pages[doc_id] = []
            self._chunks[doc_id] = []
            self._persist()
        return {"doc_id": doc_id, "file": False, "source_path": path}

    # ----- two-step file sync ------------------------------------------------
    def check(self, corpus_id, tenant_id, path, size, mtime, head_b64, acl=None):
        """Register file metadata, decide whether content upload is required.

        Returns (token, reason). token is None when the file should be skipped;
        reason explains why: unsupported_type / empty_content / too_large /
        invalid_content / extractor_unavailable.
        """
        path = norm_path(path)
        if not extract.is_supported(path):
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "unsupported_type", acl)
            return None, "unsupported_type"
        if size is not None and size <= 0:
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "empty_content", acl)
            return None, "empty_content"
        if size is not None and size > MAX_CONTENT_BYTES:
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "too_large", acl)
            return None, "too_large"
        head = _decode_head(head_b64)
        if extract.ext_of(path) in extract.TEXT_EXTS and b"\x00" in head:
            # A text format carrying NUL bytes is binary/corrupt, not text.
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "invalid_content", acl)
            return None, "invalid_content"
        if not extract.extractor_available(path):
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime,
                                  "extractor_unavailable", acl)
            return None, "extractor_unavailable"
        token = hashlib.sha1(
            ("%s|%s|%s|%s" % (corpus_id, path, size, mtime)).encode("utf-8")
        ).hexdigest()
        with self._lock:
            self._pending[token] = {
                "corpus_id": corpus_id,
                "tenant_id": tenant_id,
                "source_path": path,
                "title": os.path.basename(path) or path,
                "size": size,
                "mtime": mtime,
                "acl": acl,
                "expires_at": time.time() + self.token_ttl_seconds,
            }
        return token, None

    def _store_meta_only(self, corpus_id, tenant_id, path, size, mtime, reason=None, acl=None):
        doc_id = _doc_id(tenant_id, corpus_id, path)
        with self._lock:
            self._corpora.setdefault(corpus_id, {"tenant_id": tenant_id, "embedding": self._backend})
            acl = self._acl_for(doc_id, acl)
            self._docs[doc_id] = {
                "doc_id": doc_id,
                "corpus_id": corpus_id,
                "tenant_id": tenant_id,
                "source_path": path,
                "title": os.path.basename(path) or path,
                "file": True,
                "mtime": mtime,
                "size": size,
                "pages": 0,
                "chunks": 0,
                "indexed": False,
                "skip_reason": reason,
                "acl": acl,
            }
            self._pages.setdefault(doc_id, [])
            self._chunks.setdefault(doc_id, [])
            self._persist()

    def commit_content(self, token, raw):
        with self._lock:
            meta = self._pending.get(token)
            if meta is None:
                return None
            expires_at = meta.get("expires_at")
            if expires_at is not None and time.time() > expires_at:
                self._pending.pop(token, None)
                return None
            self._pending.pop(token, None)
        pages = extract.extract_pages(raw, meta["source_path"])
        return self._index_document(
            corpus_id=meta["corpus_id"],
            tenant_id=meta["tenant_id"],
            source_path=meta["source_path"],
            title=meta["title"],
            pages=pages,
            mtime=meta["mtime"],
            size=meta["size"],
            acl=meta.get("acl"),
        )

    # ----- direct ingest -----------------------------------------------------
    def ingest_text(self, corpus_id, tenant_id, source_path, title, text, metadata):
        metadata = metadata or {}
        pages = [(1, "root", text.strip())] if text and text.strip() else []
        return self._index_document(
            corpus_id=corpus_id,
            tenant_id=tenant_id,
            source_path=norm_path(source_path),
            title=title or os.path.basename(source_path),
            pages=pages,
            mtime=metadata.get("mtime"),
            size=metadata.get("size"),
            acl=metadata.get("acl"),
        )

    def ingest_content(self, corpus_id, tenant_id, path, title, raw, metadata):
        metadata = metadata or {}
        pages = extract.extract_pages(raw, path)
        return self._index_document(
            corpus_id=corpus_id,
            tenant_id=tenant_id,
            source_path=norm_path(path),
            title=title or os.path.basename(path),
            pages=pages,
            mtime=metadata.get("mtime"),
            size=metadata.get("size"),
            acl=metadata.get("acl"),
        )

    # ----- core indexing -----------------------------------------------------
    def _index_document(self, corpus_id, tenant_id, source_path, title, pages, mtime, size,
                        acl=None):
        doc_id = _doc_id(tenant_id, corpus_id, source_path)
        page_records = []
        chunk_records = []
        chunk_texts = []
        for (page_number, section_path, page_text) in pages:
            page_id = "%s-p%d" % (doc_id, page_number)
            page_records.append({
                "page_id": page_id,
                "page_number": page_number,
                "section_path": section_path,
            })
            parts = chunk_page(source_path, page_text)
            for ci, part in enumerate(parts):
                chunk_records.append({
                    "chunk_id": "%s-p%d-c%d" % (doc_id, page_number, ci),
                    "page_id": page_id,
                    "page_number": page_number,
                    "section_path": section_path,
                    "text": part,
                    "vector": None,
                })
                chunk_texts.append(part)

        with self._lock:
            self._corpus_embedding_guard(corpus_id, tenant_id)

        # Embedding a large document takes minutes, so it runs outside the lock:
        # holding it here made every search wait for the whole ingest.
        vectors = self._embed(chunk_texts) if chunk_texts else []

        with self._lock:
            # The corpus may have been reconfigured while we were embedding.
            self._corpus_embedding_guard(corpus_id, tenant_id)
            self._note_dim(vectors)
            for rec, vec in zip(chunk_records, vectors):
                rec["vector"] = vec
            self._docs[doc_id] = {
                "doc_id": doc_id,
                "corpus_id": corpus_id,
                "tenant_id": tenant_id,
                "source_path": source_path,
                "title": title,
                "file": True,
                "mtime": mtime,
                "size": size,
                "pages": len(page_records),
                "chunks": len(chunk_records),
                # A file that yielded no text (typically a scanned PDF) is kept
                # so the operator can see it exists, but is not counted as
                # indexed -- it is not searchable, and the reason drives the
                # OCR backlog.
                "indexed": bool(chunk_records),
                "skip_reason": None if chunk_records else EMPTY_EXTRACTION_WARNING,
                "acl": self._acl_for(doc_id, acl),
            }
            self._pages[doc_id] = page_records
            self._chunks[doc_id] = chunk_records
            self._persist()
        return index_result(doc_id, page_records, chunk_records, source_path, size)

    # ----- delete ------------------------------------------------------------
    def drop_tree(self, corpus_id, path, keep_exact):
        path = norm_path(path)
        deleted_docs = 0
        deleted_pages = 0
        deleted_chunks = 0
        with self._lock:
            victims = []
            for doc_id, doc in self._docs.items():
                if doc.get("corpus_id") != corpus_id:
                    continue
                sp = doc.get("source_path", "")
                if not under(sp, path):
                    continue
                if keep_exact and norm_path(sp) == path:
                    continue
                victims.append(doc_id)
            for doc_id in victims:
                deleted_pages += len(self._pages.get(doc_id, []))
                deleted_chunks += len(self._chunks.get(doc_id, []))
                self._docs.pop(doc_id, None)
                self._pages.pop(doc_id, None)
                self._chunks.pop(doc_id, None)
                deleted_docs += 1
            # Drop the corpus registration if it is now empty.
            if not any(d.get("corpus_id") == corpus_id for d in self._docs.values()):
                self._corpora.pop(corpus_id, None)
            self._persist()
        return {
            "corpus_id": corpus_id,
            "path": path,
            "keep_exact": keep_exact,
            "deleted_documents": deleted_docs,
            "deleted_pages": deleted_pages,
            "deleted_chunks": deleted_chunks,
        }

    # ----- retrieval ---------------------------------------------------------
    def _vocabulary(self, corpus_ids, access=None):
        """The scope's accented words, rebuilt when any of its corpora change.
        Only visible documents contribute (E03)."""
        scope = tuple(corpus_ids)
        texts = [
            ch["text"]
            for doc_id, doc in self._docs.items() if doc.get("corpus_id") in scope
            and access_filter.doc_allowed(doc.get("acl"), access)
            for ch in self._chunks.get(doc_id, [])
        ]
        key = (scope, None if access is None else json.dumps(access, sort_keys=True))
        cached = self._vocabularies.get(key)
        if cached is not None and cached[0] == len(texts):
            return cached[1]
        vocabulary = reaccent.build(texts[:REACCENT_MAX_CHUNKS])
        if access is not None and len(self._vocabularies) >= 64:
            for stale in [k for k in self._vocabularies if k[1] is not None][:16]:
                self._vocabularies.pop(stale, None)
        self._vocabularies[key] = (len(texts), vocabulary)
        return vocabulary

    def search_context(self, corpus_id, question, top_k=3, max_context_tokens=4000,
                       corpus_ids=None, source_paths=None, evidence_chunk_ids=None,
                       access=None):
        import retrieval
        scope = normalize_corpus_ids(corpus_id, corpus_ids)
        if not scope or not question or top_k <= 0:
            return [], {"chunk_hits_count": 0, "estimated_tokens": 0}
        with self._lock:
            for cid in scope:
                self._corpus_embedding_guard(cid, self._corpora.get(cid, {}).get("tenant_id", "default"))
            if question and REACCENT_ACTIVE:
                question = self._vocabulary(scope, access).repair(question)
            qvec = self._embed([question])[0] if question else None
            candidates = []
            for doc_id, doc in self._docs.items():
                if doc.get("corpus_id") not in scope:
                    continue
                if not retrieval.match_source(doc.get("source_path"), source_paths):
                    continue
                if not access_filter.doc_allowed(doc.get("acl"), access):
                    continue
                for ch in self._chunks.get(doc_id, []):
                    if not ch.get("vector"):
                        continue
                    score = _cosine(qvec, ch["vector"])
                    candidates.append((score, doc, ch))
        candidates.sort(key=lambda t: t[0], reverse=True)
        limit = min(max(top_k, 0), 50)
        pool_size = min(max(limit * 8, 40), 256)
        dense = [chunk["chunk_id"] for score, doc, chunk in candidates
                 if not MIN_SCORE_ACTIVE or score >= MIN_SCORE][:pool_size]
        query_terms = set(retrieval.terms(question))
        frequencies = {}
        matches = {}
        for score, doc, chunk in candidates:
            matched = query_terms.intersection(retrieval.terms(chunk["text"], limit=None))
            matches[chunk["chunk_id"]] = matched
            for term in matched:
                frequencies[term] = frequencies.get(term, 0) + 1
        lexical_scores = {chunk_id: sum(1.0 / frequencies[term] for term in matched)
                          for chunk_id, matched in matches.items() if matched}
        lexical = sorted(lexical_scores, key=lambda chunk_id: (-lexical_scores[chunk_id], chunk_id))[:pool_size]
        by_id = {chunk["chunk_id"]: (score, doc, chunk) for score, doc, chunk in candidates}
        evidence = [chunk_id for chunk_id in (evidence_chunk_ids or [])[:8] if chunk_id in by_id]
        ranked = retrieval.fuse(dense, lexical, evidence)

        contexts = []
        used_tokens = 0
        seen_text = set()
        for chunk_id in ranked:
            score, doc, ch = by_id[chunk_id]
            if MIN_SCORE_ACTIVE and score < MIN_SCORE:
                continue
            text_key = (doc.get("corpus_id"), " ".join(ch["text"].casefold().split()))
            if text_key in seen_text:
                continue
            seen_text.add(text_key)
            est = _estimate_tokens(ch["text"])
            if contexts and used_tokens + est > max_context_tokens:
                continue
            used_tokens += est
            contexts.append({
                "chunk_id": ch["chunk_id"],
                "doc_id": doc["doc_id"],
                "page_id": ch["page_id"],
                "corpus_id": doc.get("corpus_id"),
                "title": doc.get("title"),
                "source_path": doc.get("source_path"),
                "page_number": ch.get("page_number"),
                "section_path": ch.get("section_path") or "root",
                "text": ch["text"],
                "score": round(score, 6),
                "score_breakdown": {
                    "chunk": round(score, 6),
                    "page": 0.0,
                    "doc": 0.0,
                    "keyword": 1.0 if chunk_id in lexical else 0.0,
                },
                "metadata": {
                    "file": doc.get("file", True),
                    "mtime": doc.get("mtime"),
                    "size": doc.get("size"),
                },
            })
            if len(contexts) >= limit:
                break
        return contexts, {"chunk_hits_count": len(candidates), "estimated_tokens": used_tokens,
                          "dense_candidates": len(dense), "lexical_candidates": len(lexical),
                          "evidence_revalidated": len(evidence), "retrieval": "dense+lexical"}

    # ----- whole-document retrieval ------------------------------------------
    def document_context(self, corpus_id, source_path, max_context_tokens=12000, access=None):
        """Every chunk of ONE document, in reading order.

        This is deliberately NOT a search: when the user points at a file and
        asks for an analysis of it, similarity ranking would silently drop the
        sections that happen not to match the question. The caller names the
        document, so the whole document is the context — bounded only by the
        token budget, and the debug block reports when that bound truncated it.
        """
        target = norm_path(source_path or "")
        base = target.rsplit("/", 1)[-1]
        with self._lock:
            exact, suffix = None, None
            for doc in self._docs.values():
                if doc.get("corpus_id") != corpus_id or not doc.get("file", True):
                    continue
                if not access_filter.doc_allowed(doc.get("acl"), access):
                    continue
                sp = norm_path(doc.get("source_path") or "")
                if sp == target:
                    exact = doc
                    break
                if base and suffix is None and sp.rsplit("/", 1)[-1] == base:
                    suffix = doc
            doc = exact or suffix
            if doc is None:
                return [], _document_debug(None, 0, 0, 0)
            chunks = list(self._chunks.get(doc["doc_id"], []))
            meta = {
                "doc_id": doc["doc_id"],
                "title": doc.get("title"),
                "source_path": doc.get("source_path"),
                "file": doc.get("file", True),
                "mtime": doc.get("mtime"),
                "size": doc.get("size"),
            }
        chunks.sort(key=lambda ch: (ch.get("page_number") or 0))
        contexts = []
        used_tokens = 0
        for ch in chunks:
            est = _estimate_tokens(ch["text"])
            if contexts and used_tokens + est > max_context_tokens:
                break
            used_tokens += est
            contexts.append(_document_chunk_context(
                corpus_id, meta, ch["chunk_id"], ch["page_id"],
                ch.get("page_number"), ch.get("section_path"), ch["text"]))
        return contexts, _document_debug(meta, len(chunks), len(contexts), used_tokens)

    # ----- stats -------------------------------------------------------------
    def stats(self):
        with self._lock:
            documents = sum(1 for d in self._docs.values() if d.get("file", True))
            pages = sum(len(p) for p in self._pages.values())
            chunks = sum(len(c) for c in self._chunks.values())
            corpora = len(self._corpora)
            skipped = {}
            for d in self._docs.values():
                reason = d.get("skip_reason")
                if d.get("file", True) and reason:
                    skipped[reason] = skipped.get(reason, 0) + 1
        return {"documents": documents, "pages": pages, "chunks": chunks,
                "corpora": corpora, "skipped": skipped}

    def skipped_documents(self, corpus_id=None):
        """Files we know about but cannot answer from, with the reason."""
        with self._lock:
            docs = [d for d in self._docs.values()
                    if d.get("file", True) and d.get("skip_reason")
                    and (not corpus_id or d.get("corpus_id") == corpus_id)]
        return sorted(
            ({"source_path": d["source_path"], "corpus_id": d["corpus_id"],
              "size": d.get("size"), "reason": d["skip_reason"]} for d in docs),
            key=lambda d: d["source_path"])
