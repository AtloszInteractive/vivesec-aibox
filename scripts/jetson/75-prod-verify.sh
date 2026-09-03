#!/usr/bin/env bash
# Functional check after the production redeploy: does retrieval work, and does
# a conjunctive question now reach both topics?
set -u
KEY=$(cat /home/aibox/prod_rag_api_key.txt)
URL=http://127.0.0.1:8090

ask() {
  local label="$1" corpus="$2" q="$3"
  echo "=== $label"
  echo "  q: $q"
  local start end
  start=$(date +%s%N)
  local body
  body=$(curl -fsS -m 60 -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    -d "{\"corpus_id\":\"$corpus\",\"question\":$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$q"),\"top_k\":5}" \
    "$URL/rag/search_context") || { echo "  REQUEST FAILED"; return; }
  end=$(date +%s%N)
  echo "$body" | python3 -c "
import json,sys
r=json.load(sys.stdin)
ctx=r.get('contexts') or []
print('  %d contexts, %d ms' % (len(ctx), $(( (end-start)/1000000 ))))
for c in ctx:
    print('    %-4.3f p%-3s %s' % (c.get('score',0), c.get('page_number'), c.get('source_path')))
"
  echo
}

ask "egyszeru kerdes" legal-c9902b93 "What are the data processing obligations?"
ask "KONJUNKTIV kerdes" legal-c9902b93 "What are the data processing obligations, and what does the supply agreement say about delivery?"
ask "magyar kerdes" hr-6ba4fc3f "Mennyi a napidíj külföldi kiküldetés esetén?"

echo "=== adapter end-to-end ==="
python3 /home/aibox/51-adapter-e2e-smoke.py 2>&1 | tail -12
