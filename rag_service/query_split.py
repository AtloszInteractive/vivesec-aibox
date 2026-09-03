"""Split a conjunctive question into the independent questions it contains.

"What penalty applies to X, and which court ruling covers Y" is two questions
glued together. A single embedding of the whole lands between the two topics and
reliably retrieves only one of them -- measured on the multi-hop evaluation set,
three quarters of the failures were exactly this: one document found, its partner
missed. Splitting is the cheapest fix that works; feeding the first round's text
back in as a second query was measured and gained nothing, because it stays in
the same topic.

Modes (RAG_QUERY_SPLIT):
    off          never split
    clause       split only on ", <conjunction> " -- a real clause boundary
    conjunction  also split on a bare conjunction when both halves are
                 substantial (more recall, some precision cost)
"""
import os
import re

CONJUNCTIONS = (
    "és", "valamint", "illetve",     # hu
    "and",                            # en
    "og", "samt",                     # da
    "und", "sowie",                   # de
)

_ALTERNATION = "|".join(CONJUNCTIONS)
# A comma before the conjunction marks a clause boundary; a bare conjunction is
# usually just a list ("eyes, teeth and fingers").
_CLAUSE_RE = re.compile(r",\s+(?:%s)\s+" % _ALTERNATION, re.IGNORECASE)
_BARE_RE = re.compile(r"\s+(?:%s)\s+" % _ALTERNATION, re.IGNORECASE)

_CLAUSE_MIN_WORDS = 3
_BARE_MIN_WORDS = 5

# "conjunction" measured best on the evaluation set: multi-hop recall 0.515 ->
# 0.647 and overall recall past the 0.90 acceptance bar, for ~0.014 precision.
MODE = os.environ.get("RAG_QUERY_SPLIT", "conjunction").strip().lower()


def _halves(pattern, text, min_words):
    parts = pattern.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    parts = [p.strip().strip(",") for p in parts]
    if any(len(p.split()) < min_words for p in parts):
        return None
    return [p if p.endswith("?") else p + "?" for p in parts]


def split_question(text, mode=None):
    """Return the sub-questions to retrieve for; a single-element list if none."""
    mode = (mode or MODE).strip().lower()
    if not text or mode == "off":
        return [text]
    parts = _halves(_CLAUSE_RE, text, _CLAUSE_MIN_WORDS)
    if parts:
        return parts
    if mode == "conjunction":
        parts = _halves(_BARE_RE, text, _BARE_MIN_WORDS)
        if parts:
            return parts
    return [text]


def interleave(runs, limit):
    """Merge per-sub-question results so each half gets a fair share of the slots.

    Taking the globally best scores would not work: the two halves sit on
    different score scales, and the stronger half would fill every slot.
    """
    out = []
    seen = set()
    depth = max((len(r) for r in runs), default=0)
    for rank in range(depth):
        for run in runs:
            if rank >= len(run):
                continue
            key = run[rank][0]
            if key in seen:
                continue
            seen.add(key)
            out.append(run[rank])
            if len(out) >= limit:
                return out
    return out
