#!/usr/bin/env bash
# Precise before/after for the production re-index: the previous index is still
# on the volume untouched, so the comparison is exact.
set -u
docker exec vivesec-rag python -c "
import sqlite3
old = sqlite3.connect('file:/data/rag_index.db?mode=ro', uri=True)
new = sqlite3.connect('file:/data/rag_index_v2.db?mode=ro', uri=True)
o = {r[0]: (r[1], r[2]) for r in old.execute('SELECT source_path, pages, chunks FROM documents WHERE file=1')}
n = {r[0]: (r[1], r[2]) for r in new.execute('SELECT source_path, pages, chunks FROM documents WHERE file=1')}
print('elozo: %d fajl, %d oldal, %d chunk' % (len(o), sum(v[0] for v in o.values()), sum(v[1] for v in o.values())))
print('uj   : %d fajl, %d oldal, %d chunk' % (len(n), sum(v[0] for v in n.values()), sum(v[1] for v in n.values())))
print()
print('--- oldalszamot nyert ---')
for p in sorted(n):
    if p in o and n[p][0] > o[p][0]:
        print('  %-56s %d -> %d oldal' % (p[-56:], o[p][0], n[p][0]))
print()
print('--- chunkot vesztett ---')
lost = [(p, o[p], n[p]) for p in n if p in o and n[p][1] < o[p][1]]
if not lost:
    print('  nincs ilyen')
for p, ob, nb in lost:
    print('  %-56s %d -> %d chunk' % (p[-56:], ob[1], nb[1]))
print()
print('--- 0 chunkos dokumentumok ---')
for p in sorted(n):
    if n[p][1] == 0:
        print('  %-56s elozoleg: %s' % (p[-56:], o.get(p, 'nem volt benne')))
print()
print('--- eltuntek / ujak ---')
print('  eltunt: %d, uj: %d' % (len(set(o)-set(n)), len(set(n)-set(o))))
old.close(); new.close()
"
