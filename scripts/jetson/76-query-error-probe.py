#!/usr/bin/env python3
"""Print the adapter's error body for a ui/query call (500s hide it otherwise)."""
import base64
import json
import urllib.error
import urllib.request

req = urllib.request.Request(
    "http://127.0.0.1/api/v1/ui/query",
    data=json.dumps({"query": "What is this document about?", "top_k": 3}).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "VVS-Drive": base64.urlsafe_b64encode(b"/storage/drives/aiboxdev/").decode("ascii"),
        "VVS-User": "err-probe",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=180) as r:
        print(r.status, r.read().decode("utf-8")[:400])
except urllib.error.HTTPError as e:
    print("HTTP", e.code)
    print(e.read().decode("utf-8", "replace")[:800])
