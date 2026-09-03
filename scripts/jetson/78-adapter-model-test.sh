#!/usr/bin/env bash
# Disposable test adapter with a different LLM (production untouched).
# Usage: bash 78-adapter-model-test.sh [MODEL] [PORT]
set -e
MODEL="${1:-qwen3.6:35b}"
PORT="${2:-8089}"
NAME=vivesec-adapter-modeltest

IMAGE=$(docker inspect vivesec-adapter --format '{{.Config.Image}}')
echo "base image: $IMAGE  model: $MODEL  port: $PORT"

docker rm -f $NAME 2>/dev/null || true

# inherit env from the production adapter, override port + model + think
ENVFILE=$(mktemp)
docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -v '^ADAPTER_PORT=\|^ADAPTER_GEN_MODEL=\|^ADAPTER_TLS=\|^PATH=' > "$ENVFILE"
{
  echo "ADAPTER_PORT=$PORT"
  echo "ADAPTER_GEN_MODEL=$MODEL"
  echo "ADAPTER_THINK=off"
  echo "ADAPTER_TLS=off"
} >> "$ENVFILE"

BINDS=$(docker inspect vivesec-adapter --format '{{range .HostConfig.Binds}}-v {{.}} {{end}}')

docker run -d --name $NAME --network host --env-file "$ENVFILE" $BINDS "$IMAGE"
rm -f "$ENVFILE"

# NOTE: the ~/llm_think.py hot-patch that used to live here is gone on purpose.
# ADAPTER_THINK has been baked into the image since 2026-08-13, and copying the
# old file back in silently downgraded llm.py to its 08-11 state (no detect_lang).
sleep 3
curl -s "http://127.0.0.1:$PORT/api/v1/status" || echo "STATUS FAILED"
echo
docker logs $NAME 2>&1 | tail -5
