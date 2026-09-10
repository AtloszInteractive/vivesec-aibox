"""sqlite-vec backed doc/page/chunk store for the RAG service.

Drop-in replacement for `RagStore` (store.py): exposes the IDENTICAL public
interface, but persists metadata in regular SQLite tables and chunk vectors in a
`vec0` virtual table, with KNN retrieval done in SQL.

Why this design:

  * Durability/scale: no full-file JSON rewrite on every mutation; the SQLite
    file is the index, mutations are incremental.
  * Correct corpus isolation: the `vec0` table uses `corpus_id` as a PARTITION
    KEY, so KNN is PRE-FILTERED to one corpus (correct recall) -- never the
    global-top-k-then-post-filter pattern we rejected for ACL reasons.
  * On-box only: sqlite-vec is a single loadable extension (verified to install
    from an arm64 wheel inside the Jetson RAG container), no extra service.

Shared semantics (deterministic IDs, segment-aware drop_tree, one-embedding-
config-per-corpus guard, upload-token TTL, reason set) are reused verbatim from
store.py so the two backends behave identically behind the same contract.
"""
import os
import sqlite3
import sys
import threading
import time

import sqlite_vec

# Reuse the poc retrieval core (embeddings, chunking, params) + extract gate.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
import config  # noqa: E402
import embeddings  # noqa: E402
from chunking import chunk_text  # noqa: E402

import extract  # noqa: E402
import query_split  # noqa: E402
import reaccent  # noqa: E402

# Shared helpers / constants / exception, single source of truth in store.py.
from store import (  # noqa: E402
    EMPTY_EXTRACTION_WARNING,
    EmbeddingConflict,
    MAX_CONTENT_BYTES,
    MIN_SCORE,
    MIN_SCORE_ACTIVE,
    REACCENT_ACTIVE,
    REACCENT_MAX_CHUNKS,
    TOKEN_TTL_SECONDS,
    _decode_head,
    _doc_id,
    _document_chunk_context,
    _document_debug,
    _estimate_tokens,
    index_result,
    norm_path,
    normalize_corpus_ids,
    under,
)


def _like_escape(value):
    """A filename is user data: '_' and '%' are LIKE wildcards, so they must be
    escaped or 'q1_report.pdf' would also match 'q1xreport.pdf'."""
    return (value.replace("\\", "\\\\")
                 .replace("%", "\\%")
                 .replace("_", "\\_"))


