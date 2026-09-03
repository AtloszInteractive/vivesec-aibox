"""Retrieval metrics for the parity harness.

Two families:
  - QUALITY: recall@k, precision@k, MRR, citation-hit (did we surface a relevant
    passage at all). These say "is the answer findable".
  - SAFETY:  acl_leak — the number of returned hits whose file_id is OUTSIDE the
    allowed set, OR explicitly in the case's forbidden set. This MUST be 0. A
    single leak fails the whole run regardless of quality (spec invariant 2).

All functions are pure so they are trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean


@dataclass
class CaseResult:
    case_id: str
    # quality
    recall_at_k: float
    precision_at_k: float
    reciprocal_rank: float
    citation_hit: bool
    # safety
    acl_leaks: list[str]  # file_ids that leaked (allowed-set violation or forbidden)
    # perf
    latency_ms: float
    # context
    returned_file_ids: list[str] = field(default_factory=list)


def _hit_file_ids(hits: list[dict]) -> list[str]:
    return [h.get("file_id", "") for h in hits]


def _hit_chunk_ids(hits: list[dict]) -> list[str]:
    return [h.get("chunk_id", "") for h in hits]


def recall_at_k(hits: list[dict], relevant_ids: set[str], use_chunks: bool) -> float:
    if not relevant_ids:
        return 1.0  # nothing to find => trivially satisfied
    found = set(_hit_chunk_ids(hits) if use_chunks else _hit_file_ids(hits))
    return len(relevant_ids & found) / len(relevant_ids)


def precision_at_k(hits: list[dict], relevant_ids: set[str], use_chunks: bool) -> float:
    if not hits:
        return 0.0
    found = _hit_chunk_ids(hits) if use_chunks else _hit_file_ids(hits)
    hit_rel = sum(1 for x in found if x in relevant_ids)
    return hit_rel / len(found)


def reciprocal_rank(hits: list[dict], relevant_ids: set[str], use_chunks: bool) -> float:
    found = _hit_chunk_ids(hits) if use_chunks else _hit_file_ids(hits)
    for rank, x in enumerate(found, start=1):
        if x in relevant_ids:
            return 1.0 / rank
    return 0.0


def acl_leaks(hits: list[dict], allowed: set[str], forbidden: set[str]) -> list[str]:
    """A hit leaks if its file_id is not in `allowed` OR is in `forbidden`.
    The engine must never return anything outside the HARD pre-filter."""
    leaked: list[str] = []
    for fid in _hit_file_ids(hits):
        if fid and (fid not in allowed or fid in forbidden):
            leaked.append(fid)
    return leaked


def score_case(case: dict, response: dict, wall_ms: float, use_chunks: bool) -> CaseResult:
    hits = response.get("hits", [])
    allowed = set(case.get("allowed_file_ids", []))
    forbidden = set(case.get("forbidden_file_ids", []))
    relevant = set(
        case.get("relevant_chunk_ids", []) if use_chunks else case.get("relevant_file_ids", [])
    )
    # Prefer the engine-reported latency; fall back to wall clock.
    latency = float(response.get("latency_ms", 0)) or wall_ms

    return CaseResult(
        case_id=case.get("id", "?"),
        recall_at_k=recall_at_k(hits, relevant, use_chunks),
        precision_at_k=precision_at_k(hits, relevant, use_chunks),
        reciprocal_rank=reciprocal_rank(hits, relevant, use_chunks),
        citation_hit=recall_at_k(hits, relevant, use_chunks) > 0 if relevant else True,
        acl_leaks=acl_leaks(hits, allowed, forbidden),
        latency_ms=latency,
        returned_file_ids=_hit_file_ids(hits),
    )


@dataclass
class Aggregate:
    n: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    citation_rate: float
    acl_leak_total: int
    latency_p50_ms: float
    latency_p95_ms: float


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[idx]


def aggregate(results: list[CaseResult]) -> Aggregate:
    if not results:
        return Aggregate(0, 0, 0, 0, 0, 0, 0, 0)
    lat = [r.latency_ms for r in results]
    return Aggregate(
        n=len(results),
        recall_at_k=mean(r.recall_at_k for r in results),
        precision_at_k=mean(r.precision_at_k for r in results),
        mrr=mean(r.reciprocal_rank for r in results),
        citation_rate=mean(1.0 if r.citation_hit else 0.0 for r in results),
        acl_leak_total=sum(len(r.acl_leaks) for r in results),
        latency_p50_ms=_pct(lat, 50),
        latency_p95_ms=_pct(lat, 95),
    )
