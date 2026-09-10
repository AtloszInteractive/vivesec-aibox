#!/usr/bin/env bash
# Read-only: which drives exist on this box, and how the adapter/UI are pinned.
set -u

echo "=== adapter drive config ==="
docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -Ei 'DRIVE_PREFIX|TENANT|ADAPTER_PORT|ADAPTER_TLS|NUM_CTX|JOB' || echo "(none)"
echo

echo "=== ui config ==="
docker inspect vivesec-ui --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -Ei 'DEMO|ADAPTER_URL|PORT' || echo "(none)"
echo

echo "=== drive roots seen by the mirror ==="
PREFIX=$(docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^ADAPTER_DRIVE_PREFIX=//p')
PREFIX=${PREFIX:-/storage/drives}
echo "prefix: $PREFIX"
curl -s -X POST "http://127.0.0.1:80/api/v1/index/get/children" \
  -H 'Content-Type: application/json' \
  -d "{\"path\": \"$PREFIX\"}" | head -c 4000
echo
echo

echo "=== corpora in the index ==="
curl -s "http://127.0.0.1:8090/stats" | head -c 2000
echo
