"""Measure the score distribution of the demo corpus and calibrate RAG_MIN_SCORE.

Stdlib only, Python 3.8+, designed to run ON the Jetson next to the RAG.

For every probe question it calls /rag/search_context (top_k=5) and records the
top-1 score. In-corpus questions must stay above the threshold, out-of-corpus
questions must fall below it. The script then sweeps candidate thresholds and
reports, for each one, how many real questions would be wrongly rejected and how
many out-of-corpus questions would be correctly refused.

The value calibrated on the govdocs eval corpus does NOT transfer to this
corpus — the score distribution is corpus-specific. That is exactly what this
script exists to establish.

Usage:
    RAG_API_KEY=... python3 score_probe.py --rag-url http://127.0.0.1:8093 \
        --probes out/probe_questions.json [--json report.json]
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def search(rag_url, api_key, corpus_id, tenant_id, question, top_k, timeout=120):
    payload = {"corpus_id": corpus_id, "tenant_id": tenant_id,
               "question": question, "top_k": top_k, "max_context_tokens": 4000}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(rag_url + "/rag/search_context",
                                 data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, "HTTP %s %s" % (exc.code, exc.read().decode("utf-8", "replace")[:200])
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--probes", default=os.path.join(here, "out", "probe_questions.json"))
    ap.add_argument("--rag-url", default="http://127.0.0.1:8093")
    ap.add_argument("--api-key", default=os.environ.get("RAG_API_KEY", ""))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--json", default=None, help="write the full report to this file")
    args = ap.parse_args()

    with open(args.probes) as f:
        probe = json.load(f)
    rag_url = args.rag_url.rstrip("/")
    tenant = probe.get("tenant_id", "default")
    corpora = probe["corpora"]

    in_results = []
    for item in probe["in_corpus"]:
        res, err = search(rag_url, args.api_key, item["expect_corpus"], tenant,
                          item["question"], args.top_k)
        if err:
            print("ERROR %s -> %s" % (item["question"][:50], err))
            continue
        contexts = res.get("contexts") or []
        top = contexts[0] if contexts else None
        wanted = item["expect_documents"]
        hit_rank = None
        for rank, ctx in enumerate(contexts, start=1):
            path = ctx.get("source_path") or ""
            if any(path.endswith("/" + name) for name in wanted):
                hit_rank = rank
                break
        in_results.append({
            "question": item["question"],
            "drive": item["drive"],
            "expect_documents": wanted,
            "top_score": (top or {}).get("score"),
            "top_source": (top or {}).get("source_path"),
            "hit_rank": hit_rank,
            "n_contexts": len(contexts),
        })

    out_results = []
    for item in probe["out_of_corpus"]:
        best = None
        for drive, corpus_id in sorted(corpora.items()):
            res, err = search(rag_url, args.api_key, corpus_id, tenant,
                              item["question"], args.top_k)
            if err:
                continue
            contexts = res.get("contexts") or []
            if contexts:
                score = contexts[0].get("score")
                if best is None or (score is not None and score > best["top_score"]):
                    best = {"top_score": score, "drive": drive,
                            "top_source": contexts[0].get("source_path")}
        out_results.append({
            "question": item["question"],
            "reason": item["reason"],
            "source": item["source"],
            "top_score": (best or {}).get("top_score"),
            "top_drive": (best or {}).get("drive"),
            "top_source": (best or {}).get("top_source"),
        })

    in_scores = sorted(r["top_score"] for r in in_results if r["top_score"] is not None)
    out_scores = sorted(r["top_score"] for r in out_results if r["top_score"] is not None)

    print("=" * 78)
    print("IN-CORPUS   %d questions" % len(in_results))
    hits = sum(1 for r in in_results if r["hit_rank"])
    top1 = sum(1 for r in in_results if r["hit_rank"] == 1)
    print("  expected document in top-%d : %d/%d (%.1f%%)"
          % (args.top_k, hits, len(in_results), 100.0 * hits / max(1, len(in_results))))
    print("  expected document at rank 1 : %d/%d (%.1f%%)"
          % (top1, len(in_results), 100.0 * top1 / max(1, len(in_results))))
    if in_scores:
        print("  top-1 score  min=%.4f  p10=%.4f  median=%.4f  max=%.4f"
              % (in_scores[0], in_scores[max(0, len(in_scores) // 10)],
                 in_scores[len(in_scores) // 2], in_scores[-1]))
    print("")
    print("OUT-OF-CORPUS   %d questions" % len(out_results))
    if out_scores:
        print("  top-1 score  min=%.4f  median=%.4f  p90=%.4f  max=%.4f"
              % (out_scores[0], out_scores[len(out_scores) // 2],
                 out_scores[min(len(out_scores) - 1, 9 * len(out_scores) // 10)],
                 out_scores[-1]))
    print("")
    print("  lowest scoring REAL questions (these constrain the threshold):")
    for r in sorted((x for x in in_results if x["top_score"] is not None),
                    key=lambda x: x["top_score"])[:6]:
        print("    %.4f  %-48s -> %s" % (r["top_score"], r["question"][:48],
                                         (r["top_source"] or "")[-48:]))
    print("  highest scoring OUT-OF-CORPUS questions:")
    for r in sorted((x for x in out_results if x["top_score"] is not None),
                    key=lambda x: -x["top_score"])[:6]:
        print("    %.4f  %-48s -> %s" % (r["top_score"], r["question"][:48],
                                         (r["top_source"] or "")[-48:]))

    print("")
    print("THRESHOLD SWEEP")
    print("  %-8s %-22s %-24s %s" % ("thr", "real questions kept", "out-of-corpus refused", "net"))
    best_row = None
    thresholds = [round(0.20 + 0.02 * i, 2) for i in range(26)]
    sweep = []
    for thr in thresholds:
        kept = sum(1 for s in in_scores if s >= thr)
        lost = len(in_scores) - kept
        refused = sum(1 for r in out_results if r["top_score"] is None or r["top_score"] < thr)
        net = refused - lost
        sweep.append({"threshold": thr, "kept": kept, "lost": lost,
                      "refused": refused, "net": net})
        marker = ""
        if best_row is None or net > best_row["net"]:
            best_row = sweep[-1]
        print("  %-8.2f %2d/%-2d (lost %2d)      %2d/%-2d                  %+d%s"
              % (thr, kept, len(in_scores), lost, refused, len(out_results), net, marker))

    # widest gap that keeps every real question
    safe = [row for row in sweep if row["lost"] == 0]
    safe_best = max(safe, key=lambda r: r["refused"]) if safe else None

    print("")
    if safe_best:
        print("RECOMMENDED (no real question is lost): RAG_MIN_SCORE=%.2f"
              % safe_best["threshold"])
        print("  keeps %d/%d real questions, refuses %d/%d out-of-corpus questions"
              % (safe_best["kept"], len(in_scores), safe_best["refused"], len(out_results)))
    else:
        print("NO THRESHOLD keeps every real question — the distributions overlap.")
    if best_row:
        print("BEST NET (may sacrifice a real question): RAG_MIN_SCORE=%.2f  net=%+d"
              % (best_row["threshold"], best_row["net"]))
    if in_scores and out_scores:
        print("SEPARATION: lowest real = %.4f, highest out-of-corpus = %.4f -> %s"
              % (in_scores[0], out_scores[-1],
                 "clean gap of %.4f" % (in_scores[0] - out_scores[-1])
                 if in_scores[0] > out_scores[-1] else "OVERLAP"))

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"in_corpus": in_results, "out_of_corpus": out_results,
                       "sweep": sweep,
                       "recommended": safe_best, "best_net": best_row}, f, indent=2)
        print("\nfull report: %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
