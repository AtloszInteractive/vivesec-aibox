#!/usr/bin/env python3
"""Reproduce the ViVeSecBox two-phase upload against the adapter and show the
exact error body of the failing content step."""
import base64
import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1"
PATH = "/storage/drives/aiboxdev/__probe__.md"
BODY = b"# Probe\n\nDecision memo probe content for the upload path.\n" * 5


def call(url, data, ctype):
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


st, out = call(BASE + "/api/v1/index/upsert/file/check",
               json.dumps({"path": PATH, "size": len(BODY),
                           "mtime": int(time.time() * 1e9),
                           "head": base64.b64encode(BODY[:256]).decode("ascii")}).encode(),
               "application/json")
print("check ->", st, out[:300])
token = (json.loads(out).get("token") if st == 200 else None)
if not token:
    print("no token, stopping")
    raise SystemExit(1)

st, out = call(BASE + "/api/v1/index/upsert/file/content/" + token,
               BODY, "application/octet-stream")
print("content ->", st, out[:600])
