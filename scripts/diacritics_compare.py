"""Production versus query-repair, both on the shipped configuration.

For every question, asked as a careful user types it and as most users type it:

    prod (:8090)     todays behaviour, floor 0.45, no repair
    repair (:8098)   same index, same floor, query repaired from the corpus

The reference is production's answer to the correctly spelled question -- that
is the answer the box is supposed to give either way.

Run on the Jetson: python3 diacritics_compare.py
"""
import collections
import json
import os
import time
import unicodedata
import urllib.request

PROD = "http://127.0.0.1:8090"
REPAIR = "http://127.0.0.1:8098"


def _prod_key():
    path = os.path.expanduser("~/prod_rag_api_key.txt")
    with open(path) as fh:
        return fh.read().strip()


KEYS = {PROD: _prod_key(), REPAIR: os.environ["RAG_API_KEY"]}
TOP_K = 5

HR = "hr-6ba4fc3f"
PUBLIC = "public-aa2010a2"
FINANCE = "finance-114ed822"
LEGAL = "legal-c9902b93"
ENG = "engineering-1cd6e192"

CASES = [
    ("hu", HR, "Mennyi a külföldi kiküldetés napidíja?"),
    ("hu", HR, "utazási költségtérítés napidíj"),
    ("hu", HR, "Milyen szabályok vonatkoznak a szabadságra és a távollétre?"),
    ("hu", HR, "Mennyi szabadság jár egy munkavállalónak?"),
    ("hu", HR, "Mekkora a szállásköltség felső határa Koppenhágában?"),
    ("hu", HR, "Hány óra a heti teljes munkaidő?"),
    ("hu", PUBLIC, "Hány munkavállalót foglalkoztat a Voltara csoport?"),
    ("hu", PUBLIC, "Milyen termékeket gyárt a vállalat?"),
    ("hu", PUBLIC, "Mikor alapították a céget és hol van a központja?"),
    ("de", HR, "Wie hoch ist die Tagespauschale für Auslandsreisen?"),
    ("de", HR, "Reisekostenrichtlinie Erstattung Übernachtung"),
    ("de", HR, "Welche Regelungen gelten für Überstunden?"),
    ("de", PUBLIC, "Wie viele Mitarbeiter beschäftigt die Voltara Gruppe?"),
    ("da", HR, "Hvad er dagpengesatsen for rejser i udlandet?"),
    ("da", HR, "rejsepolitik godtgørelse måltider"),
    ("da", HR, "Hvilke regler gælder for ferie og fravær?"),
    ("da", PUBLIC, "Hvor mange medarbejdere har Voltara koncernen?"),
    # No accents to lose: these must be identical on both instances.
    ("en", FINANCE, "What was the gross margin in 2025?"),
    ("en", FINANCE, "What is the covenant on net debt to EBITDA?"),
    ("en", LEGAL, "What is the liquidated damages cap in the Vestkraft contract?"),
    ("en", LEGAL, "Which open non-conformities came out of the ISO 27001 audit?"),
    ("en", ENG, "Why is the Helios project delayed?"),
    ("en", HR, "How many vacation days does an employee get?"),
]

EXTRA_FOLD = {"ø": "o", "æ": "ae", "ß": "ss"}


def strip_accents(text):
    return "".join(
        EXTRA_FOLD.get(c, c)
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
        headers={"Content-Type": "application/json", "X-API-Key": KEYS[url]},
    )
    started = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        contexts = json.load(resp).get("contexts") or []
    return contexts, (time.time() - started) * 1000


def name(ctx):
    return ctx["source_path"].rsplit("/", 1)[-1]


def rank_of(contexts, reference):
    for i, c in enumerate(contexts, 1):
        if name(c) == reference:
            return i
    return None


def main():
    header = "%-3s %-40s %-12s %-4s %-6s %-5s %s" % (
        "ny", "kérdés", "hogyan/hol", "db", "top1", "rang", "top1 dokumentum")
    print(header)
    print("-" * len(header))

    tally = collections.Counter()
    latency = collections.defaultdict(list)
    for lang, corpus_id, question in CASES:
        plain = strip_accents(question)
        runs = [
            ("ékezettel/prod", PROD, question),
            ("ékezettel/új", REPAIR, question),
            ("nélküle/prod", PROD, plain),
            ("nélküle/új", REPAIR, plain),
        ]
        reference = None
        for label, url, text in runs:
            contexts, ms = search(url, corpus_id, text)
            latency[label].append(ms)
            if reference is None:
                reference = name(contexts[0]) if contexts else "-"
            rank = rank_of(contexts, reference)
            print("%-3s %-40s %-12s %-4d %-6.3f %-5s %s" % (
                lang, question[:40], label, len(contexts),
                contexts[0]["score"] if contexts else 0.0,
                rank if rank else "-",
                name(contexts[0])[:30] if contexts else "-"))
            tally[(lang, label, "n")] += 1
            tally[(lang, label, "hit")] += 1 if contexts else 0
            tally[(lang, label, "top1")] += 1 if rank == 1 else 0
            tally[(lang, label, "top5")] += 1 if rank else 0
        print()

    print("=== összegzés ===")
    print("%-3s %-12s %-4s %-14s %-18s %s" % (
        "ny", "hogyan/hol", "n", "van találat", "top1 a helyes dok", "top5-ben"))
    for lang in ("hu", "de", "da", "en"):
        for label in ("ékezettel/prod", "ékezettel/új", "nélküle/prod", "nélküle/új"):
            n = tally[(lang, label, "n")]
            if n:
                print("%-3s %-12s %-4d %-14d %-18d %d" % (
                    lang, label, n, tally[(lang, label, "hit")],
                    tally[(lang, label, "top1")], tally[(lang, label, "top5")]))

    print()
    print("=== válaszidő (ms) ===")
    for label, values in latency.items():
        values = sorted(values)
        print("%-14s n=%d  medián %.0f  max %.0f" % (
            label, len(values), values[len(values) // 2], values[-1]))


if __name__ == "__main__":
    main()
