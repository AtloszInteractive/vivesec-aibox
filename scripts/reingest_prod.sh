#!/usr/bin/env bash
# Re-ingests the production corpus, reading the API key from the running
# container so it is never passed through the shell that invokes this.
set -eu

KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
      | sed -n 's/^RAG_API_KEY=//p')
cd /home/aibox/demo-corpus
time python3 ingest.py --rag-url http://127.0.0.1:8090 --api-key "$KEY" 2>&1 | tail -8
