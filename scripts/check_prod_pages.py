#!/usr/bin/env python3
"""Did the pagination fix actually take effect in production?

Lists the PDFs with the number of pages built for each. Before the fix every
document was a single logical page.
"""
import sqlite3

DB = "/data/rag/rag_index_v2.db"
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)

print("%-6s %-7s %s" % ("pages", "chunks", "source_path"))
multi = 0
pdfs = 0
for source_path, pages, chunks in con.execute(
        "SELECT source_path, pages, chunks FROM documents "
        "WHERE file=1 ORDER BY pages DESC, source_path"):
    ext = source_path.rsplit(".", 1)[-1].lower() if "." in source_path else ""
    if ext == "pdf":
        pdfs += 1
        if pages > 1:
            multi += 1
    if pages > 1 or ext == "pdf":
        print("%-6d %-7d %s" % (pages, chunks, source_path))

print("\nPDFs: %d, of which multi-page: %d" % (pdfs, multi))
total_pages = con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
print("total pages in index: %d" % total_pages)
con.close()
