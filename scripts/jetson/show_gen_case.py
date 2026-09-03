#!/usr/bin/env python3
"""Print one gen_eval case (question substring match) with its sources."""
import json
import sys

path, needle = sys.argv[1], sys.argv[2]
data = json.load(open(path))
cases = data.get("results", data.get("cases", [])) if isinstance(data, dict) else data
for c in cases:
    if needle.lower() in (c.get("question", "") or "").lower():
        print(json.dumps(c, indent=1, ensure_ascii=False)[:3000])
