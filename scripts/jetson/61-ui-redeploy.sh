#!/usr/bin/env bash
# Recreate the demo UI container with the same config plus the demo drive
# picker. Keeps image/mount/network identical; prints the old config first so
# the change is auditable and reversible.
set -euo pipefail

NAME=vivesec-ui
echo "=== current config ==="
docker inspect "$NAME" --format 'image={{.Config.Image}} net={{.HostConfig.NetworkMode}} restart={{.HostConfig.RestartPolicy.Name}} wd={{.Config.WorkingDir}}'
docker inspect "$NAME" --format 'cmd={{range .Config.Cmd}}{{.}} {{end}}'
docker inspect "$NAME" --format 'mount={{range .Mounts}}{{.Source}}:{{.Destination}}{{end}}'

RESTART=$(docker inspect "$NAME" --format '{{.HostConfig.RestartPolicy.Name}}')
[ -z "$RESTART" ] || [ "$RESTART" = "no" ] && RESTART=unless-stopped

echo "=== recreating ==="
docker rm -f "$NAME" >/dev/null
docker run -d --name "$NAME" \
  --network host \
  --restart "$RESTART" \
  -w /app \
  -v /home/aibox/ui-app/.output:/app/.output \
  -e NODE_ENV=production \
  -e PORT=8080 \
  -e ADAPTER_URL=http://127.0.0.1:8088 \
  -e ADAPTER_DEMO_DRIVE=/storage/drives/finance/ \
  -e ADAPTER_DEMO_USER=demo \
  -e ADAPTER_DEMO_DRIVE_PICKER=1 \
  node:22-slim node .output/server/index.mjs >/dev/null

sleep 6
echo "=== check ==="
docker ps --filter "name=$NAME" --format '{{.Names}} {{.Status}}'
curl -s -o /dev/null -w 'ui HTTP %{http_code}\n' http://127.0.0.1:8080/
docker logs --tail 3 "$NAME"
