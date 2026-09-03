#!/usr/bin/env python3
"""Same fixed accented question through the adapter AND directly to the RAG —
transport-encoding ruled out (the string lives in this file, UTF-8)."""
import base64
import json
import os
import urllib.request

Q = "Mi a cég hivatalos megnevezése?"
DRIVE = "public"
CORPUS = "public-aa2010a2"

# direct RAG
key = open(os.path.expanduser("~/prod_rag_api_key.txt")).read().strip()
req = urllib.request.Request(
    "http://127.0.0.1:8090/rag/search_context",
    data=json.dumps({"corpus_id": CORPUS, "tenant_id": "default", "question": Q,
                     "top_k": 8, "max_context_tokens": 4000}).encode(),
    headers={"Content-Type": "application/json", "X-API-Key": key}, method="POST")
with urllib.request.urlopen(req, timeout=120) as r:
    ctxs = json.loads(r.read()).get("contexts") or []
print("RAG direct: %d contexts, top=%s" % (
    len(ctxs), ctxs[0]["score"] if ctxs else None))

# adapter
h = base64.urlsafe_b64encode(("/storage/drives/%s/" % DRIVE).encode()).decode().rstrip("=")
req = urllib.request.Request(
    "http://127.0.0.1:8088/api/v1/ui/query",
    data=json.dumps({"query": Q, "top_k": 8}).encode(),
    headers={"Content-Type": "application/json", "VVS-Drive": h, "VVS-User": "demo"},
    method="POST")
with urllib.request.urlopen(req, timeout=420) as r:
    d = json.loads(r.read())
print("Adapter: hits=%d corpus=%s" % (len(d.get("hits") or []), d.get("corpus_id")))
print("Answer:", (d.get("answer") or "")[:150].replace("\n", " "))
