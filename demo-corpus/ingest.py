"""Ingest the generated demo corpus into a ViVeSec rag_service instance.

Stdlib only, Python 3.8+, so it can be copied next to the RAG on the Jetson.
Drives the v1 drive-sync contract exactly the way the adapter does:

    POST /index/upsert/file/check    {corpus_id, tenant_id, path, size, mtime,
                                      head(b64 of first 256 B), content_type}
        -> {token, token_ttl_seconds} | {token: null, reason}
    POST /index/upsert/file/content/{token}     body = raw file bytes

Identity: the service derives doc_id = sha1(tenant \0 corpus \0 path)[:12].
We send tenant/corpus/path straight from manifest.jsonl, so the ingested
doc_ids match the ones recorded there.

Resume: size+mtime come from the manifest, so a re-run skips unchanged files
(the service answers token=null with a reason).

Usage (on the Jetson):
    python3 ingest.py --manifest manifest.jsonl --root . \
        --rag-url http://127.0.0.1:8093 [--api-key KEY] [--drive finance]
"""
from __future__ import print_function

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, OrderedDict


def read_manifest(path):
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _request(url, data, headers, timeout):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:400]
        return None, "HTTP %s %s" % (exc.code, body)
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def post_json(url, payload, api_key, timeout=120):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    return _request(url, json.dumps(payload).encode("utf-8"), headers, timeout)


def post_raw(url, raw, api_key, timeout=600):
    headers = {"Content-Type": "application/octet-stream"}
    if api_key:
        headers["X-API-Key"] = api_key
    return _request(url, raw, headers, timeout)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--manifest", default=os.path.join(here, "out", "manifest.jsonl"))
    ap.add_argument("--root", default=os.path.join(here, "out"),
                    help="directory the manifest local_path values are relative to")
    ap.add_argument("--rag-url", default="http://127.0.0.1:8090")
    ap.add_argument("--api-key", default=os.environ.get("RAG_API_KEY", ""))
    ap.add_argument("--drive", action="append", default=None,
                    help="restrict to one or more drives (repeatable)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="only list what would be sent, no HTTP calls")
    ap.add_argument("--content-timeout", type=int, default=900)
    args = ap.parse_args()

    rag_url = args.rag_url.rstrip("/")
    rows = read_manifest(args.manifest)
    if args.drive:
        wanted = set(args.drive)
        rows = [r for r in rows if r["drive"] in wanted]
    if args.limit:
        rows = rows[:args.limit]

    per_corpus = OrderedDict()
    for r in rows:
        per_corpus.setdefault(r["corpus_id"], 0)
        per_corpus[r["corpus_id"]] += 1
    print("manifest        : %s" % args.manifest)
    print("documents       : %d" % len(rows))
    for cid, n in per_corpus.items():
        print("  %-26s %3d" % (cid, n))
    print("target          : %s" % rag_url)
    print("tenant          : %s" % (rows[0]["tenant_id"] if rows else "-"))
    print("api key         : %s" % ("set" if args.api_key else "NOT SET"))
    if args.dry_run:
        for r in rows:
            print("  would ingest %s" % r["source_path"])
        return 0

    stats = Counter()
    errors = []
    warnings = []
    t0 = time.time()
    total = len(rows)

    for i, row in enumerate(rows, start=1):
        local = os.path.join(args.root, *row["local_path"].split("/"))
        try:
            with open(local, "rb") as f:
                raw = f.read()
        except OSError as exc:
            stats["missing_local_file"] += 1
            errors.append({"path": row["source_path"], "stage": "read", "error": str(exc)})
            continue

        check_payload = {
            "corpus_id": row["corpus_id"],
            "tenant_id": row["tenant_id"],
            "path": row["source_path"],
            "size": row["size"],
            "mtime": row["mtime"],
            "head": base64.b64encode(raw[:256]).decode("ascii"),
            "content_type": row["content_type"],
        }
        res, err = post_json(rag_url + "/index/upsert/file/check", check_payload, args.api_key)
        if err:
            stats["check_failed"] += 1
            errors.append({"path": row["source_path"], "stage": "check", "error": err})
            continue
        token = res.get("token")
        if token is None:
            stats["skipped"] += 1
            stats["skip_reason:%s" % res.get("reason", "unknown")] += 1
            continue

        res, err = post_raw(rag_url + "/index/upsert/file/content/" + token, raw,
                            args.api_key, timeout=args.content_timeout)
        if err:
            stats["content_failed"] += 1
            errors.append({"path": row["source_path"], "stage": "content", "error": err})
            continue

        stats["ingested"] += 1
        stats["chunks"] += int(res.get("chunks") or 0)
        if res.get("warning"):
            warnings.append({"path": row["source_path"], "warning": res["warning"],
                             "chunks": res.get("chunks")})

        if i % 10 == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed else 0
            print("  %3d/%d  %.1f doc/s  ok=%d skipped=%d err=%d"
                  % (i, total, rate, stats["ingested"], stats["skipped"], len(errors)))

    elapsed = time.time() - t0
    print("")
    print("done in %.1f s" % elapsed)
    print("  ingested        : %d" % stats["ingested"])
    print("  chunks created  : %d" % stats["chunks"])
    print("  skipped         : %d" % stats["skipped"])
    for key in sorted(k for k in stats if k.startswith("skip_reason:")):
        print("      %-24s %d" % (key.split(":", 1)[1], stats[key]))
    print("  errors          : %d" % len(errors))

    if warnings:
        print("")
        print("WARNINGS from the service (%d):" % len(warnings))
        for w in warnings:
            print("  %-12s chunks=%s  %s" % (w["warning"], w["chunks"], w["path"]))

    if errors:
        print("")
        print("ERRORS (%d):" % len(errors))
        for e in errors[:20]:
            print("  [%s] %s -> %s" % (e["stage"], e["path"], e["error"][:200]))
        err_path = os.path.join(os.path.dirname(os.path.abspath(args.manifest)),
                                "ingest_errors.jsonl")
        with open(err_path, "w") as f:
            for e in errors:
                f.write(json.dumps(e) + "\n")
        print("  full list: %s" % err_path)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
