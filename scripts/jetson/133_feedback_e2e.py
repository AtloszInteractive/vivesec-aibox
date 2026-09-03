"""E2E: ask the demo box a question, then rate the answer via /ui/feedback.

Runs ON the box (loopback :80). Verifies that the stored JSONL record carries
the server-side answer trace joined by the audit id.
"""
import base64
import json
import os
import urllib.request

BASE = os.environ.get("ADAPTER_BASE", "http://127.0.0.1:80")
DRIVE = os.environ.get("DRIVE", "/storage/drives/aiboxdev/")
USER = os.environ.get("VVS_USER", "feedback-probe")
FEEDBACK_DIR = os.environ.get("FEEDBACK_DIR", "/data/feedback")


def post(path, obj, headers=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


vvs = {"VVS-Drive": base64.urlsafe_b64encode(DRIVE.encode()).decode().rstrip("="),
       "VVS-User": USER}

print("1) kerdes ...")
_, q = post("/api/v1/ui/query", {"query": "Hany telephely van a flottaban?",
                                 "top_k": 5}, vvs)
aid = (q.get("confidence") or {}).get("audit_id")
print("   audit_id:", aid, "| confidence:", (q.get("confidence") or {}).get("score"),
      (q.get("confidence") or {}).get("band"), "| hits:", len(q.get("hits") or []))
print("   valasz:", (q.get("answer") or "")[:120].replace("\n", " "))

print("2) ertekeles: down + wrong-source ...")
_, fb = post("/api/v1/ui/feedback",
             {"audit_id": aid, "rating": "down", "reason": "wrong-source",
              "comment": "e2e proba"}, vvs)
print("   ->", fb)

print("3) ertekeles: up (ujra-ertekeles) ...")
_, fb2 = post("/api/v1/ui/feedback", {"audit_id": aid, "rating": "up"}, vvs)
print("   ->", fb2)

print("4) a lemezre irt JSONL ...")
recs = []
for name in sorted(os.listdir(FEEDBACK_DIR)):
    if not name.endswith(".jsonl"):
        continue
    with open(os.path.join(FEEDBACK_DIR, name), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                recs.append((name, json.loads(line)))
print("   rekordok:", len(recs))
mine = [r for _, r in recs if r.get("audit_id") == aid]
print("   ehhez a valaszhoz:", len(mine))
if mine:
    last = mine[-1]
    tr = last.get("trace") or {}
    print("   utolso rating:", last.get("rating"), "| reason:", last.get("reason"))
    print("   trace.question:", tr.get("question"))
    print("   trace.hits:", len(tr.get("hits") or []),
          "| trace.citations:", len(tr.get("citations") or []),
          "| trace.corpus_id:", tr.get("corpus_id"))
    print("   trace.confidence:", (tr.get("confidence") or {}).get("score"),
          (tr.get("confidence") or {}).get("band"))
    ok = (tr.get("question") and tr.get("hits") is not None
          and last.get("rating") == "up")
    print("EREDMENY:", "PASS" if ok else "FAIL")
else:
    print("EREDMENY: FAIL (nincs rekord)")
