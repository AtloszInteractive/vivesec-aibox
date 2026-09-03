#!/usr/bin/env bash
# Jetson smoke test for the ViVeSecBox simulator + AIBox mock, using the real
# bge-m3 embeddings served by the host Ollama. Run on the Jetson:
#   bash ~/sim/jetson_smoke.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SIM=sim/vivesecbox_sim.py

echo "== drives =="
python3 "$SIM" drives

echo "== query: finance =="
python3 "$SIM" query "Q4 revenue and net profit" --drive finance

echo "== query: beta dev 2 (space-named drive) =="
python3 "$SIM" query "product roadmap codename" --drive "beta dev 2"

echo "== ACL leak check: beta2 content from beta dev 2 drive (must NOT leak) =="
python3 "$SIM" query "beta2 segment isolation" --drive "beta dev 2"

echo "== delete drive 'beta dev 2' =="
python3 "$SIM" delete --drive "beta dev 2"

echo "== status after delete =="
curl -s http://127.0.0.1:8088/api/v1/status; echo
