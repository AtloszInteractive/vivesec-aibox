#!/usr/bin/env bash
# A throwaway copy of the production index served with no score floor, so the
# diacritics measurement can tell "the floor cut it" apart from "nothing was
# ever retrieved". Production (vivesec-rag, :8090) is not touched: the copy is
# made through sqlite's backup API from a read-only connection.
set -eu

SRC=/data/rag_index_v3.db
COPY=/data/rag_index_diacritics_probe.db
NAME=vivesec-rag-diacritics
PORT=8097
KEY=${RAG_API_KEY:?set RAG_API_KEY}

docker rm -f "$NAME" 2>/dev/null || true

docker exec -i vivesec-rag python - "$SRC" "$COPY" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect("file:%s?mode=ro" % src, uri=True)
d = sqlite3.connect(dst)
s.backup(d)
d.close(); s.close()
print("copied")
PY

docker run -d --name "$NAME" \
  --network host \
  -v /data/rag:/data \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=$PORT \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH="$COPY" \
  -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0 \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  vivesec-rag:latest >/dev/null

sleep 6
echo "--- probe health ---"
curl -fsS -m 20 "http://127.0.0.1:$PORT/health"; echo
echo "--- probe stats ---"
curl -fsS -m 20 "http://127.0.0.1:$PORT/stats"; echo
echo "--- production untouched ---"
curl -fsS -m 20 "http://127.0.0.1:8090/health"; echo
