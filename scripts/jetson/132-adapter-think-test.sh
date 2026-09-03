#!/usr/bin/env bash
# Disposable adapter with THINKING ON, to measure what reasoning buys us.
#
#   bash 132-adapter-think-test.sh [PORT] [NUM_PREDICT] [MODEL]
#
# Two things this has to get right:
#  * thinking spends num_predict BEFORE the answer, so the budget must be big;
#  * the baked image predates the ThinkingBudgetExhausted guard, so the current
#    llm.py has to go in -- and it must be verifiably the current one, not a
#    leftover from an earlier experiment (that mistake cost us a run on 08-24).
set -e
PORT="${1:-8089}"
PREDICT="${2:-4096}"
MODEL="${3:-qwen3.6:35b}"
NAME=vivesec-adapter-thinktest
SRC=~/llm_current.py

[ -f "$SRC" ] || { echo "missing $SRC -- scp adapter/llm.py there first"; exit 1; }
grep -q ThinkingBudgetExhausted "$SRC" || { echo "$SRC has no thinking guard"; exit 1; }
echo "patch source: $(stat -c '%y  %s bytes' "$SRC")"
echo "sha256: $(sha256sum "$SRC" | cut -c1-16)"

IMAGE=$(docker inspect vivesec-adapter --format '{{.Config.Image}}')
echo "base image: $IMAGE  model: $MODEL  port: $PORT  num_predict: $PREDICT"
docker rm -f $NAME 2>/dev/null || true

ENVFILE=$(mktemp)
docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -v '^ADAPTER_PORT=\|^ADAPTER_GEN_MODEL=\|^ADAPTER_TLS=\|^ADAPTER_THINK=\|^ADAPTER_NUM_PREDICT=\|^PATH=' > "$ENVFILE"
{
  echo "ADAPTER_PORT=$PORT"
  echo "ADAPTER_GEN_MODEL=$MODEL"
  echo "ADAPTER_THINK=on"
  echo "ADAPTER_NUM_PREDICT=$PREDICT"
  echo "ADAPTER_TLS=off"
} >> "$ENVFILE"
BINDS=$(docker inspect vivesec-adapter --format '{{range .HostConfig.Binds}}-v {{.}} {{end}}')

docker run -d --name $NAME --network host --env-file "$ENVFILE" $BINDS "$IMAGE"
rm -f "$ENVFILE"

docker cp "$SRC" $NAME:/app/adapter/llm.py
docker restart $NAME >/dev/null
sleep 4

echo "--- guard present in the container? ---"
docker exec $NAME grep -c ThinkingBudgetExhausted /app/adapter/llm.py
echo "--- effective env ---"
docker exec $NAME sh -c 'echo THINK=$ADAPTER_THINK NUM_PREDICT=$ADAPTER_NUM_PREDICT MODEL=$ADAPTER_GEN_MODEL'
echo "--- status ---"
curl -s "http://127.0.0.1:$PORT/api/v1/status" | head -c 120 || echo "STATUS FAILED"
echo
