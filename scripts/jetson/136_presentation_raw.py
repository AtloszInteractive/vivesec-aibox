#!/usr/bin/env python3
"""Dump the full envelope of a /presentation request.

An empty answer that still carries a high confidence score is the failure mode
worth catching here, so print the backend label and the raw answer length.
    python3 136_presentation_raw.py <port> <drive>
"""
import base64
import json
import sys
import time
import urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else "8089"
drive = sys.argv[2] if len(sys.argv) > 2 else "engineering"
h = base64.urlsafe_b64encode(("/storage/drives/%s/" % drive).encode()).decode().rstrip("=")
payload = {"query": "#presentation fleet availability and service interventions overview",
           "params": {"audience": "executive board", "purpose": "quarterly review"}}
req = urllib.request.Request(
    "http://127.0.0.1:%s/api/v1/ui/query" % port,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "VVS-Drive": h,
             "VVS-User": "deckprobe"},
    method="POST")
t0 = time.time()
with urllib.request.urlopen(req, timeout=900) as r:
    d = json.loads(r.read())
print("elapsed      %.0f s" % (time.time() - t0))
print("backend      %r" % d.get("backend"))
print("mode         %r" % d.get("mode"))
print("hits         %d" % len(d.get("hits") or []))
print("confidence   %s" % json.dumps((d.get("confidence") or {}).get("score")))
print("answer len   %d" % len(d.get("answer") or ""))
print("answer head  %r" % (d.get("answer") or "")[:300])
