"""How long does building the accent vocabulary take, per corpus size?"""
import sqlite3
import sys
import time

sys.path.insert(0, "/app/rag_service")
import reaccent  # noqa: E402

DB = "file:%s?mode=ro" % (sys.argv[1] if len(sys.argv) > 1
                          else "/data/rag_index_diacritics_probe.db")
conn = sqlite3.connect(DB, uri=True)

print("%-24s %8s %8s %8s %8s" % ("corpus", "chunks", "sec", "forms", "MB"))
for (corpus_id,) in conn.execute("SELECT corpus_id FROM corpora ORDER BY corpus_id"):
    n = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE corpus_id=?", (corpus_id,)
    ).fetchone()[0]
    if not n:
        continue
    started = time.time()
    rows = conn.execute("SELECT text FROM chunks WHERE corpus_id=?", (corpus_id,))
    vocabulary = reaccent.build(text for (text,) in rows)
    forms = len(vocabulary)
    elapsed = time.time() - started
    size = sum(len(k) + len(v) for k, v in
               zip(vocabulary._folded, vocabulary._accented)) / 1e6
    print("%-24s %8d %8.2f %8d %8.2f" % (corpus_id, n, elapsed, forms, size))
