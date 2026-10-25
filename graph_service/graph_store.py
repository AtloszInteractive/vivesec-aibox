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
import unicodedata
from collections import Counter

from model import AttributeValue, Document, Entity, Evidence, Mention, Relation, WEAK_METHODS


class StoreError(ValueError):
    pass


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFD", text.casefold())
    return "".join(c for c in folded if unicodedata.category(c) != "Mn").split()


def _rel_key(r: Relation) -> tuple:
    a, b = (r.a, r.b)
    return (r.type, a, b, r.start or "", r.end or "")


class MemoryGraphStore:
    def __init__(self):
        self._lock = threading.RLock()
        self._reset()

    def _reset(self) -> None:
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
        self._by_path: dict[tuple[str, str], str] = {}  # (corpus_id, source_path) -> doc_id
        self._name_words: dict[str, Counter] = {}  # folded mention word -> entity id counts

    def _index_mention(self, m: Mention, sign: int) -> None:
        for word in set(_words(m.text)):
            counts = self._name_words.setdefault(word, Counter())
            counts[m.entity_id] += sign
            if counts[m.entity_id] <= 0:
                del counts[m.entity_id]
                if not counts:
                    del self._name_words[word]

    def entities_with_words(self, words) -> set[str]:
        """Ids of entities one of whose mentions contains any of `words` (folded)."""
        with self._lock:
            out = set()
            for word in words:
                out.update(self._name_words.get(word, ()))
            return out

    def doc_ids_by_path(self, corpus_id: str, path: str, tree: bool = False,
                        keep_exact: bool = False) -> list[str]:
        """Documents at `path` (and, with tree, below it) in one corpus."""
        with self._lock:
            out = []
            if not keep_exact and (corpus_id, path) in self._by_path:
                out.append(self._by_path[(corpus_id, path)])
            if tree:
                prefix = path.rstrip("/") + "/"
                out += [doc_id for (c, p), doc_id in self._by_path.items()
                        if c == corpus_id and p.startswith(prefix)]
            return out

    def load_snapshot(self, documents, entities, mentions, relations) -> None:
        """Replace the whole content (used to mirror a persistent store)."""
        with self._lock:
            self._reset()
            for d in documents:
                self.documents[d.doc_id] = d
                self._by_path[(d.corpus_id, d.source_path)] = d.doc_id
            for e in entities:
                self.entities[e.id] = e
                for a in e.attributes:
                    self._doc_attr_entities.setdefault(a.doc_id, set()).add(e.id)
            for m in mentions:
                self.mentions[m.id] = m
                self._mentions_of.setdefault(m.entity_id, set()).add(m.id)
                self._doc_mentions.setdefault(m.doc_id, set()).add(m.id)
                self._index_mention(m, 1)
            for r in relations:
                self.relations[r.id] = r
                self._rel_by_key[_rel_key(r)] = r.id
                for node in (r.a, r.b):
                    self._rels_of.setdefault(node, set()).add(r.id)
                for ev in r.evidence:
                    self._doc_rels.setdefault(ev.doc_id, set()).add(r.id)

    # -- writes ----------------------------------------------------------------

    def put_document(self, doc: Document, entities: list[Entity], mentions: list[Mention],
                     relations: list[Relation]) -> dict:
        """Replace everything previously derived from this document."""
        with self._lock:
            self.drop_document(doc.doc_id)
            self.documents[doc.doc_id] = doc
            self._by_path[(doc.corpus_id, doc.source_path)] = doc.doc_id
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
                self._index_mention(m, 1)
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
            gone = self.documents.pop(doc_id, None)
            existed = gone is not None
            if gone is not None and self._by_path.get((gone.corpus_id, gone.source_path)) == doc_id:
                del self._by_path[(gone.corpus_id, gone.source_path)]
            touched = set()
            for mid in self._doc_mentions.pop(doc_id, set()):
                m = self.mentions.pop(mid, None)
                if m is not None:
                    self._mentions_of.get(m.entity_id, set()).discard(mid)
                    self._index_mention(m, -1)
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
                self._index_mention(self.mentions[mid], -1)
                self.mentions[mid].entity_id = keep
                self._index_mention(self.mentions[mid], 1)
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

    def revive_entity(self, eid: str) -> None:
        """Turn a merge tombstone back into an active (still empty) entity; undoing
        a merge then re-puts its documents onto it."""
        with self._lock:
            e = self.entities.get(eid)
            if e is None or e.status != "merged":
                raise StoreError("%s is not a merged entity" % eid)
            e.status, e.merged_into = "active", None

    # -- reads -----------------------------------------------------------------

    def view(self, scope, include_weak: bool = False) -> "MemoryView":
        return MemoryView(self, scope, include_weak)

    def counts(self) -> dict:
        with self._lock:
            return {"documents": len(self.documents), "entities": len(self.entities),
                    "mentions": len(self.mentions), "relations": len(self.relations)}


