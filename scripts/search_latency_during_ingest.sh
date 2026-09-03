#!/usr/bin/env bash
# Samples search latency while a full corpus ingest is running. Before the lock
# change every search waited for the document being embedded; now it should stay
# flat regardless of what the indexer is doing.
set -u

KEY=${RAG_API_KEY:?set RAG_API_KEY}
URL=${1:-http://127.0.0.1:8095}
SAMPLES=${2:-40}

pgrep -f ingest_to_rag.py >/dev/null && echo "corpus ingest: RUNNING" || echo "corpus ingest: NOT running"

times=""
fails=0
for i in $(seq 1 "$SAMPLES"); do
  start=$(date +%s%N)
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 30 \
    -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
    -d '{"corpus_id":"drive_hr","question":"vacation policy","top_k":5}' \
    "$URL/rag/search_context")
  end=$(date +%s%N)
  ms=$(( (end - start) / 1000000 ))
  [ "$code" = "200" ] || fails=$((fails + 1))
  times="$times$ms\n"
  sleep 1
done

echo "samples=$SAMPLES  non-200=$fails"
printf "$times" | sort -n | awk '
  { a[NR] = $1 }
  END {
    printf "min=%d ms  p50=%d ms  p95=%d ms  max=%d ms\n",
      a[1], a[int(NR*0.5)+1], a[int(NR*0.95)+1], a[NR]
  }'
