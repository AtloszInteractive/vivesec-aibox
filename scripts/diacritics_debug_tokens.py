"""Why was a token not repaired? Prints the vocabulary's view of each token."""
import sqlite3
import sys

sys.path.insert(0, "/app/rag_service")
import reaccent  # noqa: E402

DB = "file:/data/rag_index_diacritics_probe.db?mode=ro"
CORPUS = sys.argv[1] if len(sys.argv) > 1 else "hr-6ba4fc3f"
TOKENS = sys.argv[2:] or ["kulfoldi", "kikuldetes", "vallalat", "tavolletre",
                          "szabadsagra", "termekeket", "koltsegterites"]

conn = sqlite3.connect(DB, uri=True)
rows = conn.execute("SELECT text FROM chunks WHERE corpus_id=?", (CORPUS,))
vocabulary = reaccent.build(text for (text,) in rows)
print("%s: %d accented forms" % (CORPUS, len(vocabulary)))

for token in TOKENS:
    folded = reaccent.fold(token)
    counter = vocabulary._spellings.get(folded)
    match = vocabulary._best_match(folded)
    print("\n%-16s folded=%s" % (token, folded))
    print("  spellings of that exact form: %s" % (dict(counter) if counter else "-"))
    print("  longest-prefix accented word: %s" % match)
    print("  repair -> %s" % vocabulary.repair(token))
    # what does the corpus actually know that starts similarly?
    near = [(f, s) for f, s in zip(vocabulary._folded, vocabulary._accented)
            if f.startswith(folded[:5])]
    print("  corpus words starting with '%s': %s" % (folded[:5], near[:6]))
