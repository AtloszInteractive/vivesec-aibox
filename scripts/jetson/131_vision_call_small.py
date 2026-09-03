#!/usr/bin/env python3
"""Vision call with an explicit, small context window.

OLLAMA_NUM_PARALLEL=4 on this box multiplies the KV allocation, so the default
context plus a vision projector can OOM even on an idle GPU.

    python3 131_vision_call_small.py MODEL IMAGE [NUM_CTX]
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request

model, img = sys.argv[1], sys.argv[2]
num_ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 8192
b64 = base64.b64encode(open(img, "rb").read()).decode()

body = {"model": model, "stream": False,
        "prompt": ("Transcribe every line of text visible in this scanned page. "
                   "Output the text only, preserving the order."),
        "images": [b64],
        "options": {"temperature": 0, "num_predict": 768, "num_ctx": num_ctx}}
req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read())
except urllib.error.HTTPError as e:
    print("num_ctx=%d -> HTTP %s: %s" % (num_ctx, e.code, e.read().decode()[:300]))
    sys.exit(1)
print("num_ctx=%d  elapsed %.0f s  prompt %s tok  gen %s tok"
      % (num_ctx, time.time() - t0, d.get("prompt_eval_count"), d.get("eval_count")))
print("-" * 70)
print((d.get("response") or "").strip()[:2000])
