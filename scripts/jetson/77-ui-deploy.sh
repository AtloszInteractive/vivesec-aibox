#!/usr/bin/env bash
# UI redeploy from a prebuilt Nitro bundle (~/ui-src/.output + Dockerfile).
#
# Same safety contract as the adapter deploys: the image is built and smoke
# checked BEFORE the running container is touched, the live image is retagged
# for rollback, and the container is recreated with the env captured from the
# running one.
#
#   STATUS_URL=http://127.0.0.1:8080/   what to curl after the recreate
set -eu

TS=$(date +%Y%m%d-%H%M)
SRC=${SRC:-$HOME/ui-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:8080/}

echo "=== 1. bundle going into the image ==="
cd "$SRC"
find .output -type f | wc -l | sed 's/^/  files: /'
du -sh .output | sed 's/^/  size:  /'

echo
echo "=== 2. build vivesec-ui:new ==="
docker build -f Dockerfile -t vivesec-ui:new . 2>&1 | tail -3

echo
echo "=== 3. does the new bundle boot? ==="
cid=$(docker run -d --rm -e PORT=8099 -e ADAPTER_URL=http://127.0.0.1:80 \
        --network host vivesec-ui:new)
ok=no
for _ in $(seq 1 15); do
  sleep 1
  if curl -fsS -m 3 -o /dev/null http://127.0.0.1:8099/; then ok=yes; break; fi
done
docker stop "$cid" >/dev/null
echo "  boots and serves: $ok"
if [ "$ok" != "yes" ]; then
  echo "  ABORT: the new bundle did not serve; nothing was promoted."
  exit 1
fi

echo
echo "=== 4. keep the live image for rollback ==="
docker tag vivesec-ui:latest "vivesec-ui:prev-$TS" 2>/dev/null \
  || docker tag vivesec-ui "vivesec-ui:prev-$TS"
echo "  vivesec-ui:prev-$TS -> $(docker inspect -f '{{.Id}}' "vivesec-ui:prev-$TS" | cut -c8-19)"

echo
echo "=== 5. promote + recreate (env inherited) ==="
docker tag vivesec-ui:new vivesec-ui:latest
docker tag vivesec-ui:new vivesec-ui
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui \
         | grep -vE '^(PATH|LANG|GPG_KEY|NODE_VERSION|YARN_VERSION)=')
docker stop vivesec-ui >/dev/null
docker rm vivesec-ui >/dev/null
docker run -d --name vivesec-ui --network host --restart unless-stopped \
  "${envargs[@]}" vivesec-ui >/dev/null
sleep 5

echo -n "  ui HTTP: "; curl -s -m 10 -o /dev/null -w '%{http_code}\n' "$STATUS_URL"

echo
echo "=== rollback ==="
echo "  docker rm -f vivesec-ui && docker run -d --name vivesec-ui --network host \\"
echo "    --restart unless-stopped <same env> vivesec-ui:prev-$TS"
