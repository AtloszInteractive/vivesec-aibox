#!/usr/bin/env bash
# Probe the Ollama library for available qwen3.x tags (read-only, no pull).
set -u
REG="https://registry.ollama.ai/v2/library"

probe() { # repo tag
  code=$(curl -s -o /tmp/mf.json -w '%{http_code}' "$REG/$1/manifests/$2")
  if [ "$code" = "200" ]; then
    size=$(python3 -c "import json;d=json.load(open('/tmp/mf.json'));print(sum(l['size'] for l in d.get('layers',[])))" 2>/dev/null || echo "?")
    printf '  %-28s 200  %s bytes (%.1f GB)\n' "$1:$2" "$size" "$(python3 -c "print($size/1e9)" 2>/dev/null || echo 0)"
  else
    printf '  %-28s %s\n' "$1:$2" "$code"
  fi
}

for repo in qwen3.8 qwen3.7 qwen3.6; do
  echo "== $repo =="
  for tag in latest 4b 8b 14b 27b 30b 32b 35b 27b-instruct 27b-a3b 235b; do
    probe "$repo" "$tag"
  done
done
