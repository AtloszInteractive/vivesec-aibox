#!/usr/bin/env bash
# Post-deploy verification on the demo box: the pieces that actually changed.
set -u
B=http://127.0.0.1:80/api/v1
DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
H=(-H "VVS-Drive: $DRIVE" -H 'VVS-User: demo')

echo "=== status ==="
curl -s --max-time 20 "$B/status" | tr ',' '\n' | grep -E 'scope|ws_fs|index|mirror|ok' | head -12
echo

echo "=== 1. a real drive file, listed from the mirror ==="
FILE=$(curl -s --max-time 20 -X POST "$B/ui/query" "${H[@]}" \
        -H 'Content-Type: application/json' \
        -d '{"action":"search","mode":"files","query":"files:"}' \
      | python3 -c 'import sys,json; d=json.load(sys.stdin); fs=d.get("files") or []; print(fs[0]["path"] if fs else "")' 2>/dev/null)
echo "  first file: ${FILE:-<none>}"
echo

echo "=== 2. get-file: fetch that document from the ViVeSecBox ==="
if [ -n "$FILE" ]; then
  ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")
  curl -s -o /tmp/getfile.bin -D /tmp/getfile.hdr -w '  HTTP %{http_code}  %{size_download} bytes\n' \
    --max-time 90 "${H[@]}" "$B/ui/file?path=$ENC"
  echo "  content-type: $(grep -i '^content-type' /tmp/getfile.hdr | tr -d '\r')"
  echo "  first bytes : $(head -c 60 /tmp/getfile.bin | tr -d '\0' | tr '\n' ' ')"
else
  echo "  SKIPPED (no file in the mirror)"
fi
echo

echo "=== 3. scope endpoint ==="
curl -s --max-time 20 "${H[@]}" "$B/ui/scope" | head -c 400
echo
echo

echo "=== 4. grounded question end to end (uses the new prompt path) ==="
curl -s --max-time 180 -X POST "$B/ui/query" "${H[@]}" \
  -H 'Content-Type: application/json' \
  -d '{"query":"What does this drive contain?","lang":"English"}' \
  | python3 -c 'import sys,json
d=json.load(sys.stdin)
print("  ok:",d.get("ok"),"backend:",d.get("backend"))
c=d.get("confidence") or {}
print("  confidence:",c.get("score"),c.get("band"),"hits:",len(d.get("hits") or []))
print("  answer:",(d.get("answer") or "")[:240].replace("\n"," "))' 2>/dev/null || echo "  (could not parse)"
echo

echo "=== 5. job store is live ==="
ls -la /data/jobs 2>/dev/null | head -5
echo

echo "=== 6. UI bundle carries the viewer ==="
curl -s --max-time 20 http://127.0.0.1:8080/ | grep -o '/assets/[^"]*\.js' | sort -u | while read -r a; do
  n=$(curl -s --max-time 30 "http://127.0.0.1:8080$a" | grep -c 'Fetching the document from the ViVeSecBox' || true)
  echo "  $a viewer=$n"
done
