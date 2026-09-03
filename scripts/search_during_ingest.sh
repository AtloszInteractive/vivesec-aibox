#!/usr/bin/env bash
# Availability probe: which endpoints still answer while a large document is
# being embedded? /health does not touch the store, /stats and the search do.
set -u

KEY=${RAG_API_KEY:?set RAG_API_KEY}
URL=http://127.0.0.1:8094
BUDGET=25

pgrep -f ingest_one.py >/dev/null && echo "monster ingest: RUNNING" || echo "monster ingest: FINISHED"

probe() {
  local name="$1" method="$2" path="$3" data="${4:-}"
  local start end code
  start=$(date +%s.%N)
  if [ "$method" = "GET" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' -m "$BUDGET" -H "X-API-Key: $KEY" "$URL$path")
  else
    code=$(curl -s -o /tmp/probe_out.json -w '%{http_code}' -m "$BUDGET" \
      -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d "$data" "$URL$path")
  fi
  end=$(date +%s.%N)
  printf '%-22s http=%-4s %6.2f s%s\n' "$name" "$code" "$(echo "$end - $start" | bc)" \
    "$([ "$code" = "000" ] && echo '   <-- BLOCKED (timed out)')"
}

probe "/health"             GET  /health
probe "/stats"              GET  /stats
probe "/rag/search_context" POST /rag/search_context \
  '{"corpus_id":"drive_hr","question":"vacation policy","top_k":5}'
