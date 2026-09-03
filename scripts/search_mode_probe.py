"""F7 search modes: what the box does for `#search` (text) vs `#search files:`."""
import base64
import json
import time
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVE = "/storage/drives/finance/"
HDR = {
    "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
    "VVS-User": "demo",
}


def post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def ask(body, label):
    print("=" * 70)
    print(label, "->", {k: v for k, v in body.items() if k != "query"},
          "query=%r" % body["query"])
    res = post("/api/v1/ui/ask", body, HDR)
    job = res["job_id"]
    for _ in range(20):
        res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
        if res.get("status") == "done":
            break
        time.sleep(1)
    out = res.get("result") or res
    print("  mode      :", out.get("mode"), "| backend:", out.get("backend"))
    print("  files[]   :", len(out.get("files") or []))
    print("  citations :", [c.get("path", "").rsplit("/", 1)[-1]
                            for c in out.get("citations") or []])
    print("  answer    :", (out.get("answer") or "").split("\n---\n")[0][:400])


ask({"query": "Nordkraft discount", "lang": "English", "action": "search"},
    "A) what the UI sends today")
ask({"query": "Nordkraft", "lang": "English", "action": "search", "mode": "files"},
    "B) filename lookup (mode=files)")
ask({"query": "files: Nordkraft", "lang": "English"},
    "C) typed 'files:' prefix, no explicit action")
