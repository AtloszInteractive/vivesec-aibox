#!/usr/bin/env bash
# Head-to-head raw generation speed, one model resident at a time.
# The 08-11 lesson: two big models loaded together made the box unreachable,
# so every model is explicitly unloaded (keep_alive 0) before the next one runs.
# Usage: bash 117-model-ab-bench.sh [MODEL_A] [MODEL_B]
set -u
A="${1:-qwen3.8:27b}"
B="${2:-qwen3.6:35b}"

unload() {
  curl -s http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"$1\",\"prompt\":\"x\",\"stream\":false,\"keep_alive\":0,\"options\":{\"num_predict\":1}}" \
    >/dev/null 2>&1 || true
  sleep 3
}

report() {
  echo "--- loaded now ---"; ollama ps
  echo "--- memory (GB) ---"; free -g | sed -n '2p'
  echo
}

for M in "$A" "$B"; do
  echo "############ $M ############"
  unload "$A"; unload "$B"
  python3 -u ~/bench_gen_speed.py "$M"
  report
  unload "$M"
done

echo "############ both unloaded ############"
report
