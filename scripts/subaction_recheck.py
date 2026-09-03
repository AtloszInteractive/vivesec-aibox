"""Re-run only the sub-actions that previously refused, with the reworded
prompts.

Result (2026-08-07): rewording them as proper questions made things WORSE —
3 of 5 dropped to zero citations. Function words dilute the embedding, so a
verbose prompt retrieves nothing. Keep retrieval seeds keyword-dense; the task
description belongs in adapter/llm.py TASK_INSTRUCTIONS, not in the query.
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

CASES = [
    ("/summary", "summary", "Follow-up email", "write the follow-up email to the participants of the most recent meeting"),
    ("/summary", "summary", "Risks & blockers", "which risks and blockers were raised, and what escalation was agreed?"),
    ("/tracking", "tracking", "Blockers", "which items are blocked, what is blocking them, and who has to act?"),
    ("/search", "search", "Dates & deadlines", "which deadlines and due dates are stated, and what is due on each?"),
    ("/search", "search", "Who is responsible", "which people are named in the documents, and what is each of them responsible for?"),
]

REFUSALS = ("There is no data for this", "cannot be answered", "Erre nincs adat")


def post(path, payload, headers=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(ADAPTER + path, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


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
    conf = (out or {}).get("confidence") or {}
    answer = ((out or {}).get("answer") or "").split("\n---\n")[0].strip()
    cits = len((out or {}).get("citations") or [])
    refused = any(m in answer for m in REFUSALS) or not answer
    print("%-12s %-20s %-5s %-6s cit=%-3s %s" % (
        cmd, label, conf.get("score"), conf.get("band"), cits,
        "REFUSED" if refused else "ok"))
    print("    %s" % (answer.split("\n")[0][:130] if answer else "(empty)"))
    sys.stdout.flush()
