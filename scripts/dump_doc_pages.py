#!/usr/bin/env python3
"""Dump per-document page counts from a rag_service sqlite index as JSON, so the
gold set's expected page numbers can be checked against what we actually built."""
import json
import sqlite3
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/aibox/rag-pagefix/rag_index_pagefix.db"
con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
out = {}
for source_path, pages, chunks in con.execute(
    "SELECT source_path, pages, chunks FROM documents"
):
    out[source_path] = {"pages": pages, "chunks": chunks}
con.close()
print(json.dumps(out))
