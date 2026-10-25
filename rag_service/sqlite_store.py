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
import queue
import sqlite3
import sys
import threading
import time
import uuid

import sqlite_vec

# Reuse the poc retrieval core (embeddings, chunking, params) + extract gate.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "poc"))
import config  # noqa: E402
import embeddings  # noqa: E402

import access as access_filter  # noqa: E402
import extract  # noqa: E402
import ocr  # noqa: E402
import query_split  # noqa: E402
import reaccent  # noqa: E402
import retrieval

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
    chunk_page,
    index_result,
    norm_path,
    normalize_corpus_ids,
    under,
)


# Uploads waiting for OCR are held in memory only (the box keeps no raw file);
# beyond this many bytes an upload is marked ocr_deferred and needs a resync.
OCR_QUEUE_BYTES = max(1, int(os.environ.get("RAG_OCR_QUEUE_MB", "512") or 512)) << 20
# documents.ocr_state -> skip_reason of a document that has no text (yet).
_OCR_SKIP = {"pending": "ocr_pending", "deferred": "ocr_deferred", "failed": "ocr_failed",
             "interrupted": "ocr_interrupted", "done": "ocr_no_text"}


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
        self._ocr_queue = queue.Queue()
        self._ocr_tokens = {}  # doc_id -> token of the upload whose OCR may still land
        self._ocr_bytes = 0
        self._ocr_thread = None
        self._recover_ocr()

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
        # E03: the flattened ACL. Rows indexed before it existed read as
        # unrestricted at the query's default classification.
        if "acl_restricted" not in columns:
            c.execute("ALTER TABLE documents ADD COLUMN acl_restricted INTEGER")
        if "classification" not in columns:
            c.execute("ALTER TABLE documents ADD COLUMN classification INTEGER")
        # E10b: where a document's text came from. pages.kind is NULL for a text
        # layer, 'ocr' for a recognised page, 'skipped' for one OCR could not read.
        if "ocr_state" not in columns:
            c.execute("ALTER TABLE documents ADD COLUMN ocr_state TEXT")
        page_columns = {row[1] for row in c.execute("PRAGMA table_info(pages)")}
        for name, kind in (("kind", "TEXT"), ("ocr_status", "TEXT"), ("ocr_quality", "INTEGER"),
                           ("ocr_lang", "TEXT"), ("ocr_rotation", "INTEGER")):
            if name not in page_columns:
                c.execute("ALTER TABLE pages ADD COLUMN %s %s" % (name, kind))
        c.executescript("""
            CREATE TABLE IF NOT EXISTS doc_acl (
                doc_id TEXT,
                effect TEXT,
                principal TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_doc_acl_doc ON doc_acl(doc_id, effect, principal);
        """)
        has_fts = c.execute("SELECT 1 FROM meta WHERE key='fts_ready' AND value='1'").fetchone()
        c.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_text_fts USING fts5(
                text, corpus_id, content='chunks', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2');
            CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON chunks BEGIN
                INSERT INTO chunk_text_fts(rowid, text, corpus_id) VALUES(new.rowid, new.text, new.corpus_id);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON chunks BEGIN
                INSERT INTO chunk_text_fts(chunk_text_fts, rowid, text, corpus_id)
                VALUES('delete', old.rowid, old.text, old.corpus_id);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunk_text_fts(chunk_text_fts, rowid, text, corpus_id)
                VALUES('delete', old.rowid, old.text, old.corpus_id);
                INSERT INTO chunk_text_fts(rowid, text, corpus_id) VALUES(new.rowid, new.text, new.corpus_id);
            END;
        """)
        if not has_fts:
            c.execute("INSERT INTO chunk_text_fts(chunk_text_fts) VALUES('rebuild')")
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('fts_ready', '1')")
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

    # ----- access control (E03) ----------------------------------------------
    def _acl_snapshot(self, doc_id):
        return self._conn.execute(
            "SELECT acl_restricted, classification FROM documents WHERE doc_id=?",
            (doc_id,)).fetchone()

    def _write_acl(self, doc_id, acl, previous=None):
        """Store a document's flattened ACL. Without a new one the row keeps
        what it had: a re-upload that does not repeat the ACL must not drop
        it (the INSERT OR REPLACE of the document row would otherwise)."""
        if acl is None:
            if previous is not None:
                self._conn.execute(
                    "UPDATE documents SET acl_restricted=?, classification=? WHERE doc_id=?",
                    (previous[0], previous[1], doc_id))
            return
        self._conn.execute("DELETE FROM doc_acl WHERE doc_id=?", (doc_id,))
        rows = ([(doc_id, "allow", p) for p in acl["allow"]]
                + [(doc_id, "deny", p) for p in acl["deny"]])
        if rows:
            self._conn.executemany(
                "INSERT INTO doc_acl(doc_id, effect, principal) VALUES(?,?,?)", rows)
        self._conn.execute(
            "UPDATE documents SET acl_restricted=?, classification=? WHERE doc_id=?",
            (1 if acl["restricted"] else 0, acl["classification"], doc_id))

    def update_acl(self, corpus_id, tenant_id, entries):
        """Replace the ACL of already known documents (metadata only, nothing
        is re-embedded). Unknown paths are counted, not created."""
        parsed = [(norm_path(entry["path"]), access_filter.parse_doc_acl(entry["acl"]))
                  for entry in entries]
        updated = missing = 0
        with self._lock:
            for path, acl in parsed:
                doc_id = _doc_id(tenant_id, corpus_id, path)
                if self._acl_snapshot(doc_id) is None:
                    missing += 1
                    continue
                self._write_acl(doc_id, acl)
                updated += 1
            self._conn.commit()
        return {"updated": updated, "missing": missing}

    def _excluded(self, corpus_id, acl_sql, acl_params):
        """Does the access filter hide any searchable chunk of this corpus?"""
        return self._conn.execute(
            "SELECT EXISTS(SELECT 1 FROM chunks c JOIN documents d ON d.doc_id=c.doc_id "
            "WHERE c.corpus_id=? AND c.vec_rowid IS NOT NULL AND NOT (" + acl_sql + "))",
            [corpus_id] + acl_params).fetchone()[0] == 1

    def scope_stats(self, corpus_ids, access=None):
        """Counts over what the caller may see -- the per-user system view."""
        scope = list(corpus_ids or [])
        if not scope:
            return {"documents": 0, "pages": 0, "chunks": 0, "skipped": 0}
        acl_sql, acl_params = access_filter.sql_clause(access)
        marks = ",".join("?" * len(scope))
        where = "d.corpus_id IN (%s) AND d.file=1 AND %s" % (marks, acl_sql)
        params = scope + acl_params
        with self._lock:
            documents, pages, skipped, ocr_pending = self._conn.execute(
                "SELECT COUNT(*), IFNULL(SUM(d.pages), 0), "
                "IFNULL(SUM(CASE WHEN d.skip_reason IS NOT NULL THEN 1 ELSE 0 END), 0), "
                "IFNULL(SUM(CASE WHEN d.ocr_state='pending' THEN 1 ELSE 0 END), 0) "
                "FROM documents d WHERE " + where, params).fetchone()
            chunks = self._conn.execute(
                "SELECT COUNT(*) FROM chunks c JOIN documents d ON d.doc_id=c.doc_id WHERE "
                + where, params).fetchone()[0]
        return {"documents": documents, "pages": pages, "chunks": chunks, "skipped": skipped,
                "ocr_pending": ocr_pending}

    # ----- directory ---------------------------------------------------------
    def upsert_directory(self, corpus_id, tenant_id, path, acl=None):
        path = norm_path(path)
        doc_id = _doc_id(tenant_id, corpus_id, path)
        with self._lock:
            self._ensure_corpus(corpus_id, tenant_id)
            self._delete_doc_rows(doc_id)
            previous = self._acl_snapshot(doc_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO documents("
                "doc_id, corpus_id, tenant_id, source_path, title, file, mtime, size, "
                "pages, chunks, indexed) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, corpus_id, tenant_id, path, os.path.basename(path) or path,
                 0, None, None, 0, 0, 1),
            )
            self._write_acl(doc_id, acl, previous)
            self._conn.commit()
        return {"doc_id": doc_id, "file": False, "source_path": path}

    # ----- two-step file sync ------------------------------------------------
    def check(self, corpus_id, tenant_id, path, size, mtime, head_b64, acl=None):
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
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime, "invalid_content", acl)
            return None, "invalid_content"
        if not extract.extractor_available(path):
            self._store_meta_only(corpus_id, tenant_id, path, size, mtime,
                                  "extractor_unavailable", acl)
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
                "acl": acl,
                "expires_at": time.time() + self.token_ttl_seconds,
            }
        return token, None

    def _store_meta_only(self, corpus_id, tenant_id, path, size, mtime, reason=None, acl=None):
        doc_id = _doc_id(tenant_id, corpus_id, path)
        with self._lock:
            self._ocr_tokens.pop(doc_id, None)  # an OCR of an earlier version must not land
            self._ensure_corpus(corpus_id, tenant_id)
            previous = self._acl_snapshot(doc_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO documents("
                "doc_id, corpus_id, tenant_id, source_path, title, file, mtime, size, "
                "pages, chunks, indexed, skip_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, corpus_id, tenant_id, path, os.path.basename(path) or path,
                 1, mtime, size, 0, 0, 0, reason),
            )
            self._write_acl(doc_id, acl, previous)
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
        return self._ingest_raw(meta["corpus_id"], meta["tenant_id"], meta["source_path"],
                                meta["title"], raw, meta["mtime"], meta["size"], meta.get("acl"))

    # ----- direct ingest -----------------------------------------------------
    def ingest_text(self, corpus_id, tenant_id, source_path, title, text, metadata):
        metadata = metadata or {}
        pages = [(1, "root", text.strip())] if text and text.strip() else []
        with self._lock:
            self._ocr_tokens.pop(_doc_id(tenant_id, corpus_id, norm_path(source_path)), None)
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
        return self._ingest_raw(corpus_id, tenant_id, norm_path(path),
                                title or os.path.basename(path), raw, metadata.get("mtime"),
                                metadata.get("size"), metadata.get("acl"))

    # ----- OCR (E10b) --------------------------------------------------------
    def _ingest_raw(self, corpus_id, tenant_id, source_path, title, raw, mtime, size, acl):
        """Index an upload. Pages without a text layer (and image files) are
        queued for OCR: the text pages are searchable at once, the rest follows
        when the background reader is done (documents.ocr_state 'pending')."""
        pages = extract.extract_pages(raw, source_path)
        doc_id = _doc_id(tenant_id, corpus_id, source_path)
        need = (ocr.pages_to_read(raw, source_path, pages, extract.pdf_page_breaks())
                if ocr.available() else [])
        if not need:
            with self._lock:
                self._ocr_tokens.pop(doc_id, None)
            return self._index_document(corpus_id=corpus_id, tenant_id=tenant_id,
                                        source_path=source_path, title=title, pages=pages,
                                        mtime=mtime, size=size, acl=acl)
        token = uuid.uuid4().hex
        with self._lock:
            queued = self._ocr_bytes + len(raw) <= OCR_QUEUE_BYTES
            if queued:
                self._ocr_tokens[doc_id] = token
                self._ocr_bytes += len(raw)
            else:
                self._ocr_tokens.pop(doc_id, None)
        state = "pending" if queued else "deferred"
        result = self._index_document(corpus_id=corpus_id, tenant_id=tenant_id,
                                      source_path=source_path, title=title, pages=pages,
                                      mtime=mtime, size=size, acl=acl, ocr_state=state)
        if queued:
            self._ocr_queue.put({"doc_id": doc_id, "token": token, "corpus_id": corpus_id,
                                 "tenant_id": tenant_id, "source_path": source_path,
                                 "title": title, "raw": raw, "pages": pages, "need": need,
                                 "mtime": mtime, "size": size})
            self._start_ocr_worker()
        result["ocr"] = {"state": state, "pages": len(need)}
        if "warning" in result:
            result["warning"] = _OCR_SKIP[state]
        return result

    def _recover_ocr(self):
        """Uploads waiting for OCR lived in memory only, so after a restart their
        documents are marked for a resync instead of waiting forever."""
        with self._lock:
            self._conn.execute(
                "UPDATE documents SET ocr_state='interrupted', skip_reason=CASE WHEN chunks=0 "
                "THEN 'ocr_interrupted' ELSE skip_reason END WHERE ocr_state='pending'")
            self._conn.commit()

    def _start_ocr_worker(self):
        with self._lock:
            if self._ocr_thread is None or not self._ocr_thread.is_alive():
                self._ocr_thread = threading.Thread(target=self._ocr_loop, name="ocr",
                                                    daemon=True)
                self._ocr_thread.start()

    def _ocr_current(self, job):
        with self._lock:
            return self._ocr_tokens.get(job["doc_id"]) == job["token"]

    def _ocr_loop(self):
        while True:
            job = self._ocr_queue.get()
            try:
                self._ocr_job(job)
            except Exception as error:  # noqa: BLE001 -- one bad file must not stop the queue
                sys.stderr.write("ocr: %s failed: %s\n" % (job["source_path"], error))
                with self._lock:
                    if self._ocr_tokens.get(job["doc_id"]) == job["token"]:
                        self._ocr_tokens.pop(job["doc_id"], None)
                        self._conn.execute(
                            "UPDATE documents SET ocr_state='failed', skip_reason=CASE WHEN "
                            "chunks=0 THEN 'ocr_failed' ELSE skip_reason END WHERE doc_id=?",
                            (job["doc_id"],))
                        self._conn.commit()
            finally:
                with self._lock:
                    self._ocr_bytes -= len(job["raw"])
                self._ocr_queue.task_done()

    def _ocr_job(self, job):
        if not self._ocr_current(job):
            return  # replaced or deleted while it waited
        results = ocr.read_pages(job["raw"], job["source_path"], job["need"],
                                 lambda: self._ocr_current(job))
        if not self._ocr_current(job):
            return
        pages = list(job["pages"])
        meta = {}
        for number, read in results.items():
            meta[number] = {"status": read["status"], "quality": read["quality"],
                            "lang": read["lang"], "rotation": read["rotation"]}
            if read["status"] == "ok":
                pages.append((number, "root", read["text"]))
        pages.sort(key=lambda page: page[0])
        # acl=None keeps the ACL stored with the upload (or changed since).
        self._index_document(corpus_id=job["corpus_id"], tenant_id=job["tenant_id"],
                             source_path=job["source_path"], title=job["title"], pages=pages,
                             mtime=job["mtime"], size=job["size"], acl=None, ocr_state="done",
                             page_meta=meta, still_current=lambda: self._ocr_current(job))
        with self._lock:
            if self._ocr_tokens.get(job["doc_id"]) == job["token"]:
                self._ocr_tokens.pop(job["doc_id"], None)

    def ocr_idle(self, timeout=None):
        """Wait until the OCR queue is empty (tests, maintenance); True when it is."""
        deadline = None if timeout is None else time.time() + timeout
        while self._ocr_queue.unfinished_tasks:
            if deadline is not None and time.time() > deadline:
                return False
            time.sleep(0.05)
        return True

    def _page_ocr(self, page_id):
        """{"quality", "lang"} of a recognised page, None for a text layer."""
        row = self._conn.execute(
            "SELECT kind, ocr_quality, ocr_lang FROM pages WHERE page_id=?", (page_id,)).fetchone()
        if row and row[0] == "ocr":
            return {"quality": row[1], "lang": row[2]}
        return None

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

    def _index_document(self, corpus_id, tenant_id, source_path, title, pages, mtime, size,
                        acl=None, ocr_state=None, page_meta=None, still_current=None):
        """(Re)index one document. `page_meta` {page: {status, quality, lang,
        rotation}} marks recognised pages ('ocr') and unreadable ones ('skipped',
        recorded without text); `still_current()` is checked under the write lock
        so an OCR result never overwrites a newer upload or a deletion."""
        doc_id = _doc_id(tenant_id, corpus_id, source_path)
        page_meta = page_meta or {}
        page_records = []
        chunk_records = []
        chunk_texts = []
        numbers = {number for number, _, _ in pages}
        for number in sorted(n for n in page_meta if n not in numbers):
            page_records.append(("%s-p%d" % (doc_id, number), number, "root"))
        for (page_number, section_path, page_text) in pages:
            page_id = "%s-p%d" % (doc_id, page_number)
            page_records.append((page_id, page_number, section_path))
            parts = chunk_page(source_path, page_text)
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
            if still_current is not None and not still_current():
                return {"doc_id": doc_id, "superseded": True}
            # The corpus may have been reconfigured while we were embedding.
            self._corpus_embedding_guard(corpus_id, tenant_id)
            self._note_dim(vectors)
            self._ensure_vec_table()
            self._delete_doc_rows(doc_id)

            for page_id, page_number, section_path in page_records:
                read = page_meta.get(page_number)
                kind = None if read is None else ("ocr" if read["status"] == "ok" else "skipped")
                self._conn.execute(
                    "INSERT OR REPLACE INTO pages(page_id, doc_id, page_number, section_path, "
                    "kind, ocr_status, ocr_quality, ocr_lang, ocr_rotation) VALUES(?,?,?,?,?,?,?,?,?)",
                    (page_id, doc_id, page_number, section_path, kind,
                     read and read["status"], read and read["quality"], read and read["lang"],
                     read and read["rotation"]),
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
            skip_reason = None if chunk_records else _OCR_SKIP.get(ocr_state,
                                                                   EMPTY_EXTRACTION_WARNING)
            previous = self._acl_snapshot(doc_id)
            self._conn.execute(
                "INSERT OR REPLACE INTO documents("
                "doc_id, corpus_id, tenant_id, source_path, title, file, mtime, size, "
                "pages, chunks, indexed, skip_reason, ocr_state) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, corpus_id, tenant_id, source_path, title, 1, mtime, size,
                 len(page_records), len(chunk_records), 1 if chunk_records else 0,
                 skip_reason, ocr_state),
            )
            # Same transaction as the chunks: a search never sees the new
            # chunks without the ACL that guards them.
            self._write_acl(doc_id, acl, previous)
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
                self._ocr_tokens.pop(doc_id, None)  # a queued OCR must not bring it back
                deleted_pages += self._conn.execute(
                    "SELECT COUNT(*) FROM pages WHERE doc_id=?", (doc_id,)
                ).fetchone()[0]
                deleted_chunks += self._conn.execute(
                    "SELECT COUNT(*) FROM chunks WHERE doc_id=?", (doc_id,)
                ).fetchone()[0]
                self._delete_doc_rows(doc_id)
                self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
                self._conn.execute("DELETE FROM doc_acl WHERE doc_id=?", (doc_id,))
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
    def _vocabulary(self, corpus_ids, acl_sql="1", acl_params=(), acl_key=None):
        """The scope's accented words, rebuilt when any of its corpora change.

        When the access filter hides part of the scope, the vocabulary is
        built from the visible chunks only: a word that exists only in a
        hidden document must not steer the query repair."""
        scope = tuple(corpus_ids)
        marks = ",".join("?" * len(scope))
        where = "c.corpus_id IN (%s) AND %s" % (marks, acl_sql)
        params = list(scope) + list(acl_params)
        source = "chunks c JOIN documents d ON d.doc_id=c.doc_id"
        signature = self._conn.execute(
            "SELECT COUNT(*), IFNULL(MAX(c.rowid), 0) FROM " + source + " WHERE " + where,
            params,
        ).fetchone()
        key = (scope, acl_key)
        cached = self._vocabularies.get(key)
        if cached is not None and cached[0] == signature:
            return cached[1]
        rows = self._conn.execute(
            "SELECT c.text FROM " + source + " WHERE " + where + " LIMIT ?",
            params + [REACCENT_MAX_CHUNKS],
        )
        vocabulary = reaccent.build(text for (text,) in rows)
        if acl_key is not None and len(self._vocabularies) >= 64:
            # Per-caller vocabularies are bounded; the unfiltered ones stay.
            for stale in [k for k in self._vocabularies if k[1] is not None][:16]:
                self._vocabularies.pop(stale, None)
        self._vocabularies[key] = (signature, vocabulary)
        return vocabulary

    def search_context(self, corpus_id, question, top_k=3, max_context_tokens=4000,
                       corpus_ids=None, source_paths=None, evidence_chunk_ids=None,
                       access=None):
        scope = normalize_corpus_ids(corpus_id, corpus_ids)
        if not scope:
            return [], {"chunk_hits_count": 0, "estimated_tokens": 0}
        # E03: the access filter is part of every query below (dense, lexical,
        # evidence, counts), never a post-filter over the results.
        acl_sql, acl_params = access_filter.sql_clause(access)
        with self._lock:
            totals = {}
            filtered = {}
            for cid in scope:
                tenant = "default"
                row = self._conn.execute(
                    "SELECT tenant_id FROM corpora WHERE corpus_id=?", (cid,)
                ).fetchone()
                if row is not None and row[0]:
                    tenant = row[0]
                self._corpus_embedding_guard(cid, tenant)
                totals[cid] = self._conn.execute(
                    "SELECT COUNT(*) FROM chunks c JOIN documents d ON d.doc_id=c.doc_id "
                    "WHERE c.corpus_id=? AND c.vec_rowid IS NOT NULL AND " + acl_sql,
                    [cid] + acl_params,
                ).fetchone()[0]
                # The partitioned KNN cannot take the filter, so a corpus with
                # hidden chunks is ranked with the filtered exact scan instead.
                filtered[cid] = access is not None and self._excluded(cid, acl_sql, acl_params)
            total = sum(totals.values())

            k = min(max(top_k, 0), 50)
            if not question or not self._vec_ready or k == 0 or total == 0:
                return [], {"chunk_hits_count": total, "estimated_tokens": 0}

            queries = query_split.split_question(question)
            if REACCENT_ACTIVE:
                if any(filtered.values()):
                    vocabulary = self._vocabulary(
                        scope, acl_sql, acl_params,
                        tuple(sorted(access["principals"])) + (
                            access["clearance"], access["membership_known"],
                            access["default_classification"]))
                else:
                    vocabulary = self._vocabulary(scope)
                queries = [vocabulary.repair(q) for q in queries]
            # The question is embedded ONCE for the whole scope: the embedding
            # is the expensive step, the per-partition scan is not.
            qvecs = [v for v in self._embed(queries) if v]
            if not qvecs:
                return [], {"chunk_hits_count": total, "estimated_tokens": 0}

            k = min(k, total)
            pool_size = min(max(k * 8, 40), 256)
            marks = ",".join("?" * len(scope))
            clause = "c.corpus_id IN (%s)" % marks
            bindings = list(scope)
            if source_paths:
                source_clauses = []
                for source in source_paths[:20]:
                    source = norm_path(source.replace("\\", "/"))
                    if "/" in source:
                        source_clauses.append("lower(d.source_path)=lower(?)")
                        bindings.append(source)
                    else:
                        source_clauses.append("(d.source_path LIKE ? ESCAPE '\\' OR lower(d.source_path)=lower(?))")
                        bindings.extend(["%/" + _like_escape(source), source])
                clause += " AND (" + " OR ".join(source_clauses) + ")"
            if access is not None:
                clause += " AND " + acl_sql
                bindings.extend(acl_params)
            runs = []
            for qvec in qvecs:
                blob = sqlite_vec.serialize_float32(qvec)
                kept = []
                for cid in scope:
                    if not totals[cid]:
                        continue
                    if source_paths or filtered[cid]:
                        rows = self._conn.execute(
                            "SELECT c.chunk_id, vec_distance_cosine(v.embedding, ?) AS distance "
                            "FROM chunks c JOIN documents d ON d.doc_id=c.doc_id "
                            "JOIN chunk_vectors v ON v.rowid=c.vec_rowid WHERE " + clause +
                            " AND c.corpus_id=? ORDER BY distance LIMIT ?",
                            [blob] + bindings + [cid, pool_size]).fetchall()
                    else:
                        rows = self._conn.execute(
                            "SELECT chunk_id, distance FROM chunk_vectors "
                            "WHERE corpus_id=? AND embedding MATCH ? AND k=? ORDER BY distance",
                            (cid, blob, min(pool_size, totals[cid])),
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
                runs.append(kept[:pool_size])
            dense = query_split.interleave(runs, pool_size)
            dense_scores = dict(dense)
            query_terms = retrieval.terms(question)
            lexical = []
            if query_terms:
                scope_expression = " OR ".join('"' + cid.replace('"', '""') + '"' for cid in scope)
                text_expression = " OR ".join('"' + term + '"' for term in query_terms)
                expression = "corpus_id:(" + scope_expression + ") AND text:(" + text_expression + ")"
                lexical = [row[0] for row in self._conn.execute(
                    "SELECT c.chunk_id FROM chunk_text_fts "
                    "JOIN chunks c ON c.rowid=chunk_text_fts.rowid "
                    "JOIN documents d ON d.doc_id=c.doc_id WHERE chunk_text_fts MATCH ? AND " +
                    clause + " ORDER BY bm25(chunk_text_fts, 1.0, 0.0), c.chunk_id LIMIT ?",
                    [expression] + bindings + [pool_size]).fetchall()]
            evidence = []
            for chunk_id in (evidence_chunk_ids or [])[:8]:
                if self._conn.execute(
                    "SELECT 1 FROM chunks c JOIN documents d ON d.doc_id=c.doc_id WHERE " +
                    clause + " AND c.chunk_id=?", bindings + [chunk_id]).fetchone():
                    evidence.append(chunk_id)
            hits = retrieval.fuse([chunk_id for chunk_id, _ in dense], lexical, evidence)

            contexts = []
            used_tokens = 0
            seen_text = set()
            for chunk_id in hits:
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
                text_key = (chunk_corpus, " ".join(text.casefold().split()))
                if text_key in seen_text:
                    continue
                seen_text.add(text_key)
                score = dense_scores.get(chunk_id)
                if score is None:
                    distances = [self._conn.execute(
                        "SELECT vec_distance_cosine(v.embedding, ?) FROM chunk_vectors v "
                        "JOIN chunks c ON c.vec_rowid=v.rowid WHERE c.chunk_id=?",
                        (sqlite_vec.serialize_float32(qvec), chunk_id)).fetchone()
                        for qvec in qvecs]
                    score = max((1.0 - distance[0] for distance in distances if distance), default=0.0)
                if MIN_SCORE_ACTIVE and score < MIN_SCORE:
                    continue
                est = _estimate_tokens(text)
                if contexts and used_tokens + est > max_context_tokens:
                    continue
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
                        "keyword": 1.0 if chunk_id in lexical else 0.0,
                    },
                    "metadata": {
                        "file": bool(file_flag),
                        "mtime": mtime,
                        "size": size,
                    },
                })
                recognised = self._page_ocr(page_id)
                if recognised:
                    contexts[-1]["ocr"] = recognised
                if len(contexts) >= k:
                    break
            return contexts, {"chunk_hits_count": total, "estimated_tokens": used_tokens,
                              "dense_candidates": len(dense), "lexical_candidates": len(lexical),
                              "evidence_revalidated": len(evidence), "retrieval": "dense+fts5",
                              "access_filter": access is not None}

    # ----- whole-document retrieval ------------------------------------------
    def document_context(self, corpus_id, source_path, max_context_tokens=12000, access=None):
        """Every chunk of ONE document, in reading order.

        Deliberately NOT a search: when the user points at a file and asks for
        an analysis of it, similarity ranking would silently drop the sections
        that happen not to match the question. The caller names the document, so
        the whole document is the context — bounded only by the token budget,
        which the debug block reports.
        """
        target = norm_path(source_path or "")
        base = target.rsplit("/", 1)[-1]
        # A hidden document is answered exactly like a missing one.
        acl_sql, acl_params = access_filter.sql_clause(access)
        columns = ("SELECT d.doc_id, d.title, d.source_path, d.file, d.mtime, d.size "
                   "FROM documents d WHERE d.corpus_id=? AND d.file=1 AND " + acl_sql)
        with self._lock:
            row = self._conn.execute(
                columns + " AND d.source_path=?", [corpus_id] + acl_params + [target]).fetchone()
            if row is None and base:
                row = self._conn.execute(
                    columns + " AND d.source_path LIKE ? ESCAPE '\\' "
                    "ORDER BY LENGTH(d.source_path) LIMIT 1",
                    [corpus_id] + acl_params + ["%/" + _like_escape(base)]).fetchone()
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
            with self._lock:
                recognised = self._page_ocr(page_id)
            if recognised:
                contexts[-1]["ocr"] = recognised
        return contexts, _document_debug(meta, len(rows), len(contexts), used_tokens)

    # ----- full-coverage enumeration -----------------------------------------
    def match_chunks(self, corpus_ids, match_terms, mode="any", access=None,
                     path_prefixes=None, limit=200, offset=0):
        """EVERY visible chunk of the scope that matches the terms (not a top-k),
        in reading order (path, page, position), one page of it at a time.
        Scope, folder and access filters are part of the query."""
        scope = normalize_corpus_ids(None, corpus_ids)
        expression = retrieval.fts_query(match_terms, mode)
        if not scope or not expression:
            return [], {"total": 0, "documents": 0}
        acl_sql, acl_params = access_filter.sql_clause(access)
        clause = "c.corpus_id IN (%s) AND d.file=1 AND %s" % (",".join("?" * len(scope)), acl_sql)
        bindings = list(scope) + list(acl_params)
        if path_prefixes:
            paths = []
            for prefix in path_prefixes:
                prefix = norm_path(prefix.replace("\\", "/")).rstrip("/")
                paths.append("(d.source_path=? OR d.source_path LIKE ? ESCAPE '\\')")
                bindings.extend([prefix, _like_escape(prefix) + "/%"])
            clause += " AND (" + " OR ".join(paths) + ")"
        base = ("FROM chunk_text_fts JOIN chunks c ON c.rowid=chunk_text_fts.rowid "
                "JOIN documents d ON d.doc_id=c.doc_id WHERE chunk_text_fts MATCH ? AND " + clause)
        with self._lock:
            total, documents = self._conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT c.doc_id) " + base,
                [expression] + bindings).fetchone()
            rows = self._conn.execute(
                "SELECT c.chunk_id, c.doc_id, c.corpus_id, d.title, d.source_path, "
                "c.page_number, c.text, c.page_id " + base +
                " ORDER BY d.source_path, c.page_number, c.rowid LIMIT ? OFFSET ?",
                [expression] + bindings + [int(limit), int(offset)]).fetchall()
            keys = ("chunk_id", "doc_id", "corpus_id", "title", "source_path", "page_number",
                    "text")
            out = []
            for row in rows:
                chunk = dict(zip(keys, row[:-1]))
                recognised = self._page_ocr(row[-1])
                if recognised:
                    chunk["ocr"] = recognised
                out.append(chunk)
        return out, {"total": total, "documents": documents}

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
        sql = ("SELECT d.doc_id, d.source_path, d.corpus_id, d.size, d.skip_reason, d.ocr_state "
               "FROM documents d WHERE d.file=1 AND (d.skip_reason IS NOT NULL OR d.ocr_state "
               "IN ('pending', 'deferred', 'interrupted', 'failed') OR EXISTS(SELECT 1 FROM pages p "
               "WHERE p.doc_id=d.doc_id AND p.kind='skipped'))")
        params = ()
        if corpus_id:
            sql += " AND d.corpus_id=?"
            params = (corpus_id,)
        out = []
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY d.source_path", params).fetchall()
            for doc_id, path, corpus, size, reason, state in rows:
                item = {"source_path": path, "corpus_id": corpus, "size": size,
                        "reason": reason or (_OCR_SKIP.get(state) if state != "done" else None)
                        or "ocr_pages_skipped"}
                if state:
                    item["ocr_state"] = state
                # E10b: the pages OCR could not read, listed even when the rest of
                # the document is searchable.
                pages = self._conn.execute(
                    "SELECT page_number, ocr_status, ocr_quality FROM pages "
                    "WHERE doc_id=? AND kind='skipped' ORDER BY page_number", (doc_id,)).fetchall()
                if pages:
                    item["skipped_pages"] = [{"page": n, "reason": s, "quality": q}
                                             for n, s, q in pages]
                out.append(item)
        return out
