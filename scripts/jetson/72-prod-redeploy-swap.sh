#!/usr/bin/env bash
# Production redeploy, step 2 of 3: swap the image and point at a fresh index.
#
# The old index file is left untouched and the old image is retagged, so a
# rollback is one command (printed at the end) rather than a restore.
set -eu

TS=$(date +%Y%m%d-%H%M)
NEW_INDEX=/data/rag_index_v2.db

echo "=== 1. keep the current image for rollback ==="
OLD_ID=$(docker inspect -f '{{.Id}}' vivesec-rag:latest)
docker tag vivesec-rag:latest "vivesec-rag:prev-$TS"
echo "  vivesec-rag:prev-$TS -> $(echo "$OLD_ID" | cut -c8-19)"

echo
echo "=== 2. promote the new image ==="
docker tag vivesec-rag:new vivesec-rag:latest
echo "  vivesec-rag:latest -> $(docker inspect -f '{{.Id}}' vivesec-rag:latest | cut -c8-19)"

echo
echo "=== 3. recreate the container on a fresh index ==="
envargs=()
while IFS= read -r e; do
  [ -n "$e" ] && envargs+=( -e "$e" )
done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
         | grep -vE '^(PATH|LANG|GPG_KEY|PYTHON_VERSION|PYTHON_SHA256|PYTHONUNBUFFERED|RAG_INDEX_PATH)=')
bindargs=()
while IFS= read -r b; do
  [ -n "$b" ] && bindargs+=( -v "$b" )
done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' vivesec-rag)

docker stop vivesec-rag >/dev/null
docker rm vivesec-rag >/dev/null
docker run -d --name vivesec-rag --network host --restart unless-stopped \
  "${envargs[@]}" -e RAG_INDEX_PATH="$NEW_INDEX" "${bindargs[@]}" \
  vivesec-rag:latest >/dev/null

sleep 8
echo -n "  health: "; curl -fsS -m 20 http://127.0.0.1:8090/health || echo "FAILED"
echo
echo -n "  stats : "; curl -fsS -m 20 -H "X-API-Key: $(cat ~/prod_rag_api_key.txt)" \
  http://127.0.0.1:8090/stats || echo "FAILED"
echo

echo
echo "=== rollback, if needed ==="
echo "  docker rm -f vivesec-rag && \\"
echo "  docker run -d --name vivesec-rag --network host --restart unless-stopped \\"
echo "    <same env> -e RAG_INDEX_PATH=/data/rag_index.db -v /data/rag:/data vivesec-rag:prev-$TS"
echo
echo "  the previous index is untouched at /data/rag/rag_index.db"
