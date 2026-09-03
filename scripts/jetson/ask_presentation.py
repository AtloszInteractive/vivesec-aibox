#!/usr/bin/env python3
"""Run a /presentation action through an adapter and print the raw deck markdown."""
import base64
import json
import sys
import time
import urllib.request

adapter = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8089"
drive = sys.argv[2] if len(sys.argv) > 2 else "engineering"
query = " ".join(sys.argv[3:]) or "fleet availability and service interventions overview"

h = base64.urlsafe_b64encode(("/storage/drives/%s/" % drive).encode()).decode().rstrip("=")
payload = {"query": "#presentation " + query,
           "params": {"audience": "executive board", "purpose": "quarterly review"}}
req = urllib.request.Request(
    adapter + "/api/v1/ui/query",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "VVS-Drive": h, "VVS-User": "lars.nygaard"},
    method="POST")
t0 = time.time()
with urllib.request.urlopen(req, timeout=600) as r:
    d = json.loads(r.read())
print("elapsed: %.0fs | mode: %s | hits: %d | confidence: %s" % (
    time.time() - t0, d.get("mode"), len(d.get("hits") or []),
    (d.get("confidence") or {}).get("score")))
print("-" * 70)
print(d.get("answer") or "")
