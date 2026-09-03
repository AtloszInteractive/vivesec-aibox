#!/usr/bin/env bash
# Republish the host's native (GPU) Ollama on the docker0 gateway so bridged
# containers can reach it via `host.docker.internal:host-gateway`, WITHOUT
# re-binding the host daemon itself (it stays loopback-only for the LAN).
#
# NOTE: `host-gateway` resolves to the DEFAULT bridge gateway (docker0,
# 172.17.0.1) even for containers on a compose network — binding the compose
# network's own gateway is not enough.
set -eu
BIND="${BIND:-172.17.0.1}"
docker rm -f ollama-relay >/dev/null 2>&1 || true
docker run -d --name ollama-relay --network host --restart unless-stopped \
  alpine/socat "TCP-LISTEN:11434,fork,reuseaddr,bind=${BIND}" TCP:127.0.0.1:11434 >/dev/null
sleep 4
echo "--- listeners ---"
ss -tln | grep 11434
echo "--- reachability from the service container ---"
# -i is required: the heredoc arrives on stdin and docker exec does not
# forward it otherwise.
docker exec -i colearn-rag-pageindexes-rag-service-1 python - <<'PY'
import json, urllib.request
body = json.dumps({"model": "bge-m3", "input": ["relay check"]}).encode()
req = urllib.request.Request("http://host.docker.internal:11434/api/embed",
                             data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as resp:
    dims = len(json.loads(resp.read())["embeddings"][0])
print(f"OK, embedding dimension = {dims}")
PY
