#!/usr/bin/env bash
# Deploys the skip-reason change to production and re-ingests so the reason is
# populated for documents that were already stored empty.
#
# The previous index file is left untouched under its own name, so rolling back
# is a matter of pointing RAG_INDEX_PATH back at it.
set -eu

NAME=vivesec-rag
PORT=8090
TAG=vivesec-rag:latest
INDEX=/data/rag_index_v3.db
PREV=/data/rag_index_v2.db
KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" | sed -n 's/^RAG_API_KEY=//p')

echo "=== 1. build ==="
cd /home/aibox/rag-build
docker build -f rag_service/Dockerfile -t "$TAG" . | tail -2

echo
echo "=== 2. unit tests inside the image ==="
for t in skipped_test query_split_test extract_pages_test; do
  printf '  %-22s ' "$t"
  docker run --rm --entrypoint python "$TAG" "/app/rag_service/$t.py" 2>&1 | tail -1
done

echo
echo "=== 3. recreate the container on a fresh index file ==="
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --network host --restart unless-stopped \
  -v /data/rag:/data \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=$PORT \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH="$INDEX" \
  -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.45 \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  "$TAG" >/dev/null
sleep 6
echo "  health: $(curl -fsS -m 20 http://127.0.0.1:$PORT/health)"
echo "  elozo index megmaradt: $(docker exec $NAME sh -lc "ls -lh $PREV | awk '{print \$5, \$9}'")"
