#!/usr/bin/env python3
"""Side-by-side answers for the cases where answer-language was flagged."""
import json
import sys

a = json.load(open(sys.argv[1]))["results"]
b = json.load(open(sys.argv[2]))["results"]
key = lambda r: (r["drive"], r["qlang"])
bmap = {key(r): r for r in b}
for r in a:
    s = bmap.get(key(r))
    if not (r["lang_ok"] and (s or {}).get("lang_ok")):
        print("=" * 72)
        print("%s | %s | %s" % (r["qlang"], r["drive"], r["question"][:60]))
        print("  35b [%s]: %s" % (r["answer_lang"], r["answer"][:150].replace("\n", " ")))
        if s:
            print("  14b [%s]: %s" % (s["answer_lang"], s["answer"][:150].replace("\n", " ")))
