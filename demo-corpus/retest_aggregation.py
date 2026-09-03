#!/usr/bin/env python3
"""Re-ask the aggregation question that failed, in both languages.

Checks whether the half-year roll-up document is now retrieved and whether the
answer names the correct site for the WHOLE period rather than one month's top
row. Runs on the Jetson, through the adapter, the way the UI does.
"""
import base64
import json
import sys
import urllib.request

ADAPTER = "http://127.0.0.1:8088"
CORRECT = ("DK-010", "SE-002")          # tied leaders over the whole of 2026-H1
WRONG_MONTHLY = ("SE-003", "DE-002", "DE-008")   # top rows of single months

QUESTIONS = [
    "melyik telephelyen volt a legtöbb beavatkozás 2026 H1-ben",
    "Which site had the most corrective interventions in the first half of 2026?",
    "Melyik telephelyen volt a legtöbb javítás 2026 első félévében?",
]


def ask(question, top_k=8):
    drive = base64.urlsafe_b64encode(b"/storage/drives/engineering/").decode().rstrip("=")
    req = urllib.request.Request(
        ADAPTER + "/api/v1/ui/query",
        data=json.dumps({"query": question, "top_k": top_k}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "VVS-Drive": drive,
                 "VVS-User": "lars.nygaard"})
    with urllib.request.urlopen(req, timeout=420) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    for q in QUESTIONS:
        res = ask(q)
        body = (res.get("answer") or "").split("\n---\n")[0]
        hits = res.get("hits") or []
        sources = [h.get("path", "") for h in hits]
        rollup = [s for s in sources if "/halfyear/" in s]
        conf = res.get("confidence") or {}
        right = any(c in body for c in CORRECT)
        wrong = [w for w in WRONG_MONTHLY if w in body]
        print("=" * 96)
        print("Q: %s" % q)
        print("   half-year roll-up retrieved: %s" % (rollup or "NO"))
        print("   confidence: %s%% (%s)   hits=%d" % (conf.get("score"), conf.get("band"),
                                                      len(hits)))
        print("   names a correct leader (DK-010 / SE-002): %s" % right)
        print("   names a single-month leader instead      : %s" % (wrong or "no"))
        for line in body.splitlines()[:8]:
            print("   | " + line[:140])
        print("   sources: %s" % ", ".join(s.rsplit("/", 1)[-1] for s in sources[:6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
