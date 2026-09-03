"""What does a bare command give (no sub-action text)? This is the adapter's own
tuned retrieval seed (_BARE_QUERY), which is what remains if the sub-action
menu goes away. Two runs per case, to show whether the result is stable."""
import base64
import json
import sys
import time
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVES = ["/storage/drives/finance/", "/storage/drives/engineering/"]
ACTIONS = ["summary", "report", "tracking"]
REFUSALS = ("There is no data for this", "cannot be answered", "Erre nincs adat")


def looks_refused(answer):
    """The whole answer is a refusal only if the marker opens it. A grounded
    report may legitimately say 'no data' for one empty section (same fix as
    the eval harness needed)."""
    if not answer:
        return True
    return any(m in answer[:200] for m in REFUSALS)


def post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def run(drive, action):
    hdr = {
        "VVS-Drive": base64.urlsafe_b64encode(drive.encode("utf-8")).decode("ascii"),
        "VVS-User": "demo",
    }
    # Bare form: the UI sends "#action" with no explicit action field, so the
    # adapter applies its own _BARE_QUERY retrieval seed.
    res = post("/api/v1/ui/ask", {"query": "#" + action, "lang": "English"}, hdr)
    job = res["job_id"]
    for _ in range(24):
        res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
        if res.get("status") == "done":
            out = res.get("result") or res
            conf = out.get("confidence") or {}
            answer = (out.get("answer") or "").split("\n---\n")[0].strip()
            cits = len(out.get("citations") or [])
            refused = looks_refused(answer)
            return ("REFUSED" if refused else "ok"), conf.get("score"), cits
        time.sleep(1)
    return "TIMEOUT", None, None


print("%-14s %-24s %s" % ("ACTION", "finance (2 runs)", "engineering (2 runs)"))
print("-" * 80)
for action in ACTIONS:
    cells = []
    for d in DRIVES:
        runs = [run(d, action) for _ in range(2)]
        cells.append(" | ".join("%s(%s,c%s)" % r for r in runs))
    print("%-14s %-24s %s" % ("#" + action, cells[0], cells[1]))
    sys.stdout.flush()
