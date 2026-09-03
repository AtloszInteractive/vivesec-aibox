"""Populate the ADAPTER's metadata mirror from the demo-corpus manifest.

`ingest.py` loads the documents straight into the RAG service, which is enough
for retrieval but leaves the adapter's mirror (`adapter/meta.py`) empty — and
the mirror is what answers the UI's drive listing and the `#search files:`
quick action. This script replays the same manifest through the adapter's
drive-sync endpoints so the box ends up in the state a real ViVeSecBox sync
would produce:

    POST /api/v1/index/upsert/directory        {path}
    POST /api/v1/index/upsert/file/check       {path, size, mtime, content_type}

No content is uploaded: `check` records the file metadata in the mirror
regardless of the token it hands back (adapter/service.py `_check`), and the
RAG answers `token: null` for the already-ingested documents, so nothing is
re-embedded.

Usage (from the dev machine or the box):
    python mirror_sync.py --adapter-url http://192.168.0.181:8088
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter


def read_manifest(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def post(url, payload, timeout=60):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, "HTTP %s %s" % (exc.code, exc.read().decode("utf-8", "replace")[:200])
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def directories_of(paths):
    """Every ancestor directory under /storage/drives, deepest last."""
    dirs = set()
    for p in paths:
        parts = p.strip("/").split("/")
        for i in range(3, len(parts)):  # keep /storage/drives/<drive> and below
            dirs.add("/" + "/".join(parts[:i]))
    return sorted(dirs)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=os.path.join(here, "out", "manifest.jsonl"))
    ap.add_argument("--adapter-url", default="http://127.0.0.1:8088")
    ap.add_argument("--drive", action="append", default=None,
                    help="limit to these drives (repeatable)")
    args = ap.parse_args()

    rows = read_manifest(args.manifest)
    if args.drive:
        keep = set(args.drive)
        rows = [r for r in rows if r.get("drive") in keep]
    if not rows:
        print("nothing to sync")
        return 1

    base = args.adapter_url.rstrip("/") + "/api/v1"
    paths = [r["source_path"] for r in rows]

    dirs = directories_of(paths)
    dir_err = 0
    for d in dirs:
        _, err = post(base + "/index/upsert/directory", {"path": d})
        if err:
            dir_err += 1
            print("DIR  FAIL %s -> %s" % (d, err))
    print("directories: %d ok, %d failed" % (len(dirs) - dir_err, dir_err))

    stats = Counter()
    for i, r in enumerate(rows, 1):
        out, err = post(base + "/index/upsert/file/check", {
            "path": r["source_path"],
            "size": r.get("size"),
            "mtime": r.get("mtime"),
            "content_type": r.get("content_type"),
        })
        if err:
            stats["error"] += 1
            print("FILE FAIL %s -> %s" % (r["source_path"], err))
            continue
        stats["token" if out.get("token") else "unchanged"] += 1
        if i % 25 == 0:
            print("  ... %d/%d" % (i, len(rows)))

    print("files: %d unchanged (already indexed), %d would need content, %d errors"
          % (stats["unchanged"], stats["token"], stats["error"]))
    return 0 if not stats["error"] and not dir_err else 2


if __name__ == "__main__":
    sys.exit(main())
