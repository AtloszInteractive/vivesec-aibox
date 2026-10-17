"""In-memory graph store with edge-level visibility (E26).

Used by the tests, the demo and as the reference implementation of the
visibility rule; the Neo4j store must answer every query identically.

Visibility: a mention or attribute value is visible when its document is;
a relation is visible when at least one evidence item is AND both endpoints
are visible; only the visible evidence is returned. Labels are rebuilt from
visible mentions, so a name seen only in a hidden document never leaks.
"""
from __future__ import annotations

import threading
from collections import Counter

from model import AttributeValue, Document, Entity, Evidence, Mention, Relation, WEAK_METHODS


class StoreError(ValueError):
    pass


def _rel_key(r: Relation) -> tuple:
    a, b = (r.a, r.b)
    return (r.type, a, b, r.start or "", r.end or "")


class MemoryGraphStore:
    def __init__(self):
        self._lock = threading.RLock()
        self.documents: dict[str, Document] = {}
        self.entities: dict[str, Entity] = {}
        self.mentions: dict[str, Mention] = {}
        self.relations: dict[str, Relation] = {}
        self._mentions_of: dict[str, set[str]] = {}
        self._rels_of: dict[str, set[str]] = {}
        self._rel_by_key: dict[tuple, str] = {}
        self._doc_mentions: dict[str, set[str]] = {}
        self._doc_rels: dict[str, set[str]] = {}
        self._doc_attr_entities: dict[str, set[str]] = {}

    # -- writes ----------------------------------------------------------------

    def put_document(self, doc: Document, entities: list[Entity], mentions: list[Mention],
                     relations: list[Relation]) -> dict:
        """Replace everything previously derived from this document."""
        with self._lock:
            self.drop_document(doc.doc_id)
            self.documents[doc.doc_id] = doc
            for e in entities:
                self._upsert_entity(e)
                for a in e.attributes:
                    self._doc_attr_entities.setdefault(a.doc_id, set()).add(e.id)
            for m in mentions:
                if m.doc_id != doc.doc_id or m.entity_id not in self.entities:
                    raise StoreError("mention %s must belong to this document and a known entity"
                                     % m.id)
                if m.id in self.mentions:
                    raise StoreError("mention id %s is already used" % m.id)
                self.mentions[m.id] = m
                self._mentions_of.setdefault(m.entity_id, set()).add(m.id)
                self._doc_mentions.setdefault(doc.doc_id, set()).add(m.id)
            for r in relations:
                if r.a not in self.entities or r.b not in self.entities:
                    raise StoreError("relation %s connects unknown entities" % r.id)
                if any(ev.doc_id != doc.doc_id for ev in r.evidence) or not r.evidence:
                    raise StoreError("relation %s needs evidence from this document" % r.id)
                existing = self.relations.get(r.id)
                if existing is not None and _rel_key(existing) != _rel_key(r):
                    raise StoreError("relation id %s is already used" % r.id)
                self._add_relation(r)
            return {"entities": len(entities), "mentions": len(mentions),
                    "relations": len(relations)}

    def _upsert_entity(self, e: Entity) -> None:
        cur = self.entities.get(e.id)
        if cur is None:
            self.entities[e.id] = Entity(e.id, e.type, e.subtype, e.label, list(e.attributes),
                                         e.status, e.merged_into)
            return
        if cur.type != e.type:
            raise StoreError("entity %s already exists with type %s" % (e.id, cur.type))
        cur.subtype = cur.subtype or e.subtype
        seen = {(a.name, a.value, a.doc_id) for a in cur.attributes}
        cur.attributes += [a for a in e.attributes if (a.name, a.value, a.doc_id) not in seen]

    def _add_relation(self, r: Relation) -> None:
        key = _rel_key(r)
        existing_id = self._rel_by_key.get(key)
        for ev in r.evidence:
            self._doc_rels.setdefault(ev.doc_id, set()).add(existing_id or r.id)
        if existing_id:
            cur = self.relations[existing_id]
            seen = {(ev.doc_id, ev.quote) for ev in cur.evidence}
            cur.evidence += [ev for ev in r.evidence if (ev.doc_id, ev.quote) not in seen]
            return
        self.relations[r.id] = Relation(r.id, r.type, r.a, r.b, r.start, r.end, r.raw_label,
                                        list(r.evidence))
        self._rel_by_key[key] = r.id
        for node in (r.a, r.b):
            self._rels_of.setdefault(node, set()).add(r.id)

    def drop_document(self, doc_id: str) -> bool:
        with self._lock:
            existed = self.documents.pop(doc_id, None) is not None
            touched = set()
            for mid in self._doc_mentions.pop(doc_id, set()):
                m = self.mentions.pop(mid, None)
                if m is not None:
                    self._mentions_of.get(m.entity_id, set()).discard(mid)
                    touched.add(m.entity_id)
            for eid in self._doc_attr_entities.pop(doc_id, set()):
                e = self.entities.get(eid)
                if e is not None:
                    e.attributes = [a for a in e.attributes if a.doc_id != doc_id]
                    touched.add(eid)
            for rid in self._doc_rels.pop(doc_id, set()):
                r = self.relations.get(rid)
                if r is None:
                    continue
                r.evidence = [ev for ev in r.evidence if ev.doc_id != doc_id]
                touched.update((r.a, r.b))
                if not r.evidence:
                    self._remove_relation(rid)
            for eid in touched:
                e = self.entities.get(eid)
                if e is not None and not self._mentions_of.get(eid) and not e.attributes \
                        and not self._rels_of.get(eid):
                    self.entities.pop(eid)
                    self._mentions_of.pop(eid, None)
                    self._rels_of.pop(eid, None)
            return existed

    def _remove_relation(self, rid: str) -> None:
        r = self.relations.pop(rid)
        self._rel_by_key.pop(_rel_key(r), None)
        for node in (r.a, r.b):
            self._rels_of.get(node, set()).discard(rid)

    def set_acl(self, doc_id: str, acl: dict | None) -> bool:
        with self._lock:
            doc = self.documents.get(doc_id)
            if doc is None:
                return False
            doc.acl = acl
            return True

    def merge_entities(self, keep: str, drop: str) -> None:
        """Fold `drop` into `keep` (entity resolution). The dropped id stays as a
        tombstone pointing to the survivor so the merge can be audited and undone."""
        with self._lock:
            if keep == drop or keep not in self.entities or drop not in self.entities:
                raise StoreError("merge needs two different known entities")
            k, d = self.entities[keep], self.entities[drop]
            if k.type != d.type:
                raise StoreError("cannot merge %s into %s" % (d.type, k.type))
            for mid in self._mentions_of.pop(drop, set()):
                self.mentions[mid].entity_id = keep
                self._mentions_of.setdefault(keep, set()).add(mid)
            seen = {(a.name, a.value, a.doc_id) for a in k.attributes}
            k.attributes += [a for a in d.attributes if (a.name, a.value, a.doc_id) not in seen]
            for a in d.attributes:
                self._doc_attr_entities.setdefault(a.doc_id, set()).add(keep)
            d.attributes = []
            for rid in list(self._rels_of.pop(drop, set())):
                r = self.relations.get(rid)
                if r is None:
                    continue
                self._remove_relation(rid)
                a = keep if r.a == drop else r.a
                b = keep if r.b == drop else r.b
                if a != b:
                    self._add_relation(Relation(r.id, r.type, a, b, r.start, r.end, r.raw_label,
                                                r.evidence))
            d.status, d.merged_into = "merged", keep

    # -- reads -----------------------------------------------------------------

    def view(self, scope, include_weak: bool = False) -> "MemoryView":
        return MemoryView(self, scope, include_weak)

    def counts(self) -> dict:
        with self._lock:
            return {"documents": len(self.documents), "entities": len(self.entities),
                    "mentions": len(self.mentions), "relations": len(self.relations)}


