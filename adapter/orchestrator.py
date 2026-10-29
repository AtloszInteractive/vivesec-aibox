"""Chat orchestrator (E26 analyst mode): the model plans, the adapter runs the graph.

A planning call decides what a message needs: graph queries (relations,
connections, timelines, patterns, counts, sequences ...), a document search,
or a question back to the user when a detail is missing. When the plan says
it must see the results first, the planner gets them and may add calls
(at most MAX_ROUNDS rounds). The adapter runs every query itself within the
caller's scope. The model never sees the graph API and cannot widen the
scope: it only names entities, kinds and relations from the active domain
profile, and those are resolved here. A name that fits several different
entities is asked back instead of guessed.

Every fact a query returns carries its verbatim quote and source, and becomes
one context in the RAG shape, so the answer cites it like any excerpt and the
UI opens the source the usual way. Evidence from documents the adapter's
policy hides is dropped (the graph's ACL copy may lag). Counts and sequences
come from the graph, never from the model.
"""
import json
import re
import time
import unicodedata

import llm
from dossier import TEXT as DOSSIER_TEXT

TOOLS = ("relations", "connection", "common", "timeline", "copresence",
         "shared_identifiers", "most_connected", "find_pattern", "count", "sequence",
         "read_all", "analyze_document")
# The document side of a plan: the caller runs these, not the graph.
DOCUMENT_TOOLS = ("read_all", "analyze_document")
GRAPH_TOOLS = tuple(t for t in TOOLS if t not in DOCUMENT_TOOLS)
MAX_READ_TERMS = 6
ENTITY_TYPES = ["person", "organization", "place", "identifier", "event", "object"]
GROUP_BY = {"entity": "focus", "other": "other", "month": "month", "year": "year",
            "relation": "relation", "other_kind": "other_kind"}
MAX_CALLS = 4
MAX_ROUNDS = 3
MAX_STEPS = 3
MAX_CANDIDATES = 6
PLAN_TIMEOUT = 120
MIN_PREFIX = 4
PROFILE_TTL = 60.0
SUMMARY_LINES = 6
MAX_JOINED_LISTED = 10
MAX_TALLY_LINES = 10
# Identity data of the people a result names: every identifying number, and the
# personal data only of those the message itself named (data minimisation).
ID_ATTRS = ("registry_number", "id_number")
PERSONAL_ATTRS = ("birth_date", "birth_place", "mother_name")
MAX_IDENTITY = 15
MAX_SYNONYMS = 12
# A one-word label or synonym shorter than this is too common to mean a relation.
MIN_STATED = 5
# The graph's built-in links: neither is an occurrence worth counting.
UNTALLIED = ("RELATED_TO", "CO_MENTIONED")
# ... nor the relation a named kind of link means ("Kapcsolatban áll" is not a
# contact): the planner is told so beside them.
GENERIC_RELATIONS = {
    "RELATED_TO": "generic: a link of no stated kind, only when no other relation fits",
    "CO_MENTIONED": "generic: named in the same sentence, not a relation",
}
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
# Capitalised words after the first one: candidate names in a question.
_NAME_RE = re.compile(r"(?<=\s)[A-ZÁÉÍÓÖŐÚÜŰÄÆØÅ][\w\-]{2,}")
# A word that may be the start of a name; numbers and addresses are not.
_PLAIN_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")

PLANNER = (
    "You plan how to answer a message about the entities and events in the user's "
    "documents, using a knowledge graph built from them. You do not answer it; you "
    "choose tools.\n"
    "{vocabulary}\n"
    "Tools. Names are given as the user wrote them or as they appear in the "
    "conversation; kind and relation are ids from the lists above; dates are YYYY, "
    "YYYY-MM or YYYY-MM-DD:\n"
    "- relations {entity}: everything directly linked to one entity\n"
    "- connection {a, b}: the chain of documented links between two entities\n"
    "- common {a, b}: entities linked to both\n"
    "- timeline {entity, from?, to?}: the dated events of one entity in order\n"
    "- copresence {entity, from?, to?}: who was at the same place or event at the same time\n"
    "- shared_identifiers {kind?}: identifiers or addresses used by more than one person "
    "or organisation\n"
    "- most_connected {kind}: the entities with the most links of any relation; for "
    "\"the most\" of one relation use count\n"
    "- find_pattern {start: {kind?, name?}, steps: [{relation?, kind?, name?}], from?, "
    "to?}: chains of up to 3 links, e.g. who visited the inmates placed in a given cell "
    "= start {kind: the visitors' kind}, steps [{relation: the visit relation, kind: "
    "the inmates' kind}, {relation: the placement relation, name: the cell}]; the other "
    "inmates who share a named person's contacts = start {name: the person}, steps "
    "[{relation: the contact relation, kind: person}, {relation: the contact relation, "
    "kind: the inmates' kind}]\n"
    "- count {relation?, kind?, name?, other_kind?, group_by, from?, to?}: counts links "
    "and ranks the groups. kind/name pick the side being counted, other_kind the linked "
    "side. group_by entity ranks the kind/name side (the most visited inmate = relation: "
    "the visit relation, kind: the inmates' kind; the cell with the most inmates = "
        "relation: the placement relation, kind: the cells' kind, other_kind: the inmates' "
        "kind); other ranks what is "
    "linked to them (who visited a given person most often = name: that person); "
    "month or year counts over time (visits per month); relation ranks the relation "
    "types (which kinds of links are most common); other_kind ranks the linked kinds\n"
    "- sequence {first, then, within_days, kind?, name?, from?, to?}: where a `then` "
    "relation of an entity follows its `first` relation within the given number of days "
    "(first and then are relation ids; kind is the entity both relations belong to), "
    "e.g. an incident within 30 days after a visit = first: the visit relation, then: "
    "the involvement relation, within_days: 30, kind: the visited side's kind\n"
    "{deep}"
    "Relations: pick the one whose label or [also] words fit the user's word (a contact, "
    "a visit, a call); a generic relation is never the answer to a named kind of link. "
    "common needs two named entities: for the people of a kind linked to one named "
    "entity through something, use find_pattern. "
    "Identifiers and addresses (a phone number, account number, e-mail address, registry "
    "or ID number, street address) are names like any other: relations {entity: it} "
    "shows who holds it, and find_pattern may start from it (who uses a number and whose "
    "contacts they are = start {name: the number}, steps [{relation: the use relation}, "
    "{relation: the contact relation, kind: the inmates' kind}]). find_pattern returns "
    "only complete chains: when the message asks for each level of a chain (a person's "
    "contacts, the other people those contacts are linked to, then their contacts), add "
    "one find_pattern per level (1, 2 and 3 steps), not only the longest. For a chain of "
    "more than 3 links, find the first links with more: true and continue from the names "
    "found. The registry and ID numbers, birth data and mother's name of the people "
    "found come with the results: no call is needed for them.\n"
    "Reply with one JSON object only, one of:\n"
    '{"action": "tools", "calls": [{"tool": "...", "args": {...}}], '
    '"search": "<the request as a standalone question for a document search, in the '
    'user\'s language>", "more": false}\n'
    '{"action": "clarify", "question": "<one short question to the user>"}\n'
    '{"action": "chat"} -- only when the message is about the conversation itself '
    "(summarise that, thanks, rephrase).\n"
    "Rules: at most 4 calls per reply. A role, category or type word (inmates, a "
    "visitor, cells, incidents) is a kind id, never a name: name, entity, a and b are "
    "only for specific named people, places or things. Set more: true only when you "
    "must see the results before choosing further calls (first find who or what "
    "matches, then look into them, e.g. the top of a count, then relations or "
    "find_pattern with that name); a request whose second part is about the answer "
    "to its first part (who did X most, and who visited them) always needs more: "
    "true. You then get RESULTS SO FAR and reply with new "
    'calls or {"action": "done"}; never repeat a call. Resolve pronouns and follow-ups '
    "from the conversation. "
    "Pass names as given even when they may be incomplete (a surname alone): the system "
    "looks them up and lets the user choose between matches itself. Never ask which of "
    "several people or things with a name is meant, and never ask for context the tools "
    "do not need. "
    "Ask back ONLY when the request cannot be answered without the missing detail -- "
    "who or what is meant is unclear even with the conversation, or a second entity or "
    "a period the request depends on is missing. If the user has just answered your "
    "question, plan with that answer and never ask the same thing again. "
    "When no tool fits, plan calls: [] with a search."
)

