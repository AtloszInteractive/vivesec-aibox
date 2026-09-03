#!/usr/bin/env python3
"""Wall-clock cost of one #analyze call on a named file.

The analyze path is the most prefill-heavy thing the box does, and the UI gives
up after MAX_POLLS 12 x 25 s = 300 s, so this number decides whether a model is
usable for the feature at all.

    python3 123_analyze_timing.py <port> <drive> <filename-substring>
"""
import base64
import json
import sys
import time
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8088"
DRIVE = sys.argv[2] if len(sys.argv) > 2 else "/storage/drives/engineering/"
WANT = sys.argv[3] if len(sys.argv) > 3 else "Firmware"
ADAPTER = "http://127.0.0.1:" + PORT
HDRS = {"Content-Type": "application/json",
        "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode()).decode().rstrip("="),
        "VVS-User": "demo"}


def post(path, payload, timeout=1800):
    req = urllib.request.Request(ADAPTER + path, data=json.dumps(payload).encode(),
                                 headers=HDRS, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


files = post("/api/v1/ui/query", {"query": "#search files: " + WANT}).get("files") or []
target = None
for f in files:
    if WANT.lower() in (f.get("id") or f.get("path") or "").lower():
        target = f.get("id") or f.get("path")
        break
if not target:
    listing = post("/api/v1/ui/files", {}) if False else None
    print("no file matched %r; got %d files" % (WANT, len(files)))
    sys.exit(1)

print("target:", target)
t0 = time.time()
d = post("/api/v1/ui/query", {"query": "#analyze " + target.rsplit("/", 1)[-1],
                              "files": [target]})
wall = time.time() - t0
doc = d.get("document") or {}
print("elapsed: %.0f s   backend: %s" % (wall, d.get("backend")))
print("chunks: %s/%s  truncated=%s  prompt_tokens=%s" % (
    doc.get("chunks_used"), doc.get("chunks_total"),
    doc.get("truncated"), doc.get("estimated_tokens")))
print("UI ceiling is 300 s -> %s" % ("FITS" if wall < 300 else "EXCEEDS"))
