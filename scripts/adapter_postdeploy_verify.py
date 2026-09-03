"""Post-deploy verification of the ViVeSec-spec changes now on the box:
new endpoints, the changed mirror response shapes (the UI reads these), and
that a grounded query still works end to end."""
import base64
import json
import time
import urllib.error
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVE = "/storage/drives/finance/"
HDR = {
    "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
    "VVS-User": "demo",
}


def get(path):
    req = urllib.request.Request(ADAPTER + path)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status, r.read().decode("utf-8")


def post(path, payload, headers=None, raw=False):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read().decode("utf-8")
        return r.status, (body if raw else json.loads(body))


print("1) GET /api/v1/ui-version (new endpoint)")
try:
    code, body = get("/api/v1/ui-version")
    print("   -> %s %r" % (code, body[:40]))
except urllib.error.HTTPError as e:
    print("   -> HTTP %s (MISSING)" % e.code)

print("2) POST /api/v1/index/get/children — shape the UI parses")
code, body = post("/api/v1/index/get/children", {"path": DRIVE}, HDR)
kind = "bare array" if isinstance(body, list) else "envelope %s" % list(body)[:4]
n = len(body) if isinstance(body, list) else len(body.get("children") or [])
print("   -> %s | %s | %d entries" % (code, kind, n))

print("3) POST /api/v1/status")
code, body = post("/api/v1/status", {})
print("   -> %s ok=%s ui_ready=%s mirror=%s index=%s"
      % (code, body.get("ok"), body.get("ui_ready"),
         body.get("mirror"), body.get("index")))

print("4) grounded query end to end")
code, res = post("/api/v1/ui/ask",
                 {"query": "What are the standard payment terms?",
                  "lang": "English", "action": "search"}, HDR)
job = res["job_id"]
for _ in range(24):
    code, res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
    if res.get("status") == "done":
        out = res.get("result") or res
        conf = out.get("confidence") or {}
        print("   -> %s%% %s | citations=%d"
              % (conf.get("score"), conf.get("band"), len(out.get("citations") or [])))
        print("   -> %s" % (out.get("answer") or "").split("\n")[0][:120])
        break
    time.sleep(1)
