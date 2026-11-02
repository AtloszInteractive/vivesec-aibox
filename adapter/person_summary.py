"""Per-person summaries of an investigation table (BV 1.4 / 2.4).

The investigation (orchestrator.Run.investigate) finds the people; this reads,
for each of them, what the caller's documents say about that person and writes
a short characterisation with citations -- the kind of information the
documents hold (incidents and their kind, prohibited items such as a phone and
the numbers on it, visits ...), never an assessment of the person.

Targeted reading, not a search: a person's documents are the visible documents
the graph links to that person (the identity the investigation resolved, so a
namesake's files stay out), and within them the passages naming the person
(/rag/match_chunks with the names the graph saw and the registry numbers).
The passages are read with the full-reading map step (deep.read_group: every
finding carries a quote that must occur verbatim in its passage); one short
call per person writes the summary from the findings, citing them by number;
a summary that cites nothing valid or states figures the findings do not hold
gives way to the findings themselves.

Everything runs in the caller's scope and access: the investigation is rebuilt
when the job runs, the RAG filters the passages, and every passage is checked
again against `visible`. Each person's reading is checkpointed (resumed after a
cancel or restart, analysis_checkpoint.py).

  ADAPTER_PERSON_SUMMARY_MAX_PERSONS   people per run, in table order (default 12)
  ADAPTER_PERSON_SUMMARY_MAX_DOCS      documents read per person (default 12, max 20)
  ADAPTER_PERSON_SUMMARY_MAX_PASSAGES  passages read per person (default 60)
"""
import hashlib
import os
import re
import sys

import confidence
import deep
import llm

MAX_FINDINGS = 24
WRITE_TIMEOUT = 300
WRITE_PREDICT = 700
MAX_IDS = 200
LANG_CODES = {"Hungarian": "hu", "English": "en", "Danish": "da", "German": "de"}

MAP_QUESTION = (
    "What these excerpts state about %s (%s): the incidents or events involving this "
    "person, their kind and date and the report's file number; prohibited items found on "
    "them (e.g. a mobile phone) and the numbers on them; what reports or notes say this "
    "person did; visits and calls only as who and when. Only facts about this person, "
    "nothing about anyone else. Skip the person's own identifying data (birth data, "
    "mother's name, ID, registry or document numbers, address, own phone number, vehicle) "
    "and routine records (admission, placement, headcount lists). Write the notes in %s.")

WRITE_SYSTEM = (
    "You write a short factual characterisation of ONE person for an investigator, from "
    "numbered notes taken from the investigator's documents. Write 2-4 sentences in "
    "%s. Say what kind of information the documents hold about this person: first the "
    "incidents and reports (their kind, date and file number) and prohibited items found "
    "(such as a mobile phone, with the number on it); then, in one sentence at most, who "
    "visited or called them and in which period, without counting or listing dates. Do not "
    "repeat the person's identifying data (birth data, mother's name, ID or registry "
    "numbers, address, their own phone number, vehicle): the investigation table holds it. "
    "After each statement put the number(s) of the notes it rests on in square brackets, "
    "e.g. [2] or [1][3]. Use only the notes; keep names, dates and numbers exactly as "
    "written. Never guess, assess, score or predict the person's guilt, risk or behaviour. "
    "If the notes say nothing about this person, write only: NONE"
)

