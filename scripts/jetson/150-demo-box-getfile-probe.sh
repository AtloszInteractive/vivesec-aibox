#!/usr/bin/env bash
# Why does the box refuse get-file, and is the viewer really in the UI bundle?
set -u
B=http://127.0.0.1:80/api/v1
DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
H=(-H "VVS-Drive: $DRIVE" -H 'VVS-User: demo')

echo "=== UI bundle: viewer strings (text-mode grep) ==="
curl -s --max-time 20 http://127.0.0.1:8080/ | grep -o '/assets/[^"]*\.js' | sort -u | while read -r a; do
  body=$(curl -s --max-time 30 "http://127.0.0.1:8080$a")
  v=$(printf '%s' "$body" | grep -c -a 'Fetching the document from the ViVeSecBox' || true)
  w=$(printf '%s' "$body" | grep -c -a 'Retrieved passages' || true)
  r=$(printf '%s' "$body" | grep -c -a 'What type of report do you want' || true)
  echo "  $a viewer=$v passages=$w reportWizard=$r"
done
echo

echo "=== get-file against several real files ==="
curl -s --max-time 20 -X POST "$B/ui/query" "${H[@]}" -H 'Content-Type: application/json' \
  -d '{"action":"search","mode":"files","query":"files:"}' \
  | python3 -c 'import sys,json
d=json.load(sys.stdin)
for f in (d.get("files") or [])[:5]:
    print(f["path"])' > /tmp/files.txt 2>/dev/null
cat /tmp/files.txt | sed 's/^/  candidate: /'
echo
while read -r f; do
  [ -n "$f" ] || continue
  ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$f")
  code=$(curl -s -o /tmp/gf.bin -w '%{http_code}' --max-time 60 "${H[@]}" "$B/ui/file?path=$ENC")
  echo "  [$code] $f"
  [ "$code" != "200" ] && head -c 120 /tmp/gf.bin | sed 's/^/        /' && echo
done < /tmp/files.txt
echo

echo "=== get-file for a path that does not exist (error vocabulary check) ==="
curl -s --max-time 30 -w '\n  HTTP %{http_code}\n' "${H[@]}" \
  "$B/ui/file?path=%2Fstorage%2Fdrives%2Faiboxdev%2Fdefinitely_missing_file_xyz.txt" | sed 's/^/  /'
echo

echo "=== what the box says in the adapter log ==="
docker logs --tail 60 vivesec-adapter 2>&1 | grep -Ei 'ui/file|get-file|permission' | tail -10
