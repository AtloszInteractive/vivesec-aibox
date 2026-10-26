"""Extraction throughput on the target hardware: documents per hour by kind (E26).

    python graph_service/extract_bench.py --source /src --per-kind 5 --out run.jsonl \
        [--profile /data/graph/profile.json] [--llm-url http://127.0.0.1:8099] \
        [--model qwen3.6:35b] [--num-ctx 65536] [--project 100000]
    python graph_service/extract_bench.py --report run.jsonl [other.jsonl ...]

--source is a directory (the document kind is the folder path down to
--kind-depth plus the file extension) or a graph-corpus folder with a
manifest.jsonl (kind from the manifest). Each sampled document runs through
pipeline.extract_document exactly as the ingest worker does, with the same
table mappings, but nothing is written to the graph store. Every model call is
logged with the token counts and timings the model server reports, so the
report can say where the time goes: prompt reading, generation, model loading,
or waiting (GPU broker, network). The run resumes from --out.

The projection weights each kind's mean time by its share of the whole source
(not of the sample), so it estimates one queue's time for a corpus like this one.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

STAT_KEYS = ("seconds", "llm_seconds", "llm_calls", "llm_prompt_tokens", "llm_output_tokens",
             "llm_prompt_seconds", "llm_eval_seconds", "llm_load_seconds", "llm_truncated",
             "llm_errors", "chunks", "tables", "table_rows", "pages", "entities", "relations")


# -- corpus ------------------------------------------------------------------------

def collect(source: str, kind_depth: int) -> list[dict]:
    """[{path (relative, /-separated), local, kind}] of every file in source."""
    manifest = os.path.join(source, "manifest.jsonl")
    if os.path.exists(manifest):
        with io.open(manifest, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return [{"path": r.get("source_path") or r["local_path"],
                 "local": os.path.join(source, *r["local_path"].split("/")),
                 "kind": r.get("kind") or "?", "title": r.get("title"), "key": r.get("key"),
                 "doc_id": r.get("doc_id"), "corpus_id": r.get("corpus_id")} for r in rows]
    out = []
    for root, dirs, files in os.walk(source):
        dirs.sort()
        for name in sorted(files):
            local = os.path.join(root, name)
            rel = os.path.relpath(local, source).replace(os.sep, "/")
            folder = rel.split("/")[:-1][:kind_depth]
            ext = os.path.splitext(name)[1].lower().lstrip(".") or "-"
            out.append({"path": rel, "local": local,
                        "kind": "%s/*.%s" % ("/".join(folder) or ".", ext)})
    return out


def sample(rows: list[dict], per_kind: int, seed: int) -> list[dict]:
    """per_kind documents of every kind (all when 0), interleaved by kind."""
    by_kind: dict[str, list[dict]] = {}
    for r in rows:
        by_kind.setdefault(r["kind"], []).append(r)
    rng = random.Random(seed)
    picked = {k: (rng.sample(v, per_kind) if per_kind and per_kind < len(v) else list(v))
              for k, v in sorted(by_kind.items())}
    out = []
    while any(picked.values()):
        for k in sorted(picked):
            if picked[k]:
                out.append(picked[k].pop(0))
    return out


# -- run -----------------------------------------------------------------------------

def run(args) -> int:
    import queue
    import threading

    import domain_profile
    import extraction
    import pipeline
    from llm_client import LLMClient
    from pipeline import extract_document
    from resolution import Resolver
    from table_mapping import TableMapper

    profile = domain_profile.load(args.profile)
    rows = collect(args.source, args.kind_depth)
    mix = {}
    for r in rows:
        mix[r["kind"]] = mix.get(r["kind"], 0) + 1
    kinds = set(filter(None, args.kinds.split(",")))
    todo = sample([r for r in rows if not kinds or r["kind"] in kinds], args.per_kind, args.seed)
    probe = LLMClient(url=args.llm_url, model=args.model, num_ctx=args.num_ctx)
    config = {"model": probe.model, "num_ctx": probe.num_ctx, "think": probe.think,
              "llm_url": probe.url,
              "api": probe.api, "profile": domain_profile.profile_tag(profile),
              "tables": args.tables, "compact": extraction.COMPACT, "forms": pipeline.FORMS,
              "parallel": args.parallel, "label": args.label}

    done = set()
    if os.path.exists(args.out):
        with io.open(args.out, encoding="utf-8") as f:
            done = {json.loads(line)["path"] for line in f if line.strip()}
    jobs: queue.Queue = queue.Queue()
    for i, row in enumerate(todo, start=1):
        if row["path"] not in done:
            jobs.put((i, row))
    write_lock = threading.Lock()
    started = time.time()

    def worker(out) -> None:
        # Each worker has its own client, table mapper and resolver: nothing shared
        # but the output file. With --parallel 1 this is the ingest worker's setup.
        llm = LLMClient(url=args.llm_url, model=args.model, num_ctx=args.num_ctx)
        tables = TableMapper(profile, llm, profile.get("table_mappings") or {}) \
            if args.tables == "on" else None
        resolver = Resolver(profile)
        while True:
            try:
                i, row = jobs.get_nowait()
            except queue.Empty:
                return
            with open(row["local"], "rb") as f:
                raw = f.read()
            meta = {"doc_id": row.get("doc_id") or "bench:" + hashlib.sha1(
                        row["path"].encode("utf-8")).hexdigest()[:16],
                    "corpus_id": row.get("corpus_id") or "bench", "source_path": row["path"],
                    "title": row.get("title") or row["path"].rsplit("/", 1)[-1],
                    "kind": row["kind"]}
            llm.call_log = []
            t0 = time.time()
            payload = None
            try:
                payload, stats = extract_document(raw, meta, profile, llm, resolver, tables)
                error = None
            except Exception as e:  # noqa: BLE001 - one bad file must not end the run
                stats, error = {}, "%s: %s" % (type(e).__name__, str(e)[:200])
            t1 = time.time()
            # key + stats + payload is also what graph-corpus/evaluate.py scores.
            record = {"path": row["path"], "key": row.get("key"), "kind": row["kind"],
                      "bytes": len(raw), "wall": round(t1 - t0, 2), "error": error,
                      "started_at": round(t0, 2), "finished_at": round(t1, 2),
                      "config": config, "mix": mix,
                      "stats": {k: v for k, v in stats.items() if k != "llm_error_detail"},
                      "calls": llm.call_log, "payload": payload}
            with write_lock:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()
                print("[%d/%d] %-40s %6.1fs llm=%d out_tok=%d %s" % (
                    i, len(todo), row["kind"][:40], record["wall"], stats.get("llm_calls", 0),
                    stats.get("llm_output_tokens", 0), error or ""), flush=True)

    with io.open(args.out, "a", encoding="utf-8", newline="\n") as out:
        threads = [threading.Thread(target=worker, args=(out,), daemon=True)
                   for _ in range(max(1, args.parallel))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    print("run finished in %.0fs" % (time.time() - started))
    print(report([args.out], args.project))
    return 0


# -- report --------------------------------------------------------------------------

def _load(path: str) -> list[dict]:
    with io.open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def summarize(records: list[dict]) -> dict:
    """Per-kind and overall figures of one run."""
    kinds: dict[str, list[dict]] = {}
    for r in records:
        kinds.setdefault(r["kind"], []).append(r)
    mix = {}
    for r in records:
        mix.update(r.get("mix") or {})
    out = {"config": records[0].get("config") if records else {}, "kinds": {}, "calls": {}}
    for kind, items in sorted(kinds.items()):
        walls = [r["wall"] for r in items]
        tot = {k: sum((r["stats"].get(k) or 0) for r in items) for k in STAT_KEYS}
        n = len(items)
        out["kinds"][kind] = {
            "n": n, "in_source": mix.get(kind, n), "errors": sum(1 for r in items if r["error"]),
            "mean_s": statistics.mean(walls), "p50_s": _pct(walls, 0.5),
            "p90_s": _pct(walls, 0.9), "max_s": max(walls),
            "docs_per_hour": 3600 * n / sum(walls) if sum(walls) else 0.0,
            "llm_share": tot["llm_seconds"] / sum(walls) if sum(walls) else 0.0,
            **{"per_doc_" + k: tot[k] / n for k in STAT_KEYS if k != "seconds"},
        }
    calls = [c for r in records for c in r.get("calls") or []]
    secs = sum(c["seconds"] for c in calls)
    prompt_s = sum(c.get("prompt_seconds", 0) for c in calls)
    eval_s = sum(c.get("eval_seconds", 0) for c in calls)
    load_s = sum(c.get("load_seconds", 0) for c in calls)
    # Extraction calls ask for up to 8192 tokens, table mapping calls for the default.
    out["calls"] = {
        "n": len(calls), "seconds": secs,
        "extraction": sum(1 for c in calls if c.get("max_tokens") == 8192),
        "table_mapping": sum(1 for c in calls if c.get("max_tokens") != 8192),
        "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
        "output_tokens": sum(c.get("output_tokens", 0) for c in calls),
        "prompt_seconds": prompt_s, "eval_seconds": eval_s, "load_seconds": load_s,
        "other_seconds": max(0.0, secs - prompt_s - eval_s - load_s),
        "truncated": sum(1 for c in calls if c.get("truncated")),
        "failed": sum(1 for c in calls if not c.get("ok", True)),
    }
    c = out["calls"]
    c["prompt_tok_per_s"] = c["prompt_tokens"] / prompt_s if prompt_s else 0.0
    c["output_tok_per_s"] = c["output_tokens"] / eval_s if eval_s else 0.0
    weight = sum(k["in_source"] for k in out["kinds"].values())
    mean = sum(k["in_source"] * k["mean_s"] for k in out["kinds"].values()) / weight \
        if weight else 0.0
    # Parallel runs overlap documents: the time a document took is not the time
    # it cost. Overlap = summed document time / the run's span (1 when serial).
    timed = [r for r in records
             if r.get("started_at") is not None and r.get("finished_at") is not None]
    span = (max(r["finished_at"] for r in timed) - min(r["started_at"] for r in timed)) \
        if timed else 0.0
    overlap = max(1.0, sum(r["wall"] for r in timed) / span) if span > 0 else 1.0
    out["overall"] = {"documents": len(records), "source_documents": weight,
                      "weighted_mean_s": mean, "overlap": overlap,
                      "effective_s": mean / overlap,
                      "docs_per_hour": 3600 * overlap / mean if mean else 0.0}
    return out


def report(paths: list[str], project: int = 100000) -> str:
    lines = []
    runs = [(p, summarize(_load(p))) for p in paths]
    for path, s in runs:
        cfg = s["config"] or {}
        lines.append("## %s" % (cfg.get("label") or os.path.basename(path)))
        lines.append("model %s, num_ctx %s, profile %s, tables %s, via %s" % (
            cfg.get("model"), cfg.get("num_ctx"), cfg.get("profile"), cfg.get("tables"),
            cfg.get("llm_url")))
        lines.append("")
        lines.append("| kind | n / source | mean s | p50 | p90 | max | docs/h | LLM % | calls "
                     "| chunks | prompt tok | output tok | trunc | err |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for kind, k in s["kinds"].items():
            lines.append("| %s | %d / %d | %.1f | %.1f | %.1f | %.1f | %.0f | %.0f | %.1f | %.1f "
                         "| %.0f | %.0f | %d | %d |" % (
                             kind, k["n"], k["in_source"], k["mean_s"], k["p50_s"], k["p90_s"],
                             k["max_s"], k["docs_per_hour"], 100 * k["llm_share"],
                             k["per_doc_llm_calls"], k["per_doc_chunks"],
                             k["per_doc_llm_prompt_tokens"], k["per_doc_llm_output_tokens"],
                             round(k["per_doc_llm_truncated"] * k["n"]),
                             k["errors"] + round(k["per_doc_llm_errors"] * k["n"])))
        c, o = s["calls"], s["overall"]
        secs = c["seconds"] or 1.0
        lines.append("")
        lines.append("Model calls: %d (extraction %d, table mapping %d), %.0f s; prompt %d tok "
                     "(%.0f tok/s), output %d tok (%.1f tok/s); truncated %d, failed %d." % (
                         c["n"], c["extraction"], c["table_mapping"], c["seconds"],
                         c["prompt_tokens"], c["prompt_tok_per_s"], c["output_tokens"],
                         c["output_tok_per_s"], c["truncated"], c["failed"]))
        lines.append("Time inside calls: generation %.0f%%, prompt reading %.0f%%, model loading "
                     "%.0f%%, other (wait, transfer, grammar) %.0f%%." % (
                         100 * c["eval_seconds"] / secs, 100 * c["prompt_seconds"] / secs,
                         100 * c["load_seconds"] / secs, 100 * c["other_seconds"] / secs))
        if o["weighted_mean_s"]:
            lines.append("Weighted by the source mix (%d documents): %.1f s/document, %.0f "
                         "documents/hour; %d documents in one queue: %.1f days." % (
                             o["source_documents"], o["effective_s"], o["docs_per_hour"],
                             project, project * o["effective_s"] / 86400))
            if o["overlap"] > 1.05:
                lines.append("(%d workers overlapped %.2fx; a document took %.1f s on average "
                             "while sharing the model.)" % (cfg.get("parallel") or 1,
                                                           o["overlap"], o["weighted_mean_s"]))
        lines.append("")
    if len(runs) > 1:
        kinds = sorted({k for _, s in runs for k in s["kinds"]})
        names = [(s["config"] or {}).get("label") or os.path.basename(p) for p, s in runs]
        lines.append("## Comparison (mean s/document)")
        lines.append("| kind | " + " | ".join(names) + " |")
        lines.append("|---|" + "---|" * len(names))
        for kind in kinds:
            lines.append("| %s | %s |" % (kind, " | ".join(
                "%.1f" % s["kinds"][kind]["mean_s"] if kind in s["kinds"] else "-"
                for _, s in runs)))
        lines.append("| weighted | %s |" % " | ".join(
            "%.1f" % s["overall"]["effective_s"] for _, s in runs))
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", nargs="+", metavar="JSONL", help="only print the report")
    ap.add_argument("--source")
    ap.add_argument("--out")
    ap.add_argument("--profile", default=os.environ.get("GRAPH_PROFILE_PATH") or "bv")
    ap.add_argument("--per-kind", type=int, default=5, help="documents per kind, 0 = all")
    ap.add_argument("--kind-depth", type=int, default=2)
    ap.add_argument("--kinds", default="", help="comma-separated kinds to run (default all)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--llm-url", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--num-ctx", type=int, default=None)
    ap.add_argument("--tables", choices=("on", "off"), default="on")
    ap.add_argument("--parallel", type=int, default=1,
                    help="documents in flight at once (each its own model request); "
                         "Ollama 0.32 serves qwen35moe one request at a time")
    ap.add_argument("--label", default="")
    ap.add_argument("--project", type=int, default=100000,
                    help="corpus size for the one-queue projection")
    args = ap.parse_args(argv)
    if args.report:
        print(report(args.report, args.project))
        return 0
    if not args.source or not args.out:
        ap.error("--source and --out are required for a run")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
