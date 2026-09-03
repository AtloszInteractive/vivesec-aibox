#!/usr/bin/env bash
# The query-repair build on the 240-document evaluation index, at the shipped
# floor (0.45), so the change can be compared against runs_pagefix2_th045.
set -eu

NAME=vivesec-rag-reaccent-eval
PORT=8099
DATA=/home/aibox/rag-reaccent-eval
SRC=/home/aibox/rag-pagefix2/rag_index_pagefix2.db
KEY=${RAG_API_KEY:?set RAG_API_KEY}

docker rm -f "$NAME" 2>/dev/null || true
mkdir -p "$DATA"
python3 - "$SRC" "$DATA/rag_index_eval.db" <<'PY'
import sqlite3, sys
s = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
d = sqlite3.connect(sys.argv[2])
s.backup(d)
d.close(); s.close()
print("index copied")
PY

docker run -d --name "$NAME" \
  --network host \
  -v "$DATA":/data \
  -e RAG_HOST=0.0.0.0 \
  -e RAG_PORT=$PORT \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_eval.db \
  -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.45 \
  -e RAG_DEFAULT_TENANT=default \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  vivesec-rag:reaccent >/dev/null

sleep 6
curl -fsS -m 20 "http://127.0.0.1:$PORT/health"; echo
curl -fsS -m 20 "http://127.0.0.1:$PORT/stats"; echo
