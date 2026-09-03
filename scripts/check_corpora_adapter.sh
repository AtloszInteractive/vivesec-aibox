#!/usr/bin/env bash
# The first smoke query returned nothing -- check the real corpus ids, and find
# out on which port the adapter actually listens before concluding it is down.
set -u
KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
      | sed -n 's/^RAG_API_KEY=//p')

echo "=== korpuszok es dokumentumszamuk ==="
docker exec vivesec-rag python -c "
import sqlite3
con = sqlite3.connect('file:/data/rag_index_v3.db?mode=ro', uri=True)
for cid, n in con.execute('SELECT corpus_id, COUNT(*) FROM documents WHERE file=1 GROUP BY corpus_id'):
    print('  %-24s %d dokumentum' % (cid, n))
con.close()
"

echo
echo "=== kereses a HR korpuszban ==="
HR=$(docker exec vivesec-rag python -c "
import sqlite3
con = sqlite3.connect('file:/data/rag_index_v3.db?mode=ro', uri=True)
row = con.execute(\"SELECT corpus_id FROM documents WHERE source_path LIKE '%/hr/%' LIMIT 1\").fetchone()
print(row[0] if row else '')
")
echo "  corpus_id=$HR"
curl -fsS -m 30 -X POST http://127.0.0.1:8090/rag/search_context \
  -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
  -d "{\"corpus_id\":\"$HR\",\"question\":\"utazasi koltsegterites napidij\",\"top_k\":3}" \
  | python3 -c "
import json,sys
ctx = (json.load(sys.stdin).get('contexts') or [])
print('  talalatok: %d' % len(ctx))
for c in ctx:
    print('    %.3f  %s (oldal %s)' % (c['score'], c['source_path'][-50:], c['page_number']))
"

echo
echo "=== adapter portok ==="
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-adapter | grep -iE 'port|url' || true
ss -ltnp 2>/dev/null | grep -E ':(80|443|8080|8000) ' || echo "  nincs 80/443/8080 figyelo"
echo
for p in 80 443 8000 8080; do
  printf '  port %-5s ' "$p"
  curl -s -o /dev/null -m 5 -w 'HTTP %{http_code}\n' "http://127.0.0.1:$p/health" || echo "nem valaszol"
done
