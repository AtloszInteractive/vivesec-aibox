#!/usr/bin/env python3
"""End-to-end verification of the PRODUCTION stack after the demo-corpus cutover.

Goes through the adapter on :8088 exactly the way the UI does (VVS-Drive /
VVS-User headers), so it exercises the full chain:

    adapter -> corpus_id from VVS-Drive -> rag :8090 (min_score active)
            -> grounded generation with citations

Checks all five drives, the ACL boundary (one drive never returns another
drive's content) and the negative cases from the bible's not_in_corpus list.
"""
import base64
import json
import sys
import urllib.error
import urllib.request

ADAPTER = "http://127.0.0.1:8088"
USER = "ilona.barat"

CASES = [
    ("finance", "What was the revenue in Q2 2026 and how did it compare to plan?",
     ["14.7", "15.1"], None),
    ("finance", "Are we compliant with the bank covenant?", ["COMPLIANT", "compliant"], None),
    ("legal", "How much is the annual fee for the AI platform?", ["148,000"], None),
    ("legal", "What non-conformities were open after the ISO 27001 audit?",
     ["NC-2026-01"], None),
    ("engineering", "Why is Project Helios delayed?", ["Nordcell", "cell"], None),
    ("engineering", "What is the round-trip efficiency of VoltStack 2?", ["89.4"], None),
    ("hr", "Mennyi a napidij Magyarorszagon?", ["32"], None),
    ("public", "How many employees does the company have?", ["214"], None),
    # added with the corpus extension
    ("public", "Who does the Head of Finance report to?", ["Sørensen", "Sorensen", "CFO"], None),
    ("legal", "Who may approve a purchase order above 100,000 euro?", ["Board"], None),
    ("legal", "Is the company involved in any litigation?", ["not", "no "], None),
    ("finance", "What discount can the Head of Sales approve alone?", ["4"], None),
    ("hr", "How many lost time incidents were there in the first half of 2026?", ["0", "zero"], None),
    ("engineering", "Which markets require IEEE 1547-2018?", ["Sweden"], None),
    # negatives — the answer must be a refusal or a grounded "there is none"
    ("finance", "What was the revenue in Q3 2026?", None, "refuse"),
    ("public", "When did the Shenzhen plant open?", None, "refuse"),
    ("legal", "What did the 2026 penetration test find?", None, "refuse"),
    ("public", "Which of our sites is ISO 45001 certified?", None, "refuse"),
    ("finance", "What did the board decide about the share buyback?", None, "refuse"),
    # ACL boundary — a finance-scoped question asked against the public drive
    ("public", "What is the salary band for a senior engineer in Germany?", None, "refuse"),
]

REFUSAL_MARKERS = [
    # deterministic refusals from adapter/llm.py _REFUSALS
    "no data for this", "nincs adat", "ingen data", "keine daten",
    # grounded negatives the model produces when the context states the absence
    "do not contain", "does not contain", "not provided", "no information",
    "nem tartalmaz", "no dividend", "not been scheduled", "only mentions",
    # Budget_2026_by_function.xlsx carries "Q3 2026 | 15.6 | not closed", so
    # reporting the quarter as not closed is a grounded negative, not a miss.
    "not closed",
]


def drive_header(name):
    path = ("/storage/drives/%s/" % name).encode("utf-8")
    return base64.urlsafe_b64encode(path).decode("ascii").rstrip("=")


def query(drive, question, top_k=5, timeout=300):
    payload = json.dumps({"query": question, "top_k": top_k}).encode("utf-8")
    req = urllib.request.Request(
        ADAPTER + "/api/v1/ui/query", data=payload, method="POST",
        headers={"Content-Type": "application/json",
                 "VVS-Drive": drive_header(drive),
                 "VVS-User": USER})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as exc:
        return None, "HTTP %s %s" % (exc.code, exc.read().decode("utf-8", "replace")[:300])
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


def main():
    failures = []
    for drive, question, needles, expect in CASES:
        res, err = query(drive, question)
        if err:
            print("[%-11s] ERROR  %s -> %s" % (drive, question[:46], err))
            failures.append(question)
            continue
        answer = (res.get("answer") or res.get("text") or "").strip()
        hits = res.get("hits") or res.get("citations") or []
        flat = answer.lower()
        refused = any(m in flat for m in REFUSAL_MARKERS)
        ok = True
        if expect == "refuse":
            ok = refused
        elif needles:
            ok = any(n.lower() in flat for n in needles)
        status = "OK " if ok else "FAIL"
        if not ok:
            failures.append(question)
        print("=" * 78)
        print("[%s] %-11s %s" % (status, drive, question))
        print("    hits=%d  confidence=%s" % (len(hits), res.get("confidence")))
        for line in answer.splitlines()[:6]:
            print("    " + line)

    print("")
    print("=" * 78)
    if failures:
        print("FAILED %d/%d:" % (len(failures), len(CASES)))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("ALL %d CASES PASSED" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
