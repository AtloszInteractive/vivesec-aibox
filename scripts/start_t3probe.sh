#!/usr/bin/env bash
# Starts a second, read-only-ish instance over a copy of the pagefix2 index with
# the relevance floor disabled, so we can see where the missing second-hop
# document actually ranks. Nothing touches the measured index.
set -eu

SRC=/home/aibox/rag-pagefix2
DATA=/home/aibox/rag-t3probe
NAME=vivesec-rag-t3probe
PORT=8096
KEY=${RAG_API_KEY:?set RAG_API_KEY}

docker rm -f "$NAME" 2>/dev/null || true
rm -rf "$DATA"
cp -a "$SRC" "$DATA"

docker run -d --name "$NAME" \
  --network host \
  -v "$DATA":/data \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=$PORT \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_pagefix2.db \
  -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.0 \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  vivesec-rag:pagefix2

sleep 5
curl -fsS -m 20 "http://127.0.0.1:$PORT/health"
echo
curl -fsS -m 30 -H "X-API-Key: $KEY" "http://127.0.0.1:$PORT/stats"
