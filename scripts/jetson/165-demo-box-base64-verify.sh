#!/usr/bin/env bash
# Does the base64 answer carry the SAME bytes as the raw one? A PDF is the real
# test: the tunnel left those structurally intact but blank.
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

probe() {
  FILE="$1"
  echo "file: $FILE"
  RAW=$(python3 -c "import json,sys; print(json.dumps({'path': sys.argv[1]}))" "$FILE")
  B64=$(python3 -c "import json,sys; print(json.dumps({'path': sys.argv[1], 'encode': 'base64'}))" "$FILE")
  echo -n "  raw bytes  : "
  curl -s -o /tmp/raw.bin -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 120 \
    "${H[@]}" -d "$RAW" "$B/ui/file"
  echo -n "  base64 json: "
  curl -s -o /tmp/b64.json -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 120 \
    "${H[@]}" -d "$B64" "$B/ui/file"
  python3 - "$FILE" <<'PY'
import base64, hashlib, json, sys
raw = open('/tmp/raw.bin', 'rb').read()
doc = json.load(open('/tmp/b64.json', encoding='utf-8'))
dec = base64.b64decode(doc.get('content_b64', ''))
print("  decoded    : %d bytes, content_type=%s, name=%s"
      % (len(dec), doc.get('content_type'), doc.get('name')))
print("  identical  : %s" % ("yes" if dec == raw else "NO"))
print("  sha256     : %s" % hashlib.sha256(dec).hexdigest()[:32])
print("  magic      : %r" % dec[:8])
PY
  echo
}

probe "${1:-/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md}"

# A real PDF from the drive, whichever the mirror lists first.
PDF=$(curl -s --max-time 20 -X POST "$B/ui/query" "${H[@]}" \
       -d '{"action":"search","mode":"files","query":"files:.pdf"}' \
     | python3 -c 'import sys,json
d=json.load(sys.stdin)
fs=[f["path"] for f in (d.get("files") or []) if f["path"].lower().endswith(".pdf")]
print(fs[0] if fs else "")' 2>/dev/null)
[ -n "$PDF" ] && probe "$PDF" || echo "(no PDF in the mirror)"
