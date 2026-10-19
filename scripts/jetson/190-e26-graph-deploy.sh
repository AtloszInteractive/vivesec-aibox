#!/usr/bin/env bash
# E26 relationship analysis on a box: Neo4j + graph_service, then the adapter
# is pointed at the graph and starts its GPU broker.
#
#   bash 190-e26-graph-deploy.sh deploy <graph-src.tgz>
#   bash 190-e26-graph-deploy.sh adapter-off     # rollback of step 4 only
#
# graph-src.tgz holds graph_service/ and rag_service/{extract,access}.py +
# rag_service/requirements.txt (the graph reads documents with the RAG's own
# extractor). Run it AFTER the release that brings the E26 adapter.
#
# Safety contract (as 183/172): no drive sync may be running (the adapter
# restart cuts the ws-fs channel); the graph image is tested before it runs;
# Neo4j and the graph listen on loopback only; secrets are generated on the box
# into ~/graph-secrets (0700) and never printed; the adapter keeps its env and
# binds and only gains the graph keys.
#
# Env overrides: GRAPH_PROFILE (bv), GRAPH_ADMINS (default: the UI's
# ADAPTER_DEMO_USER), BROKER_PORT (8099), NEO4J_IMAGE (neo4j:5-community).
set -eu

MODE=${1:-}
TS=$(date +%Y%m%d-%H%M)
SECRETS=$HOME/graph-secrets
STAGE=$HOME/graph-build
GRAPH_PORT=8095
BROKER_PORT=${BROKER_PORT:-8099}
NEO4J_IMAGE=${NEO4J_IMAGE:-neo4j:5-community}
PROFILE=${GRAPH_PROFILE:-bv}
GRAPH_KEYS='^(GRAPH_URL|GRAPH_API_KEY|ADAPTER_GPU_BROKER_PORT|ADAPTER_GRAPH_ADMINS)='

adapter_env() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
    | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
    | grep -vE "$GRAPH_KEYS" | grep -v '^$' || true
}

recreate_adapter() {  # extra -e arguments follow
  local envargs=() bindargs=() e b
  while IFS= read -r e; do envargs+=( -e "$e" ); done < <(adapter_env)
  while IFS= read -r b; do [ -n "$b" ] && bindargs+=( -v "$b" ); done \
    < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)
  local image
  image=$(docker inspect -f '{{.Config.Image}}' vivesec-adapter)
  docker tag "$image" "vivesec-adapter:prev-$TS"
  docker stop vivesec-adapter >/dev/null
  docker rename vivesec-adapter "vivesec-adapter-prev-$TS"
  docker run -d --name vivesec-adapter --network host --restart unless-stopped \
    "${envargs[@]}" "$@" "${bindargs[@]}" "$image" >/dev/null
  echo "  adapter recreated from $image; previous container kept as vivesec-adapter-prev-$TS"
}

wait_url() {  # url seconds
  local i
  for i in $(seq 1 "$2"); do
    curl -fsS -m 5 "$1" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

echo "=== 0. preflight: nothing may be syncing through the adapter ==="
if pgrep -f 'drive-sync-folder\.py' >/dev/null; then
  echo "  ABORT: a drive sync is running; the adapter restart would cut its ws-fs channel."
  exit 1
fi
echo "  no drive sync running"

if [ "$MODE" = "adapter-off" ]; then
  echo "=== adapter without the graph keys ==="
  recreate_adapter
  wait_url http://127.0.0.1:8088/api/v1/status 60 && echo "  adapter status OK"
  exit 0
fi
[ "$MODE" = "deploy" ] && [ -n "${2:-}" ] || { echo "usage: $0 deploy <graph-src.tgz> | adapter-off"; exit 2; }
SRC_TGZ=$2

echo
echo "=== 1. secrets (generated once, never printed) ==="
mkdir -p "$SECRETS"; chmod 700 "$SECRETS"
umask 022
if [ ! -s "$SECRETS/neo4j_password" ]; then
  python3 -c 'import secrets; print(secrets.token_hex(24))' > "$SECRETS/neo4j_password"
  echo "  new Neo4j password"
fi
if [ ! -s "$SECRETS/graph_api_key" ]; then
  python3 -c 'import secrets; print(secrets.token_hex(24))' > "$SECRETS/graph_api_key"
  echo "  new graph API key"
fi
printf 'neo4j/%s' "$(cat "$SECRETS/neo4j_password")" > "$SECRETS/neo4j_auth"
# Readable inside the containers (their users differ); the 0700 directory
# keeps them private on the host.
chmod 644 "$SECRETS/neo4j_password" "$SECRETS/neo4j_auth" "$SECRETS/graph_api_key"
GRAPH_API_KEY=$(cat "$SECRETS/graph_api_key")

echo
echo "=== 2. Neo4j (loopback bolt only) ==="
if docker inspect vivesec-neo4j >/dev/null 2>&1; then
  echo "  vivesec-neo4j exists: $(docker inspect -f '{{.State.Status}}' vivesec-neo4j) (kept)"
  docker start vivesec-neo4j >/dev/null
else
  docker pull -q "$NEO4J_IMAGE" >/dev/null
  docker run -d --name vivesec-neo4j --restart unless-stopped \
    -p 127.0.0.1:7687:7687 \
    -v /data/neo4j/data:/data -v /data/neo4j/logs:/logs \
    -v "$SECRETS/neo4j_auth:/run/secrets/neo4j_auth:ro" \
    -e NEO4J_AUTH_FILE=/run/secrets/neo4j_auth \
    -e NEO4J_server_memory_heap_initial__size=1g \
    -e NEO4J_server_memory_heap_max__size=1g \
    -e NEO4J_server_memory_pagecache_size=512m \
    "$NEO4J_IMAGE" >/dev/null
  echo "  started $(docker inspect -f '{{.Config.Image}}' vivesec-neo4j)"
fi
for i in $(seq 1 120); do
  docker logs vivesec-neo4j 2>&1 | grep -q 'Started\.' && break
  sleep 1
done
docker logs vivesec-neo4j 2>&1 | grep -q 'Started\.' || { echo "  ABORT: Neo4j did not start"; docker logs --tail 20 vivesec-neo4j; exit 1; }
echo "  Neo4j started"

echo
echo "=== 3. graph image: build + tests inside it ==="
rm -rf "$STAGE"; mkdir -p "$STAGE"
tar -xzf "$SRC_TGZ" -C "$STAGE"
docker build -q -f "$STAGE/graph_service/Dockerfile" -t "vivesec-graph:e26-$TS" "$STAGE" >/dev/null
echo "  built vivesec-graph:e26-$TS"
set +e
tests=$(docker run --rm --network none -e VIVESEC_BACKEND=fallback --entrypoint sh \
  "vivesec-graph:e26-$TS" -c 'cd /app/graph_service && python -m unittest discover -p "*_test.py" 2>&1')
status=$?
set -e
echo "$tests" | tail -3 | sed 's/^/  /'
[ "$status" -eq 0 ] || { echo "  ABORT: graph tests failed in the image; nothing changed."; exit 1; }
docker run --rm --network none --entrypoint tesseract "vivesec-graph:e26-$TS" --list-langs 2>&1 \
  | grep -qx hun && echo "  tesseract hun OK"

echo
echo "=== 4. graph container ==="
MODEL=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter | sed -n 's/^ADAPTER_GEN_MODEL=//p')
NUM_CTX=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter | sed -n 's/^ADAPTER_NUM_CTX=//p')
if docker inspect vivesec-graph >/dev/null 2>&1; then
  docker tag "$(docker inspect -f '{{.Config.Image}}' vivesec-graph)" "vivesec-graph:prev-$TS"
  docker rm -f vivesec-graph >/dev/null
  echo "  previous graph image kept as vivesec-graph:prev-$TS"
