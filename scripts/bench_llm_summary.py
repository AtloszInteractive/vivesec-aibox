"""Cost of per-page / per-document LLM summaries on the AI Box.

PageIndexes currently builds the document vector from `normalize(text[:1200])`
and the page vector from the first 700 characters. CoLearn's option (1) is to
replace those with real LLM summaries. This measures what that costs using the
generation model that already runs on the box, and extrapolates to a corpus.

Ollama reports prefill and generation separately, so the projection is grounded:
prefill scales with input size, generation with summary length.

    python3 bench_llm_summary.py [--model qwen2.5:14b] [--pages 3300] [--docs 240]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request

# A realistic page: ~2600 characters, the size PageIndexes uses per page.
_BASE = ("The contractor shall furnish all labor, materials and equipment required "
         "for the installation of the concrete paving described in Section 02510. "
         "Group VIII operators receive a base rate of $20.33 per hour with a fringe "
         "benefit contribution of $5.27. Zone differentials apply beyond a 15 mile "
         "radius measured from the Big I interchange. ")
PAGE_TEXT = (_BASE * 8)[:2600]


def generate(url: str, model: str, prompt: str, num_predict: int = 120) -> dict:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": num_predict},
    }).encode()
    req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as resp:
        payload = json.loads(resp.read())
    payload["_wall_s"] = time.time() - t0
    return payload


def report(label: str, d: dict) -> float:
    ns = 1e9
    prefill_n = d.get("prompt_eval_count", 0)
    prefill_s = d.get("prompt_eval_duration", 0) / ns
    gen_n = d.get("eval_count", 0)
    gen_s = d.get("eval_duration", 0) / ns
    total = d.get("total_duration", 0) / ns or d["_wall_s"]
    print(f"{label:18s} prefill {prefill_n:5d} tok / {prefill_s:6.2f} s   "
          f"gen {gen_n:4d} tok / {gen_s:6.2f} s   OSSZ {total:6.2f} s")
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen2.5:14b")
    ap.add_argument("--pages", type=int, default=3300, help="pages in the subset")
    ap.add_argument("--docs", type=int, default=240, help="documents in the subset")
    args = ap.parse_args()

    print(f"modell: {args.model}  (host Ollama, GPU)\n")

    generate(args.url, args.model, "Summarize: hello world.", num_predict=8)  # warm-up

    page_prompt = "Summarize the following page in two sentences:\n\n" + PAGE_TEXT
    samples = [report(f"oldal-summary #{i + 1}",
                      generate(args.url, args.model, page_prompt))
               for i in range(3)]
    page_s = sum(samples) / len(samples)

    # Document summary = map-reduce over the page summaries of one document.
    # Subset average is ~14 pages/doc, so the reduce step ingests ~14 short summaries.
    doc_prompt = ("Summarize the following page summaries into one paragraph:\n\n"
                  + "\n".join(f"- Page {i + 1}: wage schedules, zone differentials "
                              f"and paving specifications." for i in range(14)))
    doc_s = report("dok-summary", generate(args.url, args.model, doc_prompt))

    print()
    total_s = args.pages * page_s + args.docs * doc_s
    print(f"vetites a {args.docs}-dokumentumos ({args.pages} oldal) reszhalmazra:")
    print(f"  oldal-summaryk : {args.pages} x {page_s:5.2f} s = {args.pages * page_s / 60:7.1f} perc")
    print(f"  dok-summaryk   : {args.docs} x {doc_s:5.2f} s = {args.docs * doc_s / 60:7.1f} perc")
    print(f"  OSSZESEN       : {total_s / 3600:.1f} ora")
    print()
    scale = 2011 / args.docs
    print(f"vetites a teljes 2011-dokumentumos korpuszra: ~{total_s * scale / 3600:.0f} ora")
    print(f"(osszehasonlitasul: a jelenlegi teljes ingest GPU-val ~20 perc / 240 dok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
