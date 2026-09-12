#!/usr/bin/env bash
# Adapter-only redeploy on the demo box (the RAG is already current, so it is
# left running). Same contract as the catch-up deploy: build, test inside the
# candidate image, tag the live image for rollback, then recreate with the env
# and binds captured from the running container.
set -eu

TS=$(date +%Y%m%d-%H%M)
ADAPTER_SRC=${ADAPTER_SRC:-$HOME/adapter-src}
STATUS_URL=${STATUS_URL:-http://127.0.0.1:80/api/v1/status}
PREV="vivesec-adapter:prev-$TS"

echo "=== 0. live container ==="
net=$(docker inspect -f '{{.HostConfig.NetworkMode}}' vivesec-adapter)
echo "  image=$(docker inspect -f '{{.Image}}' vivesec-adapter | cut -c8-19) network=$net"
[ "$net" = "host" ] || { echo "ABORT: not on the host network"; exit 1; }

echo
echo "=== 1. rollback tag ==="
docker tag "$(docker inspect -f '{{.Image}}' vivesec-adapter)" "$PREV"
echo "  $PREV"

echo
echo "=== 2. build candidate ==="
cd "$ADAPTER_SRC"
sha256sum service.py | sed 's/^/  /'
docker build -q -f Dockerfile -t vivesec-adapter:postfile . >/dev/null
echo "  $(docker inspect -f '{{.Id}}' vivesec-adapter:postfile | cut -c8-19)"

echo
echo "=== 3. tests inside the candidate ==="
for t in scope_test voice_test docgen_test scheduler_test llm_stream_test smoke_test; do
  printf '  %-18s ' "$t"
  if out=$(docker run --rm --entrypoint python vivesec-adapter:postfile \
             "/app/adapter/$t.py" 2>&1); then
    echo "$out" | tail -1
  else
    echo "FAILED"; echo "$out" | tail -10 | sed 's/^/      /'
    echo "  ABORT: nothing was promoted."
    exit 1
  fi
done

echo
echo "=== 4. promote (env + binds inherited) ==="
aenv=()
while IFS= read -r e; do
  [ -n "$e" ] && aenv+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=')
abinds=()
while IFS= read -r b; do
  [ -n "$b" ] && abinds+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-adapter)
docker tag vivesec-adapter:postfile vivesec-adapter:latest
docker rm -f vivesec-adapter >/dev/null
docker run -d --name vivesec-adapter --network host --restart unless-stopped \
  "${aenv[@]}" "${abinds[@]}" vivesec-adapter:latest >/dev/null
sleep 6

echo
echo "=== 5. verify both entry points ==="
DRIVE=$(printf '/storage/drives/aiboxdev/' | base64 | tr -d '=' | tr '+/' '-_')
H=(-H "VVS-Drive: $DRIVE" -H 'VVS-User: 28744CZP27222')
FILE='/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")

echo -n "  GET  with query : "
curl -s -o /tmp/g.bin -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 60 \
  "${H[@]}" "http://127.0.0.1:80/api/v1/ui/file?path=$ENC"
echo -n "  POST with body  : "
curl -s -o /tmp/p.bin -w 'HTTP %{http_code}  %{size_download} bytes\n' --max-time 60 \
  "${H[@]}" -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json,sys; print(json.dumps({'path': sys.argv[1]}))" "$FILE")" \
  "http://127.0.0.1:80/api/v1/ui/file"
echo -n "  identical bytes : "
cmp -s /tmp/g.bin /tmp/p.bin && echo yes || echo NO
echo -n "  POST, no path   : "
curl -s -o /dev/null -w 'HTTP %{http_code}\n' --max-time 20 "${H[@]}" \
  -H 'Content-Type: application/json' -d '{}' "http://127.0.0.1:80/api/v1/ui/file"

echo
echo "  ws-fs: $(curl -s --max-time 15 "$STATUS_URL" | grep -o '"ws_fs":[^}]*}')"
echo
echo "ROLLBACK: docker rm -f vivesec-adapter && docker run -d --name vivesec-adapter --network host --restart unless-stopped <env/binds> $PREV"
