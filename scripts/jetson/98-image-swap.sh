#!/usr/bin/env bash
# Recreate a container from a named image, keeping its env and binds.
#
# Used to move a box between builds for a controlled measurement: the same
# container definition runs, only the image changes, so any difference in the
# numbers belongs to the build and not to a stray setting.
#
#   CONTAINER=vivesec-adapter \
#   IMAGE=vivesec-adapter:prev-20260814-1211 \
#   [SET=ADAPTER_GEN_MODEL=qwen3.6:35b,ADAPTER_NUM_PREDICT=2048] \
#   [UNSET=ADAPTER_ANALYZE_NUM_CTX,ADAPTER_ANALYZE_MAX_CHARS] \
#   [STATUS_URL=http://127.0.0.1:80/api/v1/status] \
#   bash 98-image-swap.sh
#
# SET and UNSET are COMMA separated and must contain no spaces: these commands
# travel through a PowerShell -> ssh chain that eats embedded quotes.
set -eu

CONTAINER=${CONTAINER:?set CONTAINER}
IMAGE=${IMAGE:?set IMAGE}
SET=${SET:-}
UNSET=${UNSET:-}
STATUS_URL=${STATUS_URL:-}
TS=$(date +%Y%m%d-%H%M%S)

echo "=== 1. target image exists? ==="
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "  ABORT: no such image: $IMAGE"
  exit 1
fi
echo "  $IMAGE -> $(docker image inspect -f '{{.Id}}' "$IMAGE" | cut -c8-19)"

echo
echo "=== 2. what is running now ==="
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "  ABORT: no such container: $CONTAINER"
  exit 1
fi
was=$(docker inspect -f '{{.Image}}' "$CONTAINER" | cut -c8-19)
echo "  $CONTAINER on $was"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" > "/tmp/$CONTAINER.env.$TS"
docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$CONTAINER" > "/tmp/$CONTAINER.binds.$TS"
echo "  env/binds captured in /tmp/$CONTAINER.{env,binds}.$TS"

# Keys the image supplies itself; carrying a stale copy across a swap would
# pin the new image to the old image's defaults.
drop='^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED|NODE_VERSION|YARN_VERSION)='
IFS=',' read -ra unset_keys <<< "$UNSET"
for k in "${unset_keys[@]}"; do
  [ -n "$k" ] && drop="$drop"'|^'"$k"'='
done

envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(grep -vE "$drop" "/tmp/$CONTAINER.env.$TS" || true)
IFS=',' read -ra set_pairs <<< "$SET"
for kv in "${set_pairs[@]}"; do
  [ -n "$kv" ] && envargs+=( -e "$kv" )
done

bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(cat "/tmp/$CONTAINER.binds.$TS")

echo
echo "=== 3. recreate on $IMAGE ==="
docker stop "$CONTAINER" >/dev/null
docker rm "$CONTAINER" >/dev/null
docker run -d --name "$CONTAINER" --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" "$IMAGE" >/dev/null
sleep 8

echo
echo "=== 4. verify ==="
docker inspect -f '  running={{.State.Running}} image={{.Image}}' "$CONTAINER" | cut -c1-60
if [ -n "$STATUS_URL" ]; then
  echo -n "  status: "; curl -fsS -m 20 "$STATUS_URL" | head -c 110 || echo FAILED
  echo
fi
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" \
  | grep -E '^(ADAPTER_(GEN_MODEL|THINK|NUM_PREDICT|NUM_CTX|ANALYZE)|RAG_)' \
  | sed 's/^/    /' || true

echo
echo "PREVIOUS IMAGE WAS: $was   (restore with IMAGE=<that tag>)"
