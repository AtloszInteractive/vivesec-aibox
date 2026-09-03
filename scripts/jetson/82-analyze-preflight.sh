#!/usr/bin/env bash
# Read-only preflight for the whole-document analysis deploy (#analyze).
# Reports what the live containers look like, so the deploy can inherit it.
set -eu

echo "=== rag ==="
docker inspect vivesec-rag \
  --format '  image={{.Image}}{{"\n"}}  binds={{.HostConfig.Binds}}' | cut -c1-200
echo -n "  health: "; curl -fsS -m 10 http://127.0.0.1:8090/health || echo FAILED
echo
echo -n "  stats : "
curl -fsS -m 20 -H "X-API-Key: $(cat ~/prod_rag_api_key.txt)" http://127.0.0.1:8090/stats || echo FAILED
echo

echo "=== adapter ==="
docker inspect vivesec-adapter \
  --format '  image={{.Image}}{{"\n"}}  binds={{.HostConfig.Binds}}' | cut -c1-200
docker inspect vivesec-adapter \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^ADAPTER_(GEN_MODEL|THINK|NUM_PREDICT|NUM_CTX|ANALYZE|DEMO|TENANT|DRIVE_PREFIX)=' \
  | sed 's/^/  /'

echo "=== ui ==="
docker inspect vivesec-ui \
  --format '  image={{.Config.Image}}{{"\n"}}  workdir={{.Config.WorkingDir}}{{"\n"}}  cmd={{.Config.Cmd}}{{"\n"}}  binds={{.HostConfig.Binds}}'
docker inspect vivesec-ui \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(ADAPTER|PORT|NITRO|HOST)' | sed 's/^/  /'
echo -n "  http: "; curl -s -o /dev/null -w '%{http_code}\n' -m 10 http://127.0.0.1:8080/

echo "=== staging dirs ==="
echo "  rag-build   : $(ls ~/rag-build/rag_service/*.py 2>/dev/null | wc -l) py files"
echo "  adapter-src : $(ls ~/adapter-src/*.py 2>/dev/null | wc -l) py files"
ls -d ~/ui-app ~/ui-src 2>/dev/null | sed 's/^/  ui dir: /' || echo "  (no ui staging dir)"
