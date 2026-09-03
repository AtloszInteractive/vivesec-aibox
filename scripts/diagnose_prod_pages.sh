#!/usr/bin/env bash
# 1. Is the previous production index still intact for rollback?
# 2. Why did one PDF produce no text at all?
# 3. How big are the production PDFs really, compared with the evaluation corpus?
set -u

echo "=== index files on the production volume ==="
docker exec vivesec-rag sh -lc 'ls -lh /data/*.db 2>/dev/null'

echo
echo "=== document counts in each index ==="
docker exec vivesec-rag python -c "
import sqlite3, glob
for path in sorted(glob.glob('/data/*.db')):
    try:
        con = sqlite3.connect('file:%s?mode=ro' % path, uri=True)
        files = con.execute('SELECT COUNT(*) FROM documents WHERE file=1').fetchone()[0]
        pages = con.execute('SELECT COUNT(*) FROM pages').fetchone()[0]
        chunks = con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
        print('%-42s files=%-5d pages=%-6d chunks=%d' % (path, files, pages, chunks))
        con.close()
    except Exception as exc:
        print('%-42s unreadable: %s' % (path, exc))
"

CORPUS=/home/aibox/demo-corpus/out
echo
echo "=== the PDF that yielded nothing ==="
PAC=$(find "$CORPUS" -name 'Vestkraft_PAC_Protocol_signed.pdf' 2>/dev/null | head -1)
if [ -n "$PAC" ]; then
  ls -lh "$PAC"
  docker cp "$PAC" vivesec-rag:/tmp/pac.pdf
  docker exec vivesec-rag python -c "
import sys
sys.path.insert(0, '/app/rag_service')
import extract, io
raw = open('/tmp/pac.pdf','rb').read()
print('bytes:', len(raw))
from pdfminer.high_level import extract_text
t = extract_text(io.BytesIO(raw))
print('pdfminer chars:', len(t.strip()), 'form feeds:', t.count(chr(12)))
md = extract._extract_markitdown(raw, '/tmp/pac.pdf')
print('markitdown chars:', len(md.strip()))
print('extract_pages ->', len(extract.extract_pages(raw, '/tmp/pac.pdf')), 'pages')
" 2>&1 | grep -v RuntimeWarning | grep -v 'warn('
else
  echo "not found under $CORPUS"
fi

echo
echo "=== size of the production PDFs vs the evaluation corpus ==="
echo "production:"
find "$CORPUS" -name '*.pdf' -printf '%s %p\n' 2>/dev/null | sort -rn | head -5 \
  | awk '{printf "  %6d KB  %s\n", $1/1024, $2}'
echo "  count: $(find "$CORPUS" -name '*.pdf' 2>/dev/null | wc -l)"
echo "evaluation (govdocs subset):"
find /home/aibox/govdocs-eval/selected -name '*.pdf' -printf '%s %p\n' 2>/dev/null | sort -rn | head -5 \
  | awk '{printf "  %6d KB  %s\n", $1/1024, $2}'
echo "  count: $(find /home/aibox/govdocs-eval/selected -name '*.pdf' 2>/dev/null | wc -l)"
