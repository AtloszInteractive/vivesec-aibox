#!/usr/bin/env python3
"""Feed one image to a vision model, via /api/generate and /api/chat, and print
the server's error body if it refuses. Usage: 129_vision_call.py MODEL IMAGE
"""
import base64
import json
import sys
import time
import urllib.error
import urllib.request

model, img = sys.argv[1], sys.argv[2]
b64 = base64.b64encode(open(img, "rb").read()).decode()
PROMPT = ("Transcribe every line of text visible in this scanned page. "
          "Output the text only, preserving the order.")


def call(path, body):
    req = urllib.request.Request("http://127.0.0.1:11434" + path,
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            d = json.loads(r.read())
        wall = time.time() - t0
        txt = d.get("response") or (d.get("message") or {}).get("content") or ""
        print("[%s] OK  %.0f s  %s tok" % (path, wall, d.get("eval_count")))
        print("-" * 70)
        print(txt.strip()[:2500])
        return True
    except urllib.error.HTTPError as e:
        print("[%s] HTTP %s: %s" % (path, e.code, e.read().decode()[:400]))
    except Exception as e:
        print("[%s] %s" % (path, e))
    return False


opts = {"temperature": 0, "num_predict": 1024}
if call("/api/generate", {"model": model, "prompt": PROMPT, "images": [b64],
                          "stream": False, "options": opts}):
    sys.exit(0)
call("/api/chat", {"model": model, "stream": False, "options": opts, "think": False,
                   "messages": [{"role": "user", "content": PROMPT, "images": [b64]}]})
