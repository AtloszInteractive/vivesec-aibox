"""Full-coverage reading: map-reduce over EVERY matching passage (E26 analyst
mode; the engine is meant to serve E10a's whole-document analysis as well).

Top-k retrieval answers from the best few passages, so "list every case",
"in how many reports" or "everything about X" quietly misses the rest. Here the
RAG lists every visible passage that contains the terms (/rag/match_chunks, in
reading order), the passages are read in fixed-size groups, and each group
yields findings, each with a verbatim quote that must occur in its passage. The
findings become cited contexts for the usual answer step, with a note on what
was covered; a cap that cuts the list makes the note say "partial".

Everything runs in the caller's scope and access (the RAG filters both), and
every model call is a foreground call of a job that holds the heavy slot.
"""
import json
import os
import re
import time
import unicodedata

import llm

MAX_CHUNKS = max(1, int(os.environ.get("ADAPTER_DEEP_MAX_CHUNKS", "400") or 400))
BATCH_CHARS = max(2000, int(os.environ.get("ADAPTER_DEEP_BATCH_CHARS", "9000") or 9000))
MAX_FINDINGS = max(10, int(os.environ.get("ADAPTER_DEEP_MAX_FINDINGS", "120") or 120))
MAP_TIMEOUT = 180
PAGE = 500
MAX_QUOTE = 300

LANG_CODES = {"Hungarian": "hu", "English": "en", "Danish": "da", "German": "de"}

MAP_SYSTEM = (
    "You read numbered excerpts of the user's documents and note everything in them "
    "that helps answer the QUESTION. Use only the excerpts.\n"
    'Reply with JSON only: {"findings": [{"excerpt": <number>, "quote": "<the exact '
    'words of that excerpt that show it, copied character for character, at most 300 '
    'characters>", "note": "<what it shows for the question, one short sentence>"}]}\n'
    "Give one finding per relevant fact and list every one of them, even when they look "
    "alike: the question may need all cases. Keep names, dates and numbers as written. "
    "An excerpt that does not help gets no finding; if none helps, reply "
    '{"findings": []}.'
)
_SCHEMA = {"type": "object", "properties": {"findings": {"type": "array", "items": {
    "type": "object", "properties": {
        "excerpt": {"type": "integer"}, "quote": {"type": "string"},
        "note": {"type": "string"}},
    "required": ["excerpt", "quote", "note"]}}}, "required": ["findings"]}

_TEXT = {
    "coverage": {
        "en": "Full reading: every passage of the documents you may see that contains %s "
              "was read -- %d passages in %d documents; %d relevant findings.",
        "hu": "Teljes \u00e1tn\u00e9z\u00e9s: az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 "
              "dokumentumok minden r\u00e9szlet\u00e9t elolvastam, amely ezt tartalmazza: "
              "%s -- %d r\u00e9szlet %d dokumentumb\u00f3l; %d relev\u00e1ns "
              "meg\u00e1llap\u00edt\u00e1s.",
        "da": "Fuld gennemgang: hver passage i de dokumenter, du har adgang til, som "
              "indeholder %s, er l\u00e6st -- %d passager i %d dokumenter; %d relevante fund.",
        "de": "Vollst\u00e4ndige Durchsicht: Jede Passage der Dokumente, die Sie sehen "
              "d\u00fcrfen, die %s enth\u00e4lt, wurde gelesen -- %d Passagen in %d "
              "Dokumenten; %d relevante Befunde.",
    },
    "partial": {
        "en": "Partial result: %d passages contain the terms, only the first %d (in path "
              "order) were read. Narrow the question (a person, a period, a folder) for a "
              "complete reading.",
        "hu": "R\u00e9szleges eredm\u00e9ny: %d r\u00e9szlet tartalmazza a keresett "
              "kifejez\u00e9seket, ebb\u0151l csak az els\u0151 %d-t (el\u00e9r\u00e9si "
              "\u00fat szerinti sorrendben) olvastam el. A teljes \u00e1tn\u00e9z\u00e9shez "
              "sz\u0171k\u00edtse a k\u00e9rd\u00e9st (szem\u00e9ly, id\u0151szak, mappa).",
        "da": "Delvist resultat: %d passager indeholder ordene, kun de f\u00f8rste %d (efter "
              "sti) er l\u00e6st. Afgr\u00e6ns sp\u00f8rgsm\u00e5let (en person, en periode, "
              "en mappe) for en fuld gennemgang.",
        "de": "Teilergebnis: %d Passagen enthalten die Begriffe, nur die ersten %d (nach "
              "Pfad) wurden gelesen. Schr\u00e4nken Sie die Frage ein (eine Person, ein "
              "Zeitraum, ein Ordner), um alles zu lesen.",
    },
    "trimmed": {
        "en": "Of %d findings the first %d (in path order) went into the answer.",
        "hu": "A(z) %d meg\u00e1llap\u00edt\u00e1sb\u00f3l az els\u0151 %d (el\u00e9r\u00e9si "
              "\u00fat szerinti sorrendben) ker\u00fclt a v\u00e1laszba.",
        "da": "Af %d fund indgik de f\u00f8rste %d (efter sti) i svaret.",
        "de": "Von %d Befunden flossen die ersten %d (nach Pfad) in die Antwort ein.",
    },
    "none": {
        "en": "Full reading: no passage of the documents you may see contains %s.",
        "hu": "Teljes \u00e1tn\u00e9z\u00e9s: az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 "
              "dokumentumok egyik r\u00e9szlete sem tartalmazza ezt: %s.",
        "da": "Fuld gennemgang: ingen passage i de dokumenter, du har adgang til, "
              "indeholder %s.",
        "de": "Vollst\u00e4ndige Durchsicht: Keine Passage der Dokumente, die Sie sehen "
              "d\u00fcrfen, enth\u00e4lt %s.",
    },
}


