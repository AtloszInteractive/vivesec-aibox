"""Neo4j-backed graph store (E26): persistent, same behaviour as the memory store.

Layout (Neo4j Community 5):
  (:Document {doc_id, corpus_id, source_path, title, kind, pages, acl_json,
              rel_ids, attr_entities})
  (:Entity {id, type, subtype, label, status, merged_into})
  (:Mention {id, doc_id, text, page, start, end, method, confidence})-[:OF]->(:Entity)
  (:Attr {doc_id, name, value, page})-[:OF]->(:Entity)
  (:Entity)-[:REL {id, key, type, start, end, raw_label, doc_ids, ev}]->(:Entity)

Evidence lives on the relationship as JSON (`ev`) with `doc_ids` beside it;
each document lists the relationships it supports (`rel_ids`), so dropping a
document touches only its own data. Visibility is decided in GraphView, never
in Cypher, so both stores apply the identical rule.
"""
from __future__ import annotations

import json

from graph_store import GraphView, StoreError, _rel_key
from model import AttributeValue, Document, Entity, Evidence, Mention, Relation

SCHEMA = [
    "CREATE CONSTRAINT graph_doc_id IF NOT EXISTS FOR (d:Document) REQUIRE d.doc_id IS UNIQUE",
    "CREATE CONSTRAINT graph_entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT graph_mention_id IF NOT EXISTS FOR (m:Mention) REQUIRE m.id IS UNIQUE",
    "CREATE INDEX graph_mention_doc IF NOT EXISTS FOR (m:Mention) ON (m.doc_id)",
    "CREATE INDEX graph_attr_doc IF NOT EXISTS FOR (a:Attr) ON (a.doc_id)",
    "CREATE INDEX graph_doc_path IF NOT EXISTS FOR (d:Document) ON (d.corpus_id, d.source_path)",
    "CREATE INDEX graph_entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    "CREATE INDEX graph_rel_id IF NOT EXISTS FOR ()-[r:REL]-() ON (r.id)",
    "CREATE INDEX graph_rel_key IF NOT EXISTS FOR ()-[r:REL]-() ON (r.key)",
]


def _key_str(r: Relation) -> str:
    return "\x1f".join(_rel_key(r))


def _ev_json(evidence: list[Evidence]) -> str:
    return json.dumps([{"doc_id": e.doc_id, "quote": e.quote, "page": e.page,
                        "method": e.method, "confidence": e.confidence} for e in evidence],
                      ensure_ascii=False)


def _ev_load(raw: str) -> list[Evidence]:
    return [Evidence(x["doc_id"], x["quote"], x["page"], x["method"], x["confidence"])
            for x in json.loads(raw or "[]")]


def _relation(props: dict, a: str, b: str) -> Relation:
    return Relation(props["id"], props["type"], a, b, props.get("start"), props.get("end"),
                    props.get("raw_label") or "", _ev_load(props.get("ev")))


