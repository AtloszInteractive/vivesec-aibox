"""Background graph ingestion: files in, extracted graph out, progress visible (E26).

The adapter hands over a document's bytes and metadata; one worker thread
extracts it (pipeline.extract_document) and writes the result to the store.
One worker on purpose: the extraction model shares the GPU with chat and
embedding, so documents are processed one at a time and the queue is bounded
(a full queue answers 429, the caller retries later). Queued bytes stay in
memory only; no cleartext copy is written to disk. When many documents wait
(GRAPH_DEFER_PROSE_QUEUE), the next ones get rules, tables and forms only and a
"/partial" profile tag; reprocessing fetches them again for the running text.
The same file sent again (same content, current profile) is not extracted again.

Possible same-person pairs the resolver did not dare to merge are kept as a
review list; an administrator decides merge (store.merge_entities) or keep apart.
A merge records what the folded person brought (its mentions, attributes and
relationship evidence, per document), so it can be undone: the person is revived
and those documents are written back onto it. Undoing either decision reopens
the pair.
"""
from __future__ import annotations

import collections
import dataclasses
import hashlib
import io
import json
import os
import threading
import time

from extraction import fold
from ingest import IngestError, parse_document_payload
from graph_store import StoreError
from llm_client import LLMError
from model import Entity, Evidence, Relation
from domain_profile import profile_tag  # noqa: F401 (re-exported)
from pipeline import extract_document
from resolution import Resolver
from table_mapping import TableMapper
from visibility import parse_doc_acl

RECENT = 200


def _defer_queue() -> int:
    try:
        return max(0, int(os.environ.get("GRAPH_DEFER_PROSE_QUEUE", "50") or 0))
    except ValueError:
        return 50


# Documents waiting above which the next ones are read without the prose model
# (0: never). See IngestWorker._process.
DEFER_PROSE_QUEUE = _defer_queue()
# Appended to the profile tag of a document whose running text was not (fully)
# read by the model: it stays stale, so reprocessing completes it.
PARTIAL_SUFFIX = "/partial"


class QueueFull(Exception):
    pass


