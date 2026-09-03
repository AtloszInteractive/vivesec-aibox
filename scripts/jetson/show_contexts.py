#!/usr/bin/env python3
"""Show the raw contexts the RAG returns for one question (what the LLM actually sees)."""
import json
import os
import sys
import urllib.request

question = " ".join(sys.argv[1:])
corpus = os.environ.get("CORPUS", "engineering-1cd6e192")
key = open(os.path.expanduser("~/prod_rag_api_key.txt")).read().strip()
payload = {"corpus_id": corpus, "tenant_id": "default", "question": question,
           "top_k": 5, "max_context_tokens": 4000}
req = urllib.request.Request(
    "http://127.0.0.1:8090/rag/search_context",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "X-API-Key": key}, method="POST")
with urllib.request.urlopen(req, timeout=120) as r:
    ctxs = json.loads(r.read()).get("contexts") or []
print("contexts: %d (corpus=%s)" % (len(ctxs), corpus))
for i, c in enumerate(ctxs, 1):
    text = c.get("text") or ""
    marker = " <<< HAS round-trip" if "round-trip" in text.lower() else ""
    print("[#%d] %s p%s score=%.3f len=%d%s" % (
        i, c.get("source_path"), c.get("page_number"), c.get("score") or 0, len(text), marker))
    if marker:
        idx = text.lower().find("round-trip")
        print("      ...%s..." % text[max(0, idx-80):idx+120].replace("\n", " "))
