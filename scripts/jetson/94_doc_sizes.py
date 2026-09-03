#!/usr/bin/env python3
"""Document size distribution of the live index, on the box.

The analyze character cap decides how many documents can be read whole. That
cap was tuned on the dev corpus; a customer corpus has its own distribution, so
it has to be measured here before the cap is trusted.

    python3 94_doc_sizes.py [index_path] [container]
"""
import json
import subprocess
import sys

INDEX = sys.argv[1] if len(sys.argv) > 1 else "/data/rag/index.db"
CONTAINER = sys.argv[2] if len(sys.argv) > 2 else "vivesec-rag"
CAPS = [26000, 50000, 100000, 200000, 400000]
PREFILL_TOKS_PER_S = 790.0
DENSE_CHARS_PER_TOK = 1.75

PROBE = """
import json, sqlite3
c = sqlite3.connect("file:%s?mode=ro", uri=True)
rows = c.execute(
    "SELECT SUM(LENGTH(ch.text)) AS chars, COUNT(*) "
    "FROM documents d JOIN chunks ch ON ch.doc_id = d.doc_id "
    "WHERE d.file = 1 GROUP BY d.doc_id").fetchall()
print(json.dumps([[r[0] or 0, r[1] or 0] for r in rows]))
""" % INDEX


def main():
    out = subprocess.run(["docker", "exec", "-i", CONTAINER, "python", "-c", PROBE],
                         capture_output=True, text=True)
    line = (out.stdout or "").strip().splitlines()
    if not line:
        print("probe failed:", (out.stderr or "")[-500:])
        return 1
    docs = json.loads(line[-1])
    docs.sort(key=lambda d: -d[0])
    total = len(docs)
    chars = [d[0] for d in docs]
    print("documents with text: %d" % total)
    print("total characters   : %d" % sum(chars))
    if total:
        s = sorted(chars)
        def pct(p):
            return s[min(int(total * p / 100.0), total - 1)]
        print("percentiles (chars): p50=%d p90=%d p99=%d max=%d"
              % (pct(50), pct(90), pct(99), s[-1]))
    print("\nlargest 10 (chars, chunks):")
    for c, n in docs[:10]:
        print("  %9d %6d" % (c, n))
    print("\nanalyze coverage by character cap:")
    print("  %-10s %-22s %s" % ("cap", "fully analysed", "worst-case prefill"))
    for cap in CAPS:
        whole = sum(1 for c in chars if c <= cap)
        secs = (cap / DENSE_CHARS_PER_TOK) / PREFILL_TOKS_PER_S
        print("  %-10d %6d / %-6d (%5.1f%%)  %5.0f s"
              % (cap, whole, total, 100.0 * whole / max(total, 1), secs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
