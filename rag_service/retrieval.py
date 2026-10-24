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


def fuse(dense, lexical, evidence=()):
    scores = {}
    for run, weight in [(dense, 1.0), (lexical, 2.0)]:
        for rank, chunk_id in enumerate(dict.fromkeys(run), 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (60 + rank)
    ranked = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return list(dict.fromkeys(list(evidence)[:3] + ranked))

# -- full-coverage matching (match_chunks) ------------------------------------
# A term matches a chunk when every word of it starts a word of the chunk
# (inflected Hungarian: "Balázs" finds "Balázzsal" only through the stem, but
# "látogatás" finds "látogatásai"), the words lying close together. Short words
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