def _t(key, lang):
    code = LANG_CODES.get(lang or "", "en")
    return _TEXT[key].get(code) or _TEXT[key]["en"]


def _squash(text):
    folded = unicodedata.normalize("NFC", (text or "").casefold())
    return " ".join(re.sub(r"[\"\u201c\u201d\u201e\u00bb\u00ab']", "", folded).split())


def collect(post, scope_body, terms, mode="any", path_prefixes=None, cap=None):
    """Every matching passage up to `cap` (default MAX_CHUNKS): (chunks, total, documents)."""
    cap = MAX_CHUNKS if cap is None else cap
    chunks, offset, total, documents = [], 0, 0, 0
    while len(chunks) < cap:
        body = dict(scope_body, terms=list(terms), mode=mode, offset=offset,
                    limit=min(PAGE, cap - len(chunks)))
        if path_prefixes:
            body["path_prefixes"] = list(path_prefixes)
        res = post("/rag/match_chunks", body)
        chunks += res.get("chunks") or []
        total, documents = int(res.get("total") or 0), int(res.get("documents") or 0)
        if res.get("next_offset") is None:
            break
        offset = res["next_offset"]
    return chunks[:cap], total, documents


def batches(chunks, budget=None):
    """Consecutive groups of at most `budget` characters, in the given order."""
    budget = BATCH_CHARS if budget is None else budget
    groups, current, size = [], [], 0
    for chunk in chunks:
        length = len(chunk.get("text") or "")
        if current and size + length > budget:
            groups.append(current)
            current, size = [], 0
        current.append(chunk)
        size += length
    if current:
        groups.append(current)
    return groups


def _map_user(question, group):
    parts = []
    for i, chunk in enumerate(group, 1):
        where = chunk.get("source_path") or ""
        if chunk.get("page_number"):
            where += " (p.%s)" % chunk["page_number"]
        parts.append("[%d] %s\n%s" % (i, where, (chunk.get("text") or "").strip()))
    return "QUESTION: %s\n\nEXCERPTS:\n%s" % (question, "\n\n".join(parts))


def parse_findings(content, group):
    """The model's findings whose quote really occurs in the excerpt it names."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    items = data.get("findings") if isinstance(data, dict) else None
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        n, quote, note = item.get("excerpt"), item.get("quote"), item.get("note")
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(group):
            continue
        if not isinstance(quote, str) or not quote.strip() or not isinstance(note, str):
            continue
        quote = " ".join(quote.split())[:MAX_QUOTE]
        if _squash(quote) not in _squash(group[n - 1].get("text")):
            continue  # not in the passage: a paraphrase or an invention
        out.append({"chunk": group[n - 1], "quote": quote, "note": " ".join(note.split())[:400]})
    return out


def read_all(post, scope_body, question, terms, lang=None, mode="any", path_prefixes=None,
             cancel_event=None, progress=None, chat=None):
    """Reads every matching passage. Returns (contexts, info): contexts are the
    findings as citable contexts plus one coverage note (origin "search-note")."""
    chat = chat or llm._chat
    started = time.time()
    shown = ", ".join("\u201e%s\u201d" % t for t in terms)
    if progress:
        progress("collect", 0, 0)
    chunks, total, documents = collect(post, scope_body, terms, mode, path_prefixes)
    groups = batches(chunks)
    findings = []
    for i, group in enumerate(groups):
        if cancel_event is not None and cancel_event.is_set():
            raise llm.GenerationCancelled("cancelled")
        if progress:
            progress("read", i, len(groups))
        content, _, _ = chat(MAP_SYSTEM, _map_user(question, group), timeout=MAP_TIMEOUT,
                             stream=False, num_predict=2048, response_format=_SCHEMA)
        findings += parse_findings(content, group)
    if progress:
        progress("write", len(groups), len(groups))
    seen, unique = set(), []
    for f in findings:
        key = (f["chunk"].get("chunk_id"), _squash(f["quote"]))
        if key not in seen:
            seen.add(key)
            unique.append(f)
    notes = [_t("coverage", lang) % (shown, len(chunks), documents, len(unique))
             if total else _t("none", lang) % shown]
    if total > len(chunks):
        notes.append(_t("partial", lang) % (total, len(chunks)))
    if len(unique) > MAX_FINDINGS:
        notes.append(_t("trimmed", lang) % (len(unique), MAX_FINDINGS))
    contexts = [{"text": "\n".join(notes), "source_path": None, "title": "full reading",
                 "corpus_id": None, "page_number": None, "chunk_id": None, "score": 1.0,
                 "origin": "search-note"}]
    for f in unique[:MAX_FINDINGS]:
        chunk = f["chunk"]
        contexts.append({"text": '%s "%s"' % (f["note"], f["quote"]),
                         "source_path": chunk.get("source_path"),
                         "corpus_id": chunk.get("corpus_id"),
                         "page_number": chunk.get("page_number"),
                         "chunk_id": chunk.get("chunk_id"), "score": 1.0, "origin": "deep"})
    info = {"terms": list(terms), "mode": mode, "matched": total, "read": len(chunks),
            "documents": documents, "groups": len(groups), "findings": len(unique),
            "partial": total > len(chunks) or len(unique) > MAX_FINDINGS,
            "seconds": round(time.time() - started, 1)}
    return contexts, info
