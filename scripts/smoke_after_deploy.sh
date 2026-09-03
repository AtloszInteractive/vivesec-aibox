#!/usr/bin/env bash
# End-to-end smoke check after the production deploy: retrieval works, the query
# split is active, and the containers around the RAG service are healthy.
set -u
KEY=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' vivesec-rag \
      | sed -n 's/^RAG_API_KEY=//p')

echo "=== konteneRek ==="
docker ps --filter name=vivesec --format '{{.Names}}\t{{.Status}}'

echo
echo "=== egyszeru kereses ==="
curl -fsS -m 30 -X POST http://127.0.0.1:8090/rag/search_context \
  -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
  -d '{"corpus_id":"hr-3a6ac1cd","question":"utazasi szabalyzat napidij","top_k":3}' \
  | python3 -c "
import json,sys
r = json.load(sys.stdin)
ctx = r.get('contexts') or []
print('talalatok: %d' % len(ctx))
for c in ctx:
    print('  %.3f  %s  (oldal %s)' % (c['score'], c['source_path'][-52:], c['page_number']))
"

echo
echo "=== osszekapcsolt kerdes (szetvagas) ==="
curl -fsS -m 30 -X POST http://127.0.0.1:8090/rag/search_context \
  -H 'Content-Type: application/json' -H "X-API-Key: $KEY" \
  -d '{"corpus_id":"legal-c9902b93","question":"Mit tartalmaz a Vestkraft szallitasi szerzodes, es milyen adatvedelmi kotelezettsegek szerepelnek a feldolgozasi megallapodasban?","top_k":5}' \
  | python3 -c "
import json,sys
r = json.load(sys.stdin)
ctx = r.get('contexts') or []
print('talalatok: %d, kulonbozo dokumentum: %d' % (ctx.__len__(), len({c['source_path'] for c in ctx})))
for c in ctx:
    print('  %.3f  %s' % (c['score'], c['source_path'][-56:]))
"

echo
echo "=== adapter ==="
curl -s -m 10 -o /dev/null -w 'adapter HTTP %{http_code}\n' http://127.0.0.1:80/health || true