DEEP_TOOL = (
    "- read_all {terms, mode?}: reads EVERY passage of the documents that contains the "
    "terms, instead of the few best matching ones (takes a few minutes). Use it, beside "
    "or instead of graph tools, only when the message needs every case, mention or "
    "occurrence in the documents themselves (list all..., every..., in which reports..., "
    "how many times... is written, everything about X in the files) or something the "
    "graph does not hold (what was said, why, how). terms: 1-6 specific names or rare "
    "words as the documents would write them, word stems for inflected words "
    "(\"látogat\"), never generic words; mode all when every term must appear in the "
    "same passage, else any.\n"
    "- analyze_document {file}: reads ONE document from beginning to end and analyses "
    "all of it (takes minutes for a long file). Use it instead of read_all when the "
    "message asks for the analysis, summary or review of one specific file it names by "
    "its file name; file: that file name exactly as written in the message.\n"
)

_TEXT = {
    "ambiguous": {
        "en": "Several entries match \u201c%s\u201d. Which one do you mean?",
        "hu": "T\u00f6bb tal\u00e1lat is van erre: \u201e%s\u201d. Melyikre gondol?",
        "da": "Flere poster passer til \u201d%s\u201d. Hvilken mener du?",
        "de": "Mehrere Eintr\u00e4ge passen zu \u201e%s\u201c. Welchen meinen Sie?",
    },
    "documents": {"en": "%d documents", "hu": "%d dokumentum", "da": "%d dokumenter",
                  "de": "%d Dokumente"},
    "not_found": {
        "en": "Graph search: nothing named \u201c%s\u201d in the documents you may see.",
        "hu": "Gr\u00e1fkeres\u00e9s: \u201e%s\u201d nev\u0171 szerepl\u0151 nincs az \u00d6n "
              "sz\u00e1m\u00e1ra l\u00e1that\u00f3 dokumentumokban.",
        "da": "Grafs\u00f8gning: intet med navnet \u201d%s\u201d i de dokumenter, du har "
              "adgang til.",
        "de": "Graphsuche: nichts mit dem Namen \u201e%s\u201c in den Dokumenten, die Sie "
              "sehen d\u00fcrfen.",
    },
    "namesakes": {
        "en": "Graph search: %d separate records are named \u201c%s\u201d; the documents do "
              "not confirm they are the same.",
        "hu": "Gr\u00e1fkeres\u00e9s: %d k\u00fcl\u00f6n bejegyz\u00e9s viseli a \u201e%s\u201d "
              "nevet; a dokumentumok nem igazolj\u00e1k, hogy ugyanarr\u00f3l van sz\u00f3.",
        "da": "Grafs\u00f8gning: %d separate poster hedder \u201d%s\u201d; dokumenterne "
              "bekr\u00e6fter ikke, at det er den samme.",
        "de": "Graphsuche: %d getrennte Eintr\u00e4ge hei\u00dfen \u201e%s\u201c; die "
              "Dokumente best\u00e4tigen nicht, dass es dieselbe Person ist.",
    },
    "no_path": {
        "en": "Graph search: no documented chain of links between %s and %s.",
        "hu": "Gr\u00e1fkeres\u00e9s: %s \u00e9s %s k\u00f6z\u00f6tt nincs dokument\u00e1lt "
              "kapcsolatl\u00e1nc.",
        "da": "Grafs\u00f8gning: ingen dokumenteret k\u00e6de af forbindelser mellem %s og %s.",
        "de": "Graphsuche: keine dokumentierte Verbindungskette zwischen %s und %s.",
    },
    "nothing": {
        "en": "Graph search (%s): no result in the documents you may see.",
        "hu": "Gr\u00e1fkeres\u00e9s (%s): nincs tal\u00e1lat az \u00d6n sz\u00e1m\u00e1ra "
              "l\u00e1that\u00f3 dokumentumokban.",
        "da": "Grafs\u00f8gning (%s): intet resultat i de dokumenter, du har adgang til.",
        "de": "Graphsuche (%s): kein Ergebnis in den Dokumenten, die Sie sehen d\u00fcrfen.",
    },
    "unknown": {
        "en": "Graph search: \u201c%s\u201d is not a known kind or relation, so that query "
              "was skipped.",
        "hu": "Gr\u00e1fkeres\u00e9s: \u201e%s\u201d nem ismert fajta vagy kapcsolat, "
              "ez\u00e9rt ez a lek\u00e9rdez\u00e9s kimaradt.",
        "da": "Grafs\u00f8gning: \u201d%s\u201d er ikke en kendt type eller relation, s\u00e5 "
              "foresp\u00f8rgslen blev sprunget over.",
        "de": "Graphsuche: \u201e%s\u201c ist keine bekannte Art oder Beziehung, daher wurde "
              "diese Abfrage \u00fcbersprungen.",
    },
    "ranking": {
        "en": "Graph statistics -- number of distinct linked entities, counted over the "
              "documents you may see:",
        "hu": "Gr\u00e1f-statisztika -- a kapcsol\u00f3d\u00f3 k\u00fcl\u00f6nb\u00f6z\u0151 "
              "szerepl\u0151k sz\u00e1ma, az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 "
              "dokumentumok alapj\u00e1n:",
        "da": "Grafstatistik -- antal forskellige forbundne enheder, talt i de dokumenter, "
              "du har adgang til:",
        "de": "Graphstatistik -- Anzahl verschiedener verbundener Eintr\u00e4ge, gez\u00e4hlt "
              "in den Dokumenten, die Sie sehen d\u00fcrfen:",
    },
    "count": {
        "en": "Graph count -- %s by %s, counted over the documents you may see:",
        "hu": "Gr\u00e1f-sz\u00e1ml\u00e1l\u00e1s -- %s, csoportos\u00edtva: %s, az \u00d6n "
              "sz\u00e1m\u00e1ra l\u00e1that\u00f3 dokumentumok alapj\u00e1n:",
        "da": "Graft\u00e6lling -- %s efter %s, talt i de dokumenter, du har adgang til:",
        "de": "Graphz\u00e4hlung -- %s nach %s, gez\u00e4hlt in den Dokumenten, die Sie sehen "
              "d\u00fcrfen:",
    },
    "count_row": {
        "en": "%d. %s -- %d links (%d different linked entities)",
        "hu": "%d. %s -- %d kapcsolat (%d k\u00fcl\u00f6nb\u00f6z\u0151 kapcsol\u00f3d\u00f3 "
              "szerepl\u0151)",
        "da": "%d. %s -- %d forbindelser (%d forskellige forbundne enheder)",
        "de": "%d. %s -- %d Verbindungen (%d verschiedene verbundene Eintr\u00e4ge)",
    },
    "count_row_kind": {
        "en": "%d. %s -- %s: %d different (%d links)",
        "hu": "%d. %s -- %s: %d k\u00fcl\u00f6nb\u00f6z\u0151 (%d kapcsolat)",
        "da": "%d. %s -- %s: %d forskellige (%d forbindelser)",
        "de": "%d. %s -- %s: %d verschiedene (%d Verbindungen)",
    },
    "all_links": {"en": "all links", "hu": "minden kapcsolat", "da": "alle forbindelser",
                  "de": "alle Verbindungen"},
    "group": {
        "en": {"entity": "entity", "other": "linked entity", "month": "month",
               "year": "year", "relation": "relation", "other_kind": "linked kind"},
        "hu": {"entity": "szerepl\u0151", "other": "kapcsol\u00f3d\u00f3 szerepl\u0151",
               "month": "h\u00f3nap", "year": "\u00e9v", "relation": "kapcsolatt\u00edpus",
               "other_kind": "kapcsol\u00f3d\u00f3 fajta"},
        "da": {"entity": "enhed", "other": "forbundet enhed", "month": "m\u00e5ned",
               "year": "\u00e5r", "relation": "relation", "other_kind": "forbundet type"},
        "de": {"entity": "Eintrag", "other": "verbundener Eintrag", "month": "Monat",
               "year": "Jahr", "relation": "Beziehung", "other_kind": "verbundene Art"},
    },
    "sequence": {
        "en": "Graph sequence -- %s followed by %s within %d days:",
        "hu": "Gr\u00e1f-sorrend -- %s ut\u00e1n %s, %d napon bel\u00fcl:",
        "da": "Grafsekvens -- %s efterfulgt af %s inden for %d dage:",
        "de": "Graphabfolge -- %s gefolgt von %s innerhalb von %d Tagen:",
    },
    "days": {"en": "%d days later", "hu": "%d nappal k\u00e9s\u0151bb",
             "da": "%d dage senere", "de": "%d Tage sp\u00e4ter"},
    "together": {"en": "both at", "hu": "egyszerre itt:", "da": "begge ved",
                 "de": "beide bei"},
    "types": {
        "en": {"person": "Person", "organization": "Organisation", "place": "Place",
               "identifier": "Identifier", "event": "Event", "object": "Object"},
        "hu": {"person": "Szem\u00e9ly", "organization": "Szervezet", "place": "Hely",
               "identifier": "Azonos\u00edt\u00f3", "event": "Esem\u00e9ny",
               "object": "T\u00e1rgy"},
        "da": {"person": "Person", "organization": "Organisation", "place": "Sted",
               "identifier": "Identifikator", "event": "H\u00e6ndelse",
               "object": "Genstand"},
        "de": {"person": "Person", "organization": "Organisation", "place": "Ort",
               "identifier": "Kennung", "event": "Ereignis", "object": "Gegenstand"},
    },
    "tally": {
        "en": "Graph count -- repeated links of the entities asked about, counted once per "
              "date over the documents you may see (use these figures for how many times):",
        "hu": "Gr\u00e1f-sz\u00e1ml\u00e1l\u00e1s -- a k\u00e9rdezett szerepl\u0151k "
              "ism\u00e9tl\u0151d\u0151 kapcsolatai, d\u00e1tumonk\u00e9nt egyszer "
              "sz\u00e1molva, az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 dokumentumok "
              "alapj\u00e1n (a \u201eh\u00e1nyszor\u201d k\u00e9rd\u00e9sekhez ezeket a "
              "sz\u00e1mokat haszn\u00e1lja):",
        "da": "Graft\u00e6lling -- gentagne forbindelser for de adspurgte enheder, talt \u00e9n "
              "gang pr. dato i de dokumenter, du har adgang til (brug disse tal til hvor "
              "mange gange):",
        "de": "Graphz\u00e4hlung -- wiederholte Verbindungen der erfragten Eintr\u00e4ge, einmal "
              "pro Datum gez\u00e4hlt, in den Dokumenten, die Sie sehen d\u00fcrfen (verwenden "
              "Sie diese Zahlen f\u00fcr \u201ewie oft\u201c):",
    },
    "tally_row": {"en": "- %s -- %s -- %s: %d times (%s to %s)",
                  "hu": "- %s -- %s -- %s: %d alkalommal (%s \u00e9s %s k\u00f6z\u00f6tt)",
                  "da": "- %s -- %s -- %s: %d gange (fra %s til %s)",
                  "de": "- %s -- %s -- %s: %d-mal (von %s bis %s)"},
    "joined": {
        "en": "Linked by name: the records of these people from different documents were "
              "joined only by the same name; no birth date or ID number confirms it: %s.",
        "hu": "N\u00e9v alapj\u00e1n \u00f6sszekapcsolva: ezeknek a szem\u00e9lyeknek a "
              "k\u00fcl\u00f6nb\u00f6z\u0151 dokumentumokban szerepl\u0151 bejegyz\u00e9seit csak "
              "az azonos n\u00e9v k\u00f6ti \u00f6ssze, sz\u00fclet\u00e9si d\u00e1tum vagy "
              "azonos\u00edt\u00f3 nem er\u0151s\u00edti meg: %s.",
        "da": "Forbundet via navn: disse personers poster fra forskellige dokumenter er kun "
              "forbundet via samme navn; ingen f\u00f8dselsdato eller ID-nummer bekr\u00e6fter "
              "det: %s.",
        "de": "Namensbasiert verkn\u00fcpft: Die Eintr\u00e4ge dieser Personen aus "
              "verschiedenen Dokumenten sind nur \u00fcber den gleichen Namen verbunden; kein "
              "Geburtsdatum und keine Ausweisnummer best\u00e4tigt es: %s.",
    },
    "and_more": {"en": " and %d more", "hu": " \u00e9s m\u00e9g %d", "da": " og %d mere",
                 "de": " und %d weitere"},
    "counted_joined": {
        "en": "Linked by name: the counts treat records of the same name from different "
              "documents as one person, although no birth date or ID number confirms it.",
        "hu": "N\u00e9v alapj\u00e1n \u00f6sszekapcsolva: a sz\u00e1ml\u00e1l\u00e1s a "
              "k\u00fcl\u00f6nb\u00f6z\u0151 dokumentumokban szerepl\u0151 azonos nev\u0171 "
              "bejegyz\u00e9seket egy szem\u00e9lynek tekinti, b\u00e1r sz\u00fclet\u00e9si "
              "d\u00e1tum vagy azonos\u00edt\u00f3 ezt nem er\u0151s\u00edti meg.",
        "da": "Forbundet via navn: opt\u00e6llingen regner poster med samme navn fra "
              "forskellige dokumenter som \u00e9n person, selv om ingen f\u00f8dselsdato eller "
              "ID-nummer bekr\u00e6fter det.",
        "de": "Namensbasiert verkn\u00fcpft: Die Z\u00e4hlung behandelt gleichnamige "
              "Eintr\u00e4ge aus verschiedenen Dokumenten als eine Person, obwohl kein "
              "Geburtsdatum und keine Ausweisnummer es best\u00e4tigt.",
    },
    "ambiguous_names": {
        "en": "Graph search: %d names in the documents you may see belong to records that "
              "contradict each other (for example different birth dates), so they were not "
              "linked across documents; the result may be incomplete.",
        "hu": "Gr\u00e1fkeres\u00e9s: az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 "
              "dokumentumokban %d n\u00e9vhez egym\u00e1snak ellentmond\u00f3 bejegyz\u00e9sek "
              "tartoznak (p\u00e9ld\u00e1ul elt\u00e9r\u0151 sz\u00fclet\u00e9si d\u00e1tum), "
              "ez\u00e9rt ezeket nem kapcsoltuk \u00f6ssze a dokumentumok k\u00f6z\u00f6tt; az "
              "eredm\u00e9ny hi\u00e1nyos lehet.",
        "da": "Grafs\u00f8gning: %d navne i de dokumenter, du har adgang til, h\u00f8rer til "
              "poster, der modsiger hinanden (f.eks. forskellige f\u00f8dselsdatoer), s\u00e5 de "
              "blev ikke forbundet p\u00e5 tv\u00e6rs af dokumenter; resultatet kan v\u00e6re "
              "ufuldst\u00e6ndigt.",
        "de": "Graphsuche: %d Namen in den Dokumenten, die Sie sehen d\u00fcrfen, geh\u00f6ren "
              "zu Eintr\u00e4gen, die einander widersprechen (z. B. verschiedene "
              "Geburtsdaten), daher wurden sie nicht dokument\u00fcbergreifend verkn\u00fcpft; "
              "das Ergebnis kann unvollst\u00e4ndig sein.",
    },
    "qualified": {
        "en": "Same name, different place: %s -- the name in brackets (institution or source document) tells them apart. Keep it with the name in the answer and do not add up their figures.",
        "hu": "Azonos n\u00e9v, k\u00fcl\u00f6nb\u00f6z\u0151 hely: %s -- a z\u00e1r\u00f3jeles r\u00e9sz (int\u00e9zet vagy forr\u00e1sdokumentum) k\u00fcl\u00f6nb\u00f6zteti meg \u0151ket. A v\u00e1laszban is a z\u00e1r\u00f3jeles r\u00e9sszel egy\u00fctt nevezze meg \u0151ket, \u00e9s ne adja \u00f6ssze a sz\u00e1maikat.",
        "da": "Samme navn, forskelligt sted: %s -- navnet i parentes (institution eller kildedokument) skelner dem. Behold det sammen med navnet i svaret, og l\u00e6g ikke deres tal sammen.",
        "de": "Gleicher Name, anderer Ort: %s -- der Name in Klammern (Anstalt oder Quelldokument) unterscheidet sie. Behalten Sie ihn in der Antwort beim Namen und addieren Sie ihre Zahlen nicht.",
    },
    "unmerged": {
        "en": "Graph search: %d pairs of same-named records in the documents you may see "
              "await an administrator's merge decision. Until merged, links recorded in "
              "different documents are not joined, so this result may be incomplete.",
        "hu": "Gr\u00e1fkeres\u00e9s: az \u00d6n sz\u00e1m\u00e1ra l\u00e1that\u00f3 "
              "dokumentumokban %d azonos nev\u0171 bejegyz\u00e9sp\u00e1r v\u00e1r "
              "adminisztr\u00e1tori egyes\u00edt\u00e9si d\u00f6nt\u00e9sre. Am\u00edg nincsenek "
              "egyes\u00edtve, a k\u00fcl\u00f6nb\u00f6z\u0151 dokumentumokban r\u00f6gz\u00edtett "
              "kapcsolatok nem kapcsol\u00f3dnak \u00f6ssze, ez\u00e9rt az eredm\u00e9ny "
              "hi\u00e1nyos lehet.",
        "da": "Grafs\u00f8gning: %d par af poster med samme navn i de dokumenter, du har "
              "adgang til, venter p\u00e5 en administrators beslutning om sammenl\u00e6gning. "
              "Indtil da forbindes oplysninger fra forskellige dokumenter ikke, s\u00e5 "
              "resultatet kan v\u00e6re ufuldst\u00e6ndigt.",
        "de": "Graphsuche: %d Paare gleichnamiger Eintr\u00e4ge in den Dokumenten, die Sie "
              "sehen d\u00fcrfen, warten auf die Zusammenf\u00fchrungsentscheidung eines "
              "Administrators. Bis dahin werden Verbindungen aus verschiedenen Dokumenten "
              "nicht verkn\u00fcpft, das Ergebnis kann daher unvollst\u00e4ndig sein.",
    },
}
LANG_CODES = {"Hungarian": "hu", "English": "en", "Danish": "da", "German": "de"}


