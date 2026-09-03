#!/usr/bin/env python3
"""Where did the five failed documents sit in ingest order, and how big were
their neighbours? Ingest is sequential, so a document that timed out right
behind a very large one was probably only queued, not slow itself."""
import json

MANIFEST = "/home/aibox/govdocs-eval/corpus_manifest_subset.jsonl"
FAILED = [
    "/drive_engineering/102/102797.txt",
    "/drive_public/101/101613.txt",
    "/drive_public/101/101676.pdf",
    "/drive_public/101/101696.pdf",
    "/drive_public/101/101706.pdf",
]

docs = []
with open(MANIFEST, encoding="utf-8") as fh:
    for line in fh:
        docs.append(json.loads(line))

pos = {d["source_path"]: i for i, d in enumerate(docs, start=1)}

print("%-42s %5s %10s" % ("failed document", "pos", "size KB"))
for path in FAILED:
    i = pos.get(path)
    size = next((d["size"] for d in docs if d["source_path"] == path), 0)
    print("%-42s %5s %10d" % (path, i, size // 1024))

print("\n--- neighbourhood of each failure (2 before, 1 after) ---")
for path in FAILED:
    i = pos.get(path)
    if not i:
        continue
    print("\n@%d  %s" % (i, path))
    for j in range(max(1, i - 2), min(len(docs), i + 1) + 1):
        d = docs[j - 1]
        mark = "<<<" if j == i else "   "
        print("  %s %4d %9d KB  %s" % (mark, j, d["size"] // 1024, d["source_path"]))

print("\n--- 10 largest documents in the corpus, with ingest position ---")
for d in sorted(docs, key=lambda x: -x["size"])[:10]:
    print("  pos=%4d %9d KB  %s" % (pos[d["source_path"]], d["size"] // 1024, d["source_path"]))
