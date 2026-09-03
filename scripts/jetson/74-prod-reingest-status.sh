#!/usr/bin/env bash
# Progress and health of the production instance during/after the re-ingest.
set -u
KEY=$(cat /home/aibox/prod_rag_api_key.txt)

pgrep -f 'ingest.py.*8090' >/dev/null && echo "reingest: RUNNING" || echo "reingest: FINISHED"
echo "--- log tail ---"
tail -8 /home/aibox/demo-corpus/ingest_prod_v2.log
echo "--- stats ---"
curl -fsS -m 20 -H "X-API-Key: $KEY" http://127.0.0.1:8090/stats
echo
echo "--- health ---"
curl -fsS -m 20 http://127.0.0.1:8090/health
echo