class Neo4jGraphStore:
    def __init__(self, uri: str, user: str, password: str, database: str = "neo4j"):
        from neo4j import GraphDatabase  # optional dependency, only for GRAPH_STORE=neo4j

        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.database = database
        with self.driver.session(database=database) as s:
            for stmt in SCHEMA:
                s.run(stmt)

    def close(self) -> None:
        self.driver.close()

    def _session(self):
        return self.driver.session(database=self.database)

    def wipe(self) -> None:
        """Delete every graph node (tests and factory reset only)."""
        with self._session() as s:
            s.run("MATCH (n) WHERE n:Document OR n:Entity OR n:Mention OR n:Attr "
                  "CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS")

    # -- writes ----------------------------------------------------------------

    def put_document(self, doc: Document, entities: list[Entity], mentions: list[Mention],
                     relations: list[Relation]) -> dict:
        with self._session() as s:
            s.execute_write(self._put_tx, doc, entities, mentions, relations)
        return {"entities": len(entities), "mentions": len(mentions), "relations": len(relations)}

    def _put_tx(self, tx, doc, entities, mentions, relations):
        self._drop_tx(tx, doc.doc_id)
        ids = [e.id for e in entities]
        referenced = sorted(set(ids) | {m.entity_id for m in mentions}
                            | {x for r in relations for x in (r.a, r.b)})
        existing = {r["id"]: r["type"] for r in tx.run(
            "MATCH (e:Entity) WHERE e.id IN $ids RETURN e.id AS id, e.type AS type",
            ids=referenced)}
        for e in entities:
            if e.id in existing and existing[e.id] != e.type:
                raise StoreError("entity %s already exists with type %s" % (e.id, existing[e.id]))
        tx.run("UNWIND $rows AS row MERGE (e:Entity {id: row.id}) "
               "ON CREATE SET e.type = row.type, e.subtype = row.subtype, e.label = row.label, "
               "e.status = 'active' "
               "ON MATCH SET e.subtype = CASE WHEN coalesce(e.subtype, '') = '' "
               "THEN row.subtype ELSE e.subtype END",
               rows=[{"id": e.id, "type": e.type, "subtype": e.subtype, "label": e.label}
                     for e in entities])
        attrs = [{"eid": e.id, "doc_id": a.doc_id, "name": a.name, "value": a.value,
                  "page": a.page} for e in entities for a in e.attributes]
        tx.run("UNWIND $rows AS row MATCH (e:Entity {id: row.eid}) "
               "CREATE (:Attr {doc_id: row.doc_id, name: row.name, value: row.value, "
               "page: row.page})-[:OF]->(e)", rows=attrs)
        known = set(ids) | set(existing)
        for m in mentions:
            if m.doc_id != doc.doc_id or m.entity_id not in known:
                raise StoreError("mention %s must belong to this document and a known entity"
                                 % m.id)
        taken = [r["id"] for r in tx.run("MATCH (m:Mention) WHERE m.id IN $ids RETURN m.id AS id",
                                         ids=[m.id for m in mentions])]
        if taken:
            raise StoreError("mention id %s is already used" % taken[0])
        tx.run("UNWIND $rows AS row MATCH (e:Entity {id: row.eid}) "
               "CREATE (:Mention {id: row.id, doc_id: row.doc_id, text: row.text, page: row.page, "
               "`start`: row.`start`, `end`: row.`end`, method: row.method, "
               "confidence: row.confidence})-[:OF]->(e)",
               rows=[{"id": m.id, "eid": m.entity_id, "doc_id": m.doc_id, "text": m.text,
                      "page": m.page, "start": m.start, "end": m.end, "method": m.method,
                      "confidence": m.confidence} for m in mentions])
        for r in relations:
            if r.a not in known or r.b not in known:
                raise StoreError("relation %s connects unknown entities" % r.id)
            if not r.evidence or any(ev.doc_id != doc.doc_id for ev in r.evidence):
                raise StoreError("relation %s needs evidence from this document" % r.id)
        rel_ids = self._add_relations_tx(tx, relations)
        tx.run("CREATE (d:Document {doc_id: $doc_id, corpus_id: $corpus_id, "
               "source_path: $source_path, title: $title, kind: $kind, pages: $pages, "
               "profile: $profile, acl_json: $acl, rel_ids: $rel_ids, attr_entities: $attr_entities})",
               doc_id=doc.doc_id, corpus_id=doc.corpus_id, source_path=doc.source_path,
               title=doc.title, kind=doc.kind, pages=doc.pages, profile=doc.profile,
               acl=json.dumps(doc.acl) if doc.acl is not None else None,
               rel_ids=sorted(set(rel_ids.values())),
               attr_entities=sorted({a["eid"] for a in attrs}))

    def _add_relations_tx(self, tx, relations: list[Relation]) -> dict[str, str]:
        """Create or extend relationships; returns key -> id now carrying the evidence."""
        if not relations:
            return {}
        keys = {_key_str(r): r for r in relations}
        found = {row["key"]: row for row in tx.run(
            "MATCH ()-[r:REL]->() WHERE r.key IN $keys RETURN r.key AS key, r.id AS id, "
            "r.ev AS ev", keys=list(keys))}
        by_id = {row["id"]: row["key"] for row in tx.run(
            "MATCH ()-[r:REL]->() WHERE r.id IN $ids RETURN r.id AS id, r.key AS key",
            ids=[r.id for r in relations])}
        out, updates, creates = {}, [], []
        merged: dict[str, list[Evidence]] = {}
        for key, r in keys.items():
            if r.id in by_id and by_id[r.id] != key:
                raise StoreError("relation id %s is already used" % r.id)
            if key in found:
                current = merged.get(key) or _ev_load(found[key]["ev"])
                seen = {(e.doc_id, e.quote) for e in current}
                current += [e for e in r.evidence if (e.doc_id, e.quote) not in seen]
                merged[key] = current
                out[key] = found[key]["id"]
            else:
                creates.append({"a": r.a, "b": r.b, "id": r.id, "key": key, "type": r.type,
                                "start": r.start, "end": r.end, "raw_label": r.raw_label,
                                "ev": _ev_json(r.evidence),
                                "doc_ids": sorted({e.doc_id for e in r.evidence})})
                out[key] = r.id
        for key, evidence in merged.items():
            updates.append({"key": key, "ev": _ev_json(evidence),
                            "doc_ids": sorted({e.doc_id for e in evidence})})
        tx.run("UNWIND $rows AS row MATCH ()-[r:REL {key: row.key}]->() "
               "SET r.ev = row.ev, r.doc_ids = row.doc_ids", rows=updates)
        tx.run("UNWIND $rows AS row MATCH (a:Entity {id: row.a}), (b:Entity {id: row.b}) "
               "CREATE (a)-[:REL {id: row.id, key: row.key, type: row.type, `start`: row.`start`, "
               "`end`: row.`end`, raw_label: row.raw_label, ev: row.ev, doc_ids: row.doc_ids}]->(b)",
               rows=creates)
        return out

    def drop_document(self, doc_id: str) -> bool:
        with self._session() as s:
            return s.execute_write(self._drop_tx, doc_id)

    def _drop_tx(self, tx, doc_id: str) -> bool:
        row = tx.run("MATCH (d:Document {doc_id: $id}) RETURN d.rel_ids AS rel_ids, "
                     "d.attr_entities AS attr_entities", id=doc_id).single()
        touched = set()
        touched |= {r["eid"] for r in tx.run(
            "MATCH (m:Mention {doc_id: $id})-[:OF]->(e:Entity) WITH m, e.id AS eid "
            "DETACH DELETE m RETURN DISTINCT eid", id=doc_id)}
        touched |= {r["eid"] for r in tx.run(
            "MATCH (a:Attr {doc_id: $id})-[:OF]->(e:Entity) WITH a, e.id AS eid "
            "DETACH DELETE a RETURN DISTINCT eid", id=doc_id)}
        if row is None:
            return False
        rels = list(tx.run("MATCH (a)-[r:REL]->(b) WHERE r.id IN $ids "
                           "RETURN r.id AS id, r.ev AS ev, a.id AS a, b.id AS b",
                           ids=row["rel_ids"] or []))
        updates, deletes = [], []
        for r in rels:
            evidence = [e for e in _ev_load(r["ev"]) if e.doc_id != doc_id]
            touched.update((r["a"], r["b"]))
            if evidence:
                updates.append({"id": r["id"], "ev": _ev_json(evidence),
                                "doc_ids": sorted({e.doc_id for e in evidence})})
            else:
                deletes.append(r["id"])
        tx.run("UNWIND $rows AS row MATCH ()-[r:REL {id: row.id}]->() "
               "SET r.ev = row.ev, r.doc_ids = row.doc_ids", rows=updates)
        tx.run("MATCH ()-[r:REL]->() WHERE r.id IN $ids DELETE r", ids=deletes)
        tx.run("MATCH (d:Document {doc_id: $id}) DELETE d", id=doc_id)
        tx.run("MATCH (e:Entity) WHERE e.id IN $ids "
               "AND NOT EXISTS { (:Mention)-[:OF]->(e) } AND NOT EXISTS { (:Attr)-[:OF]->(e) } "
               "AND NOT EXISTS { (e)-[:REL]-() } DELETE e", ids=sorted(touched))
        return True

    def set_acl(self, doc_id: str, acl: dict | None) -> bool:
        with self._session() as s:
            row = s.run("MATCH (d:Document {doc_id: $id}) SET d.acl_json = $acl RETURN d.doc_id",
                        id=doc_id, acl=json.dumps(acl) if acl is not None else None).single()
        return row is not None

    def merge_entities(self, keep: str, drop: str) -> None:
        with self._session() as s:
            s.execute_write(self._merge_tx, keep, drop)

    def _merge_tx(self, tx, keep, drop):
        rows = {r["id"]: r for r in tx.run(
            "MATCH (e:Entity) WHERE e.id IN [$k, $d] RETURN e.id AS id, e.type AS type",
            k=keep, d=drop)}
        if keep == drop or keep not in rows or drop not in rows:
            raise StoreError("merge needs two different known entities")
        if rows[keep]["type"] != rows[drop]["type"]:
            raise StoreError("cannot merge %s into %s" % (rows[drop]["type"], rows[keep]["type"]))
        for label in ("Mention", "Attr"):
            tx.run("MATCH (x:%s)-[o:OF]->(:Entity {id: $d}) MATCH (k:Entity {id: $k}) "
                   "CREATE (x)-[:OF]->(k) DELETE o" % label, k=keep, d=drop)
        tx.run("MATCH (doc:Document) WHERE $d IN doc.attr_entities "
               "SET doc.attr_entities = [x IN doc.attr_entities WHERE x <> $d] + "
               "CASE WHEN $k IN doc.attr_entities THEN [] ELSE [$k] END", k=keep, d=drop)
        old = list(tx.run("MATCH (a)-[r:REL]-(b) WHERE (a.id = $d OR b.id = $d) "
                          "AND startNode(r) = a RETURN DISTINCT r {.*} AS r, a.id AS a, b.id AS b",
                          d=drop))
        tx.run("MATCH (:Entity {id: $d})-[r:REL]-() DELETE r", d=drop)
        moved = []
        for row in old:
            r = _relation(row["r"], row["a"], row["b"])
            r.a = keep if r.a == drop else r.a
            r.b = keep if r.b == drop else r.b
            if r.a != r.b:
                moved.append(r)
        ids = self._add_relations_tx(tx, moved)
        for r in moved:
            new_id = ids[_key_str(r)]
            # The old id may stay listed; dropping a document skips ids that no longer exist.
            tx.run("MATCH (doc:Document) WHERE doc.doc_id IN $docs AND NOT $new IN doc.rel_ids "
                   "SET doc.rel_ids = doc.rel_ids + [$new]",
                   docs=sorted({e.doc_id for e in r.evidence}), new=new_id)
        tx.run("MATCH (e:Entity {id: $d}) SET e.status = 'merged', e.merged_into = $k",
               d=drop, k=keep)

    def revive_entity(self, eid: str) -> None:
        with self._session() as s:
            row = s.run("MATCH (e:Entity {id: $id}) WHERE e.status = 'merged' "
                        "SET e.status = 'active', e.merged_into = null RETURN e.id AS id",
                        id=eid).single()
        if row is None:
            raise StoreError("%s is not a merged entity" % eid)

    # -- reads -----------------------------------------------------------------

    def export(self):
        """Everything as model objects: (documents, entities, mentions, relations)."""
        with self._session() as s:
            documents = [Document(d["doc_id"], d["corpus_id"], d["source_path"],
                                  d.get("title") or "",
                                  json.loads(d["acl_json"]) if d.get("acl_json") else None,
                                  d.get("kind") or "", d.get("pages") or 0,
                                  d.get("profile") or "")
                         for d in (r["d"] for r in s.run("MATCH (d:Document) RETURN d {.*} AS d"))]
            entities = {}
            for r in s.run("MATCH (e:Entity) RETURN e {.*} AS e"):
                e = r["e"]
                entities[e["id"]] = Entity(e["id"], e["type"], e.get("subtype") or "",
                                           e.get("label") or "", [], e.get("status") or "active",
                                           e.get("merged_into"))
            for r in s.run("MATCH (a:Attr)-[:OF]->(e:Entity) RETURN a {.*} AS a, e.id AS eid"):
                a = r["a"]
                entities[r["eid"]].attributes.append(
                    AttributeValue(a["name"], a["value"], a["doc_id"], a.get("page")))
            mentions = [Mention(m["id"], m["doc_id"], r["eid"], m["text"], m.get("page"),
                                m.get("start"), m.get("end"), m.get("method") or "ner",
                                m.get("confidence") if m.get("confidence") is not None else 1.0)
                        for r in s.run("MATCH (m:Mention)-[:OF]->(e:Entity) "
                                       "RETURN m {.*} AS m, e.id AS eid")
                        for m in [r["m"]]]
            relations = [_relation(r["r"], r["a"], r["b"]) for r in s.run(
                "MATCH (a:Entity)-[r:REL]->(b:Entity) RETURN r {.*} AS r, a.id AS a, b.id AS b")]
        return documents, list(entities.values()), mentions, relations

    def view(self, scope, include_weak: bool = False) -> "Neo4jView":
        return Neo4jView(self, scope, include_weak)

    def doc_ids_by_path(self, corpus_id: str, path: str, tree: bool = False,
                        keep_exact: bool = False) -> list[str]:
        with self._session() as s:
            return [r["id"] for r in s.run(
                "MATCH (d:Document {corpus_id: $c}) "
                "WHERE (NOT $keep AND d.source_path = $p) OR ($tree AND d.source_path "
                "STARTS WITH $prefix) RETURN d.doc_id AS id",
                c=corpus_id, p=path, keep=keep_exact, tree=tree,
                prefix=path.rstrip("/") + "/")]

    def counts(self) -> dict:
        with self._session() as s:
            row = s.run("CALL () { MATCH (d:Document) RETURN count(d) AS documents } "
                        "CALL () { MATCH (e:Entity) RETURN count(e) AS entities } "
                        "CALL () { MATCH (m:Mention) RETURN count(m) AS mentions } "
                        "CALL () { MATCH ()-[r:REL]->() RETURN count(r) AS relations } "
                        "RETURN documents, entities, mentions, relations").single()
        return dict(row)


