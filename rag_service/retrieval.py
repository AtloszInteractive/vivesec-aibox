import os
import re
import unicodedata


def terms(question, limit=32):
    normalized = unicodedata.normalize("NFKD", question.casefold())
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return list(dict.fromkeys(re.findall(r"[^\W_]{2,}", normalized)))[:limit]


def match_source(path, sources):
    normalized = (path or "").replace("\\", "/").casefold()
    return not sources or any(
        normalized == source.replace("\\", "/").casefold()
        if "/" in source.replace("\\", "/")
        else normalized.rsplit("/", 1)[-1] == source.casefold()
        for source in sources)


# -- spreading the context over documents --------------------------------------
# One file with many alike chunks (an 80-chunk order export) must not take every
# slot from another file the question also needs (the one-chunk stock list).
# A document keeps at most half of the slots when another document comes close
# to the best hit (RAG_SPREAD_MARGIN of cosine score) or is a top keyword match;
# otherwise the slots stay with it. The ranking itself is untouched: the wider
# search only brings candidates of other documents for those freed slots.
LEXICAL_RESCUE = 3
SPREAD_MARGIN_DEFAULT = 0.08


def spread_margin(env=None):
    """RAG_SPREAD_MARGIN: 0 turns spreading off; empty or invalid takes the default."""
    raw = ((env if env is not None else os.environ).get("RAG_SPREAD_MARGIN") or "").strip()
    try:
        value = float(raw) if raw else SPREAD_MARGIN_DEFAULT
    except ValueError:
        return SPREAD_MARGIN_DEFAULT
    return value if 0 <= value <= 1 else SPREAD_MARGIN_DEFAULT


def pool_size(k):
    """Dense and keyword candidates per sub-question that the ranking fuses."""
    return min(max(k * 8, 40), 256)


def spread_pool_size(k, margin):
    """How far the dense search looks for other documents to spread to (the
    other document's chunk may rank far below the crowded one's)."""
    if margin > 0 and k >= 2:
        return min(max(k * 24, 120), 256)
    return pool_size(k)


def spread(pool, k, margin, document=None, lexical_rank=None):
    """`pool`: candidate contexts in rank order (each with a `score`). Returns
    them reordered: the k to use first, in rank order, then the rest."""
    document = document or (lambda c: c.get("doc_id") or c.get("source_path"))
    lexical_rank = lexical_rank or (lambda c: None)
    if margin <= 0 or k < 2 or len(pool) <= k:
        return pool
    cap = (k + 1) // 2
    chosen = list(range(k))
    counts = {}
    for i in chosen:
        counts[document(pool[i])] = counts.get(document(pool[i]), 0) + 1
    top = max(pool[i]["score"] for i in chosen)
    for i in range(k, len(pool)):
        over = [doc for doc, n in counts.items() if n > cap]
        if not over:
            break
        doc = document(pool[i])
        if doc in counts:
            continue
        rank = lexical_rank(pool[i])
        if not (pool[i]["score"] >= top - margin or (rank is not None and rank <= LEXICAL_RESCUE)):
            continue
        crowded = max(over, key=lambda d: counts[d])
        dropped = max(j for j in chosen if document(pool[j]) == crowded)
        chosen.remove(dropped)
        counts[crowded] -= 1
        chosen.append(i)
        counts[doc] = 1
    chosen.sort()
    picked = set(chosen)
    return [pool[i] for i in chosen] + [c for i, c in enumerate(pool) if i not in picked]


def fuse(dense, lexical, evidence=()):
    scores = {}
    for run, weight in [(dense, 1.0), (lexical, 2.0)]:
        for rank, chunk_id in enumerate(dict.fromkeys(run), 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (60 + rank)
    ranked = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return list(dict.fromkeys(list(evidence)[:3] + ranked))

# -- full-coverage matching (match_chunks) ------------------------------------
# A term matches a chunk when every word of it starts a word of the chunk
# (inflected Hungarian: "BalĂˇzs" finds "BalĂˇzzsal" only through the stem, but
# "lĂˇtogatĂˇs" finds "lĂˇtogatĂˇsai"), the words lying close together. Short words
# must match whole, or "Kis" would find every "kisebb". The JSON store and the
# FTS5 query of the SQLite store implement the same rule.
PREFIX_MIN = 4
NEAR_SLACK = 2
MAX_MATCH_TERMS = 20


def _folded(text):
    normalized = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def tokens(text):
    """Every folded word of a text, in order (repeats kept, unlike terms())."""
    return re.findall(r"[^\W_]+", _folded(text))


def term_words(term):
    return [w for w in tokens(term) if len(w) >= 2][:8]


def _word_ok(pattern, word):
    return word.startswith(pattern) if len(pattern) >= PREFIX_MIN else word == pattern


def chunk_matches(chunk_words, match_terms, mode="any"):
    """Does a chunk (its tokens()) match the terms; mode any|all."""
    results = []
    for term in match_terms:
        words = term_words(term)
        if not words:
            continue
        span = len(words) - 1 + NEAR_SLACK
        hit = any(all(any(_word_ok(w, x) for x in chunk_words[start:start + span + 1])
                      for w in words)
                  for start in range(len(chunk_words)))
        results.append(hit)
    if not results:
        return False
    return all(results) if mode == "all" else any(results)


def fts_query(match_terms, mode="any"):
    """The FTS5 MATCH expression of the same rule; "" when no term has a word."""
    parts = []
    for term in match_terms:
        words = term_words(term)
        if not words:
            continue
        phrases = ['"%s"%s' % (w, "*" if len(w) >= PREFIX_MIN else "") for w in words]
        parts.append(phrases[0] if len(phrases) == 1
                     else "NEAR(%s, %d)" % (" ".join(phrases), NEAR_SLACK))
    if not parts:
        return ""
    return "text : (%s)" % (" AND " if mode == "all" else " OR ").join(parts)


def under_any(path, prefixes):
    """Segment-aware: the path is one of the prefixes or lies below one."""
    path = (path or "").rstrip("/")
    return not prefixes or any(path == p.rstrip("/") or path.startswith(p.rstrip("/") + "/")
                               for p in prefixes)
