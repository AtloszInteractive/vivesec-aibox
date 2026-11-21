"""Counts and totals from a few retrieved passages (BV test 2026-11-20).

A plain answer reads the best few passages of a search, so "how many calls were
there in July" or "the total of the open PX-200 orders" counts only what those
passages hold, and the model states it as the whole. Where the question asks
for a count, a total or every item and the answer did not come from a full
reading or a graph count, a note under the answer says what it is based on.
The note is added as is: the model may drop such a sentence from its text.
"""
import re
import unicodedata

_CUES = re.compile(
    r"\b(?:"
    # Hungarian
    r"h[aá]ny(?:an|szor|at)?|mennyi|[oö]sszes\w*|[oö]sszeg\w*|darabsz[aá]m\w*|[aá]tlag\w*"
    # English
    r"|how\s+many|how\s+much|total\w*|sum|count|number\s+of|average"
    # Danish
    r"|hvor\s+mange|hvor\s+meget|i\s+alt|samlet|antal\w*|gennemsnit\w*"
    # German
    r"|wie\s+viele?|insgesamt|summe|gesamt\w*|anzahl|durchschnitt\w*"
    r")\b", re.IGNORECASE)

_NOTE = {
    "en": "Counting note: this answer rests on %d passages retrieved from %d documents, not on "
          "every record of the documents you may see. A count or total stated here may be "
          "incomplete; narrow the question (one file, one period) or ask for a full reading.",
    "hu": "Sz\u00e1mol\u00e1si megjegyz\u00e9s: ez a v\u00e1lasz %d visszakeresett "
          "r\u00e9szleten alapul (%d dokumentumb\u00f3l), nem az \u00d6n sz\u00e1m\u00e1ra "
          "l\u00e1that\u00f3 dokumentumok minden rekordj\u00e1n. Az itt szerepl\u0151 "
          "darabsz\u00e1m vagy \u00f6sszeg hi\u00e1nyos lehet; sz\u0171k\u00edtse a "
          "k\u00e9rd\u00e9st (egy f\u00e1jl, egy id\u0151szak), vagy k\u00e9rjen teljes "
          "\u00e1tn\u00e9z\u00e9st.",
    "da": "Bem\u00e6rkning om opt\u00e6lling: svaret bygger p\u00e5 %d fundne passager fra %d "
          "dokumenter, ikke p\u00e5 alle poster i de dokumenter, du har adgang til. Et antal "
          "eller en sum her kan v\u00e6re ufuldst\u00e6ndig; afgr\u00e6ns sp\u00f8rgsm\u00e5let "
          "(en fil, en periode) eller bed om en fuld gennemgang.",
    "de": "Hinweis zur Z\u00e4hlung: Diese Antwort beruht auf %d gefundenen Passagen aus %d "
          "Dokumenten, nicht auf allen Datens\u00e4tzen der Dokumente, die Sie sehen d\u00fcrfen. "
          "Eine hier genannte Anzahl oder Summe kann unvollst\u00e4ndig sein; schr\u00e4nken Sie "
          "die Frage ein (eine Datei, ein Zeitraum) oder fordern Sie eine vollst\u00e4ndige "
          "Durchsicht an.",
}
LANG_CODES = {"Hungarian": "hu", "English": "en", "Danish": "da", "German": "de"}
# Graph tools whose figures are counted over every visible document.
COUNTING_TOOLS = ("count", "most_connected")


def asks_for_count(question):
    """Does the question ask for a count, a total, an average or all of something?"""
    return bool(_CUES.search(unicodedata.normalize("NFC", question or "")))


def counted_by_graph(info):
    """Did the analyst plan count it in the graph (complete over the visible documents)?"""
    return any(c.get("tool") in COUNTING_TOOLS and c.get("facts")
               for c in (info or {}).get("calls") or [])


def partial_note(contexts, lang=None):
    """The note for an answer built from these retrieved passages, or None when
    there is no retrieved passage (nothing was counted from documents)."""
    passages = [c for c in contexts or [] if c.get("chunk_id") and c.get("source_path")]
    if not passages:
        return None
    documents = len({c["source_path"] for c in passages})
    return _NOTE[LANG_CODES.get(lang or "", "en")] % (len(passages), documents)
