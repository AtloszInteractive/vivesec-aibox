#!/usr/bin/env bash
# Which rag images and containers exist, and does the running code know about
# the query spelling repair?
set -u
echo "=== images ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedSince}}' | grep vivesec-rag
echo
echo "=== containers ==="
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep vivesec-rag
echo
echo "=== reaccent.py present? ==="
for c in $(docker ps --format '{{.Names}}' | grep vivesec-rag); do
  printf '%-26s ' "$c"
  if docker exec "$c" test -f /app/rag_service/reaccent.py 2>/dev/null; then
    echo -n "van  "
    curl -fsS -m 10 "http://127.0.0.1:$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | sed -n 's/^RAG_PORT=//p')/health" 2>/dev/null | head -c 200
    echo
  else
    echo "nincs"
  fi
done
