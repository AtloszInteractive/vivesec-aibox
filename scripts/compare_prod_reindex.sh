#!/usr/bin/env bash
# Two questions:
#   1. Did any document lose content in the re-index? (compare with the backup)
#   2. Does the extractor really paginate a production PDF, or is the corpus
#      simply made of one- and two-page documents?
set -u

NAME=vivesec-rag
BACKUP=$(docker exec "$NAME" sh -lc 'ls -t /data/rag_index.backup-*.db 2>/dev/null | head -1')

echo "=== backup: ${BACKUP:-<none found>} ==="
if [ -n "$BACKUP" ]; then
docker exec "$NAME" python -c "
import sqlite3
old = sqlite3.connect('file:$BACKUP?mode=ro', uri=True)
new = sqlite3.connect('file:/data/rag_index_v2.db?mode=ro', uri=True)
o = {r[0]: (r[1], r[2]) for r in old.execute('SELECT source_path, pages, chunks FROM documents WHERE file=1')}
n = {r[0]: (r[1], r[2]) for r in new.execute('SELECT source_path, pages, chunks FROM documents WHERE file=1')}
print('old files: %d   new files: %d' % (len(o), len(n)))
lost = [p for p in n if n[p][1] == 0]
print()
print('documents with NO chunks after the re-index: %d' % len(lost))
for p in lost:
    before = o.get(p)
    print('  %-62s before=%s after=%s' % (p[-62:], before, n[p]))
print()
worse = [(p, o[p], n[p]) for p in n if p in o and n[p][1] < o[p][1]]
print('documents that lost chunks: %d' % len(worse))
for p, ob, nb in sorted(worse, key=lambda x: x[1][1] - x[2][1], reverse=True)[:15]:
    print('  %-58s %s -> %s' % (p[-58:], ob, nb))
print()
gained = [(p, o[p], n[p]) for p in n if p in o and n[p][0] > o[p][0]]
print('documents that gained pages: %d' % len(gained))
for p, ob, nb in gained:
    print('  %-58s pages %d -> %d' % (p[-58:], ob[0], nb[0]))
missing = sorted(set(o) - set(n))
print()
print('documents present before but not now: %d' % len(missing))
for p in missing[:10]:
    print('  %s' % p)
old.close(); new.close()
"
fi

echo
echo "=== what does the extractor see for a multi-page production PDF? ==="
docker exec "$NAME" python -c "
import sys
sys.path.insert(0, '/app/rag_service')
import extract
for path in ['/storage/drives/legal/contracts/Vestkraft_Supply_Agreement_2025.pdf',
             '/storage/drives/legal/contracts/Vestkraft_PAC_Protocol_signed.pdf',
             '/storage/drives/legal/audit/Pentest_Report_2025.pdf']:
    try:
        raw = open(path, 'rb').read()
    except OSError as exc:
        print('%-52s NOT ON DISK (%s)' % (path[-52:], exc.strerror))
        continue
    pages = extract.extract_pages(raw, path)
    words = sum(len(t.split()) for _, _, t in pages)
    print('%-52s %4d KB  %2d oldal  %6d szo' % (path[-52:], len(raw)//1024, len(pages), words))
" 2>&1 | grep -v RuntimeWarning | grep -v 'warn('
