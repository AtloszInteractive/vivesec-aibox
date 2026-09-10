#!/usr/bin/env bash
# The LAN demo UI injects its own VVS-User; the ViVeSecBox only grants reads to
# an identity that actually has drive access, so point the proxy at the real one.
set -eu

USER_ID=${1:?usage: 155-demo-box-ui-user.sh <VVS-User>}
TS=$(date +%Y%m%d-%H%M)

echo "=== current UI env ==="
docker inspect vivesec-ui --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'ADAPTER_|PORT' | sed 's/^/  /'

echo
echo "=== recreate with ADAPTER_DEMO_USER=$USER_ID ==="
envargs=()
while IFS= read -r e; do
  case "$e" in
    ADAPTER_DEMO_USER=*) continue ;;
  esac
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui \
         | grep -vE '^(PATH|LANG|GPG_KEY|NODE_VERSION|YARN_VERSION)=')
envargs+=( -e "ADAPTER_DEMO_USER=$USER_ID" )

docker rm -f vivesec-ui >/dev/null
docker run -d --name vivesec-ui --network host --restart unless-stopped \
  "${envargs[@]}" vivesec-ui >/dev/null
sleep 5

echo "  ui HTTP: $(curl -s -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:8080/)"
echo "  env now: $(docker inspect vivesec-ui --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'ADAPTER_DEMO' | tr '\n' ' ')"

echo
echo "=== through the UI proxy: does get-file work now? ==="
FILE='/storage/drives/aiboxdev/AI BOX TESZT/OFFICE ASSISTANT/README.md'
ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$FILE")
curl -s -o /tmp/ui_gf.bin -w '  HTTP %{http_code}  %{size_download} bytes\n' --max-time 90 \
  "http://127.0.0.1:8080/api/v1/ui/file?path=$ENC"
head -c 90 /tmp/ui_gf.bin | sed 's/^/  /'
echo
echo
echo "ROLLBACK: docker rm -f vivesec-ui && docker run -d --name vivesec-ui --network host --restart unless-stopped -e PORT=8080 -e ADAPTER_URL=http://127.0.0.1:80 -e ADAPTER_DEMO_DRIVE=/storage/drives/aiboxdev/ vivesec-ui   # (state before $TS)"
