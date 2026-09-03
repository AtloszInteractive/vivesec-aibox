#!/usr/bin/env bash
# Recreates the pagefix2 evaluation instance with a given relevance floor, so
# the measurement matches what production actually runs. The index lives on the
# bind mount and survives the recreate.
set -eu

FLOOR=${1:-0.45}
NAME=vivesec-rag-pagefix2
PORT=8095
DATA=/home/aibox/rag-pagefix2
KEY=${RAG_API_KEY:?set RAG_API_KEY}

docker rm -f "$NAME" >/dev/null 2>&1 || true

docker run -d --name "$NAME" \
  --network host \
  -v "$DATA":/data \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=$PORT \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_pagefix2.db \
  -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE="$FLOOR" \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  vivesec-rag:pagefix2 >/dev/null

sleep 5
curl -fsS -m 20 "http://127.0.0.1:$PORT/health"
echo
curl -fsS -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:$PORT/stats"
