#!/usr/bin/env bash
# Which identities has the ViVeSecBox actually presented, and does get-file work
# for one of them? "permission" for a non-existent path points at the USER, not
# the file: the box grants reads per user drive access.
set -u
B=http://127.0.0.1:80/api/v1

echo "=== VVS identities seen in the adapter log ==="
docker logs vivesec-adapter 2>&1 | grep -o "VVS identity: user='[^']*' drive='[^']*'" | sort | uniq -c | sort -rn | head -20
echo

echo "=== VVS-Other-Drives sightings ==="
docker logs vivesec-adapter 2>&1 | grep -i 'other-drives' | tail -10 || echo "  (none in this container's log)"
echo

echo "=== requests that arrived over the mTLS front (:443) ==="
docker logs vivesec-adapter 2>&1 | grep -Ei 'tls|443' | tail -10 || echo "  (none)"
echo

echo "=== try get-file for every identity we have seen ==="
USERS=$(docker logs vivesec-adapter 2>&1 | grep -o "VVS identity: user='[^']*'" | sed "s/.*user='//;s/'//" | sort -u)
if [ -z "$USERS" ]; then
  echo "  no identity lines in this container's log (it was recreated 15:08)"
  USERS="demo"
fi
DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
FILE='/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")
for u in $USERS; do
  code=$(curl -s -o /tmp/gf.bin -w '%{http_code}' --max-time 60 \
          -H "VVS-Drive: $DRIVE" -H "VVS-User: $u" "$B/ui/file?path=$ENC")
  echo "  user='$u' -> HTTP $code  $(head -c 80 /tmp/gf.bin | tr -d '\n')"
done
