#!/usr/bin/env bash
# Non-interactive Ollama GPU smoke test for the Jetson AGX Orin.
# Generates a short reply via the HTTP API (no interactive TTY, no shell-quoting
# pitfalls), then reports model placement (size_vram > 0 means GPU is in use).
set -euo pipefail

MODEL="${1:-qwen2.5:14b}"
API="http://127.0.0.1:11434"

echo "[20] Ollama service: $(systemctl is-active ollama)"
echo "[20] Generating with ${MODEL} (first load pulls the model into VRAM, may take ~1-2 min)..."

REQ="$(mktemp)"
cat > "$REQ" <<JSON
{"model": "${MODEL}", "prompt": "Reply with exactly: OK", "stream": false, "options": {"num_predict": 16}}
JSON

# total_duration / eval_count etc. come back in the JSON; jq pulls the essentials.
curl -s "${API}/api/generate" --data @"$REQ" \
  | jq '{response, total_duration_ms: (.total_duration/1e6), eval_count, eval_per_s: (.eval_count / (.eval_duration/1e9))}'
rm -f "$REQ"

echo "[20] ---- ollama ps (size_vram > 0 == GPU engaged) ----"
curl -s "${API}/api/ps" | jq '.models[] | {name, size, size_vram}'
echo "[20] done."
