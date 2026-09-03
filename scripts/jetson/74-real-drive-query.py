#!/usr/bin/env python3
"""End-to-end query against the drive that the real ViVeSecBox synced."""
import base64
import json
import sys
import urllib.request

BASE = "http://127.0.0.1"
DRIVE = "/storage/drives/aiboxdev/"
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "What is this document about?"

req = urllib.request.Request(
    BASE + "/api/v1/ui/query",
    data=json.dumps({"query": QUESTION, "top_k": 3}).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
        "VVS-User": "smoke",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=180) as resp:
    out = json.loads(resp.read().decode("utf-8"))

print("ok       :", out.get("ok"))
print("corpus   :", out.get("corpus_id"))
conf = out.get("confidence") or {}
print("confidence:", conf.get("score"), conf.get("band"))
print("answer   :", (out.get("answer") or "")[:700])
for h in (out.get("hits") or [])[:3]:
    print("  hit:", h.get("path"), "score", h.get("score"))