class SqliteVecStore:
    def __init__(self, persist_path=None):
        self._lock = threading.RLock()
        self.persist_path = persist_path
        self.token_ttl_seconds = TOKEN_TTL_SECONDS
        self._pending = {}  # token -> {corpus_id, tenant_id, source_path, ...} (ephemeral)
        self._use_ollama = embeddings.use_ollama_embed()
        self._backend = embeddings.backend_name(self._use_ollama)
        self._dim = None
        self._vec_ready = False
        self._vocabularies = {}  # corpus_id -> (chunk signature, Vocabulary)

        db_path = persist_path if persist_path else ":memory:"
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._init_schema()
        self._load_meta()

    # ----- schema ------------------------------------------------------------
    def _init_schema(self):
        c = self._conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS corpora (
                corpus_id TEXT PRIMARY KEY,
                tenant_id TEXT,
                embedding TEXT
            );
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                corpus_id TEXT,
                tenant_id TEXT,
                source_path TEXT,
                title TEXT,
                file INTEGER,
                mtime INTEGER,
                size INTEGER,
                pages INTEGER,
                chunks INTEGER,
                indexed INTEGER,
                skip_reason TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_documents_corpus ON documents(corpus_id);
            CREATE TABLE IF NOT EXISTS pages (
                page_id TEXT PRIMARY KEY,
                doc_id TEXT,
                page_number INTEGER,
                section_path TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pages_doc ON pages(doc_id);
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT,
                corpus_id TEXT,
                page_id TEXT,
                page_number INTEGER,
                section_path TEXT,
                text TEXT,
                vec_rowid INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_corpus ON chunks(corpus_id);
            """
        )
        # Indexes created before skip_reason existed are upgraded in place; the
        # column is nullable so old rows simply read as "no reason recorded".
        columns = {row[1] for row in c.execute("PRAGMA table_info(documents)")}
        if "skip_reason" not in columns:
            c.execute("ALTER TABLE documents ADD COLUMN skip_reason TEXT")
        c.commit()

    def _load_meta(self):
        row = self._conn.execute("SELECT value FROM meta WHERE key='dim'").fetchone()
        if row is not None:
            try:
                self._dim = int(row[0])
            except (TypeError, ValueError):
                self._dim = None
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_vectors'"
        ).fetchone()
        self._vec_ready = exists is not None

    def _ensure_vec_table(self):
        """Create the vec0 table lazily, once the embedding dimension is known."""
        if self._vec_ready or self._dim is None:
            return
        self._conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0("
            "corpus_id TEXT PARTITION KEY, "
            "embedding FLOAT[%d] distance_metric=cosine, "
            "+chunk_id TEXT)" % self._dim
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES('dim', ?)", (str(self._dim),)
        )
        self._conn.commit()
        self._vec_ready = True

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
        row = self._conn.execute(
            "SELECT embedding FROM corpora WHERE corpus_id=?", (corpus_id,)
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT OR IGNORE INTO corpora(corpus_id, tenant_id, embedding) VALUES(?,?,?)",
                (corpus_id, tenant_id, self._backend),
            )
            self._conn.commit()
            return
        if row[0] != self._backend:
            raise EmbeddingConflict(
                "Corpus %s for tenant %s was indexed with incompatible embedding "
                "settings: model=%s runtime=%s"
                % (corpus_id, tenant_id, row[0], self._backend)
            )

    def _ensure_corpus(self, corpus_id, tenant_id):
        self._conn.execute(
            "INSERT OR IGNORE INTO corpora(corpus_id, tenant_id, embedding) VALUES(?,?,?)",
            (corpus_id, tenant_id, self._backend),
        )

    # ----- directory ---------------------------------------------------------
    def upsert_directory(self, corpus_id, tenant_id, path):
        path = norm_path(path)
        doc_id = _doc_id(tenant_id, corpus_id, path)
        with self._lock:
            self._ensure_corpus(corpus_id, tenant_id)
            self._delete_doc_rows(doc_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO documents("
                "doc_id, corpus_id, tenant_id, source_path, title, file, mtime, size, "
                "pages, chunks, indexed) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, corpus_id, tenant_id, path, os.path.basename(path) or path,
                 0, None, None, 0, 0, 1),
            )
            self._conn.commit()
        return {"doc_id": doc_id, "file": False, "source_path": path}

    # ----- two-step file sync ------------------------------------------------
    def check(self, corpus_id, tenant_id, path, size, mtime, head_b64):
        path = norm_path(path)
        if not extract.is_supported(path):
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "unsupported_type")
            return None, "unsupported_type"
        if size is not None and size <= 0:
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "empty_content")
            return None, "empty_content"
        if size is not None and size > MAX_CONTENT_BYTES:
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "too_large")
            return None, "too_large"
        head = _decode_head(head_b64)
        if extract.ext_of(path) in extract.TEXT_EXTS and b"\x00" in head:
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "invalid_content")
            return None, "invalid_content"
        if not extract.extractor_available(path):
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime,
                                  "extractor_unavailable")
            return None, "extractor_unavailable"
        import hashlib
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
                "expires_at": time.time() + self.token_ttl_seconds,
            }
        return token, None

    def _store_meta_only(self, corpus_id, tenant_id, path, size, mtime, reason=None):
        doc_id = _doc_id(tenant_id, corpus_id, path)
        with self._lock:
            self._ensure_corpus(corpus_id, tenant_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO documents("
                "doc_id, corpus_id, tenant_id, source_path, title, file, mtime, size, "
                "pages, chunks, indexed, skip_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, corpus_id, tenant_id, path, os.path.basename(path) or path,
                 1, mtime, size, 0, 0, 0, reason),
            )
            self._conn.commit()

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
        )

    # ----- core indexing -----------------------------------------------------
    def _delete_doc_rows(self, doc_id):
        """Remove a document's pages + chunks + their vectors (for re-ingest)."""
        rowids = [
            r[0] for r in self._conn.execute(
                "SELECT vec_rowid FROM chunks WHERE doc_id=? AND vec_rowid IS NOT NULL",
                (doc_id,),
            ).fetchall()
        ]
        if rowids and self._vec_ready:
            self._conn.executemany(
                "DELETE FROM chunk_vectors WHERE rowid=?", [(rid,) for rid in rowids]
            )
        self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        self._conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))

    def _index_document(self, corpus_id, tenant_id, source_path, title, pages, mtime, size):
        doc_id = _doc_id(tenant_id, corpus_id, source_path)
        page_records = []
        chunk_records = []
        chunk_texts = []
        for (page_number, section_path, page_text) in pages:
            page_id = "%s-p%d" % (doc_id, page_number)
            page_records.append((page_id, page_number, section_path))
            parts = chunk_text(page_text, config.CHUNK_WORDS, config.CHUNK_OVERLAP)
            for ci, part in enumerate(parts):
                chunk_records.append({
                    "chunk_id": "%s-p%d-c%d" % (doc_id, page_number, ci),
                    "page_id": page_id,
                    "page_number": page_number,
                    "section_path": section_path,
                    "text": part,
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
            self._ensure_vec_table()
            self._delete_doc_rows(doc_id)

            for page_id, page_number, section_path in page_records:
                self._conn.execute(
                    "INSERT OR REPLACE INTO pages(page_id, doc_id, page_number, section_path) "
                    "VALUES(?,?,?,?)",
                    (page_id, doc_id, page_number, section_path),
                )

            for idx, rec in enumerate(chunk_records):
                vec_rowid = None
                if idx < len(vectors) and vectors[idx] and self._vec_ready:
                    cur = self._conn.execute(
                        "INSERT INTO chunk_vectors(corpus_id, embedding, chunk_id) VALUES(?,?,?)",
                        (corpus_id, sqlite_vec.serialize_float32(vectors[idx]), rec["chunk_id"]),
                    )
                    vec_rowid = cur.lastrowid
                self._conn.execute(
                    "INSERT OR REPLACE INTO chunks("
                    "chunk_id, doc_id, corpus_id, page_id, page_number, section_path, text, vec_rowid) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (rec["chunk_id"], doc_id, corpus_id, rec["page_id"], rec["page_number"],
                     rec["section_path"], rec["text"], vec_rowid),
                )

            # A file that yielded no text (typically a scanned PDF) is kept so
            # the operator can see it exists, but not counted as indexed -- it
            # is not searchable, and the reason is what drives the OCR backlog.
            skip_reason = None if chunk_records else EMPTY_EXTRACTION_WARNING
            self._conn.execute(
                "INSERT OR REPLACE INTO documents("
                "doc_id, corpus_id, tenant_id, source_path, title, file, mtime, size, "
                "pages, chunks, indexed, skip_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, corpus_id, tenant_id, source_path, title, 1, mtime, size,
                 len(page_records), len(chunk_records), 1 if chunk_records else 0,
                 skip_reason),
            )
            self._conn.commit()
        return index_result(doc_id, page_records, chunk_records, source_path, size)

    # ----- delete ------------------------------------------------------------
    def drop_tree(self, corpus_id, path, keep_exact):
        path = norm_path(path)
        deleted_docs = 0
        deleted_pages = 0
        deleted_chunks = 0
        with self._lock:
            docs = self._conn.execute(
                "SELECT doc_id, source_path FROM documents WHERE corpus_id=?", (corpus_id,)
            ).fetchall()
            victims = []
            for doc_id, sp in docs:
                if not under(sp, path):
                    continue
                if keep_exact and norm_path(sp) == path:
                    continue
                victims.append(doc_id)
            for doc_id in victims:
                deleted_pages += self._conn.execute(
                    "SELECT COUNT(*) FROM pages WHERE doc_id=?", (doc_id,)
                ).fetchone()[0]
                deleted_chunks += self._conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE doc_id=?", (doc_id,)
                ).fetchone()[0]
                self._delete_doc_rows(doc_id)
                self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
                deleted_docs += 1
            # Drop the corpus registration if it is now empty.
            remaining = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE corpus_id=?", (corpus_id,)
            ).fetchone()[0]
            if remaining == 0:
                self._conn.execute("DELETE FROM corpora WHERE corpus_id=?", (corpus_id,))
            self._conn.commit()
        return {
            "corpus_id": corpus_id,
            "path": path,
            "keep_exact": keep_exact,
            "deleted_documents": deleted_docs,
            "deleted_pages": deleted_pages,
            "deleted_chunks": deleted_chunks,
        }

    # ----- retrieval ---------------------------------------------------------
    def _vocabulary(self, corpus_ids):
        """The scope's accented words, rebuilt when any of its corpora change."""
        scope = tuple(corpus_ids)
        marks = ",".join("?" * len(scope))
        signature = self._conn.execute(
            "SELECT COUNT(*), IFNULL(MAX(rowid), 0) FROM chunks "
            "WHERE corpus_id IN (%s)" % marks,
            scope,
        ).fetchone()
        cached = self._vocabularies.get(scope)
        if cached is not None and cached[0] == signature:
            return cached[1]
        rows = self._conn.execute(
            "SELECT text FROM chunks WHERE corpus_id IN (%s) LIMIT ?" % marks,
            scope + (REACCENT_MAX_CHUNKS,),
        )
        vocabulary = reaccent.build(text for (text,) in rows)
        self._vocabularies[scope] = (signature, vocabulary)
        return vocabulary

    def search_context(self, corpus_id, question, top_k=3, max_context_tokens=4000,
                       corpus_ids=None):
        scope = normalize_corpus_ids(corpus_id, corpus_ids)
        if not scope:
            return [], {"chunk_hits_count": 0, "estimated_tokens": 0}
        with self._lock:
            totals = {}
            for cid in scope:
                tenant = "default"
                row = self._conn.execute(
                    "SELECT tenant_id FROM corpora WHERE corpus_id=?", (cid,)
                ).fetchone()
                if row is not None and row[0]:
                    tenant = row[0]
                self._corpus_embedding_guard(cid, tenant)
                totals[cid] = self._conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE corpus_id=? AND vec_rowid IS NOT NULL",
                    (cid,),
                ).fetchone()[0]
            total = sum(totals.values())

            k = max(top_k, 0)
            if not question or not self._vec_ready or k == 0 or total == 0:
                return [], {"chunk_hits_count": total, "estimated_tokens": 0}

            queries = query_split.split_question(question)
            if REACCENT_ACTIVE:
                vocabulary = self._vocabulary(scope)
                queries = [vocabulary.repair(q) for q in queries]
            # The question is embedded ONCE for the whole scope: the embedding
            # is the expensive step, the per-partition scan is not.
            qvecs = [v for v in self._embed(queries) if v]
            if not qvecs:
                return [], {"chunk_hits_count": total, "estimated_tokens": 0}

            k = min(k, total)
            runs = []
            for qvec in qvecs:
                blob = sqlite_vec.serialize_float32(qvec)
                kept = []
                for cid in scope:
                    if not totals[cid]:
                        continue
                    rows = self._conn.execute(
                        "SELECT chunk_id, distance FROM chunk_vectors "
                        "WHERE corpus_id=? AND embedding MATCH ? AND k=? ORDER BY distance",
                        (cid, blob, min(k, totals[cid])),
                    ).fetchall()
                    for chunk_id, distance in rows:
                        score = 1.0 - float(distance)
                        # Rows arrive best-first, so the first one under the floor
                        # ends this corpus's list.
                        if MIN_SCORE_ACTIVE and score < MIN_SCORE:
                            break
                        kept.append((chunk_id, score))
                # Corpora compete on one cosine scale per sub-question; the
                # sub-questions themselves are interleaved afterwards, so a
                # strong corpus cannot take every slot from a sub-question.
                kept.sort(key=lambda hit: hit[1], reverse=True)
                runs.append(kept[:k])
            hits = query_split.interleave(runs, k)

            contexts = []
            used_tokens = 0
            for chunk_id, score in hits:
                row = self._conn.execute(
                    "SELECT c.doc_id, c.page_id, c.page_number, c.section_path, c.text, "
                    "c.corpus_id, d.title, d.source_path, d.file, d.mtime, d.size "
                    "FROM chunks c JOIN documents d ON c.doc_id=d.doc_id "
                    "WHERE c.chunk_id=?",
                    (chunk_id,),
                ).fetchone()
                if row is None:
                    continue
                (doc_id, page_id, page_number, section_path, text,
                 chunk_corpus, title, source_path, file_flag, mtime, size) = row
                est = _estimate_tokens(text)
                if contexts and used_tokens + est > max_context_tokens:
                    break
                used_tokens += est
                contexts.append({
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "page_id": page_id,
                    "corpus_id": chunk_corpus,
                    "title": title,
                    "source_path": source_path,
                    "page_number": page_number,
                    "section_path": section_path or "root",
                    "text": text,
                    "score": round(score, 6),
                    "score_breakdown": {
                        "chunk": round(score, 6),
                        "page": 0.0,
                        "doc": 0.0,
                        "keyword": 0.0,
                    },
                    "metadata": {
                        "file": bool(file_flag),
                        "mtime": mtime,
                        "size": size,
                    },
                })
            return contexts, {"chunk_hits_count": total, "estimated_tokens": used_tokens}

    # ----- whole-document retrieval ------------------------------------------
    def document_context(self, corpus_id, source_path, max_context_tokens=12000):
        """Every chunk of ONE document, in reading order.

        Deliberately NOT a search: when the user points at a file and asks for
        an analysis of it, similarity ranking would silently drop the sections
        that happen not to match the question. The caller names the document, so
        the whole document is the context — bounded only by the token budget,
        which the debug block reports.
        """
        target = norm_path(source_path or "")
        base = target.rsplit("/", 1)[-1]
        columns = ("SELECT doc_id, title, source_path, file, mtime, size "
                   "FROM documents WHERE corpus_id=? AND file=1")
        with self._lock:
            row = self._conn.execute(
                columns + " AND source_path=?", (corpus_id, target)).fetchone()
            if row is None and base:
                row = self._conn.execute(
                    columns + " AND source_path LIKE ? ESCAPE '\\' "
                    "ORDER BY LENGTH(source_path) LIMIT 1",
                    (corpus_id, "%/" + _like_escape(base))).fetchone()
            if row is None:
                return [], _document_debug(None, 0, 0, 0)
            doc_id, title, sp, file_flag, mtime, size = row
            rows = self._conn.execute(
                "SELECT chunk_id, page_id, page_number, section_path, text "
                "FROM chunks WHERE doc_id=? ORDER BY page_number, rowid",
                (doc_id,)).fetchall()
        meta = {"doc_id": doc_id, "title": title, "source_path": sp,
                "file": bool(file_flag), "mtime": mtime, "size": size}
        contexts = []
        used_tokens = 0
        for chunk_id, page_id, page_number, section_path, text in rows:
            est = _estimate_tokens(text)
            if contexts and used_tokens + est > max_context_tokens:
                break
            used_tokens += est
            contexts.append(_document_chunk_context(
                corpus_id, meta, chunk_id, page_id, page_number, section_path, text))
        return contexts, _document_debug(meta, len(rows), len(contexts), used_tokens)

    # ----- stats -------------------------------------------------------------
    def stats(self):
        with self._lock:
            documents = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE file=1"
            ).fetchone()[0]
            pages = self._conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
            chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            corpora = self._conn.execute("SELECT COUNT(*) FROM corpora").fetchone()[0]
            skipped = dict(self._conn.execute(
                "SELECT skip_reason, COUNT(*) FROM documents "
                "WHERE file=1 AND skip_reason IS NOT NULL GROUP BY skip_reason"
            ).fetchall())
        return {"documents": documents, "pages": pages, "chunks": chunks,
                "corpora": corpora, "skipped": skipped}

    def skipped_documents(self, corpus_id=None):
        """Files we know about but cannot answer from, with the reason.

        This is what tells an operator which documents need OCR or a format fix,
        instead of them silently never appearing in any answer.
        """
        sql = ("SELECT source_path, corpus_id, size, skip_reason FROM documents "
               "WHERE file=1 AND skip_reason IS NOT NULL")
        params = ()
        if corpus_id:
            sql += " AND corpus_id=?"
            params = (corpus_id,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY source_path", params).fetchall()
        return [{"source_path": r[0], "corpus_id": r[1], "size": r[2], "reason": r[3]}
                for r in rows]
