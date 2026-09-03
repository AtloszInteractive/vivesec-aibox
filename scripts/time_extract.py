#!/usr/bin/env python3
"""Time extract_pages on the documents that timed out during the pagefix
re-index, to separate a slow extractor from slow embedding.

Run inside a rag container:  docker exec -i <c> python /tmp/time_extract.py
"""
import os
import sys
import time

sys.path.insert(0, "/app/rag_service")
sys.path.insert(0, "/app/poc")
import extract  # noqa: E402

FILES = [
    "/tmp/probe/101676.pdf",
    "/tmp/probe/101696.pdf",
    "/tmp/probe/101706.pdf",
    "/tmp/probe/102797.txt",
    "/tmp/probe/101613.txt",
]

try:
    import pdfminer
    print("pdfminer:", getattr(pdfminer, "__version__", "?"))
except Exception as exc:  # noqa: BLE001
    print("pdfminer: n/a (%s)" % exc)
try:
    import markitdown
    print("markitdown:", getattr(markitdown, "__version__", "?"))
except Exception as exc:  # noqa: BLE001
    print("markitdown: n/a (%s)" % exc)
print("-" * 60)

for path in FILES:
    if not os.path.exists(path):
        print("%-16s MISSING" % os.path.basename(path))
        continue
    raw = open(path, "rb").read()
    start = time.time()
    try:
        pages = extract.extract_pages(raw, path)
        words = sum(len(t.split()) for _, _, t in pages)
        print("%-16s %8.1f s  %5d KB  %4d pages  %7d words" % (
            os.path.basename(path), time.time() - start, len(raw) // 1024,
            len(pages), words))
    except Exception as exc:  # noqa: BLE001
        print("%-16s %8.1f s  FAILED: %s" % (
            os.path.basename(path), time.time() - start, str(exc)[:120]))
