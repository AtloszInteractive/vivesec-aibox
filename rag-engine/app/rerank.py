"""Reranking — reorders the top retrieval window before top_k is applied.

The production engine loads a cross-encoder (e.g. bge-reranker-v2-m3) from the
mounted model volume and scores each (query, chunk) pair jointly. That is
hardware-bound (Jetson), so until then this provides a dependency-free LEXICAL
reranker: it nudges chunks that actually contain the query terms upward — a cheap
cross-encoder stand-in that sharpens precision.

Reranking only REORDERS the candidate window; it never adds or drops rows, so the
ACL pre-filter and invariant 2 are untouched (no chunk outside allowed_file_ids
can appear, and the empty-allowed short-circuit upstream still holds).
"""
from __future__ import annotations

from typing import Any

from .retrieval import word_tokens

# How much the lexical signal adjusts the fused (dense+BM25 RRF) score.
# Conservative by design: the fused order dominates, lexical coverage only nudges.
LEXICAL_WEIGHT = 0.3


def _coverage(query_terms: list[str], chunk_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    hit = sum(1 for t in query_terms if t in chunk_terms)
    return hit / len(query_terms)


def lexical_rerank(
    query: str, ranked: list[tuple[float, Any]]
) -> list[tuple[float, Any]]:
    """Blend each fused score with query-term coverage, then re-sort.

    `ranked` is a list of (fused_score, row) where row exposes ["chunk_text"].
    Returns a new list ordered best-first; the input set is preserved exactly.
    """
    q_terms = list(dict.fromkeys(word_tokens(query)))
    if not q_terms or not ranked:
        return ranked
    rescored: list[tuple[float, Any]] = []
    for fused, row in ranked:
        cov = _coverage(q_terms, set(word_tokens(row["chunk_text"])))
        blended = (1.0 - LEXICAL_WEIGHT) * float(fused) + LEXICAL_WEIGHT * cov
        rescored.append((blended, row))
    rescored.sort(key=lambda x: x[0], reverse=True)
    return rescored
