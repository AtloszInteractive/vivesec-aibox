#!/usr/bin/env bash
# The run configuration of the two reaccent instances, so the rebuild can
# recreate them exactly.
set -u
for c in vivesec-rag-reaccent vivesec-rag-reaccent-eval; do
  echo "=== $c ==="
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | grep -E '^(RAG_|OLLAMA_|VIVESEC_)'
  docker inspect -f 'network={{.HostConfig.NetworkMode}}' "$c"
  docker inspect -f '{{range .Mounts}}mount={{.Source}}:{{.Destination}}{{println}}{{end}}' "$c"
  echo
done
