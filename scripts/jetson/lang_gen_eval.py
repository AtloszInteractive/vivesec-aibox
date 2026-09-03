#!/usr/bin/env python3
"""Per-language generation eval: same 5 questions in hu/en/de/da through an
adapter (the way the UI asks: no explicit lang), scoring (a) expected value
present, (b) answer language == question language.

Usage: python3 lang_gen_eval.py http://127.0.0.1:8089 [--out report.json]
"""
import base64
import json
import re
import sys
import time
import urllib.request

ADAPTER = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8089"
OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None

# (drive, expected value tokens ANY-of, {lang: question})
CASES = [
    ("finance", ["14.7", "14,7"], {
        "en": "What was the revenue in Q2 2026 and how did it compare to plan?",
        "hu": "Mekkora volt az árbevétel 2026 második negyedévében, és hogyan viszonyult a tervhez?",
        "de": "Wie hoch war der Umsatz im zweiten Quartal 2026 und wie verhielt er sich zum Plan?",
        "da": "Hvor stor var omsætningen i andet kvartal 2026, og hvordan var den i forhold til planen?",
    }),
    ("legal", ["148,000", "148 000", "148.000", "148000"], {
        "en": "How much is the annual fee for the AI platform and what notice period applies?",
        "hu": "Mennyi az AI platform éves díja, és milyen felmondási idő vonatkozik rá?",
        "de": "Wie hoch ist die Jahresgebühr für die KI-Plattform und welche Kündigungsfrist gilt?",
        "da": "Hvor meget koster AI-platformen om året, og hvilket opsigelsesvarsel gælder?",
    }),
    ("hr", ["32"], {
        "en": "What is the daily allowance in Hungary and in Denmark?",
        "hu": "Mennyi a napidíj Magyarországon és Dániában?",
        "de": "Wie hoch ist das Tagegeld in Ungarn und in Dänemark?",
        "da": "Hvor stor er diæten i Ungarn og i Danmark?",
    }),
    ("public", ["214"], {
        "en": "How many employees does Voltara have in total?",
        "hu": "Összesen hány alkalmazottja van a Voltarának?",
        "de": "Wie viele Mitarbeiter hat Voltara insgesamt?",
        "da": "Hvor mange medarbejdere har Voltara i alt?",
    }),
    ("engineering", ["DK-010", "SE-002"], {
        "en": "Which site had the most interventions in H1 2026?",
        "hu": "Melyik telephelyen volt a legtöbb beavatkozás 2026 első félévében?",
        "de": "Welcher Standort hatte die meisten Eingriffe im ersten Halbjahr 2026?",
        "da": "Hvilket anlæg havde flest indgreb i første halvår 2026?",
    }),
]

_MARKERS = {
    "hu": ["és", "az ", "nem ", "volt", "mértéke", "alapján", "első", "legtöbb",
           "díja", "vonatkozik", "összesen", "dokumentumok"],
    "de": ["und", "der ", "die ", "das ", "ist ", "im ", "für", "betrug", "wurde",
           "keine", "dokumente", "insgesamt"],
    "da": ["og ", "var ", "på ", "af ", "ikke", "til ", "blev", "havde", "flest",
           "dokumenterne", "udgjorde", "i alt"],
    "en": ["the ", "was ", "and ", "of ", "in ", "is ", "to ", "there ", "with",
           "documents", "total"],
}


def detect_lang(text):
    t = " " + (text or "").lower() + " "
    scores = {}
    for lang, words in _MARKERS.items():
        scores[lang] = sum(t.count(" " + w if not w.endswith(" ") else " " + w) for w in words)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "?"


def strip_footer(text):
    # the audit footer is always Hungarian -> would skew detection
    return re.split(r"\n-{2,}\n|\*\*Adatkontroll", text or "")[0]


def ask(drive, question):
    h = base64.urlsafe_b64encode(("/storage/drives/%s/" % drive).encode()).decode().rstrip("=")
    req = urllib.request.Request(
        ADAPTER + "/api/v1/ui/query",
        data=json.dumps({"query": question, "top_k": 5}).encode(),
        headers={"Content-Type": "application/json", "VVS-Drive": h, "VVS-User": "lars.nygaard"},
        method="POST")
    with urllib.request.urlopen(req, timeout=420) as r:
        return json.loads(r.read())


def main():
    results = []
    val_ok = lang_ok = n = 0
    per_lang = {}
    for drive, accept, questions in CASES:
        for qlang in ("en", "hu", "de", "da"):
            q = questions[qlang]
            t0 = time.time()
            try:
                d = ask(drive, q)
            except Exception as e:  # noqa: BLE001
                print("[ERR] %s %s: %s" % (qlang, q[:40], e))
                continue
            body = strip_footer(d.get("answer") or "")
            alang = detect_lang(body)
            v = any(a.lower() in body.lower() for a in accept)
            l = alang == qlang
            n += 1
            val_ok += v
            lang_ok += l
            s = per_lang.setdefault(qlang, {"n": 0, "value": 0, "lang": 0})
            s["n"] += 1
            s["value"] += v
            s["lang"] += l
            flag = ("VAL+" if v else "val-") + (" LANG+" if l else " lang->" + alang)
            print("[%s] %-4s %-11s %s  (%.0fs)" % (flag, qlang, drive, q[:52], time.time() - t0))
            print("       %s" % body.strip().replace("\n", " ")[:180])
            results.append({"drive": drive, "qlang": qlang, "question": q,
                            "answer": body.strip(), "value_ok": v,
                            "answer_lang": alang, "lang_ok": l})
    print("=" * 70)
    print("TOTAL: value %d/%d  |  answer-language %d/%d" % (val_ok, n, lang_ok, n))
    for lang in ("en", "hu", "de", "da"):
        s = per_lang.get(lang)
        if s:
            print("  %s: value %d/%d  lang %d/%d" % (lang, s["value"], s["n"], s["lang"], s["n"]))
    if OUT:
        json.dump({"adapter": ADAPTER, "results": results}, open(OUT, "w"),
                  ensure_ascii=False, indent=1)
        print("report:", OUT)


if __name__ == "__main__":
    main()
