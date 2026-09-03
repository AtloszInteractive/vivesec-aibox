#!/usr/bin/env bash
# Rebuild and redeploy the rag_service and the adapter after a source change.
#
# Expects the changed sources to have been copied in already:
#   ~/rag-build/rag_service/{store.py,sqlite_store.py,...}
#   ~/adapter-src/{confidence.py,...}
#
# Env and volumes are inherited from the RUNNING containers, so RAG_API_KEY,
# RAG_MIN_SCORE and the bind mounts survive the recreate. The index lives on a
# bind mount, so no re-ingest is needed.
set -eu

echo "=== build rag image ==="
cd ~/rag-build
docker build -q -f rag_service/Dockerfile -t vivesec-rag:latest . | tail -1

echo "=== build adapter image ==="
# Build context is the adapter directory itself (see adapter/Dockerfile).
docker build -q -f ~/adapter-src/Dockerfile -t vivesec-adapter:latest ~/adapter-src | tail -1

recreate() {
    local name="$1"
    local image envargs=() bindargs=() e b
    image=$(docker inspect -f '{{.Config.Image}}' "$name")
    while IFS= read -r e; do [ -n "$e" ] && envargs+=( -e "$e" ); done \
        < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$name" \
            | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED)=')
    while IFS= read -r b; do [ -n "$b" ] && bindargs+=( -v "$b" ); done \
        < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$name")
    docker stop "$name" >/dev/null
    docker rm "$name" >/dev/null
    docker run -d --name "$name" --network host --restart unless-stopped \
        "${envargs[@]}" "${bindargs[@]}" "$image" >/dev/null
    echo "  $name recreated on $(docker inspect -f '{{.Image}}' "$name" | cut -c8-19)"
}

echo "=== recreate containers ==="
recreate vivesec-rag
recreate vivesec-adapter
sleep 8

echo "=== verify ==="
echo -n "  rag  /health : "; curl -s http://127.0.0.1:8090/health
echo
echo -n "  adapter status: "; curl -s -X POST http://127.0.0.1:8088/api/v1/status -d '{}' | head -c 160
echo
echo -n "  stats        : "
curl -s -H "X-API-Key: $(cat ~/prod_rag_api_key.txt)" http://127.0.0.1:8090/stats
echo
