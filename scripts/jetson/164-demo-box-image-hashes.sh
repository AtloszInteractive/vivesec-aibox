#!/usr/bin/env bash
# Hashes of the sources INSIDE the running containers, so the workspace can be
# diffed against what is actually serving.
set -u
echo "=== adapter (running image) ==="
docker exec vivesec-adapter sh -c 'sha256sum /app/adapter/*.py' 2>/dev/null | sed 's#/app/adapter/##'
echo
echo "=== rag_service (running image) ==="
docker exec vivesec-rag sh -c 'sha256sum /app/rag_service/*.py' 2>/dev/null | sed 's#/app/rag_service/##'
echo
echo "=== poc (running rag image) ==="
docker exec vivesec-rag sh -c 'sha256sum /app/poc/*.py' 2>/dev/null | sed 's#/app/poc/##'