class GraphView:
    """The part of the graph one caller may see, computed per request.

    Backends only fetch raw records (_load_*); every visibility decision is
    made here, so the memory and the Neo4j store cannot drift apart."""

    def __init__(self, scope, include_weak: bool):
        self.scope = scope
        self.include_weak = include_weak
        self._doc_ok: dict[str, bool] = {}
        self._docs: dict[str, Document] = {}
        self._node_cache: dict[str, dict | None] = {}
        self._rel_cache: dict[str, list[Relation]] = {}

    # backend hooks ---------------------------------------------------------
    def _load_documents(self, doc_ids: list[str]) -> dict[str, Document]:
        raise NotImplementedError

    def _load_nodes(self, eids: list[str]) -> dict[str, tuple[Entity, list[Mention]]]:
        raise NotImplementedError

    def _load_relations_of(self, eid: str) -> list[Relation]:
        raise NotImplementedError

    def _load_relations_of_many(self, eids: list[str]) -> dict[str, list[Relation]]:
        return {eid: self._load_relations_of(eid) for eid in eids}

    def _iter_entity_ids(self, types=None):
        raise NotImplementedError

    def _iter_relations(self):
        raise NotImplementedError

    # shared logic ----------------------------------------------------------
    def _check_docs(self, doc_ids) -> None:
        missing = sorted({d for d in doc_ids if d not in self._doc_ok})
        if not missing:
            return
        found = self._load_documents(missing)
        for d in missing:
            doc = found.get(d)
            self._doc_ok[d] = doc is not None and self.scope.allows(doc)
            if self._doc_ok[d]:
                self._docs[d] = doc

    def prefetch(self, eids) -> None:
        missing = sorted({e for e in eids if e not in self._node_cache})
        if not missing:
            return
        loaded = self._load_nodes(missing)
        self._check_docs({m.doc_id for _, ms in loaded.values() for m in ms}
                         | {a.doc_id for e, _ in loaded.values() for a in e.attributes})
        for eid in missing:
            self._node_cache[eid] = self._build_node(*loaded[eid]) if eid in loaded else None

    def _build_node(self, e: Entity, mentions: list[Mention]) -> dict | None:
        if e.status != "active":
            return None
        mentions = [m for m in mentions if self._doc_ok.get(m.doc_id)]
        attrs = [a for a in e.attributes if self._doc_ok.get(a.doc_id)]
        if not mentions and not attrs:
            return None
        surfaces = Counter(m.text for m in mentions)
        name = [a.value for a in attrs if a.name == "name"]
        label = surfaces.most_common(1)[0][0] if surfaces else (name[0] if name else e.id)
        within = Counter(a.value for a in attrs if a.name == "within")
        if within:
            # "102. számú zárka (Kőhegyi Intézet)": the container, as a visible
            # document names it, tells apart places that share a name.
            label = "%s (%s)" % (label, within.most_common(1)[0][0])
        elif "@doc:" in e.id:
            # A document-local place or thing ("101. számú zárka" of a list that
            # names no institution): its document tells it apart.
            doc = self._docs.get(e.id.rsplit("@doc:", 1)[1])
            if doc is not None:
                label = "%s (%s)" % (label, doc.title or doc.source_path.rsplit("/", 1)[-1])
        return {"id": e.id, "type": e.type, "subtype": e.subtype, "label": label,
                "aliases": sorted(surfaces), "mentions": len(mentions),
                "documents": sorted({m.doc_id for m in mentions} | {a.doc_id for a in attrs}),
                "attributes": [{"name": a.name, "value": a.value, "doc_id": a.doc_id,
                                "page": a.page} for a in attrs]}

    def node(self, eid: str) -> dict | None:
        if eid not in self._node_cache:
            self.prefetch([eid])
        return self._node_cache[eid]

    def name_candidates(self, words) -> set[str] | None:
        """Entity ids that may carry one of these name words; None: unknown, scan all."""
        return None

    def _edge(self, r: Relation) -> dict | None:
        evidence = [ev for ev in r.evidence if self._doc_ok.get(ev.doc_id)]
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

    def _edges(self, rels: list[Relation]) -> list[dict]:
        self._check_docs({ev.doc_id for r in rels for ev in r.evidence})
        self.prefetch({x for r in rels for x in (r.a, r.b)})
        return [e for r in sorted(rels, key=lambda r: r.id) if (e := self._edge(r))]

    def edges_of(self, eid: str) -> list[dict]:
        if self.node(eid) is None:
            return []
        self.prefetch_edges([eid])
        return self._edges(self._rel_cache[eid])

    def prefetch_edges(self, eids) -> None:
        """Load the relations of many entities at once (one round trip per batch)."""
        missing = sorted({e for e in eids if e not in self._rel_cache})
        if missing:
            loaded = self._load_relations_of_many(missing)
            for eid in missing:
                self._rel_cache[eid] = loaded.get(eid, [])
            rels = [r for eid in missing for r in self._rel_cache[eid]]
            self._check_docs({ev.doc_id for r in rels for ev in r.evidence})
            self.prefetch({x for r in rels for x in (r.a, r.b)})

    def nodes(self, types=None):
        batch = []
        for eid in self._iter_entity_ids(types):
            batch.append(eid)
            if len(batch) >= 1000:
                yield from self._emit(batch, types)
                batch = []
        yield from self._emit(batch, types)

    def _emit(self, batch, types):
        self.prefetch(batch)
        self.prefetch_edges([e for e in batch if self._node_cache[e]])
        for eid in batch:
            n = self._node_cache[eid]
            if n and (types is None or n["type"] in types):
                yield n

    def edges(self):
        batch = []
        for r in self._iter_relations():
            batch.append(r)
            if len(batch) >= 1000:
                yield from self._edges(batch)
                batch = []
        yield from self._edges(batch)

    def document(self, doc_id: str) -> Document | None:
        self._check_docs([doc_id])
        return self._docs.get(doc_id)


