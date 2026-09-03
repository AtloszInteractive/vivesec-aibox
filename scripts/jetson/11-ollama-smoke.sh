#!/usr/bin/env bash
# Non-interactive Ollama smoke test on the Jetson.
# Calls the local HTTP API (avoids interactive `ollama run` TTY issues over SSH),
# measures load + generation, and prints GPU/VRAM usage via `ollama ps`.
set -euo pipefail

MODEL="${1:-qwen2.5:3b}"
API="http://127.0.0.1:11434"

echo "[smoke] model: ${MODEL}"
echo "[smoke] generating (stream=false) ..."

cat > /tmp/ollama_req.json <<EOF
{"model":"${MODEL}","prompt":"Reply with exactly: OK","stream":false}
EOF

curl -s "${API}/api/generate" -d @/tmp/ollama_req.json | jq -r '
  "[smoke] response   : " + (.response // "<none>")
+ "\n[smoke] total_dur  : " + ((.total_duration // 0) / 1e9 | tostring) + " s"
+ "\n[smoke] load_dur   : " + ((.load_duration // 0) / 1e9 | tostring) + " s"
+ "\n[smoke] eval_count : " + ((.eval_count // 0) | tostring) + " tokens"
'

echo "[smoke] ollama ps (size_vram > 0 => GPU in use):"
ollama ps

echo "[smoke] done."
