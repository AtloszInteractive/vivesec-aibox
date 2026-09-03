#!/usr/bin/env bash
# Ingests the two oversized documents that genuinely exceeded the client timeout,
# with a timeout large enough to measure their true cost. Also restores the
# pagefix index to full 240-document parity with the baseline.
set -eu

cd /home/aibox/govdocs-eval
python3 /home/aibox/ingest_one.py \
  --manifest corpus_manifest_subset.jsonl \
  --root selected \
  --rag-url http://127.0.0.1:8094 \
  --api-key "$(cat eval_api_key.txt)" \
  --timeout 5400 \
  /drive_engineering/102/102797.txt \
  /drive_public/101/101613.txt
