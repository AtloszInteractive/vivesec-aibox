#!/usr/bin/env bash
# Builds the query-repair image and runs it on a throwaway instance with the
# PRODUCTION configuration (floor 0.45) over a copy of the production index, so
# the measurement is of what we would actually ship. Production is not touched.
set -eu

TAG=vivesec-rag:reaccent
NAME=vivesec-rag-reaccent
PORT=8098
SRC=/data/rag_index_v3.db
COPY=/data/rag_index_reaccent.db
KEY=${RAG_API_KEY:?set RAG_API_KEY}

cd /home/aibox/rag-build
docker build -f rag_service/Dockerfile -t "$TAG" .

echo "--- unit tests inside the image ---"
docker run --rm --entrypoint python "$TAG" /app/rag_service/reaccent_test.py 2>&1 | tail -3

docker rm -f "$NAME" 2>/dev/null || true
docker exec -i vivesec-rag python - "$SRC" "$COPY" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
d = sqlite3.connect(dst)
s.backup(d)
d.close(); s.close()
print("index copied")
PY

docker run -d --name "$NAME" \
  --network host \
  -v /data/rag:/data \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=$PORT \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH="$COPY" \
  -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.45 \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  "$TAG" >/dev/null

sleep 6
echo "--- health ---"
curl -fsS -m 20 "http://127.0.0.1:$PORT/health"; echo
curl -fsS -m 20 "http://127.0.0.1:$PORT/stats"; echo
