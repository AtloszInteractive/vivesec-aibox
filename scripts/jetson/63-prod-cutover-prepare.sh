#!/usr/bin/env bash
# PRODUCTION cut-over, step 1 of 2: back up, rotate the key, set the score
# floor, and remove the old three-document demo corpus.
#
# What it does NOT do: ingest. That is `ingest.py --rag-url http://127.0.0.1:8090`
# run afterwards, so the destructive part and the long part are separate.
#
#   backup   /data/rag/rag_index.backup-<ts>.db   (sqlite online backup API)
#   key      rotated into ~/prod_rag_api_key.txt, adapter recreated with it
#   score    RAG_MIN_SCORE 0.40 -> 0.45  (safety floor only; the refusal is
#            the generation layer's job — the score distributions overlap on
#            the new corpus, see demo-corpus/score_probe.py)
#   drop     /index/drop/tree on corpus finance-114ed822 at /storage/drives/finance
set -eu

MIN_SCORE=${MIN_SCORE:-0.45}
OLD_CORPUS=finance-114ed822
OLD_ROOT=/storage/drives/finance

echo "=== 1. backup ==="
TS=$(date +%Y%m%d-%H%M)
docker exec vivesec-rag python -c "
import sqlite3
src = sqlite3.connect('/data/rag_index.db')
dst = sqlite3.connect('/data/rag_index.backup-$TS.db')
with dst:
    src.backup(dst)
print('integrity:', dst.execute('pragma integrity_check').fetchone()[0])
n = dst.execute('select count(*) from documents').fetchone()[0]
c = dst.execute('select count(*) from chunks').fetchone()[0]
print('backed up: %d documents, %d chunks' % (n, c))
dst.close(); src.close()
"
docker exec vivesec-rag sh -lc "ls -lh /data/rag_index.backup-$TS.db"

echo
echo "=== 2. rotate the API key and set the score floor ==="
KEY=$(openssl rand -hex 16)
echo "$KEY" > ~/prod_rag_api_key.txt && chmod 600 ~/prod_rag_api_key.txt

recreate() {
    local name="$1"; shift
    local image envargs=() bindargs=() e b
    image=$(docker inspect -f '{{.Config.Image}}' "$name")
    while IFS= read -r e; do
        [ -n "$e" ] && envargs+=( -e "$e" )
    done < <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$name" \
             | grep -vE '^(RAG_API_KEY|RAG_MIN_SCORE)=')
    while IFS= read -r b; do
        [ -n "$b" ] && bindargs+=( -v "$b" )
    done < <(docker inspect -f '{{range .HostConfig.Binds}}{{println .}}{{end}}' "$name")
    docker stop "$name" >/dev/null
    docker rm "$name" >/dev/null
    docker run -d --name "$name" --network host --restart unless-stopped \
        "${envargs[@]}" -e RAG_API_KEY="$KEY" "$@" "${bindargs[@]}" "$image" >/dev/null
    echo "  $name recreated ($image)"
}

recreate vivesec-rag -e RAG_MIN_SCORE="$MIN_SCORE"
recreate vivesec-adapter
sleep 6
echo "  health: $(curl -s http://127.0.0.1:8090/health)"
echo "  POST without a key (expect 401): $(curl -s -o /dev/null -w '%{http_code}' \
  -X POST http://127.0.0.1:8090/rag/search_context -H 'Content-Type: application/json' -d '{}')"

echo
echo "=== 3. drop the old demo documents ==="
curl -s -X POST http://127.0.0.1:8090/index/drop/tree \
  -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
  -d "{\"corpus_id\":\"$OLD_CORPUS\",\"path\":\"$OLD_ROOT\",\"keep_exact\":false}"
echo
echo "  remaining documents:"
docker exec vivesec-rag python -c "
import sqlite3
c = sqlite3.connect('file:/data/rag_index.db?mode=ro', uri=True)
rows = list(c.execute('select corpus_id, source_path from documents order by 1,2'))
print('   (index is empty)' if not rows else '')
for cid, sp in rows:
    print('   %-20s %s' % (cid, sp))
"

echo
echo "PREPARED. Next:"
echo "  cd ~/demo-corpus && RAG_API_KEY=\$(cat ~/prod_rag_api_key.txt) \\"
echo "    python3 ingest.py --rag-url http://127.0.0.1:8090 --root out --manifest out/manifest.jsonl"
