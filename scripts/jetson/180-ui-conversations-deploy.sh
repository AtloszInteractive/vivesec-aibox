#!/usr/bin/env bash
# Swap the live UI bundle for the one staged in ~/ui-stage-f1.
#
# The UI container binds a DIRECTORY read-only, so the contents are replaced in
# place (moving the directory would leave the container on the old inode). The
# live path is read from the container itself -- on this box it is the release
# stage's build/.output, not ~/ui-app/.output.
set -eu

TS=$(date +%Y%m%d-%H%M)
STAGE=${STAGE:-$HOME/ui-stage-f1}
LIVE=$(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-ui \
       | grep ':/app/.output' | cut -d: -f1)
BACKUP="$LIVE.bak-$TS"

[ -n "$LIVE" ] || { echo "ABORT: no /app/.output bind on vivesec-ui"; exit 1; }
[ -f "$STAGE/server/index.mjs" ] || { echo "ABORT: staged bundle incomplete"; exit 1; }
grep -rlq 'conversations/list' "$STAGE" || { echo "ABORT: staged bundle has no conversation client"; exit 1; }

echo "=== 1. backup the live bundle ==="
echo "  live: $LIVE"
cp -a "$LIVE" "$BACKUP"
echo "  backup: $BACKUP ($(find "$BACKUP" -type f | wc -l) files)"

echo
echo "=== 2. replace contents in place ==="
find "$LIVE" -mindepth 1 -delete
cp -a "$STAGE"/. "$LIVE"/
echo "  live: $(find "$LIVE" -type f | wc -l) files"

echo
echo "=== 3. restart + verify ==="
docker restart vivesec-ui >/dev/null
sleep 6
echo -n "  http: "; curl -s -o /dev/null -w '%{http_code}\n' -m 20 http://127.0.0.1:8080/
echo -n "  served bundle mentions the thread client: "
docker exec vivesec-ui sh -c 'grep -rl "conversations/list" /app/.output | wc -l'
echo "  demo env (must survive the restart):"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui \
  | grep -E '^ADAPTER_' | sed 's/^/    /'

echo
echo "ROLLBACK: find $LIVE -mindepth 1 -delete && cp -a $BACKUP/. $LIVE/ && docker restart vivesec-ui"
