"""Dump the feedback JSONL on the box (last N records, human readable)."""
import json
import os
import sys

ROOT = os.environ.get("FEEDBACK_DIR", "/data/feedback")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5

recs = []
for name in sorted(os.listdir(ROOT)):
    if not name.endswith(".jsonl"):
        continue
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        for line in f:
            if line.strip():
                recs.append(json.loads(line))

print("osszes rekord: %d" % len(recs))
for e in recs[-N:]:
    t = e.get("trace") or {}
    c = t.get("confidence") or {}
    print("-" * 60)
    print("audit   : %s | rating: %s | reason: %s" %
          (e.get("audit_id"), e.get("rating"), e.get("reason")))
    print("user    : %s | drive: %s" % (e.get("user"), e.get("drive")))
    print("kerdes  : %s" % (t.get("question") or (e.get("client") or {}).get("question")))
    print("action  : %s | agent: %s | backend: %s" %
          (t.get("action"), t.get("agent"), t.get("backend")))
    print("conf    : %s %s" % (c.get("score"), c.get("band")))
    print("hits    : %d | citations: %d | corpus: %s" %
          (len(t.get("hits") or []), len(t.get("citations") or []), t.get("corpus_id")))
    for h in (t.get("hits") or [])[:3]:
        print("   #%s %s p.%s score=%s" %
              (h.get("rank"), h.get("path"), h.get("page_number"), h.get("score")))
    ans = (t.get("answer") or (e.get("client") or {}).get("answer") or "")
    print("valasz  : %s" % ans[:160].replace("\n", " "))
    if e.get("comment"):
        print("megjegyz: %s" % e["comment"])
