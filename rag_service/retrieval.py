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