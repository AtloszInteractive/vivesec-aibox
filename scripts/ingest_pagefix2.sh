#!/usr/bin/env bash
# Re-indexes the 240-document evaluation corpus into the pdfminer-pagination
# instance. The content timeout is raised well past the slowest document
# measured (989 s) so corpus parity with the baseline is not lost to timeouts.
set -eu

cd /home/aibox/govdocs-eval
python3 ingest_to_rag.py \
  --manifest corpus_manifest_subset.jsonl \
  --root selected \
  --rag-url http://127.0.0.1:8095 \
  --api-key "$(cat eval_api_key.txt)" \
  --content-timeout 2400