fi
docker tag "vivesec-graph:e26-$TS" vivesec-graph:latest
# One num_ctx with the adapter: a different window makes Ollama reload the model.
docker run -d --name vivesec-graph --network host --restart unless-stopped \
  -v /data/graph:/data/graph \
  -v "$SECRETS/neo4j_password:/run/secrets/neo4j_password:ro" \
  -e GRAPH_HOST=127.0.0.1 -e GRAPH_PORT=$GRAPH_PORT -e GRAPH_API_KEY="$GRAPH_API_KEY" \
  -e GRAPH_STORE=neo4j -e GRAPH_NEO4J_URI=bolt://127.0.0.1:7687 \
  -e GRAPH_NEO4J_PASSWORD_FILE=/run/secrets/neo4j_password \
  -e GRAPH_PROFILE="$PROFILE" -e GRAPH_PROFILE_PATH=/data/graph/profile.json \
  -e GRAPH_REVIEW_PATH=/data/graph/review.jsonl -e GRAPH_INGEST=on \
  -e GRAPH_LLM_URL=http://127.0.0.1:$BROKER_PORT -e GRAPH_LLM_MODEL="${MODEL:-qwen3.6:35b}" \
  -e GRAPH_LLM_NUM_CTX="${NUM_CTX:-65536}" \
  vivesec-graph:latest >/dev/null
wait_url http://127.0.0.1:$GRAPH_PORT/health 90 || { echo "  ABORT: graph did not come up"; docker logs --tail 30 vivesec-graph; exit 1; }
curl -s -m 10 http://127.0.0.1:$GRAPH_PORT/health | sed 's/^/  health: /'; echo

echo
echo "=== 5. adapter: graph keys + GPU broker (env and binds inherited) ==="
ADMINS=${GRAPH_ADMINS:-$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui | sed -n 's/^ADAPTER_DEMO_USER=//p')}
recreate_adapter -e GRAPH_URL=http://127.0.0.1:$GRAPH_PORT -e GRAPH_API_KEY="$GRAPH_API_KEY" \
  -e ADAPTER_GPU_BROKER_PORT=$BROKER_PORT -e ADAPTER_GRAPH_ADMINS="$ADMINS"
wait_url http://127.0.0.1:8088/api/v1/status 90 || { echo "  adapter did not come up"; exit 1; }

echo
echo "=== 6. verify ==="
curl -s -m 10 http://127.0.0.1:$BROKER_PORT/health | sed 's/^/  broker: /'; echo
DRIVE=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui | sed -n 's/^ADAPTER_DEMO_DRIVE=//p')
B64=$(printf '%s' "${DRIVE:-/storage/drives/engineering/}" | base64 -w0 | tr '+/' '-_')
for ep in status profile/get; do
  echo -n "  /ui/graph/$ep: "
  curl -s -m 20 -o /tmp/e26_probe.json -w '%{http_code} ' -H 'Content-Type: application/json' \
    -H "VVS-User: ${ADMINS%%,*}" -H "VVS-Drive: $B64" -d '{}' \
    http://127.0.0.1:8088/api/v1/ui/graph/$ep
  head -c 160 /tmp/e26_probe.json; echo
done
rm -f /tmp/e26_probe.json
docker logs vivesec-adapter 2>&1 | grep -E 'graph|broker' | tail -3 | sed 's/^/  log: /'

echo
echo "ROLLBACK: adapter -> bash $0 adapter-off  (or rename vivesec-adapter-prev-$TS back);"
echo "          graph  -> docker rm -f vivesec-graph vivesec-neo4j  (data stays in /data/neo4j, /data/graph)"
