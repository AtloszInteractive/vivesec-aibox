#!/usr/bin/env bash
# Compares the OLD (single-page) and NEW (form-feed paginated) index on the same
# 240-doc corpus: how much did per-page chunking inflate the chunk count, and
# which documents inflated the most.
set -u

for c in vivesec-rag-eval-sub vivesec-rag-pagefix; do
  echo "=== $c"
  docker inspect -f '{{range .Mounts}}{{.Source}} => {{.Destination}}{{"\n"}}{{end}}' "$c" 2>/dev/null
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" 2>/dev/null | grep -Ei 'rag_|index|data|chunk|min_score' || true
done
