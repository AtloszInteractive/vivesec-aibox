"""Validates one document's extraction result against the active profile (E26).

The extraction pipeline (later) and the oracle loader send the same payload:

  {"document":  {doc_id, corpus_id, source_path, title?, kind?, pages?, acl?},
   "entities":  [{id, type, subtype?, label?, attributes?: [{name, value, page?}]}],
   "mentions":  [{id, entity_id, text, page?, start?, end?, method?, confidence?}],
   "relations": [{id, type, a, b, start?, end?, raw_label?,
                  evidence: [{quote, page?, method?, confidence?}]}]}

Anything the profile does not allow is rejected; the pipeline must map an
unknown relation onto RELATED_TO (keeping the original wording in raw_label).
"""
from __future__ import annotations

import re

from model import CORE_TYPES, METHODS, AttributeValue, Document, Entity, Evidence, Mention, Relation
from visibility import parse_doc_acl

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_ITEMS = 50000


class IngestError(ValueError):
    pass


def _str(raw, name, required=True, limit=2000):
    if raw is None and not required:
        return ""
    if not isinstance(raw, str) or (required and not raw.strip()) or len(raw) > limit:
        raise IngestError("%s must be a string of at most %d characters" % (name, limit))
    return raw


def _page(raw, name):
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise IngestError("%s must be a page number" % name)
    return raw


def _date(raw, name):
    if raw is None:
        return None
    if not isinstance(raw, str) or not _DATE.match(raw):
        raise IngestError("%s must be an ISO date (YYYY-MM-DD)" % name)
    return raw


def _confidence(raw, name):
    value = 1.0 if raw is None else raw
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise IngestError("%s must be between 0 and 1" % name)
    return float(value)


def _method(raw, name):
    value = raw or "llm"
    if value not in METHODS:
        raise IngestError("%s must be one of %s" % (name, METHODS))
    return value


def _list(raw, name):
    raw = raw or []
    if not isinstance(raw, list) or len(raw) > MAX_ITEMS:
        raise IngestError("%s must be a list of at most %d items" % (name, MAX_ITEMS))
    return raw


def parse_document_payload(body: dict, profile: dict):
    d = body.get("document")
    if not isinstance(d, dict):
        raise IngestError("document is required")
    try:
        acl = parse_doc_acl(d.get("acl"))
    except ValueError as e:
        raise IngestError(str(e))
    doc = Document(_str(d.get("doc_id"), "document.doc_id", limit=200),
                   _str(d.get("corpus_id"), "document.corpus_id", limit=200),
                   _str(d.get("source_path"), "document.source_path"),
                   _str(d.get("title"), "document.title", required=False),
                   acl, _str(d.get("kind"), "document.kind", required=False, limit=100),
                   _page(d.get("pages"), "document.pages") or 0)

    subtypes = {s["id"]: s["type"] for s in profile["entity_subtypes"]}
    rel_types = {r["id"]: r for r in profile["relation_types"]}

    entities, types = [], {}
    for i, e in enumerate(_list(body.get("entities"), "entities")):
        where = "entities[%d]" % i
        if not isinstance(e, dict):
            raise IngestError("%s must be an object" % where)
        eid = _str(e.get("id"), where + ".id", limit=300)
        type_ = e.get("type")
        if type_ not in CORE_TYPES:
            raise IngestError("%s.type must be one of %s" % (where, CORE_TYPES))
        subtype = e.get("subtype") or ""
        if subtype and subtypes.get(subtype) != type_:
            raise IngestError("%s.subtype %r is not a %s subtype in the profile"
                              % (where, subtype, type_))
        attrs = []
        for j, a in enumerate(_list(e.get("attributes"), where + ".attributes")):
            if not isinstance(a, dict):
                raise IngestError("%s.attributes[%d] must be an object" % (where, j))
            attrs.append(AttributeValue(_str(a.get("name"), "attribute name", limit=100),
                                        _str(a.get("value"), "attribute value"), doc.doc_id,
                                        _page(a.get("page"), "attribute page")))
        entities.append(Entity(eid, type_, subtype, _str(e.get("label"), where + ".label",
                                                         required=False), attrs))
        types[eid] = type_

    mentions = []
    for i, m in enumerate(_list(body.get("mentions"), "mentions")):
        where = "mentions[%d]" % i
        if not isinstance(m, dict) or m.get("entity_id") not in types:
            raise IngestError("%s must reference an entity of this payload" % where)
        mentions.append(Mention(_str(m.get("id"), where + ".id", limit=300), doc.doc_id,
                                m["entity_id"], _str(m.get("text"), where + ".text"),
                                _page(m.get("page"), where + ".page"),
                                _page(m.get("start"), where + ".start"),
                                _page(m.get("end"), where + ".end"),
                                _method(m.get("method"), where + ".method"),
                                _confidence(m.get("confidence"), where + ".confidence")))

    relations = []
    for i, r in enumerate(_list(body.get("relations"), "relations")):
        where = "relations[%d]" % i
        if not isinstance(r, dict):
            raise IngestError("%s must be an object" % where)
        spec = rel_types.get(r.get("type"))
        if spec is None:
            raise IngestError("%s.type %r is not in the profile (map it to RELATED_TO)"
                              % (where, r.get("type")))
        a, b = r.get("a"), r.get("b")
        if a not in types or b not in types:
            raise IngestError("%s must connect entities of this payload" % where)
        if types[a] not in spec["from"] or types[b] not in spec["to"]:
            raise IngestError("%s: %s cannot go from %s to %s" % (where, spec["id"], types[a],
                                                                  types[b]))
        if spec["symmetric"] and b < a:
            a, b = b, a
        evidence = []
        for j, ev in enumerate(_list(r.get("evidence"), where + ".evidence")):
            if not isinstance(ev, dict):
                raise IngestError("%s.evidence[%d] must be an object" % (where, j))
            evidence.append(Evidence(doc.doc_id, _str(ev.get("quote"), "evidence quote",
                                                      required=False),
                                     _page(ev.get("page"), "evidence page"),
                                     _method(ev.get("method"), "evidence method"),
                                     _confidence(ev.get("confidence"), "evidence confidence")))
        if not evidence:
            raise IngestError("%s needs at least one evidence item" % where)
        relations.append(Relation(_str(r.get("id"), where + ".id", limit=300), spec["id"], a, b,
                                  _date(r.get("start"), where + ".start"),
                                  _date(r.get("end"), where + ".end"),
                                  _str(r.get("raw_label"), where + ".raw_label", required=False,
                                       limit=300), evidence))
    return doc, entities, mentions, relations
