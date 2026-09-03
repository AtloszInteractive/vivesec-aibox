"""How much does dropping diacritics cost the retrieval? Run on the Jetson.

Each question is asked three ways against the no-floor probe instance (:8097,
RAG_MIN_SCORE=0) so we can separate "the floor cut it" from "it was never
retrieved":

    orig   as a careful user types it
    strip  accents simply removed  (utazási -> utazasi, für -> fur)
    trans  the conventional transliteration (fuer, oe/aa) -- German and Danish
           only, where it is an established spelling

The reference is the question's own accented run: whichever document it ranks
first is what the other two spellings should also find. That needs no ground
truth, and it measures exactly what a user notices.

Usage: python3 diacritics_measure.py [probe_url] [--floor 0.45]
"""
import json
import os
import sys
import unicodedata
import urllib.request

PROBE = "http://127.0.0.1:8097"
KEY = os.environ["RAG_API_KEY"]
TOP_K = 5

HR = "hr-6ba4fc3f"
PUBLIC = "public-aa2010a2"
FINANCE = "finance-114ed822"
LEGAL = "legal-c9902b93"

# (language, corpus, question as correctly spelled)
CASES = [
    ("hu", HR, "Mennyi a külföldi kiküldetés napidíja?"),
    ("hu", HR, "utazási költségtérítés napidíj"),
    ("hu", HR, "Milyen szabályok vonatkoznak a szabadságra és a távollétre?"),
    ("hu", PUBLIC, "Hány munkavállalót foglalkoztat a Voltara csoport?"),
    ("hu", PUBLIC, "Milyen termékeket gyárt a vállalat?"),
    ("de", HR, "Wie hoch ist die Tagespauschale für Auslandsreisen?"),
    ("de", HR, "Reisekostenrichtlinie Erstattung Übernachtung"),
    ("de", HR, "Welche Regelungen gelten für Überstunden?"),
    ("de", PUBLIC, "Wie viele Mitarbeiter beschäftigt die Voltara Gruppe?"),
    ("de", PUBLIC, "Welche Produkte stellt das Unternehmen für Energiespeicher her?"),
    ("da", HR, "Hvad er dagpengesatsen for rejser i udlandet?"),
    ("da", HR, "rejsepolitik godtgørelse måltider"),
    ("da", HR, "Hvilke regler gælder for ferie og fravær?"),
    ("da", PUBLIC, "Hvor mange medarbejdere har Voltara koncernen?"),
    ("da", PUBLIC, "Hvilke produkter fremstiller virksomheden?"),
    # English has no diacritics; these are the control group -- any movement
    # here would mean the measurement itself is noisy.
    ("en", FINANCE, "What was the gross margin in 2025?"),
    ("en", LEGAL, "What is the liquidated damages cap in the Vestkraft contract?"),
]

TRANSLITERATION = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "å": "aa", "ø": "oe", "æ": "ae",
    "Å": "Aa", "Ø": "Oe", "Æ": "Ae",
}


def strip_accents(text):
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(c)
    ).replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("Æ", "Ae")


def transliterate(text):
    return "".join(TRANSLITERATION.get(c, c) for c in text)


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


def main():
    url = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else PROBE
    floor = 0.45
    if "--floor" in sys.argv:
        floor = float(sys.argv[sys.argv.index("--floor") + 1])

    print("probe=%s  top_k=%d  production floor=%.2f" % (url, TOP_K, floor))
    print()
    header = "%-3s %-42s %-7s %-6s %-5s %-6s %s" % (
        "ny", "kérdés", "változat", "top1", "rang", "floor", "top1 dokumentum")
    print(header)
    print("-" * len(header))

    summary = {}
    for lang, corpus, question in CASES:
        variants = [("orig", question), ("strip", strip_accents(question))]
        trans = transliterate(question)
        if trans != question and trans != variants[1][1]:
            variants.append(("trans", trans))

        reference = None
        for label, text in variants:
            ctx = search(url, corpus, text)
            top1 = ctx[0]["score"] if ctx else 0.0
            doc = name(ctx[0]) if ctx else "-"
            if label == "orig":
                reference = doc
                rank = "1" if ctx else "-"
            else:
                rank = "-"
                for i, c in enumerate(ctx, 1):
                    if name(c) == reference:
                        rank = str(i)
                        break
            above = sum(1 for c in ctx if c["score"] >= floor)
            print("%-3s %-42s %-7s %-6.3f %-5s %-6d %s" % (
                lang, question[:42], label, top1, rank, above, doc[:34]))

            if label != "orig":
                key = (lang, label)
                s = summary.setdefault(key, {"n": 0, "lost_doc": 0, "no_hit": 0, "drop": 0.0})
                s["n"] += 1
                if rank == "-":
                    s["lost_doc"] += 1
                if above == 0:
                    s["no_hit"] += 1
                s["drop"] += ref_score - top1
            else:
                ref_score = top1
        print()

    print("=== összegzés (az eredeti íráshoz képest) ===")
    print("%-3s %-6s %-4s %-16s %-18s %s" % (
        "ny", "vált.", "n", "elveszti a dokot", "0 találat a floor", "átl. pontvesztés"))
    for (lang, label), s in sorted(summary.items()):
        print("%-3s %-6s %-4d %-16d %-18d %.3f" % (
            lang, label, s["n"], s["lost_doc"], s["no_hit"], s["drop"] / s["n"]))


if __name__ == "__main__":
    main()
