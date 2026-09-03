#!/usr/bin/env python3
"""Read-only look at the production index before a redeploy: what is in it, and
where did it come from? A redeploy that changes chunking needs a re-ingest, so
the source files have to be locatable first."""
import json
import sqlite3
from collections import Counter

DB = "/data/rag/rag_index.db"
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)

docs = con.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
pages = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
print("documents=%d pages=%d chunks=%d" % (docs, pages, chunks))

print("\n--- corpora ---")
for corpus_id, tenant, n in con.execute(
        "SELECT d.corpus_id, MAX(d.tenant_id), COUNT(*) FROM documents d GROUP BY d.corpus_id"):
    print("  %-32s tenant=%-10s docs=%d" % (corpus_id, tenant, n))

print("\n--- pages per document (are they all 1?) ---")
hist = Counter(p for (p,) in con.execute("SELECT pages FROM documents"))
for k in sorted(hist)[:8]:
    print("  %d page(s): %d documents" % (k, hist[k]))

print("\n--- sample source paths ---")
for (sp,) in con.execute("SELECT source_path FROM documents ORDER BY source_path LIMIT 12"):
    print("  %s" % sp)

print("\n--- file types ---")
ext = Counter()
for (sp,) in con.execute("SELECT source_path FROM documents"):
    ext["." + sp.rsplit(".", 1)[-1].lower() if "." in sp else "(none)"] += 1
print("  " + json.dumps(dict(ext.most_common(10))))
con.close()
