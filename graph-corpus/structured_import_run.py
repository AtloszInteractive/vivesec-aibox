"""Import periodic exports into the graph through a live adapter (E28).

A graph administrator's call to /api/v1/ui/graph/import for each file: the
adapter checks the caller and the folder, the graph reads the rows by the
profile's mapping (structured_sources) and answers with what changed. Without
files, the synthetic exports of structured_samples.py are imported (the box
check of the contact linking).

    python3 structured_import_run.py --adapter http://127.0.0.1:8088 --user <graph admin> \
        --drive /storage/drives/<drive> --folder /storage/drives/<drive>/<folder> \
        [--file contacts=export.csv --as-of 2026-11-01] [--mode full] [--force]

The user must be in ADAPTER_GRAPH_ADMINS; the records are visible to callers who
may read the folder and whom each record's own rule admits (bv: the group of
its institution, group:bv-intezet-<code>, or group:bv-orszagos).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import structured_samples  # noqa: E402


def b64(text):
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def post(adapter, path, user, drive, body, timeout=600):
    req = urllib.request.Request(
        adapter.rstrip("/") + path, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "VVS-User": user,
                 "VVS-Drive": b64(drive.rstrip("/") + "/")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, {"ok": False, "error": e.read().decode("utf-8", "replace")[:500]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--adapter", default="http://127.0.0.1:8088")
    ap.add_argument("--user", required=True)
    ap.add_argument("--drive", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--file", action="append", default=[],
                    help="source=path (CSV/XLSX); repeatable. None: the synthetic samples")
    ap.add_argument("--as-of", default=structured_samples.AS_OF)
    ap.add_argument("--mode", choices=("delta", "full"))
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    jobs = []
    for item in args.file:
        source, _, path = item.partition("=")
        with open(path, "rb") as f:
            jobs.append((source, os.path.basename(path), f.read()))
    if not jobs:
        for source, (header, rows) in structured_samples.exports().items():
            jobs.append((source, source + ".csv", structured_samples.to_csv(header, rows)))
    failed = False
    for source, name, content in jobs:
        body = {"source": source, "as_of": args.as_of, "folder": args.folder, "filename": name,
                "content_b64": base64.b64encode(content).decode("ascii"), "force": args.force,
                "wait": 300}
        if args.mode:
            body["mode"] = args.mode
        status, out = post(args.adapter, "/api/v1/ui/graph/import", args.user, args.drive, body)
        print(json.dumps({"source": source, "file": name, "status": status,
                          "result": out.get("import") or out}, ensure_ascii=False))
        failed = failed or status != 200
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
