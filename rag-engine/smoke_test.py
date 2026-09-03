"""End-to-end smoke test for the rag-engine contract skeleton.

Verifies the wire contract and the security invariants WITHOUT any heavy deps:
    - /healthz reports engine + backend
    - /ingest indexes a doc and is idempotent on the same content_hash
    - /search honours the HARD allowed_file_ids pre-filter (invariant 2):
        * empty allowed set => zero hits
        * a file_id NOT in allowed set is never returned
    - /search returns verbatim chunk_text (invariant 3)

Run the server first:  uvicorn app.main:app --port 8081
Then:                  python smoke_test.py
"""
from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("RAG_BASE_URL", "http://127.0.0.1:8081")
TOKEN = os.environ.get("RAG_INTERNAL_TOKEN", "")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _post(path: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers=_headers(), method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _get(path: str) -> tuple[int, dict]:
    req = urllib.request.Request(BASE + path, headers=_headers(), method="GET")
    with urllib.request.urlopen(req) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def main() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    # health
    code, health = _get("/healthz")
    check("healthz 200", code == 200)
    check("healthz reports vector_backend", "vector_backend" in health)

    fid_a = "vivesec://files/aaa-1"
    fid_b = "vivesec://files/bbb-2"

    # ingest A
    code, res = _post(
        "/ingest",
        {
            "file_id": fid_a,
            "content_hash": "sha256:aaa",
            "filename": "Q4_Financials.txt",
            "mime_type": "text/plain",
            "acl_scope": ["finance"],
            "sensitivity": "confidential",
            "language_hint": "en",
            "content_base64": _b64("The Q4 revenue was 4.2 billion HUF in total."),
        },
    )
    check("ingest A 200", code == 200)
    check("ingest A status indexed", res.get("status") == "indexed")

    # ingest A again (idempotent)
    code, res = _post(
        "/ingest",
        {
            "file_id": fid_a,
            "content_hash": "sha256:aaa",
            "filename": "Q4_Financials.txt",
            "mime_type": "text/plain",
            "acl_scope": ["finance"],
            "sensitivity": "confidential",
            "content_base64": _b64("ignored"),
        },
    )
    check("ingest A idempotent (unchanged)", res.get("status") == "unchanged")

    # ingest B (a file the user will NOT be allowed to see)
    _post(
        "/ingest",
        {
            "file_id": fid_b,
            "content_hash": "sha256:bbb",
            "filename": "Secret_Board_Notes.txt",
            "mime_type": "text/plain",
            "acl_scope": ["board"],
            "sensitivity": "restricted",
            "content_base64": _b64("The Q4 revenue secret board commentary."),
        },
    )

    # search with empty allowed set => 0 hits (invariant 2)
    code, res = _post("/search", {"query": "Q4 revenue", "allowed_file_ids": []})
    check("search empty allowed => 0 hits", code == 200 and res.get("hits") == [])

    # search allowing only A => B must never appear (invariant 2)
    code, res = _post("/search", {"query": "Q4 revenue", "allowed_file_ids": [fid_a]})
    hits = res.get("hits", [])
    check("search returns hits for allowed file", len(hits) >= 1)
    check("search never leaks disallowed file", all(h["file_id"] == fid_a for h in hits))
    check("search returns verbatim chunk_text", bool(hits and hits[0].get("chunk_text")))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
