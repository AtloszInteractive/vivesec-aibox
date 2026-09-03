#!/usr/bin/env python3
"""Print the ingest failures recorded during the pagefix re-index, with the full
error text, so the cause (timeout vs extraction vs embedding) is unambiguous."""
import json

PATH = "/home/aibox/govdocs-eval/ingest_errors.jsonl"

with open(PATH, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        print("-" * 70)
        for key, value in rec.items():
            text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if len(text) > 700:
                text = text[:700] + " ...[cut]"
            print("%-14s %s" % (key + ":", text))
