"""Demo corpus loader: feed the showcase documents into a running rag-engine.

This is the OPS-side counterpart to the engine's document processor. It does NOT
parse/chunk anything itself — it only reads each source file, stamps the right
file_id + ACL metadata, and POSTs it to /ingest. The engine (extract.py +
chunking.py) does the actual extraction + chunking behind that contract.

Why a separate script: it keeps the swappable engine a black box behind
/ingest + /search. Swap the engine, re-run this loader, done (the Box stays the
source of truth — see RAG_MOTOR_CSERE_RUNBOOK.md).

The file_id + acl_scope + sensitivity below MIRROR src/lib/rag/registry.server.ts
and poc/data/manifest.json. They MUST stay in sync: the bridge computes
allowed_file_ids from the registry, and only ingested file_ids with matching ids
are reachable. If you edit one, edit all three.

Usage (engine must be running, see RAG_MOTOR_CSERE_RUNBOOK.md):
    cd rag-engine
    python ingest_corpus.py
    python ingest_corpus.py --engine-url http://127.0.0.1:8081 --token <RAG_INTERNAL_TOKEN>

Stdlib-only (matches harness/client.py + smoke_test.py philosophy).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Corpus registry — KEEP IN SYNC with src/lib/rag/registry.server.ts.
# source = the .txt actually ingested; filename = display name shown in the UI /
# citation (DriveFile name in ViveSecApp.tsx). The .txt content is fed under the
# display name; the engine falls back to text decode if it can't binary-parse it.
# --------------------------------------------------------------------------- #
@dataclass
class CorpusDoc:
    file_id: str
    source: str          # file under --data-dir
    filename: str        # display name (citation label)
    acl_scope: list[str]
    sensitivity: str     # public | internal | confidential | restricted
    mime_type: str = "text/plain"
    doc_type: str | None = None
    language_hint: str | None = None


CORPUS: list[CorpusDoc] = [
    CorpusDoc("f1", "Contract_2025_Final.txt", "Contract_2025_Final.pdf",
              ["legal"], "confidential", doc_type="contract"),
    CorpusDoc("f2", "Q4_Financials.txt", "Q4_Financials.xlsx",
              ["finance"], "confidential", doc_type="financials"),
    CorpusDoc("f4", "Board_Meeting_Transcript_2025-11-12.txt", "Board_Meeting_Transcript_2025-11-12.txt",
              ["board"], "restricted", doc_type="transcript"),
    CorpusDoc("f5", "Security_Audit_Report.txt", "Security_Audit_Report.pdf",
              ["security"], "confidential", doc_type="audit"),
    CorpusDoc("f6", "Vendor_Risk_Matrix.txt", "Vendor_Risk_Matrix.md",
              ["risk", "procurement"], "internal", doc_type="matrix"),
    CorpusDoc("fh1", "Employee_Handbook_v4.txt", "Employee_Handbook_v4.docx",
              ["hr", "all"], "internal", doc_type="handbook"),
    CorpusDoc("fsla", "Cloud_Infrastructure_SLA_2026.txt", "Cloud_Infrastructure_SLA_2026.pdf",
              ["it", "all"], "internal", doc_type="sla"),
    CorpusDoc("fv1", "Vendor_Agreement_2025_Final.txt", "Vendor_Agreement_2025.pdf",
              ["legal", "procurement"], "confidential", doc_type="agreement"),
    CorpusDoc("fa1", "Q4_Infrastructure_Security_Audit.txt", "Q4_Infrastructure_Security_Audit.pdf",
              ["security"], "confidential", doc_type="audit"),
]


@dataclass
class Result:
    file_id: str
    ok: bool
    status: str = ""
    chunks: int = 0
    note: str = ""


def _headers(token: str) -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _health(engine_url: str, token: str, timeout: float) -> dict:
    req = urllib.request.Request(
        engine_url.rstrip("/") + "/healthz", headers=_headers(token), method="GET"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_ingest(engine_url: str, token: str, payload: dict, timeout: float) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        engine_url.rstrip("/") + "/ingest", data=data, headers=_headers(token), method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": str(e)}
        return e.code, body


def _build_payload(doc: CorpusDoc, raw: bytes) -> dict:
    content_hash = hashlib.sha256(raw).hexdigest()
    payload = {
        "file_id": doc.file_id,
        "content_hash": content_hash,
        "filename": doc.filename,
        "mime_type": doc.mime_type,
        "acl_scope": doc.acl_scope,
        "sensitivity": doc.sensitivity,
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "metadata": {"source_file": doc.source},
    }
    if doc.doc_type:
        payload["doc_type"] = doc.doc_type
    if doc.language_hint:
        payload["language_hint"] = doc.language_hint
    return payload


def ingest_corpus(engine_url: str, token: str, data_dir: Path, timeout: float) -> list[Result]:
    results: list[Result] = []
    for doc in CORPUS:
        path = data_dir / doc.source
        if not path.is_file():
            results.append(Result(doc.file_id, False, note=f"missing source: {path}"))
            print(f"  SKIP {doc.file_id:5s} {doc.filename:42s} (missing {doc.source})")
            continue

        raw = path.read_bytes()
        code, body = _post_ingest(engine_url, token, _build_payload(doc, raw), timeout)
        if code != 200:
            results.append(Result(doc.file_id, False, note=f"HTTP {code}: {body}"))
            print(f"  FAIL {doc.file_id:5s} {doc.filename:42s} HTTP {code}: {body}")
            continue

        status = body.get("status", "?")
        chunks = body.get("chunks_indexed", body.get("chunks_total", 0))
        results.append(Result(doc.file_id, True, status=status, chunks=chunks))
        print(f"  OK   {doc.file_id:5s} {doc.filename:42s} {status:10s} chunks={chunks}")
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load the demo corpus into a running rag-engine.")
    ap.add_argument("--engine-url", default=os.environ.get("RAG_ENGINE_URL", "http://127.0.0.1:8081"))
    ap.add_argument("--token", default=os.environ.get("RAG_INTERNAL_TOKEN", ""))
    ap.add_argument(
        "--data-dir",
        default=str(Path(__file__).resolve().parent.parent / "poc" / "data"),
        help="Folder holding the corpus source files (default: ../poc/data).",
    )
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    print(f"rag-engine : {args.engine_url}")
    print(f"corpus dir : {data_dir}")

    try:
        health = _health(args.engine_url, args.token, timeout=10.0)
    except Exception as e:
        print(f"\nERROR: rag-engine not reachable at {args.engine_url} ({e})", file=sys.stderr)
        print("Start it first (see RAG_MOTOR_CSERE_RUNBOOK.md):", file=sys.stderr)
        print("  uvicorn app.main:app --host 127.0.0.1 --port 8081 --app-dir .", file=sys.stderr)
        return 2

    print(f"engine     : {health.get('engine', {}).get('name', '?')} "
          f"v{health.get('engine', {}).get('version', '?')}\n")

    results = ingest_corpus(args.engine_url, args.token, data_dir, args.timeout)

    ok = sum(1 for r in results if r.ok)
    total_chunks = sum(r.chunks for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    print(f"\n{ok}/{len(results)} documents ingested, {total_chunks} chunks total.")
    if failed:
        print(f"{len(failed)} failed: {', '.join(r.file_id for r in failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
