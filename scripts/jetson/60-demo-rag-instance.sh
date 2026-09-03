#!/usr/bin/env bash
# Create (or recreate) the DEMO rag_service instance on the Jetson.
#
# Separate container + separate index so the production :8090 stays untouched
# while the new Voltara demo corpus is loaded, measured and calibrated.
#
#   port    127.0.0.1:8093  (loopback only)
#   index   ~/rag-demo-data/rag_index_demo.db  (home, so no sudo is needed;
#           the demo index is small — a few tens of megabytes)
#   key     ~/demo_rag_api_key.txt (generated once, chmod 600)
#   score   RAG_MIN_SCORE=0  -> NO threshold, so the raw score distribution can
#           be measured on this corpus before picking a production value.
set -euo pipefail

NAME=vivesec-rag-demo
IMAGE=vivesec-rag:latest
PORT=8093
DATA_DIR="$HOME/rag-demo-data"
KEY_FILE="$HOME/demo_rag_api_key.txt"

if [ ! -f "$KEY_FILE" ]; then
  python3 -c "import secrets;print(secrets.token_hex(16))" > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "generated a new API key in $KEY_FILE"
else
  echo "reusing the existing API key in $KEY_FILE"
fi
KEY=$(cat "$KEY_FILE")

mkdir -p "$DATA_DIR"

if [ "${1:-}" = "--wipe" ] && [ -f "$DATA_DIR/rag_index_demo.db" ]; then
  ts=$(date +%Y%m%d-%H%M)
  mv "$DATA_DIR/rag_index_demo.db" "$DATA_DIR/rag_index_demo.db.bak-$ts"
  echo "existing index moved aside: rag_index_demo.db.bak-$ts"
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --network host \
  -e RAG_API_KEY="$KEY" \
  -e RAG_DEFAULT_TENANT=default \
  -e RAG_MIN_SCORE=0 \
  -e OLLAMA_URL=http://localhost:11434 \
  -e VIVESEC_BACKEND=auto \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT="$PORT" \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_demo.db \
  -v "$DATA_DIR":/data \
  "$IMAGE" >/dev/null

for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then break; fi
  sleep 1
done

echo "--- health ---"
curl -s "http://127.0.0.1:$PORT/health"; echo
echo "--- auth (no key, expect 401) ---"
curl -s -o /dev/null -w '%{http_code}\n' -X POST "http://127.0.0.1:$PORT/rag/search_context" \
  -H 'Content-Type: application/json' -d '{}'
echo "--- stats ---"
curl -s -H "X-API-Key: $KEY" "http://127.0.0.1:$PORT/stats"; echo
echo
echo "container $NAME is up on 127.0.0.1:$PORT (loopback only)"
