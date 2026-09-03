"""List the corpora and documents of the diacritics probe index."""
import sqlite3

DB = "file:/data/rag_index_diacritics_probe.db?mode=ro"
c = sqlite3.connect(DB, uri=True)

for corpus_id, tenant in c.execute("SELECT corpus_id, tenant_id FROM corpora ORDER BY corpus_id"):
    n = c.execute(
        "SELECT COUNT(*) FROM documents WHERE corpus_id=? AND file=1", (corpus_id,)
    ).fetchone()[0]
    print("%s  tenant=%s  docs=%d" % (corpus_id, tenant, n))

print()
print("--- documents whose name hints at a non-English language ---")
rows = c.execute(
    "SELECT corpus_id, source_path FROM documents WHERE file=1 ORDER BY source_path"
).fetchall()
for corpus_id, path in rows:
    name = path.rsplit("/", 1)[-1]
    if any(t in name for t in ("_HU", "_DE", "_DA", "hu", "de", "da")):
        print("%-16s %s" % (corpus_id, path))
