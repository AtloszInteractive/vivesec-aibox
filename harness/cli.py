"""Parity / eval harness CLI — the dev/CI acceptance gate for the RAG engine.

NOT shipped to the appliance. This is our gate: run a gold-set against an engine,
or compare a CANDIDATE engine against the BASELINE and decide if it may swap in.

Speaks the v1 retrieval contract (`/health`, `/ingest`, `/rag/search_context`,
`/index/drop/tree`) against `rag_service`; isolation is per corpus_id.

Examples:
    # evaluate one engine (our rag_service) on the dev split
    python cli.py run --engine-url http://127.0.0.1:8090 --split dev

    # parity gate: candidate (CoLearn) vs baseline (ours), on holdout
    python cli.py compare \
        --baseline-url http://127.0.0.1:8090 \
        --candidate-url http://127.0.0.1:8091 \
        --split holdout --latency-budget-ms 1500

Exit code is non-zero when a run has ACL leaks or the gate blocks the candidate,
so CI can fail the build.
"""
from __future__ import annotations

import argparse
import os
import sys

from client import EngineClient
from compare import GateConfig, evaluate_gate, print_parity
from corpus import FIXTURES_DIR, HERE
from runner import print_run, run_engine

DEFAULT_TOKEN = os.environ.get("RAG_API_KEY", os.environ.get("RAG_INTERNAL_TOKEN", ""))
# Profile -> (fixtures dir, gold split suffix). SMOKE is the tiny hand-written
# regression set; FULL is the generated, statistically meaningful gate set
# (see gen_corpus.py).
PROFILES = {
    "smoke": (FIXTURES_DIR, ""),
    "full": (os.path.join(HERE, "fixtures_full"), "_full"),
}


def _client(url: str, token: str) -> EngineClient:
    return EngineClient(base_url=url, token=token)


def cmd_run(args: argparse.Namespace) -> int:
    fixtures_dir, gold_suffix = PROFILES[args.profile]
    client = _client(args.engine_url, args.token)
    report = run_engine(
        client,
        split=args.split,
        top_k=args.top_k or 5,
        use_chunks=args.use_chunks,
        do_ingest=not args.no_ingest,
        fixtures_dir=fixtures_dir,
        gold_suffix=gold_suffix,
    )
    print_run(report)
    # CI safety: any ACL leak fails the run.
    return 1 if report.agg.acl_leak_total > 0 else 0


def cmd_compare(args: argparse.Namespace) -> int:
    fixtures_dir, gold_suffix = PROFILES[args.profile]
    base = run_engine(
        _client(args.baseline_url, args.token),
        split=args.split,
        top_k=args.top_k or 5,
        use_chunks=args.use_chunks,
        do_ingest=not args.no_ingest,
        fixtures_dir=fixtures_dir,
        gold_suffix=gold_suffix,
    )
    cand = run_engine(
        _client(args.candidate_url, args.token),
        split=args.split,
        top_k=args.top_k or 5,
        use_chunks=args.use_chunks,
        do_ingest=not args.no_ingest,
        fixtures_dir=fixtures_dir,
        gold_suffix=gold_suffix,
    )
    print_run(base)
    print_run(cand)
    gate = evaluate_gate(
        base,
        cand,
        GateConfig(
            recall_eps=args.recall_eps,
            citation_eps=args.citation_eps,
            latency_budget_ms=args.latency_budget_ms,
        ),
    )
    print_parity(base, cand, gate)
    return 0 if gate.passed else 1


def cmd_system(args: argparse.Namespace) -> int:
    """LEVEL-2 system eval through the adapter (`/api/v1/ui/*`) — the REAL
    production path incl. sync-push ingest + grounded generation."""
    from adapter_client import AdapterClient
    from system_eval import (aggregate_system, cleanup_drives, print_system,
                             run_system, sync_fixtures)
    fixtures_dir, gold_suffix = PROFILES[args.profile]
    client = AdapterClient(base_url=args.adapter_url, user=args.user)
    if not args.no_ingest:
        stats = sync_fixtures(client, fixtures_dir, args.drive_prefix)
        print(f"synced: {stats}")
    results = run_system(client, fixtures_dir, gold_suffix, args.split,
                         args.drive_prefix, top_k=args.top_k, limit=args.limit)
    print_system(results, args.split)
    if args.cleanup:
        cleanup_drives(client, fixtures_dir, args.drive_prefix)
        print("cleanup: eval drives dropped")
    agg = aggregate_system(results)
    # CI safety: any answer-level leak fails; transport errors fail too.
    return 1 if (agg.leak_total > 0 or agg.errors > 0) else 0


def _silver_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--silver", required=True, help="silver_all.jsonl")
    sp.add_argument("--manifest", required=True, help="corpus_manifest.jsonl")
    sp.add_argument("--langs", default="",
                    help="comma list (en,hu,da,de); empty = all")
    sp.add_argument("--limit", type=int, default=0)


