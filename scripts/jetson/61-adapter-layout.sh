#!/usr/bin/env bash
# Where does the adapter keep its python modules inside the container?
set -u
echo "=== workdir / cmd ==="
docker inspect vivesec-adapter --format '{{.Config.WorkingDir}}'
docker inspect vivesec-adapter --format '{{join .Config.Cmd " "}}'
echo "=== mounts ==="
docker inspect vivesec-adapter --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}
{{end}}'
echo "=== / listing ==="
docker exec vivesec-adapter ls -la /
echo "=== python module search ==="
docker exec vivesec-adapter sh -lc 'find / -name llm.py -maxdepth 6 2>/dev/null | head -20'
