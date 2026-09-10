#!/usr/bin/env bash
# Turn on multi-drive read scope on a box that has no ViVeSecBox header yet.
#
# The entitlements file is the documented stand-in for VVS-Other-Drives: it
# grants drives per user ("*" = everyone). This lets the cross-drive search be
# exercised against drives that are ALREADY indexed, with no corpus upload.
#
# Set DRIVES to override; by default every drive the mirror knows is granted.
set -eu

# Container-side path: the adapter reads it, and the host bind (/data/adapter)
# is root-owned, so the file is written THROUGH the container.
FILE=${FILE:-/data/entitlements.json}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8088/api/v1/status}
MIRROR_URL=${MIRROR_URL:-http://127.0.0.1:8088/api/v1/index/get/children}
TS=$(date +%Y%m%d-%H%M)

echo "=== 1. adapter binds (the file must live on one of them) ==="
docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter | sed 's/^/  /'

echo
echo "=== 2. drives known to the mirror ==="
if [ -n "${DRIVES:-}" ]; then
  list="$DRIVES"
else
  list=$(curl -s --max-time 10 -X POST "$MIRROR_URL" \
           -H 'Content-Type: application/json' \
           -d '{"path":"/storage/drives"}' \
         | tr ',' '\n' | grep -o '/storage/drives/[^"]*' | sort -u | tr '\n' ' ')
fi
echo "  $list"
[ -n "$list" ] || { echo "ABORT: no drives found"; exit 1; }

echo
echo "=== 3. write $FILE (inside the container) ==="
json=$(
  printf '{\n  "*": [\n'
  first=1
  for d in $list; do
    [ $first -eq 1 ] || printf ',\n'
    printf '    "%s/"' "$d"
    first=0
  done
  printf '\n  ]\n}\n'
)
printf '%s' "$json" | docker exec -i vivesec-adapter sh -c "cat > $FILE"
docker exec vivesec-adapter cat "$FILE" | sed 's/^/  /'

echo
echo "=== 4. recreate the adapter with ADAPTER_ENTITLEMENTS ==="
docker tag "$(docker inspect -f '{{.Image}}' vivesec-adapter)" "vivesec-adapter:prevcfg-$TS"
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=' \
         | grep -v '^ADAPTER_ENTITLEMENTS=')
envargs+=( -e "ADAPTER_ENTITLEMENTS=$FILE" )
bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)
docker rm -f vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${envargs[@]}" "${bindargs[@]}" vivesec-adapter:latest >/dev/null
sleep 5

echo
echo "=== 5. verify ==="
b64() { printf '%s' "$1" | base64 -w0 | tr '+/' '-_' | tr -d '='; }
first_drive=$(echo "$list" | awk '{print $1}')
echo "  status scope: $(curl -s --max-time 15 -X POST "$STATUS_URL" \
   -H 'Content-Type: application/json' -d '{}' | tr ',' '\n' | grep -A2 '"scope"' | tr '\n' ' ')"
echo "  resolved:"
curl -s --max-time 10 http://127.0.0.1:8088/api/v1/ui/scope \
  -H "VVS-Drive: $(b64 "$first_drive/")" -H 'VVS-User: demo' | sed 's/^/    /'
echo

echo "ROLLBACK: remove ADAPTER_ENTITLEMENTS from the container env"
echo "  (rerun this script with the var dropped, or restore vivesec-adapter:prevcfg-$TS)"
