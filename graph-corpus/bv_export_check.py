"""Capability check of the investigation spreadsheet (BV 1.5 / 2.5) on a live adapter.

P-1.5-excel and I-2.5-excel ask for an Excel table of the people found from a
person or a phone number. The answer text cannot score that, so this check:

  1. asks the capability question (fresh user, every corpus drive, purpose) and
     records whether the planner built an investigation table;
  2. downloads the table through /ui/graph/investigation/export -- the table of
     the answer, or, when the planner made none, a direct export from the seed;
  3. checks the workbook (OOXML parts, sheets, header, row count, sources within
     the scope) with the stdlib only;
  4. scores the cell text against the gold items of the scored questions on the
     same seed (bv_eval.score), i.e. how much of what BV asks for is in the file;
  5. exports again narrowed to one drive and checks that every source stays in it.

    python3 bv_export_check.py --adapter http://127.0.0.1:8088 --case-ref BV-EVAL/1 \
        [--legal-basis ...] [--out bv_export_check.jsonl]
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
import zipfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bv_eval  # noqa: E402
from bv_eval_run import DRIVES, answer_text, b64  # noqa: E402

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
CHECKS = {"P-1.5-excel": 2, "I-2.5-excel": 2}
HEADER_HU = ["Szint", "Név", "Szerep", "Nyilvántartási szám", "Azonosító szám", "Telefon",
             "Lakcím", "E-mail", "Egyéb azonosítók", "Kapcsolódás útja", "További szál",
             "Forrás", "Oldal", "Összevont rekordok"]


def post(adapter, path, user, drives, body, timeout):
    req = urllib.request.Request(
        adapter.rstrip("/") + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "VVS-User": user,
                 "VVS-Drive": b64(drives[0]), "VVS-Other-Drives": b64("\x00".join(drives))})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "error": e.read().decode("utf-8", "replace")[:500]}


def read_book(content):
    """{sheet name: rows of cell strings} and the problems found in the package."""
    problems, sheets = [], {}
    book = zipfile.ZipFile(io.BytesIO(content))
    names = set(book.namelist())
    for part in ("[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
                 "xl/_rels/workbook.xml.rels", "xl/styles.xml"):
        if part not in names:
            problems.append("missing " + part)
    for name in names:
        if name.endswith(".xml") or name.endswith(".rels"):
            try:
                ET.fromstring(book.read(name))
            except ET.ParseError as e:
                problems.append("%s: %s" % (name, e))
    for i, sheet in enumerate(ET.fromstring(book.read("xl/workbook.xml")).find("m:sheets", NS), 1):
        rows = []
        root = ET.fromstring(book.read("xl/worksheets/sheet%d.xml" % i))
        if root.find(".//m:f", NS) is not None:
            problems.append("formula in " + sheet.get("name"))
        for row in root.find("m:sheetData", NS):
            cells = {}
            for c in row:
                col = "".join(ch for ch in c.get("r") if ch.isalpha())
                t = c.find("m:is/m:t", NS)
                cells[col] = t.text if t is not None else c.find("m:v", NS).text
            width = max((sum((ord(ch) - 64) * 26 ** k for k, ch in enumerate(reversed(col)))
                         for col in cells), default=0)
            rows.append([cells.get(_letter(j)) for j in range(width)])
        sheets[sheet.get("name")] = rows
    return sheets, problems


def _letter(index):
    out, index = "", index + 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def export(args, user, drives, start, levels, audit_id=None, narrow=None):
    body = {"start": start, "levels": levels, "lang": "hu", "deliver": "download",
            "audit_id": audit_id}
    if narrow:
        body["drives"] = narrow
    for key in ("case_ref", "legal_basis"):
        if getattr(args, key):
            body[key] = getattr(args, key)
    return post(args.adapter, "/api/v1/ui/graph/investigation/export", user, drives, body,
                args.timeout)


def check_table(out, scope):
    if not out.get("content_b64"):
        return {"ok": False, "status": out.get("status"), "error": out.get("error")}
    sheets, problems = read_book(base64.b64decode(out["content_b64"]))
    table = next(iter(sheets.values()))
    header, rows = table[0], table[1:]
    src = header.index("Forrás") if "Forrás" in header else None
    sources = sorted({r[src] for r in rows if src is not None and src < len(r) and r[src]})
    outside = [s for s in sources if not any(s.startswith(d) for d in scope)]
    if header != HEADER_HU:
        problems.append("header %s" % header)
    if len(rows) != out.get("rows"):
        problems.append("rows %d != reported %s" % (len(rows), out.get("rows")))
    if outside:
        problems.append("sources outside the scope: %s" % outside[:3])
    text = "\n".join(" | ".join(c or "" for c in r) for r in rows)
    return {"ok": not problems, "problems": problems, "sheets": list(sheets), "rows": len(rows),
            "size": out.get("size"), "truncated": out.get("truncated"), "sources": len(sources),
            "text": text}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--persons", type=int, default=120)
    ap.add_argument("--seed", type=int, default=26)
    ap.add_argument("--drives", default=",".join(DRIVES))
    ap.add_argument("--narrow", default=DRIVES[0], help="the one drive of the narrowed export")
    ap.add_argument("--user-prefix", default="bv-export")
    ap.add_argument("--case-ref", dest="case_ref", default=os.environ.get("BV_EVAL_CASE_REF", ""))
    ap.add_argument("--legal-basis", dest="legal_basis",
                    default=os.environ.get("BV_EVAL_LEGAL_BASIS", ""))
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="bv_export_check.jsonl")
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
            # The scored questions on the same seed: the gold of the file.
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
            out = export(args, user, drives, start, levels, res.get("audit_id"))
            table = check_table(out, drives)
            narrow = check_table(export(args, user, drives, start, levels,
                                        narrow=[args.narrow.rstrip("/")]), [args.narrow])
            scores = [bv_eval.score(r, table.get("text") or "", data["names"], data["phones"])
                      for r in related]
            hits, must = sum(s["hits"] for s in scores), sum(s["must"] for s in scores)
            row = {"id": q["id"], "question": q["question"], "seconds": seconds,
                   "answer_ok": res.get("ok"), "calls": [c.get("tool") for c in
                                                         graph.get("calls") or []],
                   "investigation": how, "start": start, "levels": levels,
                   "answer_rows": len(invs[0].get("rows") or []) if invs else None,
                   "table": {k: v for k, v in table.items() if k != "text"},
                   "narrow": {k: v for k, v in narrow.items() if k != "text"},
                   "gold": {s["id"]: "%d/%d" % (s["hits"], s["must"]) for s in scores},
                   "missed": {s["id"]: s["missed"] for s in scores if s["missed"]},
                   "answer": answer_text(res)[:600]}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            good = table.get("ok") and (narrow.get("ok") or narrow.get("status") == 404)
            ok = ok and bool(good)
            print("%-14s %-6s start=%s L%d rows=%s (answer %s, narrowed %s) file %s; gold %d/%d %s"
                  % (q["id"], how, start, levels, table.get("rows"), row["answer_rows"],
                     narrow.get("rows", narrow.get("status")),
                     "ok" if table.get("ok") else table.get("problems") or table.get("error"),
                     hits, must, row["gold"]), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
