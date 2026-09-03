"""Prototype: can the corpus itself repair an accent-less query?

The measurement showed the Hungarian collapse is a scoring collapse -- the right
document usually stays in the top five, it just falls under the production
floor. This checks whether rag_service/reaccent.py puts enough of the accents
back to bring the score up again.

Run inside the probe container (which has the module and the index):
    docker exec vivesec-rag-diacritics python /tmp/diacritics_reaccent_probe.py
"""
import collections
import json
import os
import sqlite3
import sys
import urllib.request

sys.path.insert(0, "/app/rag_service")
import reaccent  # noqa: E402

DB = "file:/data/rag_index_diacritics_probe.db?mode=ro"
PROBE = "http://127.0.0.1:8097"
KEY = os.environ["RAG_API_KEY"]
TOP_K = 5
FLOOR = 0.45

CASES = [
    ("hu", "hr-6ba4fc3f", "Mennyi a külföldi kiküldetés napidíja?"),
    ("hu", "hr-6ba4fc3f", "utazási költségtérítés napidíj"),
    ("hu", "hr-6ba4fc3f", "Milyen szabályok vonatkoznak a szabadságra és a távollétre?"),
    ("hu", "hr-6ba4fc3f", "Mennyi szabadság jár egy munkavállalónak?"),
    ("hu", "public-aa2010a2", "Hány munkavállalót foglalkoztat a Voltara csoport?"),
    ("hu", "public-aa2010a2", "Milyen termékeket gyárt a vállalat?"),
    ("de", "hr-6ba4fc3f", "Wie hoch ist die Tagespauschale für Auslandsreisen?"),
    ("de", "hr-6ba4fc3f", "Reisekostenrichtlinie Erstattung Übernachtung"),
    ("de", "hr-6ba4fc3f", "Welche Regelungen gelten für Überstunden?"),
    ("de", "public-aa2010a2", "Wie viele Mitarbeiter beschäftigt die Voltara Gruppe?"),
    ("da", "hr-6ba4fc3f", "Hvad er dagpengesatsen for rejser i udlandet?"),
    ("da", "hr-6ba4fc3f", "rejsepolitik godtgørelse måltider"),
    ("da", "hr-6ba4fc3f", "Hvilke regler gælder for ferie og fravær?"),
    ("da", "public-aa2010a2", "Hvor mange medarbejdere har Voltara koncernen?"),
    # English has no accents to lose: nothing here may change.
    ("en", "finance-114ed822", "What was the gross margin in 2025?"),
    ("en", "legal-c9902b93", "What is the liquidated damages cap in the Vestkraft contract?"),
    ("en", "hr-6ba4fc3f", "How many vacation days does an employee get?"),
]


def search(corpus_id, question):
    body = json.dumps({
        "corpus_id": corpus_id, "tenant_id": "default",
        "question": question, "top_k": TOP_K,
    }).encode()
    req = urllib.request.Request(
        PROBE + "/rag/search_context", data=body,
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp).get("contexts") or []


def name(ctx):
    return ctx["source_path"].rsplit("/", 1)[-1]


def interleave(a, b, limit):
    """The same fair merge the store uses for split questions."""
    out, seen = [], set()
    for rank in range(max(len(a), len(b))):
        for run in (a, b):
            if rank >= len(run):
                continue
            key = run[rank]["chunk_id"]
            if key in seen:
                continue
            seen.add(key)
            out.append(run[rank])
            if len(out) >= limit:
                return out
    return out


def main():
    conn = sqlite3.connect(DB, uri=True)
    vocabularies = {}
    for _, corpus_id, _ in CASES:
        if corpus_id in vocabularies:
            continue
        rows = conn.execute("SELECT text FROM chunks WHERE corpus_id=?", (corpus_id,))
        vocabularies[corpus_id] = reaccent.build(text for (text,) in rows)
        print("%-18s %d accented forms" % (corpus_id, len(vocabularies[corpus_id])))
    print()

    header = "%-3s %-38s %-7s %-6s %-5s %-6s %s" % (
        "ny", "kérdés", "változat", "top1", "rang", "floor", "top1 dokumentum")
    print(header)
    print("-" * len(header))
    tally = collections.Counter()
    for lang, corpus_id, question in CASES:
        plain = reaccent.fold(question)
        repaired = vocabularies[corpus_id].repair(plain)

        results = {}
        for label, text in (("orig", question), ("strip", plain), ("repair", repaired)):
            results[label] = search(corpus_id, text)
        # What the store would return if it retrieved for both spellings and
        # interleaved them, the way it already does for split questions. The
        # floor is applied per run, before the merge, as the store does.
        results["both"] = interleave(
            [c for c in results["strip"] if c["score"] >= FLOOR],
            [c for c in results["repair"] if c["score"] >= FLOOR],
            TOP_K)

        reference = name(results["orig"][0]) if results["orig"] else "-"
        for label in ("orig", "strip", "repair", "both"):
            ctx = results[label]
            top1 = ctx[0]["score"] if ctx else 0.0
            doc = name(ctx[0]) if ctx else "-"
            rank = "-"
            for i, c in enumerate(ctx, 1):
                if name(c) == reference:
                    rank = str(i)
                    break
            above = sum(1 for c in ctx if c["score"] >= FLOOR)
            print("%-3s %-38s %-7s %-6.3f %-5s %-6d %s" % (
                lang, question[:38], label, top1, rank, above, doc[:30]))
            tally[(lang, label, "n")] += 1
            tally[(lang, label, "above")] += 1 if above else 0
            tally[(lang, label, "rank1")] += 1 if rank == "1" else 0
            tally[(lang, label, "found")] += 1 if rank != "-" else 0
        if repaired != plain:
            print("     javítva: %s" % repaired)
        print()

    print("=== összegzés ===")
    print("%-3s %-7s %-4s %-26s %-18s %s" % (
        "ny", "vált.", "n", "van találat a floor felett", "top1 a helyes dok", "a helyes dok a top5-ben"))
    for lang in ("hu", "de", "da", "en"):
        for label in ("orig", "strip", "repair", "both"):
            n = tally[(lang, label, "n")]
            if n:
                print("%-3s %-7s %-4d %-26d %-18d %d" % (
                    lang, label, n, tally[(lang, label, "above")],
                    tally[(lang, label, "rank1")], tally[(lang, label, "found")]))


if __name__ == "__main__":
    main()
