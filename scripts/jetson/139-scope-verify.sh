#!/usr/bin/env bash
# Post-deploy verification for the multi-drive read scope on the dev box.
#
# The point is the DEFAULT: with no VVS-Other-Drives header the box must behave
# exactly as before (scope = the active drive), so this also doubles as the
# regression check for every existing single-drive deployment.
set -eu

BASE=${BASE:-http://127.0.0.1:8088/api/v1}
DRIVE=${DRIVE:-/storage/drives/engineering/}
USER_ID=${USER_ID:-verify}

b64() { printf '%s' "$1" | base64 -w0 | tr '+/' '-_' | tr -d '='; }
H_DRIVE="VVS-Drive: $(b64 "$DRIVE")"
H_USER="VVS-User: $USER_ID"

echo "=== drives known to the mirror ==="
curl -s --max-time 10 -X POST "$BASE/index/get/children" \
  -H 'Content-Type: application/json' \
  -d '{"path":"/storage/drives"}' | head -c 600
echo

echo
echo "=== 1. resolved scope (no header -> single active drive) ==="
curl -s --max-time 10 "$BASE/ui/scope" -H "$H_DRIVE" -H "$H_USER"
echo

echo
echo "=== 2. scope with a VVS-Other-Drives header ==="
OTHER=${OTHER:-}
if [ -n "$OTHER" ]; then
  H_OTHER="VVS-Other-Drives: $(printf '%s' "$OTHER" | base64 -w0 | tr '+/' '-_' | tr -d '=')"
  curl -s --max-time 10 "$BASE/ui/scope" -H "$H_DRIVE" -H "$H_USER" -H "$H_OTHER"
  echo
else
  echo "  (skipped: set OTHER=/storage/drives/<name>/ to exercise the header)"
fi

echo
echo "=== 3. status scope block ==="
curl -s --max-time 15 -X POST "$BASE/status" -H 'Content-Type: application/json' -d '{}' \
  | tr ',' '\n' | grep -A3 -i 'scope' | head -12
echo

echo
echo "=== 4. a real question still answers (single-drive regression) ==="
curl -s --max-time 180 -X POST "$BASE/ui/query" \
  -H 'Content-Type: application/json' -H "$H_DRIVE" -H "$H_USER" \
  -d '{"query":"What does this drive contain?","top_k":3}' \
  | head -c 400
echo

echo
echo "=== 5. an unentitled drive selection must be refused ==="
code=$(curl -s -o /tmp/scope_403.json -w '%{http_code}' --max-time 60 -X POST "$BASE/ui/query" \
  -H 'Content-Type: application/json' -H "$H_DRIVE" -H "$H_USER" \
  -d '{"query":"x","drives":["/storage/drives/definitely-not-granted/"]}')
echo "  HTTP $code (expected 403)"
head -c 200 /tmp/scope_403.json
echo
