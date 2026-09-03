#!/usr/bin/env bash
# The exact run configuration of production, so the redeploy can reproduce it.
set -u
c=vivesec-rag
echo "=== image ==="
docker inspect -f '{{.Config.Image}}  id={{.Image}}' "$c"
echo "=== env (kulcs elrejtve) ==="
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" \
  | grep -E '^(RAG_|OLLAMA_|VIVESEC_)' | sed 's/^RAG_API_KEY=.*/RAG_API_KEY=<rejtve>/'
echo "=== hálózat / újraindítás / mountok ==="
docker inspect -f 'network={{.HostConfig.NetworkMode}}' "$c"
docker inspect -f 'restart={{.HostConfig.RestartPolicy.Name}}' "$c"
docker inspect -f '{{range .Mounts}}mount={{.Source}}:{{.Destination}}{{println}}{{end}}' "$c"
echo "=== adapter, ami rá épül ==="
docker inspect -f 'adapter image={{.Config.Image}} restart={{.HostConfig.RestartPolicy.Name}}' vivesec-adapter
curl -fsS -m 10 http://127.0.0.1:8090/health; echo
curl -fsS -m 10 http://127.0.0.1:8090/stats; echo
