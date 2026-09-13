#!/usr/bin/env bash
set -euo pipefail

secret_file=/data/app/rag-api-key
ollama_url=http://127.0.0.1:11434

[[ $(findmnt -nro SOURCE /data) == /dev/nvme0n1p1 ]] || {
  echo "Expected /data on /dev/nvme0n1p1." >&2
  exit 1
}
for image in vivesec-rag:latest vivesec-adapter:latest vivesec-ui:latest; do
  docker image inspect "$image" >/dev/null
done
for model in bge-m3:latest qwen3.6:35b; do
  ollama list | awk 'NR > 1 {print $1}' | grep -Fxq "$model" || {
    echo "Missing Ollama model: $model" >&2
    exit 1
  }
done
curl -fsS "$ollama_url/api/version" >/dev/null

if [[ ! -s $secret_file ]]; then
  umask 077
  openssl rand -hex 32 > "$secret_file"
fi
chmod 0600 "$secret_file"
rag_api_key="$(cat "$secret_file")"
[[ ${#rag_api_key} -eq 64 ]] || { echo "Invalid RAG API key file." >&2; exit 1; }

docker rm -f vivesec-ui vivesec-adapter vivesec-rag >/dev/null 2>&1 || true

docker run -d --name vivesec-rag --network host --restart unless-stopped \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=8090 \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag/index.db \
  -e RAG_DEFAULT_TENANT=default \
  -e RAG_API_KEY="$rag_api_key" \
  -e OLLAMA_URL="$ollama_url" \
  -e VIVESEC_BACKEND=ollama \
  -e VIVESEC_EMBED_MODEL=bge-m3 \
  -v /data/rag:/data/rag \
  vivesec-rag:latest >/dev/null

docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  -e ADAPTER_HOST=0.0.0.0 \
  -e ADAPTER_PORT=80 \
  -e ADAPTER_TLS=on \
  -e ADAPTER_TLS_PORT=443 \
  -e ADAPTER_TENANT_ID=default \
  -e ADAPTER_DRIVE_PREFIX=/storage/drives \
  -e ADAPTER_META_PATH=/data/adapter/meta.json \
  -e ADAPTER_PKI_DIR=/data/pki \
  -e ADAPTER_FILES_DIR=/data/generated \
  -e ADAPTER_SESSION_DIR=/data/sessions \
  -e ADAPTER_JOBS_DIR=/data/jobs \
  -e ADAPTER_FEEDBACK_DIR=/data/feedback \
  -e ADAPTER_STORAGE_MODE=off \
  -e ADAPTER_DISCOVERY=auto \
  -e ADAPTER_CHAT_POLICY=locked_hybrid \
  -e ADAPTER_GEN_MODEL=qwen3.6:35b \
  -e ADAPTER_GENERATE=auto \
  -e ADAPTER_THINK=off \
  -e ADAPTER_NUM_PREDICT=2048 \
  -e ADAPTER_NUM_CTX=65536 \
  -e ADAPTER_ANALYZE_MAX_CONTEXT_TOKENS=60000 \
  -e ADAPTER_ANALYZE_MAX_CHARS=100000 \
  -e ADAPTER_ANALYZE_NUM_CTX=65536 \
  -e RAG_URL=http://127.0.0.1:8090 \
  -e RAG_API_KEY="$rag_api_key" \
  -e OLLAMA_URL="$ollama_url" \
  -v /data/adapter:/data/adapter \
  -v /data/pki:/data/pki \
  -v /data/generated:/data/generated \
  -v /data/sessions:/data/sessions \
  -v /data/jobs:/data/jobs \
  -v /data/feedback:/data/feedback \
  vivesec-adapter:latest >/dev/null

docker run -d --name vivesec-ui --network host --restart unless-stopped \
  -e PORT=8080 \
  -e HOST=0.0.0.0 \
  -e NITRO_HOST=0.0.0.0 \
  -e ADAPTER_URL=http://127.0.0.1:80 \
  -e ADAPTER_DEMO_DRIVE=/storage/drives/aiboxdev/ \
  -e ADAPTER_DEMO_USER=demo \
  vivesec-ui:latest >/dev/null

for url in http://127.0.0.1:8090/health http://127.0.0.1:80/api/v1/status http://127.0.0.1:8080/; do
  curl -fsS --retry 20 --retry-connrefused --retry-delay 1 --max-time 10 "$url" >/dev/null
done

unauthorized="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8090/rag/search_context)"
authorized="$(curl -sS -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' -H "X-API-Key: $rag_api_key" \
  -d '{}' http://127.0.0.1:8090/rag/search_context)"
[[ $unauthorized == 401 && $authorized == 400 ]] || {
  echo "Unexpected RAG auth responses: unauthorized=$unauthorized authorized=$authorized" >&2
  exit 1
}

echo "AIBOX_STACK_READY ui=http://$(hostname -I | awk '{print $1}'):8080 rag_auth=ok"