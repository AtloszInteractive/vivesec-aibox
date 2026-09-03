#!/usr/bin/env bash
# Remove the no-floor probe and the index copy it needed. The A/B instances
# (:8098 repair on, :8100 repair off, :8099 evaluation corpus) are kept.
set -eu
docker rm -f vivesec-rag-diacritics >/dev/null 2>&1 || true
docker run --rm -v /data/rag:/data alpine sh -c \
  'rm -f /data/rag_index_diacritics_probe.db*' >/dev/null
echo "--- ami maradt ---"
docker ps --format '{{.Names}} {{.Image}}' | grep vivesec-rag
echo "--- a production index érintetlen ---"
docker run --rm -v /data/rag:/data alpine ls -la /data | grep '\.db'
