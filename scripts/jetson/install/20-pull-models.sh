#!/usr/bin/env bash
# ViVeSec AI Box - download the two models the stack needs.
#
# The embedding model feeds the index, the generation model answers questions.
# Both are pulled through the local Ollama service, so they land under
# /data/ollama/models (set by the systemd drop-in installed in the ollama phase).
#
#   bash 20-pull-models.sh bge-m3:latest qwen3.6:35b
set -euo pipefail

api=http://127.0.0.1:11434
embed_model="${1:?usage: $0 EMBED_MODEL GEN_MODEL}"
gen_model="${2:?usage: $0 EMBED_MODEL GEN_MODEL}"

curl -fsS --retry 30 --retry-connrefused --retry-delay 2 "$api/api/version" >/dev/null || {
  echo "Ollama is not answering on $api - run the ollama phase first." >&2
  exit 1
}

models_dir="$(systemctl show ollama -p Environment --value | tr ' ' '\n' | tr -d '"' |
  awk -F= '/^OLLAMA_MODELS=/ {print $2}')"
[[ $models_dir == /data/ollama/models ]] || {
  echo "Unexpected OLLAMA_MODELS: ${models_dir:-<unset>} (expected /data/ollama/models)" >&2
  exit 1
}

for model in "$embed_model" "$gen_model"; do
  if ollama list | awk 'NR > 1 {print $1}' | grep -Fxq "$model"; then
    echo "MODEL_PRESENT $model"
    continue
  fi
  echo "Pulling $model (this can take a while on the first run)..."
  ollama pull "$model"
  ollama list | awk 'NR > 1 {print $1}' | grep -Fxq "$model" || {
    echo "Model still missing after pull: $model" >&2
    exit 1
  }
done

free_bytes="$(df --output=avail -B1 /data | tail -n 1 | tr -d ' ')"
printf 'AIBOX_MODELS_READY embed=%s gen=%s models_dir=%s data_free_bytes=%s\n' \
  "$embed_model" "$gen_model" "$models_dir" "$free_bytes"
