#!/usr/bin/env python3
"""Ingest a few named documents on their own, timing each one.

Used to separate a document that is genuinely too slow from one that merely
timed out while queued behind a slow neighbour on the single-threaded server.
"""
import argparse
import base64
import json
import time
import urllib.error
import urllib.request


def post(url, data, api_key, content_type, timeout):
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", content_type)
    if api_key:
        req.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--rag-url", required=True)
    ap.add_argument("--api-key", default="")
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("paths", nargs="+", help="source_path values to ingest")
    args = ap.parse_args()

    wanted = set(args.paths)
    docs = []
    with open(args.manifest, encoding="utf-8") as fh:
        for line in fh:
            doc = json.loads(line)
            if doc["source_path"] in wanted:
                docs.append(doc)
    print("matched %d of %d requested" % (len(docs), len(wanted)))

    rag = args.rag_url.rstrip("/")
    for doc in docs:
        rel = doc["source_path"][len("/" + doc["corpus_id"] + "/"):]
        raw = open(args.root.rstrip("/") + "/" + rel, "rb").read()
        start = time.time()
        try:
            check = post(
                rag + "/index/upsert/file/check",
                json.dumps({
                    "corpus_id": doc["corpus_id"],
                    "tenant_id": doc["tenant_id"],
                    "path": doc["source_path"],
                    "size": doc["size"],
                    "mtime": doc["mtime"],
                    "head": base64.b64encode(raw[:256]).decode("ascii"),
                    "content_type": doc.get("content_type"),
                }).encode("utf-8"),
                args.api_key, "application/json", 120,
            )
            token = check.get("token")
            if not token:
                print("%-42s SKIP (%s)" % (doc["source_path"], check.get("reason")))
                continue
            res = post(rag + "/index/upsert/file/content/" + token, raw,
                       args.api_key, "application/octet-stream", args.timeout)
            print("%-42s %8.1f s  pages=%-5s chunks=%-6s" % (
                doc["source_path"], time.time() - start,
                res.get("pages"), res.get("chunks")))
        except Exception as exc:  # noqa: BLE001
            print("%-42s %8.1f s  FAILED: %s" % (
                doc["source_path"], time.time() - start, str(exc)[:120]))


if __name__ == "__main__":
    main()
