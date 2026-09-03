#!/usr/bin/env bash
# Read-only survey of a box before the before/after experiment.
# Tells us which build is running, what the corpus is, and where the adapter
# listens — the demo box fronts on :80, the dev box on :8088.
set -eu

echo "=== host ==="
hostname; free -g | sed -n '1,2p'; df -h / /data 2>/dev/null | tail -2

echo
echo "=== containers ==="
docker ps --format '{{.Names}}  {{.Image}}  {{.Status}}' | grep -i vivesec || echo "(none)"

echo
echo "=== ollama ==="
ollama list

echo
echo "=== adapter ==="
for p in 8088 80; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 8 -X POST \
         -H 'Content-Type: application/json' -d '{}' \
         "http://127.0.0.1:$p/api/v1/status" || true)
  echo "  port $p -> HTTP $code"
done
docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
  | grep -E '^ADAPTER_(GEN_MODEL|THINK|NUM_PREDICT|NUM_CTX|ANALYZE|PORT|TLS|TENANT_ID|DRIVE_PREFIX)=' \
  | sed 's/^/  /' || echo "  (no adapter container)"
echo "  binds: $(docker inspect vivesec-adapter --format '{{.HostConfig.Binds}}' 2>/dev/null)"
echo "  image: $(docker inspect vivesec-adapter --format '{{.Image}}' 2>/dev/null | cut -c8-19)"

echo
echo "=== rag ==="
curl -fsS -m 10 http://127.0.0.1:8090/health 2>/dev/null || echo "  (no /health)"
echo
echo "  image: $(docker inspect vivesec-rag --format '{{.Image}}' 2>/dev/null | cut -c8-19)"
echo "  binds: $(docker inspect vivesec-rag --format '{{.HostConfig.Binds}}' 2>/dev/null)"
if [ -f ~/prod_rag_api_key.txt ]; then
  echo -n "  stats: "
  curl -fsS -m 20 -H "X-API-Key: $(cat ~/prod_rag_api_key.txt)" http://127.0.0.1:8090/stats || echo FAILED
  echo
else
  echo "  (no ~/prod_rag_api_key.txt)"
fi

echo
echo "=== capability probe (which build?) ==="
KEY=$(cat ~/prod_rag_api_key.txt 2>/dev/null || echo "")
echo -n "  /rag/document_context: "
curl -s -o /dev/null -w '%{http_code} (404=old build, 400=new)\n' -m 15 -X POST \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8090/rag/document_context

echo
echo "=== ui ==="
docker inspect vivesec-ui --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
  | grep -E '^(ADAPTER_|PORT)' | sed 's/^/  /' || echo "  (no ui container)"
echo "  binds: $(docker inspect vivesec-ui --format '{{.HostConfig.Binds}}' 2>/dev/null)"

echo
echo "=== staging dirs ==="
for d in ~/rag-build ~/adapter-src ~/ui-app ~/ui-src; do
  [ -d "$d" ] && echo "  $d ($(find "$d" -maxdepth 2 -type f | wc -l) files)" || echo "  $d (missing)"
done

echo
echo "=== drives ==="
curl -s -m 10 -X POST -H 'Content-Type: application/json' \
  -d '{"path":"/storage/drives"}' http://127.0.0.1:8088/api/v1/index/get/children 2>/dev/null \
  | head -c 400 || true
curl -s -m 10 -X POST -H 'Content-Type: application/json' \
  -d '{"path":"/storage/drives"}' http://127.0.0.1:80/api/v1/index/get/children 2>/dev/null \
  | head -c 400 || true
echo
