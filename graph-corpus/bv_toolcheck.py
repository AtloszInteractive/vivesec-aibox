"""Tool ceiling of the BV eval set on a perfect-extraction graph (E26b, dev only).

Loads the synthetic corpus as flawless extraction would (oracle.py, with the
person attributes the documents state) into the graph service's memory store,
runs each question's reference plan through the adapter's own orchestrator
(adapter/orchestrator.py Run) and scores the gold items against what the
answer model is given: the graph facts and notes. A gold item missing there
cannot be in a grounded answer whatever the planner or the model does, so the
figure separates tool and data gaps from planning and wording.

    python graph-corpus/bv_toolcheck.py [--max-facts 40] [--no-name-join] [--json report.json]

No model, no network. Document-side calls (read_all, analyze_document) are
reported as not checked: they read the documents, not the graph.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
# graph_service first: the adapter has a service.py of its own.
sys.path.insert(1, os.path.join(ROOT, "graph_service"))
sys.path.append(os.path.join(ROOT, "demo-corpus"))
sys.path.append(os.path.join(ROOT, "adapter"))

import bv_eval  # noqa: E402
import domain_profile  # noqa: E402
import generate  # noqa: E402
import oracle  # noqa: E402
import orchestrator  # noqa: E402
import service  # noqa: E402
import world as world_mod  # noqa: E402
from graph_store import MemoryGraphStore  # noqa: E402
from render import Renderer  # noqa: E402

ACCESS = {"principals": ["user:bv-eval"], "clearance": 0, "membership_known": True,
          "default_classification": 0}


class Client:
    """The adapter's graph client, answered by the graph service in-process."""

    def __init__(self, state):
        self.state = state

    def post(self, path, body, timeout=None):
        return service.handle_post(self.state, path, copy.deepcopy(body))


def load_graph(persons: int = 120, seed: int = 26, tenant: str = "default"):
    """The perfect-extraction graph of the corpus, and the corpus ids it spans."""
    w = world_mod.build(persons, seed)
    docs = Renderer(w, seed).render_all()
    key = generate.build_answer_key(w, docs, seed)
    meta = {}
    for d in docs:
        source_path = "%s/%s/%s" % (generate.DRIVE_PREFIX, d.drive, d.rel)
        corpus_id = generate.corpus_id_of_drive_root("%s/%s" % (generate.DRIVE_PREFIX, d.drive))
        meta[d.key] = {"doc_id": generate.make_doc_id(tenant, corpus_id, source_path),
                       "corpus_id": corpus_id, "source_path": source_path, "kind": d.kind}
    prof = domain_profile.load("bv")
    state = service.State(MemoryGraphStore(), prof)
    for body in oracle.payloads(key, meta, attributes=True):
        service.handle_post(state, "/graph/document/put", body)
    return state, sorted({m["corpus_id"] for m in meta.values()})


def run_plan(client, scope, prof, question, max_facts, name_join):
    """The facts and notes the reference plan gives the answer model."""
    run = orchestrator.Run(client, scope, lambda path: True, "Hungarian", max_facts, prof,
                           name_join=name_join)
    plan = question.get("reference_plan") or []
    skipped = [c["tool"] for c in plan if c["tool"] not in orchestrator.GRAPH_TOOLS]
    clarify = None
    for round_no in sorted({c.get("round", 1) for c in plan}):
        calls = [{"tool": c["tool"], "args": c["args"]} for c in plan
                 if c.get("round", 1) == round_no and c["tool"] in orchestrator.GRAPH_TOOLS]
        try:
            run.run(calls, round_no)
        except orchestrator.Clarify as e:
            clarify = e.question
            break
    texts = [c["text"] for c in run.output()]
    if run.caveat:
        texts.append(run.caveat)
    return {"text": "\n".join(texts), "facts": len(run.contexts), "clarify": clarify,
            "calls": [{"tool": c["tool"], "facts": c["facts"]} for c in run.calls],
            "not_checked": skipped}


def check(data, state, corpora, max_facts=40, name_join=True):
    client = Client(state)
    prof = client.post("/profile/get", {})["profile"]
    scope = {"corpus_ids": corpora, "access": ACCESS}
    rows = []
    for q in data["questions"]:
        if not q.get("reference_plan"):
            rows.append({"id": q["id"], "bv_step": q["bv_step"], "capability": True})
            continue
        row = {"id": q["id"], "bv_step": q["bv_step"]}
        for label, cap in (("capped", max_facts), ("uncapped", 10000)):
            got = run_plan(client, scope, prof, q, cap, name_join)
            s = bv_eval.score(q, got["text"], data["names"], data["phones"])
            row[label] = {"hits": s["hits"], "must": s["must"], "recall": s["recall"],
                          "missed": s["missed"], "extra": len(s["extra"]),
                          "facts": got["facts"], "clarify": got["clarify"],
                          "calls": got["calls"], "not_checked": got["not_checked"]}
            if "no_data_ok" in s:
                row[label]["no_data_ok"] = not s["extra"] and not got["facts"]
        rows.append(row)
    return rows


def _missed_kinds(missed):
    kinds = {}
    for it in missed:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    return ", ".join("%s %d" % kv for kv in sorted(kinds.items())) or "-"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--persons", type=int, default=120)
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--max-facts", type=int, default=40)
    ap.add_argument("--no-name-join", action="store_true",
                    help="ADAPTER_GRAPH_NAME_JOIN=off (the dev box runs same_name)")
    ap.add_argument("--json", help="write the full report here")
    args = ap.parse_args()
    data = bv_eval.build_eval(args.persons, args.seed)
    state, corpora = load_graph(args.persons, args.seed)
    rows = check(data, state, corpora, args.max_facts, not args.no_name_join)
    print("%-32s %-6s %-9s %-9s %-6s %s" % ("question", "step", "cap %d" % args.max_facts,
                                             "uncapped", "facts", "missed (uncapped)"))
    for r in rows:
        if r.get("capability"):
            print("%-32s %-6s %s" % (r["id"], r["bv_step"], "capability (not scored)"))
            continue
        c, u = r["capped"], r["uncapped"]

        def fig(x):
            if "no_data_ok" in x:
                return "ok" if x["no_data_ok"] else "FAIL"
            return "%d/%d" % (x["hits"], x["must"])
        extra = ""
        if u["clarify"]:
            extra = " clarify"
        if u["not_checked"]:
            extra += " not checked: %s" % ", ".join(u["not_checked"])
        print("%-32s %-6s %-9s %-9s %-6s %s%s" % (r["id"], r["bv_step"], fig(c), fig(u),
                                                 "%d/%d" % (c["facts"], u["facts"]),
                                                 _missed_kinds(u["missed"]), extra))
    if args.json:
        with io.open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