_TEXT = {
    "title": {"en": "Summary per person", "hu": "Összefoglaló személyenként",
              "da": "Opsummering pr. person", "de": "Zusammenfassung je Person"},
    "start": {"en": "starting point", "hu": "kiindulópont", "da": "udgangspunkt",
              "de": "Ausgangspunkt"},
    "level": {"en": "level %d", "hu": "%d. szint", "da": "niveau %d", "de": "Ebene %d"},
    "levels": {"en": "%d levels", "hu": "%d szint", "da": "%d niveauer", "de": "%d Ebenen"},
    "no_docs": {"en": "No document you may see links to this person.",
                "hu": "Az Ön számára látható iratok közül egyik sem kapcsolódik ehhez a "
                      "személyhez.",
                "da": "Ingen af de dokumenter, du har adgang til, er knyttet til denne "
                      "person.",
                "de": "Keines der Dokumente, die Sie sehen dürfen, ist mit dieser Person "
                      "verknüpft."},
    "nothing": {"en": "In the documents you may see (%d) I found no incident, report or other "
                      "information about this person beyond identifying and register data.",
                "hu": "Az Ön számára látható iratokban (%d) nem találtam róla eseményt, "
                      "jelentést vagy más, az azonosító és nyilvántartási adatokon túli "
                      "információt.",
                "da": "I de dokumenter, du har adgang til (%d), fandt jeg ingen hændelse, "
                      "rapport eller anden oplysning om personen ud over identifikations- og "
                      "registerdata.",
                "de": "In den Dokumenten, die Sie sehen dürfen (%d), fand ich keinen Vorfall, "
                      "keinen Bericht und keine anderen Angaben zu dieser Person über "
                      "Identifikations- und Registerdaten hinaus."},
    "findings": {"en": "What the documents note about this person:",
                 "hu": "Amit az iratok erről a személyről rögzítenek:",
                 "da": "Hvad dokumenterne noterer om denne person:",
                 "de": "Was die Dokumente zu dieser Person festhalten:"},
    "read": {"en": "Read: %d passages in %d documents.",
             "hu": "Elolvasva: %d részlet %d iratból.",
             "da": "Læst: %d passager i %d dokumenter.",
             "de": "Gelesen: %d Passagen in %d Dokumenten."},
    "docs_cut": {"en": "Only %(read)d of the %(total)d documents linked to this person were read.",
                 "hu": "A személyhez kapcsolódó %(total)d iratból csak %(read)d-t olvastam el.",
                 "da": "Kun %(read)d af de %(total)d dokumenter knyttet til personen er læst.",
                 "de": "Nur %(read)d der %(total)d mit der Person verknüpften Dokumente wurden gelesen."},
    "passages_cut": {"en": "Only the first %(read)d of %(total)d passages naming this person were read.",
                     "hu": "A személyt említő %(total)d részletből csak az első %(read)d-t olvastam el.",
                     "da": "Kun de første %(read)d af %(total)d passager, der nævner personen, er læst.",
                     "de": "Nur die ersten %(read)d von %(total)d Passagen, die die Person nennen, wurden "
                           "gelesen."},
    "persons_cut": {"en": "Summaries for the first %(read)d of the %(total)d people of the table; ask "
                          "for the others by selecting them.",
                    "hu": "A táblázat %(total)d személyéből az első %(read)d-ről készült összefoglaló; a "
                          "többiekhez válassza ki őket külön.",
                    "da": "Opsummeringer for de første %(read)d af tabellens %(total)d personer; vælg de "
                          "øvrige for at få dem.",
                    "de": "Zusammenfassungen für die ersten %(read)d der %(total)d Personen der Tabelle; "
                          "wählen Sie die übrigen gesondert aus."},
    "resumed": {"en": "%d people were read by an earlier, unfinished run.",
                "hu": "%d személyt egy korábbi, félbeszakadt futás olvasott el.",
                "da": "%d personer blev læst af en tidligere, uafsluttet kørsel.",
                "de": "%d Personen wurden von einem früheren, unvollendeten Lauf gelesen."},
    "basis": {"en": "Based only on the documents you may see now; it states what they say "
                    "and assesses no one.",
              "hu": "Kizárólag az Ön számára most látható iratok alapján; azt rögzíti, amit "
                    "az iratok tartalmaznak, senkit nem értékel.",
              "da": "Bygger kun på de dokumenter, du har adgang til nu; den gengiver, hvad "
                    "de siger, og vurderer ingen.",
              "de": "Beruht nur auf den Dokumenten, die Sie jetzt sehen dürfen; sie gibt "
                    "wieder, was darin steht, und bewertet niemanden."},
}


def _t(key, code):
    return _TEXT[key].get(code) or _TEXT[key]["en"]


def _bounded(env, name, default, low, high):
    try:
        value = int(env.get(name, "") or default)
    except ValueError:
        sys.stderr.write("[adapter] invalid %s -> %d\n" % (name, default))
        return default
    return max(low, min(value, high))


def settings(env=None):
    env = env if env is not None else os.environ
    return {"max_persons": _bounded(env, "ADAPTER_PERSON_SUMMARY_MAX_PERSONS", 12, 1, 50),
            "max_docs": _bounded(env, "ADAPTER_PERSON_SUMMARY_MAX_DOCS", 12, 1, 20),
            "max_passages": _bounded(env, "ADAPTER_PERSON_SUMMARY_MAX_PASSAGES", 60, 1, 400)}


def title(lang, start):
    return "%s: %s" % (_t("title", LANG_CODES.get(lang or "", "en")), start)


def parse_ids(value):
    """The client's optional choice of people (row ids): None for all, a list
    of ids, or ValueError when it is malformed."""
    if value is None:
        return None
    if not isinstance(value, list) or len(value) > MAX_IDS \
            or not all(isinstance(v, str) and v and len(v) <= 300 for v in value):
        raise ValueError("ids must be a list of row ids")
    return list(dict.fromkeys(value))


def people(inv, ids=None):
    """The people of the table in table order (level, then name), narrowed to
    `ids` when given; anything else in `ids` is ignored."""
    rows = [r for r in inv.get("rows") or [] if r.get("type") == "person"]
    if ids is not None:
        wanted = set(ids)
        rows = [r for r in rows if r["id"] in wanted]
    return rows


