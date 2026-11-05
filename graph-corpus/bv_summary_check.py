"""Capability check of the per-person summaries (BV 1.4 / 2.4) on a live adapter.

P-1.4-szemelyenkent and I-2.4-szemelyenkent ask for a short summary of each
person found from an inmate or a phone number. The summaries are a background
task under an investigation answer, so this check:

  1. asks the capability question (fresh user, every corpus drive, purpose) and
     records whether the planner built an investigation table;
  2. starts the summaries through /ui/graph/investigation/summaries -- for the
     table of the answer, or, when the planner made none, directly from the
     start with one level -- and polls the background task to its end;
  3. checks the result: one section per person, every [#n] a citation, every
     cited source within the scope;
  4. scores each person's own section against that person's gold (bv_eval.
     summary_gold: the kinds of their incidents, the number on a phone found on
     them);
  5. runs again narrowed to one drive and checks that every source stays in it.

    python3 bv_summary_check.py --adapter http://127.0.0.1:8088 --case-ref BV-EVAL/1 \
        [--legal-basis ...] [--only P-1.4-szemelyenkent] [--out bv_summary_check.jsonl]
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bv_eval  # noqa: E402
from bv_eval_run import DRIVES, answer_text  # noqa: E402
from bv_export_check import post  # noqa: E402

CHECKS = ("P-1.4-szemelyenkent", "I-2.4-szemelyenkent")


def purpose(args):
    return {k: getattr(args, k) for k in ("case_ref", "legal_basis") if getattr(args, k)}


def summaries(args, user, drives, start, levels, audit_id=None, narrow=None):
    """(submission, finished job result, seconds)."""
    body = dict({"start": start, "levels": levels, "lang": "hu", "audit_id": audit_id},
                **purpose(args))
    if narrow:
        body["drives"] = narrow
    started = time.time()
    sub = post(args.adapter, "/api/v1/ui/graph/investigation/summaries", user, drives, body,
               args.timeout)
    if not sub.get("job_id"):
        return sub, None, 0.0
    deadline = time.time() + args.job_timeout
    while time.time() < deadline:
        res = post(args.adapter, "/api/v1/ui/poll", user, drives,
                   {"job_id": sub["job_id"], "timeout": 25}, 60)
        if res.get("status") != "pending":
            return sub, res, round(time.time() - started, 1)
    return sub, {"ok": False, "error": "timeout"}, round(time.time() - started, 1)


def sections(text):
    """{person name: their section} of the summaries' markdown."""
    out = {}
    for part in re.split(r"\n### ", "\n" + text)[1:]:
        heading, _, body = part.partition("\n")
        name = re.split(r" \u2014 | \(", heading, 1)[0].strip()
        out.setdefault(name, "")
        out[name] += body
    return out


