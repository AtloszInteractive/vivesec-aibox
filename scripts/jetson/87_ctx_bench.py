#!/usr/bin/env python3
"""How much does a bigger context window actually cost on this box?

Loads the generation model at several num_ctx values with a realistic prompt
and reports resident size, prefill and generation speed. The point is to decide
ADAPTER_ANALYZE_NUM_CTX on measurement rather than on hope: on a Jetson the KV
cache comes out of the SAME 64 GB the rest of the box uses.

    python3 87_ctx_bench.py [chars] [num_ctx ...]
"""
import json
import subprocess
import sys
import time
import urllib.request

OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen3.6:35b"
CHARS = int(sys.argv[1]) if len(sys.argv) > 1 else 40000
CTXS = [int(x) for x in sys.argv[2:]] or [16384, 32768, 65536]

SAMPLE = "/storage/drives/engineering/fleet/VoltStack2_Operation_and_Maintenance_Manual.pdf"


def free_gb():
    out = subprocess.run(["free", "-g"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("Mem:"):
            f = line.split()
            return {"used": f[2], "free": f[3], "available": f[6]}
    return {}


def ps_size():
    out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout
    for line in out.splitlines()[1:]:
        if line.strip():
            return " ".join(line.split()[2:5])
    return "(not loaded)"


def unload():
    body = json.dumps({"model": MODEL, "keep_alive": 0}).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120).read()
    except Exception:
        pass
    time.sleep(6)


def run(num_ctx, prompt):
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.2, "num_predict": 400, "num_ctx": num_ctx},
    }).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.loads(r.read().decode())
    wall = time.time() - t0
    return out, wall


def main():
    # A realistic prompt: repeat real prose until the target size.
    seed = ("The VoltStack 2 containerised battery energy storage system uses a "
            "liquid cooled thermal loop, a GC-3000 grid controller and a DC bus "
            "that stays energised after the AC breaker opens. Maintenance is "
            "scheduled quarterly and recorded in the Voltara Service Portal. ")
    prompt = ("Summarise the following document.\n\n" + seed * (CHARS // len(seed) + 1))[:CHARS]
    print("prompt: %d chars (~%d words)" % (len(prompt), len(prompt.split())))
    print("baseline memory:", free_gb())
    print()
    print("%-9s %-26s %8s %8s %10s %10s" %
          ("num_ctx", "resident", "prompt", "gen", "prefill/s", "wall_s"))
    for ctx in CTXS:
        unload()
        try:
            out, wall = run(ctx, prompt)
        except Exception as e:  # noqa: BLE001
            print("%-9d FAILED: %s" % (ctx, e))
            continue
        size = ps_size()
        pc = out.get("prompt_eval_count") or 0
        pd = out.get("prompt_eval_duration") or 1
        ec = out.get("eval_count") or 0
        content = (out.get("message") or {}).get("content") or ""
        print("%-9d %-26s %8d %8d %10.0f %10.1f" %
              (ctx, size, pc, ec, pc / (pd / 1e9), wall))
        print("          answer=%d chars  mem=%s" % (len(content.strip()), free_gb()))
    unload()
    print("\nunloaded; memory:", free_gb())


if __name__ == "__main__":
    main()
