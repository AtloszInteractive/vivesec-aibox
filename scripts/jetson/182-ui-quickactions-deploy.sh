#!/usr/bin/env bash
# Swap the live UI bundle for the quick-action results master-detail build.
#
# The UI container binds a DIRECTORY read-only, so the contents are replaced in
# place (moving the directory would leave the container on the old inode). The
# live path is read from the container itself -- on this box it is the release
# stage's build/.output, not ~/ui-app/.output.
#
# Usage: bash 182-ui-quickactions-deploy.sh [/path/to/ui-output.tgz]
set -eu

TS=$(date +%Y%m%d-%H%M)
TGZ=${1:-$HOME/ui-output-qa.tgz}
STAGE=${STAGE:-$HOME/ui-stage-qa}
LIVE=$(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-ui \
       | grep ':/app/.output' | cut -d: -f1)
BACKUP="$LIVE.bak-$TS"

[ -n "$LIVE" ] || { echo "ABORT: no /app/.output bind on vivesec-ui"; exit 1; }
[ -f "$TGZ" ] || { echo "ABORT: bundle not found: $TGZ"; exit 1; }

echo "=== 1. unpack the staged bundle ==="
rm -rf "$STAGE"
mkdir -p "$STAGE.tmp"
tar -xzf "$TGZ" -C "$STAGE.tmp"
[ -f "$STAGE.tmp/.output/server/index.mjs" ] || { echo "ABORT: archive has no .output/server/index.mjs"; exit 1; }
mv "$STAGE.tmp/.output" "$STAGE"
rm -rf "$STAGE.tmp"
echo "  staged: $STAGE ($(find "$STAGE" -type f | wc -l) files)"

echo
echo "=== 2. guard: the bundle must carry both the new and the old features ==="
grep -rlq 'conversations/list' "$STAGE" || { echo "ABORT: staged bundle has no conversation client"; exit 1; }
grep -rlq 'Back to tasks' "$STAGE" || { echo "ABORT: staged bundle has no results master-detail view"; exit 1; }
grep -rlq 'Vissza a feladatokhoz' "$STAGE" || { echo "ABORT: staged bundle is missing the HU translation"; exit 1; }
echo "  ok"

echo
echo "=== 3. backup the live bundle ==="
echo "  live: $LIVE"
cp -a "$LIVE" "$BACKUP"
echo "  backup: $BACKUP ($(find "$BACKUP" -type f | wc -l) files)"

echo
echo "=== 4. replace contents in place ==="
find "$LIVE" -mindepth 1 -delete
cp -a "$STAGE"/. "$LIVE"/
echo "  live: $(find "$LIVE" -type f | wc -l) files"

echo
echo "=== 5. restart + verify ==="
docker restart vivesec-ui >/dev/null
sleep 6
echo -n "  http: "; curl -s -o /dev/null -w '%{http_code}\n' -m 20 http://127.0.0.1:8080/
echo -n "  served bundle mentions the thread client: "
docker exec vivesec-ui sh -c 'grep -rl "conversations/list" /app/.output | wc -l'
echo -n "  served bundle mentions the results detail view: "
docker exec vivesec-ui sh -c 'grep -rl "Back to tasks" /app/.output | wc -l'
echo "  demo env (must survive the restart):"
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui \
  | grep -E '^ADAPTER_' | sed 's/^/    /'

echo
echo "ROLLBACK: find $LIVE -mindepth 1 -delete && cp -a $BACKUP/. $LIVE/ && docker restart vivesec-ui"
