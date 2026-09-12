#!/usr/bin/env bash
# Embedded (iframe) vs direct-IP get-file: what actually reaches the adapter.
# The adapter logs every request line and the resolved VVS identity, so the two
# candidate causes separate cleanly:
#   * no /ui/file line          -> the tunnel never forwarded the request
#   * 403/502 on /ui/file       -> the ViVeSecBox refused THIS identity
#   * 200 on /ui/file           -> bytes left us fine; the tunnel/client mangles them
set -u

echo "=== deployed UI image + demo env ==="
docker inspect vivesec-ui --format '{{.Config.Image}} {{.Image}}' | cut -c1-60
docker inspect vivesec-ui --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'ADAPTER_DEMO|ADAPTER_URL'
echo

echo "=== every VVS identity the adapter has resolved (with counts) ==="
docker logs vivesec-adapter 2>&1 | grep -o "VVS identity: user='[^']*' drive='[^']*'" | sort | uniq -c | sort -rn | head -20
echo

echo "=== every /ui/file request, with status code, newest last ==="
docker logs -t vivesec-adapter 2>&1 | grep 'ui/file' | tail -30
echo

echo "=== status-code tally for /ui/file ==="
for code in 200 400 403 404 502 503; do
  n=$(docker logs vivesec-adapter 2>&1 | grep 'ui/file' | grep -c " $code -" || true)
  [ "$n" -gt 0 ] && echo "  HTTP $code : $n"
done
echo

echo "=== ws-fs channel state ==="
curl -s --max-time 10 http://127.0.0.1:80/api/v1/status | grep -o '"ws_fs":[^}]*}'
echo

echo "=== requests seen in the last 15 minutes (any kind) ==="
docker logs --since 15m vivesec-adapter 2>&1 | grep -oE '"(GET|POST) /api/v1/[^ ?"]*' | sort | uniq -c | sort -rn | head -15
