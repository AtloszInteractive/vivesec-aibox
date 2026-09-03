#!/usr/bin/env python3
"""Ask one question through an adapter and print the answer + retrieved sources."""
import base64
import json
import sys
import urllib.request

adapter = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8089"
question = sys.argv[2]
drive = sys.argv[3] if len(sys.argv) > 3 else "engineering"

h = base64.urlsafe_b64encode(("/storage/drives/%s/" % drive).encode()).decode().rstrip("=")
req = urllib.request.Request(
    adapter + "/api/v1/ui/query",
    data=json.dumps({"query": question, "top_k": 8}).encode(),
    headers={"Content-Type": "application/json", "VVS-Drive": h, "VVS-User": "lars.nygaard"},
    method="POST")
with urllib.request.urlopen(req, timeout=420) as r:
    d = json.loads(r.read())
print("ANSWER:", (d.get("answer") or "")[:500])
print("mode:", d.get("mode"), "| confidence:", (d.get("confidence") or {}).get("score"))
for i, hit in enumerate(d.get("hits") or [], 1):
    print("  [#%d] %s p%s score=%.3f" % (i, hit.get("source_path"), hit.get("page_number"), hit.get("score") or 0))
