"""Background graph ingestion: files in, extracted graph out, progress visible (E26).

The adapter hands over a document's bytes and metadata; one worker thread
extracts it (pipeline.extract_document) and writes the result to the store.
One worker on purpose: the extraction model shares the GPU with chat and
embedding, so documents are processed one at a time and the queue is bounded
(a full queue answers 429, the caller retries later). Queued bytes stay in
memory only; no cleartext copy is written to disk.

Possible same-person pairs the resolver did not dare to merge are kept as a
review list; an administrator decides merge (store.merge_entities) or keep apart.
"""
from __future__ import annotations

import collections
import io
import json
import os
import threading
import time

from ingest import IngestError, parse_document_payload
from llm_client import LLMError
from pipeline import extract_document
from resolution import Resolver
from table_mapping import TableMapper

RECENT = 200


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
                    self.decided.add((event["a"], event["b"]))
        self._saved = len(self.resolver.review)

    def _save_review(self, events: list[dict]) -> None:
        if not self.review_path or not events:
            return
        with self.review_lock, io.open(self.review_path, "a", encoding="utf-8",
                                       newline="\n") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # -- queue ---------------------------------------------------------------------

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

    def _process(self, meta: dict, raw: bytes) -> dict:
        profile = self.state.profile
        if (profile["id"], profile["version"]) != self.profile_version:
            self._reset_models()
        payload, stats = extract_document(raw, meta, profile, self.llm, self.resolver, self.tables)
        doc, entities, mentions, relations = parse_document_payload(payload, profile)
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

    def decide(self, a: str, b: str, merge: bool) -> None:
        """merge: `a` (the newer person) is folded into `b`; otherwise they stay apart."""
        if merge:
            self.state.store.merge_entities(b, a)
            self.resolver.merge(b, a)
        self.decided.add((a, b))
        self._save_review([{"op": "decided", "a": a, "b": b, "merge": merge}])
