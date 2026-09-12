#!/usr/bin/env bash
# Wait for the ViVeSecBox channel, then prove GET and POST return the same bytes.
set -u
B=http://127.0.0.1:80/api/v1
for i in $(seq 1 24); do
  state=$(curl -s --max-time 8 "$B/status" | grep -o '"connected": *true' || true)
  [ -n "$state" ] && break
  sleep 5
done
echo "ws-fs: $(curl -s --max-time 8 "$B/status" | grep -o '"ws_fs":[^}]*}')"
echo

DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
H=(-H "VVS-Drive: $DRIVE" -H 'VVS-User: 28744CZP27222')
FILE=${1:-'/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'}
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")
BODY=$(python3 -c "import json,sys; print(json.dumps({'path': sys.argv[1]}))" "$FILE")

echo "file: $FILE"
echo -n "  GET  with query : "
curl -s -o /tmp/g.bin -D /tmp/g.hdr -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 90 \
  "${H[@]}" "$B/ui/file?path=$ENC"
echo -n "  POST with body  : "
curl -s -o /tmp/p.bin -D /tmp/p.hdr -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 90 \
  "${H[@]}" -H 'Content-Type: application/json' -d "$BODY" "$B/ui/file"
echo -n "  identical bytes : "
cmp -s /tmp/g.bin /tmp/p.bin && echo yes || echo NO
echo "  content-type    : $(grep -i '^content-type' /tmp/p.hdr | tr -d '\r')"
echo "  first bytes     : $(head -c 70 /tmp/p.bin | tr '\n' ' ')"
