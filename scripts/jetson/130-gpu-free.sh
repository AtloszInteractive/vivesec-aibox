#!/usr/bin/env bash
# Free the GPU: unload every loaded model, then report.
set -u
for m in $(ollama ps | awk 'NR>1 {print $1}'); do
  echo "unloading $m"
  curl -s http://127.0.0.1:11434/api/generate \
    -d "{\"model\":\"$m\",\"prompt\":\"x\",\"keep_alive\":0,\"stream\":false,\"options\":{\"num_predict\":1}}" \
    >/dev/null 2>&1 || true
done
sleep 5
echo "--- loaded now ---"; ollama ps
echo "--- memory (GB) ---"; free -g | sed -n '2p'
