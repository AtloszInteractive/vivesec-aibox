#!/usr/bin/env bash
# Switch the adapter between the workspace and the analyst app mode
# (ADAPTER_APP_MODE). Same image, env and binds; only that one key changes.
#
#   bash 191-app-mode.sh analyst     # chat + relationships, orchestrator on
#   bash 191-app-mode.sh workspace   # drive browser + quick actions (rollback)
#
# The adapter restart cuts the ws-fs channel for a moment, so never while a
# drive sync runs. The UI follows /api/v1/status; no UI change is needed.
set -eu
MODE=${1:-}
case "$MODE" in analyst|workspace) ;; *) echo "usage: $0 analyst|workspace"; exit 2;; esac
TS=$(date +%Y%m%d-%H%M%S)

if pgrep -f 'drive-sync-folder\.py' >/dev/null; then
  echo "ABORT: a drive sync is running; the adapter restart would cut its ws-fs channel."
  exit 1
fi

envargs=()
while IFS= read -r e; do [ -n "$e" ] && envargs+=( -e "$e" ); done < <(
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
  | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED|ADAPTER_APP_MODE)=')
bindargs=()
while IFS= read -r b; do [ -n "$b" ] && bindargs+=( -v "$b" ); done < <(
  docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)
image=$(docker inspect -f '{{.Config.Image}}' vivesec-adapter)

# Rename first: a name clash then fails before anything is stopped.
docker rename vivesec-adapter "vivesec-adapter-prev-$TS"
docker stop "vivesec-adapter-prev-$TS" >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${envargs[@]}" -e ADAPTER_APP_MODE="$MODE" "${bindargs[@]}" "$image" >/dev/null || {
  docker rm -f vivesec-adapter >/dev/null 2>&1 || true
  docker rename "vivesec-adapter-prev-$TS" vivesec-adapter && docker start vivesec-adapter >/dev/null
  echo "ABORT: the new adapter did not start; the previous one runs again."; exit 1; }
for i in $(seq 1 90); do
  curl -fsS -m 3 http://127.0.0.1:8088/api/v1/status >/dev/null 2>&1 && break
  sleep 1
done
curl -s -m 10 http://127.0.0.1:8088/api/v1/status \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("app:", d.get("app"), "ui_ready:", d.get("ui_ready"))'
echo "previous container kept stopped as vivesec-adapter-prev-$TS (docker rm it when no longer needed)"
