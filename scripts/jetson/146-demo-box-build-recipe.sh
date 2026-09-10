#!/usr/bin/env bash
# How is this box actually built/deployed? Read-only.
set -u

echo "=== deploy scripts in home ==="
ls -1 ~/*.sh 2>/dev/null
echo

echo "=== aibox-provision ==="
ls -1 ~/aibox-provision 2>/dev/null | head -30
echo

echo "=== where are the Dockerfiles ==="
find ~ -maxdepth 4 -name 'Dockerfile*' -not -path '*/node_modules/*' 2>/dev/null | head -20
echo

echo "=== adapter-src contents (non-.py) ==="
ls -la ~/adapter-src 2>/dev/null | head -30
echo

echo "=== rag-build contents ==="
ls -la ~/rag-build 2>/dev/null | head -20
echo

echo "=== ui-src contents ==="
ls -la ~/ui-src 2>/dev/null | head -20
echo

echo "=== 60-run-containers.sh (the container recipe) ==="
sed -n '1,120p' ~/aibox-provision/60-run-containers.sh 2>/dev/null || echo "(not found)"
