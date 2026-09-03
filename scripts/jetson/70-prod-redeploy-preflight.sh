#!/usr/bin/env bash
# Pre-flight for the production redeploy: is the source corpus complete, is the
# API key present, and how big is the index we are about to replace?
set -u

echo "=== source corpus ==="
MANIFEST=/home/aibox/demo-corpus/out/manifest.jsonl
if [ ! -f "$MANIFEST" ]; then
  echo "  MISSING: $MANIFEST"
  exit 1
fi
total=$(wc -l < "$MANIFEST")
echo "  manifest entries: $total"

missing=0
while IFS= read -r rel; do
  [ -f "/home/aibox/demo-corpus/out/$rel" ] || { echo "  missing file: $rel"; missing=$((missing+1)); }
done < <(python3 -c "
import json
for line in open('$MANIFEST'):
    line=line.strip()
    if line:
        print(json.loads(line)['local_path'])
")
echo "  missing files: $missing"
du -sh /home/aibox/demo-corpus/out/drives 2>/dev/null | sed 's/^/  corpus size: /'

echo
echo "=== production key ==="
if [ -f /home/aibox/prod_rag_api_key.txt ]; then
  echo "  ~/prod_rag_api_key.txt present"
else
  echo "  MISSING ~/prod_rag_api_key.txt"
fi

echo
echo "=== index we are replacing ==="
ls -lh /data/rag/ 2>/dev/null | sed 's/^/  /'
echo -n "  live stats: "
curl -fsS -m 10 -H "X-API-Key: $(cat /home/aibox/prod_rag_api_key.txt 2>/dev/null)" \
  http://127.0.0.1:8090/stats || echo "(unreachable)"
echo

echo
echo "=== disk ==="
df -h /data | tail -1 | sed 's/^/  /'