def person_documents(run, row):
    """[(doc_id, doc)] the person's node links to, visible in the caller's
    scope now, in path order."""
    node = run.nodes.get(row["id"]) or {}
    ids = set(node.get("documents") or [])
    ids.update(a.get("doc_id") for a in node.get("attributes") or [])
    docs = []
    for doc_id in ids:
        doc = run.documents.get(doc_id)
        path = (doc or {}).get("source_path") or ""
        if doc is not None and path and run.visible(path):
            docs.append((doc_id, doc))
    docs.sort(key=lambda item: (item[1].get("source_path") or "", item[0]))
    return docs


def terms_of(run, row):
    """The words that find the person's passages: the names the graph saw in
    the documents (inflected forms included) and the registry numbers."""
    node = run.nodes.get(row["id"]) or {}
    names = [row.get("name")] + list(node.get("aliases") or [])
    names += list(row.get("registry_number") or []) + list(row.get("id_number") or [])
    out = []
    for name in names:
        name = " ".join(str(name or "").split())
        if len(name) >= 3 and name.casefold() not in {o.casefold() for o in out}:
            out.append(name[:80])
    return out[:12]


def _describe(row):
    parts = [row.get("role") or ""]
    parts += list(row.get("registry_number") or [])
    return ", ".join(p for p in parts if p)


def _write(chat, row, findings, lang):
    notes = "\n".join('[%d] %s "%s"' % (i, f["note"], f["quote"])
                      for i, f in enumerate(findings, 1))
    content, _, _ = chat(WRITE_SYSTEM % (lang or "English"),
                         "PERSON: %s (%s)\n\nNOTES:\n%s" % (row["name"], _describe(row),
                                                            notes),
                         timeout=WRITE_TIMEOUT, stream=False, num_predict=WRITE_PREDICT)
    return " ".join((content or "").split())


_REF = re.compile(r"\[(\d+(?:\s*[,;]\s*\d+)*)\]")


def cite(text, count, offset):
    """The summary with its [n] references renumbered to the answer's [#m]
    (offset + n); references to no note are dropped. (text, cited count)."""
    cited = set()

    def repl(match):
        refs = []
        for part in re.split(r"[,;]", match.group(1)):
            n = int(part.strip())
            if 1 <= n <= count:
                refs.append("[#%d]" % (offset + n))
                cited.add(n)
        return "".join(refs)
    out = _REF.sub(repl, text or "")
    return " ".join(out.split()), len(cited)


def _context(finding, doc, run):
    chunk = finding["chunk"]
    return {"text": '%s "%s"%s' % (finding["note"], finding["quote"], run.doc_ref_note(doc)),
            "source_path": chunk.get("source_path"), "corpus_id": chunk.get("corpus_id"),
            "page_number": chunk.get("page_number"), "chunk_id": chunk.get("chunk_id"),
            "score": 1.0, "origin": "deep"}


