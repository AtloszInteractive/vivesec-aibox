"""Hybrid retrieval fusion — the A2 step on top of dense search.

Invariant 4 (spec §2): all three retrievers start. The production engine fuses
bge-m3 DENSE + bge-m3 learned-SPARSE + lexical BM25, then reranks with a
cross-encoder (spec §5.3). This module provides the model-free skeleton of that:

    - DENSE      : cosine over embeddings (computed in engine.py, passed in here)
    - WORD-BM25  : classic lexical BM25 over word tokens
    - CHAR-BM25  : BM25 over character trigrams — a subword/"sparse" signal that
                   catches morphology + multilingual variants (HU/DA/DE) the word
                   index misses. Honest stand-in until bge-m3 learned-sparse lands.

The three ranked lists are fused with Reciprocal Rank Fusion (RRF), which is
score-scale-agnostic — exactly why it's the standard glue for hybrid retrieval.
A cross-encoder reranker then slots in behind `rerank` (no-op until a model is
mounted, mirroring the embeddings/Docling optional-backend pattern).

Stdlib-only (re, math, collections) — no new dependencies.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Iterable

_WORD = re.compile(r"\w+", re.UNICODE)

RRF_K = 60  # standard RRF damping constant


def word_tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def char_ngrams(text: str, n: int = 3) -> list[str]:
    """Character n-grams over word-internal positions (with word boundaries)."""
    grams: list[str] = []
    for tok in _WORD.findall(text.lower()):
        s = f"#{tok}#"
        if len(s) <= n:
            grams.append(s)
            continue
        grams.extend(s[i : i + n] for i in range(len(s) - n + 1))
    return grams


def bm25_scores(
    query_terms: list[str],
    docs: list[tuple[str, list[str]]],
    k1: float = 1.5,
    b: float = 0.75,
) -> dict[str, float]:
    """BM25 over an arbitrary term space (words or char-ngrams).

    `docs` is [(doc_id, terms), ...]; stats are computed over THIS candidate set
    (already ACL-filtered), which is correct for the pre-filtered demo scale.
    """
    n = len(docs)
    if n == 0:
        return {}
    df: Counter[str] = Counter()
    for _, terms in docs:
        df.update(set(terms))
    avgdl = sum(len(terms) for _, terms in docs) / n or 1.0

    q = set(query_terms)
    scores: dict[str, float] = {}
    for doc_id, terms in docs:
        tf = Counter(terms)
        dl = len(terms) or 1
        s = 0.0
        for term in q:
            f = tf.get(term, 0)
            if f == 0:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores[doc_id] = s
    return scores


def _ranked_ids(scores: dict[str, float], all_ids: list[str]) -> list[str]:
    """Order ids best-first; ids absent from `scores` (or zero) rank last but are
    still present, so RRF is well-defined over a stable candidate universe."""
    return sorted(all_ids, key=lambda i: scores.get(i, 0.0), reverse=True)


def rrf_fuse(rank_lists: Iterable[list[str]], k: int = RRF_K) -> dict[str, float]:
    agg: dict[str, float] = defaultdict(float)
    for rl in rank_lists:
        for rank, doc_id in enumerate(rl):
            agg[doc_id] += 1.0 / (k + rank + 1)
    return agg


def fuse_dense_lexical(
    query: str,
    candidate_ids: list[str],
    dense_scores: dict[str, float],
    word_docs: list[tuple[str, list[str]]],
    char_docs: list[tuple[str, list[str]]],
) -> list[tuple[str, float]]:
    """Run WORD-BM25 + CHAR-BM25, fuse with the passed DENSE scores via RRF.

    Returns [(chunk_id, fused_score)] ordered best-first, fused_score in [0,1]
    (normalized RRF) so it's a presentable confidence-ish number.
    """
    if not candidate_ids:
        return []

    word_bm25 = bm25_scores(word_tokens(query), word_docs)
    char_bm25 = bm25_scores(char_ngrams(query), char_docs)

    rank_lists = [
        _ranked_ids(dense_scores, candidate_ids),
        _ranked_ids(word_bm25, candidate_ids),
        _ranked_ids(char_bm25, candidate_ids),
    ]
    fused = rrf_fuse(rank_lists)
    ordered = sorted(candidate_ids, key=lambda i: fused.get(i, 0.0), reverse=True)

    top = fused.get(ordered[0], 0.0) if ordered else 0.0
    norm = top or 1.0
    return [(cid, fused.get(cid, 0.0) / norm) for cid in ordered]
