#!/usr/bin/env python3
"""Would a bigger analyze window actually buy anything on THIS corpus?

Memory is not the constraint (measured: even 262k ctx fits), so the question is
how many real documents are cut by the character cap, and what the prefill time
would be for the ones that are. Reads the live index read-only.

    python3 90_doc_size_report.py
"""
import subprocess
import sys

CAPS = [100000, 200000, 400000, 800000]
PREFILL_TOKS_PER_S = 790.0   # measured on this box, 87_ctx_bench.py
DENSE_CHARS_PER_TOK = 1.75   # measured worst case, 88_token_ratio.py

SQL = r"""
import sqlite3
c = sqlite3.connect("file:/data/rag_index_v3.db?mode=ro", uri=True)
rows = c.execute(
    "SELECT d.corpus_id, d.source_path, SUM(LENGTH(ch.text)) AS chars, COUNT(*) "
    "FROM documents d JOIN chunks ch ON ch.doc_id = d.doc_id "
    "WHERE d.file = 1 GROUP BY d.doc_id").fetchall()
for r in rows:
    print("%s\t%s\t%s\t%s" % r)
"""


def main():
    out = subprocess.run(
        ["ssh", "aibox@192.168.0.181",
         "docker exec -i vivesec-rag python -c \"$(cat)\""],
        input=SQL, capture_output=True, text=True)
    lines = [l for l in out.stdout.splitlines() if "\t" in l]
    if not lines:
        print("no rows;", out.stderr[-400:])
        return 1
    docs = []
    for l in lines:
        corpus, path, chars, chunks = l.split("\t")
        docs.append((corpus, path, int(chars or 0), int(chunks or 0)))
    docs.sort(key=lambda d: -d[2])
    total = len(docs)
    print("documents with text: %d" % total)
    print("\nlargest 10:")
    for corpus, path, chars, chunks in docs[:10]:
        print("  %9d chars %5d chunks  %s" % (chars, chunks, path.split("/")[-1][:52]))

    print("\ncoverage by character cap:")
    print("  %-10s %-22s %s" % ("cap", "fully analysed", "worst-case prefill"))
    for cap in CAPS:
        whole = sum(1 for d in docs if d[2] <= cap)
        secs = (cap / DENSE_CHARS_PER_TOK) / PREFILL_TOKS_PER_S
        print("  %-10d %5d / %-5d (%5.1f%%)   %5.0f s" %
              (cap, whole, total, 100.0 * whole / total, secs))

    print("\ndocuments still cut at 100k: %s"
          % ", ".join(d[1].split("/")[-1] for d in docs if d[2] > 100000) or "(none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
