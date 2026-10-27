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
# What the findings may take of the answer prompt. Beyond it (or beyond
# MAX_FINDINGS) they are condensed instead of cut: per document first, then, if
# that is still too much, into digests across documents.
ANSWER_CHARS = max(8000, int(os.environ.get("ADAPTER_DEEP_ANSWER_CHARS", "60000") or 60000))
# Whole-document analysis (E10a): characters of one document per section call.
DOC_BATCH_CHARS = max(4000, int(os.environ.get("ADAPTER_ANALYZE_SECTION_CHARS", "24000") or 24000))
MIN_SHARE = 300
MAX_ROUNDS = 3
# A map reply cut off at MAP_PREDICT tokens is re-read in halves, at most this deep.
MAP_PREDICT = 4096
MAX_SPLITS = 3
MAP_TIMEOUT = 180
SECTION_TIMEOUT = 600
PAGE = 500
MAX_QUOTE = 300

LANG_CODES = {"Hungarian": "hu", "English": "en", "Danish": "da", "German": "de"}

MAP_SYSTEM = (
    "You read numbered excerpts of the user's documents and note everything in them "
    "that helps answer the QUESTION. Use only the excerpts.\n"
    'Reply with JSON only: {"findings": [{"excerpt": <number>, "quote": "<the exact '
    'words of that excerpt that show it, copied character for character, at most 300 '
    'characters>", "note": "<what it shows for the question, in a few words>"}]}\n'
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

CONDENSE_SYSTEM = (
    "You condense findings noted from the user's documents. For each numbered DOCUMENT, "
    "rewrite its findings as one summary for the QUESTION, no longer than the character "
    "limit given for it. Keep every distinct case, person, date, number and place; merge "
    "only findings that state the same fact; use only the findings; keep names, dates and "
    "numbers as written.\n"
    'Reply with JSON only: {"documents": [{"document": <number>, "summary": "<text>"}]}'
)
_CONDENSE_SCHEMA = {"type": "object", "properties": {"documents": {"type": "array", "items": {
    "type": "object", "properties": {
        "document": {"type": "integer"}, "summary": {"type": "string"}},
    "required": ["document", "summary"]}}}, "required": ["documents"]}

DIGEST_SYSTEM = (
    "You merge notes taken from the user's documents into one digest for the QUESTION, "
    "at most %d characters. Keep every distinct case, person, date, number and place; "
    "merge only what states the same fact; after each fact name, in square brackets, the "
    "document (file name) and page it comes from, as the notes give them. Use only the "
    "notes. Plain text, no preamble."
)

SECTION_SYSTEM = (
    "You read one part of a long document, as numbered excerpts in reading order, and "
    "note what the TASK needs from this part: its key facts, parties, obligations, dates, "
    "amounts, risks and conclusions. Use only the excerpts.\n"
    'Reply with JSON only: {"findings": [{"excerpt": <number>, "quote": "<the exact '
    'words of that excerpt that show it, copied character for character, at most 300 '
    'characters>", "note": "<the point, one short sentence>"}]}\n'
    "At most 8 findings, the most important first; keep names, dates and numbers as "
    'written. A part with nothing of note gets {"findings": []}.'
)

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
    "unreadable": {
        "en": "%d of the documents you may see have no readable text (e.g. scanned PDFs "
              "without OCR, or unsupported formats); they could not be read.",
        "hu": "Az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 dokumentumok k\u00f6z\u00fcl %d-nak "
              "nincs olvashat\u00f3 sz\u00f6vege (pl. OCR n\u00e9lk\u00fcli szkennelt PDF vagy "
              "nem t\u00e1mogatott form\u00e1tum), ezeket nem lehetett \u00e1tn\u00e9zni.",
        "da": "%d af de dokumenter, du har adgang til, har ingen l\u00e6sbar tekst (f.eks. "
              "scannede PDF'er uden OCR eller formater, der ikke underst\u00f8ttes); de kunne "
              "ikke l\u00e6ses.",
        "de": "%d der Dokumente, die Sie sehen d\u00fcrfen, haben keinen lesbaren Text "
              "(z. B. gescannte PDFs ohne OCR oder nicht unterst\u00fctzte Formate); sie "
              "konnten nicht gelesen werden.",
    },
    "ocr_pending": {
        "en": "%d of the documents you may see are scans still being read by OCR; they are "
              "not covered yet.",
        "hu": "Az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 dokumentumok k\u00f6z\u00fcl %d "
              "szkennelt dokumentum sz\u00f6vegfelismer\u00e9se (OCR) m\u00e9g folyamatban "
              "van; ezek m\u00e9g nem szerepelnek az eredm\u00e9nyben.",
        "da": "%d af de dokumenter, du har adgang til, er scanninger, som OCR stadig "
              "l\u00e6ser; de er ikke d\u00e6kket endnu.",
        "de": "%d der Dokumente, die Sie sehen d\u00fcrfen, sind Scans, die noch per OCR "
              "gelesen werden; sie sind noch nicht erfasst.",
    },
    "cut": {
        "en": "In %d places a single passage held more findings than one model reply can "
              "carry, so some may be missing; the result is partial. Ask about a narrower "
              "subject (a person, a period).",
        "hu": "%d helyen egyetlen r\u00e9szletben t\u00f6bb meg\u00e1llap\u00edt\u00e1s "
              "volt, mint amennyit egy modellv\u00e1lasz elb\u00edr, ez\u00e9rt n\u00e9h\u00e1ny "
              "hi\u00e1nyozhat; az eredm\u00e9ny r\u00e9szleges. K\u00e9rdezzen "
              "sz\u0171kebben (szem\u00e9ly, id\u0151szak).",
        "da": "%d steder rummede en enkelt passage flere fund, end et modelsvar kan b\u00e6re, "
              "s\u00e5 nogle kan mangle; resultatet er delvist. Sp\u00f8rg sn\u00e6vrere (en "
              "person, en periode).",
        "de": "An %d Stellen enthielt eine einzelne Passage mehr Befunde, als eine "
              "Modellantwort fassen kann, daher k\u00f6nnen einige fehlen; das Ergebnis ist "
              "unvollst\u00e4ndig. Fragen Sie enger (eine Person, ein Zeitraum).",
    },
    "condensed": {        "en": "The %d findings were too many to pass on one by one; they were condensed "
              "per document.",
        "hu": "A(z) %d meg\u00e1llap\u00edt\u00e1s egyenk\u00e9nt nem f\u00e9rt a "
              "v\u00e1laszba, ez\u00e9rt dokumentumonk\u00e9nt \u00f6sszes\u00edtettem "
              "\u0151ket.",
        "da": "De %d fund var for mange til at give videre enkeltvis; de blev sammenfattet "
              "pr. dokument.",
        "de": "Die %d Befunde waren zu viele, um sie einzeln weiterzugeben; sie wurden je "
              "Dokument zusammengefasst.",
    },
    "digest": {
        "en": "They were then merged into %d digests across documents; a digest names its "
              "documents in its text.",
        "hu": "Ezut\u00e1n %d, t\u00f6bb dokumentumot \u00e1tfog\u00f3 \u00f6sszegz\u00e9sbe "
              "vontam \u0151ket \u00f6ssze; az \u00f6sszegz\u00e9s a sz\u00f6veg\u00e9ben "
              "nevezi meg a dokumentumait.",
        "da": "Derefter blev de samlet i %d oversigter p\u00e5 tv\u00e6rs af dokumenter; en "
              "oversigt n\u00e6vner sine dokumenter i teksten.",
        "de": "Anschlie\u00dfend wurden sie zu %d dokument\u00fcbergreifenden "
              "Zusammenfassungen vereint; eine Zusammenfassung nennt ihre Dokumente im Text.",
    },
    "lost": {
        "en": "%d groups of findings could not be condensed and were cut short; the result "
              "is partial.",
        "hu": "%d meg\u00e1llap\u00edt\u00e1scsoportot nem siker\u00fclt "
              "\u00f6sszes\u00edteni, ezeket ler\u00f6vid\u00edtettem; az eredm\u00e9ny "
              "r\u00e9szleges.",
        "da": "%d grupper af fund kunne ikke sammenfattes og blev afkortet; resultatet er "
              "delvist.",
        "de": "%d Gruppen von Befunden konnten nicht zusammengefasst werden und wurden "
              "gek\u00fcrzt; das Ergebnis ist unvollst\u00e4ndig.",
    },
    "document": {
        "en": "Full-coverage analysis: all %d passages of %s were read in reading order, in "
              "%d parts; the answer rests on the part summaries.",
        "hu": "Teljes lefedetts\u00e9g\u0171 elemz\u00e9s: a(z) %d r\u00e9szletet (%s) "
              "olvas\u00e1si sorrendben, %d r\u00e9szben dolgoztam fel; a v\u00e1lasz a "
              "r\u00e9sz-\u00f6sszefoglal\u00f3kon alapul.",
        "da": "Fuld analyse: alle %d passager i %s blev l\u00e6st i r\u00e6kkef\u00f8lge, i "
              "%d dele; svaret bygger p\u00e5 delresum\u00e9erne.",
        "de": "Vollst\u00e4ndige Analyse: Alle %d Passagen von %s wurden der Reihe nach in "
              "%d Teilen gelesen; die Antwort beruht auf den Teilzusammenfassungen.",
    },
    "doc_failed": {
        "en": "%d of the %d parts could not be summarised (no usable reply from the model); "
              "the analysis is partial.",
        "hu": "%d r\u00e9szt a(z) %d k\u00f6z\u00fcl nem siker\u00fclt \u00f6sszefoglalni (a "
              "modell nem adott haszn\u00e1lhat\u00f3 v\u00e1laszt); az elemz\u00e9s "
              "r\u00e9szleges.",
        "da": "%d af de %d dele kunne ikke resumeres (intet brugbart svar fra modellen); "
              "analysen er delvis.",
        "de": "%d der %d Teile konnten nicht zusammengefasst werden (keine verwertbare Antwort "
              "des Modells); die Analyse ist unvollst\u00e4ndig.",
    },
    "doc_resumed": {
        "en": "%d of the %d parts were read by an earlier run of this analysis that did not "
              "finish; it was continued from there.",
        "hu": "%d r\u00e9szt a(z) %d k\u00f6z\u00fcl ennek az elemz\u00e9snek egy "
              "kor\u00e1bbi, f\u00e9lbeszakadt fut\u00e1sa olvasott el; az elemz\u00e9s "
              "onnan folytat\u00f3dott.",
        "da": "%d af de %d dele blev l\u00e6st af en tidligere, ufuldf\u00f8rt k\u00f8rsel af "
              "denne analyse; den blev forts\u00e6t derfra.",
        "de": "%d der %d Teile wurden von einem fr\u00fcheren, nicht beendeten Lauf dieser "
              "Analyse gelesen; sie wurde dort fortgesetzt.",
    },
    "doc_capped": {
        "en": "Only the first %d of the document's %d passages could be read; the analysis "
              "is partial.",
        "hu": "Csak az els\u0151 %d r\u00e9szletet lehetett elolvasni a dokumentum %d "
              "r\u00e9szlet\u00e9b\u0151l; az elemz\u00e9s r\u00e9szleges.",
        "da": "Kun de f\u00f8rste %d af dokumentets %d passager kunne l\u00e6ses; analysen er "
              "delvis.",
        "de": "Nur die ersten %d der %d Passagen des Dokuments konnten gelesen werden; die "
              "Analyse ist unvollst\u00e4ndig.",
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


def finding_items(content):
    """(items, complete): the reply's finding objects, and whether the reply was
    whole JSON. A reply cut off at the output limit keeps the objects that were
    finished before the cut (complete=False)."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        items = data.get("findings")
        return (items if isinstance(items, list) else []), True
    key = text.find('"findings"')
    i = text.find("[", key) + 1 if key >= 0 else 0
    items, decoder = [], json.JSONDecoder()
    while 0 < i < len(text):
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text) or text[i] != "{":
            break
        try:
            item, i = decoder.raw_decode(text, i)
        except ValueError:
            break
        items.append(item)
    return items, False


def verified_findings(items, group):
    """The findings whose quote really occurs in the excerpt they name."""
    out = []
    for item in items:
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


def parse_findings(content, group):
    """The model's findings whose quote really occurs in the excerpt it names."""
    return verified_findings(finding_items(content)[0], group)


def read_group(chat, question, group, depth=0):
    """Findings of one group: (findings, cut). A reply cut off at the output
    limit (a group with very many facts, e.g. a register) is read again in two
    halves, so no passage is left unread; `cut` counts the groups that were
    still cut off alone, whose missing findings make the result partial."""
    content, _, _ = chat(MAP_SYSTEM, _map_user(question, group), timeout=MAP_TIMEOUT,
                         stream=False, num_predict=MAP_PREDICT, response_format=_SCHEMA)
    items, complete = finding_items(content)
    if complete or len(group) == 1 or depth >= MAX_SPLITS:
        return verified_findings(items, group), 0 if complete else 1
    half = len(group) // 2
    first, cut_a = read_group(chat, question, group[:half], depth + 1)
    second, cut_b = read_group(chat, question, group[half:], depth + 1)
    return first + second, cut_a + cut_b

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
    unreadable, ocr_pending = _unreadable(post, scope_body)
    groups = batches(chunks)
    findings, cut = [], 0
    for i, group in enumerate(groups):
        if cancel_event is not None and cancel_event.is_set():
            raise llm.GenerationCancelled("cancelled")
        if progress:
            progress("read", i, len(groups))
        found, cut_off = read_group(chat, question, group)
        findings += found
        cut += cut_off
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
    if unreadable:
        notes.append(_t("unreadable", lang) % unreadable)
    if ocr_pending:
        notes.append(_t("ocr_pending", lang) % ocr_pending)
    if cut:
        notes.append(_t("cut", lang) % cut)
    found = [_finding_context(f) for f in unique]
    reduced = {"level": 0, "lost": 0, "digests": 0, "trimmed": 0}
    if len(found) > MAX_FINDINGS or _size(found) > ANSWER_CHARS:
        found, reduced = reduce_findings(question, unique, chat, cancel_event, progress)
        notes.append(_t("condensed", lang) % len(unique))
        if reduced["digests"]:
            notes.append(_t("digest", lang) % reduced["digests"])
        if reduced["lost"]:
            notes.append(_t("lost", lang) % reduced["lost"])
        if reduced["trimmed"]:
            notes.append(_t("trimmed", lang) % (reduced["trimmed"] + len(found), len(found)))
    if progress:
        progress("write", len(groups), len(groups))
    contexts = [{"text": "\n".join(notes), "source_path": None, "title": "full reading",
                 "corpus_id": None, "page_number": None, "chunk_id": None, "score": 1.0,
                 "origin": "search-note"}] + found
    info = {"terms": list(terms), "mode": mode, "matched": total, "read": len(chunks),
            "documents": documents, "groups": len(groups), "findings": len(unique),
            "unreadable": unreadable, "ocr_pending": ocr_pending, "condensed": reduced["level"],
            "cut": cut,
            "partial": total > len(chunks) or bool(cut or reduced["lost"] or reduced["trimmed"]),
            "seconds": round(time.time() - started, 1)}
    return contexts, info


# -- condensing what does not fit ---------------------------------------------------

def _size(contexts):
    return sum(len(c.get("text") or "") for c in contexts)


def _finding_line(finding):
    page = finding["chunk"].get("page_number")
    return '- %s "%s"%s' % (finding["note"], finding["quote"], " (p.%s)" % page if page else "")


def _finding_context(finding):
    chunk = finding["chunk"]
    context = {"text": '%s "%s"' % (finding["note"], finding["quote"]),
               "source_path": chunk.get("source_path"), "corpus_id": chunk.get("corpus_id"),
               "page_number": chunk.get("page_number"), "chunk_id": chunk.get("chunk_id"),
               "score": 1.0, "origin": "deep"}
    if chunk.get("ocr"):
        context["ocr"] = chunk["ocr"]
    return context


def _document_context(path, findings, text):
    first = findings[0]["chunk"]
    pages = {f["chunk"].get("page_number") for f in findings}
    return {"text": text, "source_path": path, "corpus_id": first.get("corpus_id"),
            "page_number": first.get("page_number") if len(pages) == 1 else None,
            "chunk_id": first.get("chunk_id"), "score": 1.0, "origin": "deep"}


def by_document(findings):
    """[(path, findings)] in the order the documents first appear (path order)."""
    docs, index = [], {}
    for f in findings:
        path = f["chunk"].get("source_path") or ""
        if path not in index:
            index[path] = len(docs)
            docs.append((path, []))
        docs[index[path]][1].append(f)
    return docs


def shares(sizes, budget):
    """Max-min fair split of `budget` characters: the small ones keep their size,
    the rest share what is left equally."""
    out, remaining, left = [0] * len(sizes), budget, len(sizes)
    for i in sorted(range(len(sizes)), key=lambda k: sizes[k]):
        out[i] = min(sizes[i], remaining // left)
        remaining -= out[i]
        left -= 1
    return out


def _pack(items, size_of, budget):
    groups, current, used = [], [], 0
    for item in items:
        n = size_of(item)
        if current and used + n > budget:
            groups.append(current)
            current, used = [], 0
        current.append(item)
        used += n
    if current:
        groups.append(current)
    return groups


def _cut(text, limit):
    return text if len(text) <= limit else text[:max(0, limit - 1)].rstrip() + "\u2026"


def _json_list(content, key):
    text = (content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    try:
        data = json.loads(text[start:end + 1]) if 0 <= start < end else None
    except ValueError:
        return None
    items = data.get(key) if isinstance(data, dict) else None
    return items if isinstance(items, list) else None


def _check(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise llm.GenerationCancelled("cancelled")


def reduce_findings(question, findings, chat, cancel_event=None, progress=None,
                    budget=None):
    """Findings that do not fit the answer, condensed without dropping any:
    one context per document (exact citation), the documents over their fair
    share summarised by the model, and only if that is still too much, digests
    across documents. Returns (contexts, {"level", "lost", "digests", "trimmed"})."""
    budget = ANSWER_CHARS if budget is None else budget
    docs = by_document(findings)
    lines = [[_finding_line(f) for f in fs] for _, fs in docs]
    sizes = [len("\n".join(ls)) for ls in lines]
    limits = [max(MIN_SHARE, s) for s in shares(sizes, budget)]
    # Units of at most one call's input: a document with very many findings is
    # condensed in parts, each with its share of the document's limit.
    units = []
    for d, (ls, size, limit) in enumerate(zip(lines, sizes, limits)):
        if size <= limit:
            continue
        for part in _pack(ls, lambda line: len(line) + 1, BATCH_CHARS):
            part_size = len("\n".join(part))
            units.append((d, part, max(MIN_SHARE // 2, limit * part_size // size)))
    calls = _pack(units, lambda u: len("\n".join(u[1])) + 200, BATCH_CHARS)
    summaries, lost = {}, 0
    for i, call in enumerate(calls):
        _check(cancel_event)
        if progress:
            progress("condense", i, len(calls))
        user = "QUESTION: %s\n\nDOCUMENTS:\n%s" % (question, "\n\n".join(
            "[%d] %s (limit: %d characters)\n%s" % (k, docs[d][0], limit, "\n".join(part))
            for k, (d, part, limit) in enumerate(call, 1)))
        content, _, _ = chat(CONDENSE_SYSTEM, user, timeout=MAP_TIMEOUT, stream=False,
                             num_predict=min(4096, 200 + sum(u[2] for u in call) // 2),
                             response_format=_CONDENSE_SCHEMA)
        written = {}
        for item in _json_list(content, "documents") or []:
            n = item.get("document") if isinstance(item, dict) else None
            text = item.get("summary") if isinstance(item, dict) else None
            if isinstance(n, int) and not isinstance(n, bool) and isinstance(text, str) \
                    and text.strip():
                written[n] = " ".join(text.split())
        for k, (d, part, limit) in enumerate(call, 1):
            if k in written:
                summaries.setdefault(d, []).append(_cut(written[k], limit + limit // 2))
            else:
                # No usable summary: the findings themselves, cut to the share.
                lost += 1
                summaries.setdefault(d, []).append(_cut("\n".join(part), limit))
    contexts = []
    for d, (path, fs) in enumerate(docs):
        text = "\n".join(summaries[d]) if d in summaries else "\n".join(lines[d])
        contexts.append(_document_context(path, fs, text))
    result = {"level": 1, "lost": lost, "digests": 0, "trimmed": 0}
    contexts, digests, rounds = _digest_rounds(question, contexts, budget, chat,
                                               cancel_event, progress)
    if rounds:
        result.update(level=1 + rounds, digests=digests)
    contexts, result["trimmed"] = _trim(contexts, budget)
    return contexts, result


def _digest_rounds(question, contexts, budget, chat, cancel_event, progress,
                   keep_source=False, num_ctx=None):
    """Merges consecutive contexts into digests until they fit `budget` (at most
    MAX_ROUNDS times). Returns (contexts, digests made in the last round, rounds)."""
    rounds = digests = 0
    while (_size(contexts) > budget or len(contexts) > MAX_FINDINGS) and rounds < MAX_ROUNDS \
            and len(contexts) > 1:
        rounds += 1
        total = max(1, _size(contexts))
        groups = _pack(contexts, lambda c: len(c.get("text") or "") + 120, BATCH_CHARS)
        merged = []
        for i, group in enumerate(groups):
            _check(cancel_event)
            if progress:
                progress("condense", i, len(groups))
            limit = max(MIN_SHARE, budget * _size(group) // total * 9 // 10)
            notes = "\n\n".join("[%s%s]\n%s" % (
                c.get("source_path") or c.get("title") or "document",
                (", p.%s" % c["page_number"]) if c.get("page_number") else "",
                (c.get("text") or "").strip()) for c in group)
            content, _, _ = chat(DIGEST_SYSTEM % limit, "QUESTION: %s\n\nNOTES:\n%s"
                                 % (question, notes), timeout=SECTION_TIMEOUT, stream=False,
                                 num_ctx=num_ctx, num_predict=min(4096, 200 + limit // 2))
            text = " ".join((content or "").split()) or _cut(
                " ".join(notes.split()), limit)
            sources = []
            for c in group:
                if c.get("source_path") and c["source_path"] not in sources:
                    sources.append(c["source_path"])
            single = keep_source and len(sources) == 1
            merged.append({
                "text": _cut(text, limit + limit // 2) if single else
                "(%s)\n%s" % ("; ".join(sources), _cut(text, limit + limit // 2)),
                "source_path": sources[0] if single else None,
                "title": "full reading digest",
                "corpus_id": group[0].get("corpus_id") if single else None,
                "page_number": group[0].get("page_number") if single else None,
                "chunk_id": group[0].get("chunk_id") if single else None,
                "score": 1.0, "origin": "deep"})
        contexts, digests = merged, len(merged)
    return contexts, digests, rounds


def _trim(contexts, budget):
    """Last resort when even digests do not fit: keep the leading ones."""
    kept, used = [], 0
    for c in contexts:
        n = len(c.get("text") or "")
        if kept and (used + n > budget or len(kept) >= MAX_FINDINGS):
            break
        kept.append(c)
        used += n
    return kept, len(contexts) - len(kept)


# -- whole-document analysis (E10a) -------------------------------------------------

def read_document(chunks, task, lang=None, name=None, chunks_total=None,
                  cancel_event=None, progress=None, chat=None, num_ctx=None, budget=None,
                  checkpoint=None):
    """Full-coverage analysis of ONE document too large for a single call: every
    passage, in reading order, in fixed groups; each group yields findings with
    verified quotes (a part summary with page references), and the part
    summaries, merged if they still do not fit, are the context of the final
    analysis. Returns (contexts, info): a coverage note (origin "search-note")
    first, then the part summaries. With a `checkpoint` (analysis_checkpoint),
    each part read is kept, and the parts an earlier, unfinished run read are
    taken from it instead of being read again."""
    chat = chat or llm._chat
    budget = ANSWER_CHARS if budget is None else budget
    started = time.time()
    groups = batches(chunks, DOC_BATCH_CHARS)
    parts, failed, resumed = [], 0, 0
    for i, group in enumerate(groups):
        _check(cancel_event)
        if progress:
            progress("read", i, len(groups))
        if checkpoint is not None:
            found, part = checkpoint.get(i)
            if found:
                resumed += 1
                if part:
                    parts.append(part)
                continue
        findings = None
        for _ in range(2):  # one retry: a malformed reply must not pass for "nothing here"
            content, _, _ = chat(SECTION_SYSTEM, _map_user(task, group).replace(
                "QUESTION:", "TASK:", 1), timeout=SECTION_TIMEOUT, stream=False,
                num_ctx=num_ctx, num_predict=2048, response_format=_SCHEMA)
            items, complete = finding_items(content)
            if complete or items:
                findings = verified_findings(items, group)
                break
        if findings is None:
            failed += 1
            continue
        pages = [c.get("page_number") for c in group if c.get("page_number")]
        label = ("pp. %s\u2013%s" % (pages[0], pages[-1]) if pages and pages[0] != pages[-1]
                 else "p. %s" % pages[0] if pages else "part %d" % (i + 1))
        part = None
        if findings:
            first = group[0]
            part = {"text": "%s:\n%s" % (label, "\n".join(_finding_line(f) for f in findings)),
                    "source_path": first.get("source_path"),
                    "corpus_id": first.get("corpus_id"),
                    "page_number": first.get("page_number"),
                    "chunk_id": first.get("chunk_id"), "score": 1.0, "origin": "deep"}
            parts.append(part)
        if checkpoint is not None:
            checkpoint.put(i, part)
    parts, digests, rounds = _digest_rounds(task, parts, budget, chat, cancel_event, progress,
                                            keep_source=True, num_ctx=num_ctx)
    parts, trimmed = _trim(parts, budget)
    total = max(len(chunks), chunks_total or 0)
    notes = [_t("document", lang) % (len(chunks), name or "the document", len(groups))]
    if resumed:
        notes.append(_t("doc_resumed", lang) % (resumed, len(groups)))
    if total > len(chunks):
        notes.append(_t("doc_capped", lang) % (len(chunks), total))
    if failed:
        notes.append(_t("doc_failed", lang) % (failed, len(groups)))
    if trimmed:
        notes.append(_t("trimmed", lang) % (trimmed + len(parts), len(parts)))
    if progress:
        progress("write", len(groups), len(groups))
    note = {"text": "\n".join(notes), "source_path": None, "title": "full reading",
            "corpus_id": None, "page_number": None, "chunk_id": None, "score": 1.0,
            "origin": "search-note"}
    info = {"read": len(chunks), "total": total, "groups": len(groups), "failed": failed,
            "resumed": resumed,
            "parts": len(parts), "condensed": rounds, "digests": digests,
            "partial": total > len(chunks) or bool(failed or trimmed),
            "seconds": round(time.time() - started, 1)}
    return [note] + parts, info


def _unreadable(post, scope_body):
    """(visible documents without readable text, of which still waiting for OCR);
    (0, 0) when the RAG cannot tell (the note is advisory)."""
    try:
        stats = post("/rag/scope_stats", {"corpus_ids": scope_body.get("corpus_ids") or [],
                                          "access": scope_body.get("access")})
        stats = stats.get("stats") or {}
        pending = int(stats.get("ocr_pending") or 0)
        return max(0, int(stats.get("skipped") or 0) - pending), pending
    except Exception:  # noqa: BLE001
        return 0, 0
