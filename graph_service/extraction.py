"""One document's local graph: rules + language model + co-mentions (E26).

Layers, cheapest first:
  1. identifier patterns from the profile (phone, plate, ID card ...): exact, free;
  2. the local model reads one excerpt at a time and returns entities with every
     spelling it saw, attributes, and typed relations with a verbatim quote;
  3. sentences that name several entities give weak CO_MENTIONED edges.
Everything the model returns is checked against the excerpt: a name or quote
that does not occur in the text is dropped, so the graph never holds
something no document says.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from domain_profile import normalize_value
from model import CORE_TYPES

MAX_CHUNK_CHARS = 3000
TABLE_ROWS_PER_CHUNK = 15
ATTRIBUTE_KEYS = ("birth_date", "birth_place", "mother_name", "id_number")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.casefold())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.split())


def squash(text: str) -> str:
    return " ".join(text.split())


@dataclass
class Chunk:
    page: int
    offset: int  # start of the chunk inside the page text
    text: str


@dataclass
class LocalEntity:
    ref: str
    type: str
    subtype: str = ""
    names: list[str] = field(default_factory=list)
    attrs: dict[str, tuple[str, int]] = field(default_factory=dict)  # key -> (value, page)
    value: str = ""  # normalized identifier value
    method: str = "llm"  # llm | structured (table mapping)


@dataclass
class LocalRelation:
    type: str
    a: str
    b: str
    quote: str
    page: int
    method: str
    start: str | None = None
    end: str | None = None
    raw_label: str = ""
    confidence: float = 0.8


# -- chunking -------------------------------------------------------------------

def _is_table(lines: list[str]) -> bool:
    if len(lines) < 4:
        return False
    sep = ";" if lines[0].count(";") >= 2 else "\t" if lines[0].count("\t") >= 2 else None
    return bool(sep) and sum(1 for ln in lines[1:6] if ln.count(sep) >= 2) >= 3


def chunk_pages(pages: list[tuple[int, str]]) -> list[Chunk]:
    out = []
    for page, text in pages:
        lines = text.split("\n")
        if _is_table(lines):
            header = lines[0]
            pos = len(header) + 1
            for i in range(1, len(lines), TABLE_ROWS_PER_CHUNK):
                rows = lines[i:i + TABLE_ROWS_PER_CHUNK]
                body = "\n".join(rows)
                out.append(Chunk(page, pos, header + "\n" + body))
                pos += len(body) + 1
            continue
        start, current = 0, []
        size = 0
        offset = 0
        for line in lines:
            if current and size + len(line) + 1 > MAX_CHUNK_CHARS:
                out.append(Chunk(page, start, "\n".join(current)))
                start, current, size = offset, [], 0
            current.append(line)
            size += len(line) + 1
            offset += len(line) + 1
        if any(x.strip() for x in current):
            out.append(Chunk(page, start, "\n".join(current)))
    return out


# -- identifier patterns ----------------------------------------------------------

def find_identifiers(pages: list[tuple[int, str]], profile: dict) -> list[tuple[str, str, str, int, int, int]]:
    """[(subtype, surface, normalized, page, start, end)] for every profile pattern hit."""
    hits = []
    for p in profile["identifier_patterns"]:
        rx = re.compile(p["regex"])
        for page, text in pages:
            for m in rx.finditer(text):
                hits.append((p["subtype"], m.group(0), normalize_value(m.group(0), p["normalize"]),
                             page, m.start(), m.end()))
    return hits


# -- language model -------------------------------------------------------------

def _schema(profile: dict) -> dict:
    rel_ids = [r["id"] for r in profile["relation_types"] if r["id"] != "CO_MENTIONED"]
    entity = {"type": "object", "properties": {
        "ref": {"type": "string"}, "type": {"type": "string", "enum": list(CORE_TYPES)},
        "subtype": {"type": "string"},
        "names": {"type": "array", "items": {"type": "string"}},
        **{k: {"type": "string"} for k in ATTRIBUTE_KEYS}},
        "required": ["ref", "type", "names"]}
    relation = {"type": "object", "properties": {
        "type": {"type": "string", "enum": rel_ids}, "from": {"type": "string"},
        "to": {"type": "string"}, "date": {"type": "string"}, "end_date": {"type": "string"},
        "label": {"type": "string"}, "quote": {"type": "string"}},
        "required": ["type", "from", "to", "quote"]}
    return {"type": "object", "properties": {
        "entities": {"type": "array", "items": entity},
        "relations": {"type": "array", "items": relation}}, "required": ["entities", "relations"]}


def _system_prompt(profile: dict) -> str:
    subtypes = "; ".join("%s (%s)" % (s["id"], s["type"]) for s in profile["entity_subtypes"])
    rels = "\n".join("- %s: %s -> %s. %s%s" % (
        r["id"], "/".join(r["from"]), "/".join(r["to"]), r["label"]["en"],
        (" Typical wording: " + ", ".join(r["synonyms"][:8])) if r["synonyms"] else "")
        for r in profile["relation_types"] if r["id"] != "CO_MENTIONED")
    return (
        "You build a knowledge graph from ONE excerpt of a document. Use only the excerpt.\n"
        "Entity types: person; organization; place (addresses, institutions, rooms, cells, towns); "
        "identifier (phone numbers, ID card numbers, licence plates, bank accounts, case numbers, "
        "e-mail addresses); event (incidents, meetings); object (vehicles, devices).\n"
        "Allowed subtypes (leave empty if none fits): " + subtypes + "\n"
        "For each entity give a short ref (e1, e2 ...) and in `names` EVERY spelling the excerpt "
        "uses for it, copied character for character (keep misspellings, missing accents, "
        "initials, capitals). Words such as 'nevezett', 'az illető', 'he' are not names.\n"
        "For people also copy birth_date (YYYY-MM-DD), birth_place, mother_name and id_number "
        "when the excerpt states them.\n"
        "Relations - use only these types:\n" + rels + "\n"
        "Use RELATED_TO with a short `label` only when a stated relation fits none of them.\n"
        "`from`/`to` are entity refs. `quote` is the exact sentence or table row that states the "
        "relation, copied verbatim. Fill `date` (YYYY-MM-DD) whenever the sentence, the table row "
        "or the document gives the day of the event; `end_date` when a period ends.\n"
        "A table row is one statement: relate the entities of that row directly (do not create "
        "an event for a row).\n"
        "When a sentence refers back to a named person ('nevezett', 'the inmate'), attach the "
        "relation to that named person.\n"
        "Never add a relation the excerpt does not state. Return JSON only."
    )


def _found(name: str, text_folded: str) -> bool:
    return bool(name.strip()) and fold(name) in text_folded


def _dates_in(text: str) -> set[str]:
    out = set()
    for y, m, d in re.findall(r"(\d{4})[.\-/] ?(\d{1,2})[.\-/] ?(\d{1,2})", text):
        out.add("%s-%02d-%02d" % (y, int(m), int(d)))
    return out


def llm_extract(chunk: Chunk, profile: dict, llm, doc_title: str = "") -> tuple[list, list]:
    """Validated (entities, relations) of one chunk; refs are chunk-local."""
    user = "Document: %s\n---\n%s\n---" % (doc_title, chunk.text)
    raw = llm.json_call(_system_prompt(profile), user, _schema(profile), max_tokens=8192)
    text_folded = fold(chunk.text)
    dates = _dates_in(chunk.text)
    subtypes = {s["id"]: s["type"] for s in profile["entity_subtypes"]}
    rel_specs = {r["id"]: r for r in profile["relation_types"]}

    entities = {}
    for e in raw.get("entities") or []:
        if not isinstance(e, dict) or e.get("type") not in CORE_TYPES or not e.get("ref"):
            continue
        names = [n for n in dict.fromkeys(e.get("names") or []) if isinstance(n, str)
                 and _found(n, text_folded)]
        if e["type"] == "person":
            # A person's name has a capitalised word; drops roles like "parancsnok".
            names = [n for n in names if any(w[:1].isupper() for w in n.split())]
        if not names:
            continue
        subtype = e.get("subtype") or ""
        if subtypes.get(subtype) != e["type"]:
            subtype = ""
        ent = LocalEntity(str(e["ref"]), e["type"], subtype, names)
        for key in ATTRIBUTE_KEYS:
            value = e.get(key)
            if not isinstance(value, str) or not value.strip():
                continue
            ok = value in dates if key == "birth_date" else _found(value, text_folded)
            if ok:
                ent.attrs[key] = (value.strip(), chunk.page)
        entities[ent.ref] = ent

    relations = []
    for r in raw.get("relations") or []:
        if not isinstance(r, dict):
            continue
        spec = rel_specs.get(r.get("type"))
        a, b = entities.get(r.get("from")), entities.get(r.get("to"))
        if not spec or not a or not b or a is b:
            continue
        if a.type not in spec["from"] or b.type not in spec["to"]:
            if b.type in spec["from"] and a.type in spec["to"]:
                a, b = b, a
            else:
                continue
        quote = squash(r.get("quote") or "")
        if not quote or fold(quote) not in text_folded:
            continue
        start = r.get("date") if r.get("date") in dates else None
        end = r.get("end_date") if r.get("end_date") in dates else None
        relations.append(LocalRelation(spec["id"], a.ref, b.ref, quote, chunk.page, "llm", start,
                                       end, str(r.get("label") or "")[:120]))
    return list(entities.values()), relations


# -- co-mentions ------------------------------------------------------------------

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def co_mentions(pages: list[tuple[int, str]], spans: dict[str, list[tuple[int, int, int]]],
                max_entities: int) -> list[LocalRelation]:
    """Weak edges between entities named in the same sentence or table row."""
    out = []
    by_page: dict[int, list[tuple[int, int, str]]] = {}
    for ref, items in spans.items():
        for page, s, e in items:
            by_page.setdefault(page, []).append((s, e, ref))
    for page, text in pages:
        hits = sorted(by_page.get(page, []))
        if not hits:
            continue
        for m in _SENTENCE.finditer(text):
            inside = sorted({ref for s, e, ref in hits if s >= m.start() and e <= m.end()})
            if 2 <= len(inside) <= max_entities:
                quote = squash(m.group(0))[:300]
                for i, a in enumerate(inside):
                    for b in inside[i + 1:]:
                        out.append(LocalRelation("CO_MENTIONED", a, b, quote, page, "cooccurrence",
                                                 confidence=0.3))
    return out
