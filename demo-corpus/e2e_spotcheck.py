#!/usr/bin/env python3
"""End-to-end spot check on the DEMO corpus: retrieval (:8093) -> grounded answer.

Runs ON the Jetson. Pulls contexts from the demo RAG instance and generates the
answer with the adapter's own llm module inside the adapter container, so the
prompt, the grounding rules and the refusal behaviour are exactly what the
product does.

Purpose: show whether the *generation* layer refuses the questions the score
threshold cannot separate (the score distributions overlap on this corpus).
"""
import json
import os
import subprocess
import sys
import urllib.request

RAG_URL = os.environ.get("RAG_URL", "http://127.0.0.1:8093")
KEY = open(os.path.expanduser("~/demo_rag_api_key.txt")).read().strip()
CORPORA = {
    "finance": "finance-114ed822",
    "legal": "legal-c9902b93",
    "engineering": "engineering-1cd6e192",
    "hr": "hr-6ba4fc3f",
    "public": "public-aa2010a2",
}

CASES = [
    ("REAL   ", "finance", "What was the revenue in Q2 2026 and how did it compare to plan?"),
    ("REAL   ", "legal", "How much is the annual fee for the AI platform and what notice period applies?"),
    ("REAL   ", "hr", "Mennyi a napidij Magyarorszagon es Daniaban?"),
    ("NEGATIVE", "finance", "How much dividend was paid for FY2025?"),
    ("NEGATIVE", "finance", "What was the revenue in Q3 2026?"),
    ("NEGATIVE", "legal", "What did the 2026 penetration test find?"),
    ("NEGATIVE", "public", "Which company did Voltara acquire in 2025?"),
    ("NEGATIVE", "public", "When did the Shenzhen plant open?"),
]


def search(corpus_id, question, top_k=5):
    payload = {"corpus_id": corpus_id, "tenant_id": "gaphopper", "question": question,
               "top_k": top_k, "max_context_tokens": 4000}
    req = urllib.request.Request(
        RAG_URL + "/rag/search_context",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8")).get("contexts") or []


RUNNER = r'''
import json, sys
sys.path.insert(0, "/app/adapter")
import llm
payload = json.load(sys.stdin)
answer, mode = llm.generate(payload["question"], payload["contexts"], lang=payload["lang"])
print(json.dumps({"answer": answer, "mode": mode}))
'''


def generate(question, contexts, lang):
    payload = json.dumps({"question": question, "contexts": contexts, "lang": lang})
    proc = subprocess.run(
        ["docker", "exec", "-i", "vivesec-adapter", "python", "-c", RUNNER],
        input=payload.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return {"answer": "<<generation failed: %s>>" % proc.stderr.decode()[-300:], "mode": "error"}
    return json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])


def main():
    for kind, drive, question in CASES:
        contexts = search(CORPORA[drive], question)
        top = contexts[0]["score"] if contexts else None
        lang = "Hungarian" if "Mennyi" in question else "English"
        res = generate(question, contexts, lang)
        print("=" * 78)
        print("%s [%s]  %s" % (kind, drive, question))
        print("  contexts=%d  top_score=%s  mode=%s"
              % (len(contexts), ("%.4f" % top) if top else "-", res["mode"]))
        for line in res["answer"].strip().splitlines():
            print("    " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
