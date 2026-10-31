"""Runs the BV eval set (bv_eval.py) against a live adapter and scores the answers.

Each question is asked as a fresh user (no conversation history), with every
corpus drive in scope and the purpose binding the box may require. The answer
text is scored against the gold items; the report keeps the answer, the
planner's graph calls, the citations and the timing per question.

    # on the dev box, next to world.py, render.py and bv_eval.py:
    python3 bv_eval_run.py --adapter http://127.0.0.1:8088 --case-ref BV-EVAL/1 \
        --legal-basis <an allowed legal basis> --out bv_eval_run.jsonl [--only P-1.1-adatok]

Answers come from the real model on the real (extracted, not perfect) graph,
so a miss here and a hit in bv_toolcheck.py points at extraction, planning or
wording rather than at the tools. Capability items (diagram, Excel) are skipped.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bv_eval  # noqa: E402

DRIVES = ["/storage/drives/%s/" % d for d in ("nyilvantartas", "kommunikacio", "jelentesek",
                                              "egyeb")]


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def ask(adapter, user, drives, question, case_ref, legal_basis, timeout):
    body = {"query": question}
    if case_ref:
        body["case_ref"] = case_ref
    if legal_basis:
        body["legal_basis"] = legal_basis
    req = urllib.request.Request(
        adapter.rstrip("/") + "/api/v1/ui/query", data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "VVS-User": user,
                 "VVS-Drive": b64(drives[0]), "VVS-Other-Drives": b64("\x00".join(drives))})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode("utf-8", "replace")[:500]}


def answer_text(result):
    """The answer without the audit footer (it names no one, but keep it clean)."""
    text = result.get("answer") or ""
    return text.split("\n---\n**Adatkontroll", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--eval", help="bv_eval.json (default: generated with --persons/--seed)")
    ap.add_argument("--persons", type=int, default=120)
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--drives", default=",".join(DRIVES))
    ap.add_argument("--user-prefix", default="bv-eval")
    ap.add_argument("--case-ref", default=os.environ.get("BV_EVAL_CASE_REF", ""))
    ap.add_argument("--legal-basis", default=os.environ.get("BV_EVAL_LEGAL_BASIS", ""))
    ap.add_argument("--only", action="append", default=[], help="question id (repeatable)")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="bv_eval_run.jsonl")
    args = ap.parse_args()

    if args.eval:
        with io.open(args.eval, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = bv_eval.build_eval(args.persons, args.seed)
    drives = [d.strip() for d in args.drives.split(",") if d.strip()]
    stamp = time.strftime("%Y%m%d%H%M%S")
    rows = []
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as out:
        for q in data["questions"]:
            if args.only and q["id"] not in args.only:
                continue
            if not q.get("reference_plan"):
                continue  # a capability item: nothing to score in the text
            user = "%s-%s-%s" % (args.user_prefix, stamp, q["id"].lower())
            started = time.time()
            result = ask(args.adapter, user, drives, q["question"], args.case_ref,
                         args.legal_basis, args.timeout)
            seconds = round(time.time() - started, 1)
            text = answer_text(result)
            s = bv_eval.score(q, text, data["names"], data["phones"])
            graph = result.get("graph") or {}
            row = {"id": q["id"], "bv_step": q["bv_step"], "question": q["question"],
                   "seconds": seconds, "ok": result.get("ok", False),
                   "status": result.get("status"), "error": result.get("error"),
                   "clarify": bool(result.get("clarify")), "score": s,
                   "calls": graph.get("calls") or [], "facts": graph.get("facts"),
                   "stop": graph.get("stop"), "deep": bool(result.get("deep")),
                   "citations": len(result.get("citations") or []), "answer": text}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            rows.append(row)
            fig = ("no-data %s" % ("ok" if s.get("no_data_ok") else "FAIL")) \
                if "no_data_ok" in s else "%d/%d" % (s["hits"], s["must"])
            print("%-32s %-10s %6.1fs calls %-38s extra %d%s" % (
                q["id"], fig, seconds, ",".join(c.get("tool", "?") for c in row["calls"])[:38],
                len(s["extra"]), " CLARIFY" if row["clarify"] else
                (" ERROR %s" % row["status"] if not row["ok"] else "")), flush=True)
    scored = [r["score"] for r in rows if r["score"]["recall"] is not None]
    if scored:
        hits, must = sum(s["hits"] for s in scored), sum(s["must"] for s in scored)
        print("recall %d/%d = %.0f%% over %d questions; negative controls ok: %d/%d" % (
            hits, must, 100.0 * hits / must if must else 0, len(scored),
            sum(1 for r in rows if r["score"].get("no_data_ok")),
            sum(1 for r in rows if "no_data_ok" in r["score"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
