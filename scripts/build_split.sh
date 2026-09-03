#!/usr/bin/env bash
# Builds the query-splitting image and runs it with a chosen split mode and
# relevance floor, so the three modes can be measured against each other on the
# production floor. Reuses the existing pagefix2 index.
#
#   usage: build_split.sh [off|clause|conjunction] [floor] [--build]
set -eu

MODE=${1:-clause}
FLOOR=${2:-0.45}
BUILD=${3:-}

TAG=vivesec-rag:split
NAME=vivesec-rag-pagefix2
PORT=8095
DATA=/home/aibox/rag-pagefix2
KEY=${RAG_API_KEY:?set RAG_API_KEY}

if [ "$BUILD" = "--build" ]; then
  cd /home/aibox/rag-build
  docker build -f rag_service/Dockerfile -t "$TAG" . | tail -3
fi

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
  -e RAG_QUERY_SPLIT="$MODE" \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  "$TAG" >/dev/null

sleep 5
echo "mode=$MODE floor=$FLOOR"
curl -fsS -m 20 "http://127.0.0.1:$PORT/health"
echo
if [ "$BUILD" = "--build" ]; then
  docker exec -i "$NAME" python /app/rag_service/query_split_test.py 2>&1 | tail -3
fi
