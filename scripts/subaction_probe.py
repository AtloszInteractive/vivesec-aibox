"""Run every sub-action prompt exactly as the UI would, and report whether the
box answers it in a grounded way (confidence band + citation count).

Kept for the record: this is the measurement that removed the sub-action menu
(2026-08-07). 11 of 16 prompts answered on /finance, 5 refused. The menu is
gone from the UI, so this only runs against a hand-written CASES list now.
"""
import base64
import json
import sys
import time
import urllib.request

ADAPTER = "http://100.88.253.38:8088"
DRIVE = "/storage/drives/finance/"
HDR = {
    "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode("utf-8")).decode("ascii"),
    "VVS-User": "demo",
}

# (slash command, action sent to the adapter, sub-action label, prompt)
CASES = [
    ("/summary", "summary", "Latest meeting", "the most recent meeting — key decisions and action items"),
    ("/summary", "summary", "Action items only", "action items only, with owners and deadlines"),
    ("/summary", "summary", "Follow-up email", "as a follow-up email to the participants"),
    ("/summary", "summary", "Risks & blockers", "risks, blockers and items that need escalation"),
    ("/report", "report", "Weekly status", "weekly status across all active workstreams"),
    ("/report", "report", "Achievements & KPIs", "key achievements and the quantifiable metrics reached"),
    ("/report", "report", "Blockers & risks", "bottlenecks, blockers and risks that need attention"),
    ("/report", "report", "Next steps & deadlines", "next-step priorities and the important upcoming deadlines"),
    ("/tracking", "tracking", "Project status", "project status — health indicator, milestones, open tasks"),
    ("/tracking", "tracking", "Milestones", "milestone timeline with owners and target dates"),
    ("/tracking", "tracking", "Pending tasks", "pending tasks grouped by owner with deadlines"),
    ("/tracking", "tracking", "Blockers", "blocked items, dependencies and required escalations"),
    ("/search", "search", "Key figures", "the key figures and numbers stated in the documents"),
    ("/search", "search", "Dates & deadlines", "the important dates and deadlines stated in the documents"),
    ("/search", "search", "Who is responsible", "who is responsible for what according to the documents"),
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


print("%-12s %-24s %-6s %-6s %-4s %s" % ("CMD", "SUB-ACTION", "SCORE", "BAND", "CIT", "VERDICT"))
print("-" * 92)
bad = 0
for cmd, action, label, prompt in CASES:
    res = post("/api/v1/ui/ask",
               {"query": prompt, "lang": "English", "action": action}, HDR)
    job = res["job_id"]
    out = None
    for _ in range(24):
        res = post("/api/v1/ui/poll", {"job_id": job, "timeout": 25})
        if res.get("status") == "done":
            out = res.get("result") or res
            break
        time.sleep(1)
    if not out:
        print("%-12s %-24s %-6s %-6s %-4s %s" % (cmd, label, "-", "-", "-", "TIMEOUT"))
        bad += 1
        continue
    conf = out.get("confidence") or {}
    answer = (out.get("answer") or "").split("\n---\n")[0].strip()
    cits = len(out.get("citations") or [])
    refused = any(m in answer for m in REFUSALS) or not answer
    verdict = "REFUSED" if refused else ("thin" if cits < 2 else "ok")
    if verdict != "ok":
        bad += 1
    print("%-12s %-24s %-6s %-6s %-4s %s" % (
        cmd, label, conf.get("score"), conf.get("band"), cits, verdict))
    sys.stdout.flush()

print("-" * 92)
print("not ok: %d / %d" % (bad, len(CASES)))
