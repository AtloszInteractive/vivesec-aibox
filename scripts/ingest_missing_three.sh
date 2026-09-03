#!/usr/bin/env bash
# Ingests the five documents that timed out, one at a time with a generous
# timeout, into the throwaway pagefix index -- to see the true per-document cost.
set -eu

cd /home/aibox/govdocs-eval
python3 /home/aibox/ingest_one.py \
  --manifest corpus_manifest_subset.jsonl \
  --root selected \
  --rag-url http://127.0.0.1:8094 \
  --api-key "$(cat eval_api_key.txt)" \
  --timeout 3600 \
  /drive_public/101/101676.pdf \
  /drive_public/101/101696.pdf \
  /drive_public/101/101706.pdf