def cmd_silver(args: argparse.Namespace) -> int:
    """Evaluate ONE engine on the govdocs silver set (ingest via
    ingest_govdocs.py first; this command never ingests)."""
    from silver_eval import print_lang_breakdown, run_silver

    langs = [x for x in args.langs.split(",") if x] or None
    report = run_silver(_client(args.engine_url, args.token), args.silver,
                        args.manifest, split=args.split, langs=langs,
                        limit=args.limit, top_k_override=args.top_k or 0)
    print_run(report)
    print_lang_breakdown(report)
    return 1 if report.agg.acl_leak_total > 0 else 0


def cmd_silver_compare(args: argparse.Namespace) -> int:
    """Parity gate on the silver set: candidate vs baseline (both engines
    must already hold the SAME govdocs corpus)."""
    from silver_eval import print_lang_breakdown, run_silver

    langs = [x for x in args.langs.split(",") if x] or None
    base = run_silver(_client(args.baseline_url, args.token), args.silver,
                      args.manifest, split=args.split, langs=langs,
                      limit=args.limit, top_k_override=args.top_k or 0)
    cand = run_silver(_client(args.candidate_url, args.token), args.silver,
                      args.manifest, split=args.split, langs=langs,
                      limit=args.limit, top_k_override=args.top_k or 0)
    print_run(base)
    print_lang_breakdown(base)
    print_run(cand)
    print_lang_breakdown(cand)
    gate = evaluate_gate(
        base,
        cand,
        GateConfig(
            recall_eps=args.recall_eps,
            citation_eps=args.citation_eps,
            latency_budget_ms=args.latency_budget_ms,
        ),
    )
    print_parity(base, cand, gate)
    return 0 if gate.passed else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ViVeSec RAG parity / eval harness")
    p.add_argument("--token", default=DEFAULT_TOKEN,
                   help="engine X-API-Key (env RAG_API_KEY); empty if service runs open")
    p.add_argument("--split", default="dev", choices=["dev", "holdout"])
    p.add_argument("--profile", default="smoke", choices=sorted(PROFILES),
                   help="smoke = tiny hand-written set; full = generated gate set "
                        "(fixtures_full + *_full.jsonl, see gen_corpus.py)")
    p.add_argument("--top-k", type=int, default=None,
                   help="hits per query (run/compare default 5; silver default: "
                        "per-item top_k)")
    p.add_argument("--use-chunks", action="store_true",
                   help="score recall at chunk granularity (default: file)")
    p.add_argument("--no-ingest", action="store_true",
                   help="skip ingesting fixtures (engine already populated)")

    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="evaluate one engine")
    pr.add_argument("--engine-url", required=True)
    pr.set_defaults(func=cmd_run)

    pc = sub.add_parser("compare", help="parity gate: candidate vs baseline")
    pc.add_argument("--baseline-url", required=True)
    pc.add_argument("--candidate-url", required=True)
    pc.add_argument("--recall-eps", type=float, default=0.0)
    pc.add_argument("--citation-eps", type=float, default=0.0)
    pc.add_argument("--latency-budget-ms", type=float, default=1500.0)
    pc.set_defaults(func=cmd_compare)

    ps = sub.add_parser("system", help="LEVEL-2 system eval through the adapter "
                                       "(/api/v1/ui/* production path)")
    ps.add_argument("--adapter-url", required=True,
                    help="e.g. http://192.168.0.181:8088")
    ps.add_argument("--user", default="harness", help="VVS-User header value")
    ps.add_argument("--drive-prefix", default="eval-",
                    help="box drive name prefix (isolates eval corpora)")
    ps.add_argument("--limit", type=int, default=None,
                    help="only run the first N gold cases (quick validation)")
    ps.add_argument("--cleanup", action="store_true",
                    help="drop the eval drives from the box after the run")
    ps.set_defaults(func=cmd_system)

    psv = sub.add_parser("silver", help="evaluate one engine on the govdocs "
                                        "silver set (no ingest — run "
                                        "ingest_govdocs.py first)")
    psv.add_argument("--engine-url", required=True)
    _silver_args(psv)
    psv.set_defaults(func=cmd_silver)

    psc = sub.add_parser("silver-compare", help="parity gate on the govdocs "
                                                "silver set")
    psc.add_argument("--baseline-url", required=True)
    psc.add_argument("--candidate-url", required=True)
    psc.add_argument("--recall-eps", type=float, default=0.0)
    psc.add_argument("--citation-eps", type=float, default=0.0)
    psc.add_argument("--latency-budget-ms", type=float, default=1500.0)
    _silver_args(psc)
    psc.set_defaults(func=cmd_silver_compare)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
