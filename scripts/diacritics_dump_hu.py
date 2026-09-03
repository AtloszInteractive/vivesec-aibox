"""What Hungarian text does the index actually hold for the HU documents?"""
import sqlite3
import sys

DB = "file:/data/rag_index_diacritics_probe.db?mode=ro"
conn = sqlite3.connect(DB, uri=True)

for word in ("külföldi", "kiküldet", "távollét", "termék", "napidíj"):
    n = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE text LIKE ?", ("%" + word + "%",)
    ).fetchone()[0]
    print("%-12s %d chunk" % (word, n))

print()
for path in ("Utazasi_szabalyzat_v3_HU.pdf", "Munkavallaloi_kezikonyv_kivonat_HU.md",
             "Voltara_Cegbemutato_2026_HU.md"):
    rows = conn.execute(
        "SELECT c.chunk_id, c.page_number, c.text FROM chunks c "
        "JOIN documents d ON c.doc_id=d.doc_id WHERE d.source_path LIKE ? "
        "ORDER BY c.chunk_id", ("%" + path,)
    ).fetchall()
    print("=== %s: %d chunk ===" % (path, len(rows)))
    for chunk_id, page, text in rows[:2]:
        print("  [p%s] %s" % (page, " ".join(text.split())[:400]))
    print()
