#!/usr/bin/env bash
# Ask the production RAG directly, bypassing the adapter, to tell a retrieval
# failure apart from a generation-layer refusal.
set -u
Q="${1:-What was the fleet availability in April 2026?}"
DRIVE="${2:-/storage/drives/engineering/}"
KEY=$(cat ~/prod_rag_api_key.txt 2>/dev/null || echo "")

CORPUS=$(python3 - "$DRIVE" <<'PY'
import hashlib, re, sys
root = sys.argv[1].rstrip('/')
slug = re.sub(r'[^a-z0-9]+', '-', root.rsplit('/', 1)[-1].lower()).strip('-')
print("%s-%s" % (slug, hashlib.sha1(root.encode()).hexdigest()[:8]))
PY
)
echo "corpus_id guess: $CORPUS"

python3 - "$Q" "$CORPUS" "$KEY" <<'PY'
import json, sys, urllib.request
q, corpus, key = sys.argv[1], sys.argv[2], sys.argv[3]
body = {"corpus_id": corpus, "tenant_id": "default", "question": q, "top_k": 5}
req = urllib.request.Request("http://127.0.0.1:8090/rag/search_context",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json", "X-API-Key": key})
try:
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read())
    ctx = d.get("contexts") or []
    print("contexts:", len(ctx))
    for c in ctx:
        print("  %.4f  %s p%s" % (c.get("score") or 0, c.get("source_path"), c.get("page_number")))
except Exception as e:
    print("ERROR:", e)
PY

echo "--- /stats ---"
curl -s http://127.0.0.1:8090/stats | head -c 400
