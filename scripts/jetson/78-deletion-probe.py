#!/usr/bin/env python3
"""Prove a ViVeSecBox deletion propagated into the vector index, not just the UI.

  78-deletion-probe.py [drive] [path-fragment]

Run it before and after deleting a file/folder on the box: `MIRROR` is what the
drive panel shows, `INDEX` is what retrieval can still reach.
"""
import json
import os
import subprocess
import sys

DRIVE = sys.argv[1] if len(sys.argv) > 1 else "/storage/drives/aiboxdev"
NEEDLE = sys.argv[2] if len(sys.argv) > 2 else ""
MIRROR = "/data/adapter/meta.json"

PROBE = r"""
import sqlite3, json, sys
db, drive, needle = sys.argv[1], sys.argv[2], sys.argv[3]
con = sqlite3.connect(db)
names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
out = {}
cols = [r[1] for r in con.execute("pragma table_info(documents)")] if "documents" in names else []
key = "source_path" if "source_path" in cols else ("path" if "path" in cols else None)
if key:
    out["docs_in_drive"] = con.execute(
        "select count(*) from documents where %s like ?" % key, (drive + "%",)).fetchone()[0]
    if needle:
        rows = con.execute(
            "select %s from documents where %s like ? limit 10" % (key, key),
            ("%" + needle + "%",)).fetchall()
        out["matching_docs"] = [r[0] for r in rows]
        out["matching_count"] = con.execute(
            "select count(*) from documents where %s like ?" % key,
            ("%" + needle + "%",)).fetchone()[0]
if "chunks" in names:
    out["chunks_total"] = con.execute("select count(*) from chunks").fetchone()[0]
    ccols = [r[1] for r in con.execute("pragma table_info(chunks)")]
    if "doc_id" in ccols and "documents" in names:
        out["orphan_chunks"] = con.execute(
            "select count(*) from chunks where doc_id not in (select doc_id from documents)"
        ).fetchone()[0]
        if needle and key:
            out["matching_chunks"] = con.execute(
                "select count(*) from chunks where doc_id in "
                "(select doc_id from documents where %s like ?)" % key,
                ("%" + needle + "%",)).fetchone()[0]
print(json.dumps(out, ensure_ascii=False))
"""


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


mirror_all = mirror_hit = 0
hits = []
try:
    with open(MIRROR, encoding="utf-8") as f:
        docs = json.load(f).get("docs", {})
    for p in docs:
        if not p.startswith(DRIVE):
            continue
        mirror_all += 1
        if NEEDLE and NEEDLE in p:
            mirror_hit += 1
            if len(hits) < 10:
                hits.append(p)
except OSError as e:
    print("mirror unreadable: %s" % e)

print("MIRROR  entries under drive: %d" % mirror_all)
if NEEDLE:
    print("MIRROR  matching %r: %d" % (NEEDLE, mirror_hit))
    for h in hits:
        print("          %s" % h)

print("RAG     /stats: %s" % sh("curl -s -m 5 http://127.0.0.1:8090/stats"))

with open("/tmp/_rag_probe.py", "w", encoding="utf-8") as f:
    f.write(PROBE)
os.system("docker cp /tmp/_rag_probe.py vivesec-rag:/tmp/_rag_probe.py >/dev/null 2>&1")
print("INDEX   %s" % sh(
    'docker exec vivesec-rag python /tmp/_rag_probe.py /data/rag/index.db "%s" "%s"'
    % (DRIVE, NEEDLE)))
