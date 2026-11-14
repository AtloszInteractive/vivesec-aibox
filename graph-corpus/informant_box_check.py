"""Box check of the informant protection (E29) with group tokens.

Runs on a standalone box after the intelligence log sample was imported
(structured_import_run.py) into a folder of --drive. It signs short-lived
tokens (as structured_import_run.headers_for) for invented callers that differ
only in their groups and roles, and checks through the live adapter:

  * a national analyst (group bv-orszagos), an analyst of institution 01 and a
    leader do not see any informant of the sample: not by name (graph find),
    not around a log entry (neighborhood), not in the investigation table, its
    subgraph or its spreadsheet;
  * a FEFO member, by group (bv-fefo) or by role (fefo), finds every informant;
  * the leader's query log view shows the FEFO member's records sealed: no
    question text, no informant name, no protected path;
  * with --rag (E29 step 2), the free text of the log and ODIN entries is among
    the retrieval hits of a question about it (a model answer each) only for a
    caller the folder and the record's institution admit, never the informant's
    name or a protected record (a caller without any group sees none).

    python3 informant_box_check.py --drive /storage/drives/nyilvantartas \
        --case-ref 2026/1114/1 [--rag] [--adapter http://127.0.0.1:8088]

Prints one JSON line per check and exits 1 when one fails.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import structured_samples as samples  # noqa: E402
from structured_import_run import adapter_env, headers_for, post  # noqa: E402

OPEN_PEOPLE = {row[2] for row in samples.INMATES} | {row[3] for row in samples.CONTACT_ROWS}
# Bodó Ákos is an informant and an inmate of the FŐNIX export: everyone finds the
# inmate; that he informed is checked through the names only informants have.
INFORMANTS = sorted({row[8] for row in samples.INTEL_ROWS} - OPEN_PEOPLE)
SUBJECTS = sorted({row[4] for row in samples.INTEL_ROWS})
ENTRIES = sorted({row[1] for row in samples.INTEL_ROWS})
HIDDEN_MARKS = ["Forrás neve", "Forrás nyilvántartási száma", "Forrás típusa", "~protected"]


class Caller:
    def __init__(self, name, groups=(), roles=(), fefo=False):
        self.name, self.groups, self.roles, self.fefo = name, groups, roles, fefo


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--drive", required=True)
    ap.add_argument("--case-ref", required=True)
    ap.add_argument("--container", default="vivesec-adapter")
    ap.add_argument("--rag", action="store_true", help="check the log's free text in the RAG")
    args = ap.parse_args()
    env = adapter_env(args.container)
    stamp = time.strftime("%H%M%S")
    callers = [
        Caller("e29-orszagos-" + stamp, ["bv-orszagos"]),
        Caller("e29-intezet01-" + stamp, ["bv-intezet-01"]),
        Caller("e29-vezeto-" + stamp, ["bv-orszagos"], ["leader"]),
        Caller("e29-fefo-" + stamp, ["bv-orszagos", "bv-fefo"], fefo=True),
        Caller("e29-feforole-" + stamp, ["bv-orszagos"], ["fefo"], fefo=True),
    ]
    failed = False

    def call(caller, path, body):
        headers = headers_for(caller.name, args.drive, env, args.container,
                              groups=caller.groups, roles=caller.roles)
        return post(args.adapter, path, headers, dict(body, case_ref=args.case_ref))

    def report(check, caller, ok, **detail):
        nonlocal failed
        failed = failed or not ok
        print(json.dumps(dict({"check": check, "caller": caller.name, "fefo": caller.fefo,
                               "ok": ok}, **detail), ensure_ascii=False), flush=True)

    def find(caller, text, types):
        status, out = call(caller, "/api/v1/ui/graph/query",
                           {"query": "find", "params": {"text": text, "types": types}})
        return status, out.get("entities") or []

    for caller in callers:
        seen = []
        for name in INFORMANTS:
            status, found = find(caller, name, ["person"])
            if status != 200:
                report("find", caller, False, status=status, error=str(found)[:200])
                continue
            if any(e.get("label") == name for e in found):
                seen.append(name)
        report("find informants", caller, seen == (INFORMANTS if caller.fefo else []), seen=seen)
        subjects = [n for n in SUBJECTS if any(e.get("label") == n
                                               for e in find(caller, n, ["person"])[1])]
        report("find subjects", caller, bool(subjects), seen=subjects)
        around = ""
        for entry in ENTRIES:
            status, found = find(caller, entry, ["event"])
            for e in found:
                status, hood = call(caller, "/api/v1/ui/graph/query",
                                    {"query": "neighborhood", "params": {"id": e["id"]}})
                around += json.dumps(hood, ensure_ascii=False)
        named = [n for n in INFORMANTS if n in around]
        marks = [m for m in HIDDEN_MARKS if m in around]
        report("log entry neighborhood", caller,
               (bool(named) if caller.fefo else not named and not marks) and bool(around),
               informants=named, marks=marks)
        request = {"start": "NY-1002", "levels": 2}
        status, sub = call(caller, "/api/v1/ui/graph/investigation/graph", request)
        status_x, xlsx = call(caller, "/api/v1/ui/graph/investigation/export", request)
        blob = json.dumps(sub, ensure_ascii=False)
        if status_x == 200 and xlsx.get("content_b64"):
            book = zipfile.ZipFile(io.BytesIO(base64.b64decode(xlsx["content_b64"])))
            blob += "".join(book.read(n).decode("utf-8") for n in book.namelist())
        named = [n for n in INFORMANTS if n in blob]
        marks = [m for m in HIDDEN_MARKS if m in blob]
        report("investigation, subgraph, spreadsheet", caller,
               status in (200, 404) and status_x in (200, 404)
               and (caller.fefo or not named and not marks),
               status=[status, status_x], informants=named, marks=marks)

    if args.rag:
        for caller in (callers[0], callers[1], callers[3], Caller("e29-nogroup-" + stamp)):
            rag_check(caller, call, report)

    leader = callers[2]
    status, log = call(leader, "/api/v1/ui/querylog", {})
    records = log.get("records") or []
    dump = json.dumps(log, ensure_ascii=False)
    fefo_records = [r for r in records if r.get("user") in {c.name for c in callers if c.fefo}]
    report("leader's query log", leader,
           status == 200 and log.get("role") == "leader" and bool(fefo_records)
           and all(r.get("sealed") and "question" not in r for r in fefo_records)
           and not [n for n in INFORMANTS if n in dump] and "~protected" not in dump,
           status=status, role=log.get("role"), fefo_records=len(fefo_records),
           sealed=sum(1 for r in fefo_records if r.get("sealed")))
    return 1 if failed else 0


RAG_PROBES = [  # (question, title of the record it is about, institution)
    ("Mikor akarnak kábítószert bejuttatni a látogatáskor?", "Felderítési napló 2026-14-1", "01"),
    ("Mikor terveznek szökési kísérletet a sétaidőben?", "Felderítési napló 2026-14-3", "02"),
    ("Hol találtak mobiltelefont a matrac alatt a zárkaellenőrzéskor?", "ODIN R-2026-31", "01"),
    ("Mit találtak a sétaudvar kerítésénél, vágószerszámot?", "ODIN R-2026-32", "02"),
]


def rag_check(caller, call, report):
    """The log's and ODIN's free text through the adapter's retrieval: a record
    is among the hits only for a caller its folder and its institution admit;
    an informant's name or a protected record never."""
    allowed = {"01", "02"} if "bv-orszagos" in caller.groups else \
        {g[-2:] for g in caller.groups if g.startswith("bv-intezet-")}
    for question, title, inst in RAG_PROBES:
        status, out = call(caller, "/api/v1/ui/query", {"query": question})
        hits = out.get("hits") or []
        paths = [h.get("path") or "" for h in hits]
        blob = json.dumps(out, ensure_ascii=False)
        found = any("/~records/" in p and "/%s (" % title in p for p in paths)
        named = [n for n in INFORMANTS if n in blob]
        report("free text in the RAG: " + title, caller,
               status == 200 and found == (inst in allowed) and not named
               and "~protected" not in blob,
               status=status, found=found, expected=inst in allowed, informants=named,
               records=[p.rsplit("/", 1)[-1] for p in paths if "/~records/" in p])


if __name__ == "__main__":
    sys.exit(main())
