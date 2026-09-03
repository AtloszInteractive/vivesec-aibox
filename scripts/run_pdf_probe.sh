#!/usr/bin/env bash
# Run probe_pdf_pages.py against a real govdocs PDF inside the live rag container.
set -eu
PDF="${1:-$HOME/stage_a/data/selected/102/102044.pdf}"
docker cp probe_pdf_pages.py vivesec-rag:/tmp/probe_pdf_pages.py
docker cp "$PDF" vivesec-rag:/tmp/sample.pdf
docker exec vivesec-rag python /tmp/probe_pdf_pages.py /tmp/sample.pdf
