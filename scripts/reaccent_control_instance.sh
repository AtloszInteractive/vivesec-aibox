#!/usr/bin/env bash
# The control half of the A/B: the same image and the same index with the
# repair switched off, so the comparison differs in one setting only and
# production never has to be queried.
set -eu
docker rm -f vivesec-rag-reaccent-off >/dev/null 2>&1 || true
docker run -d --name vivesec-rag-reaccent-off --network host \
  -v /data/rag:/data \
  -e RAG_HOST=127.0.0.1 -e RAG_PORT=8100 -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_reaccent.db \
  -e RAG_API_KEY="${RAG_API_KEY:?set RAG_API_KEY}" \
  -e RAG_MIN_SCORE=0.45 -e RAG_DEFAULT_TENANT=default \
  -e RAG_QUERY_REACCENT=off \
  -e OLLAMA_URL=http://localhost:11434 -e VIVESEC_BACKEND=auto \
  vivesec-rag:reaccent >/dev/null
sleep 6
curl -fsS -m 20 http://127.0.0.1:8100/health; echo
