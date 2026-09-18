#!/usr/bin/env bash
# Read-only survey of the live RAG index: spreadsheet documents by extension,
# chunk counts and skip reasons. Runs inside the vivesec-rag container.
set -u
C=${1:-vivesec-rag}
DB=$(docker inspect "$C" --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^RAG_INDEX_PATH=' | cut -d= -f2)
echo "container=$C index=$DB"
docker exec -i "$C" python3 - "$DB" <<'PY'
import sqlite3, sys, os, collections
db = sys.argv[1]
con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
rows = con.execute("SELECT source_path, chunks, pages, indexed, skip_reason, size FROM documents WHERE file=1").fetchall()
by = collections.defaultdict(lambda: [0, 0, 0, 0, collections.Counter()])
for p, ch, pg, idx, skip, size in rows:
    ext = os.path.splitext(p)[1].lower() or "(none)"
    b = by[ext]
    b[0] += 1
    b[1] += ch or 0
    if (ch or 0) == 0:
        b[2] += 1
    if skip:
        b[3] += 1
        b[4][skip] += 1
print("%-8s %6s %8s %8s %8s  %s" % ("ext", "docs", "chunks", "0chunk", "skipped", "skip_reasons"))
for ext, b in sorted(by.items(), key=lambda kv: -kv[1][0]):
    print("%-8s %6d %8d %8d %8d  %s" % (ext, b[0], b[1], b[2], b[3], dict(b[4])))
print()
print("--- spreadsheet docs (first 40) ---")
for p, ch, pg, idx, skip, size in rows:
    if os.path.splitext(p)[1].lower() in (".xlsx", ".xls", ".xlsm", ".csv"):
        print("chunks=%-5s pages=%-3s indexed=%s skip=%s size=%s  %s" % (ch, pg, idx, skip, size, p))
try:
    # sample chunk text from one xlsx to see NaN density
    r = con.execute("""SELECT c.text FROM chunks c JOIN documents d ON d.doc_id=c.doc_id
                        WHERE lower(d.source_path) LIKE '%.xlsx' LIMIT 3""").fetchall()
    for (t,) in r:
        print("--- xlsx chunk sample (%d chars, NaN=%d, pipes=%d) ---" % (len(t), t.count("NaN"), t.count("|")))
        print(t[:500])
    tot = con.execute("""SELECT count(*), sum(length(c.text)), sum((length(c.text)-length(replace(c.text,'NaN','')))/3)
                        FROM chunks c JOIN documents d ON d.doc_id=c.doc_id WHERE lower(d.source_path) LIKE '%.xlsx'""").fetchone()
    print("xlsx chunks total=%s chars=%s NaN_tokens=%s" % tot)
except Exception as e:
    print("chunk sample failed:", e)
PY
echo "--- adapter log: RAG 500s on content upload (last 2000 lines) ---"
docker logs --tail 2000 vivesec-adapter 2>&1 | grep -iE 'file/content.*(500|error)|markitdown|xlsx' | tail -20