_QUALIFIED = re.compile(r"^(.*\S) \(([^()]+)\)$")


def _t(key, code):
    return _TEXT[key].get(code) or _TEXT[key]["en"]


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", (text or "").casefold())
                   if unicodedata.category(c) != "Mn").strip()


# -- the domain profile ----------------------------------------------------------
_PROFILE = {"at": 0.0, "profile": None, "client": None}


def profile(client):
    """The active domain profile, cached briefly (an admin may change it)."""
    if _PROFILE["profile"] is None or _PROFILE["client"] is not client \
            or time.time() - _PROFILE["at"] > PROFILE_TTL:
        _PROFILE.update(profile=client.post("/profile/get", {}).get("profile") or {},
                        at=time.time(), client=client)
    return _PROFILE["profile"]


def _label_of(labels, code, fallback):
    labels = labels or {}
    return labels.get(code) or labels.get("en") or fallback


def vocabulary(prof, lang=None):
    """The kinds and relations of the profile, for the planner."""
    code = LANG_CODES.get(lang or "", "en")
    subtypes = {}
    for s in prof.get("entity_subtypes") or []:
        subtypes.setdefault(s.get("type"), []).append(
            "%s = %s" % (s["id"], _label_of(s.get("label"), code, s["id"])))
    kinds = ["%s (%s)" % (t, ", ".join(subtypes[t])) if subtypes.get(t) else t
             for t in ENTITY_TYPES]
    relations = []
    # The generic built-ins last: listed first, they were picked for named links.
    ordered = sorted(prof.get("relation_types") or [],
                     key=lambda r: r.get("id") in GENERIC_RELATIONS)
    for r in ordered:
        ends = ""
        if r.get("from") or r.get("to"):
            ends = " (%s -> %s)" % ("/".join(r.get("from") or ["any"]),
                                    "/".join(r.get("to") or ["any"]))
        # The words a user may write for it ("kapcsolattartó" for a relative).
        also = [s for s in r.get("synonyms") or [] if isinstance(s, str) and s.strip()]
        if also:
            ends += " [also: %s]" % ", ".join(also[:MAX_SYNONYMS])
        if r["id"] in GENERIC_RELATIONS:
            ends += " -- %s" % GENERIC_RELATIONS[r["id"]]
        relations.append("%s = %s%s" % (r["id"], _label_of(r.get("label"), code, r["id"]), ends))
    return ("ENTITY KINDS (a kind is a type or one of its subtypes): %s\nRELATIONS: %s"
            % ("; ".join(kinds), "; ".join(relations)))


