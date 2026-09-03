#!/usr/bin/env bash
# How does the UI container run on the box, and what backend does it point at?
set -u
echo "=== vivesec-ui env ==="
docker inspect vivesec-ui --format '{{range .Config.Env}}{{println .}}{{end}}'
echo "=== vivesec-ui cmd / workdir ==="
docker inspect vivesec-ui --format 'workdir={{.Config.WorkingDir}}'
docker inspect vivesec-ui --format 'entrypoint={{range .Config.Entrypoint}}{{.}} {{end}}'
docker inspect vivesec-ui --format 'cmd={{range .Config.Cmd}}{{.}} {{end}}'
echo "=== vivesec-ui mounts ==="
docker inspect vivesec-ui --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}
{{end}}'
echo "=== vivesec-ui network / ports ==="
docker inspect vivesec-ui --format 'network={{.HostConfig.NetworkMode}}'
docker port vivesec-ui 2>/dev/null || echo "(host network, no port map)"
echo "=== does the UI actually reach the adapter? ==="
docker exec vivesec-ui sh -lc 'command -v curl >/dev/null && curl -s -o /dev/null -w "adapter status=%{http_code}\n" -X POST http://127.0.0.1:8088/api/v1/status -d "{}" || echo "(no curl in the UI image)"'
echo "=== UI served page contains which file names? ==="
curl -s http://127.0.0.1:8080/ | head -c 400; echo
