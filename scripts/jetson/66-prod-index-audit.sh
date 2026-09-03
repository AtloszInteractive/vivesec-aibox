#!/usr/bin/env bash
# After an ingest: is the production index exactly the manifest, no orphans?
set -u
KEY=$(cat ~/prod_rag_api_key.txt)
echo "=== stats ==="
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8090/stats; echo
echo
echo "=== per corpus ==="
# NB: `docker exec ... python - <<EOF` needs -i, otherwise the container gets no stdin.
docker exec -i vivesec-rag python - <<'PY'
import sqlite3
c = sqlite3.connect('file:/data/rag_index.db?mode=ro', uri=True)
for cid, n, ch in c.execute('''
        select d.corpus_id, count(*),
               (select count(*) from chunks ch where ch.corpus_id = d.corpus_id)
        from documents d group by d.corpus_id order by 1'''):
    print('  %-26s %3d dok  %6d chunk' % (cid, n, ch))
print()
print('  0-chunk documents:')
rows = list(c.execute('''select doc_id, source_path from documents d
                         where not exists (select 1 from chunks ch where ch.doc_id = d.doc_id)'''))
for did, sp in rows:
    print('    %s  %s' % (did, sp))
if not rows:
    print('    (none)')
PY
echo
echo "=== orphans: in the index but not in the manifest ==="
docker exec -i vivesec-rag python - < /dev/null 2>/dev/null || true
python3 - <<'PY'
import json, subprocess, io
want = set()
with io.open('/home/aibox/demo-corpus/out/manifest.jsonl', encoding='utf-8') as f:
    for line in f:
        if line.strip():
            want.add(json.loads(line)['source_path'])
out = subprocess.run(
    ['docker', 'exec', 'vivesec-rag', 'python', '-c',
     "import sqlite3;c=sqlite3.connect('file:/data/rag_index.db?mode=ro',uri=True);"
     "print('\\n'.join(r[0] for r in c.execute('select source_path from documents')))"],
    capture_output=True, text=True)
have = {l for l in out.stdout.splitlines() if l.strip()}
orphans = sorted(have - want)
missing = sorted(want - have)
print('  manifest: %d, index: %d' % (len(want), len(have)))
print('  orphans (should be dropped): %s' % (orphans if orphans else 'none'))
print('  missing (not ingested)     : %s' % (missing if missing else 'none'))
PY
