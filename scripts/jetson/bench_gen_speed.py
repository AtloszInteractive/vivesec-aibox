#!/usr/bin/env python3
"""Raw generation speed smoke for an Ollama model on the Jetson.

Measures prefill + generation rate with a grounded-RAG-shaped prompt
(instruction + context + question), the closest thing to the adapter's
real workload without touching the adapter.
"""
import json
import sys
import time
import urllib.request

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3.6:35b"
URL = "http://127.0.0.1:11434/api/generate"

CONTEXT = """[#1] Voltara Energy Group - Q2 2026 Flash Report
Revenue in Q2 2026 was EUR 14.7 million against a plan of EUR 15.1 million
(-2.6%). Gross margin was 43.1%. H1 2026 revenue totalled EUR 27.9 million.

[#2] Fleet Service Review 2026-H1
DK-010 Hjorring recorded 6 interventions in H1 2026, SE-002 recorded 6,
DE-008 recorded 5. This ranking covers the whole of 2026-H1.
"""

PROMPT = (
    "Answer ONLY from the CONTEXT below. Cite sources as [#N]. "
    "If the answer is not in the context, say so.\n\nCONTEXT:\n"
    + CONTEXT
    + "\nQUESTION: What was the Q2 2026 revenue versus plan, and which site "
    "had the most interventions in H1 2026?\n"
)


def run(label, options, think=None):
    body = {"model": MODEL, "prompt": PROMPT, "stream": False, "options": options}
    if think is not None:
        body["think"] = think
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as resp:
        data = json.loads(resp.read())
    wall = time.time() - t0
    pe = data.get("prompt_eval_count", 0)
    ped = data.get("prompt_eval_duration", 1) / 1e9
    ec = data.get("eval_count", 0)
    ed = data.get("eval_duration", 1) / 1e9
    print("== %s ==" % label)
    print("  wall %.1fs | prefill %d tok / %.2fs (%.0f tok/s) | gen %d tok / %.1fs (%.1f tok/s)"
          % (wall, pe, ped, pe / max(ped, 1e-9), ec, ed, ec / max(ed, 1e-9)))
    print("  answer: %s" % (data.get("response", "").strip()[:400].replace("\n", " ")))
    thinking = (data.get("thinking") or "").strip()
    if thinking:
        print("  thinking (%d chars): %s..." % (len(thinking), thinking[:200].replace("\n", " ")))
    print()


# first call includes model load; second call is the steady-state number
run("cold (model load + gen)", {"temperature": 0, "num_predict": 256})
run("warm, think off", {"temperature": 0, "num_predict": 256}, think=False)
run("warm, think off 2", {"temperature": 0, "num_predict": 256}, think=False)
