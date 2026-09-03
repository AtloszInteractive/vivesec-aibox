"""Retrieval engine — the swappable core.

`RagEngine` is the contract-internal interface. `BaselineEngine` is a minimal,
RUNNABLE retriever so the /ingest + /search HTTP contract works end-to-end today.
It is NOT yet the full production retriever — it does DENSE retrieval over a
persistent sqlite vector store (cosine on bge-m3 / hashing-fallback embeddings).

Status (this engine, vs the production target in spec §5.3):
    - extract: Docling (PDF/DOCX/XLSX -> structured text + tables)      [done, optional]
    - chunk:   structure-aware 350-700 tok, overlap 50-100 (spec §4.4) [done]
    - embed:   bge-m3 via Ollama, hashing fallback for dev/CI           [done, embeddings.py]
    - store:   sqlite vector store (persistent)                         [done, store.py]
               postgres+pgvector is the production-scale alternative    [TODO, spec §11.1]
    - search:  DENSE cosine + allowed_file_ids pre-filter               [done]
               + word-BM25 + char-BM25 -> RRF                          [done, retrieval.py]
               + cross-encoder rerank (lexical stand-in for now)       [done, rerank.py]

INVARIANTS the engine MUST keep (spec §2):
    1. internal-only (enforced at the network/app layer, not here)
    2. allowed_file_ids is a HARD pre-filter; empty => zero hits; never widen it
    3. chunk_text is stored verbatim and returned
    4. all three retrievers start (dense + sparse + BM25)
    5. ingest- and query-embedding use the same model
    6. model weights come from a mounted volume
"""
from __future__ import annotations

import json
import time

from .chunking import chunk_blocks
from .config import settings
from .embeddings import build_embedder
from .extract import extract_blocks
from .rerank import lexical_rerank
from .retrieval import char_ngrams, fuse_dense_lexical, word_tokens
from .schemas import (
    EngineInfo,
    Hit,
    IngestRequest,
    IngestResponse,
    SearchRequest,
    SearchResponse,
    SourceRef,
)
from .store import VectorStore