class MemoryView:
    """The part of the graph one caller may see, computed per request."""

    def __init__(self, store: MemoryGraphStore, scope, include_weak: bool):
        self.s = store
        self.include_weak = include_weak
        with store._lock:
            self.docs = {d for d, doc in store.documents.items() if scope.allows(doc)}
        self._node_cache: dict[str, dict | None] = {}

    def node(self, eid: str) -> dict | None:
        if eid in self._node_cache:
            return self._node_cache[eid]
        e = self.s.entities.get(eid)
        out = None
        if e is not None and e.status == "active":
            mentions = [self.s.mentions[m] for m in self.s._mentions_of.get(eid, ())
                        if self.s.mentions[m].doc_id in self.docs]
            attrs = [a for a in e.attributes if a.doc_id in self.docs]
            if mentions or attrs:
                surfaces = Counter(m.text for m in mentions)
                name = [a.value for a in attrs if a.name == "name"]
                label = surfaces.most_common(1)[0][0] if surfaces else (name[0] if name else e.id)
                out = {"id": e.id, "type": e.type, "subtype": e.subtype, "label": label,
                       "aliases": sorted(surfaces), "mentions": len(mentions),
                       "documents": sorted({m.doc_id for m in mentions} | {a.doc_id for a in attrs}),
                       "attributes": [{"name": a.name, "value": a.value, "doc_id": a.doc_id,
                                       "page": a.page} for a in attrs]}
        self._node_cache[eid] = out
        return out

    def _edge(self, r: Relation) -> dict | None:
        evidence = [ev for ev in r.evidence if ev.doc_id in self.docs]
        if not evidence:
            return None
        weak = all(ev.method in WEAK_METHODS for ev in evidence)
        if weak and not self.include_weak:
            return None
        if self.node(r.a) is None or self.node(r.b) is None:
            return None
        return {"id": r.id, "type": r.type, "a": r.a, "b": r.b, "start": r.start, "end": r.end,
                "raw_label": r.raw_label, "weak": weak,
                "confidence": max(ev.confidence for ev in evidence),
                "evidence": [{"doc_id": ev.doc_id, "page": ev.page, "quote": ev.quote,
                              "method": ev.method, "confidence": ev.confidence}
                             for ev in evidence]}

    def edges_of(self, eid: str) -> list[dict]:
        if self.node(eid) is None:
            return []
        out = []
        for rid in sorted(self.s._rels_of.get(eid, ())):
            r = self.s.relations.get(rid)
            if r is not None and (edge := self._edge(r)):
                out.append(edge)
        return out

    def nodes(self, types=None):
        for eid in sorted(self.s.entities):
            n = self.node(eid)
            if n and (types is None or n["type"] in types):
                yield n

    def edges(self):
        for rid in sorted(self.s.relations):
            if edge := self._edge(self.s.relations[rid]):
                yield edge

    def document(self, doc_id: str) -> Document | None:
        return self.s.documents.get(doc_id) if doc_id in self.docs else None


def evidence_from_dict(raw: dict) -> Evidence:
    return Evidence(raw["doc_id"], raw.get("quote", ""), raw.get("page"),
                    raw.get("method", "llm"), float(raw.get("confidence", 1.0)))


def attribute_from_dict(raw: dict) -> AttributeValue:
    return AttributeValue(raw["name"], str(raw["value"]), raw["doc_id"], raw.get("page"))
