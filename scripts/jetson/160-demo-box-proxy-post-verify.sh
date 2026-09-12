#!/usr/bin/env bash
# Does the POST document fetch survive the UI proxy (the LAN/IP path)?
set -u
FILE=${1:-'/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'}
BODY=$(python3 -c "import json,sys; print(json.dumps({'path': sys.argv[1]}))" "$FILE")

echo -n "  via UI proxy, POST : "
curl -s -o /tmp/u.bin -D /tmp/u.hdr -w 'HTTP %{http_code}  %{size_download} bytes\n' \
  --max-time 90 -H 'Content-Type: application/json' -d "$BODY" \
  http://127.0.0.1:8080/api/v1/ui/file
echo "  content-type       : $(grep -i '^content-type' /tmp/u.hdr | tr -d '\r')"
echo "  first bytes        : $(head -c 60 /tmp/u.bin | tr '\n' ' ')"
echo
echo "  ui env: $(docker inspect vivesec-ui --format '{{range .Config.Env}}{{println .}}{{end}}' | grep ADAPTER_DEMO_USER)"
echo "  ui image: $(docker inspect vivesec-ui --format '{{.Image}}' | cut -c8-19)"