class CachedNeo4jStore:
    """Neo4j persists, an in-memory mirror answers queries.

    Every write goes to Neo4j first (which validates and commits atomically) and
    is then replayed on the mirror with the same memory-store code; any mismatch
    reloads the mirror from Neo4j, so a read never sees uncommitted data."""

    def __init__(self, persistent: Neo4jGraphStore):
        from graph_store import MemoryGraphStore

        self.db = persistent
        self.mirror = MemoryGraphStore()
        self.reload()

    def reload(self) -> None:
        self.mirror.load_snapshot(*self.db.export())

    def _replay(self, method, *args):
        try:
            return getattr(self.mirror, method)(*args)
        except StoreError:
            self.reload()

    def put_document(self, doc, entities, mentions, relations) -> dict:
        out = self.db.put_document(doc, entities, mentions, relations)
        self._replay("put_document", doc, entities, mentions, relations)
        return out

    def drop_document(self, doc_id: str) -> bool:
        out = self.db.drop_document(doc_id)
        self._replay("drop_document", doc_id)
        return out

    def set_acl(self, doc_id: str, acl) -> bool:
        out = self.db.set_acl(doc_id, acl)
        self._replay("set_acl", doc_id, acl)
        return out

    def merge_entities(self, keep: str, drop: str) -> None:
        self.db.merge_entities(keep, drop)
        self._replay("merge_entities", keep, drop)

    def revive_entity(self, eid: str) -> None:
        self.db.revive_entity(eid)
        self._replay("revive_entity", eid)

    def view(self, scope, include_weak: bool = False):
        return self.mirror.view(scope, include_weak)

    def doc_ids_by_path(self, corpus_id: str, path: str, tree: bool = False,
                        keep_exact: bool = False) -> list[str]:
        return self.mirror.doc_ids_by_path(corpus_id, path, tree, keep_exact)

    def counts(self) -> dict:
        return self.mirror.counts()

    def close(self) -> None:
        self.db.close()


