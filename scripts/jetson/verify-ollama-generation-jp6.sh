#!/usr/bin/env bash
set -euo pipefail

api=http://127.0.0.1:11434
model="${1:-qwen3.6:35b}"
request="$(mktemp)"
response="$(mktemp)"
trap 'rm -f "$request" "$response"' EXIT

cat > "$request" <<JSON
{"model":"$model","prompt":"Reply with exactly: OK","stream":false,"think":false,"options":{"num_ctx":4096,"num_predict":8,"temperature":0}}
JSON

curl -fsS --max-time 300 "$api/api/generate" \
  -H 'Content-Type: application/json' \
  --data-binary "@$request" \
  -o "$response"

done="$(jq -r '.done' "$response")"
response_text="$(jq -r '.response // empty' "$response" | tr -d '\r\n')"
eval_count="$(jq -r '.eval_count // 0' "$response")"
[[ $done == true && -n $response_text && $eval_count -gt 0 ]] || {
  echo "Generation failed: $(cat "$response")" >&2
  exit 1
}

ps_json="$(curl -fsS "$api/api/ps")"
size_vram="$(jq -r --arg model "$model" '.models[] | select(.name == $model) | .size_vram' <<<"$ps_json")"
[[ -n $size_vram && $size_vram -gt 0 ]] || {
  echo "Model is not resident on the GPU: $ps_json" >&2
  exit 1
}

eval_rate="$(jq -r 'if .eval_duration > 0 then (.eval_count / (.eval_duration / 1000000000)) else 0 end' "$response")"
echo "OLLAMA_GENERATION_GPU_OK model=$model response=$response_text eval_count=$eval_count eval_per_s=$eval_rate vram=$size_vram"