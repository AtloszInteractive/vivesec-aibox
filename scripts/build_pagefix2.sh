#!/usr/bin/env bash
# Builds the pdfminer-pagination image and runs it on a throwaway instance, so
# the change can be measured without touching the production container or the
# existing eval indexes.
set -eu

TAG=vivesec-rag:pagefix2
NAME=vivesec-rag-pagefix2
PORT=8095
DATA=/home/aibox/rag-pagefix2
KEY=${RAG_API_KEY:?set RAG_API_KEY}

cd /home/aibox/rag-build
docker build -f rag_service/Dockerfile -t "$TAG" .

docker rm -f "$NAME" 2>/dev/null || true
rm -rf "$DATA"
mkdir -p "$DATA"

# Host networking, matching the other rag containers: the service binds
# loopback only and reaches the host's Ollama at localhost.
docker run -d --name "$NAME" \
  --network host \
  -v "$DATA":/data \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=$PORT \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_pagefix2.db \
  -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.52 \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  "$TAG"

sleep 5
echo "--- health ---"
curl -fsS -m 20 "http://127.0.0.1:$PORT/health" || echo "health FAILED"
echo
echo "--- unit tests inside the image ---"
docker exec -i "$NAME" python /app/rag_service/extract_pages_test.py 2>&1 | tail -3