def _check(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise llm.GenerationCancelled("cancelled")


def summarize(run, inv, post, scope_body, *, lang="English", user="", ids=None, config=None,
              chat=None, checkpoints=None, cancel_event=None, progress=None):
    """The summaries of the people of one investigation table, as an answer:
    {"text": markdown with [#n], "contexts": the cited findings in [#n] order,
    "persons": [...], "info": {...}, "sources": [paths read]}."""
    cfg = config or settings()
    chat = chat or llm._chat
    code = LANG_CODES.get(lang or "", "en")
    everyone = people(inv, ids)
    chosen = everyone[:cfg["max_persons"]]
    question_hash = hashlib.sha256((deep.MAP_SYSTEM + MAP_QUESTION + WRITE_SYSTEM)
                                   .encode("utf-8")).hexdigest()
    contexts, persons, sources, resumed = [], [], [], 0
    for index, row in enumerate(chosen):
        _check(cancel_event)
        if progress:
            progress("person", index, len(chosen))
        docs = person_documents(run, row)
        entry = {"id": row["id"], "name": row["name"], "level": row.get("level", 0),
                 "role": row.get("role"), "registry_number": list(row.get("registry_number")
                                                                 or []),
                 "documents": len(docs), "documents_read": 0, "passages": 0, "findings": 0,
                 "cited": 0, "summary": "", "notes": [], "partial": False}
        persons.append(entry)
        read = docs[:cfg["max_docs"]]
        if len(docs) > len(read):
            entry["notes"].append(_t("docs_cut", code) % {"read": len(read), "total": len(docs)})
            entry["partial"] = True
        if not read:
            entry["summary"] = _t("no_docs", code)
            continue
        by_path = {doc.get("source_path"): doc for _, doc in read}
        chunks, total, _ = deep.collect(post, scope_body, terms_of(run, row), "any",
                                        sorted(by_path), cap=cfg["max_passages"])
        # The prefixes match a longer path too; only the person's own documents count.
        chunks = [c for c in chunks if c.get("source_path") in by_path
                  and run.visible(c.get("source_path") or "")]
        if total > cfg["max_passages"]:
            entry["notes"].append(_t("passages_cut", code) % {"read": cfg["max_passages"],
                                                              "total": total})
            entry["partial"] = True
        entry["passages"] = len(chunks)
        entry["documents_read"] = len({c.get("source_path") for c in chunks})
        sources += [p for p in dict.fromkeys(c.get("source_path") for c in chunks)
                    if p not in sources]
        checkpoint = checkpoints.open(
            user, chunks, MAP_QUESTION % (row["name"], _describe(row), lang or "English"), lang,
            llm.GEN_MODEL, {"batch": deep.BATCH_CHARS, "prompt": question_hash}) \
            if checkpoints is not None and chunks else None
        findings, from_checkpoint = [], True
        question = MAP_QUESTION % (row["name"], _describe(row), lang or "English")
        for g, group in enumerate(deep.batches(chunks)):
            done, part = checkpoint.get(g) if checkpoint is not None else (False, None)
            if not done:
                _check(cancel_event)
                from_checkpoint = False
                part, _ = deep.read_group(chat, question, group)
                if checkpoint is not None:
                    checkpoint.put(g, part)
            findings += part or []
        seen, unique = set(), []
        for f in findings:
            key = (f["chunk"].get("chunk_id"), deep._squash(f["quote"]))
            if key not in seen:
                seen.add(key)
                unique.append(f)
        unique = unique[:MAX_FINDINGS]
        entry["findings"] = len(unique)
        if not unique:
            entry["summary"] = _t("nothing", code) % len(read)
            continue
        done, text = checkpoint.get("summary") if checkpoint is not None else (False, None)
        if not done:
            _check(cancel_event)
            from_checkpoint = False
            text = _write(chat, row, unique, lang)
            if checkpoint is not None and text:
                checkpoint.put("summary", text)
        if from_checkpoint:
            resumed += 1
        own = [_context(f, by_path.get(f["chunk"].get("source_path")), run) for f in unique]
        summary, cited = cite(text if text and text.strip().upper() != "NONE" else "",
                              len(own), len(contexts))
        if summary and cited:
            check = confidence.score(own, re.sub(r"\[#\d+\]", "", summary))
            if check.get("suppress"):
                summary, cited = "", 0
        if not summary or not cited:
            # Nothing the model wrote can be traced to a note: the notes themselves.
            lines = ["- %s [#%d]" % (f["note"], len(contexts) + i)
                     for i, f in enumerate(unique[:8], 1)]
            summary, cited = _t("findings", code) + "\n" + "\n".join(lines), min(8, len(unique))
        contexts += own
        entry.update(summary=summary, cited=cited)
        entry["notes"].insert(0, _t("read", code) % (entry["passages"],
                                                     entry["documents_read"]))
    if progress:
        progress("person", len(chosen), len(chosen))
    notes = []
    if len(everyone) > len(chosen):
        notes.append(_t("persons_cut", code) % {"read": len(chosen),
                                                           "total": len(everyone)})
    if resumed:
        notes.append(_t("resumed", code) % resumed)
    notes.append(_t("basis", code))
    return {"text": render(inv, persons, notes, code), "contexts": contexts,
            "persons": persons, "sources": sources,
            "info": {"start": inv.get("start"), "levels": inv.get("levels"),
                     "rows": len(inv.get("rows") or []), "people": len(everyone),
                     "summarised": len(chosen), "resumed": resumed,
                     "passages": sum(p["passages"] for p in persons),
                     "findings": sum(p["findings"] for p in persons),
                     "partial": len(everyone) > len(chosen)
                     or any(p["partial"] for p in persons)}}


def render(inv, persons, notes, code):
    lines = ["## %s \u2014 %s: %s (%s)" % (_t("title", code), _t("start", code),
                                           inv.get("start"),
                                           _t("levels", code) % (inv.get("levels") or 0))]
    for p in persons:
        where = _t("start", code) if not p["level"] else _t("level", code) % p["level"]
        about = ", ".join(x for x in [p.get("role") or ""] + p["registry_number"] if x)
        lines.append("")
        lines.append("### %s \u2014 %s (%s)" % (p["name"], about, where) if about
                     else "### %s (%s)" % (p["name"], where))
        lines.append(p["summary"])
        if p["notes"]:
            lines.append("")
            lines.append("*%s*" % " ".join(p["notes"]))
    if notes:
        lines.append("")
        lines += ["> %s" % n for n in notes]
    return "\n".join(lines)
