#!/usr/bin/env python3
"""Print the per-case detail of a 91_benchmark.py report.

The verdict block only carries totals; when a number looks wrong it is the
individual cases that say whether the build, the corpus or the harness is at
fault.

    python3 96_bench_detail.py ~/bench_demo_2_code.json
"""
import json
import sys


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)

    print("== %s (schema %s) ==" % (d.get("label"), d.get("schema")))

    print("\nANALYZE")
    for c in (d.get("analyze") or {}).get("cases") or []:
        print("  %-7s %-44s %9s B  %s/%s chunks  try#%s  %ss  %s chars  conf=%s%s"
              % (c.get("size"), (c.get("file") or "")[:44], c.get("bytes"),
                 c.get("chunks_used"), c.get("chunks_total"), c.get("attempt"),
                 c.get("seconds"), c.get("body_chars"), c.get("confidence"),
                 "  NO INDEXED TEXT" if c.get("no_text") else ""))

    print("\nLANGUAGE")
    for c in (d.get("language") or {}).get("cases") or []:
        print("  %-10s -> %-10s ok=%-5s refusal_ok=%s"
              % (c.get("asked"), c.get("detected"), c.get("language_ok"),
                 c.get("refusal_ok")))

    print("\nACTIONS")
    for c in (d.get("actions") or {}).get("cases") or []:
        print("  %-13s %5ss  %5s tok/s  %5s chars  conf=%-4s %s"
              % (c.get("action"), c.get("seconds"), c.get("tok_per_s"),
                 c.get("body_chars"), c.get("confidence"),
                 "REFUSED" if c.get("refused") else ""))

    print("\nGROUNDING")
    for c in (d.get("grounding") or {}).get("cases") or []:
        print("  conf=%-4s ungrounded=%-5s suppressed=%-5s %s"
              % (c.get("confidence"), c.get("ungrounded"), c.get("suppressed"),
                 (c.get("probe") or "")[:60]))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
