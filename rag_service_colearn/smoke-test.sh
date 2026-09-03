#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
API_KEY="${API_KEY:-change-me}"

curl -fsS "${BASE_URL}/health"
printf "\n"
curl -fsS "${BASE_URL}/ready"
printf "\n"

curl -fsS -X POST "${BASE_URL}/index/upsert/directory" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"corpus_id":"smoke","path":"docs","metadata":{"source":"smoke"}}'
printf "\n"

curl -fsS -X POST "${BASE_URL}/index/upsert/file/content" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  --data-binary @- <<'JSON'
{
  "corpus_id": "smoke",
  "path": "docs/hello.txt",
  "title": "Hello smoke",
  "content_type": "text/plain",
  "content_b64": "SGVsbG8gUkFHIHNtb2tlIHRlc3QuIFRoaXMgZG9jdW1lbnQgaXMgYWJvdXQgZmFzdGFwaSwgb2xsYW1hLCBxZHJhbnQsIGFuZCBwYWdlaW5kZXhlcy4=",
  "metadata": {"source": "smoke"}
}
JSON
printf "\n"

curl -fsS -X POST "${BASE_URL}/rag/search_context" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"corpus_id":"smoke","question":"What is this document about?","top_k":3,"include_debug":true}'
printf "\n"

curl -fsS -X POST "${BASE_URL}/index/drop/tree" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"corpus_id":"smoke","path":"docs"}'
printf "\n"
