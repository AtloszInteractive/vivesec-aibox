#!/usr/bin/env bash
# Pre-cutover inspection of the PRODUCTION rag_service (:8090) — read only.
set -u
echo "=== adapter tenant / drive prefix ==="
docker inspect vivesec-adapter --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(ADAPTER_TENANT_ID|ADAPTER_DRIVE_PREFIX|ADAPTER_RAG_URL|RAG_URL)=' || echo "(defaults)"
echo
echo "=== rag :8090 health ==="
curl -s http://127.0.0.1:8090/health; echo
echo
echo "=== documents currently in the production index ==="
docker exec vivesec-rag python -c "
import sqlite3
c = sqlite3.connect('file:/data/rag_index.db?mode=ro', uri=True)
for cid, did, sp, tid, n in c.execute('''
        select d.corpus_id, d.doc_id, d.source_path, d.tenant_id,
               (select count(*) from chunks ch where ch.doc_id = d.doc_id)
        from documents d order by d.corpus_id, d.source_path'''):
    print('  %-20s %-14s tenant=%-10s chunks=%-5s %s' % (cid, did, tid, n, sp))
print()
for cid, n, ch in c.execute('''
        select d.corpus_id, count(*),
               (select count(*) from chunks ch where ch.corpus_id = d.corpus_id)
        from documents d group by d.corpus_id'''):
    print('  TOTAL %-20s %d dok  %d chunk' % (cid, n, ch))
"
echo
echo "=== index file size ==="
docker exec vivesec-rag sh -lc 'ls -lh /data/rag_index.db*'
