"""End-to-end: what does a user get today, and what with the query repair?

Both endpoints are the same image serving the same index with the production
floor (0.45); they differ in RAG_QUERY_REACCENT alone. Each question is asked
the way a user without accents would type it; the reference is the same
question spelled correctly, which is what the answer should have been.

    python3 diacritics_endtoend.py
"""
import collections
import json
import os
import sys
import unicodedata
import urllib.request

TODAY = "http://127.0.0.1:8100"      # repair off
REPAIRED = "http://127.0.0.1:8098"   # repair on
KEY = os.environ["RAG_API_KEY"]
TOP_K = 5

HR = "hr-6ba4fc3f"
PUBLIC = "public-aa2010a2"
FINANCE = "finance-114ed822"
LEGAL = "legal-c9902b93"
ENGINEERING = "engineering-1cd6e192"

CASES = [
    ("hu", HR, "Mennyi a külföldi kiküldetés napidíja?"),
    ("hu", HR, "utazási költségtérítés napidíj"),
    ("hu", HR, "Milyen szabályok vonatkoznak a szabadságra és a távollétre?"),
    ("hu", HR, "Mennyi szabadság jár egy munkavállalónak?"),
    ("hu", HR, "Mekkora a szállásköltség felső határa Koppenhágában?"),
    ("hu", HR, "Hány óra a heti teljes munkaidő?"),
    ("hu", PUBLIC, "Hány munkavállalót foglalkoztat a Voltara csoport?"),
    ("hu", PUBLIC, "Milyen termékeket gyárt a vállalat?"),
    ("hu", PUBLIC, "Mekkora volt az árbevétel 2025-ben?"),
    ("de", HR, "Wie hoch ist die Tagespauschale für Auslandsreisen?"),
    ("de", HR, "Reisekostenrichtlinie Erstattung Übernachtung"),
    ("de", HR, "Welche Regelungen gelten für Überstunden?"),
    ("de", PUBLIC, "Wie viele Mitarbeiter beschäftigt die Voltara Gruppe?"),
    ("da", HR, "Hvad er dagpengesatsen for rejser i udlandet?"),
    ("da", HR, "rejsepolitik godtgørelse måltider"),
    ("da", HR, "Hvilke regler gælder for ferie og fravær?"),
    ("da", PUBLIC, "Hvor mange medarbejdere har Voltara koncernen?"),
    # English cannot lose accents; nothing here may move.
    ("en", FINANCE, "What was the gross margin in 2025?"),
    ("en", LEGAL, "What is the liquidated damages cap in the Vestkraft contract?"),
    ("en", HR, "How many vacation days does an employee get?"),
    ("en", ENGINEERING, "What is the status of the Helios project?"),
    ("en", FINANCE, "What is the covenant on net debt to EBITDA?"),
]

_EXTRA = {"ø": "o", "æ": "ae", "ß": "ss"}


def strip_accents(text):
    return "".join(
        _EXTRA.get(c, c)
        for c in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(c)
    )


def search(url, corpus_id, question):
    body = json.dumps({
        "corpus_id": corpus_id, "tenant_id": "default",
        "question": question, "top_k": TOP_K,
    }).encode()
    req = urllib.request.Request(
        url + "/rag/search_context", data=body,
        headers={"Content-Type": "application/json", "X-API-Key": KEY},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp).get("contexts") or []


def name(ctx):
    return ctx["source_path"].rsplit("/", 1)[-1]


def rank_of(contexts, document):
    for i, ctx in enumerate(contexts, 1):
        if name(ctx) == document:
            return i
    return None


def main():
    header = "%-3s %-40s %-9s %-6s %-5s %s" % (
        "ny", "kérdés ékezet nélkül", "végpont", "top1", "rang", "top1 dokumentum")
    print(header)
    print("-" * len(header))

    tally = collections.Counter()
    for lang, corpus_id, question in CASES:
        typed = strip_accents(question)
        reference = search(TODAY, corpus_id, question)
        expected = name(reference[0]) if reference else "-"

        for label, url in (("ma", TODAY), ("javítva", REPAIRED)):
            ctx = search(url, corpus_id, typed)
            rank = rank_of(ctx, expected)
            print("%-3s %-40s %-9s %-6.3f %-5s %s" % (
                lang, typed[:40], label,
                ctx[0]["score"] if ctx else 0.0,
                rank if rank else "-",
                name(ctx[0])[:34] if ctx else "NINCS TALÁLAT"))
            tally[(lang, label, "n")] += 1
            tally[(lang, label, "any")] += 1 if ctx else 0
            tally[(lang, label, "top1")] += 1 if rank == 1 else 0
            tally[(lang, label, "top5")] += 1 if rank else 0
        print()

    print("=== összegzés (ékezet nélkül beírt kérdésre) ===")
    print("%-3s %-9s %-4s %-14s %-20s %s" % (
        "ny", "végpont", "n", "van találat", "helyes dok a top1", "helyes dok a top5"))
    for lang in ("hu", "de", "da", "en"):
        for label in ("ma", "javítva"):
            n = tally[(lang, label, "n")]
            if n:
                print("%-3s %-9s %-4d %-14d %-20d %d" % (
                    lang, label, n, tally[(lang, label, "any")],
                    tally[(lang, label, "top1")], tally[(lang, label, "top5")]))


if __name__ == "__main__":
    main()
