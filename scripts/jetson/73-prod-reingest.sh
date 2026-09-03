#!/usr/bin/env bash
# Production redeploy, step 3 of 3: re-ingest the whole corpus.
#
# A full re-ingest is required, not an incremental one: the extraction now
# paginates PDFs, so every document's chunks and page numbers change.
set -eu

cd /home/aibox/demo-corpus
KEY=$(cat /home/aibox/prod_rag_api_key.txt)

python3 ingest.py \
  --manifest out/manifest.jsonl \
  --root out \
  --rag-url http://127.0.0.1:8090 \
  --api-key "$KEY"
