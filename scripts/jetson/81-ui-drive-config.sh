#!/usr/bin/env bash
# Dev-box only: switch the UI's default drive and/or enable the drive picker.
# On a real ViVeSecBox the drive comes from the session (VVS-Drive) — the
# picker must stay OFF there (user rule, 2026-08-04).
# Usage: bash 81-ui-drive-config.sh [/storage/drives/engineering/] [picker:1|0]
set -eu
DRIVE="${1:-/storage/drives/engineering/}"
PICKER="${2:-1}"

IMAGE=$(docker inspect -f '{{.Config.Image}}' vivesec-ui)
NET=$(docker inspect -f '{{.HostConfig.NetworkMode}}' vivesec-ui)
RESTART=$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' vivesec-ui)
CMD=$(docker inspect -f '{{join .Config.Cmd " "}}' vivesec-ui)
WORKDIR=$(docker inspect -f '{{.Config.WorkingDir}}' vivesec-ui)

envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-ui \
         | grep -vE '^(PATH|NODE_VERSION|YARN_VERSION)=' \
         | grep -vE '^(ADAPTER_DEMO_DRIVE|ADAPTER_DEMO_DRIVE_PICKER)=')
envargs+=( -e "ADAPTER_DEMO_DRIVE=$DRIVE" )
[ "$PICKER" = "1" ] && envargs+=( -e "ADAPTER_DEMO_DRIVE_PICKER=1" )

bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-ui)

docker rm -f vivesec-ui >/dev/null
docker run -d --name vivesec-ui --network "$NET" --restart "$RESTART" \
  ${WORKDIR:+-w "$WORKDIR"} "${envargs[@]}" "${bindargs[@]}" "$IMAGE" $CMD >/dev/null
sleep 4
echo -n "ui http: "; curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/ || true
echo
docker exec vivesec-ui env | grep -E '^ADAPTER_DEMO' | sed 's/^/  /'
