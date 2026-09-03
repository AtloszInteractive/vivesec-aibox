#!/usr/bin/env python3
"""Live E2E for the conversational mode on the finance drive: a grounded
question, a pronoun follow-up (condense must rewrite), and a chat-only turn
(summarize -> answered from history, no retrieval)."""
import base64
import json
import os
import urllib.request

ADAPTER = os.environ.get("ADAPTER_URL", "http://127.0.0.1:8088")
DRIVE = os.environ.get("E2E_DRIVE", "/storage/drives/finance/")
USER = os.environ.get("E2E_USER", "chat.e2e.20260814")
TURNS = [t for t in os.environ.get("E2E_TURNS", "").split("|") if t] or [
    "What was the revenue in Q2 2026?",
    "And how did that compare to the plan?",
    "Summarize in one sentence what you have told me so far.",
]


def ask(question):
    payload = json.dumps({"query": question}).encode()
    req = urllib.request.Request(
        ADAPTER + "/api/v1/ui/query", data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode()).decode(),
                 "VVS-User": USER})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())


for q in TURNS:
    res = ask(q)
    ans = (res.get("answer") or "").split("\n---")[0].strip()
    print("=" * 70)
    print("Q:", q)
    print("backend=%s conf=%s%% band=%s hits=%d" % (
        res.get("backend"), res["confidence"]["score"],
        res["confidence"]["band"], len(res.get("hits") or [])))
    if res.get("retrieval_query"):
        print("REWRITTEN ->", res["retrieval_query"])
    print("A:", " ".join(ans.split())[:400])
