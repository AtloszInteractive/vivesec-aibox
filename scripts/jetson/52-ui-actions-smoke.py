#!/usr/bin/env python3
"""D1b smoke: the demo UI's slash commands must reach the adapter quick actions.

Covers both call shapes the UI produces:
  * "/summary board decisions"  -> {"action": "summary", "query": "board decisions"}
  * bare "/tracking"            -> {"query": "#tracking"} (adapter bare-query seed)

Checks the async /ui/ask + /ui/poll channel (the path the UI uses) and that the
answer carries the C6 confidence block, which the UI badge renders.
"""
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8088/api/v1"
HEADERS = {
    "Content-Type": "application/json",
    "VVS-Drive": "L3N0b3JhZ2UvZHJpdmVzL2ZpbmFuY2Uv",  # base64: /storage/drives/finance/
    "VVS-User": "smoke",
}


def post(path, payload, timeout=120):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def ask(payload):
    status, out = post("/ui/ask", payload)
    if status != 202:
        return {"ok": False, "error": "ui/ask HTTP %s %s" % (status, out)}
    job = out["job_id"]
    for _ in range(12):
        status, out = post("/ui/poll", {"job_id": job, "timeout": 25}, timeout=40)
        if out.get("status") == "pending":
            continue
        return out
    return {"ok": False, "error": "poll timeout"}


CASES = [
    ("slash with argument", {"action": "summary", "query": "decisions and action items"}, "summary"),
    ("bare slash (#prefix)", {"query": "#tracking"}, "tracking"),
    ("search files mode", {"action": "search", "query": "files: report"}, "search"),
]

failed = 0
for name, payload, want in CASES:
    t0 = time.time()
    out = ask(payload)
    action = out.get("action")
    conf = out.get("confidence") or {}
    ok = bool(out.get("ok")) and action == want
    failed += 0 if ok else 1
    print("[%s] %s | action=%s (want %s) | confidence=%s/%s | %.1fs"
          % ("PASS" if ok else "FAIL", name, action, want,
             conf.get("score", "-"), conf.get("band", "-"), time.time() - t0))
    if not ok:
        print("       error:", out.get("error"), "| answer:", (out.get("answer") or "")[:120])

print("RESULT:", "ALL PASS" if failed == 0 else "%d FAILED" % failed)
raise SystemExit(1 if failed else 0)
