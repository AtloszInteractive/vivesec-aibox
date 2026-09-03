"""Persistent vector store — sqlite-backed dense index.

This is the `sqlite` VECTOR_BACKEND (spec §11.1) in skeleton form: chunks +
their embeddings live in a single sqlite file, so the index SURVIVES restarts
(the in-memory list it replaces did not — every restart needed a full reingest).

Scope of this step (A1): dense retrieval only — cosine over embeddings, with the
allowed_file_ids HARD pre-filter applied IN SQL (invariant 2). Sparse + BM25 +
RRF + cross-encoder rerank (invariant 4, spec §5.3) are the next step and slot in
behind the same search() call.

Dependency-light on purpose (stdlib sqlite3 + array): no pgvector / faiss needed
for the demo corpus. Postgres+pgvector is the production-scale alternative behind
the same interface. Embeddings are stored as float32; vectors are pre-normalized
by the embedder, so cosine == dot product.
"""
from __future__ import annotations

import array
import json
import os
import sqlite3
import threading
from typing import Any, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id      TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    file_id      TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    chunk_text   TEXT NOT NULL,
    acl_scope    TEXT NOT NULL,   -- json list
    sensitivity  TEXT NOT NULL,
    language     TEXT,
    doc_type     TEXT,
    section_path TEXT,            -- json list
    page         INTEGER,
    filename     TEXT,
    embedding    BLOB NOT NULL    -- float32 array
);
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
"""


def _to_blob(vec: list[float]) -> bytes:
    return array.array("f", vec).tobytes()


def _from_blob(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def _dot(query: list[float], stored: array.array) -> float:
    # Both sides are L2-normalized by the embedder -> dot product == cosine.
    return sum(q * s for q, s in zip(query, stored))


class VectorStore:
    def __init__(self, path: str) -> None:
        self.path = path or ":memory:"
        self._lock = threading.Lock()
        if self.path != ":memory:":
            parent = os.path.dirname(self.path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        # uvicorn may touch the connection from worker threads.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    # ---- ingest side ----------------------------------------------------- #
    def get_file_hash(self, file_id: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT content_hash FROM files WHERE file_id = ?", (file_id,)
        ).fetchone()
        return row["content_hash"] if row else None

    def replace_file(self, file_id: str, content_hash: str, rows: list[dict[str, Any]]) -> None:
        """Atomically drop a file's old chunks and insert the new ones."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
            self._conn.execute("DELETE FROM files WHERE file_id = ?", (file_id,))
            self._conn.execute(
                "INSERT INTO files(file_id, content_hash) VALUES (?, ?)", (file_id, content_hash)
            )
            self._conn.executemany(
                """INSERT INTO chunks
                   (chunk_id, file_id, chunk_index, chunk_text, acl_scope, sensitivity,
                    language, doc_type, section_path, page, filename, embedding)
                   VALUES (:chunk_id, :file_id, :chunk_index, :chunk_text, :acl_scope, :sensitivity,
                           :language, :doc_type, :section_path, :page, :filename, :embedding)""",
                [self._row_params(r) for r in rows],
            )

    @staticmethod
    def _row_params(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_id": r["chunk_id"],
            "file_id": r["file_id"],
            "chunk_index": r["chunk_index"],
            "chunk_text": r["chunk_text"],
            "acl_scope": json.dumps(r.get("acl_scope", [])),
            "sensitivity": r.get("sensitivity", ""),
            "language": r.get("language"),
            "doc_type": r.get("doc_type"),
            "section_path": json.dumps(r.get("section_path", [])),
            "page": r.get("page"),
            "filename": r.get("filename"),
            "embedding": _to_blob(r["embedding"]),
        }

    # ---- search side ----------------------------------------------------- #
    def get_candidates(
        self,
        allowed_file_ids: list[str],
        doc_types: Optional[list[str]] = None,
    ) -> list[sqlite3.Row]:
        """ACL-pre-filtered candidate rows (invariant 2: empty allowed => none).

        The hybrid retrievers (dense + BM25 + char-BM25) all score over THIS set,
        so the pre-filter is enforced once, in SQL, before any ranking.
        """
        if not allowed_file_ids:
            return []
        params: list[Any] = list(allowed_file_ids)
        sql = (
            "SELECT * FROM chunks WHERE file_id IN (%s)"
            % ",".join("?" for _ in allowed_file_ids)
        )
        if doc_types:
            sql += " AND doc_type IN (%s)" % ",".join("?" for _ in doc_types)
            params.extend(doc_types)
        return list(self._conn.execute(sql, params))

    @staticmethod
    def cosine(query_vec: list[float], row: sqlite3.Row) -> float:
        # Embedder pre-normalizes both sides -> cosine == dot product.
        return _dot(query_vec, _from_blob(row["embedding"]))

    # ---- introspection --------------------------------------------------- #
    def stats(self) -> tuple[int, int]:
        nf = self._conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
        nc = self._conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
        return nf, nc

    def count_chunks(self) -> int:
        return self._conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
