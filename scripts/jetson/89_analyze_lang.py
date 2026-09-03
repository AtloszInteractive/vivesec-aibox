#!/usr/bin/env python3
"""Language check for #analyze: the analysis, the truncation note and the
too-large message must all speak the user's language, not the corpus's.

    python3 89_analyze_lang.py
"""
import base64
import json
import sys
import urllib.request

ADAPTER = "http://127.0.0.1:8088"
DRIVE = "/storage/drives/engineering/"
SMALL = DRIVE + "fleet/VoltStack2_Operation_and_Maintenance_Manual.pdf"
BIG = DRIVE + "test/GC3000_Firmware_v3_Test_Log_2026-06.txt"


def post(payload, timeout=1800):
    req = urllib.request.Request(
        ADAPTER + "/api/v1/ui/query", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode()).decode().rstrip("="),
                 "VVS-User": "demo"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def check(label, cond, detail=""):
    print("  %s %s %s" % ("PASS" if cond else "FAIL", label, detail))
    return bool(cond)


def main():
    ok = True

    print("Hungarian analysis of a document that fits whole:")
    res = post({"action": "analyze", "query": "VoltStack2 kezikonyv",
                "lang": "Hungarian", "files": [SMALL]})
    doc = res.get("document") or {}
    answer = res.get("answer") or ""
    print("  chunks %s/%s truncated=%s" %
          (doc.get("chunks_used"), doc.get("chunks_total"), doc.get("truncated")))
    ok &= check("whole document covered", not doc.get("truncated"))
    hu = sum(answer.count(c) for c in "áéíóöőúüű")
    ok &= check("answer is in Hungarian", hu > 20, "(%d accented chars)" % hu)
    print("  ---")
    print("\n".join(answer.splitlines()[:6]))

    print("\nHungarian note on a document that does NOT fit:")
    res = post({"action": "analyze", "query": "firmware teszt naplo",
                "lang": "Hungarian", "files": [BIG]})
    doc = res.get("document") or {}
    answer = res.get("answer") or ""
    print("  chunks %s/%s truncated=%s" %
          (doc.get("chunks_used"), doc.get("chunks_total"), doc.get("truncated")))
    ok &= check("truncation is reported", bool(doc.get("truncated")))
    ok &= check("note is Hungarian",
                "szakasz" in answer and "hossz" in answer,
                "(looked for the Hungarian note)")
    for line in answer.splitlines():
        if "szakasz" in line or "sections" in line:
            print("  note:", line.strip())

    print("\n%s" % ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
