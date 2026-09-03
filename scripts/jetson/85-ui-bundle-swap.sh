#!/usr/bin/env bash
# Swap the bind-mounted UI bundle from ~/ui-stage into ~/ui-app/.output.
#
# The container binds the DIRECTORY, so its contents are replaced in place
# (moving the directory would leave the container on the old inode). The
# previous bundle is kept next to it for rollback.
set -eu

TS=$(date +%Y%m%d-%H%M)
STAGE="$HOME/ui-stage"
LIVE="$HOME/ui-app/.output"
BACKUP="$HOME/ui-app/.output.bak-$TS"

[ -f "$STAGE/server/index.mjs" ] || { echo "ABORT: staged bundle incomplete"; exit 1; }

echo "=== 1. backup the live bundle ==="
cp -a "$LIVE" "$BACKUP"
echo "  $BACKUP ($(find "$BACKUP" -type f | wc -l) files)"

echo
echo "=== 2. replace contents in place ==="
find "$LIVE" -mindepth 1 -delete
cp -a "$STAGE"/. "$LIVE"/
echo "  live: $(find "$LIVE" -type f | wc -l) files"

echo
echo "=== 3. restart ==="
docker restart vivesec-ui >/dev/null
sleep 6
echo -n "  http: "
curl -s -o /dev/null -w '%{http_code}\n' -m 20 http://127.0.0.1:8080/

echo
echo "ROLLBACK: find $LIVE -mindepth 1 -delete && cp -a $BACKUP/. $LIVE/ && docker restart vivesec-ui"
