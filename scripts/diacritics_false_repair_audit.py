"""How often does the repair change a word into a different word?

The repair only fires on tokens the corpus does not contain as typed, so every
word that really occurs is safe by construction. The risk is elsewhere: a word
the corpus has never seen may share a prefix with an accented corpus word and
get rewritten into something that means something else ("szelet" -> "szélet").

This measures that. The innocent set is every word of the English-language
corpora that the Hungarian-heavy HR corpus does not contain, plus a hand-picked
list of Hungarian pairs that differ only in their accents.

    docker exec vivesec-rag-diacritics python /tmp/diacritics_false_repair_audit.py
"""
import re
import sqlite3
import sys

sys.path.insert(0, "/app/rag_service")
import reaccent  # noqa: E402

DB = "file:/data/rag_index_diacritics_probe.db?mode=ro"
VOCAB_CORPUS = "hr-6ba4fc3f"          # the corpus with the most accented words
INNOCENT_CORPORA = ("engineering-1cd6e192", "finance-114ed822", "legal-c9902b93")

# Hungarian words whose accent-less spelling is a different, real word. If the
# repair rewrites these, a user asking about the left-hand word is answered
# from documents about the right-hand one.
TRAPS = [
    ("szelet", "szélet/szeletel"),
    ("kerek", "kérek/kerék"),
    ("egesz", "egész"),
    ("terem", "térem"),
    ("verem", "vérem"),
    ("meret", "méret"),
    ("beszed", "beszéd"),
    ("hatar", "határ"),
    ("koros", "kóros/körös"),
    ("orult", "őrült"),
    ("felet", "félet/felét"),
    ("tavol", "távol"),
    ("erted", "érted"),
    ("szeles", "széles/szeles"),
    ("elem", "elém"),
]


def main():
    conn = sqlite3.connect(DB, uri=True)
    rows = conn.execute("SELECT text FROM chunks WHERE corpus_id=?", (VOCAB_CORPUS,))
    vocabulary = reaccent.build(text for (text,) in rows)
    print("szótár: %s, %d ékezetes alak" % (VOCAB_CORPUS, len(vocabulary)))

    words = set()
    for corpus_id in INNOCENT_CORPORA:
        for (text,) in conn.execute(
                "SELECT text FROM chunks WHERE corpus_id=?", (corpus_id,)):
            for word in reaccent.WORD_RE.findall(text or ""):
                word = word.lower()
                if len(word) >= 5 and reaccent.fold(word) == word:
                    words.add(word)
    # Anything the vocabulary corpus itself contains is protected already.
    innocent = sorted(w for w in words if not vocabulary._spellings.get(w, {}).get(w))
    print("ártatlan szavak (más korpuszból, a szótár-korpuszban nem szerepelnek): %d"
          % len(innocent))

    changed = [(w, vocabulary.repair(w)) for w in innocent]
    changed = [(w, r) for w, r in changed if r != w]
    print("ebből átírva: %d (%.2f%%)" % (len(changed), 100.0 * len(changed) / max(len(innocent), 1)))
    for word, repaired in changed[:40]:
        print("   %-22s -> %s" % (word, repaired))

    print()
    print("=== magyar csapdaszavak ===")
    for word, meaning in TRAPS:
        repaired = vocabulary.repair(word)
        mark = "ÁTÍRVA" if repaired != word else "ok"
        print("   %-10s (%-16s) -> %-14s %s" % (word, meaning, repaired, mark))


if __name__ == "__main__":
    main()
