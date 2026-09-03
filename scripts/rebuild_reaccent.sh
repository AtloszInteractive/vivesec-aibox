#!/usr/bin/env bash
# Rebuild the query-repair image from the current sources and recreate the two
# measurement instances with the settings they already had. Production
# (vivesec-rag, :8090) is not touched -- it runs vivesec-rag:latest.
set -eu

TAG=vivesec-rag:reaccent
KEY=${RAG_API_KEY:?set RAG_API_KEY}

cd /home/aibox/rag-build
cp ~/src/*.py rag_service/
docker build -q -f rag_service/Dockerfile -t "$TAG" . >/dev/null
echo "image: $(docker images --format '{{.ID}}' "$TAG")"

echo "--- unit tests inside the image ---"
docker run --rm "$TAG" python /app/rag_service/reaccent_test.py 2>&1 | tail -3
docker run --rm "$TAG" python /app/rag_service/query_split_test.py 2>&1 | tail -3

docker rm -f vivesec-rag-reaccent vivesec-rag-reaccent-eval >/dev/null 2>&1 || true

docker run -d --name vivesec-rag-reaccent --network host \
  -v /data/rag:/data \
  -e RAG_HOST=127.0.0.1 -e RAG_PORT=8098 -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_reaccent.db -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.45 -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 -e VIVESEC_BACKEND=auto "$TAG" >/dev/null

docker run -d --name vivesec-rag-reaccent-eval --network host \
  -v /home/aibox/rag-reaccent-eval:/data \
  -e RAG_HOST=0.0.0.0 -e RAG_PORT=8099 -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_eval.db -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.45 -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 -e VIVESEC_BACKEND=auto "$TAG" >/dev/null

sleep 6
for p in 8098 8099; do
  printf ':%s health  ' "$p"; curl -fsS -m 20 "http://127.0.0.1:$p/health"; echo
  printf ':%s stats   ' "$p"; curl -fsS -m 20 "http://127.0.0.1:$p/stats"; echo
done
