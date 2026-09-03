"""Manifest-driven govdocs corpus loader for the silver-set evaluation.

Pushes the RAW selected files (data/selected/...) through the production
Drive-sync route: POST /index/upsert/file/content {content_b64} — each engine
does its own extraction/chunking, which is exactly what the drop-in replaces.

Stdlib-only on purpose: the same script must run on the Jetson (python3, no
venv). Idempotent: each corpus is dropped once ("/") before its first doc.

Usage (dev machine or Jetson):
    python ingest_govdocs.py \
        --engine-url http://localhost:8090 --token <key> \
        --manifest .../corpus_manifest.jsonl --files-root .../data \
        [--zip-prefix 100] [--limit 50] [--no-clear]

`--zip-prefix 100` selects the Stage-A subset (source_path /corpus/100/...).
Exit code 1 when any document failed, so wrappers can gate on it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request


def _post(url: str, payload: dict, token: str, timeout: float = 300.0) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-API-Key"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # keep body for diagnostics
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            body = {}
        return e.code, body
    except (urllib.error.URLError, OSError) as e:
        # timeout / connection error: report as status 0, caller counts a
        # failure and CONTINUES (one giant PDF must not kill the whole run)
        return 0, {"error": f"{type(e).__name__}: {e}"}


def load_manifest(path: str) -> list[dict]:
    docs: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine-url", required=True)
    ap.add_argument("--token", default=os.environ.get("RAG_API_KEY", ""))
    ap.add_argument("--manifest", required=True, help="corpus_manifest.jsonl")
    ap.add_argument("--files-root", required=True,
                    help="dir that contains selected/<zip>/<file> (the factory data/ dir)")
    ap.add_argument("--zip-prefix", default=None,
                    help="only docs whose source_path is /<corpus>/<zip-prefix>/... (Stage A)")
    ap.add_argument("--paths-file", default=None,
                    help="file with one source_path per line; only those docs "
                         "are ingested (targeted Stage-A batches)")
    ap.add_argument("--limit", type=int, default=0, help="max docs (0 = all)")
    ap.add_argument("--no-clear", action="store_true",
                    help="do not drop corpora first (append mode)")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()

    docs = load_manifest(args.manifest)
    if args.zip_prefix:
        docs = [d for d in docs
                if d["source_path"].split("/")[2] == str(args.zip_prefix)]
    if args.paths_file:
        with open(args.paths_file, "r", encoding="utf-8") as f:
            wanted = {line.strip() for line in f if line.strip()}
        docs = [d for d in docs if d["source_path"] in wanted]
        missing = wanted - {d["source_path"] for d in docs}
        if missing:
            print(f"WARN {len(missing)} wanted paths not in manifest: "
                  f"{sorted(missing)[:3]}...", file=sys.stderr)
    if args.limit:
        docs = docs[: args.limit]
    if not docs:
        print("no documents selected", file=sys.stderr)
        return 1

    base = args.engine_url.rstrip("/")
    cleared: set[str] = set()
    ok = failed = 0
    fail_by_type: dict[str, int] = {}
    t0 = time.time()

    for i, d in enumerate(docs, 1):
        corpus_id = d["corpus_id"]
        if corpus_id not in cleared and not args.no_clear:
            code, body = _post(f"{base}/index/drop/tree",
                               {"corpus_id": corpus_id, "tenant_id": d["tenant_id"],
                                "path": "/", "keep_exact": False},
                               args.token, args.timeout)
            if code != 200:
                print(f"FATAL drop_tree({corpus_id}) -> {code} {body}", file=sys.stderr)
                return 1
            cleared.add(corpus_id)

        local = os.path.join(args.files_root, *d["local_path"].replace("\\", "/").split("/")[1:]) \
            if not os.path.isabs(d["local_path"]) else d["local_path"]
        # manifest local_path is like data\selected\100\100000.html — relative
        # to the factory root; files-root points AT that data/ dir.
        try:
            with open(local, "rb") as f:
                raw = f.read()
        except OSError as e:
            print(f"READ-FAIL {d['source_path']}: {e}", file=sys.stderr)
            failed += 1
            fail_by_type[d["file_type"]] = fail_by_type.get(d["file_type"], 0) + 1
            continue

        payload = {
            "corpus_id": corpus_id,
            "tenant_id": d["tenant_id"],
            "path": d["source_path"],
            "source_path": d["source_path"],  # both names: spec + tolerant engines
            "title": d.get("title") or os.path.basename(d["source_path"]),
            "content_type": d.get("content_type") or "application/octet-stream",
            "content_b64": base64.b64encode(raw).decode("ascii"),
            "metadata": {**(d.get("metadata") or {}), "acl": d.get("acl", []),
                         "file_type": d.get("file_type")},
        }
        code, body = _post(f"{base}/index/upsert/file/content", payload,
                           args.token, args.timeout)
        if code == 200 and body.get("ok", True):
            ok += 1
        else:
            failed += 1
            fail_by_type[d["file_type"]] = fail_by_type.get(d["file_type"], 0) + 1
            detail = body.get("detail") or body.get("error") or body
            print(f"INGEST-FAIL {d['source_path']} -> {code} {str(detail)[:200]}",
                  file=sys.stderr)

        if i % 25 == 0 or i == len(docs):
            rate = i / max(time.time() - t0, 1e-9) * 60.0
            print(f"[{i}/{len(docs)}] ok={ok} failed={failed} ({rate:.1f} docs/min)",
                  flush=True)

    dt = time.time() - t0
    print(f"DONE docs={len(docs)} ok={ok} failed={failed} in {dt:.1f}s "
          f"({len(docs) / max(dt, 1e-9) * 60.0:.1f} docs/min)")
    if fail_by_type:
        print("failures by file_type:", json.dumps(fail_by_type))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
