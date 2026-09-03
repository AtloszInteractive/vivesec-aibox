#!/usr/bin/env bash
# Add an X-API-Key to the production RAG (:8090) and wire the adapter to send it.
# Envs/volumes are captured from the RUNNING containers via docker inspect, so
# every previously configured setting survives the recreate.
set -e

KEY=$(openssl rand -hex 16)
echo "$KEY" > ~/prod_rag_api_key.txt && chmod 600 ~/prod_rag_api_key.txt

recreate_with_key() {
    local name="$1"
    local image
    image=$(docker inspect -f '{{.Config.Image}}' "$name")
    local envargs=()
    while IFS= read -r e; do
        [ -n "$e" ] && envargs+=( -e "$e" )
    done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$name" | grep -v '^RAG_API_KEY=')
    local bindargs=()
    while IFS= read -r b; do
        [ -n "$b" ] && bindargs+=( -v "$b" )
    done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$name")
    docker stop "$name" >/dev/null
    docker rm "$name" >/dev/null
    docker run -d --name "$name" --network host --restart unless-stopped \
        "${envargs[@]}" -e RAG_API_KEY="$KEY" "${bindargs[@]}" "$image" >/dev/null
    echo "$name recreated ($image)"
}

recreate_with_key vivesec-rag
recreate_with_key vivesec-adapter
sleep 5

echo "--- rag /health (GET, auth-mentes):"
curl -s http://127.0.0.1:8090/health | head -c 120; echo
echo "--- rag POST kulcs NELKUL (401 kell):"
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8090/rag/search_context -d '{}'
echo "--- rag POST kulccsal (400 kell — hianyzo mezok, de auth OK):"
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "X-API-Key: $KEY" http://127.0.0.1:8090/rag/search_context -d '{}'
echo "--- adapter status (ui-ready kell):"
curl -s -X POST http://127.0.0.1:8088/api/v1/status -d '{}' | head -c 200; echo
echo "KEY_FILE=~/prod_rag_api_key.txt"