# -- planning ------------------------------------------------------------------
def _transcript(history, turns=6, chars=600):
    lines = []
    for turn in (history or [])[-turns:]:
        content = (turn.get("content") or "").strip()
        if turn.get("role") in ("user", "assistant") and content:
            lines.append("%s: %s" % ("USER" if turn["role"] == "user" else "ASSISTANT",
                                     content[:chars]))
    return "\n".join(lines)


def _text(value, cap=120):
    return value.strip()[:cap] if isinstance(value, str) and value.strip() else None


def _side_args(value):
    value = value if isinstance(value, dict) else {}
    return {k: v for k, v in (("kind", _text(value.get("kind"))),
                              ("name", _text(value.get("name")))) if v}


def _clean_args(tool, args):
    args = args if isinstance(args, dict) else {}
    if tool == "analyze_document":
        name = _text(args.get("file"), 260)
        return {"file": name} if name else None
    if tool == "read_all":
        raw = args.get("terms")
        raw = [raw] if isinstance(raw, str) else raw
        terms = list(dict.fromkeys(t for t in (_text(x, 80) for x in raw or []) if t))
        if not terms:
            return None
        return {"terms": terms[:MAX_READ_TERMS],
                "mode": "all" if args.get("mode") == "all" else "any"}
    out = {}
    for key in ("entity", "a", "b", "kind", "name", "relation", "other_kind", "first", "then"):
        value = _text(args.get(key))
        if value:
            out[key] = value
    for key in ("from", "to"):
        value = args.get(key)
        if isinstance(value, str) and _DATE_RE.match(value.strip()):
            out[key] = value.strip()
    if tool == "find_pattern":
        steps = []
        for raw in [s for s in args.get("steps") or [] if isinstance(s, dict)][:MAX_STEPS]:
            step = _side_args(raw)
            relation = _text(raw.get("relation"))
            if relation:
                step["relation"] = relation
            steps.append(step)
        if not steps:
            return None
        out["start"] = _side_args(args.get("start"))
        out["steps"] = steps
    if tool == "count":
        group = args.get("group_by")
        out["group_by"] = group if group in GROUP_BY else "entity"
    if tool == "sequence":
        days = args.get("within_days")
        out["within_days"] = days if isinstance(days, int) and 0 <= days <= 3660 else 30
    need = {"relations": ("entity",), "timeline": ("entity",), "copresence": ("entity",),
            "connection": ("a", "b"), "common": ("a", "b"),
            "sequence": ("first", "then")}.get(tool, ())
    return out if all(k in out for k in need) else None


