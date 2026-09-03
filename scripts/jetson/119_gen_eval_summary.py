#!/usr/bin/env python3
"""Summarise a gen_eval report and show the disagreements.

Usage: python3 119_gen_eval_summary.py report.json [report_b.json]
"""
import json
import sys


def load(path):
    with open(path) as fh:
        return json.load(fh)


def summarise(path):
    d = load(path)
    cases = d.get("cases") or d.get("results") or []
    total = len(cases)
    ok = sum(1 for c in cases if c.get("pass") or c.get("passed") or c.get("ok"))
    print("== %s ==" % path)
    print("  cases: %d   passed: %d" % (total, ok))
    by_kind = {}
    confs_ok, confs_bad = [], []
    for c in cases:
        k = c.get("kind") or c.get("type") or "?"
        p = bool(c.get("pass") or c.get("passed") or c.get("ok"))
        t, n = by_kind.get(k, (0, 0))
        by_kind[k] = (t + (1 if p else 0), n + 1)
        conf = c.get("confidence")
        if isinstance(conf, (int, float)):
            (confs_ok if p else confs_bad).append(conf)
    for k in sorted(by_kind):
        good, n = by_kind[k]
        print("    %-10s %d/%d" % (k, good, n))
    if confs_ok:
        print("  mean confidence  pass: %.1f" % (sum(confs_ok) / len(confs_ok)))
    if confs_bad:
        print("  mean confidence  fail: %.1f" % (sum(confs_bad) / len(confs_bad)))
    print()
    for c in cases:
        p = bool(c.get("pass") or c.get("passed") or c.get("ok"))
        if p:
            continue
        print("  [FAIL] %s" % c.get("question"))
        print("     expect: %s" % (c.get("accept") or c.get("expected")))
        print("     got   : %s" % (c.get("answer") or "")[:300].replace("\n", " "))
        print("     conf=%s hits=%s" % (c.get("confidence"), c.get("hits")))
        print()
    return {c.get("question"): bool(c.get("pass") or c.get("passed") or c.get("ok")) for c in cases}


res = [summarise(p) for p in sys.argv[1:]]
if len(res) == 2:
    a, b = res
    print("== disagreements (%s vs %s) ==" % (sys.argv[1], sys.argv[2]))
    for q in a:
        if q in b and a[q] != b[q]:
            print("  %-70s A=%s B=%s" % (q[:70], a[q], b[q]))
