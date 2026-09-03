#!/bin/sh
# Per-corpus document counts straight from the RAG service database.

echo "=== db files currently open by the service ==="
docker exec vivesec-rag sh -c 'ls -l /proc/1/fd 2>/dev/null | grep "\.db"'
echo
DB=$(docker exec vivesec-rag sh -c 'ls -l /proc/1/fd 2>/dev/null | grep -o "/data/[^ ]*\.db" | head -1')
echo "using: $DB"
echo

docker exec -i vivesec-rag python - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
cur = con.cursor()
rows = cur.execute(
    "SELECT corpus_id, COUNT(*) FROM documents GROUP BY corpus_id ORDER BY 2 DESC"
).fetchall()
print("%-24s %8s" % ("corpus_id", "docs"))
print("-" * 34)
for cid, n in rows:
    print("%-24s %8d" % (cid, n))
print("%-24s %8d" % ("TOTAL", sum(n for _, n in rows)))
print()
print("source_path prefixes (drive roots) per corpus:")
for cid, _ in rows:
    pref = cur.execute(
        "SELECT DISTINCT substr(source_path, 1, 30) FROM documents WHERE corpus_id=? LIMIT 4",
        (cid,),
    ).fetchall()
    print("  %-24s %s" % (cid, [p[0] for p in pref]))
PY
