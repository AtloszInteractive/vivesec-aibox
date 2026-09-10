#!/usr/bin/env bash
# Read-only survey of the demo box before a deploy: what runs, from which image,
# and the hash of every adapter/rag source file so the drift can be measured
# instead of guessed. Prints no environment VALUES (they carry API keys).
set -u

echo "=== host ==="
hostname
uname -m
echo

echo "=== containers ==="
docker ps -a --no-trunc | cut -c1-200
echo

echo "=== vivesec images ==="
docker images | grep -i vivesec || echo "(none)"
echo

echo "=== adapter container config ==="
for c in vivesec-adapter vivesec-rag vivesec-ui; do
  echo "--- $c"
  docker inspect "$c" --format '{{.Config.Image}} {{.Image}}' 2>/dev/null || echo "(missing)"
  docker inspect "$c" --format '{{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}' 2>/dev/null
  # env NAMES only: values hold API keys
  docker inspect "$c" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
    | sed 's/=.*$//' | sort | tr '\n' ' '
  echo
done
echo

echo "=== adapter sources on box ==="
for d in ~/adapter-src ~/adapter-jobs-src ~/vivesec_iabox_app/adapter; do
  if [ -d "$d" ]; then
    echo "--- $d"
    ls -1 "$d"/*.py 2>/dev/null | wc -l
    sha256sum "$d"/*.py 2>/dev/null | sed "s#$d/##"
  fi
done
echo

echo "=== rag sources on box ==="
for d in ~/rag-build/rag_service ~/vivesec_iabox_app/rag_service; do
  if [ -d "$d" ]; then
    echo "--- $d"
    sha256sum "$d"/*.py 2>/dev/null | sed "s#$d/##"
  fi
done
echo

echo "=== adapter sources INSIDE the running image ==="
docker exec vivesec-adapter sh -c 'sha256sum /app/adapter/*.py 2>/dev/null | sed "s#/app/adapter/##"' 2>/dev/null || echo "(exec failed)"
echo

echo "=== rag sources INSIDE the running image ==="
docker exec vivesec-rag sh -c 'sha256sum /app/rag_service/*.py 2>/dev/null | sed "s#/app/rag_service/##"' 2>/dev/null || echo "(exec failed)"
echo

echo "=== ui bundle ==="
ls -la ~/ui-src/.output 2>/dev/null | head -5
ls -la ~/ui-app/.output 2>/dev/null | head -5
echo

echo "=== disk ==="
df -h / /data 2>/dev/null | sed -n '1,6p'
echo

echo "=== ollama ==="
ollama list 2>/dev/null || echo "(ollama cli unavailable)"
