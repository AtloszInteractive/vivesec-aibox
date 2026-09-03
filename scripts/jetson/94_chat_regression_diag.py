#!/usr/bin/env python3
"""Diagnose the 2 gen_eval regressions: ask each question twice through the
adapter — once as a FRESH user (no session history -> no condense) and once as
the gen_eval user (history -> condense active) — and print the answer head +
the retrieval_query field (present only when the condenser rewrote)."""
import base64
import json
import urllib.request

ADAPTER = "http://127.0.0.1:8088"
DRIVE = "/storage/drives/engineering/"
QUESTIONS = [
    "Which customer has the most sites?",
    "What was the mean time to repair in May 2026?",
]


def drive_header(path):
    return base64.urlsafe_b64encode(path.encode()).decode()


def ask(question, user):
    payload = json.dumps({"query": question, "top_k": 8}).encode()
    req = urllib.request.Request(
        ADAPTER + "/api/v1/ui/query", data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "VVS-Drive": drive_header(DRIVE), "VVS-User": user})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())


for q in QUESTIONS:
    print("=" * 70)
    print("Q:", q)
    for user in ("diag.fresh.20260814", "lars.nygaard"):
        res = ask(q, user)
        ans = (res.get("answer") or "").split("\n---")[0].strip()
        print("--- user=%s conf=%s%% band=%s hits=%d" % (
            user, res["confidence"]["score"], res["confidence"]["band"],
            len(res.get("hits") or [])))
        if res.get("retrieval_query"):
            print("    REWRITTEN ->", res["retrieval_query"])
        print("    " + " ".join(ans.split())[:300])
