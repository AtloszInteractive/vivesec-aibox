#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:11434
model="${1:-bge-m3}"
request="$(mktemp)"
response="$(mktemp)"
trap 'rm -f "$request" "$response"' EXIT

cat > "$request" <<JSON
{"model":"$model","input":"Jetson GPU embedding smoke test"}
JSON

curl -fsS "$api/api/embed" \
  -H 'Content-Type: application/json' \
  --data-binary "@$request" \
  -o "$response"

dimension="$(jq -r '.embeddings[0] | length' "$response")"
[[ $dimension == 1024 ]] || {
  echo "Unexpected embedding dimension: $dimension" >&2
  exit 1
}

processor="$(curl -fsS "$api/api/ps" | jq -r --arg model "$model" \
  '.models[] | select(.name == ($model + ":latest")) | .details.quantization_level as $q | "\(.size_vram)/\(.size) \($q)"')"
[[ -n $processor && ${processor%%/*} -gt 0 ]] || {
  echo "Model is not resident on the GPU." >&2
  exit 1
}

echo "OLLAMA_GPU_OK model=$model dimension=$dimension vram=$processor"