class MemoryView(GraphView):
    def __init__(self, store: MemoryGraphStore, scope, include_weak: bool):
        super().__init__(scope, include_weak)
        self.s = store

    def name_candidates(self, words) -> set[str] | None:
        return self.s.entities_with_words(words)

    def _load_documents(self, doc_ids):
        return {d: self.s.documents[d] for d in doc_ids if d in self.s.documents}

    def _load_nodes(self, eids):
        out = {}
        for eid in eids:
            e = self.s.entities.get(eid)
            if e is not None:
                out[eid] = (e, [self.s.mentions[m] for m in self.s._mentions_of.get(eid, ())])
        return out

    def _load_relations_of(self, eid):
        return [self.s.relations[r] for r in self.s._rels_of.get(eid, ()) if r in self.s.relations]

    def _iter_entity_ids(self, types=None):
        return [e.id for e in sorted(self.s.entities.values(), key=lambda e: e.id)
                if types is None or e.type in types]

    def _iter_relations(self):
        return [self.s.relations[r] for r in sorted(self.s.relations)]


def evidence_from_dict(raw: dict) -> Evidence:
    return Evidence(raw["doc_id"], raw.get("quote", ""), raw.get("page"),
                    raw.get("method", "llm"), float(raw.get("confidence", 1.0)))


def attribute_from_dict(raw: dict) -> AttributeValue:
    return AttributeValue(raw["name"], str(raw["value"]), raw["doc_id"], raw.get("page"))
