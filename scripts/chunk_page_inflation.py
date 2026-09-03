#!/usr/bin/env python3
"""Compare the pre-fix (single logical page) and post-fix (form-feed paginated)
indexes over the same 240-doc corpus.

Answers two questions:
  1. How much did per-page chunking inflate the chunk count, in total and per doc?
  2. Where does the inflation come from -- many short pages, or big documents?

Reads both sqlite indexes read-only; touches only plain tables so the sqlite-vec
extension is not needed.
"""
import sqlite3
import sys

OLD = "/data/rag-eval-sub/rag_index_evalsub.db"
NEW = "/home/aibox/rag-pagefix/rag_index_pagefix.db"


def load(path):
    con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    docs = {}
    for source_path, pages, chunks in con.execute(
        "SELECT source_path, pages, chunks FROM documents"
    ):
        docs[source_path] = (pages, chunks)
    # word count per chunk, to see how many chunks are degenerate stubs
    sizes = [
        n for (n,) in con.execute(
            "SELECT length(text) - length(replace(text, ' ', '')) + 1 FROM chunks"
        )
    ]
    con.close()
    return docs, sizes


def pct(values, p):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def main():
    try:
        old, old_sizes = load(OLD)
        new, new_sizes = load(NEW)
    except sqlite3.OperationalError as exc:
        print("cannot open index: %s" % exc)
        return 1

    both = sorted(set(old) & set(new))
    print("docs: old=%d new=%d common=%d" % (len(old), len(new), len(both)))
    print("only in old (failed to re-index): %d" % len(set(old) - set(new)))

    op = sum(old[d][0] for d in both)
    oc = sum(old[d][1] for d in both)
    np_ = sum(new[d][0] for d in both)
    nc = sum(new[d][1] for d in both)
    print("\n--- common docs (n=%d) ---" % len(both))
    print("pages : %6d -> %6d  (x%.2f)" % (op, np_, np_ / max(1, op)))
    print("chunks: %6d -> %6d  (x%.2f)" % (oc, nc, nc / max(1, oc)))

    print("\n--- chunk length in words ---")
    for label, sizes in (("old", old_sizes), ("new", new_sizes)):
        stubs = sum(1 for s in sizes if s < 20)
        print("%s: n=%6d  p50=%4d p90=%4d  mean=%5.1f  stubs(<20w)=%d (%.1f%%)" % (
            label, len(sizes), pct(sizes, 50), pct(sizes, 90),
            sum(sizes) / max(1, len(sizes)), stubs, 100.0 * stubs / max(1, len(sizes)),
        ))

    print("\n--- top 15 by chunk inflation ---")
    rows = []
    for d in both:
        o_pages, o_chunks = old[d]
        n_pages, n_chunks = new[d]
        rows.append((n_chunks - o_chunks, d, o_pages, n_pages, o_chunks, n_chunks))
    rows.sort(reverse=True)
    print("%6s %5s %6s %7s %7s  %s" % ("+chunk", "pages", "->", "chunks", "->", "path"))
    for delta, d, op_, np2, oc_, nc_ in rows[:15]:
        print("%+6d %5d %6d %7d %7d  %s" % (delta, op_, np2, oc_, nc_, d[:70]))

    missing = sorted(set(old) - set(new))
    if missing:
        print("\n--- documents that did NOT re-index (timeouts) ---")
        for d in missing:
            print("  pages=%-4d chunks=%-6d %s" % (old[d][0], old[d][1], d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
