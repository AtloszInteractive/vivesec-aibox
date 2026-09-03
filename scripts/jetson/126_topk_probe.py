#!/usr/bin/env python3
"""Does the optional top_k field change the retrieval result?

Written after ask_one.py (which sends top_k: 8) reported zero hits on a question
that the same adapter answers correctly without the field.
"""
import base64
import json
import urllib.request

H = base64.urlsafe_b64encode(b"/storage/drives/engineering/").decode().rstrip("=")
Q = "What was the fleet availability in April 2026?"

for payload in ({"query": Q},
                {"query": Q, "top_k": 8},
                {"query": Q, "top_k": 4}):
    req = urllib.request.Request(
        "http://127.0.0.1:8088/api/v1/ui/query",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "VVS-Drive": H,
                 "VVS-User": "probe-topk"},
        method="POST")
    with urllib.request.urlopen(req, timeout=420) as r:
        d = json.loads(r.read())
    print("%-28s hits=%d  %s" % (
        json.dumps(payload.get("top_k")), len(d.get("hits") or []),
        (d.get("answer") or "")[:90].replace("\n", " ")))
