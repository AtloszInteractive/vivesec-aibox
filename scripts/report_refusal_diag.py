"""Why does a bare #report refuse on the engineering drive even though it
retrieved 10 contexts? Print the confidence components and the raw answer."""
import base64
import json
import time
import urllib.request

ADAPTER = "http://100.88.253.38:8088"


def post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


for drive, action in [("/storage/drives/engineering/", "report"),
                      ("/storage/drives/finance/", "report")]:
    hdr = {
        "VVS-Drive": base64.urlsafe_b64encode(drive.encode("utf-8")).decode("ascii"),
        "VVS-User": "demo",
    }
    res = post("/api/v1/ui/ask", {"query": "#" + action, "lang": "English"}, hdr)
    job = res["job_id"]
    out = None
    for _ in range(24):
        res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
        if res.get("status") == "done":
            out = res.get("result") or res
            break
        time.sleep(1)
    conf = out.get("confidence") or {}
    print("=" * 78)
    print(drive, "#" + action)
    print("  score:", conf.get("score"), conf.get("band"),
          "| backend:", out.get("backend"))
    print("  degraded:", conf.get("degraded"))
    for k, v in (conf.get("components") or {}).items():
        print("    %-22s %s/%s %s" % (k, v.get("points"), v.get("max"),
                                      v.get("ungrounded") or ""))
    print("  answer:", (out.get("answer") or "").split("\n---\n")[0][:300])
