#!/usr/bin/env python3
"""Measure the REAL chars-per-token ratio of the corpus, per document.

The RAG budgets context with a word-based estimate (words * 1.3). Dense content
(logs, tables, numbers) tokenizes far denser than prose, and that gap is what
silently overflowed the model window. This measures the true ratio against the
actual tokenizer, so the analyze character cap can be derived instead of
guessed.

    python3 88_token_ratio.py [drive] [chars]
"""
import json
import subprocess
import sys
import urllib.request

RAG = "http://127.0.0.1:8090"
OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen3.6:35b"
DRIVE = sys.argv[1] if len(sys.argv) > 1 else "/storage/drives/engineering/"
CHARS = int(sys.argv[2]) if len(sys.argv) > 2 else 30000

DOCS = [
    "fleet/VoltStack2_Operation_and_Maintenance_Manual.pdf",
    "test/GC3000_Firmware_v3_Test_Log_2026-06.txt",
    "fleet/exports/fleet_service_events.csv",
]


def api_key():
    with open("/home/aibox/prod_rag_api_key.txt") as f:
        return f.read().strip()


def corpus_id(drive):
    out = subprocess.run(
        ["docker", "exec", "vivesec-adapter", "python", "-c",
         "import sys; sys.path.insert(0,'/app/adapter'); import corpus; "
         "print(corpus.corpus_id_of_drive(%r))" % drive],
        capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def doc_text(cid, path):
    body = json.dumps({"corpus_id": cid, "source_path": path,
                       "max_context_tokens": 10 ** 7}).encode()
    req = urllib.request.Request(RAG + "/rag/document_context", data=body,
                                 headers={"Content-Type": "application/json",
                                          "X-API-Key": api_key()})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read().decode())
    return "\n\n".join(c.get("text") or "" for c in res.get("contexts", [])), res.get("document", {})


def count_tokens(text):
    body = json.dumps({
        "model": MODEL, "prompt": text, "stream": False, "think": False,
        "options": {"num_predict": 1, "num_ctx": 262144},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.loads(r.read().decode()).get("prompt_eval_count") or 0


def main():
    cid = corpus_id(DRIVE)
    print("corpus:", cid)
    print("%-46s %8s %8s %9s %9s %8s" %
          ("document", "chunks", "chars", "est_tok", "real_tok", "ch/tok"))
    worst = 99.0
    for rel in DOCS:
        path = DRIVE.rstrip("/") + "/" + rel
        try:
            text, doc = doc_text(cid, path)
        except Exception as e:  # noqa: BLE001
            print("%-46s FAILED %s" % (rel[:46], e))
            continue
        if not text:
            print("%-46s (not found)" % rel[:46])
            continue
        sample = text[:CHARS]
        est = int(len(sample.split()) * 1.3) + 1
        real = count_tokens(sample)
        ratio = len(sample) / real if real else 0
        worst = min(worst, ratio)
        print("%-46s %8s %8d %9d %9d %8.2f" %
              (rel.split("/")[-1][:46], doc.get("chunks_total"), len(sample),
               est, real, ratio))
    print("\nworst chars/token observed: %.2f" % worst)
    for ctx in (16384, 32768, 65536):
        budget = ctx - 2048 - 800
        print("  num_ctx %6d -> prompt budget %6d tok -> safe cap %6d chars"
              % (ctx, budget, int(budget * worst)))


if __name__ == "__main__":
    main()
