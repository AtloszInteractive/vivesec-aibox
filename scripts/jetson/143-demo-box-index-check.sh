#!/usr/bin/env bash
# Why does the adapter report a different index than 127.0.0.1:8090?
set -u

echo "=== adapter RAG_URL ==="
docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^RAG_URL=' || echo "(unset)"
echo

echo "=== rag container config ==="
docker inspect vivesec-rag --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E 'RAG_PORT|RAG_INDEX_PATH|RAG_HOST|RAG_STORE_BACKEND|RAG_MIN_SCORE' || echo "(none)"
docker inspect vivesec-rag --format '{{.HostConfig.NetworkMode}}'
echo

echo "=== listening rag-ish ports ==="
ss -ltnp 2>/dev/null | grep -E ':(8090|8091|8092|8093|8094|8095)\b' || echo "(none visible without privileges)"
echo

echo "=== stats straight from the adapter's RAG_URL ==="
RAG_URL=$(docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^RAG_URL=//p')
RAG_URL=${RAG_URL:-http://127.0.0.1:8090}
echo "adapter uses: $RAG_URL"
curl -s "$RAG_URL/stats" | head -c 1200
echo
echo

echo "=== index files on disk ==="
ls -la /data/rag/ 2>/dev/null | head -20
echo

echo "=== adapter status index block ==="
curl -s http://127.0.0.1:80/api/v1/status | tr ',' '\n' | grep -A2 -E 'index|mirror' | head -20
