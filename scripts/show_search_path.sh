#!/usr/bin/env bash
# Print the search path of a PageIndexes image, to see how the final contexts
# are selected (top_k slice? score floor? token budget?).
set -eu
IMAGE="${1:-sseres/pageindexes-rag-service:0.93}"
docker run --rm --entrypoint sh "$IMAGE" -c \
  'sed -n "/async def search/,/return {/p" /app/app/retrieval/service.py'
