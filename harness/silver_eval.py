"""Silver-set evaluation over the govdocs corpus (doc-level matching).

Bridges the CoLearn-format silver set (govdocs_silver_factory) onto the
existing metrics/gate machinery WITHOUT an intermediate gold file:

  - file_id       := normalized source_path (stable, human-readable)
  - allowed set   := every manifest path of the case's corpus_id — corpus
                     isolation IS the ACL pre-filter; a hit outside the
                     corpus universe resolves to itself and counts as a leak
                     (fail-closed, same invariant as runner.py)
  - relevant set  := normalized expected_source_paths (doc-level; chunk_id
                     schemes differ per engine BY DESIGN, so never match on
                     chunk_id — see repo decision 2026-07-07)
  - T4 negatives  := empty relevant set -> recall/citation trivially satisfied,
                     the case still exercises latency and the leak canary.

Ingest is NOT done here — run ingest_govdocs.py first (both engines must hold
the same corpus). Reuses CaseResult/aggregate + RunReport/print_run + gate.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

from client import EngineClient
from metrics import CaseResult, aggregate, score_case
from runner import MAX_CONTEXT_TOKENS, RunReport


def _norm(path: str) -> str:
    if path and len(path) > 1 and path.endswith("/"):
        return path.rstrip("/")
    return path


def load_silver(path: str, split: str, langs: list[str] | None = None,
                limit: int = 0) -> list[dict]:
    cases: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if item.get("split") != split:
                continue
            if langs and item.get("language") not in langs:
                continue
            cases.append(item)
    if limit:
        cases = cases[:limit]
    return cases


def corpus_universe(manifest_path: str) -> dict[str, set[str]]:
    """{corpus_id -> set(normalized source_path)} from corpus_manifest.jsonl."""
    universe: dict[str, set[str]] = defaultdict(set)
    with open(manifest_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                d = json.loads(line)
                universe[d["corpus_id"]].add(_norm(d["source_path"]))
    return dict(universe)


def run_silver(client: EngineClient, silver_path: str, manifest_path: str,
               split: str = "dev", langs: list[str] | None = None,
               limit: int = 0, top_k_override: int = 0) -> RunReport:
    universe = corpus_universe(manifest_path)
    cases = load_silver(silver_path, split, langs, limit)
    if not cases:
        raise SystemExit(f"no silver cases for split={split} langs={langs}")

    health = client.health()
    emb = health.get("embedding", {}) or {}
    engine_name = health.get("vector_backend", "?")
    engine_version = f"{emb.get('model_name', '?')}/{emb.get('dimension', '?')}d"

    results: list[CaseResult] = []
    for item in cases:
        corpus_id = item["corpus_id"]
        allowed = universe.get(corpus_id, set())
        top_k = top_k_override or int(item.get("top_k") or 5)
        response, wall_ms = client.search_context(
            {
                "corpus_id": corpus_id,
                "tenant_id": item.get("tenant_id", "gaphopper"),
                "question": item["question"],
                "top_k": top_k,
                "max_context_tokens": MAX_CONTEXT_TOKENS,
            }
        )
        hits = []
        for ctx in response.get("contexts", []):
            src = _norm(ctx.get("source_path", "") or "")
            # Unknown path => keep it verbatim; it cannot be in `allowed`,
            # so it surfaces as a leak (fail-closed).
            hits.append(
                {
                    "file_id": src if src else "unresolved://empty",
                    "chunk_id": ctx.get("chunk_id", ""),
                    "doc_id": ctx.get("doc_id", ""),
                    "score": float(ctx.get("score") or 0.0),
                    "source_path": ctx.get("source_path", ""),
                    "snippet": (ctx.get("text") or "")[:240],
                }
            )
        case = {
            "id": item["id"],
            "query": item["question"],
            "allowed_file_ids": sorted(allowed),
            "forbidden_file_ids": [],
            "relevant_file_ids": [_norm(p) for p in item.get("expected_source_paths", [])],
        }
        results.append(score_case(case, {"hits": hits}, wall_ms, use_chunks=False))

    return RunReport(
        engine_name=engine_name,
        engine_version=engine_version,
        split=split,
        use_chunks=False,
        cases=results,
        agg=aggregate(results),
    )


def print_lang_breakdown(report: RunReport) -> None:
    """Per-language rollup; case ids look like silver_<lang>_000123."""
    by_lang: dict[str, list[CaseResult]] = defaultdict(list)
    for c in report.cases:
        parts = c.case_id.split("_")
        lang = parts[1] if len(parts) > 2 and len(parts[1]) == 2 else "??"
        by_lang[lang].append(c)
    for lang in sorted(by_lang):
        a = aggregate(by_lang[lang])
        print(f"    [{lang}] n={a.n:<3} recall={a.recall_at_k:.3f} "
              f"MRR={a.mrr:.3f} citation={a.citation_rate:.3f} "
              f"leaks={a.acl_leak_total} p95={a.latency_p95_ms:.0f}ms")
