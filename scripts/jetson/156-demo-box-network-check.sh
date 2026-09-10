#!/usr/bin/env bash
# Addresses in play: ours, the ViVeSecBox's, and what SSDP advertises.
set -u
echo "=== this box ==="
hostname -I
echo

echo "=== SSDP advertisement (what the ViVeSecBox is told to call) ==="
docker logs vivesec-adapter 2>&1 | grep -i 'Discovery' | tail -3
echo

echo "=== ws-fs channel: connected, and from where ==="
docker logs vivesec-adapter 2>&1 | grep -i 'ws-fs channel' | tail -5
curl -s --max-time 10 http://127.0.0.1:80/api/v1/status | grep -o '"ws_fs":[^}]*}'
echo

echo "=== established connections to the adapter ports ==="
ss -tn state established 2>/dev/null | grep -E ':(80|443)\b' | head -10 || echo "  (none visible)"
echo

echo "=== live get-file through the UI proxy (real identity) ==="
FILE='/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")
curl -s -o /tmp/x.bin -w '  HTTP %{http_code}  %{size_download} bytes\n' --max-time 60 \
  "http://127.0.0.1:8080/api/v1/ui/file?path=$ENC"
echo
echo "=== container health ==="
docker ps --filter name=vivesec --format 'table {{.Names}}\t{{.Status}}'
