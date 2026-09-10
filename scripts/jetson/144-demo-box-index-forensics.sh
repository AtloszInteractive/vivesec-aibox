#!/usr/bin/env bash
# What happened to the index? Look for tree drops, rebuilds and content uploads.
set -u

echo "=== index-touching calls in the adapter log (last 2000 lines) ==="
docker logs --tail 4000 vivesec-adapter 2>&1 \
  | grep -E 'index/(upsert|drop|rebuild)|ws-fs|Traceback|Error|error' \
  | tail -60
echo

echo "=== counts by kind (last 4000 lines) ==="
docker logs --tail 4000 vivesec-adapter 2>&1 | grep -c 'index/upsert/file/content' | sed 's/^/content uploads: /'
docker logs --tail 4000 vivesec-adapter 2>&1 | grep -c 'index/upsert/file/check' | sed 's/^/checks: /'
docker logs --tail 4000 vivesec-adapter 2>&1 | grep -c 'index/drop' | sed 's/^/drops: /'
docker logs --tail 4000 vivesec-adapter 2>&1 | grep -c 'index/rebuild' | sed 's/^/rebuilds: /'
echo

echo "=== rag log tail ==="
docker logs --tail 40 vivesec-rag 2>&1 | tail -40
echo

echo "=== container start times ==="
docker inspect vivesec-adapter --format 'adapter started: {{.State.StartedAt}}'
docker inspect vivesec-rag --format 'rag started: {{.State.StartedAt}}'
echo

echo "=== per-corpus document counts ==="
curl -s http://127.0.0.1:8090/stats
echo