class Neo4jView(GraphView):
    def __init__(self, store: Neo4jGraphStore, scope, include_weak: bool):
        super().__init__(scope, include_weak)
        self.s = store

    def _run(self, query, **params):
        with self.s._session() as s:
            return list(s.run(query, **params))

    def _load_documents(self, doc_ids):
        out = {}
        for r in self._run("MATCH (d:Document) WHERE d.doc_id IN $ids RETURN d {.*} AS d",
                           ids=list(doc_ids)):
            d = r["d"]
            out[d["doc_id"]] = Document(d["doc_id"], d["corpus_id"], d["source_path"],
                                        d.get("title") or "",
                                        json.loads(d["acl_json"]) if d.get("acl_json") else None,
                                        d.get("kind") or "", d.get("pages") or 0,
                                        d.get("profile") or "")
        return out

    def _load_nodes(self, eids):
        out = {}
        rows = self._run(
            "MATCH (e:Entity) WHERE e.id IN $ids "
            "RETURN e {.*} AS e, "
            "COLLECT { MATCH (m:Mention)-[:OF]->(e) RETURN m {.*} } AS ms, "
            "COLLECT { MATCH (a:Attr)-[:OF]->(e) RETURN a {.*} } AS attrs", ids=list(eids))
        for r in rows:
            e = r["e"]
            entity = Entity(e["id"], e["type"], e.get("subtype") or "", e.get("label") or "",
                            [AttributeValue(a["name"], a["value"], a["doc_id"], a.get("page"))
                             for a in r["attrs"]],
                            e.get("status") or "active", e.get("merged_into"))
            mentions = [Mention(m["id"], m["doc_id"], e["id"], m["text"], m.get("page"),
                                m.get("start"), m.get("end"), m.get("method") or "ner",
                                m.get("confidence") if m.get("confidence") is not None else 1.0)
                        for m in r["ms"]]
            out[e["id"]] = (entity, mentions)
        return out

    def _load_relations_of(self, eid):
        return self._load_relations_of_many([eid]).get(eid, [])

    def _load_relations_of_many(self, eids):
        rows = self._run("MATCH (e:Entity)-[r:REL]-() WHERE e.id IN $ids "
                         "RETURN e.id AS eid, r {.*} AS r, startNode(r).id AS a, "
                         "endNode(r).id AS b", ids=list(eids))
        out: dict[str, list[Relation]] = {}
        for row in rows:
            out.setdefault(row["eid"], []).append(_relation(row["r"], row["a"], row["b"]))
        return out

    def _iter_entity_ids(self, types=None):
        rows = self._run("MATCH (e:Entity) WHERE $types IS NULL OR e.type IN $types "
                         "RETURN e.id AS id ORDER BY id",
                         types=sorted(types) if types is not None else None)
        return [r["id"] for r in rows]

    def _iter_relations(self):
        rows = self._run("MATCH (a:Entity)-[r:REL]->(b:Entity) "
                         "RETURN r {.*} AS r, a.id AS a, b.id AS b ORDER BY r.id")
        return [_relation(row["r"], row["a"], row["b"]) for row in rows]
