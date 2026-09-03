#!/usr/bin/env bash
# Start the three ViVeSec AIBox containers on the Jetson (prod hand-container
# layout, all on --network host). Idempotent: removes any existing container of
# the same name first. Run as the docker-group user (no sudo needed for docker),
# but /data dirs are pre-created by the caller (sudo mkdir).
set -euo pipefail

RAG_IMAGE="${RAG_IMAGE:-vivesec-rag}"
ADAPTER_IMAGE="${ADAPTER_IMAGE:-vivesec-adapter}"
UI_IMAGE="${UI_IMAGE:-vivesec-ui}"
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"

echo "[60] Removing any existing containers"
docker rm -f vivesec-rag vivesec-adapter vivesec-ui 2>/dev/null || true

echo "[60] Starting vivesec-rag (loopback :8090, sqlite-vec, /data/rag)"
docker run -d --name vivesec-rag --network host --restart unless-stopped \
  -e RAG_HOST=127.0.0.1 \
  -e RAG_PORT=8090 \
  -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag/index.db \
  -e OLLAMA_URL="${OLLAMA_URL}" \
  -v /data/rag:/data/rag \
  "${RAG_IMAGE}" >/dev/null

echo "[60] Starting vivesec-adapter (LAN :80 plain + :443 mTLS, generates via host Ollama qwen2.5:14b)"
# Port 80 is not a choice: after SSDP discovery the ViVeSecBox connects to
# http://<aibox-ip>/ and ignores the LOCATION port, so the v2 front must live there.
# Init runs on :80, everything after it on :443 mTLS — one process serves both,
# because two containers would overwrite each other's metadata mirror.
# PKI, generated files and sessions MUST be on the host: /data/pki holds the
# stable SSDP device_uuid (the ViVeSecBox stores our USN when binding) and the
# mTLS provisioning material, so a container recreate must not lose them.
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  -e ADAPTER_HOST=0.0.0.0 \
  -e ADAPTER_PORT=80 \
  -e ADAPTER_TLS=on \
  -e ADAPTER_TLS_PORT=443 \
  -e RAG_URL=http://127.0.0.1:8090 \
  -e OLLAMA_URL="${OLLAMA_URL}" \
  -e ADAPTER_GEN_MODEL=qwen2.5:14b \
  -e ADAPTER_GENERATE=auto \
  -e ADAPTER_META_PATH=/data/adapter/meta.json \
  -e ADAPTER_PKI_DIR=/data/pki \
  -e ADAPTER_FILES_DIR=/data/generated \
  -e ADAPTER_SESSION_DIR=/data/sessions \
  -e ADAPTER_JOBS_DIR=/data/jobs \
  -v /data/adapter:/data/adapter \
  -v /data/pki:/data/pki \
  -v /data/generated:/data/generated \
  -v /data/sessions:/data/sessions \
  -v /data/jobs:/data/jobs \
  "${ADAPTER_IMAGE}" >/dev/null

echo "[60] Starting vivesec-ui (LAN :8080)"
docker run -d --name vivesec-ui --network host --restart unless-stopped \
  -e PORT=8080 \
  -e ADAPTER_URL=http://127.0.0.1:80 \
  -e ADAPTER_DEMO_DRIVE="${ADAPTER_DEMO_DRIVE:-/storage/drives/aiboxdev/}" \
  "${UI_IMAGE}" >/dev/null

sleep 4
echo "[60] ---- running containers ----"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
echo "[60] ---- health probes ----"
echo -n "rag /health: ";     curl -s -m 5 http://127.0.0.1:8090/health | head -c 200; echo
echo -n "adapter status: ";  curl -s -m 5 -X POST http://127.0.0.1:80/api/v1/status -d '{}' | head -c 200; echo
echo -n "ui HTTP code: ";    curl -s -m 8 -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
echo "[60] done."
