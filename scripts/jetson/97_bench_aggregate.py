#!/usr/bin/env python3
"""Aggregate repeated 91_benchmark.py runs of the same configuration.

A single run cannot separate a real change from run-to-run variance: the same
box, code and model produced 4, 3 and 1 generated quick actions on consecutive
runs. So counting metrics are POOLED across runs (12 action probes instead of
4) and continuous metrics are reported as median with the observed range. A
metric whose range overlaps between two configurations has not moved, whatever
the medians say.

    python3 97_bench_aggregate.py before1.json before2.json before3.json
    python3 97_bench_aggregate.py b1.json b2.json b3.json -- a1.json a2.json a3.json
"""
import json
import sys


def load_all(paths):
    out = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            out.append(json.load(f))
    return out


def median(vals):
    vals = sorted(v for v in vals if isinstance(v, (int, float)))
    if not vals:
        return None
    n = len(vals)
    return vals[n // 2] if n % 2 else round((vals[n // 2 - 1] + vals[n // 2]) / 2.0, 1)


def spread(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return "-"
    if len(set(vals)) == 1:
        return "%s" % vals[0]
    return "%s  [%s-%s]" % (median(vals), min(vals), max(vals))


def pool(runs, section, key):
    total = 0
    for r in runs:
        v = (r.get(section) or {}).get(key)
        if isinstance(v, (int, float)):
            total += v
    return total


def summarise(runs):
    """Pooled counts and median[range] figures for one configuration."""
    s = {}
    s["runs"] = len(runs)
    s["label"] = runs[0].get("label")
    env = runs[0].get("environment") or {}
    aenv = env.get("adapter_env") or {}
    s["model"] = aenv.get("ADAPTER_GEN_MODEL")
    s["analyze_num_ctx"] = aenv.get("ADAPTER_ANALYZE_NUM_CTX", "-")
    s["analyze_max_chars"] = aenv.get("ADAPTER_ANALYZE_MAX_CHARS", "-")

    caps = [r.get("capabilities") or {} for r in runs]
    s["document_context"] = sorted({str(c.get("rag_document_context")) for c in caps})
    s["analyze_supported"] = sorted({str(c.get("analyze_supported")) for c in caps})

    s["language_ok"] = pool(runs, "language", "language_match")
    s["language_total"] = pool(runs, "language", "total")
    for mode in ("auto", "explicit"):
        ok = tot = 0
        for r in runs:
            m = ((r.get("language") or {}).get("by_mode") or {}).get(mode) or {}
            ok += m.get("ok") or 0
            tot += m.get("trials") or 0
        s["lang_%s_ok" % mode] = ok
        s["lang_%s_total" % mode] = tot
    s["refusal_ok"] = pool(runs, "language", "refusal_correct")
    s["refusal_localised"] = pool(runs, "language", "refusal_localised")
    unstable = set()
    for r in runs:
        unstable.update((r.get("language") or {}).get("unstable_languages") or [])
    s["unstable_languages"] = sorted(unstable)

    s["actions_generated"] = pool(runs, "actions", "generated")
    s["actions_total"] = sum(len((r.get("actions") or {}).get("cases") or [])
                             for r in runs)
    s["actions_short"] = pool(runs, "actions", "short_outputs")
    s["truncated"] = pool(runs, "actions", "truncated_outputs")
    s["tok_per_s"] = spread([(r.get("actions") or {}).get("mean_tok_per_s")
                             for r in runs])

    s["analyze_analyzable"] = pool(runs, "analyze", "analyzable")
    s["analyze_full"] = pool(runs, "analyze", "fully_covered")
    s["analyze_degenerate"] = pool(runs, "analyze", "degenerate_answers")
    s["analyze_no_text"] = pool(runs, "analyze", "no_text_documents")

    s["confidence"] = spread([(r.get("grounding") or {}).get("mean_confidence")
                              for r in runs])
    s["ungrounded"] = pool(runs, "grounding", "with_ungrounded")
    s["wall_seconds"] = spread([r.get("wall_seconds") for r in runs])
    return s


ROWS = [
    ("runs aggregated", "runs"),
    ("model", "model"),
    ("analyze num_ctx", "analyze_num_ctx"),
    ("analyze max chars", "analyze_max_chars"),
    (None, None),
    ("document_context endpoint", "document_context"),
    ("#analyze supported", "analyze_supported"),
    (None, None),
    ("answer language ok", "_lang"),
    ("  no lang given (UI path)", "_lang_auto"),
    ("  lang given (control)", "_lang_explicit"),
    ("unstable languages", "unstable_languages"),
    ("refusal emitted", "_refusal"),
    ("refusal localised", "refusal_localised"),
    (None, None),
    ("quick actions generated", "_actions"),
    ("  short outputs", "actions_short"),
    ("  truncated", "truncated"),
    ("generation tok/s", "tok_per_s"),
    (None, None),
    ("analyze docs with text", "analyze_analyzable"),
    ("  fully covered", "analyze_full"),
    ("  degenerate", "analyze_degenerate"),
    ("  files w/o indexed text", "analyze_no_text"),
    (None, None),
    ("mean confidence", "confidence"),
    ("ungrounded cases", "ungrounded"),
    ("wall seconds per run", "wall_seconds"),
]


def cell(s, key):
    if key == "_lang":
        return "%s/%s" % (s["language_ok"], s["language_total"])
    if key == "_lang_auto":
        return "%s/%s" % (s.get("lang_auto_ok", "-"), s.get("lang_auto_total", "-"))
    if key == "_lang_explicit":
        return "%s/%s" % (s.get("lang_explicit_ok", "-"),
                          s.get("lang_explicit_total", "-"))
    if key == "_refusal":
        return "%s/%s" % (s["refusal_ok"], s["language_total"])
    if key == "_actions":
        return "%s/%s" % (s["actions_generated"], s["actions_total"])
    v = s.get(key)
    if isinstance(v, list):
        return ",".join(v) if v else "none"
    return v


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    groups = [[]]
    for a in args:
        if a == "--":
            groups.append([])
        else:
            groups[-1].append(a)
    groups = [g for g in groups if g]
    sums = [summarise(load_all(g)) for g in groups]

    width = 26
    print("=" * (width + 26 * len(sums)))
    print("ViVeSec AIBox — aggregated benchmark")
    print("=" * (width + 26 * len(sums)))
    header = "%-*s" % (width, "")
    for s in sums:
        header += "%-26s" % s["label"]
    print(header)
    print("-" * (width + 26 * len(sums)))
    for title, key in ROWS:
        if title is None:
            print()
            continue
        line = "%-*s" % (width, title)
        for s in sums:
            line += "%-26s" % cell(s, key)
        print(line)
    print()
    print("Counts are pooled over the runs; figures in [brackets] are the")
    print("observed min-max across runs. Overlapping ranges mean no movement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
