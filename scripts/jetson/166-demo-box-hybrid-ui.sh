#!/usr/bin/env bash
set -euo pipefail

ARCHIVE=${1:?Pass the prebuilt UI archive path}
STAMP=$(date +%Y%m%d-%H%M%S)
STAGE=$(mktemp -d "$HOME/ui-hybrid-XXXXXX")
PREVIOUS="vivesec-ui-prev-$STAMP"
IMAGE="vivesec-ui:hybrid-$STAMP"
CANDIDATE="vivesec-ui-check-$STAMP"
ADAPTER_BEFORE=$(docker inspect -f '{{.Id}}' vivesec-adapter)
RAG_BEFORE=$(docker inspect -f '{{.Id}}' vivesec-rag)
test "$(docker inspect -f '{{.HostConfig.NetworkMode}}' vivesec-ui)" = host
test "$(docker inspect -f '{{len .Mounts}}' vivesec-ui)" = 0
test "$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' vivesec-ui)" = unless-stopped
test "$(docker inspect -f '{{.HostConfig.Privileged}}' vivesec-ui)" = false

cp "$HOME/ui-src/Dockerfile" "$STAGE/Dockerfile"
tar -xzf "$ARCHIVE" -C "$STAGE"
grep -rlq locked_hybrid "$STAGE/.output"
docker build -q -t "$IMAGE" "$STAGE"

PROMOTED=no
cleanup() {
  result=$?
  trap - EXIT
  docker rm -f "$CANDIDATE" >/dev/null 2>&1 || true
  if [ "$result" -ne 0 ] && [ "$PROMOTED" = yes ]; then
    docker rm -f vivesec-ui >/dev/null 2>&1 || true
    docker rename "$PREVIOUS" vivesec-ui
    docker start vivesec-ui >/dev/null
    echo "ROLLBACK: original UI container restored"
  fi
  exit "$result"
}
trap cleanup EXIT

envargs=()
while IFS= read -r entry; do
  [ -z "$entry" ] || envargs+=( -e "$entry" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui)

docker run -d --name "$CANDIDATE" --network host "${envargs[@]}" -e PORT=8099 "$IMAGE" >/dev/null
curl -fsS --retry 15 --retry-connrefused --retry-max-time 60 --max-time 5 http://127.0.0.1:8099/ -o "$STAGE/candidate.html"
docker exec "$CANDIDATE" grep -rl locked_hybrid /app/.output
docker rm -f "$CANDIDATE" >/dev/null

docker tag "$(docker inspect -f '{{.Image}}' vivesec-ui)" "vivesec-ui:prev-$STAMP"
docker stop vivesec-ui >/dev/null
docker rename vivesec-ui "$PREVIOUS"
PROMOTED=yes
docker run -d --name vivesec-ui --network host --restart unless-stopped "${envargs[@]}" "$IMAGE" >/dev/null
curl -fsS --retry 15 --retry-connrefused --retry-max-time 60 --max-time 5 http://127.0.0.1:8080/ -o "$STAGE/live.html"
cmp "$STAGE/candidate.html" "$STAGE/live.html" >/dev/null || test -s "$STAGE/live.html"
docker exec vivesec-ui grep -rl locked_hybrid /app/.output
test "$ADAPTER_BEFORE" = "$(docker inspect -f '{{.Id}}' vivesec-adapter)"
test "$RAG_BEFORE" = "$(docker inspect -f '{{.Id}}' vivesec-rag)"
docker tag "$IMAGE" vivesec-ui:latest
docker tag "$IMAGE" vivesec-ui
echo "PASS: UI HTTP 200; locked_hybrid present; adapter and RAG containers unchanged"
echo "IMAGE=$IMAGE"
echo "STAGE=$STAGE"
echo "ROLLBACK_CONTAINER=$PREVIOUS"
echo "ROLLBACK_IMAGE=vivesec-ui:prev-$STAMP"