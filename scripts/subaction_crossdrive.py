"""Are the surviving sub-action prompts really drive-agnostic? Run the ones
that passed on /finance against a second, unrelated drive.

Result (2026-08-07): only 6 of 11 survived both drives — and "Weekly status"
flipped between ok and refused on the SAME drive across runs. That
non-determinism is why canned generic prompts were dropped: the UI text is
also the retrieval query, so a prompt without domain content words sits right
at the relevance floor.
"""
import base64
import json
import sys
import time
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVES = ["/storage/drives/finance/", "/storage/drives/engineering/"]

CASES = [
    ("/summary", "summary", "Latest meeting", "the most recent meeting — key decisions and action items"),
    ("/summary", "summary", "Action items only", "action items only, with owners and deadlines"),
    ("/report", "report", "Weekly status", "weekly status across all active workstreams"),
    ("/report", "report", "Achievements & KPIs", "key achievements and the quantifiable metrics reached"),
    ("/report", "report", "Blockers & risks", "bottlenecks, blockers and risks that need attention"),
    ("/report", "report", "Next steps & deadlines", "next-step priorities and the important upcoming deadlines"),
    ("/tracking", "tracking", "Project status", "project status — health indicator, milestones, open tasks"),
    ("/tracking", "tracking", "Milestones", "milestone timeline with owners and target dates"),
    ("/tracking", "tracking", "Pending tasks", "pending tasks grouped by owner with deadlines"),
    ("/search", "search", "Key figures", "the key figures and numbers stated in the documents"),
    ("/search", "search", "Obligations & terms", "the obligations, terms and conditions stated in the documents"),
]

REFUSALS = ("There is no data for this", "cannot be answered", "Erre nincs adat")


def post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def run(drive, action, prompt):
    hdr = {
        "VVS-Drive": base64.urlsafe_b64encode(drive.encode("utf-8")).decode("ascii"),
        "VVS-User": "demo",
    }
    res = post("/api/v1/ui/ask",
               {"query": prompt, "lang": "English", "action": action}, hdr)
    job = res["job_id"]
    for _ in range(24):
        res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
        if res.get("status") == "done":
            out = res.get("result") or res
            conf = out.get("confidence") or {}
            answer = (out.get("answer") or "").split("\n---\n")[0].strip()
            cits = len(out.get("citations") or [])
            refused = any(m in answer for m in REFUSALS) or not answer
            return ("REFUSED" if refused else "ok"), conf.get("score"), cits
        time.sleep(1)
    return "TIMEOUT", None, None


print("%-12s %-24s %-22s %s" % ("CMD", "SUB-ACTION", "finance", "engineering"))
print("-" * 84)
keep = []
for cmd, action, label, prompt in CASES:
    cells = []
    for d in DRIVES:
        v, score, cits = run(d, action, prompt)
        cells.append("%s (%s, cit=%s)" % (v, score, cits))
    ok = all(c.startswith("ok") for c in cells)
    if ok:
        keep.append((cmd, label))
    print("%-12s %-24s %-22s %s%s" % (cmd, label, cells[0], cells[1], "" if ok else "   <-- DROP"))
    sys.stdout.flush()

print("-" * 84)
print("survives both drives: %d / %d" % (len(keep), len(CASES)))
for c, l in keep:
    print("  %s  %s" % (c, l))
