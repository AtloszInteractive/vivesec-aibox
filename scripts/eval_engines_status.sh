#!/usr/bin/env bash
# Report what the two eval-facing engines currently hold.
#   :8091 = eval API exposed to CoLearn (Tailscale)
#   :8092 = subset instance used as our compare baseline
set -u
KEY="$(cat ~/govdocs-eval/eval_api_key.txt 2>/dev/null || echo '')"
for port in 8091 8092; do
  echo "--- :${port} ---"
  curl -fsS -m 25 -H "X-API-Key: ${KEY}" "http://127.0.0.1:${port}/stats" || echo "(nem elerheto)"
  echo
done
echo "--- kotesek ---"
ss -tln | grep -E ':(8091|8092)'
echo "--- konteynerek ---"
docker ps --filter name=vivesec-rag-eval --format '{{.Names}} {{.Status}}'
