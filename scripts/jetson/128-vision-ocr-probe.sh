#!/usr/bin/env bash
# Can the vision-capable model read the ONE document our text extractor cannot?
# The scanned PAC protocol is the corpus' only `extraction_empty` file, so it is
# the honest test of an OCR path.
set -u
MODEL="${1:-qwen3.8:27b}"
PDF=$(find ~/demo-corpus/out/drives /storage/drives -iname '*PAC_Protocol*' -print -quit 2>/dev/null)
[ -n "$PDF" ] || { echo "scanned PDF not found"; exit 1; }
echo "source: $PDF ($(stat -c %s "$PDF") bytes)"

WORK=~/ocr-probe
rm -rf "$WORK"; mkdir -p "$WORK"
pdftoppm -r 150 -png -f 1 -l 1 "$PDF" "$WORK/page"
ls -la "$WORK"

IMG=$(ls "$WORK"/page*.png | head -1)
python3 - "$MODEL" "$IMG" <<'PY'
import base64, json, sys, time, urllib.request
model, img = sys.argv[1], sys.argv[2]
b64 = base64.b64encode(open(img, 'rb').read()).decode()
body = {"model": model, "stream": False, "think": False,
        "images": [b64],
        "prompt": ("Transcribe every line of text visible in this scanned page. "
                   "Output the text only, preserving the order. "
                   "If a value is unreadable, write [unreadable]."),
        "options": {"temperature": 0, "num_predict": 1024}}
req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
t0 = time.time()
with urllib.request.urlopen(req, timeout=900) as r:
    d = json.loads(r.read())
print("elapsed: %.0f s   gen %s tok" % (time.time() - t0, d.get("eval_count")))
print("-" * 70)
print((d.get("response") or "").strip()[:2500])
PY
