#!/usr/bin/env bash
# Verifies that the deployed production container really runs the pdfminer
# pagination path, and shows what the extractor reports for the PDFs actually in
# the production corpus. Read-only.
set -u

NAME=vivesec-rag

echo "=== image ==="
docker inspect -f '{{.Config.Image}}  created={{.Created}}' "$NAME"

echo
echo "=== extract.py in the running container ==="
docker exec "$NAME" grep -c '_extract_pdf' /app/rag_service/extract.py \
  && echo "  _extract_pdf present" || echo "  _extract_pdf MISSING (old image!)"
docker exec "$NAME" grep -n 'RAG_QUERY_SPLIT\|def split_question' /app/rag_service/query_split.py 2>/dev/null \
  | head -3 || echo "  query_split.py MISSING"

echo
echo "=== env ==="
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" \
  | grep -E 'RAG_MIN_SCORE|RAG_QUERY_SPLIT|RAG_INDEX_PATH'

echo
echo "=== pages per document in the production index (PDFs first) ==="
docker exec "$NAME" python -c "
import sqlite3, os
db = os.environ.get('RAG_INDEX_PATH', '/data/rag_index.db')
con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
rows = con.execute('SELECT source_path, pages, chunks FROM documents WHERE file=1').fetchall()
pdfs = [r for r in rows if r[0].lower().endswith('.pdf')]
print('documents: %d   of which PDF: %d' % (len(rows), len(pdfs)))
print('total pages: %d   total chunks: %d' % (sum(r[1] for r in rows), sum(r[2] for r in rows)))
print()
print('%-58s %6s %7s' % ('PDF', 'pages', 'chunks'))
for sp, p, c in sorted(pdfs, key=lambda r: -r[2])[:25]:
    print('%-58s %6d %7d' % (sp[-58:], p, c))
print()
top = sorted(rows, key=lambda r: -r[2])[:10]
print('%-58s %6s %7s' % ('largest documents overall', 'pages', 'chunks'))
for sp, p, c in top:
    print('%-58s %6d %7d' % (sp[-58:], p, c))
con.close()
"
