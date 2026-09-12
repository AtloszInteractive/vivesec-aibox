#!/usr/bin/env bash
# Whatever the tunnel preserves, the document fetch must resolve: body only,
# query only, or both. Run after the adapter redeploy.
set -u
B=http://127.0.0.1:80/api/v1
for i in $(seq 1 24); do
  curl -s --max-time 8 "$B/status" | grep -q '"connected": *true' && break
  sleep 5
done
echo "ws-fs: $(curl -s --max-time 8 "$B/status" | grep -o '"ws_fs":[^}]*}')"
echo

DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
H=(-H "VVS-Drive: $DRIVE" -H 'VVS-User: 28744CZP27222' -H 'Content-Type: application/json')
FILE=${1:-'/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'}
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")
BODY=$(python3 -c "import json,sys; print(json.dumps({'path': sys.argv[1]}))" "$FILE")

echo "file: $FILE"
echo -n "  POST body only     : "
curl -s -o /tmp/b.bin -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 90 \
  "${H[@]}" -d "$BODY" "$B/ui/file"
echo -n "  POST query only    : "
curl -s -o /tmp/q.bin -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 90 \
  "${H[@]}" -d '{}' "$B/ui/file?path=$ENC"
echo -n "  POST body + query  : "
curl -s -o /tmp/bq.bin -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 90 \
  "${H[@]}" -d "$BODY" "$B/ui/file?path=$ENC"
echo -n "  GET  query (legacy): "
curl -s -o /tmp/g.bin -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 90 \
  "${H[@]}" "$B/ui/file?path=$ENC"
echo -n "  all four identical : "
if cmp -s /tmp/b.bin /tmp/q.bin && cmp -s /tmp/b.bin /tmp/bq.bin && cmp -s /tmp/b.bin /tmp/g.bin; then
  echo yes
else
  echo NO
fi
echo -n "  POST, nothing given: "
curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 20 "${H[@]}" -d '{}' "$B/ui/file"