def parse_plan(text):
    """The planner's reply as {"action", "calls", "search", "question", "more"}, or None."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    action = data.get("action")
    if action == "clarify":
        question = data.get("question")
        if isinstance(question, str) and question.strip():
            return {"action": "clarify", "question": question.strip()[:500], "calls": [],
                    "search": "", "more": False}
        return None
    if action in ("chat", "done"):
        return {"action": action, "calls": [], "search": "", "more": False}
    if action != "tools":
        return None
    calls = []
    for call in data.get("calls") or []:
        if not isinstance(call, dict) or call.get("tool") not in TOOLS:
            continue
        args = _clean_args(call["tool"], call.get("args"))
        if args is not None:
            calls.append({"tool": call["tool"], "args": args})
    search = data.get("search")
    return {"action": "tools", "calls": calls[:MAX_CALLS],
            "search": search.strip()[:500] if isinstance(search, str) else "",
            "more": data.get("more") is True}


def plan(question, history, lang=None, vocab="", results="", deep=False):
    """One planning call; None when the model is off, fails or answers nonsense
    (the caller then keeps the plain document + graph-facts path). `deep` offers
    the full-reading tool (first round only)."""
    if llm.GENERATE == "off":
        return None
    system = PLANNER.replace("{vocabulary}", vocab).replace(
        "{deep}", DEEP_TOOL if deep else "") + (
        " Write any question to the user in %s." % lang if lang else "")
    convo = _transcript(history)
    user = ("CONVERSATION:\n%s\n\nMESSAGE: %s" % (convo, question)) if convo \
        else "MESSAGE: %s" % question
    if results:
        user += ("\n\nRESULTS SO FAR:\n%s\n\nPlan the next calls with the names found "
                 "above (the top of a ranking first) for the part of the message still "
                 'unanswered, or reply {"action": "done"} if nothing is left. Never repeat '
                 "a call listed above." % results)
    try:
        content, _, _ = llm._chat(system, user, timeout=PLAN_TIMEOUT, stream=False,
                                  num_predict=600, response_format="json")
    except Exception:  # noqa: BLE001
        return None
    return parse_plan(content)


def orchestrate(run, first, replan, max_rounds=MAX_ROUNDS):
    """Runs the plan; while it asks to see results, replans with them.
    `replan(results_text)` returns the next plan or None. Raises Clarify."""
    current = first
    for round_no in range(1, max_rounds + 1):
        run.run(current["calls"], round_no)
        if not current.get("more"):
            run.stop = "complete"
            break
        if round_no == max_rounds:
            run.stop = "max_rounds"
            break
        nxt = replan(run.summary())
        if not nxt or nxt["action"] != "tools":
            run.stop = (nxt or {}).get("action") or "invalid"
            break
        # A planner that repeats itself has nothing further to look up.
        nxt = dict(nxt, calls=run.fresh(nxt["calls"]))
        if not nxt["calls"]:
            run.stop = "repeat"
            break
        current = nxt
    return run.output(), run.info()


def _links(relation, ends):
    """Whether a profile relation can join the two sides (types or None for any)
    in either direction."""
    if not ends:
        return True
    a, b = ends
    src, dst = relation.get("from") or ENTITY_TYPES, relation.get("to") or ENTITY_TYPES

    def fits(side, allowed):
        return side is None or bool(set(side) & set(allowed))
    return (fits(a, src) and fits(b, dst)) or (fits(a, dst) and fits(b, src))


# -- running the plan ----------------------------------------------------------
class Clarify(Exception):
    """A name the user gave fits several different entities."""

    def __init__(self, question):
        super().__init__(question)
        self.question = question


class Run:
    """Executes plans against the graph within one caller's scope."""

    def __init__(self, client, scope_body, visible, lang=None, max_facts=40, prof=None,
                 name_join=False, message=""):
        self.client = client
        self.scope = scope_body
        self.visible = visible
        self.code = LANG_CODES.get(lang or "", "en")
        self.max_facts = max_facts
        # The user's message: a kind of link it names ("kapcsolattartói") overrides a
        # generic relation the planner chose for it.
        self.message = message or ""
        self._stated = None
        # Patterns, sequences and counts treat same-named records as one person
        # (graph_service/namesakes.py); the answer names who was joined so.
        self.name_join = name_join
        self.joined = {}
        self._joined_counts = False
        self._ambiguous = 0
        self.contexts = []
        self.notes = []
        self.documents = {}
        self.nodes = {}
        self.calls = []
        self._seen = set()
        self._names = {}
        self._profile = prof
        self._maps = None
        self._call_facts = 0
        self._call_budget = max_facts
        self._call_labels = set()
        # Distinct dates of each link of a named entity: how many times, counted here.
        self._tally = {}
        # The people the facts name, in order: their identity data goes with them.
        self._people = []
        # The ids of the names the first planning round passed: the people the
        # message itself is about (later rounds continue from names found).
        self._asked = set()
        self._round = 1
        # A kind word passed as a name ("cell"): its query is skipped quietly.
        self._kind_as_name = False
        # A call skipped on purpose (a pattern touching no one the message named).
        self._quiet = False
        self._unmerged = None
        self._unmerged_noted = False
        # Shown under the answer when a cross-document query came back empty
        # for a reason the model tends to drop (records awaiting a merge).
        self._reason = None
        # Why the planning rounds ended (complete, done, repeat, max_rounds ...).
        self.stop = None

    def query(self, name, **params):
        if self.name_join and name in ("match", "aggregate", "sequence"):
            params["join_namesakes"] = True
        res = self.client.post("/query/" + name, dict(self.scope, **params))
        self.documents.update(res.get("documents") or {})
        if params.get("join_namesakes"):
            self._ambiguous = max(self._ambiguous,
                                  int((res.get("namesakes") or {}).get("ambiguous") or 0))
        return res

    def _mark_joined(self, nodes):
        """Note the people of a result that stand for several same-named records."""
        for node in nodes:
            if node and len(node.get("joined") or []) > 1 and node.get("label"):
                self.joined[node["label"]] = len(node["joined"])

    # the domain profile, in the user's language
    def _label_maps(self):
        if self._maps is None:
            if self._profile is None:
                self._profile = self.client.post("/profile/get", {}).get("profile") or {}
            self._maps = (
                {r["id"]: r for r in self._profile.get("relation_types") or []},
                {s["id"]: s for s in self._profile.get("entity_subtypes") or []})
        return self._maps

    def _label(self, entries, key):
        return _label_of((entries.get(key) or {}).get("label"), self.code, key)

    def _kind_label(self, kind):
        """A subtype or entity type id in the user's language."""
        _, subtypes = self._label_maps()
        if kind in subtypes:
            return self._label(subtypes, kind)
        return _t("types", self.code).get(kind) or kind

    def kind_spec(self, kind):
        """A side filter for a kind (type or subtype, by id or label); None if unknown."""
        if not kind:
            return {}
        found = self.kind_of_word(kind)
        if found in ENTITY_TYPES:
            return {"types": [found]}
        if found:
            return {"subtypes": [found]}
        self.notes.append(_t("unknown", self.code) % kind)
        return None

    def relation_ids(self, relation, ends=None):
        """Relation type ids for an id, label or synonym; [] when none is given,
        None when it is unknown (a count over every relation would mislead).
        `ends`: (types, types) a pattern step links -- the previous and the target
        side, None for any --, used when a generic relation gives way to the
        stated one."""
        if not relation:
            return []
        wanted = fold(relation)
        relations, _ = self._label_maps()
        ids = [rid for rid, r in relations.items()
               if wanted in {fold(rid), *(fold(v) for v in (r.get("label") or {}).values()),
                             *(fold(v) for v in r.get("synonyms") or [])}]
        if not ids:
            self.notes.append(_t("unknown", self.code) % relation)
            return None
        if set(ids) <= set(GENERIC_RELATIONS):
            stated = self._stated_relations(ends)
            if len(stated) == 1:
                return stated
        return ids

    def _stated_relations(self, ends=None):
        """The specific relations the message names by a label or synonym word
        (inflected: "kapcsolattartói") that can link the two `ends`."""
        relations, _ = self._label_maps()
        return [rid for rid in self._stated_terms()
                if _links(relations[rid], ends)]

    def _stated_terms(self):
        """{relation id: the label or synonym the message names it by}, once per run."""
        if self._stated is not None:
            return self._stated
        words = re.findall(r"\w+", fold(self.message))
        text = " ".join(words)
        relations, _ = self._label_maps()
        self._stated = {}
        for rid, r in relations.items():
            if rid in GENERIC_RELATIONS:
                continue
            terms = [t for t in [*(r.get("label") or {}).values(), *(r.get("synonyms") or [])]
                     if isinstance(t, str)]
            for original in terms:
                term = " ".join(re.findall(r"\w+", fold(original)))
                if len(term) < MIN_STATED and " " not in term:
                    continue
                if (" " in term and term in text) or any(w.startswith(term) for w in words):
                    self._stated[rid] = original
                    break
        return self._stated

    def _types_of(self, spec):
        """The entity types a side filter can match (None: any)."""
        if not spec:
            return None
        if spec.get("types"):
            return list(spec["types"])
        _, subtypes = self._label_maps()
        types = {(subtypes.get(s) or {}).get("type") for s in spec.get("subtypes") or []}
        types |= {(self.nodes.get(i) or {}).get("type") for i in spec.get("ids") or []}
        return sorted(t for t in types if t) or None

    def kind_of_word(self, word):
        """The kind id a word names ("Fogvatartott" -> inmate), else None."""
        wanted = fold(word)
        if wanted in ENTITY_TYPES:
            return wanted
        _, subtypes = self._label_maps()
        for sid, s in subtypes.items():
            if wanted in {fold(sid), *(fold(v) for v in (s.get("label") or {}).values())}:
                return sid
        return None

    def side(self, kind=None, name=None):
        """The filter for one side of a link; None when it cannot match anything."""
        if name and self.kind_of_word(name):
            # A category passed as a name ("Inmate") means every entity of that kind;
            # the word is at least as specific as a kind given beside it.
            kind, name = self.kind_of_word(name), None
        spec = self.kind_spec(kind)
        if spec is None:
            return None  # an unknown kind must not widen the search to everything
        if name:
            ids = self.resolve(name)
            if not ids:
                return None
            spec = dict(spec, ids=ids)
        return spec

    def describe(self, nodes):
        """One option line for records sharing a name (namesakes are one choice)."""
        node = nodes[0]
        kind = self._kind_label(node.get("subtype") or node["type"])
        docs = sum(len(n.get("documents") or []) for n in nodes)
        count = ("1 document" if docs == 1 and self.code == "en"
                 else _t("documents", self.code) % docs)
        return "%s (%s, %s)" % (node["label"], kind, count)

    def _options(self, name, found):
        """The ask-back text when `found` holds different names, else None."""
        groups = {}
        for n in found:
            groups.setdefault(fold(n["label"]), []).append(n)
        if len(groups) < 2:
            return None
        options = "\n".join("- " + self.describe(g)
                            for g in list(groups.values())[:MAX_CANDIDATES])
        return "%s\n\n%s" % (_t("ambiguous", self.code) % name, options)

    def _find_prefix(self, word):
        """Entities for one inflected word ("Nagyot" -> Nagy ...), longest prefix first."""
        for cut in range(len(word), MIN_PREFIX - 1, -1):
            stem = word[:cut]
            found = (self.query("find", text=stem, limit=20, types=ENTITY_TYPES)
                     .get("entities") or [])
            # "Nagy" means the Nagys, not "Nagykanizsa".
            whole = [n for n in found if fold(stem) in fold(n["label"]).split()]
            if whole or found:
                return stem, whole or found
        return word, []

    def name_options(self, text):
        """For a question the planner wants to ask back: if the message names
        someone only partly and the graph knows several such names, list them."""
        if self.query("mentioned", text=(text or "")[:4000], limit=1,
                      types=ENTITY_TYPES).get("entities"):
            return None  # a full name is there; the planner's own question stands
        for word in _NAME_RE.findall(text or ""):
            stem, found = self._find_prefix(word)
            question = self._options(stem, found)
            if question:
                return question
        return None

    def resolve(self, name):
        """Ids for one name. Namesakes (same name, separate records) are kept
        together; different entities that fit a partial name are asked back."""
        key = fold(name)
        if key in self._names:
            return self._names[key]
        if self.kind_of_word(name):
            # A planner slip ("common: Kovács, cell"): a kind is no one's name, and
            # a "nothing named cell" note would reach the answer.
            self._kind_as_name = True
            return []
        found = self.query("find", text=name, limit=8, types=ENTITY_TYPES).get("entities") or []
        exact = [n for n in found if key in {fold(x) for x in [n["label"], *n.get("aliases", [])]}]
        if exact:
            picked = exact
        elif len(found) == 1 or len({fold(n["label"]) for n in found}) == 1:
            picked = found
        elif found:
            raise Clarify(self._options(name, found))
        else:
            # Inflected forms ("Kovács Jánossal") are matched by the mention finder.
            picked = (self.query("mentioned", text=name, limit=1, types=ENTITY_TYPES)
                      .get("entities") or [])
            # A number, e-mail or address that is not there is just not there:
            # its first letters ("kovacs.gabor@...") are nobody's name.
            if not picked and len(name.split()) == 1 and _PLAIN_WORD.fullmatch(name):
                stem, found = self._find_prefix(name)
                question = self._options(stem, found)
                if question:
                    raise Clarify(question)
                picked = found
        for node in picked:
            self.nodes[node["id"]] = node
        if not picked:
            self.notes.append(_t("not_found", self.code) % name)
        elif len(picked) > 1:
            self.notes.append(_t("namesakes", self.code) % (len(picked), picked[0]["label"]))
        self._names[key] = [n["id"] for n in picked]
        if self._round == 1:
            self._asked.update(self._names[key])
        return self._names[key]

    def _evidence(self, edge):
        return [ev for ev in edge.get("evidence") or []
                if ev.get("doc_id") in self.documents
                and self.visible(self.documents[ev["doc_id"]].get("source_path") or "")]

    def _add(self, text, ev):
        if len(self.contexts) >= self.max_facts or self._call_facts >= self._call_budget:
            return False
        doc = self.documents[ev["doc_id"]]
        key = (text, doc.get("source_path"), ev.get("page"))
        if key in self._seen:
            return True
        self._seen.add(key)
        quote = " ".join((ev.get("quote") or "").split())
        self.contexts.append({
            "text": "%s%s" % (text, ' "%s"' % quote if quote else ""),
            "source_path": doc.get("source_path"), "corpus_id": doc.get("corpus_id"),
            "page_number": ev.get("page"), "chunk_id": None, "score": 1.0,
            "origin": "graph"})
        self._call_facts += 1
        return True

    def _name(self, node_id):
        node = self.nodes.get(node_id) or {}
        return node.get("label") or node_id

    def _relation_name(self, rtype):
        relations, _ = self._label_maps()
        return self._label(relations, rtype)

    def edge_fact(self, edge):
        evidence = self._evidence(edge)
        if not evidence:
            return 0
        self._count_date(edge)
        name = self._relation_name(edge["type"])
        # The user's own word for the link ("kapcsolattartó" beside "Rokona"):
        # the answer must see that this is the link asked about.
        stated = self._stated_terms().get(edge["type"])
        if stated and fold(stated) not in fold(name):
            name = "%s / %s" % (name, stated)
        raw = edge.get("raw_label") or ""
        if raw and raw != edge["type"] and not re.fullmatch(r"[A-Z0-9_]+", raw):
            name = "%s (%s)" % (name, raw)
        when = edge.get("start") or ""
        if edge.get("end") and edge["end"] != when:
            when = "%s - %s" % (when, edge["end"])
        a, b = self._name(edge["a"]), self._name(edge["b"])
        text = "%s -- %s -- %s%s." % (a, name, b, " (%s)" % when if when else "")
        added = int(self._add(text, evidence[0]))
        if added:
            self._call_labels.update((a, b))
            self._note_people(edge["a"], edge["b"])
        return added

    def _note_people(self, *ids):
        for eid in ids:
            if eid not in self._people and (self.nodes.get(eid) or {}).get("type") == "person":
                self._people.append(eid)

    def _identity(self):
        """One fact per person and source document with the identity data the
        caller may see: the people the message asked about first, then those the
        facts name."""
        named = [i for ids in self._names.values() for i in ids if i in self._asked]
        labels = {k: v.get(self.code) or v["en"] for k, v in DOSSIER_TEXT["attr"].items()}
        out = []
        for eid in list(dict.fromkeys(named + self._people)):
            node = self.nodes.get(eid) or {}
            if node.get("type") != "person":
                continue
            wanted = ID_ATTRS + (PERSONAL_ATTRS if eid in named else ())
            by_doc, pages = {}, {}
            for attr in node.get("attributes") or []:
                doc = self.documents.get(attr.get("doc_id"))
                if attr.get("name") in wanted and doc is not None \
                        and self.visible(doc.get("source_path") or ""):
                    by_doc.setdefault(attr["doc_id"], {}).setdefault(
                        attr["name"], set()).add(str(attr.get("value")))
                    if isinstance(attr.get("page"), int):
                        key = (attr["doc_id"], attr["name"])
                        pages[key] = min(pages.get(key, attr["page"]), attr["page"])
            values, sources = {}, []
            # The documents stating the most first; another only for what is left.
            for doc_id, attrs in sorted(by_doc.items(), key=lambda kv: (-len(kv[1]), kv[0])):
                new = {k: v for k, v in attrs.items() if k not in values}
                if new:
                    values.update(new)
                    sources.append((doc_id, new))
            for doc_id, attrs in sources:
                if len(out) >= MAX_IDENTITY:
                    return out
                parts = ["%s: %s" % (labels.get(k, k), " / ".join(sorted(attrs[k])))
                         for k in wanted if k in attrs]
                doc = self.documents[doc_id]
                # The page of a value this fact states, not of one another document gave.
                stated = [pages[(doc_id, k)] for k in attrs if (doc_id, k) in pages]
                out.append({"text": "%s -- %s." % (node.get("label") or eid, "; ".join(parts)),
                            "source_path": doc.get("source_path"),
                            "corpus_id": doc.get("corpus_id"),
                            "page_number": min(stated) if stated else None,
                            "chunk_id": None, "score": 1.0, "origin": "graph"})
        return out

    def _remember(self, res):
        for node in res.get("nodes") or []:
            if node:
                self.nodes.setdefault(node["id"], node)

    def _count_date(self, edge):
        """Tallies a dated, visible link of an entity the message named, before
        the facts cap: the answer's "how many times" comes from here."""
        if not edge.get("start"):
            return
        named = {i for ids in self._names.values() for i in ids}
        if not named & {edge["a"], edge["b"]}:
            return
        relations, _ = self._label_maps()
        # A free-form link (RELATED_TO "testvére" beside each visit row) or a
        # co-mention is no event of its own.
        if edge["type"] in UNTALLIED or edge["type"] not in relations:
            return
        self._tally.setdefault((edge["type"], edge["a"], edge["b"]), set()).add(edge["start"])

    def _tally_note(self):
        # By name, as the facts read: namesakes are kept together like in resolve()
        # (one person's visits recorded in several documents are several records).
        relations, _ = self._label_maps()
        groups = {}
        for (rtype, a, b), dates in self._tally.items():
            a, b = self._name(a), self._name(b)
            if (relations.get(rtype) or {}).get("symmetric") and b < a:
                a, b = b, a
            groups.setdefault((rtype, a, b), set()).update(dates)
        rows = []
        for (rtype, a, b), dates in groups.items():
            # "2025-03" beside "2025-03-13" is the same occasion, less precisely dated.
            dates = sorted(d for d in dates
                           if not any(o != d and o.startswith(d) for o in dates))
            if len(dates) > 1:
                rows.append((len(dates), a, self._relation_name(rtype), b, dates))
        if not rows:
            return None
        rows.sort(key=lambda r: (-r[0], r[1], r[2], r[3]))
        lines = []
        for n, a, rel, b, dates in rows[:MAX_TALLY_LINES]:
            # The span, not the dates: listed here, they were answered from the
            # note and the cited facts were left out.
            lines.append(_t("tally_row", self.code) % (a, rel, b, n, dates[0], dates[-1]))
        return _t("tally", self.code) + "\n" + "\n".join(lines)

    def _edges(self, edges):
        ordered = sorted(edges, key=lambda e: (not e.get("start"), e.get("start") or "", e["id"]))
        return sum(self.edge_fact(e) for e in ordered)

    # -- the tools -------------------------------------------------------------
    def relations(self, args):
        facts = 0
        for eid in self.resolve(args["entity"]):
            res = self.query("neighborhood", id=eid, hops=1, limit=80)
            self._remember(res)
            facts += self._edges([e for e in res.get("edges") or [] if eid in (e["a"], e["b"])])
        return facts

    def connection(self, args):
        ids_a, ids_b = self.resolve(args["a"]), self.resolve(args["b"])
        facts, found = 0, False
        for a in ids_a[:3]:
            for b in ids_b[:3]:
                path = self.query("path", a=a, b=b, max_hops=6).get("path")
                if path:
                    found = True
                    self._remember(path)
                    facts += sum(self.edge_fact(e) for e in path.get("edges") or [])
        if ids_a and ids_b and not found:
            self.notes.append(_t("no_path", self.code) % (args["a"], args["b"]))
        return facts

    def common(self, args):
        facts = 0
        for a in self.resolve(args["a"])[:3]:
            for b in self.resolve(args["b"])[:3]:
                for item in self.query("common", a=a, b=b).get("common") or []:
                    if item.get("node"):
                        self.nodes.setdefault(item["node"]["id"], item["node"])
                    facts += self.edge_fact(item["edge_a"]) + self.edge_fact(item["edge_b"])
        return facts

    def timeline(self, args):
        facts = 0
        for eid in self.resolve(args["entity"]):
            items = self.query("timeline", id=eid, date_from=args.get("from"),
                               date_to=args.get("to")).get("timeline") or []
            for item in items:
                other = item.get("other") or {}
                if other:
                    self.nodes.setdefault(other["id"], other)
                # The link's own direction (a visit is the visitor's, not the visited's).
                a, b = item.get("a"), item.get("b")
                if {a, b} != {eid, other.get("id", "")}:
                    a, b = eid, other.get("id", "")
                edge = {"id": "", "a": a, "b": b, "type": item["type"],
                        "start": item.get("date"), "end": item.get("end"),
                        "evidence": item.get("evidence") or []}
                facts += self.edge_fact(edge)
        return facts

    def copresence(self, args):
        facts = 0
        word = _t("together", self.code)
        for eid in self.resolve(args["entity"]):
            items = self.query("copresence", id=eid, date_from=args.get("from"),
                               date_to=args.get("to")).get("copresence") or []
            for item in items:
                evidence = self._evidence({"evidence": item.get("evidence") or []})
                if not evidence:
                    continue
                span = item["from"] + (" - %s" % item["to"] if item.get("to")
                                       and item["to"] != item["from"] else "")
                text = "%s, %s %s %s (%s)." % (self._name(eid), item["other"]["label"], word,
                                                item["place"]["label"], span)
                for ev in evidence[:2]:
                    if self._add(text, ev):
                        facts += 1
                        self._call_labels.add(item["other"]["label"])
                        self.nodes.setdefault(item["other"]["id"], item["other"])
                        self._note_people(item["other"]["id"])
        return facts

    def shared_identifiers(self, args):
        facts = 0
        params = {"limit": 20}
        if args.get("kind"):
            spec = self.kind_spec(args["kind"])
            if spec is None:
                return 0
            if spec.get("subtypes"):
                params["subtypes"] = spec["subtypes"]
        for item in self.query("shared", **params).get("shared") or []:
            holders = item.get("holders") or []
            # Namesakes (one person split into records) are not "several people".
            if len({fold(h["node"]["label"]) for h in holders}) < 2:
                continue
            self.nodes.setdefault(item["identifier"]["id"], item["identifier"])
            for holder in holders:
                self.nodes.setdefault(holder["node"]["id"], holder["node"])
                facts += sum(self.edge_fact(e) for e in holder.get("edges") or [])
        return facts

    def most_connected(self, args):
        kind = (args.get("kind") or "person").casefold()
        kind = kind if kind in ENTITY_TYPES else "person"
        ranking = self.query("ranking", types=[kind], limit=10).get("ranking") or []
        if ranking:
            lines = ["%d. %s -- %d" % (i, r["node"]["label"], r["degree"])
                     for i, r in enumerate(ranking, 1)]
            self.notes.append(_t("ranking", self.code) + "\n" + "\n".join(lines))
            self._call_labels.update(r["node"]["label"] for r in ranking)
        return len(ranking)

    def find_pattern(self, args):
        start = self.side(**args.get("start") or {})
        steps = []
        previous = self._types_of(start)
        for step in args["steps"]:
            target = self.side(step.get("kind"), step.get("name"))
            if target is None:
                return 0
            relations = self.relation_ids(step.get("relation"),
                                          (previous, self._types_of(target)))
            if relations is None:
                return 0
            steps.append({"relations": relations or None, "target": target or None})
            previous = self._types_of(target)
        if start is None:
            return 0
        if self._asked and not (start or {}).get("ids") \
                and not any((s["target"] or {}).get("ids") for s in steps):
            # The message names someone: a chain over the whole graph that
            # touches no one named is noise that crowds out the facts asked for.
            self._quiet = True
            return 0
        res = self.query("match", start=start or None, steps=steps, limit=40,
                         date_from=args.get("from"), date_to=args.get("to"))
        facts = 0
        for path in res.get("paths") or []:
            self._remember(path)
            self._mark_joined(path.get("nodes") or [])
            facts += sum(self.edge_fact(e) for e in path.get("edges") or [])
        return facts

    def _spec_label(self, spec):
        """The user-language label of a single-subtype side filter, else None."""
        subtypes = (spec or {}).get("subtypes") or []
        if len(subtypes) != 1:
            return None
        return self._label(self._label_maps()[1], subtypes[0])

    def count(self, args):
        relations = self.relation_ids(args.get("relation"))
        focus = self.side(args.get("kind"), args.get("name"))
        other = self.kind_spec(args.get("other_kind"))
        if relations is None or focus is None or other is None:
            return 0
        group = GROUP_BY[args.get("group_by") or "entity"]
        rows = self.query("aggregate", relations=relations or None, focus=focus or None,
                          other=other or None, group_by=group, limit=15,
                          date_from=args.get("from"), date_to=args.get("to")).get("rows") or []
        if not rows:
            return 0
        relations_map, _ = self._label_maps()
        what = ", ".join(self._relation_name(r) for r in relations) if relations \
            else _t("all_links", self.code)
        lines = []
        # Name the kind the "different" figure counts, when the call fixed it.
        counted = self._spec_label(other if group == "focus" else
                                   focus if group == "other" else None)
        for i, row in enumerate(rows, 1):
            label = row["label"]
            if group == "relation":
                label = self._label(relations_map, row["key"])
            elif group == "other_kind":
                label = self._kind_label(row["key"])
            if counted:
                lines.append(_t("count_row_kind", self.code) % (
                    i, label, counted, row["distinct"], row["count"]))
            else:
                lines.append(_t("count_row", self.code) % (
                    i, label, row["count"], row["distinct"]))
            self._call_labels.add(label)
        self.notes.append(_t("count", self.code) % (
            what, _t("group", self.code)[args.get("group_by") or "entity"])
            + "\n" + "\n".join(lines))
        if group in ("focus", "other"):
            # Per-entity figures are where joining by name changes the numbers.
            self._mark_joined([row.get("node") for row in rows])
            self._joined_counts = self._joined_counts or any(
                len(n.get("joined") or []) > 1 for row in rows for n in row.get("nodes") or [])
        for row in rows[:8]:
            self._remember(row)
            for edge in row.get("samples") or []:
                if self.edge_fact(edge):
                    break
        return len(rows)

    def sequence(self, args):
        first, then = self.relation_ids(args["first"]), self.relation_ids(args["then"])
        focus = self.side(args.get("kind"), args.get("name"))
        if not first or not then or focus is None:
            return 0
        pairs = self.query("sequence", first={"relations": first}, then={"relations": then},
                           within_days=args.get("within_days", 30), focus=focus or None,
                           date_from=args.get("from"), date_to=args.get("to"),
                           limit=30).get("pairs") or []
        if not pairs:
            return 0
        lines = []
        for pair in pairs:
            e1, e2 = pair["first"], pair["then"]
            # The listing names both links, so both must be visible to the caller.
            if not self._evidence(e1) or not self._evidence(e2):
                continue
            self.nodes.setdefault(pair["focus"]["id"], pair["focus"])
            self._remember(pair)
            self._mark_joined([pair["focus"], *(pair.get("nodes") or [])])
            lines.append("- %s: %s %s (%s) -> %s %s (%s), %s" % (
                pair["focus"]["label"],
                self._relation_name(e1["type"]), self._name(e1["b"] if e1["a"] ==
                                                           pair["focus"]["id"] else e1["a"]),
                e1["start"], self._relation_name(e2["type"]),
                self._name(e2["b"] if e2["a"] == pair["focus"]["id"] else e2["a"]),
                e2["start"], _t("days", self.code) % pair["days"]))
            self.edge_fact(e1)
            self.edge_fact(e2)
            self._call_labels.add(pair["focus"]["label"])
        if not lines:
            return 0
        self.notes.append(_t("sequence", self.code) % (
            self._relation_name(first[0]), self._relation_name(then[0]),
            args.get("within_days", 30)) + "\n" + "\n".join(lines))
        return len(lines)

    def fresh(self, calls):
        """The calls not run yet in this run."""
        done = {(c["tool"], json.dumps(c["args"], sort_keys=True)) for c in self.calls}
        out = []
        for call in calls:
            key = (call["tool"], json.dumps(call["args"], sort_keys=True))
            if key not in done:
                done.add(key)
                out.append(call)
        return out

    def run(self, calls, round_no=1):
        """Runs one round of calls; raises Clarify on an ambiguous name."""
        self._round = round_no
        # read_all is the document side of a plan; the caller runs it.
        calls = self.fresh([c for c in calls if c["tool"] in GRAPH_TOOLS])
        # One broad call (a crowded cell) must not crowd out the others' facts.
        self._call_budget = max(6, self.max_facts // max(1, len(calls)))
        for call in calls:
            noted = len(self.notes)
            self._call_facts = 0
            self._call_labels = set()
            self._kind_as_name = False
            self._quiet = False
            facts = getattr(self, call["tool"])(call["args"])
            self.calls.append({"tool": call["tool"], "args": call["args"], "facts": facts,
                               "round": round_no, "entities": sorted(self._call_labels)[:20]})
            if self._quiet:
                self.calls[-1]["skipped"] = True
            cross = call["tool"] in ("find_pattern", "sequence")
            if self._quiet:
                continue
            if not facts and len(self.notes) == noted and not self._kind_as_name:
                text = _t("nothing", self.code) % call["tool"]
                reason = self._empty_reason() if cross and not self._unmerged_noted else None
                if reason:
                    # Said in the same breath, so the answer cannot drop the reason.
                    text += " " + reason
                    self._unmerged_noted = True
                    self._reason = reason
                self.notes.append(text)
            elif not self.name_join and ((cross and not facts) or (
                    call["tool"] == "count"
                    and call["args"].get("group_by", "entity") in ("entity", "other"))):
                self._note_unmerged()
        return self.output()

    def _empty_reason(self):
        """Why a cross-document query may have found nothing, or None."""
        if self.name_join:
            # Joined records cannot be the reason; conflicting namesakes can.
            return _t("ambiguous_names", self.code) % self._ambiguous \
                if self._ambiguous else None
        pending = self._pending()
        return _t("unmerged", self.code) % pending if pending else None

    def _joined_note(self):
        """Which people were joined by name alone in the results, or None."""
        if not self.joined:
            return _t("counted_joined", self.code) if self._joined_counts else None
        names = sorted(self.joined)
        listed = ", ".join("%s (%d)" % (n, self.joined[n]) for n in names[:MAX_JOINED_LISTED])
        if len(names) > MAX_JOINED_LISTED:
            listed += _t("and_more", self.code) % (len(names) - MAX_JOINED_LISTED)
        return _t("joined", self.code) % listed

    @property
    def caveat(self):
        """Shown under the answer as is: the model may drop these."""
        parts = [p for p in (self._joined_note(), self._reason) if p]
        return "\n\n".join(parts) or None

    def _pending(self):
        """Same-named record pairs awaiting a merge decision in the visible
        documents (0 when unknown); asked once per run."""
        if self._unmerged is None:
            try:
                self._unmerged = int(self.client.post(
                    "/review/list", dict(self.scope, limit=1)).get("total") or 0)
            except Exception:  # noqa: BLE001 -- the note is advisory
                self._unmerged = 0
        return self._unmerged

    def _note_unmerged(self):
        """Unmerged namesakes split cross-document chains and per-entity counts;
        say so once."""
        if not self._unmerged_noted and self._pending():
            self._unmerged_noted = True
            self.notes.append(_t("unmerged", self.code) % self._pending())

    def _qualified_note(self):
        """Places that share a name ("102. sz\u00e1m\u00fa z\u00e1rka" of two prisons, or of
        a list naming no prison) carry their container or document in brackets; a
        model tends to drop the brackets and add the figures up."""
        labels = {label for c in self.calls for label in c["entities"]}
        labels |= {n["label"] for n in self.nodes.values() if n.get("label")}
        bases = {}
        for label in labels:
            m = _QUALIFIED.match(label)
            if m:
                bases.setdefault(m.group(1), set()).add(label)
        same = sorted(b for b, variants in bases.items() if len(variants) > 1 or b in labels)
        return _t("qualified", self.code) % ", ".join(same[:5]) if same else None

    def output(self):
        joined = self._joined_note()
        qualified = self._qualified_note()
        tally = self._tally_note()
        notes = [{"text": n, "source_path": None, "title": "graph search", "corpus_id": None,
                  "page_number": None, "chunk_id": None, "score": 1.0, "origin": "graph-note"}
                 for n in self.notes + [x for x in (tally, joined, qualified) if x]]
        return notes + self.contexts + self._identity()

    def summary(self):
        """What the calls so far found, for the next planning round. Rankings and
        sequences keep their top lines: the next round looks into those entities."""
        lines = ["- %s %s: %d results; entities: %s" % (
            c["tool"], json.dumps(c["args"], ensure_ascii=False), c["facts"],
            ", ".join(c["entities"]) or "none") for c in self.calls]
        lines += ["- note: %s" % "\n  ".join(n.split("\n")[:SUMMARY_LINES + 1])
                  for n in self.notes]
        return "\n".join(lines)

    def info(self):
        out = {"calls": [dict({k: c[k] for k in ("tool", "args", "facts", "round")},
                              **({"skipped": True} if c.get("skipped") else {}))
                         for c in self.calls],
               "facts": len(self.contexts),
               "entities": sorted({n["label"] for n in self.nodes.values()
                                   if any(n["id"] in ids for ids in self._names.values())})}
        if self.caveat:
            out["caveat"] = self.caveat
        if self.stop:
            out["stop"] = self.stop
        return out
