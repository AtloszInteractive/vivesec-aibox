#!/usr/bin/env bash
# Verify that every rag_service container still holds a non-empty extract.py
# matching its image (a `docker cp` accident is invisible until a restart).
set -u
for c in vivesec-rag vivesec-rag-demo vivesec-rag-eval vivesec-rag-eval-sub; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    printf '%-22s (nem fut)\n' "$c"
    continue
  fi
  size=$(docker exec "$c" wc -c /app/rag_service/extract.py 2>/dev/null | awk '{print $1}')
  sha=$(docker exec "$c" sha256sum /app/rag_service/extract.py 2>/dev/null | awk '{print $1}')
  printf '%-22s meret=%-6s sha=%s\n' "$c" "${size:-?}" "${sha:0:16}"
done
