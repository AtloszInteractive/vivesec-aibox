#!/usr/bin/env bash
# Read-only: what Ollama version runs here, how it was installed, what is current upstream.
set -u
echo "== local =="
ollama --version 2>&1 | head -3
echo "binary: $(command -v ollama)"
echo
echo "== systemd unit =="
systemctl cat ollama 2>/dev/null | sed -n '1,30p'
echo
echo "== upstream latest =="
curl -s https://api.github.com/repos/ollama/ollama/releases/latest -o /tmp/rel.json
python3 - <<'PY'
import json
try:
    d = json.load(open('/tmp/rel.json'))
    print("tag:", d.get("tag_name"), "published:", d.get("published_at"))
    names = [a["name"] for a in d.get("assets", [])]
    print("arm64 assets:", [n for n in names if "arm64" in n or "aarch64" in n])
except Exception as e:
    print("release lookup failed:", e)
PY
echo
echo "== loaded models right now =="
ollama ps
echo
echo "== free memory =="
free -g | sed -n '1,3p'
