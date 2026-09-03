#!/usr/bin/env python3
"""E2E smoke: adapter /ui/query -> key-protected production RAG -> generation."""
import json
import urllib.request

req = urllib.request.Request(
    "http://127.0.0.1:8088/api/v1/ui/query",
    data=json.dumps({"query": "What was Q4 revenue?", "top_k": 3}).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "VVS-Drive": "L3N0b3JhZ2UvZHJpdmVzL2ZpbmFuY2Uv",  # base64: /storage/drives/finance/
        "VVS-User": "smoke",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as resp:
    out = json.loads(resp.read().decode("utf-8"))
print("ok:", out.get("ok"))
print("answer:", (out.get("answer") or "")[:160])
hits = out.get("hits") or []
print("hits:", len(hits), "| top:", hits[0]["path"] if hits else "-")
