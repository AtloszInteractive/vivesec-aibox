#!/usr/bin/env bash
# Where do the 2231 pages of the evaluation index come from, and how many
# documents in either corpus extracted to nothing at all?
set -u

docker exec vivesec-rag-pagefix2 python -c "
import sqlite3
con = sqlite3.connect('file:/data/rag_index_pagefix2.db?mode=ro', uri=True)
rows = con.execute('SELECT source_path, pages, chunks FROM documents WHERE file=1').fetchall()
pdfs = [r for r in rows if r[0].lower().endswith('.pdf')]
others = [r for r in rows if not r[0].lower().endswith('.pdf')]
print('=== ERTEKELO KORPUSZ (govdocs, 240 dok) ===')
print('osszes oldal: %d' % sum(r[1] for r in rows))
print('  ebbol PDF (%d dok): %d oldal' % (len(pdfs), sum(r[1] for r in pdfs)))
print('  nem-PDF  (%d dok): %d oldal' % (len(others), sum(r[1] for r in others)))
print()
print('a 10 legtobb oldalas dokumentum:')
for sp, p, c in sorted(rows, key=lambda r: -r[1])[:10]:
    print('  %5d oldal %6d chunk  %s' % (p, c, sp[-52:]))
print()
buckets = {'1 oldal': 0, '2-5': 0, '6-20': 0, '21-50': 0, '50+': 0}
for _, p, _ in rows:
    if p <= 1: buckets['1 oldal'] += 1
    elif p <= 5: buckets['2-5'] += 1
    elif p <= 20: buckets['6-20'] += 1
    elif p <= 50: buckets['21-50'] += 1
    else: buckets['50+'] += 1
print('oldalszam-eloszlas:')
for k, v in buckets.items():
    print('  %-9s %3d dokumentum' % (k, v))
print()
empty = [r for r in rows if r[2] == 0]
print('SZOVEG NELKULI dokumentumok (0 chunk): %d' % len(empty))
for sp, p, c in empty:
    print('  %s' % sp)
con.close()
"