class IngestWorker:
    def __init__(self, state, llm, max_queue_bytes: int = 512 * 1024 * 1024,
                 review_path: str = ""):
        self.state = state  # service State: .store, .profile
        if not hasattr(getattr(state.store, "mirror", state.store), "entities"):
            # Resolution reads every known person; without the in-memory mirror
            # (GRAPH_READ_CACHE=off) that would be one Neo4j scan per document.
            raise ValueError("graph ingestion needs the memory store or the read cache")
        self.llm = llm
        self.max_queue_bytes = max_queue_bytes
        self.cond = threading.Condition()
        self.queue: collections.deque = collections.deque()
        self.queued_bytes = 0
        self.running: dict | None = None
        self.recent: collections.deque = collections.deque(maxlen=RECENT)
        self.totals = collections.Counter()
        self.decided: set[tuple[str, str]] = set()
        # pair -> its "decided" event, oldest first (undone ones removed).
        self.decisions: dict[tuple[str, str], dict] = {}
        self.review_path = review_path
        self.review_lock = threading.Lock()
        self._saved = 0
        self.profile_version = None
        self.resolver = None
        self._reset_models()
        self._load_review()
        self.thread = threading.Thread(target=self._loop, name="graph-ingest", daemon=True)
        self.thread.start()

    def _reset_models(self) -> None:
        profile = self.state.profile
        self.profile_version = (profile["id"], profile["version"])
        review = self.resolver.review if self.resolver else []
        self.resolver = Resolver(profile)
        self.resolver.review = review
        self.resolver.load_from_store(getattr(self.state.store, "mirror", self.state.store))
        self.tables = TableMapper(profile, self.llm, profile.get("table_mappings") or {})

    def _load_review(self) -> None:
        if not self.review_path or not os.path.exists(self.review_path):
            return
        with io.open(self.review_path, encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # a torn last line after a crash
                if event.get("op") == "candidate":
                    self.resolver.review.append(event["item"])
                elif event.get("op") == "decided":
                    pair = (event["a"], event["b"])
                    self.decided.add(pair)
                    self.decisions.pop(pair, None)
                    self.decisions[pair] = event
                elif event.get("op") == "undone":
                    pair = (event["a"], event["b"])
                    self.decided.discard(pair)
                    self.decisions.pop(pair, None)
        self._saved = len(self.resolver.review)

    def _save_review(self, events: list[dict]) -> None:
        if not self.review_path or not events:
            return
        with self.review_lock, io.open(self.review_path, "a", encoding="utf-8",
                                       newline="\n") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # -- queue ---------------------------------------------------------------------

    def unchanged(self, meta: dict, raw: bytes) -> bool:
        """True when exactly this file is already in the graph, extracted with the
        current profile (a drive re-sync sends every file again): nothing to
        extract, only an ACL sent with it is applied. Never while another version
        of the document waits or runs -- the last one sent must win."""
        digest = hashlib.sha256(raw).hexdigest()
        key = (meta.get("corpus_id"), meta.get("source_path"))
        store = getattr(self.state.store, "mirror", self.state.store)
        with self.cond:
            busy = [m for m, _, _ in self.queue] + ([self.running] if self.running else [])
            if any(m.get("doc_id") == meta["doc_id"]
                   or (m.get("corpus_id"), m.get("source_path")) == key for m in busy):
                return False
            with store._lock:
                doc = store.documents.get(meta["doc_id"])
            if (doc is None or not doc.content_sha256 or doc.content_sha256 != digest
                    or doc.profile != profile_tag(self.state.profile)
                    or (doc.corpus_id, doc.source_path) != key
                    or set(self.state.store.doc_ids_by_path(*key)) != {doc.doc_id}):
                return False
            # The same ACL a re-extraction would store (none when none was sent).
            acl = parse_doc_acl(meta.get("acl"))
            if acl != doc.acl:
                self.state.store.set_acl(doc.doc_id, acl)
            self.totals["unchanged"] += 1
            self.recent.append({"doc_id": meta["doc_id"], "source_path": key[1],
                                "waited": 0.0, "status": "unchanged"})
            return True

    def submit(self, meta: dict, raw: bytes) -> int:
        with self.cond:
            if self.queue and self.queued_bytes + len(raw) > self.max_queue_bytes:
                raise QueueFull("ingest queue is full")
            # A newer version of a queued document replaces the queued one.
            for item in list(self.queue):
                if item[0]["doc_id"] == meta["doc_id"] or (
                        item[0].get("corpus_id"), item[0].get("source_path")) == (
                        meta.get("corpus_id"), meta.get("source_path")):
                    self.queue.remove(item)
                    self.queued_bytes -= len(item[1])
            self.queue.append((meta, raw, time.time()))
            self.queued_bytes += len(raw)
            self.cond.notify()
            return len(self.queue)

    def forget(self, corpus_id: str, path: str) -> int:
        """Drop queued documents at or below a deleted path."""
        prefix = path.rstrip("/") + "/"
        with self.cond:
            gone = [item for item in self.queue if item[0].get("corpus_id") == corpus_id and (
                item[0].get("source_path") == path
                or str(item[0].get("source_path")).startswith(prefix))]
            for item in gone:
                self.queue.remove(item)
                self.queued_bytes -= len(item[1])
            running = self.running
            if running and running.get("corpus_id") == corpus_id and (
                    running.get("source_path") == path
                    or str(running.get("source_path")).startswith(prefix)):
                running["cancelled"] = True
            return len(gone)

    def update_acl(self, corpus_id: str, path: str, acl) -> int:
        """Apply an ACL change to a queued or running document (it is not stored yet)."""
        hits = 0
        with self.cond:
            for meta, _, _ in self.queue:
                if (meta.get("corpus_id"), meta.get("source_path")) == (corpus_id, path):
                    meta["acl"] = acl
                    hits += 1
            running = self.running
            if running and (running.get("corpus_id"), running.get("source_path")) == (
                    corpus_id, path):
                running["acl"] = acl
                running["acl_changed"] = True
                hits += 1
        return hits

    def status(self) -> dict:
        with self.cond:
            return {"queued": len(self.queue), "queued_bytes": self.queued_bytes,
                    "running": dict(self.running) if self.running else None,
                    "totals": dict(self.totals), "recent": list(self.recent)[-50:],
                    "review_open": len(self.open_review())}

    def wait_idle(self, timeout: float = 30.0) -> bool:
        end = time.time() + timeout
        with self.cond:
            while (self.queue or self.running) and time.time() < end:
                self.cond.wait(0.05)
            return not self.queue and not self.running

    def _loop(self) -> None:
        while True:
            with self.cond:
                while not self.queue:
                    self.cond.wait()
                meta, raw, queued_at = self.queue.popleft()
                self.queued_bytes -= len(raw)
                self.running = {"doc_id": meta["doc_id"], "corpus_id": meta.get("corpus_id"),
                                "source_path": meta.get("source_path"), "started": time.time()}
            record = {"doc_id": meta["doc_id"], "source_path": meta.get("source_path"),
                      "waited": round(time.time() - queued_at, 1)}
            try:
                record.update(self._process(meta, raw))
                self.totals["done"] += 1
            except (IngestError, LLMError, ValueError, KeyError) as e:
                record.update({"status": "failed", "error": "%s: %s" % (type(e).__name__, e)})
                self.totals["failed"] += 1
            except Exception as e:  # noqa: BLE001 - the worker must survive one bad file
                record.update({"status": "failed", "error": type(e).__name__})
                self.totals["failed"] += 1
            with self.cond:
                self.running = None
                self.recent.append(record)
                self.cond.notify_all()

    def _sticky_people(self, meta: dict) -> dict[str, str]:
        """{folded name: person id} of the people this document already names
        (followed through merges). A re-extraction -- a resync, or reprocessing
        after a profile change -- resolves them to the same ids first, so the
        administrator's merge and keep-apart decisions stay attached to them."""
        store = getattr(self.state.store, "mirror", self.state.store)
        out: dict[str, str] = {}
        with store._lock:
            doc_ids = {meta["doc_id"]} | set(store.doc_ids_by_path(
                meta.get("corpus_id") or "", meta.get("source_path") or ""))
            for doc_id in doc_ids:
                for mid in store._doc_mentions.get(doc_id, ()):
                    mention = store.mentions.get(mid)
                    entity = store.entities.get(mention.entity_id) if mention else None
                    seen = set()
                    while entity is not None and entity.status == "merged" \
                            and entity.merged_into and entity.id not in seen:
                        seen.add(entity.id)
                        entity = store.entities.get(entity.merged_into)
                    if entity is not None and entity.type == "person" \
                            and entity.status == "active":
                        out.setdefault(fold(mention.text), entity.id)
        return out

    def _process(self, meta: dict, raw: bytes) -> dict:
        profile = self.state.profile
        if (profile["id"], profile["version"]) != self.profile_version:
            self._reset_models()
        with self.cond:
            waiting = len(self.queue)
        # A long queue (a large sync) is drained by rules, tables and forms first; the
        # running text is read later, when reprocessing fetches the file again --
        # raw files are never kept on disk, nor piled up in memory.
        prose = not (DEFER_PROSE_QUEUE and waiting >= DEFER_PROSE_QUEUE)
        self.resolver.sticky = self._sticky_people(meta)
        try:
            payload, stats = extract_document(raw, meta, profile, self.llm, self.resolver,
                                              self.tables, prose=prose)
        finally:
            self.resolver.sticky = {}
        doc, entities, mentions, relations = parse_document_payload(payload, profile)
        doc.profile = profile_tag(profile)
        incomplete = stats.get("prose_deferred") or (
            stats["text"] in ("text", "ocr") and stats["llm_errors"])
        if incomplete:
            # Stale on purpose: listed for reprocessing until fully read.
            doc.profile += PARTIAL_SUFFIX
        doc.content_sha256 = hashlib.sha256(raw).hexdigest()
        with self.cond:
            if self.running and self.running.get("cancelled"):
                return {"status": "cancelled"}
            if self.running and self.running.get("acl_changed"):
                doc.acl = self.running["acl"]
            # Writing under the queue lock: a delete arriving now waits for this put.
            for old in self.state.store.doc_ids_by_path(doc.corpus_id, doc.source_path):
                if old != doc.doc_id:
                    self.state.store.drop_document(old)
            self.state.store.put_document(doc, entities, mentions, relations)
        new = self.resolver.review[self._saved:]
        self._save_review([{"op": "candidate", "item": item} for item in new])
        self._saved = len(self.resolver.review)
        status = "done" if stats["text"] in ("text", "ocr") and not stats["llm_errors"] else (
            "partial" if stats["text"] in ("text", "ocr") else stats["text"])
        if stats.get("prose_deferred"):
            status = "deferred"
        return {"status": status, "stats": {k: v for k, v in stats.items()
                                            if k != "llm_error_detail"}}

    # -- review ----------------------------------------------------------------------

    def open_review(self, view=None) -> list[dict]:
        """Undecided pairs whose two people still exist (and are visible in `view`)."""
        store = getattr(self.state.store, "mirror", self.state.store)
        out = []
        for item in self.resolver.review:
            pair = (item["a"], item["b"])
            if pair in self.decided:
                continue
            a, b = store.entities.get(item["a"]), store.entities.get(item["b"])
            if not a or not b or a.status != "active" or b.status != "active":
                continue  # merged or gone since
            if view is not None:
                na, nb = view.node(item["a"]), view.node(item["b"])
                if na is None or nb is None:
                    continue
                item = dict(item, a_label=na["label"], b_label=nb["label"])
            out.append(item)
        return out

    def _mirror(self):
        return getattr(self.state.store, "mirror", self.state.store)

    def _survivor(self, eid: str) -> str | None:
        """The active person `eid` was (possibly over several merges) folded into."""
        store, seen = self._mirror(), set()
        entity = store.entities.get(eid)
        while entity is not None and entity.status == "merged" and entity.id not in seen:
            seen.add(entity.id)
            entity = store.entities.get(entity.merged_into or "")
        return entity.id if entity is not None and entity.status == "active" else None

    def decide(self, a: str, b: str, merge: bool, by: str = "") -> None:
        """merge: `a` (the newer person) is folded into `b`; otherwise they stay apart."""
        pair = (a, b)
        with self.cond:  # no document write in between
            store = self._mirror()
            ea, eb = store.entities.get(a), store.entities.get(b)
            if not ea or not eb or ea.status != "active" or eb.status != "active":
                raise StoreError("one of the two people was merged or removed meanwhile")
            event = {"op": "decided", "a": a, "b": b, "merge": merge, "by": by,
                     "at": round(time.time(), 3)}
            if merge:
                event["record"] = _merge_record(store, b, a)
                self.state.store.merge_entities(b, a)
                self.resolver.merge(b, a)
            self.decided.add(pair)
            self.decisions.pop(pair, None)
            self.decisions[pair] = event
            self._save_review([event])

    def undo(self, a: str, b: str, by: str = "") -> dict:
        """Take a decision back; the pair is open for review again. A merge is
        undone by reviving `a` and writing the documents it brought back onto it:
        a document only it was named in moves back whole (also after a resync), a
        document both were named in moves back what the merge record lists. A
        document that reached the merged person later through `a`'s details stays."""
        pair = (a, b)
        out = {"undone": "separate", "documents": 0, "failed": []}
        with self.cond:
            event = self.decisions.get(pair)
            if event is None:
                raise StoreError("no such decision")
            if event.get("merge"):
                out["undone"] = "merge"
                record = event.get("record")
                if not record:
                    raise StoreError("this merge was made before merges could be undone")
                store = self._mirror()
                survivor = self._survivor(b)
                tomb = store.entities.get(a)
                if survivor is None or tomb is None or tomb.status != "merged" \
                        or self._survivor(a) != survivor:
                    raise StoreError("the merged person changed since; undo the later merge first")
                current = _current_docs(store, record)
                protected, later = set(record["keep_docs"]), False
                for other, ev in self.decisions.items():
                    if later and ev.get("merge") and ev.get("record") \
                            and self._survivor(other[1]) == survivor:
                        protected |= set(ev["record"]["keep_docs"]) | _record_docs(ev["record"])
                    later = later or other == pair
                protected = {current.get(d, d) for d in protected}
                docs = {current[d] for d in _record_docs(record) if d in current}
                self.state.store.revive_entity(a)
                try:
                    for doc_id in sorted(docs):
                        try:
                            _reassign(self.state.store, store, doc_id, a, survivor,
                                      doc_id not in protected, record, current)
                            out["documents"] += 1
                        except StoreError as e:
                            out["failed"].append({"doc_id": doc_id, "error": str(e)})
                finally:
                    self.resolver.rebuild(store)
            self.decided.discard(pair)
            self.decisions.pop(pair, None)
            self._save_review([{"op": "undone", "a": a, "b": b, "by": by,
                                "at": round(time.time(), 3)}])
        return out

    def history(self, view) -> list[dict]:
        """Decisions newest first, as far as `view` may see both sides: a merged
        person's name comes only from the documents of it the caller may see."""
        items = {(i["a"], i["b"]): i for i in self.resolver.review}
        out = []
        with self.cond:
            decisions = list(self.decisions.items())
        for pair, event in reversed(decisions):
            item = items.get(pair) or {}
            entry = {"a": pair[0], "b": pair[1], "decision": "merge" if event.get("merge")
                     else "separate", "by": event.get("by") or "", "at": event.get("at"),
                     "score": item.get("score"), "reason": item.get("reason") or ""}
            if event.get("merge"):
                survivor = self._survivor(pair[1])
                node = view.node(survivor) if survivor else None
                record = event.get("record")
                # Both sides as they were: a document of the survivor from before
                # the merge, and the folded person's name, each visible here.
                current = _current_docs(self._mirror(), record) if record else {}

                def seen(doc_id):
                    return doc_id in current and view.document(current[doc_id]) is not None

                if record and not any(seen(d) for d in record["keep_docs"]):
                    continue
                if record:
                    names = collections.Counter(
                        text for doc_id, texts in record["names"].items()
                        if seen(doc_id) for text in texts)
                elif item.get("doc_id") and view.document(item["doc_id"]) is not None:
                    names = collections.Counter(item.get("names") or [])
                else:
                    names = collections.Counter()
                if node is None or not names:
                    continue
                tomb = self._mirror().entities.get(pair[0])
                entry.update(a_label=min(names, key=lambda n: (-names[n], n)),
                             b_label=node["label"],
                             survivor=survivor,
                             undoable=bool(record) and tomb is not None
                             and tomb.status == "merged" and self._survivor(pair[0]) == survivor)
            else:
                na, nb = view.node(pair[0]), view.node(pair[1])
                if na is None or nb is None:
                    continue
                entry.update(a_label=na["label"], b_label=nb["label"], undoable=True)
            out.append(entry)
        return out


def _record_docs(record: dict) -> set[str]:
    return (set(record["names"]) | {a[2] for a in record["attrs"]}
            | {e[5] for e in record["evidence"]}
            | {ev["doc_id"] for r in record["between"] for ev in r[5]})


def _current_docs(store, record: dict) -> dict[str, str]:
    """Recorded document id -> the id its file has now (a changed file comes back
    under a new id at the same path); deleted documents are left out."""
    out = {}
    for doc_id in _record_docs(record) | set(record["keep_docs"]):
        if doc_id in store.documents:
            out[doc_id] = doc_id
        elif doc_id in record.get("paths", {}):
            corpus_id, path = record["paths"][doc_id]
            now = store._by_path.get((corpus_id, path))
            if now:
                out[doc_id] = now
    return out


def _merge_record(store, keep: str, drop: str) -> dict:
    """What `drop` brings into `keep`, so the merge can be undone."""
    with store._lock:
        names: dict[str, list[str]] = {}
        mention_ids = []
        for mid in sorted(store._mentions_of.get(drop, ())):
            m = store.mentions[mid]
            mention_ids.append(mid)
            names.setdefault(m.doc_id, []).append(m.text)
        attrs = [[a.name, a.value, a.doc_id] for a in store.entities[drop].attributes]
        evidence, between = [], []
        for rid in sorted(store._rels_of.get(drop, ())):
            r = store.relations.get(rid)
            if r is None:
                continue
            drop_is_a = r.a == drop
            other = r.b if drop_is_a else r.a
            if other == keep:
                # The merge deletes a relation between the two; keep it whole.
                between.append([r.type, drop_is_a, r.start, r.end, r.raw_label,
                                [dataclasses.asdict(ev) for ev in r.evidence]])
                continue
            evidence += [[r.type, other, drop_is_a, r.start or "", r.end or "", ev.doc_id,
                          ev.quote] for ev in r.evidence]
        keep_entity = store.entities[keep]
        keep_docs = {store.mentions[m].doc_id for m in store._mentions_of.get(keep, ())}
        keep_docs |= {a.doc_id for a in keep_entity.attributes}
        keep_docs |= {ev.doc_id for rid in store._rels_of.get(keep, ())
                      if rid in store.relations for ev in store.relations[rid].evidence}
        record = {"mentions": mention_ids, "names": names, "attrs": attrs,
                  "evidence": evidence, "between": between, "keep_docs": sorted(keep_docs)}
        record["paths"] = {d: [store.documents[d].corpus_id, store.documents[d].source_path]
                           for d in _record_docs(record) | keep_docs if d in store.documents}
        return record


def _moved_id(*parts) -> str:
    return "rel:" + hashlib.sha1("\0".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _reassign(target, store, doc_id: str, drop: str, survivor: str, whole: bool,
              record: dict, current: dict[str, str]) -> None:
    """Write one document again with what belonged to `drop` (all of the
    survivor's part when `whole`, else what the record lists) back on `drop`."""
    mention_ids = set(record["mentions"])
    attrs = {(n, v, current.get(d, d)) for n, v, d in record["attrs"]}
    evidence = {tuple(e[:5]) + (current.get(e[5], e[5]), e[6]) for e in record["evidence"]}
    with store._lock:
        doc = dataclasses.replace(store.documents[doc_id])
        entities: dict[str, Entity] = {}

        def entity(eid: str) -> Entity:
            if eid not in entities:
                e = store.entities[eid]
                entities[eid] = Entity(e.id, e.type, e.subtype, e.label, [])
            return entities[eid]

        mentions = []
        for mid in sorted(store._doc_mentions.get(doc_id, ())):
            m = dataclasses.replace(store.mentions[mid])
            if m.entity_id == survivor and (whole or m.id in mention_ids):
                m.entity_id = drop
            entity(m.entity_id)
            mentions.append(m)
        for eid in sorted(store._doc_attr_entities.get(doc_id, ())):
            if eid not in store.entities:
                continue
            for a in store.entities[eid].attributes:
                if a.doc_id != doc_id:
                    continue
                back = eid == survivor and (whole or (a.name, a.value, a.doc_id) in attrs)
                entity(drop if back else eid).attributes.append(dataclasses.replace(a))
        relations = []
        for rid in sorted(store._doc_rels.get(doc_id, ())):
            r = store.relations.get(rid)
            if r is None:
                continue
            own = [dataclasses.replace(ev) for ev in r.evidence if ev.doc_id == doc_id]
            moved = []
            if survivor in (r.a, r.b) and r.a != r.b:
                s_is_a = r.a == survivor
                other = r.b if s_is_a else r.a
                moved = [ev for ev in own if whole or (r.type, other, s_is_a, r.start or "",
                                                       r.end or "", ev.doc_id, ev.quote)
                         in evidence]
                if moved:
                    a, b = (drop, other) if s_is_a else (other, drop)
                    relations.append(Relation(_moved_id(r.id, drop), r.type, a, b, r.start,
                                              r.end, r.raw_label, moved))
                    entity(a), entity(b)
            stay = [ev for ev in own if not any(ev is m for m in moved)]
            if stay:
                relations.append(Relation(r.id, r.type, r.a, r.b, r.start, r.end, r.raw_label,
                                          stay))
                entity(r.a), entity(r.b)
        for type_, drop_is_a, start, end, raw_label, evs in record["between"]:
            own = [Evidence(**dict(ev, doc_id=doc_id)) for ev in evs
                   if current.get(ev["doc_id"], ev["doc_id"]) == doc_id]
            if own:
                a, b = (drop, survivor) if drop_is_a else (survivor, drop)
                relations.append(Relation(_moved_id("between", type_, a, b, start, end), type_,
                                          a, b, start, end, raw_label, own))
                entity(a), entity(b)
    target.put_document(doc, list(entities.values()), mentions, relations)
