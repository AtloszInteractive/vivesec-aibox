#!/usr/bin/env bash
# Read-only: which JetPack/L4T runs here, and which Ollama release added qwen3.8 support.
set -u
echo "== JetPack / L4T =="
cat /etc/nv_tegra_release 2>/dev/null | head -2
dpkg-query -W -f='${Version}\n' nvidia-l4t-core 2>/dev/null | head -1
lsb_release -ds 2>/dev/null
echo "cuda: $(ls -d /usr/local/cuda-* 2>/dev/null | tr '\n' ' ')"
echo
echo "== ollama runtime layout (for backup) =="
ls -la /usr/local/bin/ollama
ls -d /usr/local/lib/ollama 2>/dev/null && du -sh /usr/local/lib/ollama
echo
echo "== which release mentions qwen3.8 =="
curl -s "https://api.github.com/repos/ollama/ollama/releases?per_page=30" -o /tmp/rels.json
python3 - <<'PY'
import json, re
rels = json.load(open('/tmp/rels.json'))
hits = []
for r in rels:
    body = (r.get("body") or "")
    if re.search(r'qwen\s*3\.8', body, re.I):
        hits.append((r["tag_name"], r["published_at"], [l.strip() for l in body.splitlines() if re.search(r'qwen\s*3\.8', l, re.I)][:2]))
for t, p, lines in sorted(hits, key=lambda x: x[1]):
    print(t, p)
    for l in lines:
        print("   ", l[:160])
if not hits:
    print("no release note in the last 30 mentions qwen3.8 explicitly")
    print("range checked:", rels[-1]["tag_name"], "->", rels[0]["tag_name"])
PY