def check(res, scope):
    if not res or not res.get("ok"):
        return {"ok": False, "status": (res or {}).get("status"),
                "error": (res or {}).get("error")}
    problems = []
    text = answer_text(res)
    citations = res.get("citations") or []
    persons = (res.get("person_summaries") or {}).get("persons") or []
    found = sections(text)
    if len(found) != len(persons):
        problems.append("sections %d != persons %d" % (len(found), len(persons)))
    refs = {int(n) for n in re.findall(r"\[#(\d+)\]", text)}
    if refs and max(refs) > len(citations):
        problems.append("reference [#%d] beyond %d citations" % (max(refs), len(citations)))
    outside = sorted({c.get("path") for c in citations
                      if not any((c.get("path") or "").startswith(d) for d in scope)})
    if outside:
        problems.append("sources outside the scope: %s" % outside[:3])
    if any(not (p.get("summary") or "").strip() for p in persons):
        problems.append("a person without a summary")
    info = (res.get("person_summaries") or {}).get("info") or {}
    return {"ok": not problems, "problems": problems, "persons": len(persons),
            "cited": sum(1 for p in persons if p.get("cited")), "citations": len(citations),
            "sources": len({c.get("path") for c in citations}),
            "passages": info.get("passages"), "partial": info.get("partial"),
            "sections": found, "text": text}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--persons", type=int, default=120)
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--drives", default=",".join(DRIVES))
    ap.add_argument("--narrow", default=DRIVES[2], help="the one drive of the narrowed run")
    ap.add_argument("--user-prefix", default="bv-summary")
    ap.add_argument("--case-ref", dest="case_ref", default=os.environ.get("BV_EVAL_CASE_REF", ""))
    ap.add_argument("--legal-basis", dest="legal_basis",
                    default=os.environ.get("BV_EVAL_LEGAL_BASIS", ""))
    ap.add_argument("--only", action="append", default=[], help="question id (repeatable)")
    ap.add_argument("--no-narrow", action="store_true", help="skip the narrowed run")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--job-timeout", type=int, default=5400)
    ap.add_argument("--out", default="bv_summary_check.jsonl")
    args = ap.parse_args()

    data = bv_eval.build_eval(args.persons, args.seed)
    drives = [d.strip() for d in args.drives.split(",") if d.strip()]
    stamp = time.strftime("%Y%m%d%H%M%S")
    ok = True
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for q in data["questions"]:
            if q["id"] not in CHECKS or (args.only and q["id"] not in args.only):
                continue
            user = "%s-%s-%s" % (args.user_prefix, stamp, q["id"].lower())
            started = time.time()
            res = post(args.adapter, "/api/v1/ui/query", user, drives,
                       dict({"query": q["question"]}, **purpose(args)), args.timeout)
            seconds = round(time.time() - started, 1)
            graph = res.get("graph") or {}
            invs = graph.get("investigations") or []
            if invs:
                start, levels, how = invs[0]["start"], invs[0]["levels"], "answer"
            else:
                start, levels, how = q["start"], q["levels"], "direct"
            sub, done, job_seconds = summaries(args, user, drives, start, levels,
                                               res.get("audit_id"))
            result = check(done, drives)
            narrow = {"skipped": True}
            if not args.no_narrow:
                _, nres, _ = summaries(args, user, drives, start, levels,
                                       narrow=[args.narrow.rstrip("/")])
                narrow = check(nres, [args.narrow])
            gold, hits, must = {}, 0, 0
            for name, items in q["summaries"].items():
                section = (result.get("sections") or {}).get(name)
                s = bv_eval.score({"id": name, "must": items}, section or "")
                gold[name] = "%d/%d%s" % (s["hits"], s["must"], "" if section is not None
                                          else " (no section)")
                hits, must = hits + s["hits"], must + s["must"]
            row = {"id": q["id"], "question": q["question"], "seconds": seconds,
                   "answer_ok": res.get("ok"),
                   "calls": [c.get("tool") for c in graph.get("calls") or []],
                   "investigation": how, "start": start, "levels": levels,
                   "submitted": {k: sub.get(k) for k in ("ok", "status", "error", "people",
                                                         "summarised")},
                   "job_seconds": job_seconds,
                   "result": {k: v for k, v in result.items() if k not in ("text", "sections")},
                   "narrow": {k: v for k, v in narrow.items() if k not in ("text", "sections")},
                   "gold": gold, "hits": hits, "must": must,
                   "summaries": result.get("text", "")}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            good = result.get("ok") and (narrow.get("ok") or narrow.get("skipped")
                                          or narrow.get("status") == 404)
            ok = ok and bool(good)
            print("%-20s %-6s start=%s L%d people=%s/%s %.0fs result %s narrowed %s; gold %d/%d %s"
                  % (q["id"], how, start, levels, sub.get("summarised"), sub.get("people"),
                     job_seconds, "ok" if result.get("ok") else result.get("problems")
                     or result.get("error"),
                     "skipped" if narrow.get("skipped") else
                     ("ok" if narrow.get("ok") else narrow.get("problems")
                      or narrow.get("status")), hits, must, gold), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
