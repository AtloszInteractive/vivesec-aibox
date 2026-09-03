#!/usr/bin/env bash
# Is the empty HR result a regression, or just a query that scores below the
# 0.45 floor? Shows what the corpus contains and what several phrasings score.
set -u
KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
      | sed -n 's/^RAG_API_KEY=//p')
CORPUS=hr-6ba4fc3f

echo "=== mi van a HR korpuszban ==="
docker exec vivesec-rag python -c "
import sqlite3
con = sqlite3.connect('file:/data/rag_index_v3.db?mode=ro', uri=True)
for sp, ch in con.execute(
        \"SELECT source_path, chunks FROM documents WHERE corpus_id='$CORPUS' AND file=1 ORDER BY source_path\"):
    print('  %-62s %d chunk' % (sp[-62:], ch))
con.close()
"

echo
echo "=== ugyanazok a kerdesek, kulonbozo megfogalmazasban ==="
ask() {
  printf '  %-52s ' "\"$1\""
  curl -fsS -m 30 -X POST http://127.0.0.1:8090/rag/search_context \
    -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
    -d "{\"corpus_id\":\"$CORPUS\",\"question\":\"$1\",\"top_k\":3}" \
    | python3 -c "
import json,sys
ctx = (json.load(sys.stdin).get('contexts') or [])
if not ctx:
    print('0 talalat')
else:
    print('%d talalat, legjobb %.3f  %s' % (len(ctx), ctx[0]['score'], ctx[0]['source_path'][-40:]))
"
}
ask "utazasi koltsegterites napidij"
ask "utazási költségtérítés napidíj"
ask "Milyen a napidíj mértéke?"
ask "travel policy per diem"
ask "szabadság"
ask "Reisekostenrichtlinie"

echo
echo "=== ugyanez a REGI indexen (v2), osszehasonlitasul ==="
docker run --rm --network host -v /data/rag:/data \
  -e RAG_HOST=127.0.0.1 -e RAG_PORT=8099 -e RAG_STORE_BACKEND=sqlite \
  -e RAG_INDEX_PATH=/data/rag_index_v2.db -e RAG_API_KEY="$KEY" \
  -e RAG_MIN_SCORE=0.45 -e RAG_QUERY_SPLIT=off \
  -e OLLAMA_URL=http://localhost:11434 -e VIVESEC_BACKEND=auto \
  -d --name rag-v2-probe vivesec-rag:latest >/dev/null
sleep 6
for q in "utazási költségtérítés napidíj" "szabadság"; do
  printf '  %-52s ' "\"$q\""
  curl -fsS -m 30 -X POST http://127.0.0.1:8099/rag/search_context \
    -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
    -d "{\"corpus_id\":\"$CORPUS\",\"question\":\"$q\",\"top_k\":3}" \
    | python3 -c "
import json,sys
ctx = (json.load(sys.stdin).get('contexts') or [])
print('%d talalat%s' % (len(ctx), ', legjobb %.3f' % ctx[0]['score'] if ctx else ''))
"
done
docker rm -f rag-v2-probe >/dev/null
