"""Capability check of the investigation subgraph (BV 1.3 / 2.3) on a live adapter.

P-1.3-vizualis and I-2.3-vizualis ask for the links found from a person or a
phone number on a relationship diagram. The answer text cannot score that, so
this check, like bv_export_check.py for the spreadsheet:

  1. asks the capability question (fresh user, every corpus drive, purpose) and
     records whether the planner built an investigation table;
  2. fetches the subgraph through /ui/graph/investigation/graph -- of the
     answer's table, or, when the planner made none, directly from the seed;
  3. checks its shape (the /query/neighborhood shape the Relationships view
     draws: every edge between two nodes, every piece of evidence in the
     documents, every row a node, one starting point) and that every document
     is within the scope; counts nodes by type and edges by relation;
  4. scores the node labels against the gold items of the scored questions on
     the same seed (bv_eval.score), i.e. how much of what BV asks for is drawn;
  5. fetches it again narrowed to one drive and checks that every document
     stays in it.

    python3 bv_graph_check.py --adapter http://127.0.0.1:8088 --case-ref BV-EVAL/1 \
        [--legal-basis ...] [--out bv_graph_check.jsonl]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bv_eval  # noqa: E402
from bv_eval_run import DRIVES, answer_text  # noqa: E402
from bv_export_check import post  # noqa: E402

CHECKS = {"P-1.3-vizualis": 2, "I-2.3-vizualis": 2}


def subgraph(args, user, drives, start, levels, audit_id=None, narrow=None):
    body = {"start": start, "levels": levels, "audit_id": audit_id}
    if narrow:
        body["drives"] = narrow
    for key in ("case_ref", "legal_basis"):
        if getattr(args, key):
            body[key] = getattr(args, key)
    return post(args.adapter, "/api/v1/ui/graph/investigation/graph", user, drives, body,
                args.timeout)


def check_graph(out, scope):
    if "nodes" not in out:
        return {"ok": False, "status": out.get("status"), "error": out.get("error")}
    problems = []
    nodes = {n["id"]: n for n in out.get("nodes") or []}
    edges = out.get("edges") or []
    documents = out.get("documents") or {}
    rows = out.get("rows") or []
    for e in edges:
        if e.get("a") not in nodes or e.get("b") not in nodes:
            problems.append("edge %s ends outside the nodes" % e.get("id"))
        if not e.get("evidence"):
            problems.append("edge %s without evidence" % e.get("id"))
        for ev in e.get("evidence") or []:
            if ev.get("doc_id") not in documents:
                problems.append("edge %s cites an unlisted document" % e.get("id"))
    for n in nodes.values():
        for a in n.get("attributes") or []:
            if a.get("doc_id") not in documents:
                problems.append("node %s attribute from an unlisted document" % n["id"])
    missing = [r["id"] for r in rows if r["id"] not in nodes]
    if missing:
        problems.append("rows without a node: %s" % missing[:3])
    if sum(1 for r in rows if r.get("level") == 0) < 1:
        problems.append("no starting point")
    sources = sorted({d.get("source_path") or "" for d in documents.values()})
    outside = [s for s in sources if not any(s.startswith(d) for d in scope)]
    if outside:
        problems.append("documents outside the scope: %s" % outside[:3])
    ids = set(nodes)
    held = sum(1 for e in edges if {nodes.get(e["a"], {}).get("type"),
                                    nodes.get(e["b"], {}).get("type")} & {"identifier", "place"})
    text = "\n".join([n.get("label") or "" for n in nodes.values()] +
                     [r.get("path") or "" for r in rows])
    return {"ok": not problems, "problems": problems[:10], "nodes": len(nodes),
            "edges": len(edges), "rows": len(rows), "documents": len(documents),
            "levels": dict(sorted(Counter(r.get("level") for r in rows).items())),
            "types": dict(Counter(n.get("type") for n in nodes.values())),
            "relations": dict(Counter(e.get("type") for e in edges)),
            "held_edges": held, "truncated": out.get("truncated"), "ids": ids, "text": text}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--persons", type=int, default=120)
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--drives", default=",".join(DRIVES))
    ap.add_argument("--narrow", default=DRIVES[0], help="the one drive of the narrowed view")
    ap.add_argument("--user-prefix", default="bv-graph")
    ap.add_argument("--case-ref", dest="case_ref", default=os.environ.get("BV_EVAL_CASE_REF", ""))
    ap.add_argument("--legal-basis", dest="legal_basis",
                    default=os.environ.get("BV_EVAL_LEGAL_BASIS", ""))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="bv_graph_check.jsonl")
    args = ap.parse_args()

    data = bv_eval.build_eval(args.persons, args.seed)
    drives = [d.strip() for d in args.drives.split(",") if d.strip()]
    stamp = time.strftime("%Y%m%d%H%M%S")
    ok = True
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for q in data["questions"]:
            if q["id"] not in CHECKS:
                continue
            user = "%s-%s-%s" % (args.user_prefix, stamp, q["id"].lower())
            body = {"query": q["question"]}
            for key in ("case_ref", "legal_basis"):
                if getattr(args, key):
                    body[key] = getattr(args, key)
            started = time.time()
            res = post(args.adapter, "/api/v1/ui/query", user, drives, body, args.timeout)
            seconds = round(time.time() - started, 1)
            graph = res.get("graph") or {}
            invs = graph.get("investigations") or []
            related = [r for r in data["questions"]
                       if r.get("must") and r.get("seed") and r["agent"] == q["agent"]
                       and r["id"] in bv_eval.INVESTIGATE
                       and any(str(v) in q["question"] for v in r["seed"].values() if v)]
            if invs:
                start, levels, how = invs[0]["start"], invs[0]["levels"], "answer"
            else:
                seed = related[0]["seed"] if related else {}
                start = seed.get("name") or seed.get("value")
                levels, how = CHECKS[q["id"]], "direct"
            started = time.time()
            full = check_graph(subgraph(args, user, drives, start, levels, res.get("audit_id")),
                               drives)
            graph_seconds = round(time.time() - started, 1)
            narrow = check_graph(subgraph(args, user, drives, start, levels,
                                          narrow=[args.narrow.rstrip("/")]), [args.narrow])
            if full.get("ids") is not None and narrow.get("ids") is not None:
                # Narrowing only removes; namesake joining may pick another record id.
                narrow["within_full"] = narrow["ids"] <= full["ids"]
            scores = [bv_eval.score(r, full.get("text") or "", data["names"], data["phones"])
                      for r in related]
            hits, must = sum(s["hits"] for s in scores), sum(s["must"] for s in scores)
            row = {"id": q["id"], "question": q["question"], "seconds": seconds,
                   "graph_seconds": graph_seconds, "answer_ok": res.get("ok"),
                   "calls": [c.get("tool") for c in graph.get("calls") or []],
                   "investigation": how, "start": start, "levels": levels,
                   "answer_rows": len(invs[0].get("rows") or []) if invs else None,
                   "graph": {k: v for k, v in full.items() if k not in ("text", "ids")},
                   "narrow": {k: v for k, v in narrow.items() if k not in ("text", "ids")},
                   "gold": {s["id"]: "%d/%d" % (s["hits"], s["must"]) for s in scores},
                   "missed": {s["id"]: s["missed"] for s in scores if s["missed"]},
                   "answer": answer_text(res)[:600]}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            good = full.get("ok") and (narrow.get("ok") or narrow.get("status") == 404)
            ok = ok and bool(good)
            print("%-15s %-6s start=%s L%d nodes=%s edges=%s rows=%s (answer %s; narrowed "
                  "%s/%s) %s %.1fs; gold %d/%d %s"
                  % (q["id"], how, start, levels, full.get("nodes"), full.get("edges"),
                     full.get("rows"), row["answer_rows"],
                     narrow.get("nodes", narrow.get("status")), narrow.get("edges"),
                     "ok" if full.get("ok") else full.get("problems") or full.get("error"),
                     graph_seconds, hits, must, row["gold"]), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
