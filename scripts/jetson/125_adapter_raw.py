#!/usr/bin/env python3
"""Dump the full adapter answer envelope for one question.

Used to tell apart 'retrieval found nothing' from 'the adapter never asked'.
    python3 125_adapter_raw.py <port> <drive-name> <question>
"""
import base64
import json
import sys
import urllib.request

port = sys.argv[1]
drive = sys.argv[2]
question = " ".join(sys.argv[3:])
h = base64.urlsafe_b64encode(("/storage/drives/%s/" % drive).encode()).decode().rstrip("=")
req = urllib.request.Request(
    "http://127.0.0.1:%s/api/v1/ui/query" % port,
    data=json.dumps({"query": question}).encode(),
    headers={"Content-Type": "application/json", "VVS-Drive": h, "VVS-User": "rawprobe"},
    method="POST")
with urllib.request.urlopen(req, timeout=420) as r:
    d = json.loads(r.read())
for k in sorted(d):
    v = d[k]
    if k in ("answer", "hits", "contexts"):
        continue
    print("%-18s %s" % (k, json.dumps(v)[:200]))
print("hits:", len(d.get("hits") or []))
print("answer:", (d.get("answer") or "")[:200].replace("\n", " "))