class RagEngine:
    """Interface the HTTP layer depends on. Swap the implementation freely."""

    def info(self) -> EngineInfo:  # pragma: no cover - interface
        raise NotImplementedError

    def index_ready(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def stats(self) -> tuple[int, int]:  # pragma: no cover - interface
        """(files_indexed, chunks_indexed)"""
        raise NotImplementedError

    def ingest(self, req: IngestRequest) -> IngestResponse:  # pragma: no cover - interface
        raise NotImplementedError

    def search(self, req: SearchRequest) -> SearchResponse:  # pragma: no cover - interface
        raise NotImplementedError


class BaselineEngine(RagEngine):
    """Runnable skeleton. Replace internals; keep the contract identical."""

    def __init__(self) -> None:
        self._embedder = build_embedder(settings)
        self._store = VectorStore(settings.STORE_PATH)

    # ---- identity -------------------------------------------------------- #
    def info(self) -> EngineInfo:
        return EngineInfo(
            name=settings.ENGINE_NAME,
            version=settings.ENGINE_VERSION,
            embed_model=self._embedder.name,
            embed_dim=self._embedder.dim,
        )

    def index_ready(self) -> bool:
        return self._store.count_chunks() > 0

    def stats(self) -> tuple[int, int]:
        return self._store.stats()

    # ---- ingest ---------------------------------------------------------- #
    def ingest(self, req: IngestRequest) -> IngestResponse:
        # Idempotency: same file_id + content_hash => no-op (spec §3).
        if self._store.get_file_hash(req.file_id) == req.content_hash:
            return IngestResponse(
                file_id=req.file_id, status="unchanged", engine=self.info(),
            )

        # Ingest pipeline: extract (Docling/markdown) -> structure-aware chunks
        # -> embed (same model as query, invariant 5) -> upsert into the store.
        raw = self._raw_bytes(req)
        blocks = extract_blocks(raw, req.mime_type, req.filename)
        pieces = chunk_blocks(blocks)
        texts = [p.text for p in pieces]
        vectors = self._embedder.embed(texts) if texts else []

        rows = [
            {
                "chunk_id": f"{req.file_id}#{i}",
                "file_id": req.file_id,
                "chunk_index": i,
                "chunk_text": piece.text,  # verbatim (invariant 3)
                "acl_scope": req.acl_scope,
                "sensitivity": req.sensitivity,
                "language": req.language_hint or "en",
                "doc_type": req.doc_type,
                "section_path": piece.section_path,
                "page": piece.page,
                "filename": req.filename,
                "embedding": vectors[i],
            }
            for i, piece in enumerate(pieces)
        ]
        # Replace atomically (re-index drops the file's previous chunks).
        self._store.replace_file(req.file_id, req.content_hash, rows)
        return IngestResponse(
            file_id=req.file_id,
            status="indexed",
            chunks_indexed=len(rows),
            chunks_total=len(rows),
            engine=self.info(),
        )

    # ---- search ---------------------------------------------------------- #
    def search(self, req: SearchRequest) -> SearchResponse:
        t0 = time.perf_counter()

        # INVARIANT 2: empty allowed set => zero hits; engine never widens it.
        allowed = list(dict.fromkeys(req.allowed_file_ids))
        if not allowed:
            return SearchResponse(
                query=req.query, hits=[], engine=self.info(),
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        # HYBRID retrieval (A2): three retrievers START (invariant 4) over the
        # ACL-pre-filtered candidate set, fused with RRF:
        #   dense (embeddings cosine) + word-BM25 + char-BM25 (subword/"sparse").
        # A cross-encoder rerank (spec §5.3) slots in behind _rerank().
        doc_types = req.filters.doc_type if req.filters else None
        candidates = self._store.get_candidates(allowed, doc_types)
        if not candidates:
            return SearchResponse(
                query=req.query, hits=[], engine=self.info(),
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        q_vec = self._embedder.embed([req.query])[0]
        dense_scores = {row["chunk_id"]: self._store.cosine(q_vec, row) for row in candidates}
        word_docs = [(row["chunk_id"], word_tokens(row["chunk_text"])) for row in candidates]
        char_docs = [(row["chunk_id"], char_ngrams(row["chunk_text"])) for row in candidates]
        ids = [row["chunk_id"] for row in candidates]

        fused = fuse_dense_lexical(req.query, ids, dense_scores, word_docs, char_docs)
        row_by_id = {row["chunk_id"]: row for row in candidates}
        ranked = [(score, row_by_id[cid]) for cid, score in fused]

        # Rerank the top candidate window, then keep top_k (spec §5.3).
        ranked = self._rerank(req.query, ranked[: settings.CANDIDATE_K])
        top = ranked[: max(1, req.top_k)]

        hits: list[Hit] = []
        for rank, (score, row) in enumerate(top, start=1):
            hits.append(
                Hit(
                    chunk_id=row["chunk_id"],
                    file_id=row["file_id"],
                    chunk_index=row["chunk_index"],
                    score=round(float(score), 4),
                    rank=rank,
                    chunk_text=row["chunk_text"] if req.options.return_text else "",
                    section_path=json.loads(row["section_path"] or "[]"),
                    page=row["page"],
                    source_ref=SourceRef(filename=row["filename"], page=row["page"]),
                )
            )

        return SearchResponse(
            query=req.query,
            hits=hits,
            engine=self.info(),
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    # ---- helpers --------------------------------------------------------- #
    def _rerank(
        self, query: str, ranked: list[tuple[float, object]]
    ) -> list[tuple[float, object]]:
        """Rerank hook (spec §5.3).

        When settings.RERANK is on, a dependency-free LEXICAL reranker reorders
        the top window by blending the fused score with query-term coverage. The
        production engine swaps this for a cross-encoder (e.g. bge-reranker)
        loaded from the mounted model volume; the seam + window size stay the
        same. Reordering only — the candidate set (and the ACL pre-filter) is
        never widened here.
        """
        if not settings.RERANK or not ranked:
            return ranked
        return lexical_rerank(query, ranked)

    @staticmethod
    def _raw_bytes(req: IngestRequest) -> bytes:
        import base64

        if req.content_base64:
            try:
                return base64.b64decode(req.content_base64)
            except Exception:
                return b""
        if req.content_uri:
            # Mounted-file path. Real engine reads the file from the volume here.
            try:
                with open(req.content_uri, "rb") as f:
                    return f.read()
            except OSError:
                return b""
        return b""


# Single process-wide engine instance. Swap construction here when wiring a real
# backend, or select by settings.ENGINE_NAME.
def build_engine() -> RagEngine:
    return BaselineEngine()
