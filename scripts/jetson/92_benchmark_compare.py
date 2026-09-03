#!/usr/bin/env python3
"""Compare two 91_benchmark.py reports: what did the build change?

The two boxes hold different corpora, so this deliberately separates:

  BUILD SIGNALS      — corpus-independent, safe to attribute to the build
  INFORMATIONAL      — corpus-dependent, shown but never used as a verdict

Anything that cannot be attributed cleanly is labelled instead of silently
averaged, because a misattributed win is worse than no measurement.

    python 92_benchmark_compare.py bench_demo.json bench_dev_new.json
"""
import json
import sys


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def head(title):
    print("\n" + title)
    print("-" * len(title))


def row(label, a, b, better=None):
    """better: 'up' | 'down' | None (None = report only, no judgement)."""
    mark = ""
    if better and isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if b != a:
            good = (b > a) if better == "up" else (b < a)
            mark = "  IMPROVED" if good else "  REGRESSED"
    print("  %-26s %-22s -> %-22s%s" % (label, a, b, mark))


def frac(d, num, den):
    if not d:
        return "-"
    return "%s/%s" % (d.get(num), d.get(den))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    a, b = load(sys.argv[1]), load(sys.argv[2])
    la, lb = a.get("label", "A"), b.get("label", "B")
    print("=" * 72)
    print("ViVeSec AIBox benchmark:  %s  ->  %s" % (la, lb))
    print("=" * 72)

    ea, eb = a.get("environment", {}), b.get("environment", {})
    aea, aeb = ea.get("adapter_env", {}), eb.get("adapter_env", {})
    head("BUILD")
    for k in ("ADAPTER_GEN_MODEL", "ADAPTER_THINK", "ADAPTER_NUM_PREDICT",
              "ADAPTER_ANALYZE_NUM_CTX", "ADAPTER_ANALYZE_MAX_CHARS"):
        row(k.replace("ADAPTER_", ""), aea.get(k, "-"), aeb.get(k, "-"))
    row("adapter image", (ea.get("images") or {}).get("adapter", "-"),
        (eb.get("images") or {}).get("adapter", "-"))
    row("rag image", (ea.get("images") or {}).get("rag", "-"),
        (eb.get("images") or {}).get("rag", "-"))

    ca, cb = a.get("capabilities", {}), b.get("capabilities", {})
    head("CAPABILITIES (probed, not assumed)")
    row("/rag/document_context", ca.get("rag_document_context"),
        cb.get("rag_document_context"))
    row("#analyze action", ca.get("analyze_supported"), cb.get("analyze_supported"))

    head("BUILD SIGNALS — corpus-independent, these decide the comparison")
    lga, lgb = a.get("language", {}), b.get("language", {})

    def mode_cell(rep, mode):
        m = (rep.get("by_mode") or {}).get(mode)
        return "%s/%s" % (m["ok"], m["trials"]) if m else "-"

    row("answer language matches", frac(lga, "language_match", "total"),
        frac(lgb, "language_match", "total"))
    row("  no lang given (UI path)", mode_cell(lga, "auto"), mode_cell(lgb, "auto"))
    row("  lang given (control)", mode_cell(lga, "explicit"),
        mode_cell(lgb, "explicit"))
    row("  (numeric)", lga.get("language_match"), lgb.get("language_match"), "up")
    row("refusal correct", frac(lga, "refusal_correct", "total"),
        frac(lgb, "refusal_correct", "total"))
    row("  (numeric)", lga.get("refusal_correct"), lgb.get("refusal_correct"), "up")

    ga, gb = a.get("grounding", {}), b.get("grounding", {})
    row("cases w/ ungrounded nums", ga.get("with_ungrounded"),
        gb.get("with_ungrounded"), "down")
    row("suppressed answers", ga.get("suppressed"), gb.get("suppressed"))
    row("mean confidence", ga.get("mean_confidence"), gb.get("mean_confidence"))

    aca, acb = a.get("actions", {}), b.get("actions", {})
    row("actions generated", aca.get("generated"), acb.get("generated"), "up")
    row("truncated outputs", aca.get("truncated_outputs"),
        acb.get("truncated_outputs"), "down")
    row("mean action tok/s", aca.get("mean_tok_per_s", "-"),
        acb.get("mean_tok_per_s", "-"), "up")
    # Wall time per action falls when probes refuse (a refusal is cheap), so it
    # is only comparable when the same probes generated in both runs.
    row("mean action seconds", aca.get("mean_seconds"), acb.get("mean_seconds"))
    if aca.get("generated") != acb.get("generated"):
        print("      ^ not comparable: a different number of probes generated;")
        print("        judge speed on tok/s above.")

    ana, anb = a.get("analyze", {}), b.get("analyze", {})
    row("analyze documents w/ text", ana.get("analyzable", "-"),
        anb.get("analyzable", "-"))
    row("analyze fully covered", ana.get("fully_covered"), anb.get("fully_covered"), "up")
    row("analyze degenerate", ana.get("degenerate_answers"),
        anb.get("degenerate_answers"), "down")
    row("  files w/o indexed text", ana.get("no_text_documents", "-"),
        anb.get("no_text_documents", "-"))

    head("PERFORMANCE per action")
    ba = {c["action"]: c for c in (aca.get("cases") or [])}
    bb = {c["action"]: c for c in (acb.get("cases") or [])}
    for action in sorted(set(ba) | set(bb)):
        x, y = ba.get(action, {}), bb.get(action, {})
        row(action, "%ss %s tok/s%s" % (x.get("seconds"), x.get("tok_per_s"),
                                        " REFUSED" if x.get("refused") else ""),
            "%ss %s tok/s%s" % (y.get("seconds"), y.get("tok_per_s"),
                                " REFUSED" if y.get("refused") else ""))

    head("LANGUAGE detail (obeyed / trials, and what it answered in)")

    def lang_cell(rep, lang):
        per = (rep.get("per_language") or {}).get(lang)
        if per:
            return "%s/%s %s" % (per["ok"], per["trials"], ",".join(per["answered_in"]))
        # schema < 5: a single pass, so the case row is the whole story
        c = {x["asked"]: x for x in (rep.get("cases") or [])}.get(lang, {})
        if not c:
            return "-"
        return "%s%s" % (c.get("detected"), "" if c.get("language_ok") else " (WRONG)")

    for lang in ("English", "Hungarian", "Danish", "German"):
        row(lang, lang_cell(lga, lang), lang_cell(lgb, lang))
    row("unstable languages", ",".join(lga.get("unstable_languages") or []) or "-",
        ",".join(lgb.get("unstable_languages") or []) or "-")

    head("INFORMATIONAL — corpus differs, NOT a build signal")
    coa, cob = a.get("corpus", {}), b.get("corpus", {})
    row("files in drive", coa.get("file_count"), cob.get("file_count"))
    row("drive", (a.get("environment") or {}).get("drive"),
        (b.get("environment") or {}).get("drive"))
    ia = (ea.get("index") or {})
    ib = (eb.get("index") or {})
    row("indexed documents", ia.get("documents"), ib.get("documents"))
    row("indexed chunks", ia.get("chunks"), ib.get("chunks"))
    if coa.get("file_count") != cob.get("file_count"):
        print("\n  ! The two drives are NOT the same corpus. Factual accuracy is")
        print("    therefore not comparable between these runs; only the build")
        print("    signals above are.")

    head("ANALYZE detail")
    for label, rep in ((la, ana), (lb, anb)):
        print("  %s:" % label)
        if not rep.get("cases"):
            print("    (no analyze data)")
            continue
        for c in rep["cases"]:
            print("    %-8s %-42s %s/%s chunks  %ss  %s chars  conf=%s%s"
                  % (c.get("size"), (c.get("file") or "")[:42], c.get("chunks_used"),
                     c.get("chunks_total"), c.get("seconds"), c.get("body_chars"),
                     c.get("confidence"),
                     "  NO INDEXED TEXT" if c.get("no_text") else ""))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
