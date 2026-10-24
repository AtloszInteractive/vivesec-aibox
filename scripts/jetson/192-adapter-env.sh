#!/usr/bin/env bash
# Set (or clear) one ADAPTER_* setting on the running adapter. Same image, env
# and binds; only that key changes, and later releases keep it (they inherit
# the container env).
#
#   bash 192-adapter-env.sh ADAPTER_GRAPH_NAME_JOIN=same_name
#   bash 192-adapter-env.sh ADAPTER_GRAPH_NAME_JOIN=          # back to the default
#
# Only ADAPTER_* keys: secrets and service keys are not changed this way, and
# values are not echoed back. The adapter restart cuts the ws-fs channel for a
# moment, so never while a drive sync runs.
set -eu
ARG=${1:-}
KEY=${ARG%%=*}
VALUE=${ARG#*=}
if [ -z "$ARG" ] || [ "$KEY" = "$ARG" ] || ! [[ "$KEY" =~ ^ADAPTER_[A-Z0-9_]+$ ]]; then
  echo "usage: $0 ADAPTER_KEY=value   (empty value removes the key)"; exit 2
fi
TS=$(date +%Y%m%d-%H%M)

if pgrep -f 'drive-sync-folder\.py' >/dev/null; then
  echo "ABORT: a drive sync is running; the adapter restart would cut its ws-fs channel."
  exit 1
fi

envargs=()
while IFS= read -r e; do [ -n "$e" ] && envargs+=( -e "$e" ); done < <(
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -vE "^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED|$KEY)=")
[ -n "$VALUE" ] && envargs+=( -e "$KEY=$VALUE" )
bindargs=()
while IFS= read -r b; do [ -n "$b" ] && bindargs+=( -v "$b" ); done < <(
  docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)
image=$(docker inspect -f '{{.Config.Image}}' vivesec-adapter)

docker stop vivesec-adapter >/dev/null
docker rename vivesec-adapter "vivesec-adapter-prev-$TS"
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" "$image" >/dev/null
for i in $(seq 1 90); do
  curl -fsS -m 3 http://127.0.0.1:8088/api/v1/status >/dev/null 2>&1 && break
  sleep 1
done
if curl -fsS -m 10 http://127.0.0.1:8088/api/v1/status >/dev/null 2>&1; then
  if [ -n "$VALUE" ]; then echo "adapter up; $KEY set"; else echo "adapter up; $KEY removed"; fi
else
  echo "ABORT: adapter did not come up; previous container: vivesec-adapter-prev-$TS"; exit 1
fi
echo "previous container kept stopped as vivesec-adapter-prev-$TS (docker rm it when no longer needed)"
