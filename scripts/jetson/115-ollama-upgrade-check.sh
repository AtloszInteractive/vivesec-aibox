#!/usr/bin/env bash
# Read-only preflight for the Ollama upgrade: tooling + the exact asset we would fetch.
set -u
VER="${1:-v0.32.15}"
echo "== tooling =="
echo "zstd:  $(command -v zstd || echo MISSING)"
echo "tar:   $(tar --version | head -1)"
tar --help 2>/dev/null | grep -q -- '--zstd' && echo "tar --zstd: yes" || echo "tar --zstd: no (will pipe through zstd)"
echo "curl:  $(curl --version | head -1)"
echo
echo "== disk =="
df -h /data /usr/local | sed -n '1,4p'
echo
echo "== asset for $VER (jetpack5, L4T R35 = JetPack 5.x) =="
curl -s "https://api.github.com/repos/ollama/ollama/releases/tags/$VER" -o /tmp/rel.json
python3 - "$VER" <<'PY'
import json, sys
d = json.load(open('/tmp/rel.json'))
print("release:", d.get("tag_name"), d.get("published_at"))
for a in d.get("assets", []):
    if "jetpack5" in a["name"] or a["name"] == "ollama-linux-arm64.tar.zst":
        print("  %-40s %8.1f MB" % (a["name"], a["size"]/1e6))
        print("   url:", a["browser_download_url"])
PY
