#!/usr/bin/env bash
# Verifies the skip reason is now persisted and queryable in production.
set -eu
KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
      | sed -n 's/^RAG_API_KEY=//p')

echo "=== /stats (nyilvanos: csak darabszam) ==="
curl -fsS -m 20 http://127.0.0.1:8090/stats
echo
echo
echo "=== /index/skipped (hitelesitett: utvonalak) ==="
curl -fsS -m 20 -X POST http://127.0.0.1:8090/index/skipped \
  -H 'Content-Type: application/json' -H "X-API-Key: $KEY" -d '{}'
echo
echo
echo "=== hitelesites nelkul (401 kell) ==="
curl -s -o /dev/null -w 'HTTP %{http_code}\n' -X POST http://127.0.0.1:8090/index/skipped \
  -H 'Content-Type: application/json' -d '{}'
echo
echo "=== az adatbazisban ==="
docker exec vivesec-rag python -c "
import sqlite3
con = sqlite3.connect('file:/data/rag_index_v3.db?mode=ro', uri=True)
print('%-58s %8s %7s %8s  %s' % ('source_path','indexed','chunks','pages','skip_reason'))
for sp, idx, ch, pg, sr in con.execute(
        'SELECT source_path, indexed, chunks, pages, skip_reason FROM documents '
        'WHERE file=1 AND (skip_reason IS NOT NULL OR chunks=0)'):
    print('%-58s %8s %7d %8d  %s' % (sp[-58:], idx, ch, pg, sr))
n = con.execute('SELECT COUNT(*) FROM documents WHERE file=1 AND indexed=1').fetchone()[0]
print()
print('kereshetoen indexelt dokumentum: %d' % n)
con.close()
"
